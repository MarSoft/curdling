from __future__ import absolute_import, unicode_literals, print_function
from collections import defaultdict
from distlib.version import LegacyMatcher, LegacyVersion

from . import util
from .exceptions import BrokenDependency, VersionConflict


def list_constraints(requirement):
    return (
        ', '.join(' '.join(x) for x in requirement.constraints or ()).replace('== ', '')
        or None)


def format_requirement(requirement):
    return util.parse_requirement(requirement).requirement.replace('== ', '')


def wheel_version(path):
    """Retrieve the version inside of a package data slot

    If there's no key `version` inside of the data dictionary, we'll
    try to guess the version number from the file name:

    ['forbiddenfruit', '0.1.1', 'cp27', 'none', 'macosx_10_8_x86_64.whl']
                          ^
    this is the guy we get in that crazy split!
    """
    return path.split('-')[1]


class Maestro(object):

    class Status:
        PENDING   = 0
        FOUND     = 1 << 0
        RETRIEVED = 1 << 1
        BUILT     = 1 << 2
        CHECKED   = 1 << 3
        INSTALLED = 1 << 4
        FAILED    = 1 << 5

    def __init__(self):
        # This is the structure that saves all the meta-data about all the
        # requested packages. If you want to see how this structure looks like
        # when it contains actuall data.
        #
        # You should take a look in the file # `tests/unit/test_maestro.py`.
        # It contains all the possible combinations of values stored in this
        # structure.

        self.data_structure = lambda: {
            'requirement': None,
            'url': None,
            'locator_url': None,
            'directory': None,
            'tarball': None,
            'wheel': None,
            'exception': None,
        }

        self.requirement_structure = lambda: {
            'status': Maestro.Status.PENDING,
            'dependency_of': [],
            'data': defaultdict(self.data_structure),
        }

        # Main container for all the package meta-data we extract. Read notice
        # above.
        self.mapping = {}

        # The possible states of a package
        self.status_sets = defaultdict(set)

    def file_requirement(self, requirement, dependency_of=None):
        requirement = format_requirement(requirement)
        entry = self.mapping.get(requirement, None)
        if not entry:
            entry = self.requirement_structure()
            entry['data'] = self.data_structure()
            self.mapping[requirement] = entry
        entry['dependency_of'].append(dependency_of)

    def set_status(self, requirement, status):
        self.mapping[format_requirement(requirement)]['status'] = status

    def add_status(self, requirement, status):
        self.set_status(requirement, self.get_status(requirement) | status)

    def get_status(self, requirement):
        return self.mapping[format_requirement(requirement)]['status']

    def set_data(self, requirement, field, value):
        requirement = format_requirement(requirement)
        if self.mapping[requirement]['data'][field] is not None:
            raise ValueError(
                'Data field `{0}` is not empty for the requirement "{1}"'.format(
                    field, requirement))
        self.mapping[requirement]['data'][field] = value

    def get_data(self, requirement, field):
        requirement = format_requirement(requirement)
        return self.mapping[requirement]['data'][field]

    def filed_packages(self):
        return list(set(util.parse_requirement(r).name for r in self.mapping.keys()))

    def filter_by(self, status):
        is_pending = lambda k: self.get_status(key) == 0 and status == 0
        return [key for key in self.mapping.keys()
            if is_pending(key) or self.get_status(key) & status]

    def get_requirements_by_package_name(self, package_name):
        return [x for x in self.mapping.keys()
            if util.parse_requirement(x).name == util.parse_requirement(package_name).name]

    def available_versions(self, package_name):
        return sorted(set(wheel_version(self.mapping[requirement]['data']['wheel'])
            for requirement in self.mapping.keys()),
                reverse=True)

    def matching_versions(self, requirement):
        matcher = LegacyMatcher(requirement)
        package_name = util.parse_requirement(requirement).name
        versions = self.available_versions(package_name)
        return [version for version in versions if matcher.match(version)]

    def broken_versions(self, requirement):
        package_name = util.parse_requirement(requirement).name
        versions = self.available_versions(package_name)
        return [version for version in versions
            if self.get_data(requirement, 'exception')
                is not None]

    def is_primary_requirement(self, requirement):
        return not bool(filter(None, self.mapping[requirement]['dependency_of']))

    def best_version(self, requirement_or_package_name, debug=False):
        package_name = util.parse_requirement(requirement_or_package_name).name
        requirements = self.get_requirements_by_package_name(package_name)

        # Used to remember in which requirement we found each version
        requirements_by_version = {}
        get_requirement = lambda v: (v, requirements_by_version[v])

        # A helper that sorts the versions putting the newest ones first
        newest = lambda versions: sorted(versions, reverse=True)[0]

        # Gather all version info available inside of all requirements
        all_versions = []
        all_constraints = []
        primary_versions = []
        for requirement in requirements:
            if self.is_primary_requirement(requirement):
                version = wheel_version(self.get_data(requirement, 'wheel'))
                primary_versions.append(version)

            versions = self.matching_versions(requirement)
            requirements_by_version.update((v, requirement) for v in versions)
            all_versions.extend(versions)
            all_constraints.append(list_constraints(util.parse_requirement(requirement)))

        # List that will gather all the primary versions. This catches
        # duplicated first level requirements with different versions.
        if primary_versions:
            return get_requirement(newest(primary_versions))

        # Find all the versions that appear in all the requirements
        compatible_versions = [v for v in all_versions
            if all_versions.count(v) == len(requirements)]

        if not compatible_versions:
            raise VersionConflict(
                'Requirement: {0} ({1}), Available versions: {2}'.format(
                    package_name,
                    ', '.join(all_constraints),
                    ', '.join(self.available_versions(package_name)),
                ))

        return get_requirement(newest(compatible_versions))
