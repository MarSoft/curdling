# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function, unicode_literals
from mock import Mock, patch, call
from distlib import database

from curdling.exceptions import UnknownURL, TooManyRedirects, ReportableError
from curdling.services import downloader

import urllib3


def test_locators_should_be_comparable():
    "PyPiLocator() and CurdlingLocator() Should be comparable between each other"

    # When I compare locators from the same type with the same URL,
    # they should equal
    downloader.PyPiLocator('url1').should.equal(downloader.PyPiLocator('url1'))
    downloader.PyPiLocator('url1').should_not.equal(downloader.PyPiLocator('url2'))

    # And the same is true for the CurdlingLocator class
    downloader.CurdlingLocator('url1').should.equal(downloader.CurdlingLocator('url1'))
    downloader.CurdlingLocator('url1').should_not.equal(downloader.CurdlingLocator('url2'))

    # When I compare locators from different types; they should not equal
    downloader.CurdlingLocator('url1').should_not.equal(downloader.PyPiLocator('url1'))


def test_get_locator():
    "get_locator() Should an AggregatingLocator fed with all curd and pypi locators informed in `conf`"

    # Given the following configuration
    conf = {
        'pypi_urls': ['http://pypi.py.o/simple'],
        'curdling_urls': ['http://curd.clarete.li', 'http://curd.falcao.it'],
    }

    # When I try to retrieve a locator
    locator = downloader.get_locator(conf)

    # Than I see all the above locator URLs present inside of the main
    # one
    locator.should.be.a(downloader.AggregatingLocator)
    locator.locators.should.equal((
        downloader.CurdlingLocator('http://curd.clarete.li'),
        downloader.CurdlingLocator('http://curd.falcao.it'),
        downloader.PyPiLocator('http://pypi.py.o/simple'),
    ))


def test_get_opener():
    "get_opener() Should return an HTTP retriever class from urllib3"

    # When I need a regular opener; Then I should get a Pool Manager
    downloader.get_opener().should.be.a(urllib3.PoolManager)


@patch('os.getenv')
def test_get_opener_with_proxy(getenv):
    "get_opener() Should return a Proxy Manager from urllib3 when `http_proxy` is available"

    # Given the following proxy server set in the http_proxy variable
    getenv.return_value = 'http://user:pwd@srv.prx:8123'

    # When I request the opener
    opener = downloader.get_opener()

    # Then I get a Proxy Manager
    opener.should.be.a(urllib3.ProxyManager)

    # And I check that the proxy URL is right
    '{0}://{1}@{2}:{3}'.format(*tuple(opener.proxy)).should.equal('http://user:pwd@srv.prx:8123')

    # And that the authentication header is present
    opener.proxy_headers.should.equal(
        {'proxy-authorization': 'Basic dXNlcjpwd2Q='})


class TestPyPiLocator(downloader.PyPiLocator):
    def __init__(self, *args, **kw):
        super(TestPyPiLocator, self).__init__(*args, **kw)
        self.opener = Mock()


@patch('curdling.services.downloader.distlib')
def test_find_packages(distlib):
    ("find_packages should use the scheme from the "
     "locator to match the best result")
    # Background
    # The scheme is mocked
    scheme = distlib.version.get_scheme.return_value
    # As well as the matcher
    matcher = scheme.matcher.return_value
    # And a version class
    version_class = matcher.version_class.return_value

    # Given a locator
    locator = Mock()

    # And a requirement
    requirement = Mock()

    # And a versions dictionary
    distribution = Mock()
    versions = {
        '1.0': distribution
    }

    # When I invoke find_packages
    result = downloader.find_packages(locator, requirement, versions)
    # Then the result should be the expected distribution
    result.should.equal(distribution)
    # And the method calls should be correct (sorry for this sad test,
    # I'm still getting to know the codebase)
    matcher.match.assert_called_once_with(version_class)
    scheme.matcher.assert_called_once_with(requirement.requirement)
    distlib.version.get_scheme.assert_called_once_with(locator.scheme)


