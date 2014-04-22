# Not django (for now), but use the same settings format anyway

import json
import os
from logsettings import get_logger_config
from path import path
import sys

ROOT_PATH = path(__file__).abspath().dirname()
REPO_PATH = ROOT_PATH.dirname()
ENV_ROOT = REPO_PATH.dirname()

# DEFAULTS

DEBUG = False


LOGGING = get_logger_config(ENV_ROOT / "log",
                            logging_env="dev",
                            local_loglevel="DEBUG",
                            dev_env=True,
                            debug=True)

XQUEUES = {
    'test-123': {
        'AUTH': ('lms', 'lms'),
        'CONNECTIONS': 2,
        'HANDLERS': [
            {
                'HANDLER': 'xqueue_watcher.grader.Grader',
                # 'sandbox': 'python',
                'KWARGS': {
                    'grader_file': ENV_ROOT / 'data/6.00x/graders/grade.py',
                    'grader_root': ENV_ROOT / 'data/6.00x/graders'
                }
            },
        ],
    }
}

# AWS

if os.path.isfile(ENV_ROOT / "env.json"):
    print "Opening env.json file"
    with open(ENV_ROOT / "env.json") as env_file:
        ENV_TOKENS = json.load(env_file)

    LOG_DIR = ENV_TOKENS['LOG_DIR']
    local_loglevel = ENV_TOKENS.get('LOCAL_LOGLEVEL', 'INFO')
    LOGGING = get_logger_config(LOG_DIR,
                                logging_env=ENV_TOKENS['LOGGING_ENV'],
                                local_loglevel=local_loglevel,
                                debug=False)

    XQUEUES = ENV_TOKENS.get('XQUEUES')
