import os
import base64
import olxcleaner
import pkg_resources
import shutil
import tarfile
import logging
import tqdm
import json

from path import Path as path
from olxcleaner.exceptions import ErrorLevel

from django.conf import settings
from django.core.exceptions import SuspiciousOperation
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from organizations.models import Organization

from openedx.core.lib.extract_tar import safetar_extractall

from cms.djangoapps.contentstore.views.course import create_new_course_in_store
from cms.djangoapps.contentstore import errors as UserErrors

from xmodule.modulestore import ModuleStoreEnum, COURSE_ROOT
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.xml_importer import (
    CourseImportException, import_course_from_xml
)
from xmodule.modulestore.exceptions import (
    DuplicateCourseError, InvalidProctoringProvider
)

from xmodule.contentstore.django import contentstore

from openedx_tagging.core.tagging.models import Tag, Taxonomy

from openedx_tagging.core.tagging.api import delete_tags_from_taxonomy, get_children_tags
from openedx.core.djangoapps.content_tagging.api import (
    create_taxonomy, get_taxonomies_for_org,
    set_taxonomy_orgs, tag_content_object, get_content_tags,
    resync_object_tags, get_tags
)

from openedx.core.djangoapps.discussions.tasks import (
    get_sections, get_subsections, get_units
)


# Configuring logger while running in the shell to make it less verbose
logger = logging.getLogger("taxonomy-sample-data")
logger.propagate = False
logger_handler = logging.StreamHandler()
logger.addHandler(logger_handler)
logger_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s - %(message)s'))


# ----------------------------------- UTILS -----------------------------------
# These utils were extracted (and slightly modified) from the
# `import_olx` function found in:
# https://github.com/openedx/edx-platform/blob/194915d6bd050f7a778d2e4e104c56147630851a/cms/djangoapps/contentstore/tasks.py#L449

ALL_ALLOWED_XBLOCKS = frozenset(
    [entry_point.name for entry_point in pkg_resources.iter_entry_points("xblock.v1")]
)


def verify_root_name_exists(course_dir, root_name):
    """Verify root xml file exists."""

    def get_all_files(directory):
        """
        For each file in the directory, yield a 2-tuple of (file-name,
        directory-path)
        """
        for directory_path, _dirnames, filenames in os.walk(directory):
            for filename in filenames:
                yield (filename, directory_path)

    def get_dir_for_filename(directory, filename):
        """
        Returns the directory path for the first file found in the directory
        with the given name.  If there is no file in the directory with
        the specified name, return None.
        """
        for name, directory_path in get_all_files(directory):
            if name == filename:
                return directory_path
        return None

    dirpath = get_dir_for_filename(course_dir, root_name)
    if not dirpath:
        message = UserErrors.FILE_MISSING.format(root_name)
        logger.error(f'{message}')
        return
    return dirpath


def validate_course_olx(course_key, course_dir):
    """
    Validates course olx and records the errors as an artifact.

    Arguments:
        course_key: A locator identifies a course resource.
        course_dir: complete path to the course olx
    """
    olx_is_valid = True
    validation_failed_mesg = 'CourseOlx validation failed.'

    try:
        __, errorstore, __ = olxcleaner.validate(
            filename=course_dir,
            steps=settings.COURSE_OLX_VALIDATION_STAGE,
            ignore=settings.COURSE_OLX_VALIDATION_IGNORE_LIST,
            allowed_xblocks=ALL_ALLOWED_XBLOCKS
        )
    except Exception:  # pylint: disable=broad-except
        logger.exception('CourseOlx could not be validated')
        return olx_is_valid

    has_errors = errorstore.return_error(ErrorLevel.ERROR.value)
    if not has_errors:
        return olx_is_valid

    logger.error(f'{validation_failed_mesg}')

    return False


def validate_user(user_id):
    """Validate if the user exists otherwise log error. """
    try:
        return User.objects.get(pk=user_id)
    except User.DoesNotExist as exc:
        logger.error(f'Unknown User: {user_id}')
        return


