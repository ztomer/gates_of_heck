"""lib/sleep_mark.sh off macOS (GOH.WORKERFAILS): Linux has no kern.sleeptime, and its procps
`sysctl` exits non-zero on the name. Under the callers' `set -eo pipefail` the failing pipeline made
`slept_mark="$(goh_sleep_mark)"` the round's exit status: gates/round.sh stopped with 1 and no word
after taking its gate slot (tests/test_round.py, on the Linux worker). The mark is blank there."""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env


def test_a_sysctl_without_the_counter_gives_a_blank_mark_under_pipefail(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "sysctl").write_text(
        "#!/bin/sh\necho 'sysctl: cannot stat /proc/sys/kern/sleeptime' >&2\nexit 255\n"
    )
    (bin_dir / "sysctl").chmod(0o755)
    script = (
        f"set -euo pipefail; warn() {{ :; }}; . '{REPO_ROOT}/lib/sleep_mark.sh'; "
        'm="$(goh_sleep_mark)"; echo "mark=[$m]"; goh_slept_since "$m" || echo "no sleep named"'
    )
    env = hermetic_env()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env.pop("GOH_SLEEPTIME_FILE", None)
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.splitlines() == ["mark=[]", "no sleep named"], r.stdout
