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
        stdin=subprocess.DEVNULL,
        check=False,
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
            stdin=subprocess.DEVNULL,
            check=False,
        )
        assert probe.returncode != 0, "quarantine survived the install"
        assert "quarantine clear" in r.stdout

    def test_launch_flag_opens_installed_copy(self, tmp_path):
        bin_dir = tmp_path / "fakeopen"
        bin_dir.mkdir()
        # The kit runs `open` with an EMPTY environment (GOH.TRANSIENTDIR): the log path is baked.
        log = tmp_path / "open.log"
        (bin_dir / "open").write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "{log}"\n')
        (bin_dir / "open").chmod(0o755)
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

    def test_ditto_that_copies_nothing_is_not_reported_installed(self, tmp_path):
        """The claim is read back (G15/G16): `ditto` exiting 0 is not proof the copy is there."""
        bin_dir = tmp_path / "fakeditto"
        bin_dir.mkdir()
        (bin_dir / "ditto").write_text("#!/usr/bin/env bash\nexit 0\n")  # a no-op that succeeds
        (bin_dir / "ditto").chmod(0o755)
        r = run_update_dev(tmp_path, bin_dir)
        assert r.returncode != 0, r.stdout + r.stderr
        assert "installed" not in r.stdout, r.stdout
        assert "differs from" in r.stdout + r.stderr

    def test_ditto_that_copies_something_else_is_not_reported_installed(self, tmp_path):
        bin_dir = tmp_path / "fakeditto"
        bin_dir.mkdir()
        (bin_dir / "ditto").write_text(
            '#!/usr/bin/env bash\nmkdir -p "$2/Contents/MacOS"\necho stale > "$2/Contents/MacOS/Demo"\n'
        )
        (bin_dir / "ditto").chmod(0o755)
        r = run_update_dev(tmp_path, bin_dir)
        assert r.returncode != 0, r.stdout + r.stderr
        assert "installed" not in r.stdout, r.stdout

    def test_a_real_install_reports_installed_after_the_read_back(self, tmp_path):
        r = run_update_dev(tmp_path, None)
        assert r.returncode == 0, r.stdout + r.stderr
        assert f"installed {tmp_path / 'dest' / 'Demo.app'}" in r.stdout


# A world in files for the running copy (GOH.APPSTATE): `pgrep` answers from a marker, the graceful
# quit and `pkill` remove it, `open` logs and puts it back. Nothing real is quit or launched.
FAKES = {
    "pgrep": '[ -e "$STATE/running" ]\n',
    "osascript": 'rm -f "$STATE/running"\n',
    "open": 'printf "%s\\n" "$*" >> "$STATE/open.log"; touch "$STATE/running"\n',
}


FAKES["pkill"] = FAKES["osascript"]  # a fake on PATH: removes the marker, kills nothing


QUITTERS = [name for name in FAKES if name not in ("pgrep", "open")]


def _world(tmp_path: Path, running: bool) -> Path:
    bin_dir = tmp_path / "fakes"
    bin_dir.mkdir()
    for name, body in FAKES.items():
        # STATE baked in: the kit runs `open` with an empty environment (GOH.TRANSIENTDIR).
        (bin_dir / name).write_text(f"#!/usr/bin/env bash\nSTATE='{tmp_path}'\n" + body)
        (bin_dir / name).chmod(0o755)
    os.environ["STATE"] = str(tmp_path)
    if running:
        (tmp_path / "running").write_text("")
    return bin_dir


def _opened(tmp_path: Path) -> list[str]:
    log = tmp_path / "open.log"
    return log.read_text().splitlines() if log.exists() else []


class TestPutBackAsItWas:
    def test_a_copy_that_was_running_is_relaunched_after_the_install(self, tmp_path):
        r = run_update_dev(tmp_path, _world(tmp_path, running=True))
        assert r.returncode == 0, r.stdout + r.stderr
        assert _opened(tmp_path) == [str(tmp_path / "dest" / "Demo.app")]
        assert (tmp_path / "running").exists() and "Demo is running" in r.stdout

    def test_a_copy_that_was_not_running_stays_stopped(self, tmp_path):
        r = run_update_dev(tmp_path, _world(tmp_path, running=False))
        assert r.returncode == 0, r.stdout + r.stderr
        assert _opened(tmp_path) == []

    def test_no_launch_keeps_a_quit_copy_stopped_and_says_so(self, tmp_path):
        r = run_update_dev(tmp_path, _world(tmp_path, running=True), "--no-launch")
        assert r.returncode == 0, r.stdout + r.stderr
        assert _opened(tmp_path) == [] and not (tmp_path / "running").exists()
        assert "STOPPED" in r.stdout + r.stderr

    def test_launch_still_starts_a_copy_that_was_not_running(self, tmp_path):
        r = run_update_dev(tmp_path, _world(tmp_path, running=False), "--launch")
        assert r.returncode == 0, r.stdout + r.stderr
        assert len(_opened(tmp_path)) == 1

    def test_launch_and_no_launch_together_are_refused(self, tmp_path):
        r = run_update_dev(tmp_path, _world(tmp_path, running=False), "--launch", "--no-launch")
        assert r.returncode != 0 and "contradict" in r.stdout + r.stderr


