# This file is part of Buildbot.  Buildbot is free software: you can
# redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright Buildbot Team Members

import datetime

from dateutil.tz import tzutc

from mock import Mock

from twisted.internet import defer
from twisted.trial import unittest

from buildbot import config
from buildbot.plugins import util
from buildbot.process.properties import Interpolate
from buildbot.process.results import FAILURE
from buildbot.process.results import SUCCESS
from buildbot.reporters.bitbucketserver import HTTP_CREATED
from buildbot.reporters.bitbucketserver import HTTP_PROCESSED
from buildbot.reporters.bitbucketserver import BitbucketServerCoreAPIStatusPush
from buildbot.reporters.bitbucketserver import BitbucketServerPRCommentPush
from buildbot.reporters.bitbucketserver import BitbucketServerStatusPush
from buildbot.reporters.generators.build import BuildStatusGenerator
from buildbot.reporters.generators.buildset import BuildSetStatusGenerator
from buildbot.reporters.message import MessageFormatter
from buildbot.test.fake import fakemaster
from buildbot.test.fake import httpclientservice as fakehttpclientservice
from buildbot.test.util.config import ConfigErrorsMixin
from buildbot.test.util.logging import LoggingMixin
from buildbot.test.util.misc import TestReactorMixin
from buildbot.test.util.reporter import ReporterTestMixin
from buildbot.test.util.warnings import assertProducesWarnings
from buildbot.warnings import DeprecatedApiWarning

HTTP_NOT_FOUND = 404


class TestException(Exception):
    pass