def import_tarfile_in_course(tarfile_path, course_key, user_id):
    """Helper method to import provided tarfile in the course."""

    user = validate_user(user_id)
    if not user:
        return

    data_root = path(settings.GITHUB_REPO_ROOT)
    subdir = base64.urlsafe_b64encode(repr(course_key).encode('utf-8')).decode('utf-8')
    course_dir = data_root / subdir

    root_name = COURSE_ROOT
    courselike_block = modulestore().get_course(course_key)
    import_func = import_course_from_xml

    # try-finally block for proper clean up after receiving file.
    try:
        tar_file = tarfile.open(tarfile_path)
        try:
            safetar_extractall(tar_file, (course_dir + '/'))
        except SuspiciousOperation as exc:
            logger.error(f'Unsafe tar file')
            return
        finally:
            tar_file.close()
        logger.info('Course tar file extracted. Verification step started')

        dirpath = verify_root_name_exists(course_dir, root_name)
        if not dirpath:
            return

        if not validate_course_olx(course_key, dirpath):
            return

        dirpath = os.path.relpath(dirpath, data_root)

        logger.info(f'Extracted file verified. Updating course started')

        courselike_items = import_func(
            modulestore(), user.id,
            settings.GITHUB_REPO_ROOT, [dirpath],
            load_error_blocks=False,
            static_content_store=contentstore(),
            target_id=course_key,
            verbose=True,
        )

        new_location = courselike_items[0].location
        logger.debug('new course at %s', new_location)

        logger.info(f'Course import successful')
    except (CourseImportException, InvalidProctoringProvider, DuplicateCourseError) as known_exe:
        logger.exception(f"Error while importing course: {known_exe}")
    except Exception as exception:  # pylint: disable=broad-except
        logger.exception(f"Error while importing course: {exception}")
    finally:
        if course_dir.isdir():
            shutil.rmtree(course_dir)
            logger.info('Temp data cleared')

# -----------------------------------------------------------------------------


User = get_user_model()

USER_EMAIL = "edx@example.com"

user = User.objects.get(email=USER_EMAIL)

# Set to path where repo was cloned, eg: /edx/src/taxonomy-sample-data
TAXONOMY_SAMPLE_PATH = None

if TAXONOMY_SAMPLE_PATH is None:
    raise Exception("`TAXONOMY_SAMPLE_PATH` not set. Please set it in generate.py")

TARFILE_PATH = f"{TAXONOMY_SAMPLE_PATH}/course.g4vmy6n2.tar.gz"

SAMPLE_ORGS_COUNT = 2
SAMPLE_ORG_NAME = "SampleTaxonomyOrg"
COURSE_NAME = "Sample Taxonomy Course"
COURSE_NUMBER = "STC1"
COURSE_RUN = "2023_1"

DISABLED_TAXONOMY_NAME = "DisabledTaxonomy"
FLAT_TAXONOMY_NAME = "FlatTaxonomy"
HIERARCHICAL_TAXONOMY_NAME = "HierarchicalTaxonomy"
TWO_LEVEL_TAXONOMY_NAME = "TwoLevelTaxonomy"
MULTI_ORG_TAXONOMY_NAME = "MultiOrgTaxonomy"

IMPORT_OPEN_CANADA_TAXONOMY = True
IMPORT_LIGHTCAST_SKILLS_TAXONOMY = True


def get_or_create_taxonomy(org_taxonomies, name, orgs, enabled=True):
    """
    Get or create Taxonomy for Sample Taxonomy Orgs

    Arguments:
        org_taxonomies: Queryset of an org's existing taxonomies or
                        None if it should be for all orgs
        name: Taxonomy name
        enabled: Whether Taxonomy is enabled/disabled
        orgs: List of orgs Taxonomy belongs to
    """
    try:
        if org_taxonomies is None:
            taxonomy = Taxonomy.objects.get(name=name, enabled=enabled).cast()
        else:
            taxonomy = org_taxonomies.get(name=name, enabled=enabled).cast()
    except (Taxonomy.DoesNotExist, Taxonomy.MultipleObjectsReturned) as e:
        if isinstance(e, Taxonomy.MultipleObjectsReturned):
            # If for some reason there are multiple matching taxonomies,
            # delete and start from scratch
            Taxonomy.objects.filter(name=name, enabled=enabled).delete()

        taxonomy = create_taxonomy(name=name, enabled=enabled)
        set_taxonomy_orgs(taxonomy, orgs=orgs)

    return taxonomy


