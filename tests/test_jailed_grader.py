import getpass
import os
import sys
import textwrap
import unittest
from path import path

from xqueue_watcher.jailedgrader import JailedGrader
from codejail import configure
import codejail.languages


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
                    lang=codejail.languages.python3
                )
                break
        self.grader_root = path(__file__).dirname() / 'fixtures'
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

    def test_correct_python3(self):
        # metaclass is to prove we're using python3
        code = textwrap.dedent(u'''
            class foo(metaclass=type):
                def __new__(self):
                    return "hi"
        ''')
        response = self.g3.grade(self.grader_root / 'fake_grader.py', {}, code)
        self.assertEqual(response['score'], 1)

        # A python2 grader can't handle this
        response = self.g.grade(self.grader_root / 'fake_grader.py', {}, code)
        self.assertEqual(response['score'], 0)

    def test_incorrect_python3(self):
        code = textwrap.dedent('''
            def foo():
                return "heyo!"
        ''')

        response = self.g3.grade(self.grader_root / 'fake_grader.py', {}, code)
        self.assertEqual(response['score'], 0)

        response = self.g3.grade(self.grader_root / 'fake_grader.py', {}, '')
        self.assertEqual(response['score'], 0)

        response = self.g3.grade(self.grader_root / 'fake_grader.py', {}, 'asdofhpsdfuh')
        self.assertEqual(response['score'], 0)
