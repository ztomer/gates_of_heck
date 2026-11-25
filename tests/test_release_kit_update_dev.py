"""tools/release-kit/update_dev.sh — the installed copy a developer actually runs.

Split from `test_release_kit.py` by concern rather than for length alone: that file is the release
FLOW (release.sh, its steps, its idempotency, its tap bump) and this one is the dev-machine
install — replace, un-quarantine, sign, and launch only on request. Nothing here runs a release, so
it needs none of the git/bare-remote fixture the release suite builds.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pid_owner  # lib/, on the path since conftest imported load_kit
import pytest
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import GiveUp, patience

# The kit installs a macOS .app (ditto, /usr/bin/xattr's quarantine, codesign, osascript, open): on
# Linux its build step itself fails at /usr/bin/xattr (GOH.WORKERFAILS, the worker's 14 reds).
pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="update_dev installs a macOS .app bundle"
)

UPDATE_DEV = REPO_ROOT / "tools" / "release-kit" / "update_dev.sh"

BUILD_CMD = (
    "mkdir -p stage/Demo.app/Contents/MacOS && "
    "printf '#!/bin/sh\\necho demo\\n' > stage/Demo.app/Contents/MacOS/Demo && "
    "chmod +x stage/Demo.app/Contents/MacOS/Demo && "
    "/usr/bin/xattr -w com.apple.quarantine '0081;test;test;' stage/Demo.app"
)


def kit_env(workdir: Path, bin_dir: Path | None, build: str = BUILD_CMD, **extra: str) -> dict:
    env = hermetic_env()  # no caller GOH_* (GOH.LIVEROOTLEAK); GOH_DIR is this checkout
    env["UPDATE_DEV_DEST"] = str(workdir / "dest")
    env["APP_NAME"] = "Demo.app"
    env["BUILD_CMD"] = build
    env["APP_PATH"] = "stage/Demo.app"
    if bin_dir:
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        # The kit's poll waits are counted, never waited out (GOH.PYSLOW4): nothing here is timed.
        (bin_dir / "sleep").write_text(f'#!/bin/bash\necho "$1" >> "{workdir}/sleeps"\n')
        (bin_dir / "sleep").chmod(0o755)
    return {**env, **extra}


def run_update_dev(
    workdir: Path, bin_dir: Path | None, *args: str, **env: str
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/bash", str(UPDATE_DEV), *args],
        cwd=workdir,
        capture_output=True,
        text=True,
        env=kit_env(workdir, bin_dir, **env),
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
        # First install, and the real ditto and codesign on the staged name (GOH.DEVSWAP): the
        # signature made at Demo.new holds at Demo.app, and nothing is left beside it.
        assert sorted(os.listdir(tmp_path / "dest")) == ["Demo.app"]
        signed = subprocess.run(
            ["codesign", "-v", str(installed)],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        assert signed.returncode == 0, signed.stderr

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
        # the opened app never shows up: looked for 15 times, then said, never claimed
        assert (tmp_path / "sleeps").read_text().split() == ["0.2"] * 15
        assert "no Demo process showed up" in r.stdout + r.stderr, r.stdout + r.stderr

    def test_default_does_not_launch(self, tmp_path):
        bin_dir = tmp_path / "fakeopen"
        bin_dir.mkdir()
        (bin_dir / "open").write_text("#!/usr/bin/env bash\nexit 97\n")
        (bin_dir / "open").chmod(0o755)
        r = run_update_dev(tmp_path, bin_dir)
        assert r.returncode == 0, r.stdout + r.stderr

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
    and an app answers a reopen with its window (a private menu-bar app shows its 1240x960
    dashboard). The kit is the one launch path (RT.LAUNCHDASH, 2026-11-08: `install.sh --launch`
    opened the copy the kit had just relaunched, and the dashboard nobody had open appeared), so it
    never opens a running copy.
    """

    def test_a_copy_that_came_up_during_the_install_is_not_reopened(self, tmp_path):
        bin_dir = _world(tmp_path, running=False)
        # Something else (a login item, the agent's app-state restore) starts it once it is in.
        # Before the swap, a copy that came up is quit like any other (GOH.DEVSWAP).
        r = run_update_dev(
            tmp_path, bin_dir, "--launch", AFTER_INSTALL_CMD=f"touch '{tmp_path}/running'"
        )
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
        assert os.listdir(tmp_path / "dest") == [], "the staged copy was left behind"
        # ten looks after the graceful quit, ten after the signal
        assert (tmp_path / "sleeps").read_text().split() == ["0.3"] * 20


