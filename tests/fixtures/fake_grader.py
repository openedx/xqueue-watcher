from __future__ import absolute_import
from __future__ import unicode_literals
from grader_support import gradelib

grader = gradelib.Grader()

grader.add_test(gradelib.InvokeStudentFunctionTest('foo', []))
