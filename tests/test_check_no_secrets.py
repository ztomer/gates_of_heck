"""checks/check_no_secrets.py — fail on committed secrets.

One committed token dwarfs every other defect class these gates cover.
v1 scope is DELIBERATELY narrow (see module docstring): known high-
confidence prefixes + private-key headers. No entropy heuristics — FP
tuning is the work, and an unproven heuristic is worse than a narrow gate
that never cries wolf.

Every secret below is built by concatenation ("ghp_" + "A" * 36), never as
a literal — the suite polices itself through the same gate.
"""

import subprocess
from pathlib import Path

from conftest import REPO_ROOT, stage, write

CHECK = "checks/check_no_secrets.py"

GHP = "ghp_" + "A" * 36
AWS = "AKIA" + "0" * 16
KEY_HDR = "-----BEGIN " + "RSA PRIVATE KEY-----"
SLACK = "xoxb-" + "0" * 12


def run_secrets(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(REPO_ROOT / CHECK), *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def test_clean_tree_passes(repo):
    write(repo, "app.py", 'token = os.environ["APP_TOKEN"]\n')
    stage(repo, "app.py")
    r = run_secrets(repo, "--staged")
    assert r.returncode == 0, r.stdout + r.stderr


def test_github_token_fails_named(repo):
    write(repo, "cfg.py", f"TOKEN = {GHP!r}\n")
    stage(repo, "cfg.py")
    r = run_secrets(repo, "--staged")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "cfg.py:1" in (r.stdout + r.stderr)


def test_private_key_header_fails(repo):
    write(repo, "deploy/key.pem", KEY_HDR + "\n")
    stage(repo, "deploy/key.pem")
    r = run_secrets(repo, "--staged")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "key.pem" in (r.stdout + r.stderr)


def test_aws_and_slack_keys_fail(repo):
    write(repo, "a.py", f"x = {AWS!r}\n")
    write(repo, "b.py", f"y = {SLACK!r}\n")
    stage(repo, "a.py", "b.py")
    r = run_secrets(repo, "--staged")
    assert r.returncode == 1, r.stdout + r.stderr
    combined = r.stdout + r.stderr
    assert "a.py" in combined and "b.py" in combined


def test_staged_mode_reads_the_index(repo):
    write(repo, "leak.py", f"k = {GHP!r}\n")
    stage(repo, "leak.py")
    write(repo, "leak.py", "k = None\n")
    r = run_secrets(repo, "--staged")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "leak.py" in (r.stdout + r.stderr)


def test_secret_ok_marker_with_reason_suppresses(repo):
    write(repo, "fixture.py", f"k = {GHP!r}  # secret-ok: revoked test vector\n")
    stage(repo, "fixture.py")
    r = run_secrets(repo, "--staged")
    assert r.returncode == 0, r.stdout + r.stderr


def test_secret_ok_marker_without_reason_still_fails(repo):
    write(repo, "fixture.py", f"k = {GHP!r}  # secret-ok:\n")
    stage(repo, "fixture.py")
    r = run_secrets(repo, "--staged")
    assert r.returncode == 1, r.stdout + r.stderr


def test_exclude_skips_matching_paths(repo):
    write(repo, "vendor/leak.py", f"k = {GHP!r}\n")
    stage(repo, "vendor/leak.py")
    r = run_secrets(repo, "--staged", "--exclude", "vendor/")
    assert r.returncode == 0, r.stdout + r.stderr


def test_full_mode_scans_tracked_files(repo):
    write(repo, "leak.py", f"k = {GHP!r}\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-qm", "fixture"],
        check=True,
    )
    r = run_secrets(repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "leak.py" in (r.stdout + r.stderr)
