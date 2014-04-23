import sys
import imp
import json
import random
import gettext
import tempfile
from path import path
from codejail.jail_code import jail_code

from .grader import Grader

TIMEOUT = 1

THIS_DIR = path(__file__).dirname()
SUPPORT_DIR = THIS_DIR.parent / "grader_support"
SUPPORT_FILES = [
    SUPPORT_DIR / "run.py",
    SUPPORT_DIR / "gradelib.py",
    SUPPORT_DIR / "graderutil.py"
]
sys.path.append(SUPPORT_DIR)

from gradelib import EndTest
from graderutil import LANGUAGE
# LANGUAGE is set in graderutil.py
trans = gettext.translation('graders', localedir=THIS_DIR / "conf" / "locale", fallback=True, languages=[LANGUAGE])
trans.install(unicode=True, names=None)


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
    def _run(self, grader_path, thecode, seed):
        sub = tempfile.NamedTemporaryFile(suffix=".py", delete=False)
        try:
            sub.write(thecode.encode('utf-8'))
            sub.close()
            files = SUPPORT_FILES + [grader_path, sub.name]
            argv = ["run.py", path(grader_path).basename(), path(sub.name).basename(), seed]
            r = jail_code("python", files=files, argv=argv)
            return r
        finally:
            sub.unlink(sub.name)

    def syntax_error(self, submission):
        """
        Check the code for syntax errors
        """
        return False

    def grade(self, grader_path, grader_config, submission, sandbox=None):
        if type(submission) != unicode:
            self.log.warning("Submission is NOT unicode")
        _ = unicode

        # if sandbox:
        #     log_evil = sandbox.record_suspicious_submission
        # else:
        #     log_evil = record_evil_locally

        results = {
            'errors': [],
            'tests': [],
            'correct': False,
            'score': 0,
        }

        answer_path = path(grader_path).dirname() / 'answer.py'
        with open(answer_path) as f:
            answer = f.read().decode('utf-8')

        # Check the student submission for syntax errors.  We won't get far if it
        # has them, and further tests may misdiagnose in the presence of syntax errors.
        synerr = self.syntax_error(submission)
        if synerr:
            results['errors'].append(synerr)
            return results

        # Import the grader, straight from the original file.  (It probably isn't in
        # sys.path, and we may be in a long running gunicorn process, so we don't
        # want to add stuff to sys.path either.)
        grader_module = imp.load_source("grader_module", grader_path)
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
        # Timeout for the runs
        # timeout = int(grader_config.get('timeout', TIMEOUT))

        # Run the official answer, to get the expected output.
        expected_ok = False
        expected_exc = None
        try:
            # If we want a factor of two speedup for now: trust the staff solution to
            # avoid hitting the sandbox. (change run to run_trusted)
            expected_outputs = None # in case run_trusted raises an exception.
            expected_outputs = self._run(grader_path, processed_answer, seed).stdout
            if expected_outputs:
                expected = json.loads(expected_outputs)
                expected_ok = True
        except:
            expected_exc = sys.exc_info()
        else:
            # We just ran the official answer, nothing should have gone wrong, so check
            # everything, and note it as bad if anything is wrong.
            if expected_ok:
                if expected['exceptions'] or expected['grader']['status'] != 'ok' or expected['submission']['status'] != 'ok':
                    expected_ok = False

        if not expected_ok:
            # We couldn't run the official answer properly, bail out, but don't show
            # details to the student, since none of it is their code.
            results['errors'].append(_('There was a problem running the staff solution (Staff debug: L364)'))
            self.log.error("Couldn't run staff solution. grader = %s, output: %r", grader_path, expected_outputs, exc_info=expected_exc)
            return results

        # The expected code ran fine, go ahead and run the student submission.
        actual_ok = False
        actual_exc = None
        try:
            # Do NOT trust the student solution (in production).
            actual_outputs = None   # in case run raises an exception.
            actual_outputs = self._run(grader_path, processed_submission, seed).stdout
            if actual_outputs:
                actual = json.loads(actual_outputs)
                actual_ok = True
            else:
                results['errors'].append(_("There was a problem running your solution (Staff debug: L379)."))
        except:
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
            self.log.error("Couldn't run student solution. grader = %s, output: %r", grader_path, actual_outputs, exc_info=actual_exc)
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
                        act_output += u"*** {error_msg}: {error_detail} ***".format(
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
