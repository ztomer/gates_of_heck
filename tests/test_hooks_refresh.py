"""`goh hooks` names every estate repo whose stock hooks are stale; `--refresh` brings them current
(GOH.HOOKREFRESH).

Stock hooks are COPIED, and structural.sh rightly refuses a commit run by an older stock copy. What
was missing was the other half: nothing refreshed them when goh moved. On 2026-11-12/13 four repos
(game_asset_factory, the skills repo, divoom-control, CadGoose) were each refused in turn and an
agent ran install.sh by hand in each. Now one listing names them all -- the daily estate audit
records it -- and one command refreshes them: a STALE hook only (byte-identical to a stock copy
goh once shipped, or to the one it recorded installing), never one a repo edited, committed as
its own commit of `.githooks` alone, and never in a repo the caller set aside.

The old copy is a real one: the stock pre-commit as committed at OLD_STOCK.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from _fast_git import fast_init
from _gatesrc_kit import write_gatesrc
from conftest import REPO_ROOT, hermetic_env, native_goh_path

OLD_STOCK = "18330c7"  # a retired stock pre-commit (retired_hooks.sha256)
SCRIPT = REPO_ROOT / "scripts" / "hooks_refresh.py"
STOCK = {p.name: p.read_bytes() for p in (REPO_ROOT / "hooks").iterdir() if p.is_file()}
GATE = '#!/usr/bin/env bash\nexec bash "$GOH_DIR/gates/structural.sh" "$@"\n'


def _old_pre_commit() -> bytes:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "cat-file", "-p", f"{OLD_STOCK}:hooks/pre-commit"],
        capture_output=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout


def _env() -> dict[str, str]:
    env = hermetic_env(drop_git=True)
    env["GOH_BIN"] = str(native_goh_path())
    env["GOH_SKIP_BUILD"] = "1"
    return env


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env=_env(),
        stdin=subprocess.DEVNULL,
    ).stdout


def _consumer(root: Path, name: str, pre_commit: bytes) -> Path:
    repo = root / name
    hooks = repo / ".githooks"
    hooks.mkdir(parents=True)
    for h, body in STOCK.items():
        (hooks / h).write_bytes(pre_commit if h == "pre-commit" else body)
        (hooks / h).chmod(0o755)
    write_gatesrc(repo, "GOH_MAX_LINES=500\n")
    (repo / "tools").mkdir()
    (repo / "tools" / "gate.sh").write_text(GATE)
    (repo / "tools" / "gate.sh").chmod(0o755)
    fast_init(repo)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "core.hooksPath", ".githooks")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "fixture")
    return repo


def _run(root: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = _env()
    env["GOH_ESTATE_STATE"] = str(tmp_path / "state" / "estate_audit.json")
    return subprocess.run(
        ["python3", str(SCRIPT), "--root", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=300,
        stdin=subprocess.DEVNULL,
    )


def _estate(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "estate"
    old = _old_pre_commit()
    return {
        "stale": _consumer(root, "stale", old),
        "edited": _consumer(root, "edited", old + b"# this repo's own line\n"),
        "current": _consumer(root, "current", STOCK["pre-commit"]),
        "kept": _consumer(root, "kept", old),
    }


def test_the_listing_names_every_stale_repo_and_only_those(tmp_path):
    e = _estate(tmp_path)
    r = _run(tmp_path / "estate", tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out  # a stale hook is a finding
    assert "stale: pre-commit" in out and "kept: pre-commit" in out, out
    assert "edited" not in out, out  # a hook the repo edited is its own business
    assert "current:" not in out, out
    # The listing writes nothing.
    assert (e["stale"] / ".githooks" / "pre-commit").read_bytes() == _old_pre_commit()


def test_refresh_brings_a_stale_hook_current_and_commits_it_alone(tmp_path):
    e = _estate(tmp_path)
    edited_before = (e["edited"] / ".githooks" / "pre-commit").read_bytes()
    r = _run(tmp_path / "estate", tmp_path, "--refresh", "--except", str(e["kept"]))
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    # The stale repo: current, committed, clean, and the commit carries .githooks only.
    assert (e["stale"] / ".githooks" / "pre-commit").read_bytes() == STOCK["pre-commit"], out
    assert _git(e["stale"], "status", "--porcelain") == "", out
    changed = _git(e["stale"], "show", "--name-only", "--format=", "HEAD").split()
    assert changed and all(p.startswith(".githooks/") for p in changed), changed
    # A hook the repo edited is never clobbered; a repo set aside is named, not touched.
    assert (e["edited"] / ".githooks" / "pre-commit").read_bytes() == edited_before
    assert (e["kept"] / ".githooks" / "pre-commit").read_bytes() == _old_pre_commit()
    assert "kept" in out and "set aside" in out, out
    # And the gate that refused the old copy now passes the refreshed one.
    g = subprocess.run(
        ["bash", str(REPO_ROOT / "gates" / "structural.sh"), "--staged"],
        cwd=e["stale"],
        env=_env(),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    assert "OLDER stock hook" not in g.stdout + g.stderr, g.stdout + g.stderr


def test_a_repo_with_uncommitted_hook_edits_is_not_refreshed(tmp_path):
    e = _estate(tmp_path)
    (e["stale"] / ".githooks" / "commit-msg").write_bytes(STOCK["commit-msg"] + b"# wip\n")
    r = _run(tmp_path / "estate", tmp_path, "--refresh", "--except", str(e["kept"]))
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "uncommitted" in out, out
    assert (e["stale"] / ".githooks" / "pre-commit").read_bytes() == _old_pre_commit()


def test_the_estate_audit_records_the_stale_hooks(tmp_path):
    e = _estate(tmp_path)
    state = tmp_path / "state" / "estate_audit.json"
    env = _env()
    env["GOH_ESTATE_STATE"] = str(state)
    r = subprocess.run(
        [
            "python3",
            str(REPO_ROOT / "scripts" / "estate_audit.py"),
            "--root",
            str(tmp_path / "estate"),
            "--no-fetch",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=300,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    recorded = json.loads(state.read_text())["stale_hooks"]
    assert recorded == {"kept": ["pre-commit"], "stale": ["pre-commit"]}, recorded
    assert str(e["stale"]) not in recorded  # named relative to the root, like every audit entry


def test_a_held_repo_is_set_aside_untouched(tmp_path):
    e = _estate(tmp_path)
    _git(e["stale"], "config", "goh.hold", "frozen for the test")
    r = _run(tmp_path / "estate", tmp_path, "--refresh", "--except", str(e["kept"]))
    out = r.stdout + r.stderr
    assert r.returncode == 1 and "on hold" in out, out
    assert (e["stale"] / ".githooks" / "pre-commit").read_bytes() == _old_pre_commit()


def test_a_refused_commit_leaves_the_repo_as_it_was(tmp_path):
    e = _estate(tmp_path)
    (e["stale"] / "tools" / "commit_msg.sh").write_text("#!/bin/bash\nexit 1\n")
    _git(e["stale"], "add", "-A")
    _git(e["stale"], "commit", "-q", "--no-verify", "-m", "a commit-msg rule that refuses all")
    head = _git(e["stale"], "rev-parse", "HEAD")
    r = _run(tmp_path / "estate", tmp_path, "--refresh", "--except", str(e["kept"]))
    out = r.stdout + r.stderr
    assert r.returncode == 1 and "NOT refreshed" in out, out
    assert _git(e["stale"], "rev-parse", "HEAD") == head
    assert _git(e["stale"], "status", "--porcelain") == "", out
    assert (e["stale"] / ".githooks" / "pre-commit").read_bytes() == _old_pre_commit()


def test_a_repo_off_its_default_branch_is_set_aside(tmp_path):
    """GOH.HOOKSBRANCH: the refresh commits on whatever is checked out, so a repo mid-feature got
    its hooks commit on the feature branch. One off its default branch is named, never touched."""
    e = _estate(tmp_path)
    _git(e["stale"], "checkout", "-q", "-b", "feature")
    r = _run(tmp_path / "estate", tmp_path, "--refresh", "--except", str(e["kept"]))
    out = r.stdout + r.stderr
    assert r.returncode == 1 and "not its default branch" in out and "feature" in out, out
    assert (e["stale"] / ".githooks" / "pre-commit").read_bytes() == _old_pre_commit()
    assert _git(e["stale"], "rev-list", "--count", "feature").strip() == "1", out


def test_the_default_branch_is_the_remotes_when_it_names_one(tmp_path):
    """A clone's default is origin/HEAD's, so a repo whose default is `trunk` is refreshed there
    and set aside on `main`."""
    e = _estate(tmp_path)
    repo = e["stale"]
    _git(repo, "branch", "-m", "trunk")
    _git(repo, "update-ref", "refs/remotes/origin/trunk", "HEAD")
    _git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    r = _run(tmp_path / "estate", tmp_path, "--refresh", "--except", str(e["kept"]))
    assert "stale: refreshed" in r.stdout + r.stderr, r.stdout + r.stderr


# The starter gate.sh install.sh wrote before GOH.CLASS.ROLLOUT: its stock GOH line is what made
# install.sh insert goh_dir_repair (2026-11-14), so a refresh of it is the case that left 14 repos
# with an uncommitted tools/gate.sh. The fixture GATE above never carried the line, which is how the
# suite stayed green over it.
STOCK_LINE_GATE = (
    "#!/usr/bin/env bash\nset -euo pipefail\n"
    'GOH="${GOH_DIR:-${GOH:-$HOME/Projects/gates_of_heck}}"\n'
    '"$GOH/gates/structural.sh" "$@"\n'
)


def _status(repo: Path) -> str:
    return _git(repo, "status", "--porcelain", "--untracked-files=all")


def _stock_line_gate(repo: Path) -> None:
    (repo / "tools" / "gate.sh").write_text(STOCK_LINE_GATE)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "the starter gate.sh, stock GOH line")


def test_a_refresh_leaves_no_tracked_file_dirty(tmp_path):
    """GOH.CLASS.ROLLOUT: a refresh writes only what it commits. Every repo it touched is clean,
    and the repo's own tools/gate.sh is byte for byte what it committed."""
    e = _estate(tmp_path)
    for name in ("stale", "edited", "current"):
        _stock_line_gate(e[name])
    r = _run(tmp_path / "estate", tmp_path, "--refresh", "--except", str(e["kept"]))
    out = r.stdout + r.stderr
    assert "stale: refreshed" in out, out
    for name in ("stale", "edited", "current"):
        assert _status(e[name]) == "", f"{name}: {_status(e[name])}\n{out}"
        assert (e[name] / "tools" / "gate.sh").read_text() == STOCK_LINE_GATE, name


def test_a_refused_refresh_leaves_status_exactly_as_before(tmp_path):
    """GOH.REFRESHLEAVES: the commit gate refuses, and git status -- including another agent's
    uncommitted edit to tools/gate.sh and an untracked file -- is byte for byte what it was."""
    e = _estate(tmp_path)
    repo = e["stale"]
    _stock_line_gate(repo)
    (repo / "tools" / "commit_msg.sh").write_text("#!/bin/bash\nexit 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "a commit-msg rule that refuses all")
    wip = STOCK_LINE_GATE + "# another agent's uncommitted line\n"
    (repo / "tools" / "gate.sh").write_text(wip)
    (repo / "notes.txt").write_text("untracked\n")
    before = _status(repo)
    r = _run(tmp_path / "estate", tmp_path, "--refresh", "--except", str(e["kept"]))
    out = r.stdout + r.stderr
    assert r.returncode == 1 and "NOT refreshed" in out and "left as it was" in out, out
    assert _status(repo) == before, out
    assert (repo / "tools" / "gate.sh").read_text() == wip, out
    assert (repo / ".githooks" / "pre-commit").read_bytes() == _old_pre_commit()
