import sys
import getpass
import unittest
from path import path

from xqueue_watcher.jailedgrader import JailedGrader
from codejail.jail_code import configure


class JailedGraderTests(unittest.TestCase):
    def setUp(self):
        configure("python", sys.executable, user=getpass.getuser())
        self.grader_root = path(__file__).dirname() / 'fixtures'
        self.g = JailedGrader(grader_root=self.grader_root)

    def test_correct(self):
        code = '''
def foo():
    return "hi"
'''
        response = self.g.grade(self.grader_root / 'fake_grader.py', {}, code)
        self.assertEqual(response['score'], 1)

    def test_incorrect(self):
        code = '''
def zoo():
    return "hi"
'''
        response = self.g.grade(self.grader_root / 'fake_grader.py', {}, code)
        self.assertEqual(response['score'], 0)

        response = self.g.grade(self.grader_root / 'fake_grader.py', {}, '')
        self.assertEqual(response['score'], 0)

        response = self.g.grade(self.grader_root / 'fake_grader.py', {}, 'asdofhpsdfuh')
        self.assertEqual(response['score'], 0)
