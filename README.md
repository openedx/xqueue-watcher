xqueue_watcher
==========

This is an implementation of a polling [XQueue](https://github.com/edx/xqueue) client and grader.


Running
=======

`python -m xqueue_watcher -d [path to settings file]`


YAML configuration file
=======================
	CLIENTS:
	  - QUEUE_NAME: "test-123"
	    SERVER: "http://127.0.0.1:18040"
	    CONNECTIONS: 2
	    AUTH: ["lms", "password"]
	    HANDLERS:
	      - HANDLER: "xqueue_watcher.jailedgrader.JailedGrader"
	        KWARGS:
	          grader_root: "/edx/data/MITx-7.QBWx/graders/"


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

    CODEJAIL:
      name: "7qbwx"
      bin_path: "/path/to/venv/bin/python"
      user: "user2"

Then, `codejail_python` will automatically be added to the kwargs for your handler. You can then import codejail.jail_code and run `jail_code("python", code...)`. You can define multiple sandboxes and use them as in `jail_code("special-python", ...)`


Deploying With Docker
=====================
To deploy xqueue watcher using Docker:

* Create a local directory containing a configuration file called `config.yml`
* Run the container: `docker run -v /path/to/config/dir:/edx/etc/xqueue_watcher edxops/xqueue_watcher:latest`

To make grader code available in the container, map a data directory:
	`-v /path/to/graders:/edx/data/`
And set `grader_root` to `/edx/data` in your configuration file.
