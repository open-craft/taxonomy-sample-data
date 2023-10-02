import os
import base64
import olxcleaner
import pkg_resources
import shutil
import tarfile
import logging

from path import Path as path
from olxcleaner.exceptions import ErrorLevel

from django.conf import settings
from django.core.exceptions import SuspiciousOperation
from django.contrib.auth import get_user_model

from organizations.models import Organization

from opaque_keys.edx.keys import CourseKey

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


ALL_ALLOWED_XBLOCKS = frozenset(
    [entry_point.name for entry_point in pkg_resources.iter_entry_points("xblock.v1")]
)

User = get_user_model()

TARFILE_PATH = '/edx/src/taxonomy-sample-data/course.g4vmy6n2.tar.gz'

SAMPLE_ORGS_COUNT = 2
SAMPLE_ORG_NAME = "SampleTaxonomyOrg"
COURSE_NAME = "Sample Taxonomy Course"
COURSE_NUMBER = "STC1"
COURSE_RUN = "2023_1"

# TODO: Remove this and get argument from command line
# and extract the user instance and user_id (pk)
user = "edx@example.com"
user_id = 3

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

for org in sample_orgs:

    # Fetch/create Sample Taxonomy Course in org
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
                User.objects.get(email=user),  # TODO: Needs to be passed in as argument
                org.short_name,
                COURSE_NUMBER,
                COURSE_RUN,
                fields
            )
            logger.info(f"Created Sample Taxonomy Course in {org}")

    # Populate Sample Taxonomy Course with imported course data
    logger.info(f"Importing OLX data to Sample Taxonomy Course in {org}")
    import_tarfile_in_course(TARFILE_PATH, course_key, user_id)

    # TODO: Create or fetch the various taxonomies

    # TODO: Create tags for taxonomies

    # TODO: Tag units/components in the sample taxonomy courses
