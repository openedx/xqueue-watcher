import unittest
from path import path
import json
from mock import Mock
import time

import logging
from pullgrader import manager, sandbox
from tests.test_xqueue_client import MockXQueueServer


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.m = manager.Manager()
        self.config = {
            'test1': {
                'server': 'http://test1',
                'handlers': [
                    {
                        'handler': 'pullgrader.grader.Grader',
                        'kwargs': {
                            'grader_file': path(__file__).dirname() / 'mock_grader.py',
                        },
                        'sandbox': 'python'
                    }
                ]
            },
            'test2': {
                'connections': 2,
                'server': 'http://test2',
                'handlers': [
                    {
                        'handler': 'urllib.urlencode'
                    }
                ]
            }
        }

    def tearDown(self):
        try:
            self.m.shutdown()
        except SystemExit:
            pass

    def test_configuration(self):
        self.m.configure(self.config)
        self.assertEqual(len(self.m.clients), 3)
        for c in self.m.clients:
            if c.queue_name == 'test1':
                self.assertTrue(c.handlers[0].sandbox is not None)
            elif c.queue_name == 'test2':
                self.assertEqual(c.xqueue_server, 'http://test2')

    def test_start(self):
        self.m.configure(self.config)
        sess = MockXQueueServer()
        sess._json = {'return_code': 0, 'msg': 'logged in'}
        for c in self.m.clients:
            c.session = sess

        self.m.start()
        for c in self.m.clients:
            self.assertTrue(c.is_alive())

    def test_shutdown(self):
        self.m.configure(self.config)
        sess = MockXQueueServer()
        sess._json = {'return_code': 0, 'msg': 'logged in'}
        for c in self.m.clients:
            c.session = sess
        self.m.start()
        self.assertRaises(SystemExit, self.m.shutdown)

    def test_wait(self):
        # no-op
        self.m.wait()

        self.m.configure(self.config)

        def slow_reply(url, response, session):
            if url.endswith('get_submission/'):
                response.json.return_value = {
                    u'return_code': 0,
                    u'success': 1,
                    u'content': json.dumps({
                        u'xqueue_header': {u'hello': 1},
                        u'xqueue_body': {
                            u'blah': json.dumps({}),
                        }
                    })
                }
            if url.endswith('put_result/'):
                time.sleep(2)

        for c in self.m.clients:
            c.session = MockXQueueServer()
            c._json = {'return_code': 0, 'msg': 'logged in'}

        # make one client wait for a while and then blow up
        def waiter(url, response, session):
            time.sleep(.5)
            response.status_code = 500

        for c in self.m.clients:
            if c.queue_name == 'test2':
                c.session._url_checker = slow_reply
                c.session._json = {'return_code': 0}
            else:
                c.session._url_checker = waiter
        self.m.poll_time = 1
        self.m.start()

        self.assertRaises(SystemExit, self.m.wait)

    def test_main(self):
        self.assertEqual(manager.main([]), -1)
        mydir = path(__file__).dirname()
        args = ['-f', mydir / 'test_config.json', '-l', mydir / 'test_logging.json']
        self.assertEqual(manager.main(args), 0)

        args = ['-s', 'fake_settings']
        self.assertEqual(manager.main(args), 0)


class SandboxTests(unittest.TestCase):
    def test_default_sandboxing(self):
        s = sandbox.Sandbox(logging.getLogger())
        self.assertTrue(s.do_sandboxing)
        self.assertEqual(s.sandbox_cmd_list(), ['sudo', '-u', 'sandbox', 'python'])

    def test_no_sandbox(self):
        s = sandbox.Sandbox(logging.getLogger(), do_sandboxing=False)
        self.assertFalse(s.do_sandboxing)
        self.assertEqual(s.sandbox_cmd_list(), ['python'])

    def test_different_python(self):
        s = sandbox.Sandbox(logging.getLogger(), do_sandboxing=False, python_path='/not/python')
        self.assertEqual(s.sandbox_cmd_list(), ['/not/python'])

    def test_record_suspicious(self):
        ml = Mock()
        s = sandbox.Sandbox(ml)
        s.record_suspicious_submission('test', 'my code')
        ml.warning.assert_called_with('Suspicious code: test, my code')
