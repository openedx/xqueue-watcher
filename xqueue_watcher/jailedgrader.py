"""
An implementation of a grader that uses codejail to sandbox submission execution.
"""
import codecs
import os
import sys
import imp
import json
import random
import gettext
from path import Path
import six

import codejail

from grader_support.gradelib import EndTest
from grader_support.graderutil import LANGUAGE
import grader_support

from .grader import Grader

TIMEOUT = 1

def path_to_six():
    """
    Return the full path to six.py
    """
    if any(six.__file__.endswith(suffix) for suffix in ('.pyc', '.pyo')):
        # __file__ points to the compiled bytecode in python 2
        return Path(six.__file__[:-1])
    else:
        # __file__ points to the .py file in python 3
        return Path(six.__file__)


SUPPORT_FILES = [
    Path(grader_support.__file__).dirname(),
    path_to_six(),
]


def truncate(out):
    """
    Truncate test output that's too long.  This is per-test.
    """
    TOO_LONG = 5000    # 5K bytes seems like enough for a single test.
    if len(out) > TOO_LONG:
        out = out[:TOO_LONG] + "...OUTPUT TRUNCATED"

    return out


def prepend_coding(code):
    """
    Add a coding line--makes submissions with inline unicode not
    explode (as long as they're utf8, I guess)
    """
    return '# coding: utf8\n' + code


