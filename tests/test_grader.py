import unittest
import mock
import json
import sys
from path import path
from Queue import Queue

from xqueue_watcher import grader

MYDIR = path(__file__).dirname()


class GraderTests(unittest.TestCase):
    def _make_payload(self, body, files=''):
        return {
            'xqueue_body': json.dumps(body),
            'xqueue_files': files
            }

    def test_bad_payload(self):
        g = grader.Grader()

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

    def test_bad_grader(self):
        g = grader.Grader(gradepy=MYDIR / 'not_python_grader')
        pl = self._make_payload({
            'student_response': 'blah',
            'grader_payload': json.dumps({
                'grader': '/tmp/grader.py'
                })
            })
        self.assertRaises(NameError, g.process_item, pl)

    def test_correct_response(self):
        # remove mydir from path to ensure we can still find the file
        sys.path.remove(MYDIR)

        g = grader.Grader(gradepy=MYDIR / 'mock_grader.py')
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
        g = grader.Grader(gradepy=MYDIR / 'mock_grader.py')
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
        g = grader.Grader(gradepy=MYDIR / 'mock_grader.py')
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
        g = grader.Grader(gradepy=MYDIR / 'mock_grader.py')
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
