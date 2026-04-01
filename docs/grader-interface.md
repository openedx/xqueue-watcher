# Grader Interface Reference

This document describes the interfaces that xqueue-watcher uses to grade student
submissions.  Understanding this interface is necessary if you want to:

- Implement a custom grader in Python
- Grade submissions written in a language other than Python
- Extend the built-in grading pipeline

## Table of Contents

- [Architecture overview](#architecture-overview)
- [The watcher-side Grader interface](#the-watcher-side-grader-interface)
  - [Grader base class](#grader-base-class)
  - [Return value of grade()](#return-value-of-grade)
  - [Submission payload format](#submission-payload-format)
- [Built-in grader implementations](#built-in-grader-implementations)
  - [Grader (base, no sandbox)](#grader-base-no-sandbox)
  - [JailedGrader (AppArmor sandbox)](#jailedgrader-apparmor-sandbox)
  - [ContainerGrader (Docker / Kubernetes)](#containergrader-docker--kubernetes)
- [Implementing a custom Python grader](#implementing-a-custom-python-grader)
- [Supporting other languages](#supporting-other-languages)
  - [Strategy 1: Custom watcher-side Grader subclass](#strategy-1-custom-watcher-side-grader-subclass)
  - [Strategy 2: Custom grader container image](#strategy-2-custom-grader-container-image)
- [The grader container protocol](#the-grader-container-protocol)
  - [Inputs](#inputs)
  - [Output](#output)
  - [Exit codes](#exit-codes)
- [The Python grading pipeline (grader_support)](#the-python-grading-pipeline-grader_support)
  - [grader_support.gradelib.Grader](#grader_supportgradelibgrader)
  - [grader_support.gradelib.Test](#grader_supportgradelibtest)
  - [Preprocessors](#preprocessors)
  - [Input checks](#input-checks)
  - [grader_support.run.run()](#grader_supportrunrun)
- [HTML result rendering](#html-result-rendering)


---

## Architecture overview

There are two distinct "grader" concepts in xqueue-watcher; it is important not to
confuse them:

```
XQueue → xqueue_watcher.grader.Grader       (watcher-side: receives submissions)
              │
              └── grade() calls ──►  grading backend
                                         │
                                         ▼
                                     grader.py + answer.py
                                     (course-side: defines tests)
```

1. **Watcher-side grader** (`xqueue_watcher.grader.Grader` and its subclasses):
   Receives a raw submission from XQueue, extracts the student code and grader path,
   invokes the grading backend, and formats the result as HTML to send back.

2. **Course-side grader** (`grader_support.gradelib.Grader` and `grader.py`):
   A Python module that defines tests, preprocessors, and input validators for a
   specific exercise.  This runs inside the grading container (or sandbox).

When grading a non-Python language you only need to replace the grading backend — the
watcher-side interface remains the same.


---

## The watcher-side Grader interface

### Grader base class

`xqueue_watcher.grader.Grader` is the abstract base class for all watcher-side graders.
To implement a custom grader, subclass it and override `grade()`:

```python
from xqueue_watcher.grader import Grader

class MyGrader(Grader):
    def grade(self, grader_path, grader_config, student_response):
        # ... run grading logic ...
        return {
            'correct': True,
            'score': 1.0,
            'tests': [
                ('Test description', 'Long description', True, 'expected\n', 'actual\n')
            ],
            'errors': [],
        }
```

**Constructor parameters** (passed as `KWARGS` in conf.d):

| Parameter | Default | Description |
|-----------|---------|-------------|
| `grader_root` | `'/tmp/'` | Absolute path to the root directory containing grader scripts. The `grader` field from the submission's `grader_payload` is resolved relative to this path. |
| `fork_per_item` | `True` | Fork a new process for each submission. `JailedGrader` and `ContainerGrader` set this to `False`. |
| `logger_name` | module name | Name of the Python logger to use. |

**`grade()` signature:**

```python
def grade(self, grader_path: Path, grader_config: dict, student_response: str) -> dict:
```

| Argument | Type | Description |
|----------|------|-------------|
| `grader_path` | `pathlib.Path` | Absolute path to the `grader.py` file for this problem (already validated to be within `grader_root`). |
| `grader_config` | `dict` | Parsed JSON from the submission's `grader_payload`. Always contains `"grader"` (the relative path); may contain additional course-defined keys. |
| `student_response` | `str` | The raw student-submitted code as a string. |

The base class raises `NotImplementedError`.  Subclasses must override this method.

### Return value of grade()

`grade()` must return a `dict` with the following keys:

```python
{
    'correct': bool,          # True if the overall submission is correct
    'score':   float,         # 0.0 – 1.0 (fraction of tests passed)
    'tests':   list,          # list of per-test result tuples (see below)
    'errors':  list[str],     # list of error messages to show the student
}
```

Each entry in `tests` is a 5-tuple:

```
(short_description, long_description, correct, expected_output, actual_output)
```

| Position | Type | Description |
|----------|------|-------------|
| 0 | `str` | Short test description (shown as a heading). |
| 1 | `str` | Long test description (can be empty string). |
| 2 | `bool` | Whether this individual test passed. |
| 3 | `str` | Expected output (from the staff answer). |
| 4 | `str` | Actual output (from the student submission). |

When `errors` is non-empty the overall result is marked `ERROR` regardless of
`correct`.  Tests that did not run may be omitted from `tests`.

### Submission payload format

xqueue-watcher receives submissions from XQueue in this structure:

```json
{
    "xqueue_body": "{\"student_response\": \"...\", \"grader_payload\": \"...\", \"student_info\": {...}}",
    "xqueue_files": {}
}
```

The `grader_payload` field is a JSON string (double-encoded) that must contain at least:

```json
{"grader": "relative/path/to/grader.py"}
```

The `grader` path is resolved relative to `grader_root`.  Path traversal sequences
(`..`) are rejected.  The resolved path must remain within `grader_root`.


---

## Built-in grader implementations

### Grader (base, no sandbox)

`xqueue_watcher.grader.Grader`

The base class — does not implement `grade()`.  Used directly only when a fully custom
`grade()` implementation is provided.  When subclassed and `grade()` is left unimplemented
an exception is raised for every submission.

### JailedGrader (AppArmor sandbox)

`xqueue_watcher.jailedgrader.JailedGrader`

Runs Python submissions inside [CodeJail](https://github.com/openedx/codejail), which
uses Linux AppArmor to restrict what sandboxed code can do.

> **Note**: Requires an AppArmor-enabled host and the optional `codejail` dependency.
> This grader is **not suitable for Kubernetes** deployments.  Use `ContainerGrader`
> instead.

**Additional `KWARGS`**:

| Key | Default | Description |
|-----|---------|-------------|
| `codejail_python` | `"python"` | Name of the CodeJail sandbox to use (as configured with `jail_code.configure()`). |

**`CODEJAIL` handler config** (configures CodeJail in the manager):

```json
{
    "HANDLER": "xqueue_watcher.jailedgrader.JailedGrader",
    "CODEJAIL": {
        "name": "python",
        "bin_path": "/path/to/sandbox/python",
        "user": "sandbox_username",
        "limits": {
            "CPU": 1,
            "VMEM": 536870912
        }
    },
    "KWARGS": {
        "grader_root": "/path/to/graders/"
    }
}
```

`JailedGrader` expects `grader.py` and `answer.py` to exist in the same directory.
It runs both through the Python grading pipeline described in
[The Python grading pipeline](#the-python-grading-pipeline-grader_support).

### ContainerGrader (Docker / Kubernetes)

`xqueue_watcher.containergrader.ContainerGrader`

The recommended grader for Kubernetes deployments.  Runs each submission in an
isolated container (a Kubernetes Job or a local Docker container).

**`KWARGS`**:

| Key | Env override | Default | Description |
|-----|-------------|---------|-------------|
| `grader_root` | — | required | Path to the grader directory inside the container (or bind-mounted from the host for the Docker backend). |
| `image` | — | required | Docker image to run for grading. Must extend `grader-base`. |
| `backend` | `XQWATCHER_GRADER_BACKEND` | `"kubernetes"` | `"kubernetes"` or `"docker"`. |
| `namespace` | `XQWATCHER_GRADER_NAMESPACE` | `"default"` | Kubernetes namespace for grading Jobs. |
| `cpu_limit` | `XQWATCHER_GRADER_CPU_LIMIT` | `"500m"` | CPU limit for grading containers. |
| `memory_limit` | `XQWATCHER_GRADER_MEMORY_LIMIT` | `"256Mi"` | Memory limit. |
| `timeout` | `XQWATCHER_GRADER_TIMEOUT` | `20` | Max wall-clock seconds per grading job. |
| `docker_host_grader_root` | `XQWATCHER_DOCKER_HOST_GRADER_ROOT` | `None` | Host-side path to `grader_root` when xqueue-watcher runs in Docker. |
| `image_pull_policy` | — | auto | Kubernetes `imagePullPolicy`. Auto-detected from image ref: `"IfNotPresent"` for digest refs, `"Always"` for tag refs. |
| `poll_image_digest` | — | `false` | Resolve tag to digest in the background; use pinned digest for grading Jobs. |
| `digest_poll_interval` | — | `300` | Seconds between digest resolution polls. |

See [Operator Guide — ContainerGrader](operators.md#containergrader-docker--kubernetes)
for full deployment guidance.


---

## Implementing a custom Python grader

To add custom logic at the watcher level (for example, to call an external API or
apply institution-specific rules before returning a result), subclass
`xqueue_watcher.grader.Grader`:

```python
# my_package/mygrader.py
from xqueue_watcher.grader import Grader

class MyGrader(Grader):
    def __init__(self, rubric_path, **kwargs):
        super().__init__(**kwargs)
        self.rubric_path = rubric_path

    def grade(self, grader_path, grader_config, student_response):
        # Call the base ContainerGrader logic, a subprocess, an API, etc.
        # Must return a dict matching the schema in "Return value of grade()".
        ...
```

Register it in conf.d:

```json
{
    "HANDLER": "my_package.mygrader.MyGrader",
    "KWARGS": {
        "grader_root": "/graders/",
        "rubric_path": "/rubrics/course-101.json"
    }
}
```

The `my_package` module must be importable from the Python environment where
xqueue-watcher runs.


---

## Supporting other languages

xqueue-watcher is not limited to Python.  Two strategies exist for grading
submissions in other languages.

### Strategy 1: Custom watcher-side Grader subclass

Write a `Grader` subclass whose `grade()` method invokes an external tool or service
to run and evaluate the student submission.  The subclass is responsible for:

- Running the student code in an appropriate sandbox.
- Collecting test results.
- Returning a dict matching the [grade() return schema](#return-value-of-grade).

Example skeleton for a Java grader:

```python
import subprocess
from xqueue_watcher.grader import Grader

class JavaGrader(Grader):
    def grade(self, grader_path, grader_config, student_response):
        # Write the student submission to a temp file
        # Compile and run using javac / java in a subprocess
        # Parse the test output
        # Return results dict
        ...
```

This approach is suitable when you control the execution environment (e.g. the grader
runs directly on a prepared VM or in a container that already has the required runtime
installed).

### Strategy 2: Custom grader container image

The `ContainerGrader` passes the student submission to the container via the
`SUBMISSION_CODE` environment variable and reads the grade result from the container's
stdout (see [The grader container protocol](#the-grader-container-protocol)).

You can replace the Python-based entrypoint in the container with any program that
honours this protocol, making `ContainerGrader` language-agnostic.

**Steps:**

1. Write a grading entrypoint in your language of choice that reads
   `SUBMISSION_CODE` from the environment, runs the tests, and prints the result
   JSON to stdout.

2. Build a Docker image that uses this entrypoint and includes the required runtime
   and your grader scripts.

3. Reference the image in your conf.d `KWARGS`.

See the next section for the exact protocol your container must implement.


---

## The grader container protocol

`ContainerGrader` communicates with grading containers through a simple
environment-variable-in / JSON-out protocol.  Any container that implements this
protocol can be used as a grader backend, regardless of programming language.

### Inputs

The container receives the following environment variables:

| Variable | Description |
|----------|-------------|
| `SUBMISSION_CODE` | The raw student submission as a UTF-8 string. Always set; may be empty if the student submitted nothing. |
| `GRADER_LANGUAGE` | BCP-47 language tag for i18n in feedback messages (e.g. `"en"`, `"es"`). Defaults to `"en"`. |
| `HIDE_OUTPUT` | If `"1"`, `"true"`, or `"yes"`, omit per-test output details from the result (students see only correct/incorrect). Defaults to `"0"`. |
| `GRADER_DEBUG` | If `"1"`, `"true"`, or `"yes"`, print step-by-step debug output to stderr. Defaults to `"0"`. |

The container is also started with command-line arguments:

```
<entrypoint> GRADER_PATH SEED
```

| Argument | Description |
|----------|-------------|
| `GRADER_PATH` | Absolute path (inside the container) to the grader definition file for this problem. |
| `SEED` | Integer random seed for reproducibility. Both the staff answer and the student submission must use this seed. |

### Output

The container must write a single JSON object to **stdout** and then exit.  No other
output should appear on stdout (use stderr for diagnostics).

```json
{
    "errors": ["optional error message visible to student"],
    "tests": [
        ["Short description", "Long description", true, "expected output\n", "actual output\n"]
    ],
    "correct": true,
    "score": 1.0
}
```

| Field | Type | Description |
|-------|------|-------------|
| `errors` | `list[str]` | Error messages shown to the student. Empty list if no errors. |
| `tests` | `list` | Per-test result tuples: `[short_desc, long_desc, correct, expected, actual]`. May be empty when `errors` is non-empty. |
| `correct` | `bool` | Whether the overall submission is considered correct. |
| `score` | `float` | Score in the range `[0.0, 1.0]`. |

The `ContainerGrader` parses the **last line** of stdout as JSON, so debug output
written to stdout before the final JSON line will break parsing.  Always write debug
output to **stderr**.

### Exit codes

| Exit code | Meaning |
|-----------|---------|
| `0` | Grading completed (result JSON is on stdout). |
| non-zero | Grading failed; `ContainerGrader` raises a `RuntimeError` and the submission is returned as an error to the student. |

The container is always killed after `timeout` seconds regardless of exit status.


---

## The Python grading pipeline (grader_support)

For Python-based graders the `grader_support` package provides a ready-made grading
framework.  The built-in `grader-base` Docker image runs this pipeline automatically
via `grader_support.entrypoint`.

### grader_support.gradelib.Grader

The course-side grader object.  An instance named `grader` must be assigned at module
level in `grader.py`.

```python
from grader_support import gradelib

grader = gradelib.Grader()
```

**Methods:**

| Method | Description |
|--------|-------------|
| `add_test(test)` | Append a `Test` object to the test suite. |
| `add_preprocessor(fn)` | Append a preprocessor function. Preprocessors are applied in order to both the staff answer and the student submission before any test runs. |
| `add_input_check(check)` | Append an input check function. Checks run before preprocessing; a non-None return value aborts grading with an error message. |
| `add_tests_from_class(cls)` | Add tests from a class: each method starting with `test_` becomes a `Test`. |
| `tests()` | Return the list of `Test` objects. |
| `input_errors(submission_str)` | Run all input checks and return a list of error strings. |
| `preprocess(submission_str)` | Apply all preprocessors and return the result. |

### grader_support.gradelib.Test

Represents a single test case.

```python
test = gradelib.Test(
    test_fn,               # callable: (submission_module) -> None, printing to stdout
    short_description,     # str: concise description shown in feedback
    detailed_description,  # str: longer description (can be '')
    compare=None,          # optional callable: (expected_str, actual_str) -> bool
)
```

The `test_fn` callable receives the imported submission module and should print
something to stdout.  The printed output is compared to the staff answer's output for
the same test.

**`compare_results(expected, actual) -> bool`**

The default comparison is `expected == actual`.  Override for numeric tolerance,
ordering-independent comparison, etc.:

```python
def numeric_compare(expected, actual):
    try:
        return abs(float(expected.strip()) - float(actual.strip())) < 1e-4
    except (ValueError, TypeError):
        return False

test = gradelib.Test(test_fn, "Test precision", compare=numeric_compare)
```

`compare_results` may also raise `gradelib.EndTest(message)` to produce a custom
error message appended to the student's output.

**Built-in test helpers:**

| Name | Description |
|------|-------------|
| `gradelib.InvokeStudentFunctionTest(fn_name, args, ...)` | Call `submission_module.<fn_name>(*args)` and print the result. |
| `gradelib.ExecWrappedStudentCodeTest(environment, ...)` | Exec the submission code (pre-wrapped by `wrap_in_string`) in the given namespace. |
| `gradelib.invoke_student_function(fn_name, args, ...)` | Lower-level function version of `InvokeStudentFunctionTest`. |
| `gradelib.exec_wrapped_code(environment, post_process)` | Lower-level function version of `ExecWrappedStudentCodeTest`. |

### Preprocessors

Preprocessors transform the submission text before it is executed.  They receive and
return a string.

Built-in preprocessors:

| Name | Description |
|------|-------------|
| `gradelib.fix_line_endings` | Remove `\r` characters. **Installed by default.** |
| `gradelib.wrap_in_string` | Wrap the code in `submission_code = <repr of code>` so it can be exec'd multiple times. Required before `ExecWrappedStudentCodeTest`. |

Custom preprocessors:

```python
def add_import(code):
    return "import math\n" + code

grader.add_preprocessor(add_import)
```

Preprocessors run in the order they were added.

### Input checks

Input checks receive the raw (unpreprocessed) submission text and return either `None`
(check passed) or a non-empty string (error message to show the student).  They run
**before** any preprocessing or code execution.

Built-in check factories:

| Factory | Description |
|---------|-------------|
| `gradelib.required_substring(s)` | Fail if `s` is not present in the code. |
| `gradelib.prohibited_substring(s)` | Fail if `s` is present in the code. |
| `gradelib.required_keyword(kw)` | Fail if `kw` does not appear as a token (ignores comments/strings). |
| `gradelib.prohibited_keyword(kw)` | Fail if `kw` appears as a token. |
| `gradelib.must_define_function(name)` | Fail if no `def <name>` is found. |
| `gradelib.must_define_class(name)` | Fail if no `class <name>` is found. |
| `gradelib.prohibited_function_definition(name)` | Fail if `def <name>` is found. |
| `gradelib.required_class_method(class_name, method_name)` | Fail if the named class does not define the named method. |
| `gradelib.prohibited_class_method(class_name, method_name)` | Fail if the named class defines the named method. |
| `gradelib.substring_occurs(s, at_least=N, at_most=M)` | Check that `s` appears a certain number of times. |
| `gradelib.token_occurs(s, at_least=N, at_most=M)` | Same but token-aware (ignores comments/strings). |
| `gradelib.count_non_comment_lines(at_least=N, at_most=M)` | Restrict the number of substantive source lines. |
| `gradelib.one_of_required_keywords(list)` | Fail if none of the given keywords appear. |
| `gradelib.input_check_or(error_msg, *checks)` | Pass if any of the given checks pass. |

### grader_support.run.run()

The low-level function that imports a grader and a submission module, runs all tests,
and returns a raw result dict.  It is used internally by both `JailedGrader` and the
container entrypoint.

```python
from grader_support.run import run

result = run(grader_name, submission_name, seed)
```

| Parameter | Description |
|-----------|-------------|
| `grader_name` | Importable module name of the grader (without `.py`). |
| `submission_name` | Importable module name of the submission file (without `.py`). |
| `seed` | Integer random seed. |

Returns:

```python
{
    'grader':     {'status': 'ok', 'stdout': '...', 'exception': None},
    'submission': {'status': 'ok', 'stdout': '...', 'exception': None},
    'results':    [("short desc", "long desc", "output"), ...],
    'exceptions': 0,
}
```

`status` is one of `'ok'`, `'error'`, `'caught'`, or `'notrun'`.

You can call `run()` directly for unit-testing your grader scripts:

```python
import sys
sys.path.insert(0, '/path/to/exercise-3/')

from grader_support.run import run

output = run('grader', 'answer', seed=42)
assert output['grader']['status'] == 'ok'
assert output['submission']['status'] == 'ok'
assert all(r[2] != '' for r in output['results'])  # all tests produced output
```


---

## HTML result rendering

`xqueue_watcher.grader.Grader.render_results()` converts the `grade()` return dict
into an HTML string for display in the LMS.  The HTML structure is:

```html
<div class="test">
  <header>Test results</header>
  <section>
    <div class="shortform">CORRECT | INCORRECT | ERROR</div>
    <div class="longform">
      <!-- per-test result blocks -->
      <div class="result-output result-correct">...</div>
      <div class="result-output result-incorrect">...</div>
      <!-- error block if any -->
      <div class="result-errors">...</div>
    </div>
  </section>
</div>
```

All user-supplied strings (test descriptions, output, error messages) are HTML-escaped
before insertion.

If you need a different visual layout, override `render_results()` in your `Grader`
subclass.  The method signature is:

```python
def render_results(self, results: dict) -> str:
    ...
```

where `results` is the dict returned by `grade()`.
