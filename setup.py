from setuptools import setup

setup(
    name='xqueue_watcher',
    version='0.1',
    description='XQueue Pull Grader',
    packages=[
        'xqueue_watcher',
    ],
    install_requires=open('requirements.txt', 'rb').readlines()
)
