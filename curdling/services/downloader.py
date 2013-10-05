from __future__ import absolute_import, print_function, unicode_literals
from ..exceptions import ReportableError
from ..signal import Signal
from .. import util
from .base import Service

from distlib import database, metadata, compat, locators
from urlparse import urljoin, urlunparse

import re
import json
import httplib
import urllib3
import distlib.version


# Hardcoded vaue for the size of the http pool used a couple times in this
# module. Not the perfect place, though might fix the ClosedPoolError we're
# getting eventually.
POOL_MAX_SIZE = 10


def get_locator(conf):
    curds = [CurdlingLocator(u) for u in conf.get('curdling_urls', [])]
    pypi = [PyPiLocator(u) for u in conf.get('pypi_urls', [])]
    return AggregatingLocator(*(curds + pypi), scheme='legacy')


def find_packages(locator, requirement, versions):
    scheme = distlib.version.get_scheme(locator.scheme)
    matcher = scheme.matcher(requirement.requirement)

    result = {}
    if versions:
        slist = []
        for v in versions:
            if matcher.match(matcher.version_class(v)):
                slist.append(v)
        slist = sorted(slist, key=scheme.key)
        if len(slist):
            result = versions[slist[-1]]

    return result


class Pool(urllib3.PoolManager):

    def retrieve(self, url):
        # Params to be passed to request. The `preload_content` must be set to
        # False, otherwise `read()` wont honor `decode_content`.
        params = {
            'headers': util.get_auth_info_from_url(url),
            'preload_content': False,
        }

        # Request the url and ensure we've reached the final location
        response = self.request('GET', url, **params)
        return response, response.get_redirect_location() or url


class AggregatingLocator(locators.AggregatingLocator):

    def locate(self, requirement, prereleases=True):
        pkg = util.parse_requirement(requirement)
        for locator in self.locators:
            versions = locator.get_project(pkg.name)
            return find_packages(locator, pkg, versions) or None


class PyPiLocator(locators.SimpleScrapingLocator):
    def __init__(self, url, **kwargs):
        super(PyPiLocator, self).__init__(url, **kwargs)
        self.opener = Pool(maxsize=POOL_MAX_SIZE)

    def _get_project(self, name):
        return self._fetch(
            urljoin(self.base_url, '%s/' % compat.quote(name)),
            name)

    def _visit_link(self, project_name, link):
        self._seen.add(link)
        locators.logger.debug('_fetch() found link: %s', link)
        info = not self._is_platform_dependent(link) \
            and self.convert_url_to_download_info(link, project_name) \
            or None

        versions = {}
        if info:
            self._update_version_data(versions, info)
            return versions.items()[0]
        return None, None

    def _fetch(self, url, project_name, subvisit=False):
        locators.logger.debug('fetch(%s, %s)', url, project_name)
        versions = {}
        page = self.get_page(url)
        for link, rel in (page and page.links or []):
            # Let's instrospect one level down
            if self._should_queue(link, url, rel) and not subvisit:
                versions.update(self._fetch(link, project_name, subvisit=True))

            # Let's not see anything twice, I saw this check on distlib it
            # might be useful.
            if link not in self._seen:
                # Well, here we're ensuring that the first link of a given
                # version will be the one. Even if we find another package for
                # the same version, the first one will be used.
                version, distribution = self._visit_link(project_name, link)
                if version and version not in versions:
                    versions[version] = distribution
        return versions

    def get_page(self, url):
        # http://peak.telecommunity.com/DevCenter/EasyInstall#package-index-api
        scheme, netloc, path, _, _, _ = compat.urlparse(url)
        if scheme == 'file' and os.path.isdir(url2pathname(path)):
            url = urljoin(ensure_slash(url), 'index.html')

        # The `retrieve()` method follows any eventual redirects, so the
        # initial url might be different from the final one
        try:
            response, final_url = self.opener.retrieve(url)
        except urllib3.exceptions.MaxRetryError:
            return

        content_type = response.headers.get('content-type', '')
        if locators.HTML_CONTENT_TYPE.match(content_type):
            data = response.data
            encoding = response.headers.get('content-encoding')
            if encoding:
                decoder = self.decoders[encoding]   # fail if not found
                data = decoder(data)
            encoding = 'utf-8'
            m = locators.CHARSET.search(content_type)
            if m:
                encoding = m.group(1)
            try:
                data = data.decode(encoding)
            except UnicodeError:
                data = data.decode('latin-1')    # fallback
            return locators.Page(data, final_url)