class TestBitbucketServerStatusPush(TestReactorMixin, unittest.TestCase,
                                    ReporterTestMixin, LoggingMixin):

    @defer.inlineCallbacks
    def setupReporter(self, **kwargs):
        self.setUpTestReactor()
        # ignore config error if txrequests is not installed
        self.patch(config, '_errors', Mock())
        self.master = fakemaster.make_master(self, wantData=True, wantDb=True,
                                             wantMq=True)

        self._http = yield fakehttpclientservice.HTTPClientService.getService(
            self.master, self,
            'serv', auth=('username', 'passwd'),
            debug=None, verify=None)
        self.sp = BitbucketServerStatusPush("serv", Interpolate("username"),
                                            Interpolate("passwd"), **kwargs)
        yield self.sp.setServiceParent(self.master)
        yield self.master.startService()

    @defer.inlineCallbacks
    def tearDown(self):
        yield self.master.stopService()

    @defer.inlineCallbacks
    def _check_start_and_finish_build(self, build):
        # we make sure proper calls to txrequests have been made
        self._http.expect(
            'post',
            '/rest/build-status/1.0/commits/d34db33fd43db33f',
            json={'url': 'http://localhost:8080/#builders/79/builds/0',
                  'state': 'INPROGRESS', 'key': 'Builder0',
                  'description': 'Build started.'},
            code=HTTP_PROCESSED)
        self._http.expect(
            'post',
            '/rest/build-status/1.0/commits/d34db33fd43db33f',
            json={'url': 'http://localhost:8080/#builders/79/builds/0',
                  'state': 'SUCCESSFUL', 'key': 'Builder0',
                  'description': 'Build done.'},
            code=HTTP_PROCESSED)
        self._http.expect(
            'post',
            '/rest/build-status/1.0/commits/d34db33fd43db33f',
            json={'url': 'http://localhost:8080/#builders/79/builds/0',
                  'state': 'FAILED', 'key': 'Builder0',
                  'description': 'Build done.'})
        build['complete'] = False
        yield self.sp._got_event(('builds', 20, 'new'), build)
        build['complete'] = True
        yield self.sp._got_event(('builds', 20, 'finished'), build)
        build['results'] = FAILURE
        yield self.sp._got_event(('builds', 20, 'finished'), build)

    @defer.inlineCallbacks
    def test_basic(self):
        self.setupReporter()
        build = yield self.insert_build_finished(SUCCESS)
        yield self._check_start_and_finish_build(build)

    @defer.inlineCallbacks
    def test_setting_options(self):
        self.setupReporter(statusName='Build', startDescription='Build started.',
                           endDescription='Build finished.')
        build = yield self.insert_build_finished(SUCCESS)
        # we make sure proper calls to txrequests have been made
        self._http.expect(
            'post',
            '/rest/build-status/1.0/commits/d34db33fd43db33f',
            json={'url': 'http://localhost:8080/#builders/79/builds/0',
                  'state': 'INPROGRESS', 'key': 'Builder0',
                  'name': 'Build', 'description': 'Build started.'},
            code=HTTP_PROCESSED)
        self._http.expect(
            'post',
            '/rest/build-status/1.0/commits/d34db33fd43db33f',
            json={'url': 'http://localhost:8080/#builders/79/builds/0',
                  'state': 'SUCCESSFUL', 'key': 'Builder0',
                  'name': 'Build', 'description': 'Build finished.'},
            code=HTTP_PROCESSED)
        self._http.expect(
            'post',
            '/rest/build-status/1.0/commits/d34db33fd43db33f',
            json={'url': 'http://localhost:8080/#builders/79/builds/0',
                  'state': 'FAILED', 'key': 'Builder0',
                  'name': 'Build', 'description': 'Build finished.'},
            code=HTTP_PROCESSED)
        build['complete'] = False
        yield self.sp._got_event(('builds', 20, 'new'), build)
        build['complete'] = True
        yield self.sp._got_event(('builds', 20, 'finished'), build)
        build['results'] = FAILURE
        yield self.sp._got_event(('builds', 20, 'finished'), build)

    @defer.inlineCallbacks
    def test_error(self):
        self.setupReporter()
        build = yield self.insert_build_finished(SUCCESS)
        # we make sure proper calls to txrequests have been made
        self._http.expect(
            'post',
            '/rest/build-status/1.0/commits/d34db33fd43db33f',
            json={'url': 'http://localhost:8080/#builders/79/builds/0',
                  'state': 'INPROGRESS', 'key': 'Builder0',
                  'description': 'Build started.'},
            code=HTTP_NOT_FOUND,
            content_json={
                "error_description": "This commit is unknown to us",
                "error": "invalid_commit"})
        build['complete'] = False
        self.setUpLogging()
        yield self.sp._got_event(('builds', 20, 'new'), build)
        self.assertLogged('404: Unable to send Bitbucket Server status')

    @defer.inlineCallbacks
    def test_basic_with_no_revision(self):
        yield self.setupReporter()
        old_test_revision = self.TEST_REVISION
        try:
            self.TEST_REVISION = None
            build = yield self.insert_build_finished(SUCCESS)
        finally:
            self.TEST_REVISION = old_test_revision
        self.setUpLogging()
        # we don't expect any request
        build['complete'] = False
        yield self.sp._got_event(('builds', 20, 'new'), build)
        self.assertLogged("Unable to get the commit hash")
        build['complete'] = True
        yield self.sp._got_event(('builds', 20, 'finished'), build)
        build['results'] = FAILURE
        yield self.sp._got_event(('builds', 20, 'finished'), build)


class BitbucketServerStatusPushDeprecatedSend(BitbucketServerStatusPush):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.send_called_count = 0

    @defer.inlineCallbacks
    def send(self, build):
        self.send_called_count += 1
        yield super().send(build)


