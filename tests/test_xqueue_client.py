import unittest
from unittest import mock
import json
import collections
import requests
import requests.cookies
import requests.exceptions

from xqueue_watcher import client

Request = collections.namedtuple('Request', ('method', 'url', 'kwargs', 'response'))


class MockXQueueServer(mock.Mock):
    def __init__(self):
        mock.Mock.__init__(self)
        self.status_code = 200
        self._json = None
        self._requests = []
        self._loginok = True
        self._fail = False
        self._url_checker = None
        self._open = True
        self.cookies = requests.cookies.RequestsCookieJar()
        self.headers = {}

    def close(self):
        self._open = False

    def request(self, method, url, **kwargs):
        response = mock.Mock()
        response.json = mock.MagicMock()
        if self._json:
            if isinstance(self._json, Exception):
                response.json.side_effect = self._json
            else:
                response.json.return_value = self._json
        response.status_code = self.status_code

        if self._url_checker:
            self._url_checker(url, response, self)

        self._requests.append(Request(method, url, kwargs, response))
        if self._fail:
            raise self._fail
        return response


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.client = client.XQueueClient('test', xqueue_server='TEST')
        self.session = MockXQueueServer()
        self.client.session = self.session
        self.qitem = None
        self.excepted = False

        self.sample_item = {
            'return_code': 0,
            'success': 1,
            'content': json.dumps({
                'xqueue_header': {'hello': 1},
                'xqueue_body': {
                    'blah': 'blah'
                }
            })
        }
        self.session._json = self.sample_item

    def _simple_handler(self, content):
        self.qitem = content

    def test_repr(self):
        self.assertEqual(repr(self.client), 'XQueueClient(%s)' % self.client.queue_name)

    def test_process_one(self):
        self.client.add_handler(self._simple_handler)
        reply = self.client.process_one()
        self.assertTrue(reply)
        self.assertTrue(self.qitem is not None)
        self.assertEqual(self.qitem, json.loads(self.sample_item['content']))

        # try with different return_code
        del self.sample_item['return_code']
        reply = self.client.process_one()
        self.assertTrue(reply)
        self.assertTrue(self.qitem is not None)
        self.assertEqual(self.qitem, json.loads(self.sample_item['content']))

        # try with wrong return code
        self.sample_item['success'] = 'bad'
        reply = self.client.process_one()
        self.assertFalse(reply)

        # try with no return code
        del self.sample_item['success']
        reply = self.client.process_one()
        self.assertFalse(reply)

    def test_add_remove(self):
        def handler(content):
            self.qitem = content

        self.client.add_handler(handler)
        reply = self.client.process_one()
        self.assertTrue(self.qitem is not None)
        self.qitem = None

        self.client.remove_handler(handler)
        reply = self.client.process_one()
        self.assertTrue(self.qitem is None)

    def test_handler_exception(self):
        def raises(content):
            self.excepted = True
            self.qitem = content
            raise Exception('test')

        self.client.add_handler(raises)
        reply = self.client.process_one()
        self.assertTrue(reply)
        self.assertTrue(self.excepted)
        self.assertTrue(self.qitem is not None)

    def test_bad_json(self):
        self.client.add_handler(self._simple_handler)
        self.session._json = ValueError()
        reply = self.client.process_one()
        self.assertFalse(reply)

    def test_bad_connection(self):
        self.client.add_handler(self._simple_handler)
        self.session.status_code = 500
        reply = self.client.process_one()
        self.assertFalse(reply)

        # connection exception
        self.session._fail = requests.exceptions.ConnectionError()
        reply = self.client.process_one()
        self.assertFalse(reply)

        # handle timeout
        self.session._fail = requests.exceptions.Timeout()
        reply = self.client.process_one()
        self.assertTrue(reply)

    def test_redirect_to_login(self):
        self.client.add_handler(self._simple_handler)
        self.session.status_code = 302

        def login(url, response, session):
            if url.endswith('xqueue/login/'):
                response.status_code = 200
                response.json.return_value = {'return_code': 0, 'msg': 'logged in'}
                session.status_code = 200
        self.session._url_checker = login

        reply = self.client.process_one()
        # _requests[0]: original GET /xqueue/get_submission/ → 302
        # _requests[1]: GET /xqueue/login/ (CSRF prefetch)
        # _requests[2]: POST /xqueue/login/
        # _requests[3]: retry GET /xqueue/get_submission/ → 200
        get_csrf_req = self.session._requests[1]
        self.assertEqual(get_csrf_req.method, 'get')
        self.assertEqual(get_csrf_req.url, 'TEST/xqueue/login/')
        post_login_req = self.session._requests[2]
        self.assertEqual(post_login_req.method, 'post')
        self.assertEqual(post_login_req.url, 'TEST/xqueue/login/')
        self.assertEqual(post_login_req.kwargs['headers']['Referer'], 'TEST/xqueue/login/')
        self.assertTrue(reply)

    def test_bad_login(self):
        self.client.add_handler(self._simple_handler)
        self.session.status_code = 302

        def login(url, response, session):
            if url.endswith('xqueue/login/'):
                response.status_code = 200
                response.json.return_value = {'return_code': 1, 'msg': 'bad login'}
                session.status_code = 200

        self.session._url_checker = login

        reply = self.client.process_one()
        post_login_req = self.session._requests[2]
        self.assertEqual(post_login_req.method, 'post')
        self.assertEqual(post_login_req.url, 'TEST/xqueue/login/')
        self.assertFalse(reply)

    def test_login_sets_referer_header(self):
        """POST to login always includes Referer pointing at the login URL."""
        self.session._json = {'return_code': 0, 'msg': 'logged in'}
        self.client._login()
        post_req = self.session._requests[1]
        self.assertEqual(post_req.method, 'post')
        self.assertEqual(post_req.kwargs['headers']['Referer'], 'TEST/xqueue/login/')

    def test_login_sets_csrf_token_header(self):
        """X-CSRFToken is set on the POST when the CSRF cookie is present."""
        def set_csrf_cookie(url, response, session):
            if url.endswith('xqueue/login/'):
                session.cookies.set('csrftoken', 'testcsrf123')
                response.status_code = 200
                response.json.return_value = {'return_code': 0, 'msg': 'logged in'}
        self.session._url_checker = set_csrf_cookie
        self.client._login()
        post_req = self.session._requests[1]
        self.assertEqual(post_req.method, 'post')
        self.assertEqual(post_req.kwargs['headers']['X-CSRFToken'], 'testcsrf123')

    def test_login_sets_edx_csrf_token_header(self):
        """X-CSRFToken falls back to the edx-csrftoken cookie name."""
        def set_edx_csrf_cookie(url, response, session):
            if url.endswith('xqueue/login/'):
                session.cookies.set('edx-csrftoken', 'edxcsrf456')
                response.status_code = 200
                response.json.return_value = {'return_code': 0, 'msg': 'logged in'}
        self.session._url_checker = set_edx_csrf_cookie
        self.client._login()
        post_req = self.session._requests[1]
        self.assertEqual(post_req.kwargs['headers']['X-CSRFToken'], 'edxcsrf456')

    def test_login_no_csrf_token_header_when_no_cookie(self):
        """X-CSRFToken is omitted from the POST when no CSRF cookie is available."""
        self.session._json = {'return_code': 0, 'msg': 'logged in'}
        self.client._login()
        post_req = self.session._requests[1]
        self.assertNotIn('X-CSRFToken', post_req.kwargs['headers'])

    def test_login_clears_stale_session(self):
        """Stale cookies and X-CSRFToken header are cleared before the CSRF GET."""
        self.session._json = {'return_code': 0, 'msg': 'logged in'}
        self.session.cookies.set('sessionid', 'stale_session')
        self.session.headers['X-CSRFToken'] = 'stale_token'
        self.client._login()
        self.assertIsNone(self.session.cookies.get('sessionid'))
        self.assertNotIn('X-CSRFToken', self.session.headers)

    def test_login_persists_csrf_in_session_headers(self):
        """After a successful login, the session X-CSRFToken header is updated."""
        def set_cookie_on_post(url, response, session):
            if url.endswith('xqueue/login/') and response.status_code == 200:
                session.cookies.set('csrftoken', 'post_login_csrf')
                response.json.return_value = {'return_code': 0}

        self.session._url_checker = set_cookie_on_post
        self.client._login()
        self.assertEqual(self.session.headers.get('X-CSRFToken'), 'post_login_csrf')

    def test_login_persists_referer_in_session_headers(self):
        """After login, Referer is set in session headers so POST requests pass
        Django's HTTPS Referer check (required by Django 4+ CSRF enforcement)."""
        self.session._json = {'return_code': 0, 'msg': 'logged in'}
        self.client._login()
        self.assertEqual(self.session.headers.get('Referer'), self.client.xqueue_server)

    def test_reauth_only_once_per_request(self):
        """Re-authentication is attempted at most once; persistent failures return False."""
        self.client.add_handler(self._simple_handler)
        login_call_count = [0]

        def always_auth_error(url, response, session):
            if url.endswith('xqueue/login/'):
                login_call_count[0] += 1
                response.status_code = 200
                response.json.return_value = {'return_code': 0, 'msg': 'logged in'}
            else:
                response.status_code = 403

        self.session._url_checker = always_auth_error
        self.session.status_code = 403

        reply = self.client.process_one()
        self.assertFalse(reply)
        # _login() makes GET + POST = 2 requests to the login URL, but only once
        self.assertEqual(login_call_count[0], 2)

    def test_reauth_on_401(self):
        """A 401 response triggers re-authentication just like a 302 redirect."""
        self.client.add_handler(self._simple_handler)
        self.session.status_code = 401

        def login(url, response, session):
            if url.endswith('xqueue/login/'):
                response.status_code = 200
                response.json.return_value = {'return_code': 0, 'msg': 'logged in'}
                session.status_code = 200
        self.session._url_checker = login

        reply = self.client.process_one()
        self.assertTrue(reply)

    def test_reauth_on_403(self):
        """A 403 response (e.g. CSRF failure) triggers re-authentication."""
        self.client.add_handler(self._simple_handler)
        self.session.status_code = 403

        def login(url, response, session):
            if url.endswith('xqueue/login/'):
                response.status_code = 200
                response.json.return_value = {'return_code': 0, 'msg': 'logged in'}
                session.status_code = 200
        self.session._url_checker = login

        reply = self.client.process_one()
        self.assertTrue(reply)

    def test_put_result_reauth_with_csrf(self):
        """When put_result returns 403 (CSRF failure), re-auth obtains a fresh
        CSRF token via the GET /xqueue/login/ endpoint and the retry succeeds."""
        put_result_attempts = [0]

        def handler(content):
            return {'result': True}
        self.client.add_handler(handler)

        def url_checker(url, response, session):
            if url.endswith('xqueue/login/'):
                session.cookies.set('csrftoken', 'fresh_csrf_token')
                response.status_code = 200
                response.json.return_value = {'return_code': 0, 'content': ''}
            elif url.endswith('put_result/'):
                put_result_attempts[0] += 1
                if put_result_attempts[0] == 1:
                    response.status_code = 403
                else:
                    response.status_code = 200
                    response.json.return_value = {'return_code': 0, 'content': ''}

        self.session._url_checker = url_checker
        result = self.client.process_one()

        self.assertTrue(result)
        self.assertEqual(put_result_attempts[0], 2)
        self.assertEqual(self.client.session.headers.get('X-CSRFToken'), 'fresh_csrf_token')

    def test_post_back(self):
        def handler(content):
            return {'result': True}

        self.client.add_handler(handler)
        result = self.client.process_one()
        self.assertTrue(result)
        last_request = self.session._requests[-1]
        self.assertTrue(last_request.url.endswith('put_result/'))
        posted = last_request.kwargs['data']
        self.assertEqual(posted['xqueue_body'], json.dumps({'result': True}))

        # test failure case
        def postfailure(url, response, session):
            if url.endswith('put_result/'):
                response.status_code = 500
        self.session._url_checker = postfailure
        result = self.client.process_one()
        self.assertFalse(result)
        last_request = self.session._requests[-1]
        self.assertTrue(last_request.url.endswith('put_result/'))

    def test_run(self):
        def handler(content):
            return {'result': True}

        def urlchecker(url, response, session):
            if url.endswith('/login/'):
                response.status_code = 200
                response.json.return_value = {'return_code': 0, 'msg': 'logged in'}
                self.session.status_code = 200
            elif url.endswith('get_submission/') and len(session._requests) > 3:
                self.client.shutdown()
                response.status_code = 500

        self.session._url_checker = urlchecker
        self.client.add_handler(handler)

        self.client.run()
        self.assertFalse(self.client.running)
        self.assertFalse(self.session._open)

        # test failed login
        def urlchecker(url, response, session):
            if url.endswith('/login/'):
                response.status_code = 500

        self.session._url_checker = urlchecker
        self.client.running = False
        self.assertTrue(self.client.run())