def create_tags_for_disabled_taxonomy(disabled_taxonomy):
    """
    Create 10 Tags for the disabled_taxonomy
    """
    for i in range(10):
        Tag.objects.create(
            taxonomy=disabled_taxonomy, value=f"disabled taxonomy tag {i+1}"
        )


def create_tags_for_flat_taxonomy(flat_taxonomy):
    """
    Create 5000 Tags for the flat_taxonomy
    """
    for i in range(5000):
        Tag.objects.create(
            taxonomy=flat_taxonomy, value=f"flat taxonomy tag {i+1}"
        )


def _create_tags_recursively(
    level, max_levels, tags_multiplier, taxonomy, tag_value_prefix, parent=None
):
    """
    Recursively create tags based on parameters passed in

    Arguments:
        level: currently level in recursive call
        max_levels: maximum amount of levels to call recursively
        tags_multiplier: amount of tags to exponentially add per level, the x in x^level
        taxonomy: taxonomy tag belongs to
        tag_value_prefix: prefix of value for tag being created
        parent: parent of tag being created at this level, None = root level
    """
    if level > max_levels:
        return

    for i in range(tags_multiplier**level):

        if parent is None:
            tag_value = f"{tag_value_prefix} {i + 1}"
        else:
            tag_dynamic_value = parent.value.replace(
                f"{tag_value_prefix} ", ""
            )
            tag_value = f"{tag_value_prefix} {tag_dynamic_value}.{i + 1}"

        tag = Tag.objects.create(
            taxonomy=taxonomy, value=tag_value, parent=parent
        )

        _create_tags_recursively(
            level + 1, max_levels, tags_multiplier,
            taxonomy, tag_value_prefix, parent=tag
        )


def create_tags_for_hierarchical_taxonomy(hierarchical_taxonomy):
    """
    Create 4^x Tags across 3 levels for the hierarchical_taxonomy
    """
    MAX_LEVELS = 3
    TAGS_MULTIPLIER = 4

    _create_tags_recursively(
        1, MAX_LEVELS, TAGS_MULTIPLIER,
        hierarchical_taxonomy, "hierarchical taxonomy tag", parent=None
    )


def create_tags_for_two_level_taxonomy(two_level_taxonomy):
    """
    Create 2 Tags across 2 levels for the two_level_taxonomy
    """
    MAX_LEVELS = 2
    TAGS_MULTIPLIER = 1

    _create_tags_recursively(
        1, MAX_LEVELS, TAGS_MULTIPLIER,
        two_level_taxonomy, "two level tag", parent=None
    )


def create_tags_for_multi_org_taxonomy(multi_org_taxonomy):
    """
    Create 5 tags for the multi_org_taxonomy
    """
    for i in range(5):
        Tag.objects.create(
            taxonomy=multi_org_taxonomy, value=f"multi org taxonomy tag {i}"
        )


def create_tags_from_json(open_canada_taxonomy, import_json_path):
    """
    Create tags based what is defined in JSON import spec
    """

    def _create_tags(taxonomy_data, parent):
        if len(taxonomy_data) == 0:
            return

        for data in taxonomy_data:
            tag = Tag.objects.create(
                taxonomy=open_canada_taxonomy,
                value=data.get("name"),
                parent=parent,
                external_id=data.get("external_id")
            )
            _create_tags(data.get("children"), tag)

    with open(import_json_path, 'r') as json_file:
        taxonomy_data = json.load(json_file)

    _create_tags(taxonomy_data, None)