class TestBitbucketServerStatusPushDeprecatedSend(TestReactorMixin, unittest.TestCase,
                                                  ReporterTestMixin, LoggingMixin):

    @defer.inlineCallbacks
    def setupReporter(self, **kwargs):
        self.setUpTestReactor()
        # ignore config error if txrequests is not installed
        self.patch(config, '_errors', Mock())
        self.master = fakemaster.make_master(self, wantData=True, wantDb=True,
                                             wantMq=True)

        self._http = yield fakehttpclientservice.HTTPClientService.getService(
            self.master, self,
            'serv', auth=('username', 'passwd'),
            debug=None, verify=None)
        self.sp = BitbucketServerStatusPushDeprecatedSend("serv", Interpolate("username"),
                                                          Interpolate("passwd"), **kwargs)
        yield self.sp.setServiceParent(self.master)
        yield self.master.startService()

    @defer.inlineCallbacks
    def tearDown(self):
        yield self.master.stopService()

    @defer.inlineCallbacks
    def _check_start_and_finish_build(self, build):
        # we make sure proper calls to txrequests have been made
        self._http.expect(
            'post',
            '/rest/build-status/1.0/commits/d34db33fd43db33f',
            json={'url': 'http://localhost:8080/#builders/79/builds/0',
                  'state': 'INPROGRESS', 'key': 'Builder0',
                  'description': 'Build started.'},
            code=HTTP_PROCESSED)
        self._http.expect(
            'post',
            '/rest/build-status/1.0/commits/d34db33fd43db33f',
            json={'url': 'http://localhost:8080/#builders/79/builds/0',
                  'state': 'SUCCESSFUL', 'key': 'Builder0',
                  'description': 'Build done.'},
            code=HTTP_PROCESSED)
        self._http.expect(
            'post',
            '/rest/build-status/1.0/commits/d34db33fd43db33f',
            json={'url': 'http://localhost:8080/#builders/79/builds/0',
                  'state': 'FAILED', 'key': 'Builder0',
                  'description': 'Build done.'})
        build['complete'] = False
        with assertProducesWarnings(DeprecatedApiWarning,
                                    message_pattern='send\\(\\) in reporters has been deprecated'):

            yield self.sp._got_event(('builds', 20, 'new'), build)
        build['complete'] = True
        with assertProducesWarnings(DeprecatedApiWarning,
                                    message_pattern='send\\(\\) in reporters has been deprecated'):

            yield self.sp._got_event(('builds', 20, 'finished'), build)
        build['results'] = FAILURE
        with assertProducesWarnings(DeprecatedApiWarning,
                                    message_pattern='send\\(\\) in reporters has been deprecated'):
            yield self.sp._got_event(('builds', 20, 'finished'), build)

        self.assertEqual(self.sp.send_called_count, 3)

    @defer.inlineCallbacks
    def test_basic(self):
        self.setupReporter()
        build = yield self.insert_build_finished(SUCCESS)
        yield self._check_start_and_finish_build(build)


