"""
Implementation of a grader compatible with XServer
"""
import html
import time
import json
from pathlib import Path
import logging
import multiprocessing

from . import metrics as _metrics


def format_errors(errors):
    esc = html.escape
    error_string = ''
    error_list = [esc(e) for e in errors or []]
    if error_list:
        items = '\n'.join([f'<li><pre>{e}</pre></li>\n' for e in error_list])
        error_string = f'<ul>\n{items}</ul>\n'
        error_string = f'<div class="result-errors">{error_string}</div>'
    return error_string


def to_dict(result):
    # long description may or may not be provided.  If not, don't display it.
    # TODO: replace with mako template
    esc = html.escape
    if result[1]:
        long_desc = '<p>{}</p>'.format(esc(result[1]))
    else:
        long_desc = ''
    return {'short-description': esc(result[0]),
            'long-description': long_desc,
            'correct': result[2],   # Boolean; don't escape.
            'expected-output': esc(result[3]),
            'actual-output': esc(result[4])
            }


class Grader:
    results_template = """
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

    results_correct_template = """
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

    results_incorrect_template = """
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

    def __init__(self, grader_root='/tmp/', fork_per_item=True, logger_name=__name__):
        """
        grader_root = root path to graders
        fork_per_item = fork a process for every request
        logger_name = name of logger
        """
        self.log = logging.getLogger(logger_name)
        self.grader_root = Path(grader_root)

        self.fork_per_item = fork_per_item

    def __call__(self, content):
        if self.fork_per_item:
            q = multiprocessing.Queue()
            proc = multiprocessing.Process(target=self.process_item, args=(content, q))
            proc.start()
            proc.join()
            reply = q.get_nowait()
            if isinstance(reply, Exception):
                raise reply
            else:
                return reply
        else:
            return self.process_item(content)

    def grade(self, grader_path, grader_config, student_response):
        raise NotImplementedError("no grader defined")

    def process_item(self, content, queue=None):
        try:
            _metrics.process_item_counter.add(1)
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
                _metrics.grader_payload_error_counter.add(1)

                self.log.debug(f"error parsing: '{payload}' -- {err}")
                raise

            self.log.debug(f"Processing submission, grader payload: {payload}")
            relative_grader_path = grader_config['grader']
            # Reject paths that contain ".." components before resolving to
            # avoid symlink edge-cases that could slip past the relative_to()
            # check below.  Absolute paths are still subject to that check.
            if '..' in Path(relative_grader_path).parts:
                raise ValueError(
                    f"Grader path {relative_grader_path!r} contains path traversal sequences."
                )
            grader_path = (self.grader_root / relative_grader_path).resolve()
            # Guard against path traversal: ensure the resolved path stays within grader_root.
            try:
                grader_path.relative_to(self.grader_root.resolve())
            except ValueError as exc:
                raise ValueError(
                    f"Grader path {relative_grader_path!r} resolves outside "
                    f"grader_root {self.grader_root!r}"
                ) from exc
            start = time.time()
            results = self.grade(grader_path, grader_config, student_response)

            elapsed = time.time() - start
            _metrics.grading_time_histogram.record(elapsed)
            self.log.debug('grading-time seconds=%.3f', elapsed)

            # Make valid JSON message
            reply = {'correct': results['correct'],
                     'score': results['score'],
                     'msg': self.render_results(results)}

            _metrics.replies_counter.add(1)
        except Exception as e:
            self.log.exception("process_item")
            if queue:
                queue.put(e)
            else:
                raise
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