def tagify_object(object_id, taxonomies):
    """
    Tag object with tags from the provided taxonomies

    Arguments:
        object_id: ID of object to be tagged
        taxonomies: list of taxonomies of tags to tag object with
    """
    for taxonomy in taxonomies:
        leaf_tag = None
        tags = get_tags(taxonomy)
        while len(tags) > 0:
            leaf_tag = tags[0]
            tags = get_children_tags(taxonomy, leaf_tag["value"])
        try:
            tag_content_object(object_id, taxonomy, [leaf_tag["value"]])
        except IntegrityError:
            # content tag value already exists, we need to resync with
            # new tag instance
            content_tags = list(get_content_tags(object_id, taxonomy.id))
            resync_object_tags(content_tags)


# Generate sample organizations or retrieve them if they already exist
logger.info("Generating or retrieving sample Organizations...")
sample_orgs = []
for i in range(1, SAMPLE_ORGS_COUNT+1):
    org, created = Organization.objects.get_or_create(
        name=f"{SAMPLE_ORG_NAME}{i}",
        short_name=f"{SAMPLE_ORG_NAME}{i}"
    )
    logger.info(f"{'Created' if created else 'Retrieved'} {org}")
    sample_orgs.append(org)

store = modulestore()

# Retrieve/Create multi org Taxonomy with 5 tags for the sample orgs
logger.info(f"Creating or retrieving {MULTI_ORG_TAXONOMY_NAME}")
multi_org_taxonomy = get_or_create_taxonomy(
    None, MULTI_ORG_TAXONOMY_NAME, sample_orgs, enabled=True
)

# Clear any existing Tags for hierarchical_taxonomy and create fresh ones
multi_org_taxonomy_tags = get_tags(multi_org_taxonomy)
logger.info(f"Clearing existing {len(multi_org_taxonomy_tags)} Tags for {multi_org_taxonomy}")
delete_tags_from_taxonomy(
    multi_org_taxonomy,
    list(map(lambda t: t["value"], multi_org_taxonomy_tags)),
    with_subtags=True
)

logger.info(f"Creating fresh Tags for {multi_org_taxonomy}")
multi_org_taxonomy_tags = create_tags_for_multi_org_taxonomy(multi_org_taxonomy)


if IMPORT_OPEN_CANADA_TAXONOMY:
    OPEN_CANADA_TAXONOMY_NAME = "OpenCanadaTaxonomy"
    OPEN_CANADA_TAXONOMY_PATH = f"{TAXONOMY_SAMPLE_PATH}/sample_data/open_canada_taxonomy.json"

    # Retrieve/Create Open Canada Taxonomy:
    # https://open.canada.ca/data/en/dataset/6093c709-2a0d-4c23-867e-27987a79212c/resource/0a120b15-9708-4d8a-8af2-2431c4540c0b
    # It has four levels (Category > Sub-Category > Similarity Group > Descriptor
    logger.info(f"Creating or retrieving {OPEN_CANADA_TAXONOMY_NAME}")
    open_canada_taxonomy = get_or_create_taxonomy(
        None, OPEN_CANADA_TAXONOMY_NAME, sample_orgs, enabled=True
    )

    # Clear any existing Tags for open_canada_taxonomy and create fresh ones
    open_canada_taxonomy_tags = get_tags(open_canada_taxonomy)
    logger.info(
        f"Clearing existing {len(open_canada_taxonomy_tags)} Tags for {open_canada_taxonomy}"
    )
    delete_tags_from_taxonomy(
        open_canada_taxonomy,
        list(map(lambda t: t["value"], open_canada_taxonomy_tags)),
        with_subtags=True
    )

    logger.info(f"Creating fresh Tags for {open_canada_taxonomy}")

    create_tags_from_json(open_canada_taxonomy, OPEN_CANADA_TAXONOMY_PATH)