def test_update_url_credentials():
    "update_url_credentials() should update URL2 using auth info from URL1"

    # Given that I have a URL with authentication info
    url1 = 'http://user:almost-safe-password@domain.com/path/to/resource.html'

    # And another URL without auth info
    url2 = 'http://domain.com/another/path/to/a/cooler/resource.html'

    # When I update the second one based on the first one
    final_url = downloader.update_url_credentials(url1, url2)

    # Then I see that the final URL version is just the second URL with the auth
    # info from the first one
    final_url.should.equal(
        'http://user:almost-safe-password@domain.com/another/path/to/a/cooler/resource.html')


def test_update_url_credentials_not_from_the_same_server():
    "update_url_credentials() Should just use the second URL if the URLS are pointing to different services"

    # Given that I have a URL with authentication info from domain1.com
    url1 = 'http://user:passwd@domain1.com/resource1.html'

    # And another URL without auth info from domain2.com
    url2 = 'http://domain2.com/resource2.html'

    # When I update the second one based on the first one
    final_url = downloader.update_url_credentials(url1, url2)

    # Then I see that the final URL is just a copy of the second URL
    final_url.should.equal(url2)


@patch('curdling.services.downloader.util')
def test_pool_retrieve_no_redirect(util):
    ("http_retrieve() Should retrieve a URL and return a tuple "
     "containing the response and the final URL of the retrieved resource")

    # Background:
    # util.get_auth_info_from_url returns a fake dictionary
    util.get_auth_info_from_url.return_value = {'foo': 'bar'}

    # Given a mocked response
    pool = Mock()
    pool.request.return_value = Mock(headers={})

    # When I retrieve a URL
    _, url = downloader.http_retrieve(pool, 'http://github.com')

    # Then the url should be the same as requested
    url.should.equal('http://github.com')
    util.get_auth_info_from_url.assert_called_once_with('http://github.com')

    # And that the request should be executed with the correct
    # parameters
    pool.request.assert_called_once_with(
        'GET', 'http://github.com',
        headers={'foo': 'bar'},
        preload_content=False,
        redirect=False,
    )


@patch('curdling.services.downloader.util')
def test_http_retrieve(util):
    "http_retrieve() Should follow redirects and return the final URL"

    # Background:
    # util.get_auth_info_from_url returns a fake dictionary
    util.get_auth_info_from_url.return_value = {}

    # Given a mocked response
    pool = Mock()
    pool.request.side_effect = [
        Mock(headers={'location': 'http://bitbucket.com'}),
        Mock(headers={}),
    ]

    # When I retrieve a URL
    response, url = downloader.http_retrieve(pool, 'http://github.com')

    # Then the url should be the output of the redirect
    url.should.equal('http://bitbucket.com')

    # Even though we originally requested a different one
    list(pool.request.call_args_list).should.equal([
        call('GET', 'http://github.com', redirect=False, headers={}, preload_content=False),
        call('GET', 'http://bitbucket.com', redirect=False, headers={}, preload_content=False),
    ])


@patch('curdling.services.downloader.util')
def test_http_retrieve_max_redirects(util):
    "http_retrieve() Should limit the number of redirects"

    # Background:
    # util.get_auth_info_from_url returns a fake dictionary
    util.get_auth_info_from_url.return_value = {}

    # Given a mocked response that *always* returns a resource that
    # redirects to a different URL
    pool = Mock()
    pool.request.return_value = Mock(headers={'location': 'http://see-the-other-side.com'})

    # When I retrieve a URL with an infinite redirect flow; I see that
    # the downloader notices that and raises the proper exception
    downloader.http_retrieve.when.called_with(pool, 'http://see-the-other-side.com').should.throw(
        TooManyRedirects, 'Too many redirects'
    )

    # And that the limit of redirects is fixed manually to
    pool.request.call_args_list.should.have.length_of(20)