class TestBitbucketServerCoreAPIStatusPush(ConfigErrorsMixin, TestReactorMixin, unittest.TestCase,
                                           ReporterTestMixin, LoggingMixin):

    @defer.inlineCallbacks
    def setupReporter(self, token=None, **kwargs):
        self.setUpTestReactor()
        # ignore config error if txrequests is not installed
        self.patch(config, '_errors', Mock())
        self.master = fakemaster.make_master(self, wantData=True, wantDb=True,
                                             wantMq=True)

        http_headers = {} if token is None else {'Authorization': 'Bearer tokentoken'}

        self._http = yield fakehttpclientservice.HTTPClientService.getService(
            self.master, self,
            'serv', auth=('username', 'passwd'), headers=http_headers,
            debug=None, verify=None)
        self.sp = BitbucketServerCoreAPIStatusPush(
            "serv", token=token, auth=(Interpolate("username"), Interpolate("passwd")), **kwargs)
        yield self.sp.setServiceParent(self.master)
        yield self.master.startService()

    def setUp(self):
        self.master = None

    @defer.inlineCallbacks
    def tearDown(self):
        if self.master and self.master.running:
            yield self.master.stopService()

    @defer.inlineCallbacks
    def _check_start_and_finish_build(self, build, parentPlan=False):
        # we make sure proper calls to txrequests have been made

        _name = "Builder_parent #1 \u00BB Builder0 #0" if parentPlan else "Builder0 #0"
        _parent = "Builder_parent" if parentPlan else "Builder0"

        self._http.expect(
            'post',
            '/rest/api/1.0/projects/example.org/repos/repo/commits/d34db33fd43db33f/builds',
            json={'name': _name, 'description': 'Build started.', 'key': 'Builder0',
                  'url': 'http://localhost:8080/#builders/79/builds/0',
                  'ref': 'refs/heads/master', 'buildNumber': '0', 'state': 'INPROGRESS',
                  'parent': _parent, 'duration': None, 'testResults': None},
            code=HTTP_PROCESSED)
        self._http.expect(
            'post',
            '/rest/api/1.0/projects/example.org/repos/repo/commits/d34db33fd43db33f/builds',
            json={'name': _name, 'description': 'Build done.', 'key': 'Builder0',
                  'url': 'http://localhost:8080/#builders/79/builds/0',
                  'ref': 'refs/heads/master', 'buildNumber': '0', 'state': 'SUCCESSFUL',
                  'parent': _parent, 'duration': 10000, 'testResults': None},
            code=HTTP_PROCESSED)
        self._http.expect(
            'post',
            '/rest/api/1.0/projects/example.org/repos/repo/commits/d34db33fd43db33f/builds',
            json={'name': _name, 'description': 'Build done.', 'key': 'Builder0',
                  'url': 'http://localhost:8080/#builders/79/builds/0',
                  'ref': 'refs/heads/master', 'buildNumber': '0', 'state': 'FAILED',
                  'parent': _parent, 'duration': 10000, 'testResults': None},
            code=HTTP_PROCESSED)
        build['started_at'] = datetime.datetime(2019, 4, 1, 23, 38, 33, 154354, tzinfo=tzutc())
        build['complete'] = False
        yield self.sp._got_event(('builds', 20, 'new'), build)
        build["complete_at"] = datetime.datetime(2019, 4, 1, 23, 38, 43, 154354, tzinfo=tzutc())
        build['complete'] = True
        yield self.sp._got_event(('builds', 20, 'finished'), build)
        build['results'] = FAILURE
        yield self.sp._got_event(('builds', 20, 'finished'), build)

    def test_config_no_base_url(self):
        with self.assertRaisesConfigError("Parameter base_url has to be given"):
            BitbucketServerCoreAPIStatusPush(base_url=None)

    def test_config_auth_and_token_mutually_exclusive(self):
        with self.assertRaisesConfigError(
                "Only one authentication method can be given (token or auth)"):
            BitbucketServerCoreAPIStatusPush("serv", token="x", auth=("username", "passwd"))

    @defer.inlineCallbacks
    def test_basic(self):
        yield self.setupReporter()
        build = yield self.insert_build_finished(SUCCESS)
        yield self._check_start_and_finish_build(build)

    @defer.inlineCallbacks
    def test_with_parent(self):
        yield self.setupReporter()
        build = yield self.insert_build_finished(SUCCESS, parent_plan=True)
        yield self._check_start_and_finish_build(build, parentPlan=True)

    @defer.inlineCallbacks
    def test_with_token(self):
        yield self.setupReporter(token='tokentoken')
        build = yield self.insert_build_finished(SUCCESS)
        yield self._check_start_and_finish_build(build)

    @defer.inlineCallbacks
    def test_error_setup_status(self):
        yield self.setupReporter()

        @defer.inlineCallbacks
        def raise_deferred_exception(**kwargs):
            raise TestException()

        self.sp.createStatus = Mock(side_effect=raise_deferred_exception)
        build = yield self.insert_build_finished(SUCCESS)
        yield self.sp._got_event(('builds', 20, 'new'), build)

        self.assertEqual(len(self.flushLoggedErrors(TestException)), 1)

    @defer.inlineCallbacks
    def test_error(self):
        self.setupReporter()
        build = yield self.insert_build_finished(SUCCESS)
        self.setUpLogging()
        # we make sure proper calls to txrequests have been made
        self._http.expect(
            'post',
            '/rest/api/1.0/projects/example.org/repos/repo/commits/d34db33fd43db33f/builds',
            json={'name': 'Builder0 #0', 'description': 'Build started.', 'key': 'Builder0',
                  'url': 'http://localhost:8080/#builders/79/builds/0',
                  'ref': 'refs/heads/master', 'buildNumber': '0', 'state': 'INPROGRESS',
                  'parent': 'Builder0', 'duration': None, 'testResults': None},
            code=HTTP_NOT_FOUND)
        build['complete'] = False
        yield self.sp._got_event(('builds', 20, 'new'), build)
        self.assertLogged('404: Unable to send Bitbucket Server status')

    @defer.inlineCallbacks
    def test_with_full_ref(self):
        yield self.setupReporter()
        old_test_branch = self.TEST_BRANCH
        try:
            self.TEST_BRANCH = "refs/heads/master"
            build = yield self.insert_build_finished(SUCCESS)
        finally:
            self.TEST_BRANCH = old_test_branch
        yield self._check_start_and_finish_build(build)

    @defer.inlineCallbacks
    def test_with_no_ref(self):
        yield self.setupReporter()
        old_test_branch = self.TEST_BRANCH
        try:
            self.TEST_BRANCH = None
            build = yield self.insert_build_finished(SUCCESS)
        finally:
            self.TEST_BRANCH = old_test_branch
        self.setUpLogging()
        self._http.expect(
            'post',
            '/rest/api/1.0/projects/example.org/repos/repo/commits/d34db33fd43db33f/builds',
            json={'name': 'Builder0 #0', 'description': 'Build started.', 'key': 'Builder0',
                  'url': 'http://localhost:8080/#builders/79/builds/0',
                  'ref': None, 'buildNumber': '0', 'state': 'INPROGRESS',
                  'parent': 'Builder0', 'duration': None, 'testResults': None},
            code=HTTP_PROCESSED)
        build['complete'] = False
        yield self.sp._got_event(('builds', 20, 'new'), build)
        self.assertLogged("WARNING: Unable to resolve ref for SSID: 234.")

    @defer.inlineCallbacks
    def test_with_no_revision(self):
        yield self.setupReporter()
        old_test_revision = self.TEST_REVISION
        try:
            self.TEST_REVISION = None
            build = yield self.insert_build_finished(SUCCESS)
        finally:
            self.TEST_REVISION = old_test_revision
        self.setUpLogging()
        # we don't expect any request
        build['complete'] = False
        yield self.sp._got_event(('builds', 20, 'new'), build)
        self.assertLogged("Unable to get the commit hash for SSID: 234")

    @defer.inlineCallbacks
    def test_with_no_repo(self):
        yield self.setupReporter()
        old_test_repo = self.TEST_REPO
        try:
            self.TEST_REPO = None
            build = yield self.insert_build_finished(SUCCESS)
        finally:
            self.TEST_REPO = old_test_repo
        self.setUpLogging()
        # we don't expect any request
        build['complete'] = False
        yield self.sp._got_event(('builds', 20, 'new'), build)
        self.assertLogged("Unable to parse repository info from 'None' for SSID: 234")

    @defer.inlineCallbacks
    def test_with_renderers(self):
        @util.renderer
        def r_testresults(props):
            return {
                "failed": props.getProperty("unittests_failed", 0),
                "skipped": props.getProperty("unittests_skipped", 0),
                "successful": props.getProperty("unittests_successful", 0),
            }

        @util.renderer
        def r_duration(props):
            return props.getProperty("unittests_runtime")

        yield self.setupReporter(statusName=Interpolate("%(prop:plan_name)s"),
            statusSuffix=Interpolate(" [%(prop:unittests_os)s]"), buildNumber=Interpolate('100'),
            ref=Interpolate("%(prop:branch)s"), parentName=Interpolate("%(prop:master_plan)s"),
            testResults=r_testresults, duration=r_duration)
        old_test_props = self.TEST_PROPS
        try:
            self.TEST_PROPS = self.TEST_PROPS.copy()
            self.TEST_PROPS['unittests_failed'] = 0
            self.TEST_PROPS['unittests_skipped'] = 2
            self.TEST_PROPS['unittests_successful'] = 3
            self.TEST_PROPS['unittests_runtime'] = 50000
            self.TEST_PROPS['unittests_os'] = "win10"
            self.TEST_PROPS['plan_name'] = "Unittests"
            self.TEST_PROPS['master_plan'] = "Unittests-master"
            build = yield self.insert_build_finished(SUCCESS)
        finally:
            self.TEST_PROPS = old_test_props
        self._http.expect(
            'post',
            '/rest/api/1.0/projects/example.org/repos/repo/commits/d34db33fd43db33f/builds',
            json={'name': 'Unittests [win10]', 'description': 'Build done.', 'key': 'Builder0',
                  'url': 'http://localhost:8080/#builders/79/builds/0',
                  'ref': "refs/pull/34/merge", 'buildNumber': '100', 'state': 'SUCCESSFUL',
                  'parent': 'Unittests-master', 'duration': 50000, 'testResults': {'failed': 0,
                  'skipped': 2, 'successful': 3}},
            code=HTTP_PROCESSED)
        build['complete'] = True
        yield self.sp._got_event(('builds', 20, 'finished'), build)

    @defer.inlineCallbacks
    def test_with_test_results(self):
        yield self.setupReporter()
        old_test_props = self.TEST_PROPS
        try:
            self.TEST_PROPS = self.TEST_PROPS.copy()
            self.TEST_PROPS['tests_skipped'] = 2
            self.TEST_PROPS['tests_successful'] = 3
            build = yield self.insert_build_finished(SUCCESS)
        finally:
            self.TEST_PROPS = old_test_props
        self._http.expect(
            'post',
            '/rest/api/1.0/projects/example.org/repos/repo/commits/d34db33fd43db33f/builds',
            json={'name': 'Builder0 #0', 'description': 'Build done.', 'key': 'Builder0',
                  'url': 'http://localhost:8080/#builders/79/builds/0',
                  'ref': 'refs/heads/master', 'buildNumber': '0', 'state': 'SUCCESSFUL',
                  'parent': 'Builder0', 'duration': 10000, 'testResults': {'failed': 0,
                  'skipped': 2, 'successful': 3}},
            code=HTTP_PROCESSED)
        build['started_at'] = datetime.datetime(2019, 4, 1, 23, 38, 33, 154354, tzinfo=tzutc())
        build["complete_at"] = datetime.datetime(2019, 4, 1, 23, 38, 43, 154354, tzinfo=tzutc())
        build['complete'] = True
        yield self.sp._got_event(('builds', 20, 'finished'), build)

    @defer.inlineCallbacks
    def test_verbose(self):
        yield self.setupReporter(verbose=True)
        build = yield self.insert_build_finished(SUCCESS)
        self.setUpLogging()
        self._http.expect(
            'post',
            '/rest/api/1.0/projects/example.org/repos/repo/commits/d34db33fd43db33f/builds',
            json={'name': 'Builder0 #0', 'description': 'Build started.', 'key': 'Builder0',
                  'url': 'http://localhost:8080/#builders/79/builds/0',
                  'ref': "refs/heads/master", 'buildNumber': '0', 'state': 'INPROGRESS',
                  'parent': 'Builder0', 'duration': None, 'testResults': None},
            code=HTTP_PROCESSED)
        build['complete'] = False
        yield self.sp._got_event(('builds', 20, 'new'), build)
        self.assertLogged('Sending payload:')
        self.assertLogged('Status "INPROGRESS" sent for example.org/repo d34db33fd43db33f')


