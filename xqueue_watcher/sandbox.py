class Sandbox:
    def __init__(self, logger, python_path='python', do_sandboxing=True):
        self.logger = logger
        self.do_sandboxing = do_sandboxing
        self.python_path = python_path

    def record_suspicious_submission(self, msg, code_str):
        """
        Record a suspicious submission:

        TODO: upload to edx-studentcode-suspicious bucket on S3.  For now, just
        logging to avoids need for more config changes (S3 credentials, python
        requirements).
        """
        self.logger.warning('Suspicious code: {0}, {1}'.format(msg, code_str))

    def sandbox_cmd_list(self):
        """
        Return a command to use to run a python script in a sandboxed env.

        NOTE: this is kind of ugly--we should really have all copy-to-tmp dir and
        run logic here too, but then we'd have to duplicate it for testing in the
        content repo.
        """
        if self.do_sandboxing:
            return ['sudo', '-u', 'sandbox', self.python_path]
        else:
            return [self.python_path]
