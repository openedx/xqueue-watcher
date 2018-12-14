from xqueue_watcher.grader import Grader
import subprocess
import time
import re


def run_as_subprocess(cmd, compiling=False, running_code=False, timeout=None):
    """
    runs the subprocess and execute the command. if timeout is given kills the
    process after the timeout period has passed. compiling and running code flags
    helps to return related message in exception
    """
    if timeout:
        cmd = 'timeout --signal=SIGKILL {0} {1}'.format(timeout, cmd)

    output, error = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True
    ).communicate()

    if error and compiling:
        raise Exception('Compilation error occurred.')
    elif error and running_code and 'Killed' in error:
        raise Exception('Time limit exceeded.')
    elif error and running_code:
        raise Exception('Runtime or Syntax error occurred.')

    return output


def respond_with_error(message):
    """
    returns error response with message
    """
    return {
        'correct': False,
        'score': 0,
        'errors': [message],
        'tests': []
    }


def execute_code(lang, code_file_name, code_full_file_name, code_file_path, input_file, timeout):
    """
    compiles the code, runs the code for python, java and c++ and returns output of the code
    """
    if lang == 'py':
        output = run_as_subprocess('python ' + code_full_file_name + input_file, running_code=True, timeout=timeout)

    elif lang == 'java':
        run_as_subprocess('javac ' + code_full_file_name, compiling=True)
        output = run_as_subprocess(
            'java -cp {0} {1}{2}'.format(TestGrader.TMP_DATA_DIR, code_file_name, input_file),
            running_code=True, timeout=timeout
        )

    elif lang == 'cpp':
        run_as_subprocess('g++ ' + code_full_file_name + ' -o ' + code_file_path, compiling=True)
        output = run_as_subprocess('./' + code_file_path + input_file, running_code=True, timeout=timeout)

    else:
        raise Exception

    return output


def detect_code_language(student_response, code_file_name):
    """
    detects language using guesslang module and raises exception if
    language is not in one of these. JAVA, C++, PYTHON. for java
    replaces the public class name with file name to execute the code.
    LIMIT: Expects only one public class in Java solution
    """
    output = run_as_subprocess("echo '" + student_response + "' | guesslang")

    if 'Python' in output:
        lang = "py"
    elif 'Java' in output:
        lang = 'java'
        student_response = re.sub(
            'public class (.*) {', 'public class {0} {{'.format(code_file_name), student_response
        )
    elif 'C++' in output:
        lang = 'cpp'
    else:
        raise Exception('Language can only be C++, Java or Python.')
    return lang, student_response


def write_code_file(student_response, full_code_file_name):
    """
    accepts code and file name to where the code will be written.
    """
    f = open(full_code_file_name, 'w')
    f.write(student_response)
    f.close()


def compare_outputs(actual_output, expected_output_file):
    """
    compares actual and expected output line by line after stripping
    any whitespaces at the ends. Raises Exception if outputs do not match
    otherwise returns response of correct answer
    """
    expected_output = open(expected_output_file, 'r').read().strip().split('\n')
    actual_output = actual_output.strip().split('\n')

    if actual_output != expected_output:
        raise Exception('Test cases failed.')
    else:
        return {
            'correct': True,
            'score': 1,
            'errors': [],
            'tests': []
        }


class TestGrader(Grader):
    SECRET_DATA_DIR = "test_grader/secret_data/"
    TMP_DATA_DIR = "test_grader/tmp_data/"

    def grade(self, grader_path, grader_config, student_response):

        # create input and output file name from problem name
        input_file_argument = ' {0}{1}.in'.format(self.SECRET_DATA_DIR, grader_config['problem_name'])
        expected_output_file = '{0}{1}.out'.format(self.SECRET_DATA_DIR, grader_config['problem_name'])

        code_file_name = "code_" + str(int(time.time()))
        code_file_path = TestGrader.TMP_DATA_DIR + code_file_name

        try:
            lang, student_response = detect_code_language(student_response, code_file_name)

            full_code_file_name = '{0}.{1}'.format(code_file_path, lang)
            write_code_file(student_response, full_code_file_name)

            output = execute_code(
                lang, code_file_name, full_code_file_name, code_file_path, input_file_argument, grader_config['timeout']
            )

            return compare_outputs(output, expected_output_file)

        except Exception as exc:
            return respond_with_error(exc.message)
