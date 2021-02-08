import time
import json
import logging
import requests
from requests.auth import HTTPBasicAuth
import threading
import multiprocessing
from .settings import MANAGER_CONFIG_DEFAULTS

log = logging.getLogger(__name__)


class XQueueClient:
    def __init__(self,
                 queue_name,
                 xqueue_server='http://localhost:18040',
                 xqueue_auth=('user', 'pass'),
                 http_basic_auth=MANAGER_CONFIG_DEFAULTS['HTTP_BASIC_AUTH'],
                 requests_timeout=MANAGER_CONFIG_DEFAULTS['REQUESTS_TIMEOUT'],
                 poll_interval=MANAGER_CONFIG_DEFAULTS['POLL_INTERVAL'],
                 login_poll_interval=MANAGER_CONFIG_DEFAULTS['LOGIN_POLL_INTERVAL'],
                 follow_client_redirects=MANAGER_CONFIG_DEFAULTS['FOLLOW_CLIENT_REDIRECTS']):
        super().__init__()
        self.session = requests.session()
        self.xqueue_server = xqueue_server
        self.queue_name = queue_name
        self.handlers = []
        self.daemon = True
        self.username, self.password = xqueue_auth
        self.requests_timeout = requests_timeout
        self.poll_interval = poll_interval
        self.login_poll_interval = login_poll_interval
        self.follow_client_redirects = follow_client_redirects

        if http_basic_auth is not None:
            self.http_basic_auth = HTTPBasicAuth(*http_basic_auth)
        else:
            self.http_basic_auth = None

        self.running = True
        self.processing = False

    def __repr__(self):
        return f'{self.__class__.__name__}({self.queue_name})'

    def _parse_response(self, response, is_reply=True):
        if response.status_code not in [200]:
            error_message = "Server %s returned status_code=%d" % (response.url, response.status_code)
            log.error(error_message)
            return False, error_message

        try:
            xreply = response.json()
        except ValueError:
            error_message = "Could not parse xreply."
            log.error(error_message)
            return False, error_message

        if 'return_code' in xreply:
            return_code = xreply['return_code'] == 0
            content = xreply['content']
        elif 'success' in xreply:
            return_code = xreply['success']
            content = xreply
        else:
            return False, "Cannot find a valid success or return code."

        if return_code not in [True, False]:
            return False, 'Invalid return code.'

        return return_code, content

    def _request(self, method, uri, **kwargs):
        url = self.xqueue_server + uri
        r = None
        while not r:
            try:
                r = self.session.request(
                    method,
                    url,
                    auth=self.http_basic_auth,
                    timeout=self.requests_timeout,
                    allow_redirects=self.follow_client_redirects,
                    **kwargs
                )
            except requests.exceptions.ConnectionError as e:
                log.error('Could not connect to server at %s in timeout=%r', url, self.requests_timeout)
                return (False, e)
            if r.status_code == 200:
                return self._parse_response(r)
            # Django can issue both a 302 to the login page and a
            # 301 if the original URL did not have a trailing / and
            # APPEND_SLASH is true in XQueue deployment, which is the default.
            elif r.status_code in (301, 302):
                if self._login():
                    r = None
                else:
                    return (False, "Could not log in")
            else:
                message = "Received un expected response status code, {}, calling {}.".format(
                    r.status_code,url)
                log.error(message)
                return (False, message)

    def _login(self):
        if self.username is None:
            return True
        url = self.xqueue_server + '/xqueue/login/'
        log.debug(f"Trying to login to {url} with user: {self.username} and pass {self.password}")
        response = self.session.request('post', url, auth=self.http_basic_auth, data={
            'username': self.username,
            'password': self.password,
            })
        if response.status_code != 200:
            log.error('Log in error %s %s', response.status_code, response.content)
            return False
        msg = response.json()
        log.debug("login response from %r: %r", url, msg)
        return msg['return_code'] == 0

    def shutdown(self):
        """
        Close connection and shutdown
        """
        self.running = False
        self.session.close()

    def add_handler(self, handler):
        """
        Add handler function to be called for every item in the queue
        """
        self.handlers.append(handler)

    def remove_handler(self, handler):
        """
        Remove handler function
        """
        self.handlers.remove(handler)

    def _handle_submission(self, content):
        content = json.loads(content)
        success = []
        for handler in self.handlers:
            result = handler(content)
            if result:
                reply = {'xqueue_body': json.dumps(result),
                         'xqueue_header': content['xqueue_header']}
                status, message = self._request('post', '/xqueue/put_result/', data=reply, verify=False)
                if not status:
                    log.error('Failure for %r -> %r', reply, message)
                success.append(status)
        return all(success)

    def process_one(self):
        try:
            self.processing = False
            get_params = {'queue_name': self.queue_name}
            success, content = self._request('get', '/xqueue/get_submission/', params=get_params)
            if success:
                self.processing = True
                success = self._handle_submission(content)
            return success
        except requests.exceptions.Timeout:
            return True
        except Exception as e:
            log.exception(e)
            return True

    def run(self):
        """
        Run forever, processing items from the queue
        """
        if not self._login():
            log.error("Could not log in to Xqueue %s. Retrying every 5 seconds..." % self.queue_name)
            num_tries = 1
            while self.running:
                num_tries += 1
                time.sleep(self.login_poll_interval)
                if not self._login():
                    log.error("Still could not log in to %s (%s:%s) tries: %d",
                        self.queue_name,
                        self.username,
                        self.password,
                        num_tries)
                else:
                    break
        while self.running:
            if not self.process_one():
                time.sleep(self.poll_interval)
        return True


class XQueueClientThread(XQueueClient, threading.Thread):
    pass


class XQueueClientProcess(XQueueClient, multiprocessing.Process):
    pass
