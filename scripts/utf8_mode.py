"""
utf8_mode.py — run the plugin's scripts in Python's UTF-8 mode on Windows.

Windows Python reads and writes text files and the console in the local code
page (cp1252) unless UTF-8 mode is on, so a ticket title with an em dash or a
Mem note with "→" crashes a script with UnicodeEncodeError / UnicodeDecodeError.
Every entry script calls ensure() first thing under `if __name__ == "__main__"`:
on Windows without UTF-8 mode it reruns the same command with `-X utf8` and
exits with that run's code; anywhere else it does nothing.
"""

import os
import subprocess
import sys


def ensure():
    if os.name != "nt" or sys.flags.utf8_mode:
        return
    env = dict(os.environ, PYTHONUTF8="1")      # and any python the script starts
    sys.exit(subprocess.call([sys.executable, "-X", "utf8", *sys.argv], env=env))
