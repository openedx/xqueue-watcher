xqueue_watcher
==========

This is an implementation of a polling [XQueue](https://github.com/edx/xqueue) client and grader.


Running
=======

`python -m xqueue_watcher -s [settings module]`  
or  
`python -m xqueue_watcher -f [settings json file]`


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
						"grader_root": "../data/6.00x/graders/",
						"gradepy": "../data/6.00x/graders/grade.py"
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
There are two ways of implementing a pull grader.

1. *Recommended*: subclass xqueue_watcher.grader.Grader and override the `grade` method. Then add your grader to the config like `"handler": "my_module.MyGrader"`. The arguments for the `grade` method are:
	* `grader_path`: absolute path to the grader defined for the current problem
	* `grader_config`: other configuration particular to the problem
	* `student_response`: student-supplied code
	* `sandbox`: an optional module for handling sandboxed execution (*deprecated, see below*)  

2. create a module containing a `grade` function with the signature described above and set the path to the module in the `gradepy` and `grader_root` kwargs of the handler.


Sandboxing
==========
The recommended way to sandbox python is by using [CodeJail](https://github.com/edx/codejail). Create a json file like this:

	{
		"python": {
			"python_bin": "/path/to/sandbox/python",
			"user": "sandbox_username"
		}
	}

And add `-j path/to/config.json` on the xqueue_watcher command. You can then import codejail.jail_code and run `jail_code("python", code...)`. You can define multiple sandboxes and use them as in `jail_code("special-python", ...)`

The old method of sandboxing is as follows:

The `grade` function (or method of Grader) receives a `sandbox` argument, which is either `None` (for no sandboxing configured) or an object containing two methods:

* `sandbox_cmd_list` returns a list of arguments pointing to the sandboxed python command, appropriate to pass to `subprocess.Popen`
* `record_suspicious_submission` logs suspicious code. pass it an arbitrary message and the string of code

In the configuration for your handler, add a `SANDBOX` key pointing to the path to the sandboxed python
