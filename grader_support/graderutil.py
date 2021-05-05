"""
Utilities to help manage code execution and testing.
"""

import contextlib
import os, os.path
import shutil
import sys
import tempfile
import textwrap
import traceback

import io

# Set this variable to the language code you wish to use
# Default is 'en'. Dummy translations are served up in 'eo'.
# Put your translations in the file `graders/conf/locale/LANGUAGE/LC_MESSAGES/graders.mo`
LANGUAGE = 'en'


@contextlib.contextmanager
def captured_stdout():
    """
    A context manager to capture stdout into a StringIO.

        with captured_stdout() as stdout:
            # .. print stuff ..
        stdout.getvalue() # this is a string with what got printed.

    """
    old_stdout = sys.stdout
    sys.stdout = stdout = io.StringIO()

    try:
        yield stdout
    finally:
        sys.stdout = old_stdout


class ChangeDirectory:
    def __init__(self, new_dir):
        self.old_dir = os.getcwd()
        os.chdir(new_dir)

    def clean_up(self):
        os.chdir(self.old_dir)


@contextlib.contextmanager
def change_directory(new_dir):
    """
    A context manager to change the directory, and then change it back.
    """
    cd = ChangeDirectory(new_dir)
    try:
        yield new_dir
    finally:
        cd.clean_up()


class TempDirectory:
    def __init__(self, delete_when_done=True):
        self.delete_when_done = delete_when_done
        self.temp_dir = tempfile.mkdtemp(prefix="grader-")
        # Make directory readable by other users ('sandbox' user needs to be able to read it)
        os.chmod(self.temp_dir, 0o775)

    def clean_up(self):
        if self.delete_when_done:
            # if this errors, something is genuinely wrong, so don't ignore errors.
            shutil.rmtree(self.temp_dir)


@contextlib.contextmanager
def temp_directory(delete_when_done=True):
    """
    A context manager to make and use a temp directory.  If `delete_when_done`
    is true (the default), the directory will be removed when done.
    """
    tmp = TempDirectory(delete_when_done)
    try:
        yield tmp.temp_dir
    finally:
        tmp.clean_up()


class ModuleIsolation:
    """
    Manage changes to sys.modules so that we can roll back imported modules.

    Create this object, it will snapshot the currently imported modules. When
    you call `clean_up()`, it will delete any module imported since its creation.
    """

    def __init__(self):
        # Save all the names of all the imported modules.
        self.mods = set(sys.modules)

    def clean_up(self):
        # Get a list of modules that didn't exist when we were created
        new_mods = [m for m in sys.modules if m not in self.mods]
        # and delete them all so another import will run code for real again.
        for m in new_mods:
            del sys.modules[m]


@contextlib.contextmanager
def module_isolation():
    mi = ModuleIsolation()
    try:
        yield
    finally:
        mi.clean_up()


def make_file(filename, text=""):
    """Create a file.

    `filename` is the path to the file, including directories if desired,
    and `text` is the content.

    Returns the path to the file.

    """
    # Make sure the directories are available.
    dirs, __ = os.path.split(filename)
    if dirs and not os.path.exists(dirs):
        os.makedirs(dirs)

    # Create the file.
    with open(filename, 'wb') as f:
        f.write(textwrap.dedent(text))

    return filename


def format_exception(exc_info=None, main_file=None, hide_file=False):
    """
    Format an exception, defaulting to the currently-handled exception.
    `main_file` is the filename that should appear as the top-most frame,
    to hide the context in which the code was run.  If `hide_file` is true,
    then file names in the stack trace are made relative to the current
    directory.
    """
    exc_info = exc_info or sys.exc_info()
    exc_type, exc_value, exc_tb = exc_info
    if main_file:
        while exc_tb is not None and not frame_in_file(exc_tb.tb_frame, main_file):
            exc_tb = exc_tb.tb_next
    lines = traceback.format_exception(exc_type, exc_value, exc_tb)
    if hide_file:
        cwd = os.getcwd() + os.sep
        lines = [l.replace(cwd, "", 1) for l in lines]
    return "".join(lines)


def frame_in_file(frame, filename):
    """
    Does the traceback frame `frame` reference code in `filename`?
    """
    frame_file = frame.f_code.co_filename
    frame_stem = os.path.splitext(os.path.basename(frame_file))[0]
    filename_stem = os.path.splitext(filename)[0]
    return frame_stem == filename_stem