UNICODE_BODY = "body: \u00E5\u00E4\u00F6 text"
EXPECTED_API = '/rest/api/1.0/projects/PRO/repos/myrepo/pull-requests/20/comments'
PR_URL = "http://example.com/projects/PRO/repos/myrepo/pull-requests/20"


class BitbucketServerCoreAPIStatusPushDeprecatedSend(BitbucketServerCoreAPIStatusPush):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.send_called_count = 0

    @defer.inlineCallbacks
    def send(self, build):
        self.send_called_count += 1
        yield super().send(build)


class TestBitbucketServerCoreAPIStatusPushDeprecatedSend(ConfigErrorsMixin, TestReactorMixin,
                                                         unittest.TestCase,
                                                         ReporterTestMixin, LoggingMixin):

    @defer.inlineCallbacks
    def setupReporter(self, token=None, **kwargs):
        self.setUpTestReactor()
        # ignore config error if txrequests is not installed
        self.patch(config, '_errors', Mock())
        self.master = fakemaster.make_master(self, wantData=True, wantDb=True,
                                             wantMq=True)

        http_headers = {} if token is None else {'Authorization': 'Bearer tokentoken'}

        self._http = yield fakehttpclientservice.HTTPClientService.getService(
            self.master, self,
            'serv', auth=('username', 'passwd'), headers=http_headers,
            debug=None, verify=None)
        self.sp = BitbucketServerCoreAPIStatusPushDeprecatedSend(
            "serv", token=token, auth=(Interpolate("username"), Interpolate("passwd")), **kwargs)
        yield self.sp.setServiceParent(self.master)
        yield self.master.startService()

    def setUp(self):
        self.master = None

    @defer.inlineCallbacks
    def tearDown(self):
        if self.master and self.master.running:
            yield self.master.stopService()

    @defer.inlineCallbacks
    def _check_start_and_finish_build(self, build, parentPlan=False):
        # we make sure proper calls to txrequests have been made

        _name = "Builder_parent #1 \u00BB Builder0 #0" if parentPlan else "Builder0 #0"
        _parent = "Builder_parent" if parentPlan else "Builder0"

        self._http.expect(
            'post',
            '/rest/api/1.0/projects/example.org/repos/repo/commits/d34db33fd43db33f/builds',
            json={'name': _name, 'description': 'Build started.', 'key': 'Builder0',
                  'url': 'http://localhost:8080/#builders/79/builds/0',
                  'ref': 'refs/heads/master', 'buildNumber': '0', 'state': 'INPROGRESS',
                  'parent': _parent, 'duration': None, 'testResults': None},
            code=HTTP_PROCESSED)
        self._http.expect(
            'post',
            '/rest/api/1.0/projects/example.org/repos/repo/commits/d34db33fd43db33f/builds',
            json={'name': _name, 'description': 'Build done.', 'key': 'Builder0',
                  'url': 'http://localhost:8080/#builders/79/builds/0',
                  'ref': 'refs/heads/master', 'buildNumber': '0', 'state': 'SUCCESSFUL',
                  'parent': _parent, 'duration': 10000, 'testResults': None},
            code=HTTP_PROCESSED)
        self._http.expect(
            'post',
            '/rest/api/1.0/projects/example.org/repos/repo/commits/d34db33fd43db33f/builds',
            json={'name': _name, 'description': 'Build done.', 'key': 'Builder0',
                  'url': 'http://localhost:8080/#builders/79/builds/0',
                  'ref': 'refs/heads/master', 'buildNumber': '0', 'state': 'FAILED',
                  'parent': _parent, 'duration': 10000, 'testResults': None},
            code=HTTP_PROCESSED)
        build['started_at'] = datetime.datetime(2019, 4, 1, 23, 38, 33, 154354, tzinfo=tzutc())
        build['complete'] = False
        with assertProducesWarnings(DeprecatedApiWarning,
                                    message_pattern='send\\(\\) in reporters has been deprecated'):
            yield self.sp._got_event(('builds', 20, 'new'), build)
        build["complete_at"] = datetime.datetime(2019, 4, 1, 23, 38, 43, 154354, tzinfo=tzutc())
        build['complete'] = True
        with assertProducesWarnings(DeprecatedApiWarning,
                                    message_pattern='send\\(\\) in reporters has been deprecated'):
            yield self.sp._got_event(('builds', 20, 'finished'), build)
        build['results'] = FAILURE
        with assertProducesWarnings(DeprecatedApiWarning,
                                    message_pattern='send\\(\\) in reporters has been deprecated'):
            yield self.sp._got_event(('builds', 20, 'finished'), build)

        self.assertEqual(self.sp.send_called_count, 3)

    @defer.inlineCallbacks
    def test_basic(self):
        yield self.setupReporter()
        build = yield self.insert_build_finished(SUCCESS)
        yield self._check_start_and_finish_build(build)


