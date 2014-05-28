xqueue_watcher
==========

This is an implementation of a polling [XQueue](https://github.com/edx/xqueue) client and grader.


Running
=======

`python -m xqueue_watcher -d [path to settings directory]`


JSON configuration file
=======================
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

* `test-123`: the name of the queue
* `SERVER`: XQueue server address
* `AUTH`: list of username, password
* `CONNECTIONS`: how many threads to spawn to watch the queue
* `HANDLERS`: list of callables that will be called for each queue submission
	* `HANDLER`: callable name
	* `KWARGS`: optional keyword arguments to apply during instantiation


xqueue_watcher.grader.Grader
========================
To implement a pull grader:

Subclass xqueue_watcher.grader.Grader and override the `grade` method. Then add your grader to the config like `"handler": "my_module.MyGrader"`. The arguments for the `grade` method are:
	* `grader_path`: absolute path to the grader defined for the current problem
	* `grader_config`: other configuration particular to the problem
	* `student_response`: student-supplied code


Sandboxing
==========
To sandbox python, use [CodeJail](https://github.com/edx/codejail). In your handler configuration, add:

	"CODEJAIL": {
		"name": "python",
		"python_bin": "/path/to/sandbox/python",
		"user": "sandbox_username"
	}

Then, `codejail_python` will automatically be added to the kwargs for your handler. You can then import codejail.jail_code and run `jail_code("python", code...)`. You can define multiple sandboxes and use them as in `jail_code("special-python", ...)`
