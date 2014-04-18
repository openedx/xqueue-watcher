import imp
import sys
import cgi
import time
import json
from path import path
import logging
import multiprocessing
from statsd import statsd


def printit(content):
    print repr(content)


def format_errors(errors):
    esc = cgi.escape
    error_string = ''
    error_list = [esc(e) for e in errors or []]
    if error_list:
        try:
            items = u'\n'.join([u'<li><pre>{0}</pre></li>\n'.format(e) for e in error_list])
            error_string = u'<ul>\n{0}</ul>\n'.format(items)
            error_string = u'<div class="result-errors">{0}</div>'.format(error_string)
        except UnicodeDecodeError:
            # See http://wiki.python.org/moin/UnicodeDecodeError; this error happens in the above unicode encoding
            # because it's assuming str `e` is in ascii encoding; when it is in Unicode already it gets sad.
            items = '\n'.join(['<li><pre>{0}</pre></li>\n'.format(e) for e in error_list])
            error_string = '<ul>\n{0}</ul>\n'.format(items)
            error_string = '<div class="result-errors">{0}</div>'.format(error_string)
    return error_string


def to_dict(result):
    # long description may or may not be provided.  If not, don't display it.
    # TODO: replace with mako template
    esc = cgi.escape
    if result[1]:
        long_desc = u'<p>{0}</p>'.format(esc(result[1]))
    else:
        long_desc = u''
    return {'short-description': esc(result[0]),
            'long-description': long_desc,
            'correct': result[2],   # Boolean; don't escape.
            'expected-output': esc(result[3]),
            'actual-output': esc(result[4])
            }


class Grader(object):
    results_template = u"""
<div class="test">
<header>Test results</header>
  <section>
    <div class="shortform">
    {status}
    </div>
    <div class="longform">
      {errors}
      {results}
    </div>
  </section>
</div>
"""

    results_correct_template = u"""
  <div class="result-output result-correct">
    <h4>{short-description}</h4>
    <pre>{long-description}</pre>
    <dl>
    <dt>Output:</dt>
    <dd class="result-actual-output">
       <pre>{actual-output}</pre>
       </dd>
    </dl>
  </div>
"""

    results_incorrect_template = u"""
  <div class="result-output result-incorrect">
    <h4>{short-description}</h4>
    <pre>{long-description}</pre>
    <dl>
    <dt>Your output:</dt>
    <dd class="result-actual-output"><pre>{actual-output}</pre></dd>
    <dt>Correct output:</dt>
    <dd><pre>{expected-output}</pre></dd>
    </dl>
  </div>
"""

    def __init__(self, grader_file=None, sandbox=None, grader_root='/tmp/', logger_name='xserver.grader'):
        self.log = logging.getLogger(logger_name)
        self.sandbox = sandbox
        self.grader_root = path(grader_root)
        self.grader_file = None

        if grader_file:
            grader_file = path(grader_file)
            if grader_file.exists():
                self.grader_file = grader_file
            else:
                raise Exception("%s does not exist" % grader_file)

    def __call__(self, content):
        q = multiprocessing.Queue()
        proc = multiprocessing.Process(target=self._handle_one, args=(content, q))
        proc.start()
        proc.join()
        reply = q.get_nowait()
        if isinstance(reply, Exception):
            raise reply
        else:
            return reply

    def grade(self, grader_path, grader_config, student_response, sandbox):
        raise NotImplementedError("no grader defined")

    def _handle_one(self, content, queue=None):
        try:
            statsd.increment('xserver.post-requests')
            body = content['xqueue_body']
            files = content['xqueue_files']

            # Delivery from the lms
            body = json.loads(body)
            student_response = body['student_response']
            payload = body['grader_payload']
            try:
                grader_config = json.loads(payload)
            except ValueError as err:
                # If parsing json fails, erroring is fine--something is wrong in the content.
                # However, for debugging, still want to see what the problem is
                statsd.increment('xserver.grader_payload_error')

                self.log.debug("error parsing: '{0}' -- {1}".format(payload, err))
                raise

            self.log.debug("Processing submission, grader payload: {0}".format(payload))
            relative_grader_path = grader_config['grader']
            grader_path = (self.grader_root / relative_grader_path).abspath()
            if self.grader_file:
                moddir = self.grader_file.dirname()
                if moddir not in sys.path:
                    sys.path.append(moddir)
                try:
                    grade = imp.load_source('grade', self.grader_file).grade
                except (SyntaxError, AttributeError):
                    self.log.error("grade function not found in %s", self.grader_file)
                    raise
            else:
                grade = self.grade
            start = time.time()
            results = grade(grader_path, grader_config, student_response, self.sandbox)

            statsd.histogram('xserver.grading-time', time.time() - start)

            # Make valid JSON message
            reply = {'correct': results['correct'],
                     'score': results['score'],
                     'msg': self.render_results(results)}

            statsd.increment('xserver.post-replies (non-exception)')
        except Exception as e:
            if queue:
                queue.put(e)
        else:
            if queue:
                queue.put(reply)
            return reply

    def render_results(self, results):
        output = []
        test_results = [to_dict(r) for r in results['tests']]
        for result in test_results:
            if result['correct']:
                template = self.results_correct_template
            else:
                template = self.results_incorrect_template
            output += template.format(**result)

        errors = format_errors(results['errors'])

        status = 'INCORRECT'
        if errors:
            status = 'ERROR'
        elif results['correct']:
            status = 'CORRECT'

        return self.results_template.format(status=status,
                                            errors=errors,
                                            results=''.join(output))
