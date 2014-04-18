# -*- coding: utf-8 -*-


def grade(grader_path, grader_config, student_response, sandbox):
    tests = []
    errors = []
    correct = 0
    score = 0
    if grader_path.endswith('/correct'):
        correct = 1
        score = 1
        tests.append(('short', 'long', True, 'expected', 'actual'))
        tests.append(('short', '', True, 'expected', 'actual'))
    elif grader_path.endswith('/incorrect'):
        tests.append(('short', 'long', False, 'expected', 'actual'))
        errors.append('THIS IS AN ERROR')
        errors.append(u'\x00\xc3\x83\xc3\xb8\x02')

    results = {
        'correct': correct,
        'score': score,
        'tests': tests,
        'errors': errors,
    }
    return results