@patch('curdling.services.downloader.util')
def test_http_retrieve_relative_location(util):
    "http_retrieve() Should deal with relative paths on Location"

    # Background:
    # util.get_auth_info_from_url returns a fake dictionary
    util.get_auth_info_from_url.return_value = {}

    # Given a mocked response that returns a relative Location URL
    pool = Mock()
    pool.request.side_effect = [
        Mock(headers={'location': '/a/relative/url'}),
        Mock(headers={}),
    ]

    # When I check that
    downloader.http_retrieve(pool, 'http://bitbucket.com/')

    list(pool.request.call_args_list).should.equal([
        call('GET', 'http://bitbucket.com/', headers={}, preload_content=False, redirect=False),
        call('GET', 'http://bitbucket.com/a/relative/url', headers={}, preload_content=False, redirect=False),
    ])


@patch('curdling.services.downloader.util')
@patch('curdling.services.downloader.find_packages')
def test_aggregating_locator_locate(find_packages, util):
    ("AggregatingLocator#locate should return the first package "
     "that matches the given version")
    # Background:

    # parse_requirement is mocked and will return a mocked pkg
    pkg = util.parse_requirement.return_value

    # find_packages will return a package right away
    find_packages.return_value = 'the awesome "foo" package :)'


    # Specification:

    # Given a mocked locator
    locator = Mock()

    # And that the AggregatingLocator has a list containing that one locator
    class TestLocator(downloader.AggregatingLocator):
        def __init__(self):
            self.locators = [locator]

    # And an instance of AggregatingLocator
    instance = TestLocator()

    # When I try to locate a package with certain requirement
    found = instance.locate("foo==1.1.1")

    # Then it should be the expected package
    found.should.equal('the awesome "foo" package :)')


def test_pypilocator_get_project():
    ("PyPiLocator#_get_project should fetch based on the base_url")
    # Given an instance of PyPiLocator that mocks out the _fetch method
    instance = TestPyPiLocator("http://github.com")
    instance._fetch = Mock()

    # When _get_project gets called
    response = instance._get_project("forbiddenfruit")

    # Then it should have called _fetch
    instance._fetch.assert_called_once_with(
        u'http://github.com/forbiddenfruit/',
        u'forbiddenfruit',
    )


def test_visit_link_when_platform_dependent():
    ("PyPiLocator#_visit_link() should return (None, None) "
     "if link is platform dependent")

    # Given an instance of PyPiLocator
    instance = TestPyPiLocator("http://github.com")
    # And that calling _is_platform_dependent will return True
    instance._is_platform_dependent = Mock(return_value=True)

    # When I call _visit_link
    result = instance._visit_link("github", "some-link")

    # Then it should be a tuple with 2 `None` items
    result.should.equal((None, None))


def test_visit_link_when_not_platform_dependent():
    ("PyPiLocator#_visit_link() should return ('package-name', 'version') "
     "when link is not platform dependent")

    # Given an instance of PyPiLocator that mocks out the expected
    # private method calls
    class PyPiLocatorMock(TestPyPiLocator):
        _is_platform_dependent = Mock(return_value=False)
        def convert_url_to_download_info(self, link, project_name):
            return "HELLO, I AM A PROJECT INFO"

        def _update_version_data(self, versions, info):
            versions['sure'] = '4.0'
            info.should.equal('HELLO, I AM A PROJECT INFO')

    # And an instance of the locator
    instance = PyPiLocatorMock('http://curdling.io')

    # When I call _visit_link
    result = instance._visit_link('package-name', 'some-link')

    # Then it should be a tuple with 2
    result.should.equal(('sure', '4.0'))


def test_pypilocator_fetch_when_page_is_falsy():
    ("PyPiLocator#_fetch() should return empty if "
     "get_page returns a falsy value")

    # Given an instance of PyPiLocator that mocks the get_page method
    # so it returns None
    class PyPiLocatorMock(TestPyPiLocator):
        get_page = Mock(return_value=None)

    # And an instance of the locator
    instance = PyPiLocatorMock('http://curdling.io')

    # When I try to fetch a url
    response = instance._fetch('http://somewhere.com/package', 'some-name')

    # Then it should be an empty dictionary
    response.should.be.a(dict)
    response.should.be.empty


