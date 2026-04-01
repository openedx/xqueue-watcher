# Course Team Guide: Writing and Testing Graders

This guide explains how to write grader scripts for xqueue-watcher-based courses and
how to test them locally — **without a running Open edX instance or XQueue service**.

## Table of Contents

- [How grading works](#how-grading-works)
- [Grader directory structure](#grader-directory-structure)
- [Writing a grader.py](#writing-a-graderpy)
  - [Available utilities (grader_support)](#available-utilities-grader_support)
  - [Minimal example](#minimal-example)
  - [Testing function calls](#testing-function-calls)
  - [Testing script-style code](#testing-script-style-code)
  - [Input validation](#input-validation)
  - [Custom test comparisons](#custom-test-comparisons)
- [Writing an answer.py](#writing-an-answerpy)
- [Configuring the problem in Studio](#configuring-the-problem-in-studio)
- [Local testing without edX](#local-testing-without-edx)
  - [Option 1: Run the grading pipeline directly](#option-1-run-the-grading-pipeline-directly)
  - [Option 2: Run inside a grader container (Docker)](#option-2-run-inside-a-grader-container-docker)
  - [Option 3: Full local stack with docker-compose](#option-3-full-local-stack-with-docker-compose)
- [Building a grader image](#building-a-grader-image)
- [Troubleshooting](#troubleshooting)


---

## How grading works

When a student submits code for a programming exercise, the following happens:

1. The Open edX LMS sends the submission to XQueue.
2. xqueue-watcher picks it up, extracts `grader_payload` from the submission, and
   resolves it to a `grader.py` file on the server.
3. The grader runs the **staff answer** (`answer.py`) and the **student submission**
   through the test suite defined in `grader.py`.
4. The outputs of each test are compared.  Tests where the outputs match are marked
   correct.
5. A score and HTML feedback message are returned to the student.

Both `grader.py` and `answer.py` are authored by the course team.


---

## Grader directory structure

Each problem that uses xqueue-watcher needs a folder containing:

```
my-course/
└── unit-2/
    └── exercise-3/
        ├── grader.py     # defines the tests
        └── answer.py     # the reference (correct) solution
```

The path `unit-2/exercise-3/grader.py` is what goes into the Studio grader payload
(see [Configuring the problem in Studio](#configuring-the-problem-in-studio)).


---

## Writing a grader.py

`grader.py` defines a **`grader`** object — an instance of `grader_support.gradelib.Grader`.
The grader collects tests, preprocessors, and input validators.  When a submission
arrives, xqueue-watcher calls these in order to produce a result.

> **Important**: `grader.py` must assign to a module-level variable named exactly
> `grader`.  xqueue-watcher imports the file and reads `grader_module.grader`.

### Available utilities (grader_support)

`grader_support` is bundled with xqueue-watcher and is always available inside grading
containers.  Import it at the top of your `grader.py`:

```python
from grader_support import gradelib
```

Key classes and functions:

| Name | Description |
|------|-------------|
| `gradelib.Grader` | Base class for the grader object. Add tests, preprocessors, and input checks to it. |
| `gradelib.Test` | Wrap a test function with descriptions and an optional custom comparator. |
| `gradelib.InvokeStudentFunctionTest` | Convenience: call a named function from the student submission with given arguments. |
| `gradelib.ExecWrappedStudentCodeTest` | Convenience: exec the student code in a namespace and capture stdout. |
| `gradelib.wrap_in_string` | Preprocessor: wrap raw code in a string so it can be exec'd multiple times. |
| `gradelib.fix_line_endings` | Preprocessor: normalise `\r\n` to `\n` (installed by default). |
| `gradelib.required_substring` | Input check factory: fail if a required string is missing. |
| `gradelib.prohibited_substring` | Input check factory: fail if a forbidden string is present. |
| `gradelib.EndTest` | Exception: raise inside a test to end it early with an error message. |

### Minimal example

```python
# grader.py — test that the student's `add` function returns the right value
from grader_support import gradelib

grader = gradelib.Grader()
grader.add_test(gradelib.InvokeStudentFunctionTest('add', [2, 3]))
grader.add_test(gradelib.InvokeStudentFunctionTest('add', [-1, 1]))
grader.add_test(gradelib.InvokeStudentFunctionTest('add', [0, 0]))
```

The matching `answer.py`:

```python
# answer.py
def add(a, b):
    return a + b
```

A student submission that defines `add` correctly will pass all three tests.

### Testing function calls

`InvokeStudentFunctionTest` calls a function by name from the submission module and
prints the return value to stdout.  The test passes if the output matches the staff
answer's output for the same call.

```python
grader.add_test(gradelib.InvokeStudentFunctionTest('function_name', [arg1, arg2, ...]))
```

You can also write a test function directly:

```python
def test_my_function(submission_module):
    result = submission_module.my_function(42)
    print(result)   # printed output is compared to the staff answer's output

grader.add_test(gradelib.Test(test_my_function, "Test my_function(42)"))
```

### Testing script-style code

For early exercises where students write top-level code rather than functions, use the
`wrap_in_string` preprocessor together with `ExecWrappedStudentCodeTest`:

```python
from grader_support import gradelib

grader = gradelib.Grader()
grader.add_preprocessor(gradelib.wrap_in_string)
grader.add_test(gradelib.ExecWrappedStudentCodeTest({}, "Run the code and check stdout"))
```

The `answer.py` for this style contains the same top-level code:

```python
# answer.py — expected output when the code runs
x = 10
print(x * 2)
```

### Input validation

Input checks run **before** the submission is executed.  They are safe to use because
they only inspect the source text.  A failed check returns an error message to the
student and stops grading.

```python
grader.add_input_check(gradelib.required_substring('def solve('))
grader.add_input_check(gradelib.prohibited_substring('import os'))
```

You can write a custom check:

```python
def must_use_recursion(code):
    if 'def ' not in code:
        return "Your solution must define a function."
    return None   # None means the check passed

grader.add_input_check(must_use_recursion)
```

### Custom test comparisons

By default, test results are compared with simple string equality.  Override
`compare_results` on a `Test` instance or via `add_tests_from_class` for custom logic:

```python
def compare_floats(expected, actual):
    try:
        return abs(float(expected) - float(actual)) < 1e-6
    except ValueError:
        return False

grader.add_test(gradelib.Test(
    lambda mod: print(mod.compute_pi()),
    "Test compute_pi()",
    compare=compare_floats,
))
```

The `compare_results(expected, actual)` function receives the stdout output of each run
as strings and must return `True` (pass) or `False` (fail).

You can also raise `gradelib.EndTest` inside a test to produce a custom error message
for the student:

```python
def test_sorted(submission_module):
    result = submission_module.my_sort([3, 1, 2])
    if not isinstance(result, list):
        raise gradelib.EndTest("my_sort should return a list, not {!r}".format(type(result).__name__))
    print(result)
```


---

## Writing an answer.py

`answer.py` is the **reference solution**.  It is run through the same test suite as
the student submission; its output becomes the "expected" result for each test.

Rules:
- It must be in the same directory as `grader.py`.
- It must produce correct output for every test in `grader.py`.
- It is never shown to students — it runs inside the grading container only.

```python
# answer.py
def add(a, b):
    return a + b
```


---

## Configuring the problem in Studio

In the Open edX Studio problem editor, set the **grader payload** to a JSON object
containing the relative path to your `grader.py` from the `grader_root` configured for
your queue:

```json
{"grader": "unit-2/exercise-3/grader.py"}
```

Additional fields in the grader payload are passed to the grader as `grader_config` and
can be read inside `grader.py` if needed:

```json
{
    "grader": "unit-2/exercise-3/grader.py",
    "lang": "en",
    "hide_output": false,
    "skip_grader": false
}
```

| Field | Description |
|-------|-------------|
| `grader` | **Required.** Relative path to `grader.py` from `grader_root`. |
| `lang` | Language code for i18n in feedback messages (default: `en`). |
| `hide_output` | If `true`, test output details are hidden from the student (default: `false`). |
| `skip_grader` | If `true`, always marks the submission correct with a full score. Useful for problems where automated grading is not feasible (default: `false`). |


---

## Local testing without edX

You can run the complete grading pipeline locally without an Open edX instance, an
XQueue service, or any network connectivity.  Pick the option that fits your workflow.

### Option 1: Run the grading pipeline directly

This is the fastest approach.  Install xqueue-watcher with its dependencies, then call
`grader_support.entrypoint` directly from the command line:

```bash
# Install xqueue-watcher (once)
cd xqueue-watcher/
uv sync    # or: pip install -e .

# Add grader_support to your Python path
export PYTHONPATH="$PYTHONPATH:$(pwd)"
```

Create a file containing the student submission you want to test, e.g.
`/tmp/student_submission.py`:

```python
def add(a, b):
    return a + b
```

Then run the entrypoint, passing the `SUBMISSION_CODE` environment variable:

```bash
SUBMISSION_CODE="$(cat /tmp/student_submission.py)" \
    python -m grader_support.entrypoint \
    /path/to/my-course/unit-2/exercise-3/grader.py \
    42
```

The second argument (`42`) is the random seed; any integer works for testing.

The output is a JSON object:

```json
{
  "errors": [],
  "tests": [
    ["Test: add 2 3", "", true, "5\n", "5\n"]
  ],
  "correct": true,
  "score": 1.0
}
```

Each entry in `tests` is `[short_description, long_description, correct, expected_output, actual_output]`.

**Debugging:** set `GRADER_DEBUG=1` to see step-by-step trace output on stderr:

```bash
GRADER_DEBUG=1 SUBMISSION_CODE="def add(a,b): return a+b" \
    python -m grader_support.entrypoint \
    /path/to/exercise-3/grader.py 42
```

**Testing an incorrect submission:**

```bash
SUBMISSION_CODE="def add(a, b): return a - b" \
    python -m grader_support.entrypoint \
    /path/to/exercise-3/grader.py 42
```

```json
{
  "errors": [],
  "tests": [
    ["Test: add 2 3", "", false, "5\n", "-1\n"]
  ],
  "correct": false,
  "score": 0.0
}
```

**Testing input validation errors:**

```bash
SUBMISSION_CODE="x = 1" \
    python -m grader_support.entrypoint \
    /path/to/exercise-3/grader.py 42
```

If the `grader.py` requires a function definition the output will contain an `errors`
entry instead of running any tests.


### Option 2: Run inside a grader container (Docker)

If your grader image has dependencies beyond the standard library, or you want to test
exactly the environment that runs in production, build and run the grader container
locally.

**Step 1 — Build the base image** (once per xqueue-watcher checkout):

```bash
docker build \
    -f grader_support/Dockerfile.base \
    -t grader-base:local \
    .
```

**Step 2 — Write your course-specific `Dockerfile`**:

```dockerfile
FROM grader-base:local

# Copy your graders into the image
COPY my-course/ /grader/my-course/

# Install any course-specific Python dependencies
# COPY requirements.txt .
# RUN pip install -r requirements.txt
```

**Step 3 — Build your grader image**:

```bash
docker build -t my-course-grader:local .
```

**Step 4 — Run a grading job**:

```bash
docker run --rm \
    -e SUBMISSION_CODE="def add(a, b): return a + b" \
    my-course-grader:local \
    /grader/my-course/unit-2/exercise-3/grader.py 42
```

The output is the same JSON as Option 1.

**Debugging inside the container**:

```bash
docker run --rm -it \
    -e SUBMISSION_CODE="def add(a, b): return a + b" \
    -e GRADER_DEBUG=1 \
    my-course-grader:local \
    /grader/my-course/unit-2/exercise-3/grader.py 42
```

**Iterating on grader scripts without rebuilding**:

During development, bind-mount your grader directory so changes take effect immediately:

```bash
docker run --rm \
    -e SUBMISSION_CODE="def add(a, b): return a + b" \
    -v "$(pwd)/my-course:/grader/my-course:ro" \
    my-course-grader:local \
    /grader/my-course/unit-2/exercise-3/grader.py 42
```


### Option 3: Full local stack with docker-compose

Use the included `docker-compose.yml` to run a complete local environment (XQueue +
xqueue-watcher + a sample grader) and test the full submission flow end-to-end.

**Step 1 — Set the host-side grader root path**:

The Docker backend needs to know the absolute host-side path to your grader data (see
the [Operators Guide](operators.md#docker--docker-compose) for why):

```bash
export XQWATCHER_DOCKER_HOST_GRADER_ROOT="$(pwd)/data"
```

**Step 2 — Put your graders in `data/`**:

```
data/
└── unit-2/
    └── exercise-3/
        ├── grader.py
        └── answer.py
```

**Step 3 — Update `conf.d/600.json`** to point at your grader:

```json
{
    "test-123": {
        "SERVER_REF": "default",
        "CONNECTIONS": 1,
        "HANDLERS": [
            {
                "HANDLER": "xqueue_watcher.containergrader.ContainerGrader",
                "KWARGS": {
                    "grader_root": "/graders/",
                    "image": "grader-base:local",
                    "backend": "docker",
                    "timeout": 20
                }
            }
        ]
    }
}
```

**Step 4 — Start the stack**:

```bash
docker compose build   # builds grader-base:local
docker compose up
```

**Step 5 — Submit a test submission via the XQueue API**:

```bash
# Authenticate
curl -c /tmp/xqueue-cookie.txt \
    -X POST http://localhost:18040/xqueue/login/ \
    -d 'username=lms&password=password'

# Push a submission
curl -b /tmp/xqueue-cookie.txt \
    -X POST http://localhost:18040/xqueue/submit/ \
    -H 'Content-Type: application/json' \
    -d '{
        "xqueue_header": "{\"lms_callback_url\": \"http://host.docker.internal:8000/\", \"lms_key\": \"test\", \"queue_name\": \"test-123\"}",
        "xqueue_body": "{\"student_response\": \"def add(a,b): return a+b\", \"grader_payload\": \"{\\\"grader\\\": \\\"unit-2/exercise-3/grader.py\\\"}\"}"
    }'
```

xqueue-watcher will pick up the submission, run it through the grader container, and
return the result to XQueue.  Watch the logs with:

```bash
docker compose logs -f xqueue-watcher
```


---

## Building a grader image

When deploying to Kubernetes, grader scripts and dependencies are baked into a
course-specific Docker image that extends `grader-base`.

A minimal course `Dockerfile`:

```dockerfile
FROM grader-base:local   # replace with your registry path in production

# Copy grader scripts
COPY my-course/ /grader/my-course/

# Install course-specific Python packages
RUN pip install --no-cache-dir numpy scipy
```

The `grader-base` image sets the entrypoint to `python -m grader_support.entrypoint`,
so no `CMD` or `ENTRYPOINT` override is needed.

The image is used as-is by `ContainerGrader`; the watcher passes the submission code
via the `SUBMISSION_CODE` environment variable and the path to `grader.py` as an
argument.

**Image tagging for production**: use digest-pinned references
(`registry.example.com/my-course-grader@sha256:…`) in your `conf.d` configuration, or
enable `poll_image_digest: true` so the watcher resolves the latest digest
automatically.


---

## Troubleshooting

**"There was a problem running the staff solution"**

This error means `answer.py` itself failed.  Run the grader locally with `GRADER_DEBUG=1`
and look for a Python traceback in stderr.  Common causes:
- `answer.py` has a syntax error or raises an exception.
- A required dependency is missing from the grader image.
- The `grader.py` test exercises functionality that `answer.py` does not implement.

**"We couldn't run your solution"**

The student submission raised an unhandled exception.  With `GRADER_DEBUG=1` you can
see the exception detail in stderr.  The student will see only a generic error message.

**"Something went wrong: different numbers of tests ran"**

The student submission caused a different number of tests to run than the staff answer.
This usually means the student submission crashed partway through.  Investigate with
`GRADER_DEBUG=1`.

**Grader times out**

The default timeout is 20 seconds.  Increase it in your conf.d `KWARGS`:

```json
"KWARGS": {
    "timeout": 60
}
```

For local Docker testing, timeouts are applied by the Docker backend the same way as
the Kubernetes backend.

**Import errors in grader.py or answer.py**

Ensure all required packages are installed in the grader image.  Test the image
interactively:

```bash
docker run --rm -it --entrypoint python my-course-grader:local
>>> import numpy   # verify the package is available
```

**Container exits immediately with no output**

Check that `SUBMISSION_CODE` is set and non-empty.  If `SUBMISSION_CODE` is empty the
entrypoint may produce an empty result or an error.  Run with `GRADER_DEBUG=1` to see
what the entrypoint received.
