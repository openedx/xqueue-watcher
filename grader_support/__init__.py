from __future__ import absolute_import
from __future__ import unicode_literals
import sys
from . import gradelib, graderutil
# for backwards compatibility, insert gradelib and graderutil
# in the top level
sys.modules['gradelib'] = gradelib
sys.modules['graderutil'] = graderutil
