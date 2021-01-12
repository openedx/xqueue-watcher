xqueue_watcher
==========

This is an implementation of a polling [XQueue](https://github.com/edx/xqueue) client and grader.

Overview
========

There are several components in a working XQueue Watcher service:
- **XQueue Watcher**: it polls an xqueue service continually for new submissions and grades them.
- **Submissions Handler**: when the watcher finds any new submission, it will be passed to the handler for grading. It is a generic handler that can be configured to work with different submissions through individual submission graders.
- **Individual Submission Grader**: each exercise or homework may specify its own "grader". This should map to a file on the server that usually specifies test cases or additional processing for the student submission.

Usually your server will look like this:
```
root/
├── xqueue-watcher/
│   ├── ... # xqueue-watcher repo, unchanged
│   └── ...
├── config/
│   └── conf.d/
│   │   └── my-course.json
│   └── logging.json
└── my-course/
   ├── exercise1/
   │   ├── grader.py  # - per-exercise grader
   │   └── answer.py  # - if using JailedGrader
   ├── ...
   └── exercise2/
       ├── grader.py
       └── answer.py
```
Running XQueue Watcher:
======================

Usually you can run XQueue Watcher without making any changes. You should keep course-specific in another folder like shown above, so that you can update xqueue_watcher anytime.

Install the requirements before running `xqueue_watcher`
```bash
cd xqueue-watcher/
make requirements
```

Now you're ready to run it.
```bash
python -m xqueue_watcher -d [path to the config directory, eg ../config]
```

The course configuration JSON file in `conf.d` should have the following structure:
```json
    {
        "test-123": {
            "SERVER": "http://127.0.0.1:18040",
            "CONNECTIONS": 1,
            "AUTH": ["lms", "lms"],
            "HANDLERS": [
                {
                    "HANDLER": "xqueue_watcher.grader.Grader",
                    "KWARGS": {
                        "grader_root": "/path/to/course/graders/",
                    }
                }
            ]
        }
    }
```

* `test-123`: the name of the queue
* `SERVER`: XQueue server address
* `AUTH`: list of username, password
* `CONNECTIONS`: how many threads to spawn to watch the queue
* `HANDLERS`: list of callables that will be called for each queue submission
   * `HANDLER`: callable name, see below for Submissions Handler
   * `KWARGS`: optional keyword arguments to apply during instantiation
      * `grader_root`: path to the course directory, eg /path/to/my-course

> TODO: document logging.json

Submissions Handler
===================

When xqueue_watcher detects any new submission, it will be passed to the submission handler for grading. It will instantiate a new handler based on the name configured above, with submission information retrieved
from XQueue. There is a base grader defined in xqueue_watcher: Grader and JailedGrader (for Python, using CodeJail). If you don't use JailedGrader, you'd have to implement your own Grader by subclassing `xqueue_watcher.grader.Grader

The payload from XQueue will be a JSON that usually looks like this, notice that "grader" is a required field in the "grader_payload" and must be configured accordingly in the Studio for the exercise.
```json
{
    "student_info": {
        "random_seed": 1,
        "submission_time": "20210109222647",
        "anonymous_student_id": "6d07814a4ece5cdda54af1558a6dfec0"
    },
    "grader_payload": "\n        {\"grader\": \"relative/path/to/grader.py\"}\n      ",
    "student_response": "print \"hello\"\r\n      "
}
```

## Custom Handler
To implement a pull grader:

Subclass `xqueue_watcher.grader.Grader` and override the `grade` method. Then add your grader to the config like `"handler": "my_module.MyGrader"`. The arguments for the `grade` method are:
   * `grader_path`: absolute path to the grader defined for the current problem.
   * `grader_config`: other configuration particular to the problem
   * `student_response`: student-supplied code

Note that `grader_path` is constructed by appending the relative path to the grader from `grader_payload` to the `grader_root` in the configuration JSON. If the handler cannot find a `grader.py` file, it would fail to grade the submission.

## Grading Python submissions with JailedGrader

`xqueue_watcher` provides a few utilities for grading python submissions, including JailedGrader for running python code in a safe environment and grading support utilities.

### JailedGrader
To sandbox python, use [CodeJail](https://github.com/edx/codejail). In your handler configuration, add:
```json
    "HANDLER": "xqueue_watcher.jailedgrader.JailedGrader",
    "CODEJAIL": {
        "name": "python",
        "python_bin": "/path/to/sandbox/python",
        "user": "sandbox_username"
    }
```
Then, `codejail_python` will automatically be added to the kwargs for your handler. You can then import codejail.jail_code and run `jail_code("python", code...)`. You can define multiple sandboxes and use them as in `jail_code("special-python", ...)`

To use JailedGrader, you also need to provide an `answer.py` file on the same folder with the `grader.py` file. The grader will run both student submission and `answer.py` and compare the output with each other.

### Grading Support utilities
There are several grading support utilities that make writing `grader.py` for python code easy. Check out
`grader_support/gradelib.py` for the documentation.

- `grader_support.gradelib.Grader`: a base class for creating a new submission grader. Not to be confused with `xqueue-watcher.grader.Grader`. You can add input checks, preprocessors and tests to a grader object.
- `grader_support.gradelib.Test`: a base class for creating tests for a submission. Usually a submission can be graded with one or a few tests. There are also few useful test functions and classes included, like `InvokeStudentFunctionTest` , `exec_wrapped_code`, etc.
- Preprocessors: utilities to process the raw submission before grading it. `wrap_in_string` is useful for testing code that is not wrapped in a function.
- Input checks: sanity checks before running a submission, eg check `required_string` or `prohibited_string`

Using the provided grader class, your `grader.py` would look something like this:
```python
from grader_support import gradelib
grader = gradelib.Grader()

# invoke student function foo with parameter []
grader.add_test(gradelib.InvokeStudentFunctionTest('foo', []))
```

Or with a pre-processor:
```python
import gradelib

grader = gradelib.Grader()

# execute a raw student code & capture stdout
grader.add_preprocessor(gradelib.wrap_in_string)
grader.add_test(gradelib.ExecWrappedStudentCodeTest({}, "basic test"))
```

You can also write your own test class, processor and input checks.