def test_pypilocator_fetch_when_page_links_are_falsy():
    ("PyPiLocator#_fetch() should return empty if "
     "get_page returns a page with no links")

    # Given a page that has no links
    page = Mock(links=[])

    # And that PyPiLocator#get_page returns that page
    class PyPiLocatorMock(TestPyPiLocator):
        get_page = Mock(return_value=page)

    # And an instance of the locator
    instance = PyPiLocatorMock('http://curdling.io')

    # When I try to fetch a url
    response = instance._fetch('http://somewhere.com/package', 'some-name')

    # Then it should be an empty dictionary
    response.should.be.a(dict)
    response.should.be.empty


def test_pypilocator_fetch_when_not_seen():
    ("PyPiLocator#_fetch() should visit an unseen link and "
     "grab its distribution into a dict")

    # Given a page that has one link
    page = Mock(links=[('http://someserver.com/package.tgz', 'some-rel')])

    # Given an instance of PyPiLocator that mocks the get_page method
    # to return a page with no links
    class PyPiLocatorMock(TestPyPiLocator):
        get_page = Mock(return_value=page)
        _visit_link = Mock(return_value=('0.0.1', 'distribution'))

    # And an instance of the locator
    instance = PyPiLocatorMock('http://curdling.io')

    # When I try to fetch a url
    response = instance._fetch('http://somewhere.com/package', 'some-name')

    # Then it should equal the existing distribution
    response.should.equal({
        '0.0.1': 'distribution'
    })


def test_finder_handle():
    "Finder#handle() should be able to find requirements"

    # Given that I have a Finder instance that returns the given distribution
    service = downloader.Finder(index=Mock())
    distribution = Mock(
        metadata=Mock(download_url='http://srv.com/pkg-0.1.zip'),
        locator=Mock(base_url='http://usr:passwd@srv.com/simple'))
    service.locator = Mock(locate=Mock(return_value=distribution))

    # When I call the service handler with a URL requirement
    service.handle('tests', {'requirement': 'pkg'}).should.equal({
        'requirement': 'pkg',
        'locator_url': 'http://usr:passwd@srv.com/simple',
        'url': 'http://srv.com/pkg-0.1.zip'
    })


def test_finder_handle_not_found():
    "Finder#handle() should raise ReportableError when it doesn't find the requirement"

    # Given that I have a Downloader instance
    service = downloader.Finder(index=Mock())
    service.locator = Mock(locate=Mock(return_value=None))

    # When I call the service handler with a URL requirement
    service.handle.when.called_with('tests', {'requirement': 'package'}).should.throw(
        ReportableError, 'Requirement `package\' not found'
    )


def test_downloader_handle():
    "Downloader#handle() should return the `tarball' path"

    # Given that I have a Downloader instance
    service = downloader.Downloader(index=Mock())
    service._download_http = Mock(return_value=('tarball', 'package-0.1.zip'))

    # When I call the service handler with a URL requirement
    tarball = service.handle('tests', {
        'requirement': 'package (0.1)',
        'url': 'http://host/path/package-0.1.zip',
    })

    # Then I see that the right tarball name was returned
    tarball.should.equal({
        'requirement': 'package (0.1)',
        'tarball': 'package-0.1.zip',
    })


def test_downloader_handle_return_wheel():
    "Downloader#handle() should return the `wheel' path when it downloads a whl file"

    # Given that I have a Downloader instance
    service = downloader.Downloader(index=Mock())
    service._download_http = Mock(
        return_value=('wheel', 'package-0.1-cp27-none-macosx_10_8_x86_64.whl'))

    # When I call the service handler with a URL requirement
    tarball = service.handle('tests', {
        'requirement': 'package (0.1)',
        'url': 'http://host/path/package-0.1-cp27-none-macosx_10_8_x86_64.whl',
    })

    # Then I see that the right tarball name was returned
    tarball.should.equal({
        'requirement': 'package (0.1)',
        'wheel': 'package-0.1-cp27-none-macosx_10_8_x86_64.whl',
    })


