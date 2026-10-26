import subprocess

subprocess.run(["screencapture", "-x", "/tmp/a.png"], stdin=subprocess.DEVNULL, check=False)
