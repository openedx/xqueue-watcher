import os
import re

from setuptools import setup


setup(
    name='xqueue_watcher',
    version='1.0.0',
    description='XQueue Pull Grader',
    packages=[
        'grader_support',
        'xqueue_watcher',
    ],
    install_requires=open('requirements/production.txt',
                          'rt', encoding='utf-8').readlines(),
)