class JailedGrader(Grader):
    """
    A grader implementation that uses codejail.
    Instantiate it with grader_root="path/to/graders"
    and optionally codejail_python="python name" (the name that you used to configure codejail)
    """
    def __init__(self, *args, **kwargs):
        self.codejail_python = kwargs.pop("codejail_python", "python")
        super().__init__(*args, **kwargs)
        self.locale_dir = self.grader_root / "conf" / "locale"
        self.fork_per_item = False  # it's probably safe not to fork
        # EDUCATOR-3368: OpenBLAS library is allowed to allocate 1 thread
        os.environ["OPENBLAS_NUM_THREADS"] = "1"

    def _enable_i18n(self, language):
        trans = gettext.translation('graders', localedir=self.locale_dir, fallback=True, languages=[language])
        trans.install(names=None)

    def _run(self, grader_path, thecode, seed):
        files = SUPPORT_FILES + [grader_path]
        if self.locale_dir.exists():
            files.append(self.locale_dir)
        extra_files = [('submission.py', thecode.encode('utf-8'))]
        argv = ["-m", "grader_support.run", Path(grader_path).basename(), 'submission.py', seed]
        r = codejail.jail_code.jail_code(self.codejail_python, files=files, extra_files=extra_files, argv=argv)
        return r

    def grade(self, grader_path, grader_config, submission):
        if type(submission) != str:
            self.log.warning("Submission is NOT unicode")

        results = {
            'errors': [],
            'tests': [],
            'correct': False,
            'score': 0,
        }

        # There are some cases where the course team would like to accept a
        # student submission but not process the student code. Some examples are
        # cases where the problem would require dependencies that are difficult
        # or impractical to install in a sandbox or if the complexity of the
        # solution would cause the runtime of the student code to exceed what is
        # possible in the sandbox.

        # skip_grader is a flag in the grader config which is a boolean. If it
        # is set to true on a problem then it will always show that the
        # submission is correct and give the student a full score for the
        # problem.
        if grader_config.get('skip_grader', False):
            results['correct'] = True
            results['score'] = 1
            self.log.debug('Skipping the grader.')
            return results

        self._enable_i18n(grader_config.get("lang", LANGUAGE))

        answer_path = Path(grader_path).dirname() / 'answer.py'
        with open(answer_path, 'rb') as f:
            answer = f.read().decode('utf-8')

        # Import the grader, straight from the original file.  (It probably isn't in
        # sys.path, and we may be in a long running gunicorn process, so we don't
        # want to add stuff to sys.path either.)
        grader_module = imp.load_source("grader_module", str(grader_path))
        grader = grader_module.grader

        # Preprocess for grader-specified errors
        errors = grader.input_errors(submission)
        if errors != []:
            results['errors'].extend(errors)
            # Don't run tests if there were errors
            return results

        # Add a unicode encoding declaration.
        processed_answer = prepend_coding(grader.preprocess(answer))
        processed_submission = prepend_coding(grader.preprocess(submission))

        # Same seed for both runs
        seed = str(random.randint(0, 20000))

        # Run the official answer, to get the expected output.
        expected_ok = False
        expected_exc = None
        try:
            # If we want a factor of two speedup for now: trust the staff solution to
            # avoid hitting the sandbox. (change run to run_trusted)
            expected_outputs = None  # in case run_trusted raises an exception.
            expected_outputs = self._run(grader_path, processed_answer, seed).stdout
            if expected_outputs:
                expected = json.loads(expected_outputs.decode('utf-8'))
                expected_ok = True
        except Exception:
            expected_exc = sys.exc_info()
        else:
            # We just ran the official answer, nothing should have gone wrong, so check
            # everything, and note it as bad if anything is wrong.
            if expected_ok:
                if expected['exceptions'] \
                        or expected['grader']['status'] != 'ok' \
                        or expected['submission']['status'] != 'ok':
                    expected_ok = False

        if not expected_ok:
            # We couldn't run the official answer properly, bail out, but don't show
            # details to the student, since none of it is their code.
            results['errors'].append(_('There was a problem running the staff solution (Staff debug: L364)'))
            self.log.error("Couldn't run staff solution. grader = %s, output: %r",
                           grader_path, expected_outputs, exc_info=expected_exc)
            return results

        # The expected code ran fine, go ahead and run the student submission.
        actual_ok = False
        actual_exc = None
        try:
            # Do NOT trust the student solution (in production).
            actual_outputs = None   # in case run raises an exception.
            actual_outputs = self._run(grader_path, processed_submission, seed).stdout
            if actual_outputs:
                actual = json.loads(actual_outputs.decode('utf-8'))
                actual_ok = True
            else:
                results['errors'].append(_("There was a problem running your solution (Staff debug: L379)."))
        except Exception:
            actual_exc = sys.exc_info()
        else:
            if actual_ok and actual['grader']['status'] == 'ok':
                if actual['submission']['status'] != 'ok':
                    # The grader ran OK, but the student code didn't, so show the student
                    # details of what went wrong.  There is probably an exception to show.
                    shown_error = actual['submission']['exception'] or _('There was an error thrown while running your solution.')
                    results['errors'].append(shown_error)
            else:
                # The grader didn't run well, we are going to bail.
                actual_ok = False

        # If something went wrong, then don't continue
        if not actual_ok:
            results['errors'].append(_("We couldn't run your solution (Staff debug: L397)."))
            self.log.error("Couldn't run student solution. grader = %s, output: %r",
                           grader_path, actual_outputs, exc_info=actual_exc)
            return results

        # Compare actual and expected through the grader tests, but only if we haven't
        # already found a problem.
        corrects = []
        if not results['errors']:
            expected_results = expected['results']
            actual_results = actual['results']
            if len(expected_results) != len(actual_results):
                results['errors'].append(_('Something went wrong: different numbers of '
                                         'tests ran for your code and for our reference code.'))
                return results

            for test, exp, act in zip(grader.tests(), expected_results, actual_results):
                exp_short_desc, exp_long_desc, exp_output = exp
                act_short_desc, act_long_desc, act_output = act
                if exp_short_desc != act_short_desc:
                    results['errors'].append(_("Something went wrong: tests don't match up."))
                    # TODO: don't give up so easily?
                    return results
                # Truncate here--we don't want to send long output back, and also don't want to
                # confuse students by comparing the full output but sending back truncated output.
                act_output = truncate(act_output)
                try:
                    correct = test.compare_results(exp_output, act_output)
                except EndTest as e:
                    # Allows a grader's compare_results function to raise an EndTest exception
                    # (defined in gradelib.py). This enables the checker to print out an error
                    # message to the student, which will be appended to the end of stdout.
                    if e is not None:
                        act_output += '\n'
                        error_msg = _("ERROR")
                        act_output += "*** {error_msg}: {error_detail} ***".format(
                            error_msg=error_msg,
                            error_detail=e
                        )
                    correct = False
                corrects.append(correct)
                if not grader_config.get("hide_output", False):
                    results['tests'].append((exp_short_desc, exp_long_desc,
                                            correct, exp_output, act_output))

        # If there were no tests run, then there was probably an error, so it's incorrect
        n = len(corrects)
        results['correct'] = all(corrects) and n > 0
        results['score'] = float(sum(corrects))/n if n > 0 else 0

        if n == 0 and len(results['errors']) == 0:
            results['errors'] = [
                _("There was a problem while running your code (Staff debug: L450). "
                  "Please contact the course staff for assistance.")
            ]

        return results


def main(args):     # pragma: no cover
    """
    Prints a json list:
    [ ("Test description", "value") ]

    TODO: what about multi-file submission?
    """
    import logging
    from pprint import pprint
    from codejail.jail_code import configure
    import getpass

    logging.basicConfig(level=logging.DEBUG)
    if len(args) != 2:
        return

    configure("python", sys.executable, user=getpass.getuser())
    (grader_path, submission_path) = args

    with open(submission_path) as f:
        submission = f.read().decode('utf-8')

    grader_config = {"lang": "eo"}
    grader_path = path(grader_path).abspath()
    g = JailedGrader(grader_root=grader_path.dirname().parent.parent)
    pprint(g.grade(grader_path, grader_config, submission))


if __name__ == '__main__':      # pragma: no cover
    main(sys.argv[1:])