def test_downloader_download():
    "Downloader#download() Should call the right handler given the protocol of the link being processed"

    # Given that I have a Downloader instance
    service = downloader.Downloader()

    # And I mock all the actual protocol handlers (`_download_*()`)
    service._download_http = Mock()
    service._download_git = Mock()
    service._download_hg = Mock()
    service._download_svn = Mock()

    # When I try to download certain URLs
    service.download('http://source.com/blah')
    service.download('git+ssh://github.com/clarete/curdling.git')
    service.download('hg+http://hg.python.org.com/cpython')
    service.download('svn+http://svn.oldschool.com/repo')

    # Then I see that the right handlers were called. Notice that the vcs
    # prefixes will be stripped out
    service._download_http.assert_called_once_with('http://source.com/blah')
    service._download_git.assert_called_once_with('ssh://github.com/clarete/curdling.git')
    service._download_hg.assert_called_once_with('http://hg.python.org.com/cpython')
    service._download_svn.assert_called_once_with('http://svn.oldschool.com/repo')


def test_downloader_download_with_locator():
    "Downloader#download() should reuse the authentication information present in the locator's URL"

    # Given that I have a Downloader instance
    service = downloader.Downloader()

    # And I mock all the actual HTTP handler
    service._download_http = Mock()

    # When I download an HTTP link with a locator
    service.download('http://source.com/blah', 'http://user:passwd@source.com')

    # Then I see URL forwarded to the handler still have the authentication info
    service._download_http.assert_called_once_with('http://user:passwd@source.com/blah')


def test_downloader_download_bad_url():
    "Downloader#download() Should raise an exception if we can't handle the link"

    # Given that I have a Downloader instance
    service = downloader.Downloader()

    # When I try to download a weird link
    service.download.when.called_with('weird link').should.throw(
        UnknownURL,
        '''\
   "weird link"
   
   Your URL looks wrong. Make sure it's a valid HTTP
   link or a valid VCS link prefixed with the name of
   the VCS of your choice. Eg.:
   
    $ curd install https://pypi.python.org/simple/curdling/curdling-0.1.2.tar.gz
    $ curd install git+ssh://github.com/clarete/curdling.git''')


@patch('curdling.services.downloader.http_retrieve')
def test_downloader_download_http_handler(http_retrieve):
    "Downloader#_download_http() should download HTTP links"

    # Given that I have a Downloader instance
    service = downloader.Downloader(index=Mock())

    # And I patch the opener so we'll just pretend the HTTP IO is happening
    response = Mock(status=200)
    response.headers.get.return_value = ''
    http_retrieve.return_value = (response, None)

    # When I download an HTTP link
    service._download_http('http://blah/package.tar.gz')

    # Then I see that the URL was properly forward to the indexer
    service.index.from_data.assert_called_once_with(
        'http://blah/package.tar.gz',
        response.read.return_value)

    # And Then I see that the response was read raw to avoid problems with
    # gzipped packages; The curdler component will do that!
    response.read.assert_called_once_with(
        cache_content=True, decode_content=False)


@patch('curdling.services.downloader.http_retrieve')
def test_downloader_download_http_handler_blow_up_on_error(http_retrieve):
    "Downloader#_download_http() should handle HTTP status != 200"

    # Given that I have a Downloader instance
    service = downloader.Downloader()

    # And I patch the opener so we'll just pretend the HTTP IO is happening
    response = Mock(status=500)
    response.headers.get.return_value = ''
    http_retrieve.return_value = response, None

    # When I download an HTTP link
    service._download_http.when.called_with('http://blah/package.tar.gz').should.throw(
        ReportableError,
        'Failed to download url `http://blah/package.tar.gz\': 500 (Internal Server Error)'
    )


