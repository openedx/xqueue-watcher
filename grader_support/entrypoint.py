"""
Entrypoint for running the complete grading pipeline inside a container.

The grader scripts (grader file, answer.py) are baked into this image.
This module reads SUBMISSION_CODE from the environment, runs both the staff
answer and the student submission through the grader, compares results, and
prints the final grade as JSON to stdout.

Usage (set by Dockerfile ENTRYPOINT):
    python -m grader_support.entrypoint GRADER_FILE SEED
"""

import importlib.util
import json
import os
import sys
import traceback

_DEBUG = os.environ.get("GRADER_DEBUG", "").lower() in ("1", "true", "yes")


def _dbg(*args):
    """Print debug info to stderr when GRADER_DEBUG=1.

    Kubernetes reads pod logs via read_namespaced_pod_log which captures both
    stdout and stderr.  Keep this off by default so the JSON on stdout is the
    only output in the pod log that containergrader.py needs to parse.
    """
    if _DEBUG:
        print("[DEBUG entrypoint]", *args, file=sys.stderr, flush=True)


def main():
    if len(sys.argv) != 3:
        print(
            "Usage: python -m grader_support.entrypoint GRADER_FILE SEED",
            file=sys.stderr,
        )
        sys.exit(1)

    grader_path = sys.argv[1]
    seed = int(sys.argv[2])
    submission_code = os.environ.get("SUBMISSION_CODE", "")

    _dbg(f"grader_path={grader_path!r}  seed={seed}")
    _dbg(f"submission_code ({len(submission_code)} chars): {submission_code[:120]!r}")

    results = {"errors": [], "tests": [], "correct": False, "score": 0}

    # Install gettext into builtins BEFORE loading the grader module.
    # Grader scripts may call _() at module level (e.g. in input_validators),
    # so _ must be available before exec_module runs.
    import gettext
    lang = os.environ.get("GRADER_LANGUAGE", "en")
    grader_dir = os.path.dirname(os.path.abspath(grader_path))
    locale_dir = os.path.join(grader_dir, "conf", "locale")
    _dbg(f"grader_dir={grader_dir!r}  locale_dir={locale_dir!r}")
    trans = gettext.translation(
        "graders", localedir=locale_dir, fallback=True, languages=[lang]
    )
    trans.install(names=None)
    _dbg("gettext installed")

    # Load the grader module to access test definitions, preprocessors, and
    # input validators.  The grader script is baked into this image.
    _dbg(f"loading grader module from {grader_path!r}")
    try:
        spec = importlib.util.spec_from_file_location("grader_module", grader_path)
        grader_module_obj = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(grader_module_obj)
        grader = grader_module_obj.grader
        _dbg(f"grader module loaded OK, tests={len(list(grader.tests()))}")
    except Exception:
        _dbg("EXCEPTION loading grader module:")
        traceback.print_exc(file=sys.stderr)
        raise

    # Validate submission format before doing any work.
    _dbg("checking input_errors")
    try:
        errors = grader.input_errors(submission_code)
    except Exception:
        _dbg("EXCEPTION in input_errors:")
        traceback.print_exc(file=sys.stderr)
        raise
    if errors:
        _dbg(f"input_errors returned: {errors}")
        results["errors"].extend(errors)
        print(json.dumps(results))
        return
    _dbg("input_errors: none")

    # Preprocess both the staff answer and the student submission.
    answer_path = os.path.join(grader_dir, "answer.py")
    _dbg(f"reading answer from {answer_path!r}")
    with open(answer_path, "rb") as f:
        answer = f.read().decode("utf-8")
    _dbg(f"answer ({len(answer)} chars): {answer[:200]!r}")

    # Normalize tabs to spaces before preprocessing.  Many course grader files
    # were authored for Python 2 which tolerated mixed tab/space indentation;
    # Python 3's exec raises TabError on such code.
    answer = answer.expandtabs(4)
    submission_code = submission_code.expandtabs(4)

    processed_answer = "# coding: utf8\n" + grader.preprocess(answer)
    processed_submission = "# coding: utf8\n" + grader.preprocess(submission_code)
    _dbg(f"processed_answer ({len(processed_answer)} chars): {processed_answer[:300]!r}")
    _dbg(f"processed_submission ({len(processed_submission)} chars): {processed_submission[:300]!r}")

    # Write to /tmp, which is backed by an emptyDir volume mount in Kubernetes
    # (readOnlyRootFilesystem=True prevents writes to the root FS).
    with open("/tmp/answer.py", "w", encoding="utf-8") as f:
        f.write(processed_answer)
    with open("/tmp/submission.py", "w", encoding="utf-8") as f:
        f.write(processed_submission)
    _dbg("wrote /tmp/answer.py and /tmp/submission.py")

    # Make /tmp and the grader directory importable so run.py can find them.
    # /tmp must come BEFORE grader_dir: the preprocessed answer.py and
    # submission.py in /tmp must shadow the original source files in grader_dir.
    sys.path.insert(0, grader_dir)
    sys.path.insert(0, "/tmp")
    _dbg(f"sys.path[:4]={sys.path[:4]}")

    from . import run as run_module
    from .gradelib import EndTest

    grader_name = os.path.splitext(os.path.basename(grader_path))[0]
    _dbg(f"grader_name={grader_name!r}")

    # Run the staff answer first to get expected outputs.
    _dbg("running staff answer")
    expected_output = run_module.run(grader_name, "answer", seed)
    _dbg(f"expected_output grader status={expected_output['grader']['status']!r}"
         f"  submission status={expected_output['submission']['status']!r}"
         f"  exceptions={expected_output['exceptions']}"
         f"  results_count={len(expected_output['results'])}")
    if expected_output["grader"].get("exception"):
        _dbg(f"grader exception:\n{expected_output['grader']['exception']}")
    if expected_output["submission"].get("exception"):
        _dbg(f"answer exception:\n{expected_output['submission']['exception']}")

    expected_ok = (
        not expected_output["exceptions"]
        and expected_output["grader"]["status"] == "ok"
        and expected_output["submission"]["status"] == "ok"
    )
    if not expected_ok:
        _dbg("expected_ok=False → returning staff-solution error")
        results["errors"].append(
            "There was a problem running the staff solution (Staff debug)."
        )
        print(json.dumps(results))
        return

    # Run the student submission.
    _dbg("running student submission")
    actual_output = run_module.run(grader_name, "submission", seed)
    _dbg(f"actual_output grader status={actual_output['grader']['status']!r}"
         f"  submission status={actual_output['submission']['status']!r}"
         f"  exceptions={actual_output['exceptions']}"
         f"  results_count={len(actual_output['results'])}")
    if actual_output["submission"].get("exception"):
        _dbg(f"submission exception:\n{actual_output['submission']['exception']}")

    actual_ok = actual_output["grader"]["status"] == "ok"

    if actual_output["submission"]["status"] != "ok":
        shown_error = actual_output["submission"].get("exception") or (
            "There was an error thrown while running your solution."
        )
        results["errors"].append(shown_error)
        actual_ok = False

    if not actual_ok:
        results["errors"].append("We couldn't run your solution (Staff debug).")
        print(json.dumps(results))
        return

    # Compare test results.
    expected_results = expected_output["results"]
    actual_results = actual_output["results"]

    if len(expected_results) != len(actual_results):
        results["errors"].append(
            "Something went wrong: different numbers of tests ran for "
            "your code and for our reference code."
        )
        print(json.dumps(results))
        return

    hide_output = os.environ.get("HIDE_OUTPUT", "").lower() in ("1", "true", "yes")
    TOO_LONG = 5000
    corrects = []

    for test, exp, act in zip(grader.tests(), expected_results, actual_results):
        exp_short, exp_long, exp_out = exp
        act_short, act_long, act_out = act

        if exp_short != act_short:
            results["errors"].append("Something went wrong: tests don't match up.")
            print(json.dumps(results))
            return

        if len(act_out) > TOO_LONG:
            act_out = act_out[:TOO_LONG] + "...OUTPUT TRUNCATED"

        try:
            correct = test.compare_results(exp_out, act_out)
        except EndTest as e:
            if str(e).strip():
                act_out += f"\n*** ERROR: {e} ***"
            correct = False

        corrects.append(correct)
        if not hide_output:
            results["tests"].append(
                (exp_short, exp_long, correct, exp_out, act_out)
            )

    n = len(corrects)
    results["correct"] = all(corrects) and n > 0
    results["score"] = float(sum(corrects)) / n if n > 0 else 0

    if n == 0 and not results["errors"]:
        results["errors"] = [
            "There was a problem while running your code (Staff debug). "
            "Please contact the course staff for assistance."
        ]

    print(json.dumps(results))


if __name__ == "__main__":
    main()