if IMPORT_LIGHTCAST_SKILLS_TAXONOMY:
    LIGHTCAST_SKILLS_TAXONOMY_NAME = "LightCastSkillsTaxonomy"
    LIGHTCAST_SKILLS_TAXONOMY_PATH = f"{TAXONOMY_SAMPLE_PATH}/sample_data/lightcast_taxonomy.json"

    # Retrieve/Create LightCast Skills Taxonomy:
    # https://docs.google.com/spreadsheets/d/1DA3JfpBE5Krc0daImuu5Y0nsH93PEfdrWRrEa-sR-6k/edit#gid=1319222368
    # It has three levels (Category > Sub-Category > Skill
    logger.info(f"Creating or retrieving {LIGHTCAST_SKILLS_TAXONOMY_NAME}")
    lightcast_skills_taxonomy = get_or_create_taxonomy(
        None, LIGHTCAST_SKILLS_TAXONOMY_NAME, sample_orgs, enabled=True
    )

    # Clear any existing Tags for lightcast_skills_taxonomy and create fresh ones
    lightcast_skills_taxonomy_tags = get_tags(lightcast_skills_taxonomy)
    logger.info(
        f"Clearing existing {len(lightcast_skills_taxonomy_tags)} Tags for {lightcast_skills_taxonomy}"
    )
    delete_tags_from_taxonomy(
        lightcast_skills_taxonomy,
        list(map(lambda t: t["value"], lightcast_skills_taxonomy_tags)),
        lightcast_skills_taxonomy_tags
    )

    logger.info(f"Creating fresh Tags for {lightcast_skills_taxonomy}")

    create_tags_from_json(lightcast_skills_taxonomy, LIGHTCAST_SKILLS_TAXONOMY_PATH)


