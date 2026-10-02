"""tools/release-kit/update_dev.sh — the installed copy a developer actually runs.

Split from `test_release_kit.py` by concern rather than for length alone: that file is the release
FLOW (release.sh, its steps, its idempotency, its tap bump) and this one is the dev-machine
install — replace, un-quarantine, sign, and launch only on request. Nothing here runs a release, so
it needs none of the git/bare-remote fixture the release suite builds.
"""

import os
import subprocess
from pathlib import Path

from conftest import REPO_ROOT

UPDATE_DEV = REPO_ROOT / "tools" / "release-kit" / "update_dev.sh"

BUILD_CMD = (
    "mkdir -p stage/Demo.app/Contents/MacOS && "
    "printf '#!/bin/sh\\necho demo\\n' > stage/Demo.app/Contents/MacOS/Demo && "
    "chmod +x stage/Demo.app/Contents/MacOS/Demo && "
    "/usr/bin/xattr -w com.apple.quarantine '0081;test;test;' stage/Demo.app"
)


def run_update_dev(workdir: Path, bin_dir: Path | None, *args: str) -> subprocess.CompletedProcess:
    dest = workdir / "dest"
    env = dict(os.environ)
    env["GOH_DIR"] = str(REPO_ROOT)
    env["UPDATE_DEV_DEST"] = str(dest)
    env["APP_NAME"] = "Demo.app"
    env["BUILD_CMD"] = BUILD_CMD
    env["APP_PATH"] = "stage/Demo.app"
    if bin_dir:
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
    return subprocess.run(
        ["/bin/bash", str(UPDATE_DEV), *args],
        cwd=workdir,
        capture_output=True,
        text=True,
        env=env,
    )


class TestUpdateDev:
    def test_installs_and_clears_quarantine(self, tmp_path):
        r = run_update_dev(tmp_path, None)
        assert r.returncode == 0, r.stdout + r.stderr
        installed = tmp_path / "dest" / "Demo.app"
        binary = installed / "Contents" / "MacOS" / "Demo"
        assert binary.is_file()
        probe = subprocess.run(
            ["/usr/bin/xattr", "-p", "com.apple.quarantine", str(installed)],
            capture_output=True,
            text=True,
        )
        assert probe.returncode != 0, "quarantine survived the install"
        assert "quarantine clear" in r.stdout

    def test_launch_flag_opens_installed_copy(self, tmp_path):
        bin_dir = tmp_path / "fakeopen"
        bin_dir.mkdir()
        (bin_dir / "open").write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$OPEN_LOG"\n')
        (bin_dir / "open").chmod(0o755)
        os.environ["OPEN_LOG"] = str(tmp_path / "open.log")
        r = run_update_dev(tmp_path, bin_dir, "--launch")
        assert r.returncode == 0, r.stdout + r.stderr
        assert (tmp_path / "open.log").read_text().startswith(str(tmp_path / "dest"))

    def test_default_does_not_launch(self, tmp_path):
        bin_dir = tmp_path / "fakeopen"
        bin_dir.mkdir()
        (bin_dir / "open").write_text("#!/usr/bin/env bash\nexit 97\n")
        (bin_dir / "open").chmod(0o755)
        r = run_update_dev(tmp_path, bin_dir)
        assert r.returncode == 0, r.stdout + r.stderr