class TestInstallEverythingThenLaunch:
    """RT.INSTALLORDER (2026-11-08): `open` failed with -600 after the kit replaced a private
    menu-bar app, and a private Rust app's install.sh died before its CLI step -- app new, CLI old.
    The caller's other artifacts now land BEFORE the launch step (AFTER_INSTALL_CMD), and a failed
    `open` is retried once after lsregister, then reported by name."""

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

    @pytest.mark.load_sensitive
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

    @pytest.mark.load_sensitive
    def test_a_launch_that_fails_once_is_retried_after_lsregister(self, tmp_path):
        bin_dir = _world(tmp_path, running=True)
        self._failing_open(bin_dir, tmp_path, fails=1)
        r = run_update_dev(tmp_path, bin_dir)
        assert r.returncode == 0, r.stdout + r.stderr
        assert (tmp_path / "companion").exists() and (tmp_path / "running").exists()
        assert (tmp_path / "lsreg.log").exists()

    @pytest.mark.load_sensitive
    def test_a_failing_companion_install_launches_nothing(self, tmp_path):
        bin_dir = _world(tmp_path, running=True)
        os.environ["AFTER_INSTALL_CMD"] = "exit 9"
        r = run_update_dev(tmp_path, bin_dir)
        assert r.returncode != 0 and "companion install failed" in r.stdout + r.stderr
        assert _opened(tmp_path) == []


def _old_copy(tmp_path: Path) -> dict[str, bytes]:
    """An installed Demo.app the kit is to replace; returns its bytes by path."""
    exe = tmp_path / "dest" / "Demo.app" / "Contents" / "MacOS" / "Demo"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\necho old\n")
    return _bytes(tmp_path / "dest" / "Demo.app")


def _bytes(app: Path) -> dict[str, bytes]:
    return {str(f.relative_to(app)): f.read_bytes() for f in app.rglob("*") if f.is_file()}


def _fake(bin_dir: Path, name: str, body: str) -> None:
    (bin_dir / name).write_text(f"#!/bin/bash\n{body}\n")
    (bin_dir / name).chmod(0o755)


def _leftovers(tmp_path: Path) -> list[str]:
    return [n for n in os.listdir(tmp_path / "dest") if n != "Demo.app"]


