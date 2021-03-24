import flask
import itertools
import random
import time

app = flask.Flask(__name__)

counter = itertools.count()

SUBMISSIONS = [
('', 'ps03/derivatives/grade_derivatives.py'),
('import time;time.sleep(100)', 'ps03/derivatives/grade_derivatives.py'),
('''
def foo():
    return "hello"
''', 'ps02/bisect/grade_bisect.py'),
('''
deff bad():
    haha
''', 'ps02/bisect/grade_bisect.py'),
('''
monthlyInterestRate = annualInterestRate/12
lo = balance/12
hi = (balance*(1+monthlyInterestRate)**12)/12
done = False
while not done :
    payment = (lo+hi)/2
    nb = balance
    for m in range(0,12) :
        nb = (nb-payment)*(1+monthlyInterestRate)
    done = (abs(nb) < .005);
    if (nb > 0) :
        lo = payment
    else :
        hi = payment
print('Lowest Payment: %.2f' % payment)
''', 'ps02/bisect/grade_bisect.py'),
]

COUNTERS = {
    'requests': 0,
    'results': 0,
    'start': time.time()
}

@app.route('/start')
def start():
    COUNTERS['start'] = time.time()
    COUNTERS['requests'] = COUNTERS['results'] = 0
    return flask.jsonify(COUNTERS)

@app.route('/stats')
def stats():
    timediff = time.time() - COUNTERS['start']
    response = {
        'requests_per_second': COUNTERS['requests'] / timediff,
        'posts_per_second': COUNTERS['results'] / timediff
    }
    return flask.jsonify(response)


@app.route('/xqueue/get_submission/')
def get_submission():
    idx = random.randint(0, len(SUBMISSIONS) - 1)
    submission, grader = SUBMISSIONS[idx]
    payload = {
        'grader': grader
    }
    response = {
        'return_code': 0,
        'content': flask.json.dumps({
            'xqueue_header': '{}.{}'.format(next(counter), idx),
            'xqueue_body': flask.json.dumps({
                'student_response': submission,
                'grader_payload': flask.json.dumps(payload)
            }),
            'xqueue_files': ''
        })
    }
    COUNTERS['requests'] += 1
    return flask.jsonify(response)

@app.route('/xqueue/login/', methods=['POST'])
def login():
    return flask.jsonify({'return_code': 0})


@app.route('/xqueue/put_result/', methods=['POST'])
def put_result():
    COUNTERS['results'] += 1
    return flask.jsonify({'return_code': 0, 'content': 'thank you'})



if __name__ == '__main__':
    app.run(debug=True)