for org in sample_orgs:
    generated_taxonomies = [multi_org_taxonomy]

    if IMPORT_OPEN_CANADA_TAXONOMY:
        generated_taxonomies.append(open_canada_taxonomy)

    if IMPORT_LIGHTCAST_SKILLS_TAXONOMY:
        generated_taxonomies.append(lightcast_skills_taxonomy)

    # Retrieve/create Sample Taxonomy Course in org
    logger.info(
        f"Generating or retrieving Sample Taxonomy Courses for {org.short_name}..."
    )
    with store.default_store(ModuleStoreEnum.Type.split):
        course_key = store.make_course_key(
            org.short_name, COURSE_NUMBER, COURSE_RUN
        )
        sample_taxonomy_course = store.get_course(course_key)
        if sample_taxonomy_course:
            logger.info(f"Found Sample Taxonomy Course in {org}")
        else:
            fields = {
                "display_name": COURSE_NAME
            }
            sample_taxonomy_course = create_new_course_in_store(
                ModuleStoreEnum.Type.split,
                user,
                org.short_name,
                COURSE_NUMBER,
                COURSE_RUN,
                fields
            )
            logger.info(f"Created Sample Taxonomy Course in {org}")

    # Populate Sample Taxonomy Course with imported course data
    logger.info(f"Importing OLX data to Sample Taxonomy Course in {org}")
    import_tarfile_in_course(TARFILE_PATH, course_key, user.id)

    # Fetch all Taxonomies (enabled and disabled) for organization
    logger.info(f"Fetching all Taxonomies for {org}")
    org_taxonomies = get_taxonomies_for_org(org_owner=org, enabled=None)

    # Retrieve/Create disabled Taxonomy with 10 tags for org
    logger.info(f"Creating or retrieving {DISABLED_TAXONOMY_NAME}")
    disabled_taxonomy = get_or_create_taxonomy(
        org_taxonomies, DISABLED_TAXONOMY_NAME, [org], enabled=False
    )

    # Clear any existing Tags for disabled_taxonomy and create fresh ones
    disabled_taxonomy_tags = get_tags(disabled_taxonomy)
    logger.info(
        f"Clearing existing {len(disabled_taxonomy_tags)} Tags for {disabled_taxonomy}"
    )
    delete_tags_from_taxonomy(
        disabled_taxonomy,
        list(map(lambda t: t["value"], disabled_taxonomy_tags)),
        with_subtags=True
    )

    logger.info(f"Creating fresh Tags for {disabled_taxonomy}")
    create_tags_for_disabled_taxonomy(disabled_taxonomy)

    # Retrieve/Create flat Taxonomy with 5000 tags for org
    logger.info(f"Creating or retrieving {FLAT_TAXONOMY_NAME}")
    flat_taxonomy = get_or_create_taxonomy(
        org_taxonomies, FLAT_TAXONOMY_NAME, [org], enabled=True
    )

    # Clear any existing Tags for flat_taxonomy and create fresh ones
    flat_taxonomy_tags = get_tags(flat_taxonomy)
    logger.info(f"Clearing existing {len(flat_taxonomy_tags)} Tags for {flat_taxonomy}")
    delete_tags_from_taxonomy(
        flat_taxonomy,
        list(map(lambda t: t["value"], flat_taxonomy_tags)),
        with_subtags=True
    )

    logger.info(f"Creating fresh Tags for {flat_taxonomy}")
    create_tags_for_flat_taxonomy(flat_taxonomy)

    # Retrieve/Create hierarchical Taxonomy with three levels
    # and 4^x tags per level (4 root tags, each with 16 child tags,
    # each with 64 grandchild tags) for org
    logger.info(f"Creating or retrieving {HIERARCHICAL_TAXONOMY_NAME}")
    hierarchical_taxonomy = get_or_create_taxonomy(
        org_taxonomies, HIERARCHICAL_TAXONOMY_NAME, [org], enabled=True
    )

    # Clear any existing Tags for hierarchical_taxonomy and create fresh ones
    hierarchical_taxonomy_tags = get_tags(hierarchical_taxonomy)
    logger.info(
        f"Clearing existing {len(hierarchical_taxonomy_tags)} Tags for {hierarchical_taxonomy}"
    )
    delete_tags_from_taxonomy(
        hierarchical_taxonomy,
        list(map(lambda t: t["value"], hierarchical_taxonomy_tags)),
        with_subtags=True
    )

    logger.info(f"Creating fresh Tags for {hierarchical_taxonomy}")
    create_tags_for_hierarchical_taxonomy(hierarchical_taxonomy)

    # Retrieve/Create two level Taxonomy with 2 tag each level for org
    logger.info(f"Creating or retrieving {TWO_LEVEL_TAXONOMY_NAME}")
    two_level_taxonomy = get_or_create_taxonomy(
        org_taxonomies, TWO_LEVEL_TAXONOMY_NAME, [org], enabled=True
    )

    # Clear any existing tags for two_level_taxonomy and create fresh ones
    two_level_taxonomy_tags = get_tags(two_level_taxonomy)
    logger.info(
        f"Clearing existing {len(two_level_taxonomy_tags)} Tags for {two_level_taxonomy}"
    )
    delete_tags_from_taxonomy(
        two_level_taxonomy,
        list(map(lambda t: t["value"], two_level_taxonomy_tags)),
        two_level_taxonomy_tags
    )

    logger.info(f"Creating fresh Tags for {two_level_taxonomy}")
    create_tags_for_two_level_taxonomy(two_level_taxonomy)

    generated_taxonomies += [
        disabled_taxonomy, flat_taxonomy,
        hierarchical_taxonomy, two_level_taxonomy
    ]

    # Tagging Courses and Components

    # Tag course with one of each tag in taxonomies created above
    logger.info(f"Tagging {sample_taxonomy_course.id}")
    tagify_object(
        sample_taxonomy_course.id,
        generated_taxonomies
    )

    # Tag components inside units (vertical xblocks) with
    # one of each tag created above
    for section in get_sections(sample_taxonomy_course):
        for subsection in get_subsections(section):
            for unit in get_units(subsection):
                # Tag units
                logger.info(f"Tagging {unit.location}")
                tagify_object(
                    unit.location,
                    generated_taxonomies
                )
                for child in unit.get_children():
                    logger.info(f"Tagging {child.location}")
                    tagify_object(
                        child.location,
                        generated_taxonomies
                    )
