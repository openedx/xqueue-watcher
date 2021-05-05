import threading
import subprocess
import os
import sys
import time
import requests
import json
import tempfile
import getpass
from path import Path
import pprint
import argparse


WATCHER_CONFIG = {
    "load-test": {
        "SERVER": 'undefined',
        "CONNECTIONS": 1,
        "HANDLERS": [
            {
                "HANDLER": "xqueue_watcher.jailedgrader.JailedGrader",
                "KWARGS": {
                    "grader_root": Path(__file__).dirname() / "../../data/6.00x/graders/",
                }
            }
        ]
    }
}

def start_mock_xqueue(port):
    cmd = 'gunicorn -w 1 -k gevent -b 0.0.0.0:%s mock_xqueue:app' % port
    print(cmd)
    proc = subprocess.Popen(cmd.split())
    return proc

def start_queue_watcher(config_file, codejail_config_file):
    cmd = f'python -m xqueue_watcher -f {config_file} -j {codejail_config_file}'
    print(cmd)
    proc = subprocess.Popen(cmd.split())
    return proc

def get_stats(server_address):
    pprint.pprint(requests.get('%s/stats' % server_address).json())
    print('\n')

def main(args):
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--concurrency', default=2, type=int, help='number of watchers')
    parser.add_argument('-x', '--xqueue', action="store_true", help='run mock xqueue', default=False)
    parser.add_argument('-w', '--watcher', action="store_true", help='run watcher', default=False)
    parser.add_argument('-a', '--xqueue-address', help='xqueue address', default='http://127.0.0.1:18042')
    args = parser.parse_args(args)

    if not (args.xqueue or args.watcher):
        parser.print_help()
        return -1

    if args.xqueue:
        port = args.xqueue_address.split(':')[-1]
        xqueue_proc = start_mock_xqueue(port)

        time.sleep(2)
    else:
        xqueue_proc = None

    if args.watcher:
        codejail_config = tempfile.NamedTemporaryFile(delete=False)
        json.dump({'python_bin': sys.executable, 'user': getpass.getuser()}, codejail_config)
        codejail_config.close()

        watcher_config = tempfile.NamedTemporaryFile(delete=False)
        WATCHER_CONFIG['load-test']['CONNECTIONS'] = args.concurrency
        WATCHER_CONFIG['load-test']['SERVER'] = args.xqueue_address
        json.dump(WATCHER_CONFIG, watcher_config)
        watcher_config.close()
        pprint.pprint(WATCHER_CONFIG)

        watcher_proc = start_queue_watcher(watcher_config.name, codejail_config.name)
        time.sleep(1)
        print(requests.get('%s/start' % args.xqueue_address).json())
    else:
        watcher_proc = None


    while watcher_proc or xqueue_proc:
        try:
            time.sleep(2)
            get_stats(args.xqueue_address)
        except KeyboardInterrupt:
            break

    if watcher_proc:
        os.kill(watcher_proc.pid, 15)
        codejail_config.unlink(codejail_config.name)
        watcher_config.unlink(watcher_config.name)

    if xqueue_proc:
        os.kill(xqueue_proc.pid, 15)

    print('\n\ndone')
    
if __name__ == '__main__':
    main(sys.argv[1:])