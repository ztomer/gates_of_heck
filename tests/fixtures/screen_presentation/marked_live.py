import subprocess

# screen-ok: attended QA sweep, refuses under GOH_HEADLESS via lib/headless_env.sh
subprocess.run(["osascript", "-e", 'tell application "Finder" to activate'])