class TestStageThenSwap:
    """GOH.DEVSWAP: the kit quit the app and removed the installed copy BEFORE copying, so a failed
    ditto left no app, nothing running, and the words "nothing installed". The new copy is staged
    and checked beside the old one; only then is the app quit and the copy swapped in by rename."""

    @pytest.mark.parametrize(
        ("ditto", "said"),
        [
            ('mkdir -p "$2/Contents"; echo partial > "$2/Contents/x"; exit 1', "could not copy"),
            ("exit 0", "differs from"),  # a no-op that succeeds: the read-back catches it (G16)
            ('mkdir -p "$2/Contents/MacOS"; echo stale > "$2/Contents/MacOS/Demo"', "differs from"),
        ],
        ids=["fails", "copies-nothing", "copies-something-else"],
    )
    def test_a_bad_copy_leaves_the_old_app_running_and_in_place(self, tmp_path, ditto, said):
        bin_dir = _world(tmp_path, running=True)
        old = _old_copy(tmp_path)
        _fake(bin_dir, "ditto", ditto)
        r = run_update_dev(tmp_path, bin_dir)
        assert r.returncode != 0, r.stdout + r.stderr
        assert said in r.stderr and "nothing installed" in r.stderr, r.stderr
        assert "installed" not in r.stdout, r.stdout
        assert (tmp_path / "running").exists(), "the app was quit for a copy that never landed"
        assert _bytes(tmp_path / "dest" / "Demo.app") == old
        assert _leftovers(tmp_path) == []

    def test_a_copy_that_will_not_quit_keeps_the_old_one_untouched(self, tmp_path):
        bin_dir = _world(tmp_path, running=True)
        old = _old_copy(tmp_path)
        for name in QUITTERS:
            _fake(bin_dir, name, "exit 0")
        r = run_update_dev(tmp_path, bin_dir)
        assert r.returncode != 0 and "would not quit" in r.stderr, r.stdout + r.stderr
        assert _bytes(tmp_path / "dest" / "Demo.app") == old and _leftovers(tmp_path) == []

    @pytest.mark.parametrize("which", ["second", "first"])
    def test_a_failed_rename_puts_the_old_copy_back_and_relaunches_it(self, tmp_path, which):
        bin_dir = _world(tmp_path, running=True)
        old = _old_copy(tmp_path)
        # rename(2) refused once: moving the old copy aside, or the new one in.
        src = "*/Demo.new" if which == "second" else "*/dest/Demo.app"
        _fake(
            bin_dir,
            "python",
            f'n=$#; case "${{@:n-1:1}}" in {src}) echo "planted: refused" >&2; exit 1 ;; esac\n'
            f'exec "{sys.executable}" "$@"',
        )
        py = str(bin_dir / "python")
        r = run_update_dev(tmp_path, bin_dir, "--launch", GOH_PY=py, GOH_PY_OK=py)
        assert r.returncode == 1, r.stdout + r.stderr
        assert "planted: refused" in r.stderr and "could not swap" in r.stderr, r.stderr
        assert "installed /" not in r.stdout, r.stdout
        assert _bytes(tmp_path / "dest" / "Demo.app") == old and _leftovers(tmp_path) == []
        # the old copy is put back as it was: running again (one `open` of it), and --launch
        # asked for the NEW copy, so it is not what is said
        assert _opened(tmp_path) == [str(tmp_path / "dest" / "Demo.app")]

    def test_signed_under_a_name_launchservices_does_not_register_and_never_after(self, tmp_path):
        """`lsregister -R` registers an .app even inside a hidden directory and refuses a .new
        (-10811, probed 2026-11-25): the copy is checked and signed as `<hidden dir>/Demo.new`, and
        nothing touches it once it is Demo.app."""
        bin_dir = _world(tmp_path, running=True)
        _old_copy(tmp_path)
        _fake(bin_dir, "codesign", f'printf "%s\\n" "${{@: -1}}" >> "{tmp_path}/signed"; exit 1')
        r = run_update_dev(tmp_path, bin_dir)
        assert r.returncode == 0, r.stdout + r.stderr
        signed = (tmp_path / "signed").read_text().splitlines()
        assert len(signed) == 2, signed  # asked whether valid, then ad-hoc signed
        for path in map(Path, signed):
            assert path.name == "Demo.new" and path.parent.name.startswith(".Demo.app.goh-devswap.")
            assert path.parent.parent == tmp_path / "dest"
        assert _leftovers(tmp_path) == []

    def test_a_bundle_its_owner_cannot_write_is_replaced_all_the_same(self, tmp_path):
        """A read-only top directory cannot be renamed into another directory (EACCES, probed on
        APFS 2026-11-25): asked before anything is quit, of the new copy and the old one."""
        build = f"chmod -R u+w stage 2>/dev/null; {BUILD_CMD} && chmod 555 stage/Demo.app"
        for _ in range(2):  # the second run replaces the first one's read-only copy
            r = run_update_dev(tmp_path, None, build=build)
            assert r.returncode == 0, r.stdout + r.stderr
            assert "installed" in r.stdout
        assert _leftovers(tmp_path) == []
        (tmp_path / "stage" / "Demo.app").chmod(0o755)  # the temp root is removable

    def test_a_killed_runs_leftovers_go_only_when_their_owner_is_dead(self, tmp_path):
        dead = subprocess.run(
            ["/bin/sh", "-c", "echo $$"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=True,
        )
        gone = tmp_path / "dest" / f".Demo.app.goh-devswap.{int(dead.stdout)}"
        live = tmp_path / "dest" / f".Demo.app.goh-devswap.{os.getpid()}"
        old = _old_copy(tmp_path)
        gone.mkdir()
        (tmp_path / "dest" / "Demo.app").rename(gone / "Demo.old")
        (live / "Demo.new").mkdir(parents=True)
        # killed between its two renames: no Demo.app, the old copy aside; this run's build fails
        r = run_update_dev(tmp_path, None, build="exit 3")
        assert r.returncode != 0 and "build failed" in r.stderr, r.stdout + r.stderr
        assert "the previous copy is back" in r.stdout + r.stderr
        assert _bytes(tmp_path / "dest" / "Demo.app") == old
        assert _leftovers(tmp_path) == [live.name], "a live owner's staging was taken"

    @pytest.mark.load_sensitive
    def test_a_term_mid_copy_leaves_no_staged_copy_and_no_copier(self, tmp_path):
        bin_dir = _world(tmp_path, running=True)
        old = _old_copy(tmp_path)
        # a copy that has begun and does not end: it says so, with its pid, and waits
        _fake(
            bin_dir,
            "ditto",
            f'mkdir -p "$2/Contents"; echo partial > "$2/Contents/x"\n'
            f'echo $$ > "{tmp_path}/copying"; exec /bin/sleep 600',
        )
        kit = subprocess.Popen(
            ["/bin/bash", str(UPDATE_DEV)],
            cwd=tmp_path,
            env=kit_env(tmp_path, bin_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        copier = None
        try:
            give_up = GiveUp(30)
            while not (tmp_path / "copying").exists() and not give_up.passed():
                time.sleep(0.05)
            # stamped while it is the kit's running ditto, so the check below reads THAT process
            copier = pid_owner.stamp(int((tmp_path / "copying").read_text()))
            kit.terminate()
            out, errs = kit.communicate(timeout=patience(30))
        finally:
            if kit.poll() is None:
                kit.kill()
                kit.wait()
            # a kit that failed to stop its copy leaves it orphaned: reaped here, by its stamp
            pid_owner.signal(copier, signal.SIGKILL)
        assert kit.returncode == 143, out + errs
        assert "interrupted before the swap -- nothing installed" in errs, errs
        # the copy was stopped, not left writing into a removed directory
        assert copier is not None and pid_owner.gone(copier)
        assert _leftovers(tmp_path) == []
        assert _bytes(tmp_path / "dest" / "Demo.app") == old
        assert (tmp_path / "running").exists()
