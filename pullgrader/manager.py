#!/usr/bin/env python

from __future__ import print_function
import sys
import time
import json
import signal
import logging
import importlib
import logging.config

from pullgrader.sandbox import Sandbox


class Manager(object):
    """
    Manages polling connections to XQueue.
    """
    def __init__(self):
        self.clients = []
        self.log = logging.getLogger('xserver.manager')
        self.poll_time = 10

    def client_from_config(self, queue_name, config):
        """
        Return an XQueueClient from the configuration object.
        """
        import xqueue_client

        klass = getattr(xqueue_client, config.get('class', 'XQueueClientThread'))
        client = klass(queue_name,
                       xqueue_server=config.get('server', 'http://localhost:18040'),
                       auth=config.get('auth', (None, None)))

        for handler_config in config.get('handlers', []):
            handler_name = handler_config['handler']
            mod_name, classname = handler_name.rsplit('.', 1)
            module = importlib.import_module(mod_name)

            kw = handler_config.get('kwargs', {})

            # HACK
            # Graders should use codejail instead of this other sandbox implementation
            sandbox_config = handler_config.get('sandbox')
            if sandbox_config:
                kw['sandbox'] = Sandbox(logging.getLogger('xserver.sandbox.{}'.format(queue_name)),
                                        python_path=sandbox_config,
                                        do_sandboxing=True)
            handler = getattr(module, classname)
            if kw:
                # handler could be a function or a class
                handler = handler(**kw)
            client.add_handler(handler)
        return client

    def configure(self, configuration):
        """
        Configure XQueue clients.
        """
        for queue_name, config in configuration.items():
            for i in range(config.get('connections', 1)):
                client = self.client_from_config(queue_name, config)
                self.clients.append(client)

    def start(self):
        """
        Start XQueue client threads (or processes).
        """
        for c in self.clients:
            self.log.info('Starting %r', c)
            c.start()

    def wait(self):
        """
        Monitor clients.
        """
        if not self.clients:
            return
        signal.signal(signal.SIGTERM, self.shutdown)
        while 1:
            for client in self.clients:
                if not client.is_alive():
                    self.log.error('Client died -> %r',
                                   client.queue_name)
                    self.shutdown()
                try:
                    time.sleep(self.poll_time)
                except KeyboardInterrupt:  # pragma: no cover
                    self.shutdown()

    def shutdown(self, *args):
        """
        Cleanly shutdown all clients.
        """
        self.log.info('shutting down')
        for client in self.clients:
            client.shutdown()
            if client.processing:
                client.join()
            self.log.info('%r done', client)
        self.log.info('done')
        sys.exit()


def main(args=None):
    import argparse
    parser = argparse.ArgumentParser(prog="pullgrader", description="Run grader from settings")
    parser.add_argument('-s', '--settings', help='settings module to load')
    parser.add_argument('-f', '--config', type=argparse.FileType('rb'), help='settings json file to load')
    parser.add_argument('-l', '--log-config', type=argparse.FileType('rb'), help='logger settings json file to load')

    args = parser.parse_args(args)

    if args.settings:
        settings = importlib.import_module(args.settings)
        logging.config.dictConfig(settings.LOGGING)
        config = settings.XQUEUES
    elif args.config:
        config = json.load(args.config)
    else:
        print("No configuration defined")
        return -1
    if args.log_config:
        logging.config.dictConfig(json.load(args.log_config))
    manager = Manager()
    manager.configure(config)
    manager.start()
    manager.wait()
    return 0