class TestBitbucketServerPRCommentPush(TestReactorMixin, unittest.TestCase,
                                       ReporterTestMixin, LoggingMixin):

    @defer.inlineCallbacks
    def setUp(self):
        self.setUpTestReactor()
        # ignore config error if txrequests is not installed
        self.patch(config, '_errors', Mock())
        self.master = fakemaster.make_master(self, wantData=True, wantDb=True,
                                             wantMq=True)
        yield self.master.startService()

    @defer.inlineCallbacks
    def setupReporter(self, verbose=True, generator_class=BuildStatusGenerator, **kwargs):
        self._http = yield fakehttpclientservice.HTTPClientService.getService(
            self.master, self, 'serv', auth=('username', 'passwd'), debug=None,
            verify=None)

        formatter = Mock(spec=MessageFormatter)
        formatter.format_message_for_build.return_value = {
            "body": UNICODE_BODY,
            "type": "text",
            "subject": "subject"
        }
        formatter.wantProperties = True
        formatter.wantSteps = False
        formatter.wantLogs = False

        generator = generator_class(message_formatter=formatter)

        self.cp = BitbucketServerPRCommentPush("serv", Interpolate("username"),
                                               Interpolate("passwd"), verbose=verbose,
                                               generators=[generator], **kwargs)
        yield self.cp.setServiceParent(self.master)

    @defer.inlineCallbacks
    def tearDown(self):
        yield self.master.stopService()

    @defer.inlineCallbacks
    def setupBuildResults(self, buildResults, set_pr=True):
        yield super().insertTestData([buildResults], buildResults)
        build = yield self.master.data.get(('builds', 20))
        if set_pr:
            yield self.master.db.builds.setBuildProperty(
                20, "pullrequesturl", PR_URL, "test")
        return build

    @defer.inlineCallbacks
    def test_reporter_basic(self):
        yield self.setupReporter()
        build = yield self.setupBuildResults(SUCCESS)
        self._http.expect(
            "post",
            EXPECTED_API,
            json={"text": UNICODE_BODY},
            code=HTTP_CREATED)
        build["complete"] = True
        self.setUpLogging()
        yield self.cp._got_event(('builds', 20, 'finished'), build)
        self.assertLogged('Comment sent to {}'.format(PR_URL))

    @defer.inlineCallbacks
    def test_reporter_basic_without_logging(self):
        yield self.setupReporter(verbose=False)
        build = yield self.setupBuildResults(SUCCESS)
        self._http.expect(
            "post",
            EXPECTED_API,
            json={"text": UNICODE_BODY},
            code=HTTP_CREATED)
        build["complete"] = True
        self.setUpLogging()
        yield self.cp._got_event(('builds', 20, 'finished'), build)

        self.assertNotLogged('Comment sent to {}'.format(PR_URL))

    @defer.inlineCallbacks
    def test_reporter_without_pullrequest(self):
        yield self.setupReporter()
        build = yield self.setupBuildResults(SUCCESS, set_pr=False)
        build["complete"] = True
        # we don't expect any request
        yield self.cp._got_event(('builds', 20, 'finished'), build)

    @defer.inlineCallbacks
    def test_missing_worker_does_nothing(self):
        yield self.setupReporter()
        self.cp.workerMissing(("workers", 13, "missing"), 13)

    @defer.inlineCallbacks
    def test_reporter_with_buildset(self):
        yield self.setupReporter(generator_class=BuildSetStatusGenerator)
        yield self.setupBuildResults(SUCCESS)
        buildset = yield self.master.data.get(('buildsets', 98))
        self._http.expect(
            "post",
            EXPECTED_API,
            json={"text": UNICODE_BODY},
            code=HTTP_CREATED)
        yield self.cp._got_event(("buildsets", 98, "complete"), buildset)

    @defer.inlineCallbacks
    def test_reporter_logs_error_code_and_content_on_invalid_return_code(self):
        yield self.setupReporter()
        build = yield self.setupBuildResults(SUCCESS)

        http_error_code = 500
        error_body = {"errors": [
            {"message": "A dataXXXbase error has occurred."}]}

        self._http.expect(
            "post",
            EXPECTED_API,
            json={"text": UNICODE_BODY},
            code=http_error_code,
            content_json=error_body)
        self.setUpLogging()
        build['complete'] = True
        yield self.cp._got_event(('builds', 20, 'finished'), build)

        self.assertLogged(
            "^{}: Unable to send a comment: ".format(http_error_code))
        self.assertLogged("A dataXXXbase error has occurred")

    @defer.inlineCallbacks
    def test_reporter_logs_error_code_without_content_on_invalid_return_code(self):
        yield self.setupReporter()
        build = yield self.setupBuildResults(SUCCESS)
        http_error_code = 503
        self._http.expect(
            "post",
            EXPECTED_API,
            json={"text": UNICODE_BODY},
            code=http_error_code)
        self.setUpLogging()
        build['complete'] = True
        yield self.cp._got_event(('builds', 20, 'finished'), build)
        self.assertLogged("^{}: Unable to send a comment: ".format(
            http_error_code))

    @defer.inlineCallbacks
    def test_reporter_does_not_log_return_code_on_valid_return_code(
            self):
        yield self.setupReporter()
        build = yield self.setupBuildResults(SUCCESS)
        http_code = 201
        self._http.expect(
            "post",
            EXPECTED_API,
            json={"text": UNICODE_BODY},
            code=http_code)
        self.setUpLogging()
        build['complete'] = True
        yield self.cp._got_event(('builds', 20, 'finished'), build)
        self.assertNotLogged("^{}:".format(http_code))
