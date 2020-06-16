#!/usr/bin/env python
import getpass
import importlib
import inspect
import logging
import logging.config
from pathlib import Path
import signal
import sys
import time

import yaml

from codejail import jail_code

from .settings import MANAGER_CONFIG_DEFAULTS


class Manager:
    """
    Manages polling connections to XQueue.
    """
    def __init__(self):
        self.clients = []
        self.log = logging
        self.manager_config = MANAGER_CONFIG_DEFAULTS.copy()
        self.config_file = None
        self.last_configured = 0
        self.configured_log = False

    def client_from_config(self, watcher_config):
        """
        Return an XQueueClient from the configuration object.
        """
        from . import client

        klass = getattr(client, watcher_config.get('CLASS', 'XQueueClientThread'))
        watcher = klass(
            watcher_config['QUEUE_NAME'],
            xqueue_server=watcher_config.get('SERVER', 'http://localhost:18040'),
            xqueue_auth=watcher_config.get('AUTH', (None, None)),
            http_basic_auth=self.manager_config['HTTP_BASIC_AUTH'],
            requests_timeout=self.manager_config['REQUESTS_TIMEOUT'],
            poll_interval=self.manager_config['POLL_INTERVAL'],
            idle_poll_interval=self.manager_config['IDLE_POLL_INTERVAL'],
            login_poll_interval=self.manager_config['LOGIN_POLL_INTERVAL'],
        )

        for handler_config in watcher_config.get('HANDLERS', []):

            handler_name = handler_config['HANDLER']
            mod_name, classname = handler_name.rsplit('.', 1)
            module = importlib.import_module(mod_name)

            kw = handler_config.get('KWARGS', {})

            # codejail configuration per handler
            codejail_config = handler_config.get("CODEJAIL", None)
            if codejail_config:
                kw['codejail_python'] = self.enable_codejail(codejail_config)
            try:
                handler = getattr(module, classname)
            except AttributeError:
                if classname == 'urlencode' and mod_name == 'urllib':
                    module = importlib.import_module('urllib.parse')
                    handler = getattr(module, classname)
            if kw or inspect.isclass(handler):
                # handler could be a function or a class
                handler = handler(**kw)
            watcher.add_handler(handler)
        return watcher

    def configure(self, configuration):
        """
        Configure XQueue clients.
        """
        for queue_config in configuration.get('CLIENTS', []) or []:
            for i in range(queue_config.get('CONNECTIONS', 1)):
                watcher = self.client_from_config(queue_config)
                self.clients.append(watcher)

    def configure_from_file(self, config_file):
        """
        Configure manager from a yaml configuration file
        """
        self.config_file = Path(config_file)
        if self.did_config_change():
            if self.last_configured:
                self.log.info('config file %s changed.', self.config_file)
            with open(str(self.config_file), 'rb') as fp:
                config = yaml.full_load(fp) or {}

            if not self.configured_log:
                log_config = config.get('LOGGING')
                if log_config:
                    logging.config.dictConfig(config.get('LOGGING', {}))
                    self.configured_log = True
                else:
                    logging.basicConfig(level="DEBUG")
                self.log = logging.getLogger('xqueue_watcher.manager')
            self.manager_config = MANAGER_CONFIG_DEFAULTS.copy()
            self.manager_config.update(config.get('MANAGER', {}))

            self.configure(config)
            self.last_configured = self.config_file.stat().st_mtime
            return True
        else:
            return False

    def did_config_change(self):
        """
        Returns whether configuration file changed since last time it was loaded.
        """
        return self.config_file and self.config_file.stat().st_mtime > self.last_configured

    def enable_codejail(self, codejail_config):
        """
        Enable codejail for the process.
        codejail_config is a dict like this:
        {
            "name": "python",
            "bin_path": "/path/to/python",
            "user": "sandbox_username",
            "limits": {
                "CPU": 1,
                ...
            }
        }
        limits are optional
        user defaults to the current user
        """
        name = codejail_config["name"]
        bin_path = codejail_config['bin_path']
        user = codejail_config.get('user', getpass.getuser())
        jail_code.configure(name, bin_path, user=user)
        limits = codejail_config.get("limits", {})
        for name, value in limits.items():
            jail_code.set_limit(name, value)
        self.log.info("configured codejail -> %s %s %s", name, bin_path, user)
        return name

    def start(self):
        """
        Start XQueue client threads (or processes).
        """
        for c in self.clients:
            self.log.info('Starting %r', c)
            c.start()

    def wait(self, quit_if_empty=False):
        """
        Monitor clients.
        """
        signal.signal(signal.SIGTERM, self.shutdown)
        config_disappeared = False
        while 1:
            if not self.clients:
                self.log.warning('No clients configured in %s', self.config_file)
                if quit_if_empty:
                    return
            try:
                time.sleep(self.manager_config['POLL_TIME'])
            except KeyboardInterrupt:  # pragma: no cover
                self.shutdown()
            try:
                # check for config file changes
                if self.did_config_change():
                    self.restart()
                    config_disappeared = False
            except FileNotFoundError:
                if config_disappeared:
                    self.log.error('Config file %s disappeared. Exiting', self.config_file)
                    self.shutdown()
                else:
                    # in case the file was slow to move,
                    # give one more try through the loop before restarting
                    config_disappeared = True
                    self.log.error('Config file %s disappeared. Retrying', self.config_file)

            for client in self.clients:
                if not client.is_alive():
                    self.log.error('Client died -> %r',
                                   client.queue_name)
                    self.shutdown()

    def restart(self):
        self.shutdown(exit=False)
        self.log.info('Reloading config from %s', self.config_file)
        self.configure_from_file(self.config_file)
        self.start()

    def shutdown(self, *args, exit=True):
        """
        Cleanly shutdown all clients.
        """
        self.log.info('Shutting down')
        while self.clients:
            client = self.clients.pop()
            client.shutdown()
            if client.processing:
                try:
                    client.join()
                except RuntimeError:
                    self.log.exception("joining")
                    sys.exit(9)
            self.log.info('%r done', client)
        self.log.info('Done')
        if exit:
            sys.exit()


def main(args=None):
    import argparse
    parser = argparse.ArgumentParser(prog="xqueue_watcher", description="Run grader from settings")
    parser.add_argument('-f', '--config_file', required=True,
                        help='yaml file to use for all configuration ')
    parser.add_argument('-e', '--quit_if_empty', required=False, action='store_true', help='Quit if configuration is empty')
    args = parser.parse_args(args)

    manager = Manager()
    manager.configure_from_file(args.config_file)

    manager.start()
    manager.wait(quit_if_empty=args.quit_if_empty)
    return 0
