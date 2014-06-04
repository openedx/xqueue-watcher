import time
import json
import logging
import requests
import threading
import multiprocessing

log = logging.getLogger(__name__)


class XQueueClient(object):
    def __init__(self, queue_name, xqueue_server='http://localhost:18040', xqueue_auth=('user', 'pass'),
                 http_basic_auth=None):
        super(XQueueClient, self).__init__()
        self.session = requests.session()
        self.xqueue_server = xqueue_server
        self.queue_name = queue_name
        self.handlers = []
        self.daemon = True
        self.username, self.password = xqueue_auth

        if http_basic_auth is not None:
            self.auth = requests.auth.HTTPBasicAuth(http_basic_auth)
        else:
            self.auth = None

        self.http_basic_auth = http_basic_auth
        self.running = True
        self.processing = False

    def __repr__(self):
        return '{}({})'.format(self.__class__.__name__, self.queue_name)

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

    def _request(self, method, uri, timeout=None, **kwargs):
        url = self.xqueue_server + uri
        r = None
        while not r:
            try:
                r = self.session.request(method, url, auth=self.http_basic_auth, timeout=timeout, **kwargs)
            except requests.exceptions.ConnectionError as e:
                log.error('Could not connect to server at %s in timeout=%r', url, timeout)
                return (False, e.message)
            if r.status_code != 302:
                return self._parse_response(r)
            else:
                if self._login():
                    r = None
                else:
                    return (False, "Could not log in")

    def _login(self):
        if self.username is None:
            return True
        url = self.xqueue_server + '/xqueue/login/'
        log.debug("Trying to login to {0} with user: {1} and pass {2}".format(url, self.username, self.password))
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
            get_params = {'queue_name': self.queue_name, 'block': 'true'}
            success, content = self._request('get', '/xqueue/get_submission/', params=get_params)
            if success:
                self.processing = True
                success = self._handle_submission(content)
            return success
        except requests.exceptions.Timeout:
            return True
        except Exception as e:
            log.exception(e.message)
            return True

    def run(self):
        """
        Run forever, processing items from the queue
        """
        if not self._login():
            log.error("Could not log in to Xqueue %s. Quitting" % self.queue_name)
            return False
        while self.running:
            if not self.process_one():
                time.sleep(1)
        return True


class XQueueClientThread(XQueueClient, threading.Thread):
    pass


class XQueueClientProcess(XQueueClient, multiprocessing.Process):
    pass
