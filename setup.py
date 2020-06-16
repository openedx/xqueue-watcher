from setuptools import setup

def load_requirements(*requirements_paths):
    """
    Load all requirements from the specified requirements files.

    Returns:
        list: Requirements file relative path strings
    """
    requirements = set()
    for path in requirements_paths:
        requirements.update(
            line.split('#')[0].strip() for line in open(path).readlines()
            if is_requirement(line.strip())
        )
    return list(requirements)


def is_requirement(line):
    """
    Return True if the requirement line is a package requirement.

    Returns:
        bool: True if the line is not blank, a comment, a URL, or
              an included file
    """
    return line and not line.startswith(('-r', '#', '-e', 'git+', '-c'))

setup(
    name='xqueue_watcher',
    version='1.0',
    description='XQueue Pull Grader',
    packages=[
        'xqueue_watcher',
        'grader_support',
    ],
    author='edX',
    url='https://github.com/edx/xqueue-watcher',
    include_package_data=True,
    zip_safe=False,
    install_requires=load_requirements('requirements/base.in'),
)
