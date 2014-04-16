from setuptools import setup

setup(
    name='pullgrader',
    version='0.1',
    description='XQueue Pull Grader',
    packages=[
        'pullgrader',
    ],
    py_modules=[
        'xqueue_client'
    ],
    install_requires=open('requirements.txt', 'rb').readlines()
)