class TestNeverAReopen:
    """`open` on a copy that is ALREADY running is not a launch: macOS sends that app a reopen event,
    and an app answers a reopen with its window (Routines.app shows its 1240x960 dashboard). The kit
    is the one launch path (RT.LAUNCHDASH, 2026-11-08: `install.sh --launch` opened the copy the kit
    had just relaunched, and the dashboard nobody had open appeared), so it never opens a running copy.
    """

    def test_a_copy_that_came_up_during_the_install_is_not_reopened(self, tmp_path):
        bin_dir = _world(tmp_path, running=False)
        # Something else (a login item, the agent's app-state restore) starts it mid-install.
        (bin_dir / "codesign").write_text('#!/usr/bin/env bash\ntouch "$STATE/running"\nexit 0\n')
        (bin_dir / "codesign").chmod(0o755)
        r = run_update_dev(tmp_path, bin_dir, "--launch")
        assert r.returncode == 0, r.stdout + r.stderr
        assert _opened(tmp_path) == [], "a running copy was opened again: that is a reopen"
        assert "already running" in r.stdout

    def test_a_copy_that_will_not_quit_is_neither_replaced_nor_reopened(self, tmp_path):
        bin_dir = _world(tmp_path, running=True)
        for name in QUITTERS:  # a copy that survives the graceful quit and the signal
            (bin_dir / name).write_text("#!/usr/bin/env bash\nexit 0\n")
            (bin_dir / name).chmod(0o755)
        r = run_update_dev(tmp_path, bin_dir)
        assert r.returncode != 0, r.stdout + r.stderr
        assert _opened(tmp_path) == []
        assert "would not quit" in r.stdout + r.stderr
        assert not (tmp_path / "dest" / "Demo.app").exists(), "replaced under a live copy"


class TestInstallEverythingThenLaunch:
    """RT.INSTALLORDER (2026-11-08): `open` failed with -600 after the kit replaced Routines.app, and
    routines' install.sh died before its CLI step -- app new, CLI old. The caller's other artifacts
    now land BEFORE the launch step (AFTER_INSTALL_CMD), and a failed `open` is retried once after
    lsregister, then reported by name."""

    def _failing_open(self, bin_dir: Path, tmp_path: Path, fails: int) -> None:
        (bin_dir / "open").write_text(
            f"#!/usr/bin/env bash\nSTATE='{tmp_path}'\n"
            'n=$(cat "$STATE/opens" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$STATE/opens"\n'
            f'[ "$n" -le {fails} ] && exit 1\n'
            'touch "$STATE/running"\n'
        )
        lsreg = bin_dir / "lsregister"
        lsreg.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$STATE/lsreg.log"\n')
        lsreg.chmod(0o755)
        os.environ["LSREGISTER"] = str(lsreg)
        os.environ["AFTER_INSTALL_CMD"] = (
            f'test -d "{tmp_path}/dest/Demo.app" && touch "{tmp_path}/companion"'
        )

    def teardown_method(self, _m):
        os.environ.pop("LSREGISTER", None)
        os.environ.pop("AFTER_INSTALL_CMD", None)

    def test_a_launch_that_fails_twice_is_named_and_leaves_everything_installed(self, tmp_path):
        bin_dir = _world(tmp_path, running=True)
        self._failing_open(bin_dir, tmp_path, fails=2)
        r = run_update_dev(tmp_path, bin_dir)
        assert r.returncode == 4, r.stdout + r.stderr
        assert "relaunch failed" in r.stdout + r.stderr
        assert (tmp_path / "companion").exists(), "the companion artifact was not installed first"
        assert (
            tmp_path / "lsreg.log"
        ).read_text().strip() == f"-f {tmp_path / 'dest' / 'Demo.app'}"
        assert (tmp_path / "opens").read_text().strip() == "2"

    def test_a_launch_that_fails_once_is_retried_after_lsregister(self, tmp_path):
        bin_dir = _world(tmp_path, running=True)
        self._failing_open(bin_dir, tmp_path, fails=1)
        r = run_update_dev(tmp_path, bin_dir)
        assert r.returncode == 0, r.stdout + r.stderr
        assert (tmp_path / "companion").exists() and (tmp_path / "running").exists()
        assert (tmp_path / "lsreg.log").exists()

    def test_a_failing_companion_install_launches_nothing(self, tmp_path):
        bin_dir = _world(tmp_path, running=True)
        os.environ["AFTER_INSTALL_CMD"] = "exit 9"
        r = run_update_dev(tmp_path, bin_dir)
        assert r.returncode != 0 and "companion install failed" in r.stdout + r.stderr
        assert _opened(tmp_path) == []
