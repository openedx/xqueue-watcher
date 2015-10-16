from setuptools import setup

setup(
    name='xqueue_watcher',
    version='0.2',
    description='XQueue Pull Grader',
    packages=[
        'xqueue_watcher',
    ],
    install_requires=open('requirements/production.txt', 'rb').readlines()
)
