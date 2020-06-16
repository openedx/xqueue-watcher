from setuptools import setup

setup(
    name='xqueue_watcher',
    version='1.0',
    description='XQueue Pull Grader',
    packages=[
        'xqueue_watcher',
        'grader_support',
    ],
    install_requires=open('requirements/production.txt', 'rb').readlines()
)
