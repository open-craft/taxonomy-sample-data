# Taxonomy Sample Data

The purpose of this repo is to generate Sample and Real World Taxonomy Data that can be used when testing and developing on the edx-platform.

Running this script will do the following:

1. 2 Test Organizations will be created
1. For each organization created, a sample course is created that is populated with a course export that contains a variety of section/subsections/units and components
1. For each of these created organizations the following taxonomies will be created:
    1. a disabled taxonomy with 10 Tags
    1. an enabled flat taxonomy with 5000 Tags
    1. an enabled hierarchical taxonomy with three levels and 4^x tags per level (4 root tags, each with 16 child tags, each with 64 grandchild tags)
    1. a small enabled taxonomy with 2 levels with 2 Tags each
1. A multi org Taxonomy is created and enabled/used by both orgs
1. (Optional) A 4 level Taxonomy containing data obtained from [Open Canada Taxonomy](https://open.canada.ca/data/en/dataset/6093c709-2a0d-4c23-867e-27987a79212c/resource/0a120b15-9708-4d8a-8af2-2431c4540c0b)
1. (Optional) A 3 level Taxonomy containing data obtained from [LightCast Skills Taxonomy](https://docs.google.com/spreadsheets/d/1DA3JfpBE5Krc0daImuu5Y0nsH93PEfdrWRrEa-sR-6k/edit#gid=1319222368)
1. Once the Taxonomies and their Tags have been created, the script will Tag each of the courses along with all the components they contain with 1 of each Tag from the the above

**Note:** This script is designed to be idempotent. Meaning that the end state is the same every time you run it. So if you make modifications to the sample courses on Studio or the Taxonomy data in the shell and run this script again, it will reset all your changes.


### Getting Started

1. To begin, clone this repo to your host computer.
   * If you use Tutor devstack, mount it using this comamnd:
     ```
     tutor mounts add cms:/host/path/to/taxonomy-sample-data:/openedx/taxonomy-sample-data
     ```
   * If you use "devstack", put the cloned repo inside a directory so it can be accessed from within the devstack (eg: `/edx/src/`), and set the `TAXONOMY_SAMPLE_PATH` variable inside the `generate.py` file to point to the cloned repo inside your devstack/tutor environment, eg: `/edx/src/taxonomy-sample-data`
1. Set the `USER_EMAIL` variable in `generate.py` to the user the courses/units will be created under. It defaults to `edx@example.com`.
1. (Optional) If you would not like to include taxonomy data from real world examples, such as:
    - [Open Canada Taxonomy](https://open.canada.ca/data/en/dataset/6093c709-2a0d-4c23-867e-27987a79212c/resource/0a120b15-9708-4d8a-8af2-2431c4540c0b)
    - [LightCast Skills Taxonomy](https://docs.google.com/spreadsheets/d/1DA3JfpBE5Krc0daImuu5Y0nsH93PEfdrWRrEa-sR-6k/edit#gid=1319222368)

    Then set the following flags to `False` in `generate.py` accordingly:

    ```py
    IMPORT_OPEN_CANADA_TAXONOMY = True
    IMPORT_LIGHTCAST_SKILLS_TAXONOMY = True
    ```

1. To run the script, enter the CMS shell (`tutor dev run cms bash`) and run the following command:
    ```sh
    python manage.py cms shell < /openedx/taxonomy-sample-data/generate.py
    ```

### License Information

This includes a copy of the "[Skills and Competencies Taxonomy Data](https://open.canada.ca/data/en/dataset/6093c709-2a0d-4c23-867e-27987a79212c)" from Employment and Social Development Canada, which is under the [Open Government Licence - Canada](https://open.canada.ca/en/open-government-licence-canada).

This includes a copy of the [Lightcast Open Skills Taxonomy](https://lightcast.io/open-skills), which is "free for individual and not-for-profit use" and is available under the [Lightcast Open Skills Terms of Use](https://lightcast.io/downloads/lightcast-open-skills-license-agreement.pdf).
