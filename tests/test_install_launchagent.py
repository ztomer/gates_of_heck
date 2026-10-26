"""tools/install_launchagent.sh: the one installer behind every repo-owned launchd job (G14).

The plist is COPIED into LaunchAgents (a plist only in the repo protects nobody), `check` compares
content and names drift, `remove` deletes. LAUNCHAGENTS_DIR redirects the copy and skips launchctl,
so nothing here touches the real launchd. Both jobs' caller scripts are pinned to their labels, and
the sweep's plist is pinned to what the owner asked for: daily, applying, logging where the
installer's proof reads it.
"""

import json
import shutil
import subprocess

import pytest
from conftest import REPO_ROOT

pytestmark = pytest.mark.skipif(shutil.which("plutil") is None, reason="plutil is macOS only")

TOOLS = REPO_ROOT / "tools"
JOBS = {
    "install_load_reap.sh": "com.ztomer.goh-load-reap",
    "install_actions_sweep.sh": "com.ztomer.goh-actions-sweep",
}


def _plist(label):
    """The plist as plutil reads it (the load-reap plist's comments hold a double hyphen, which
    plutil forgives and a strict XML parser does not)."""
    out = subprocess.run(
        [
            "plutil",
            "-convert",
            "json",
            "-o",
            "-",
            str(REPO_ROOT / "launchagents" / f"{label}.plist"),
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=True,
    )
    return json.loads(out.stdout)


def _run(script, mode, dest):
    return subprocess.run(
        ["/bin/bash", str(TOOLS / script), mode],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env={
            "PATH": "/usr/bin:/bin:/opt/homebrew/bin",
            "HOME": str(dest),
            "LAUNCHAGENTS_DIR": str(dest / "LA"),
        },
    )


@pytest.mark.parametrize(("script", "label"), JOBS.items())
def test_install_check_drift_remove(tmp_path, script, label):
    plist = tmp_path / "LA" / f"{label}.plist"
    assert _run(script, "check", tmp_path).returncode == 1  # not installed
    assert _run(script, "install", tmp_path).returncode == 0
    assert plist.read_bytes() == (REPO_ROOT / "launchagents" / f"{label}.plist").read_bytes()
    assert _run(script, "check", tmp_path).returncode == 0
    plist.write_text(plist.read_text().replace("<true/>", "<false/>", 1))
    drift = _run(script, "check", tmp_path)
    assert drift.returncode == 1 and "differs" in drift.stderr
    assert _run(script, "remove", tmp_path).returncode == 0
    assert not plist.exists()


@pytest.mark.parametrize("label", JOBS.values())
def test_every_job_plist_is_valid_and_labelled(label):
    data = _plist(label)
    assert data["Label"] == label
    assert (REPO_ROOT / "tools" / "install_launchagent.sh").exists()


def test_the_sweep_job_is_daily_applies_and_logs_where_the_proof_reads():
    data = _plist("com.ztomer.goh-actions-sweep")
    assert data["StartCalendarInterval"] == {"Hour": 4, "Minute": 15}
    assert data["RunAtLoad"] is True
    assert data["StandardOutPath"].endswith("/Library/Logs/goh-actions-sweep.log")
    assert data["StandardErrorPath"] == data["StandardOutPath"]
    assert "tools/actions_sweep.sh" in data["ProgramArguments"][2]
    wrapper = (TOOLS / "actions_sweep.sh").read_text()
    assert "--apply" in wrapper and "scripts/actions_sweep.py" in wrapper
