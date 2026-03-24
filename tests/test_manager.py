import unittest
from pathlib import Path
import json
from unittest.mock import Mock
import time
import sys

import logging
from xqueue_watcher import manager
from tests.test_xqueue_client import MockXQueueServer

from io import StringIO

try:
    import codejail
    HAS_CODEJAIL = True
except ImportError:
    HAS_CODEJAIL = False


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.m = manager.Manager()
        self.config = {
            'test1': {
                'SERVER': 'http://test1',
                'AUTH': ('test', 'test'),
                'HANDLERS': [
                    {
                        'HANDLER': 'tests.test_grader.MockGrader',
                    }
                ]
            },
            'test2': {
                'AUTH': ('test', 'test'),
                'CONNECTIONS': 2,
                'SERVER': 'http://test2',
                'HANDLERS': [
                    {
                        'HANDLER': 'urllib.urlencode'
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
            if c.queue_name == 'test2':
                self.assertEqual(c.xqueue_server, 'http://test2')

    @unittest.skipUnless(HAS_CODEJAIL, "Codejail not installed")
    def test_codejail_config(self):
        config = {
            "name": "python",
            "bin_path": "/usr/bin/python",
            "user": "nobody",
            "limits": {
                "CPU": 2,
                "VMEM": 1024
            }
        }
        codejail_return = self.m.enable_codejail(config)
        self.assertEqual(codejail_return, config["name"])
        self.assertTrue(codejail.jail_code.is_configured("python"))
        self.m.enable_codejail({
            "name": "other-python",
            "bin_path": "/usr/local/bin/python"
            })
        self.assertTrue(codejail.jail_code.is_configured("other-python"))

        # Verify codejail config is visible to the grader running in the same process.
        # (fork_per_item=False avoids relying on multiprocessing start-method-specific
        # state inheritance, which varies between 'fork' and 'forkserver'.)
        handler_config = self.config['test1'].copy()
        handler_config['HANDLERS'][0]['KWARGS'] = {'fork_per_item': False}
        client = self.m.client_from_config("test", handler_config)
        client.session = MockXQueueServer()
        client._handle_submission(json.dumps({
            "xqueue_header": "",
            "xqueue_files": [],
            "xqueue_body": json.dumps({
                'student_response': 'blah',
                'grader_payload': json.dumps({
                    'grader': '/tmp/grader.py'
                    })
                })
            }))
        last_req = client.session._requests[-1]
        self.assertIn('codejail configured', last_req.kwargs['data']['xqueue_body'])

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
                    'return_code': 0,
                    'success': 1,
                    'content': json.dumps({
                        'xqueue_header': {'hello': 1},
                        'xqueue_body': {
                            'blah': json.dumps({}),
                        }
                    })
                }
            if url.endswith('put_result/'):
                time.sleep(2)

        import threading
        def stopper(client):
            time.sleep(.4)
            client.running = False

        for c in self.m.clients:
            c.session = MockXQueueServer()
            c._json = {'return_code': 0, 'msg': 'logged in'}
            c.session._url_checker = slow_reply
            c.session._json = {'return_code': 0}

        self.m.poll_time = 1
        self.m.start()
        threading.Thread(target=stopper, args=(self.m.clients[0],)).start()

        self.assertRaises(SystemExit, self.m.wait)

    def test_main_with_errors(self):
        stderr = sys.stderr
        sys.stderr = StringIO()
        self.assertRaises(SystemExit, manager.main, [])
        sys.stderr.seek(0)
        err_msg = sys.stderr.read()
        self.assertIn('usage: xqueue_watcher [-h] -d CONFIG_ROOT', err_msg)
        self.assertIn('-d/--config_root', err_msg)
        self.assertIn('required', err_msg)
        sys.stderr = stderr

        mydir = Path(__file__).parent
        args = ['-d', str(mydir / "fixtures/config")]
        self.assertEqual(manager.main(args), 0)


class ServerRefTests(unittest.TestCase):
    def setUp(self):
        self.m = manager.Manager()
        self.m.xqueue_servers = {
            "primary": {
                "SERVER": "http://primary-xqueue:18040",
                "AUTH": ["user1", "pass1"],
            },
        }

    def tearDown(self):
        try:
            self.m.shutdown()
        except SystemExit:
            pass

    def _simple_config(self, queue_config):
        """Wrap a single queue config dict with a handler."""
        return {
            "HANDLERS": [{"HANDLER": "tests.test_grader.MockGrader"}],
            **queue_config,
        }

    def test_server_ref_resolves_url_and_auth(self):
        config = self._simple_config({"SERVER_REF": "primary"})
        client = self.m.client_from_config("my-queue", config)
        self.assertEqual(client.xqueue_server, "http://primary-xqueue:18040")
        self.assertEqual(client.username, "user1")
        self.assertEqual(client.password, "pass1")

    def test_server_ref_unknown_raises(self):
        config = self._simple_config({"SERVER_REF": "nonexistent"})
        with self.assertRaises(ValueError) as ctx:
            self.m.client_from_config("my-queue", config)
        self.assertIn("nonexistent", str(ctx.exception))

    def test_server_ref_with_server_key_raises(self):
        config = self._simple_config({
            "SERVER_REF": "primary",
            "SERVER": "http://other:18040",
        })
        with self.assertRaises(ValueError) as ctx:
            self.m.client_from_config("my-queue", config)
        self.assertIn("SERVER_REF", str(ctx.exception))

    def test_server_ref_with_auth_key_raises(self):
        config = self._simple_config({
            "SERVER_REF": "primary",
            "AUTH": ["u", "p"],
        })
        with self.assertRaises(ValueError) as ctx:
            self.m.client_from_config("my-queue", config)
        self.assertIn("SERVER_REF", str(ctx.exception))

    def test_no_server_ref_still_works(self):
        config = self._simple_config({
            "SERVER": "http://direct:18040",
            "AUTH": ["u", "p"],
        })
        client = self.m.client_from_config("my-queue", config)
        self.assertEqual(client.xqueue_server, "http://direct:18040")
        self.assertEqual(client.username, "u")

    def test_configure_from_directory_loads_xqueue_servers(self):
        mydir = Path(__file__).parent
        m = manager.Manager()
        m.configure_from_directory(mydir / "fixtures/config")
        self.assertIn("fixture-server", m.xqueue_servers)
        self.assertEqual(
            m.xqueue_servers["fixture-server"]["SERVER"],
            "http://fixture-xqueue:18040",
        )

    def test_configure_from_directory_no_servers_file(self, tmp_path=None):
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "conf.d").mkdir()
            (tmp / "conf.d" / "empty.json").write_text("{}")
            m = manager.Manager()
            m.configure_from_directory(tmp)
            self.assertEqual(m.xqueue_servers, {})
