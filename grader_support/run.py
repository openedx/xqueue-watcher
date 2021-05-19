#!/usr/bin/env python
"""
Run a set of tests on a submission, printing the outputs to stdout as a json
string.

Note: this command will run student code, so should be run in a sandbox, or
(and!) the code should be sanitized first.  Because this just runs the code on
various sample inputs, and does not have answers, bad student code can only hurt
itself.
"""

import gettext
import json
import random
import sys

from . import gradelib  # to set the random seed
from . import graderutil

usage = "Usage: run.py GRADER SUBMISSION seed"  # pylint: disable=invalid-name

# Install gettext for translation support. This gettext install works within the sandbox,
# so the path to graders/conf/locale can be relative.
# LANGUAGE is set in graderutil.py
trans = gettext.translation(  # pylint: disable=invalid-name
    'graders',
    localedir='conf/locale',
    fallback=True,
    languages=[graderutil.LANGUAGE]
)
_ = trans.gettext
trans.install(names=None)


def run(grader_name, submission_name, seed=1):
    """
    `grader_name`: importable module name of the grader
    `submission_name`: importable module name of the submission
    `seed`: A value to seed randomness with.

    Returns a data structure:

    {
    'grader': {
        'status': 'ok',     # or 'error', 'nograder'
        'stdout': 'whatever grader printed',
        'exception': 'a stack trace if error'
        },
    'submission': {
        'status': 'ok',     # or 'error', 'caught'
        'stdout': 'whatever the submission printed',
        'exception': 'a stack trace if error'
        },
    'results': [
        ["Test short desc", "test detailed description", "test output..."],
        ...
        ],
    'exceptions': 0,    # or however many were caught.
    }

    """

    output = {
        'grader': {
            'status': 'notrun',
        },
        'submission': {
            'status': 'notrun',
        },
        'results': [],
        'exceptions': 0,
    }

    # Use a private random number generator, so student code won't accidentally
    # mess it up.  (if they mess it up deliberately, we don't care--it only
    # hurts them).
    gradelib.rand = random.Random(seed)
    # Also seed the random singleton in case the exercise uses random numbers.
    random.seed(seed + 1)

    grader_mod, results = import_captured(grader_name, our_code=True)
    if grader_mod:
        try:
            grader = grader_mod.grader
        except:  # pylint: disable=bare-except
            results['status'] = 'error'
            results['exception'] = graderutil.format_exception()
            output['exceptions'] += 1
    else:
        output['exceptions'] += 1
    output['grader'].update(results)

    if output['grader']['status'] == 'ok':
        submission, results = import_captured(submission_name)
        output['submission'].update(results)

        if submission and output['submission']['status'] == 'ok':
            # results is a list of ("short description", "detailed desc", "output") tuples.
            try:
                for test in grader.tests():
                    with graderutil.captured_stdout() as test_stdout:
                        try:
                            exception_output = ""
                            test(submission)
                        except gradelib.EndTest:
                            grader.caught_end_test()
                        except:  # pylint: disable=bare-except
                            # The error could be either the grader code or the submission code,
                            # so hide information.
                            exception_output = graderutil.format_exception(
                                main_file=submission_name,
                                hide_file=True
                            )
                            output['exceptions'] += 1
                        else:
                            exception_output = ""
                        # Get the output, including anything printed, and any exception.
                        test_output = test_stdout.getvalue()
                        if test_output and test_output[-1] != '\n':
                            test_output += '\n'
                        test_output += exception_output
                    output['results'].append(
                        (test.short_description, test.detailed_description, test_output)
                    )
            except:  # pylint: disable=bare-except
                output['grader']['status'] = 'error'
                output['grader']['exception'] = graderutil.format_exception()
                output['exceptions'] += 1
        else:
            output['exceptions'] += 1

        if grader.uncaught_end_tests():
            # We raised EndTest more than we caught them, the student must be
            # catching them, inadvertently or not.
            output['submission']['exception'] = _(
                "Your code interfered with our grader.  Don't use bare 'except' clauses.")  # pylint: disable=line-too-long
            output['submission']['status'] = 'caught'
    return output


def import_captured(name, our_code=False):
    """
    Import the module `name`, capturing stdout, and any exceptions that happen.
    Returns the module, and a dict of results.

    If `our_code` is true, then the code is edX-authored, and any exception output
    can include full context.  If `our_code` is false, then this is student-submitted
    code, and should have only student-provided information visible in exception
    traces.  This isn't a security precaution, it just keeps us from showing confusing
    and unhelpful information to students.
    """
    result = {
        'status': 'notrun',
    }
    try:
        with graderutil.captured_stdout() as stdout:
            mod = __import__(name)
    except:  # pylint: disable=bare-except
        result['status'] = 'error'
        if our_code:
            exc = graderutil.format_exception()
        else:
            exc = graderutil.format_exception(main_file=name, hide_file=True)
        result['exception'] = exc
        mod = None
    else:
        result['status'] = 'ok'
    result['stdout'] = stdout.getvalue()
    return mod, result


def main(args):  # pragma: no cover
    """
    Execute the grader from the command line
    """
    if len(args) != 3:
        print(usage)
        return

    (grader_path, submission_path, seed) = args
    seed = int(seed)

    # strip off .py
    grader_name = grader_path[:-3]
    submission_name = submission_path[:-3]

    output = run(grader_name, submission_name, seed)
    print(json.dumps(output))


if __name__ == '__main__':  # pragma: no cover
    main(sys.argv[1:])