@patch('curdling.services.downloader.http_retrieve')
def test_downloader_download_http_handler_use_right_url_on_redirect(http_retrieve):
    "Downloader#_download_http() should handle HTTP status = 302"

    # Given that I have a Downloader instance
    service = downloader.Downloader(index=Mock())

    # And I patch the opener so we'll just pretend the HTTP IO is happening
    response = Mock(status=200)
    response.headers.get.side_effect = {}.get
    http_retrieve.return_value = response, 'pkg-0.1.tar.gz'

    # When I download an HTTP link that redirects to another location
    service._download_http('http://pkg.io/download')

    # Then I see the package name being read from the redirected URL,
    # not from the original one.
    service.index.from_data.assert_called_once_with(
        'pkg-0.1.tar.gz', response.read.return_value,
    )


@patch('curdling.services.downloader.http_retrieve')
def test_downloader_download_http_handler_use_content_disposition(http_retrieve):
    "Downloader#_download_http() should know how to use the header Content-Disposition to name the new file"

    # Given that I have a Downloader instance
    service = downloader.Downloader(index=Mock())

    # And I patch the opener so we'll just pretend the HTTP IO is happening
    response = Mock(status=200)
    response.headers.get.return_value = 'attachment; filename=sure-0.1.1.tar.gz'
    http_retrieve.return_value = response, None

    # When I download an HTTP link
    service._download_http('http://blah/package.tar.gz')

    # Then I see the file name forward to the index was the one found in the header
    service.index.from_data.assert_called_once_with(
        'sure-0.1.1.tar.gz', response.read.return_value)


@patch('curdling.services.downloader.http_retrieve')
def test_downloader_download_http_handler_use_content_disposition_with_quotes(http_retrieve):
    "Downloader#_download_http() should know how to use the header Content-Disposition to name the new file and strip the quotes"

    # Given that I have a Downloader instance
    service = downloader.Downloader(index=Mock())

    # And I patch the opener so we'll just pretend the HTTP IO is happening
    response = Mock(status=200)
    response.headers.get.return_value = 'attachment; filename="sure-0.1.1.tar.gz"'
    http_retrieve.return_value = response, None

    # When I download an HTTP link
    service._download_http('http://blah/package.tar.gz')

    # Then I see the file name forward to the index was the one found in the header
    service.index.from_data.assert_called_once_with(
        'sure-0.1.1.tar.gz', response.read.return_value)


@patch('curdling.services.downloader.tempfile')
@patch('curdling.services.downloader.util')
def test_downloader_download_vcs_handlers(util, tempfile):
    "Downloader#_download_{git,hg,svn}() should call their respective shell commands to retrieve a VCS URL"

    tempfile.mkdtemp.return_value = 'tmp'

    # Given that I have a Downloader instance
    service = downloader.Downloader()

    # When I call the VCS handlers
    service._download_git('git-url')
    service._download_hg('hg-url')
    service._download_svn('svn-url')

    # Then I see that all the calls for the shell commands were done properly
    list(util.execute_command.call_args_list).should.equal([
        call('git', 'clone', 'git-url', 'tmp'),
        call('hg', 'clone', 'hg-url', 'tmp'),
        call('svn', 'co', '-q', 'svn-url', 'tmp'),
    ])


@patch('curdling.services.downloader.tempfile')
@patch('curdling.services.downloader.util')
def test_downloader_download_vcs_handlers_with_rev(util, tempfile):
    "Downloader#_download_{git,hg,svn}() Should find the revision informed in the URL and point the retrieved code to it"

    tempfile.mkdtemp.return_value = 'tmp'

    # Given that I have a Downloader instance
    service = downloader.Downloader()

    # When I call the VCS handlers with a revision
    service._download_git('git-url@rev')
    service._download_hg('hg-url@rev')
    service._download_svn('svn-url@rev')

    # Then I see that all the calls for the shell commands were done properly
    list(util.execute_command.call_args_list).should.equal([
        call('git', 'clone', 'git-url', 'tmp'),
        call('git', 'reset', '--hard', 'rev', cwd='tmp'),
        call('hg', 'clone', 'hg-url', 'tmp'),
        call('hg', 'update', '-q', 'rev', cwd='tmp'),
        call('svn', 'co', '-q', '-r', 'rev', 'svn-url', 'tmp'),
    ])
