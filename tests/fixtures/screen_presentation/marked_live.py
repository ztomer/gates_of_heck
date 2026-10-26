import subprocess

# screen-ok: attended QA sweep, refuses under GOH_HEADLESS via lib/headless_env.sh
subprocess.run(
    ["osascript", "-e", 'tell app "Finder" to activate'], stdin=subprocess.DEVNULL, check=False
)
