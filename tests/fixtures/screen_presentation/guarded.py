"""Launches the app for a capture, under the headless contract."""
import os
import subprocess

env = dict(os.environ)
env["GOH_HEADLESS"] = "1"
subprocess.run(["screencapture", "-x", "/tmp/a.png"], env=env)
