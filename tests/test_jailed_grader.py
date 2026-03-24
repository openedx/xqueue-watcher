import getpass
import os
import sys
import textwrap
import unittest
from pathlib import Path

import pytest

try:
    from codejail.jail_code import configure
    HAS_CODEJAIL = True
except ImportError:
    HAS_CODEJAIL = False

from xqueue_watcher.jailedgrader import JailedGrader


@pytest.mark.skipif(not HAS_CODEJAIL, reason="codejail not installed")
class JailedGraderTests(unittest.TestCase):
    def setUp(self):
        configure("python", sys.executable, user=getpass.getuser())
        py3paths = [os.environ.get('XQUEUEWATCHER_PYTHON3_BIN'), '/usr/bin/python3', '/usr/local/bin/python3']
        for py3path in (p3p for p3p in py3paths if p3p):
            if os.path.exists(py3path):
                configure(
                    "python3",
                    py3path,
                    user=getpass.getuser(),
                )
                break
        self.grader_root = Path(__file__).parent / 'fixtures'
        self.g = JailedGrader(grader_root=self.grader_root)
        self.g3 = JailedGrader(grader_root=self.grader_root, codejail_python='python3')

    def test_correct(self):
        code = textwrap.dedent('''
            def foo():
                return "hi"
        ''')
        response = self.g.grade(self.grader_root / 'fake_grader.py', {}, code)
        self.assertEqual(response['score'], 1)

    def test_incorrect(self):
        code = textwrap.dedent('''
            def zoo():
                return "hi"
        ''')
        response = self.g.grade(self.grader_root / 'fake_grader.py', {}, code)
        self.assertEqual(response['score'], 0)

        response = self.g.grade(self.grader_root / 'fake_grader.py', {}, '')
        self.assertEqual(response['score'], 0)

        response = self.g.grade(self.grader_root / 'fake_grader.py', {}, 'asdofhpsdfuh')
        self.assertEqual(response['score'], 0)
