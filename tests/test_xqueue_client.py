import unittest
from unittest import mock
import json
import collections
import requests
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
        req = self.session._requests[1]
        self.assertTrue(req.url, 'TEST/xqueue/login/')
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
        req = self.session._requests[1]
        self.assertTrue(req.url, 'TEST/xqueue/login/')
        self.assertFalse(reply)

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
