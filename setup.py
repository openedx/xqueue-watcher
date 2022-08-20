from setuptools import setup


setup(
    name='xqueue_watcher',
    version='0.2',
    description='XQueue Pull Grader',
    packages=[
        'grader_support',
        'xqueue_watcher',
    ],
    install_requires=open('requirements/production.txt',
                          'rt', encoding='utf-8').readlines(),
)
