import unittest
from unittest import mock
import json
import sys
from path import Path
from queue import Queue

from xqueue_watcher import grader

MYDIR = Path(__file__).dirname() / 'fixtures'


class MockGrader(grader.Grader):
    def grade(self, grader_path, grader_config, student_response):
        tests = []
        errors = []
        correct = 0
        score = 0
        if grader_path.endswith('/correct'):
            correct = 1
            score = 1
            tests.append(('short', 'long', True, 'expected', 'actual'))
            tests.append(('short', '', True, 'expected', 'actual'))
        elif grader_path.endswith('/incorrect'):
            tests.append(('short', 'long', False, 'expected', 'actual'))
            errors.append('THIS IS AN ERROR')
            errors.append('\x00\xc3\x83\xc3\xb8\x02')

        try:
            from codejail import jail_code
        except ImportError:
            tests.append(("codejail", "codejail not installed", True, "", ""))
        else:
            if jail_code.is_configured("python"):
                tests.append(("codejail", "codejail configured", True, "", ""))
            else:
                tests.append(("codejail", "codejail not configured", True, "", ""))

        results = {
            'correct': correct,
            'score': score,
            'tests': tests,
            'errors': errors,
        }
        return results


class GraderTests(unittest.TestCase):
    def _make_payload(self, body, files=''):
        return {
            'xqueue_body': json.dumps(body),
            'xqueue_files': files
            }

    def test_bad_payload(self):
        g = MockGrader()

        self.assertRaises(KeyError, g.process_item, {})
        self.assertRaises(ValueError, g.process_item, {'xqueue_body': '', 'xqueue_files': ''})
        pl = self._make_payload({
            'student_response': 'blah',
            'grader_payload': 'blah'
            })
        self.assertRaises(ValueError, g.process_item, pl)

    def test_no_grader(self):
        g = grader.Grader()
        pl = self._make_payload({
            'student_response': 'blah',
            'grader_payload': json.dumps({
                'grader': '/tmp/grader.py'
                })
            })
        self.assertRaises(NotImplementedError, g.process_item, pl)

        # grader that doesn't exist
        self.assertRaises(Exception, grader.Grader, gradepy='/asdfasdfdasf.py')

    def test_correct_response(self):
        g = MockGrader()
        pl = self._make_payload({
            'student_response': 'blah',
            'grader_payload': json.dumps({
                'grader': 'correct'
                })
            })
        reply = g.process_item(pl)
        self.assertIn('result-correct', reply['msg'])
        self.assertEqual(reply['correct'], 1)
        self.assertEqual(reply['score'], 1)

    def test_incorrect_response(self):
        g = MockGrader()
        pl = self._make_payload({
            'student_response': 'blah',
            'grader_payload': json.dumps({
                'grader': 'incorrect'
                })
            })
        reply = g.process_item(pl)
        self.assertIn('result-incorrect', reply['msg'])
        self.assertIn('THIS IS AN ERROR', reply['msg'])
        self.assertEqual(reply['correct'], 0)
        self.assertEqual(reply['score'], 0)

    def test_response_on_queue(self):
        g = MockGrader()
        pl = self._make_payload({
            'student_response': 'blah',
            'grader_payload': json.dumps({
                'grader': 'correct'
                })
            })
        q = Queue()
        reply = g.process_item(pl, queue=q)
        popped = q.get()
        self.assertEqual(reply, popped)

        del pl['xqueue_body']
        try:
            g.process_item(pl, queue=q)
        except Exception as e:
            popped = q.get()
            self.assertEqual(e, popped)

    def test_subprocess(self):
        g = MockGrader()
        pl = self._make_payload({
            'student_response': 'blah',
            'grader_payload': json.dumps({
                'grader': 'correct'
                })
            })
        reply = g(pl)
        self.assertEqual(reply['correct'], 1)

        del pl['xqueue_body']

        self.assertRaises(KeyError, g, pl)

    def test_no_fork(self):
        g = MockGrader(fork_per_item=False)
        pl = self._make_payload({
            'student_response': 'blah',
            'grader_payload': json.dumps({
                'grader': 'correct'
                })
            })
        reply = g(pl)
        self.assertEqual(reply['correct'], 1)