class CurdlingLocator(locators.Locator):

    def __init__(self, url, **kwargs):
        super(CurdlingLocator, self).__init__(**kwargs)
        self.base_url = url
        self.url = url
        self.opener = Pool(maxsize=POOL_MAX_SIZE)
        self.requirements_not_found = []

    def get_distribution_names(self):
        return json.loads(
            self.opener.retrieve(
                urljoin(self.url, 'api'))[0].data)

    def _get_project(self, name):
        # Retrieve the info
        url = urljoin(self.url, 'api/' + name)
        try:
            response, _ = self.opener.retrieve(url)
        except urllib3.exceptions.MaxRetryError:
            return None

        if response.status == 200:
            data = json.loads(response.data)
            return {v['version']: self._get_distribution(v) for v in data}
        else:
            self.requirements_not_found.append(name)

    def _get_distribution(self, version):
        # Source url for the package
        source_url = version['urls'][0]  # TODO: prefer whl files

        # Build the metadata
        mdata = metadata.Metadata(scheme=self.scheme)
        mdata.name = version['name']
        mdata.version = version['version']
        mdata.source_url = mdata.download_url = source_url['url']

        # Building the dist and associating the download url
        distribution = database.Distribution(mdata)
        distribution.locator = self
        return distribution


class Downloader(Service):

    def __init__(self, *args, **kwargs):
        super(Downloader, self).__init__(*args, **kwargs)
        self.opener = Pool(maxsize=POOL_MAX_SIZE)
        self.locator = get_locator(self.conf)

    def handle(self, requester, requirement, sender_data):
        found = None
        prereleases = self.conf.get('prereleases', True)

        # It sounds lame, but we're trying to match requirements with more than
        # one word separated with either `_` or `-`. Notice that we prefer
        # hyphens cause theres currently way more packages using hyphens than
        # underscores in pypi.p.o. Let's wait for the best here.
        options = requirement.replace('_', '-'), requirement.replace('-', '_')
        for option in options:
            found = False

            if not util.parse_requirement(requirement).is_link:
                # We're dealing with the regular requirements: "name (x.y.z)"
                found = self.locator.locate(option, prereleases)
            else:
                # We're dealing with a link
                mdata = metadata.Metadata(scheme=self.locator.scheme)
                mdata.source_url = mdata.download_url = requirement
                found = database.Distribution(mdata)

            if found:
                break

        if not found:
            raise ReportableError('Requirement `{0}\' not found'.format(
                requirement))
        return {"path": self.download(found)}

    def get_servers_to_update(self):
        failures = {}
        for locator in self.locator.locators:
            if isinstance(locator, CurdlingLocator) and locator.requirements_not_found:
                failures[locator.base_url] = locator.requirements_not_found
        return failures

    # -- Private API of the Download service --

    def update_url_credentials(self, base_url, other_url):
        base = compat.urlparse(base_url)
        other = compat.urlparse(other_url)

        # If they're not from the same server, we return right away without
        # trying to update anything
        if base.hostname != other.hostname or base.port != other.port:
            return other.geturl()

        # Since I can't change the `ParseResult` object returned by `urlparse`,
        # I'll have to do that manually and that stinks.
        scheme, netloc, path, params, query, fragment = list(other)
        return urlunparse((scheme, base.netloc, path, params, query, fragment))

    def download(self, distribution):
        # This is the URL retrieved by the locator that found the given
        # distribution.
        final_url = url = distribution.download_url

        # We're dealing with a requirement, not a link
        if distribution.locator:
            # This is the locator's `base_url` that possibly contains
            # authentication credentials that we have to add to the URL we want to
            # download right now.
            base_url = distribution.locator.base_url

            # Updated version of the full URL
            final_url = self.update_url_credentials(base_url, url)

        # Let's proceed with the request, but now with the right auth
        # credentials.
        response, _ = self.opener.retrieve(final_url)
        if response.status != 200:
            raise ReportableError(
                'Failed to download url `{0}\': {1} ({2})'.format(
                    url,
                    response.status,
                    httplib.responses[response.status],
                ))

        # Now that we're sure that our request was successful
        header = response.headers.get('content-disposition', '')
        file_name = re.findall(r'filename=([^;]+)', header)
        return self.index.from_data(
            file_name and file_name[0] or url, response.read(
                cache_content=True, decode_content=False))
