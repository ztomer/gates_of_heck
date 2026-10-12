"""A gate run never writes the repository it gates (BACKLOG 6.11).

The games metarepo's `origin` read `https://elsewhere.invalid/x.git` from 2026-10-08 until it was
repaired by hand: a probe in that commit's own hook run wrote it into the REAL `.git/config`, and
nothing compared the config before and after. The push gate now snapshots the repository's config
and refs before the gate and reads them again after it (lib/repo_writes.sh): a config write refuses
the push naming the key, a moved ref is named and not refused (another session commits during a long
gate), and a token in a URL is redacted from the report.
"""

from pathlib import Path

from test_push_gate import GATE, _git, _repo
from test_push_gate_race import _push


def _gating(tmp_path: Path, line: str) -> Path:
    repo = _repo(tmp_path)
    (repo / "tools" / "gate.sh").write_text(
        GATE.replace("exit 0\n", f'git -C "$GOH_CROSS_REPO_ROOT" {line}\nexit 0\n')
    )
    _git(repo, "commit", "-q", "-am", "a gate that writes its repository")
    return repo


def test_a_gate_that_writes_the_repos_config_is_refused_and_named(tmp_path):
    repo = _gating(tmp_path, "config remote.origin.url https://elsewhere.invalid/x.git")
    r = _push(repo, tmp_path, "refs/heads/main")
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert (
        "own .git/config" in out and "remote.origin.url=https://elsewhere.invalid/x.git" in out
    ), out


def test_a_token_in_the_written_url_is_redacted(tmp_path):
    secret = "tok" + "en-value-12345"
    repo = _gating(tmp_path, f"config remote.origin.url https://{secret}@example.invalid/x.git")
    r = _push(repo, tmp_path, "refs/heads/main")
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert secret not in out and "https://***@example.invalid/x.git" in out, out


def test_a_moved_ref_is_named_not_refused(tmp_path):
    repo = _gating(tmp_path, "branch backup/flatten-records")
    r = _push(repo, tmp_path, "refs/heads/main")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "moved refs" in out and "refs/heads/backup/flatten-records" in out, out


def test_a_gate_that_writes_nothing_says_nothing(tmp_path):
    repo = _repo(tmp_path)
    r = _push(repo, tmp_path, "refs/heads/main")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "own .git/config" not in out and "moved refs" not in out, out
