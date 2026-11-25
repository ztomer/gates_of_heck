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

import functools
import hashlib
import subprocess
from pathlib import Path

from _fast_git import fast_init
from _gatesrc_kit import write_gatesrc
from _grown import built, keyed
from _small_store import estate_state
from conftest import REPO_ROOT, hermetic_env, native_goh_path

OLD_STOCK = "18330c7"  # a retired stock pre-commit (retired_hooks.sha256)
SCRIPT = REPO_ROOT / "scripts" / "hooks_refresh.py"
STOCK = {p.name: p.read_bytes() for p in (REPO_ROOT / "hooks").iterdir() if p.is_file()}
# The repo's own gate passes; the hook's commit gate still runs goh's structural gate before it.
GATE = "#!/usr/bin/env bash\nexit 0\n"


@functools.cache
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


def _build_consumer(repo: Path) -> None:
    """A consumer whose pre-commit is the old stock one, committed; the rest is stock."""
    hooks = repo / ".githooks"
    hooks.mkdir(parents=True)
    for h, body in STOCK.items():
        (hooks / h).write_bytes(_old_pre_commit() if h == "pre-commit" else body)
        (hooks / h).chmod(0o755)
    write_gatesrc(repo, "GOH_MAX_LINES=500\n")
    (repo / "tools").mkdir()
    (repo / "tools" / "gate.sh").write_text(GATE)
    (repo / "tools" / "gate.sh").chmod(0o755)
    fast_init(repo)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "core.hooksPath", ".githooks")
    _commit(repo, "fixture")


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", message)


def _consumer(root: Path, name: str) -> Path:
    key = keyed("hooks-refresh", _old_pre_commit(), STOCK, GATE)
    return built(root / name, key, _build_consumer)


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


def _audited(root: Path, tmp_path: Path) -> dict:
    """The state one estate audit of ROOT records."""
    state = tmp_path / "state" / "estate_audit.json"
    env = _env()
    env["GOH_ESTATE_STATE"] = str(state)
    r = subprocess.run(
        [
            "python3",
            str(REPO_ROOT / "scripts" / "estate_audit.py"),
            "--root",
            str(root),
            "--no-fetch",
            "--no-grace-watch",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=300,
        stdin=subprocess.DEVNULL,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return estate_state(state)


# The starter gate.sh install.sh wrote before GOH.CLASS.ROLLOUT: its stock GOH line is what made
# install.sh insert goh_dir_repair (2026-11-14), so a refresh of it is the case that left 14 repos
# with an uncommitted tools/gate.sh. The fixture GATE above never carried the line, which is how the
# suite stayed green over it.
STOCK_LINE = (
    "#!/usr/bin/env bash\nset -euo pipefail\n"
    'GOH="${GOH_DIR:-${GOH:-$HOME/Projects/gates_of_heck}}"\n'
)
STOCK_LINE_GATE = STOCK_LINE + '"$GOH/gates/structural.sh" "$@"\n'


def _status(repo: Path) -> str:
    return _git(repo, "status", "--porcelain", "--untracked-files=all")


def _stock_line_gate(repo: Path, gate: str = STOCK_LINE_GATE) -> None:
    (repo / "tools" / "gate.sh").write_text(gate)
    _commit(repo, "the starter gate.sh, stock GOH line")


def test_the_listing_the_audit_and_a_refresh_over_one_estate(tmp_path):
    """One estate, read three ways in the order an owner does: the listing names every stale repo
    and only those, the audit records them, and the refresh brings each current and commits it."""
    root = tmp_path / "estate"
    e = {name: _consumer(root, name) for name in ("stale", "edited", "current", "kept")}
    (e["edited"] / ".githooks" / "pre-commit").write_bytes(
        _old_pre_commit() + b"# this repo's own line\n"
    )
    (e["current"] / ".githooks" / "pre-commit").write_bytes(STOCK["pre-commit"])
    for name in ("stale", "edited", "current"):
        _stock_line_gate(e[name])
    heads = {name: _git(repo, "rev-parse", "HEAD") for name, repo in e.items()}

    r = _run(root, tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out  # a stale hook is a finding
    assert "stale: pre-commit" in out and "kept: pre-commit" in out, out
    assert "edited" not in out, out  # a hook the repo edited is its own business
    assert "current:" not in out, out

    recorded = _audited(root, tmp_path)["stale_hooks"]  # named relative to the root, as all are
    assert recorded == {"kept": ["pre-commit"], "stale": ["pre-commit"]}, recorded
    # Neither wrote anything.
    for name, repo in e.items():
        assert _status(repo) == "" and _git(repo, "rev-parse", "HEAD") == heads[name], name

    edited_before = (e["edited"] / ".githooks" / "pre-commit").read_bytes()
    r = _run(root, tmp_path, "--refresh", "--except", str(e["kept"]))
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "stale: refreshed" in out, out
    # The stale repo: current, committed, clean, and the commit carries .githooks only.
    assert (e["stale"] / ".githooks" / "pre-commit").read_bytes() == STOCK["pre-commit"], out
    changed = _git(e["stale"], "show", "--name-only", "--format=", "HEAD").split()
    assert changed and all(p.startswith(".githooks/") for p in changed), changed
    # A hook the repo edited is never clobbered; a repo set aside is named, not touched.
    assert (e["edited"] / ".githooks" / "pre-commit").read_bytes() == edited_before
    assert (e["kept"] / ".githooks" / "pre-commit").read_bytes() == _old_pre_commit()
    assert "kept" in out and "set aside" in out, out
    # GOH.CLASS.ROLLOUT: a refresh writes only what it commits. Every repo it touched is clean,
    # and the repo's own tools/gate.sh is byte for byte what it committed.
    for name in ("stale", "edited", "current"):
        assert _status(e[name]) == "", f"{name}: {_status(e[name])}\n{out}"
        assert (e[name] / "tools" / "gate.sh").read_text() == STOCK_LINE_GATE, name
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


def test_a_repo_set_aside_or_refused_is_left_as_it_was_and_the_rest_refreshed(tmp_path):
    """One refresh over an estate holding a repo for each rule, each named with its own reason:
    * `wip`: uncommitted hook edits;
    * `held`: on hold;
    * `feature` (GOH.HOOKSBRANCH): the refresh commits on whatever is checked out, so a repo
      mid-feature got its hooks commit on the feature branch;
    * `trunk`: the default branch is origin/HEAD's when it names one, so this one IS refreshed;
    * `refused` (GOH.REFRESHLEAVES): its commit gate refuses, and git status -- another agent's
      uncommitted edit to tools/gate.sh and an untracked file -- is byte for byte what it was."""
    root = tmp_path / "estate"
    names = ("wip", "held", "feature", "trunk", "refused")
    e = {name: _consumer(root, name) for name in names}
    (e["wip"] / ".githooks" / "commit-msg").write_bytes(STOCK["commit-msg"] + b"# wip\n")
    _git(e["held"], "config", "goh.hold", "frozen for the test")
    _git(e["feature"], "checkout", "-q", "-b", "feature")
    _git(e["trunk"], "branch", "-m", "trunk")
    _git(e["trunk"], "update-ref", "refs/remotes/origin/trunk", "HEAD")
    _git(e["trunk"], "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    (e["refused"] / "tools" / "commit_msg.sh").write_text("#!/bin/bash\nexit 1\n")
    _stock_line_gate(e["refused"], STOCK_LINE)
    work = STOCK_LINE + "# another agent's uncommitted line\n"
    (e["refused"] / "tools" / "gate.sh").write_text(work)
    (e["refused"] / "notes.txt").write_text("untracked\n")
    before = {name: (_status(repo), _git(repo, "rev-parse", "HEAD")) for name, repo in e.items()}

    r = _run(root, tmp_path, "--refresh")
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    said = {n: next(ln for ln in out.splitlines() if f"{n}: " in ln) for n in names}
    assert "uncommitted" in said["wip"], out
    assert "on hold" in said["held"], out
    assert "not its default branch" in said["feature"] and "feature" in said["feature"], out
    assert "trunk: refreshed" in said["trunk"], out
    assert "NOT refreshed" in said["refused"] and "left as it was" in said["refused"], out
    for name in ("wip", "held", "feature", "refused"):
        repo = e[name]
        assert (_status(repo), _git(repo, "rev-parse", "HEAD")) == before[name], (name, out)
        assert (repo / ".githooks" / "pre-commit").read_bytes() == _old_pre_commit(), name
    assert (e["refused"] / "tools" / "gate.sh").read_text() == work, out


def _tracking_origin(repo: Path, tmp_path: Path) -> None:
    """REPO's main tracks a bare origin holding its fixture commit."""
    origin = tmp_path / "origins" / f"{repo.name}.git"
    _git(repo, "clone", "-q", "--bare", str(repo), str(origin))
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "fetch", "-q", "origin")
    branch = _git(repo, "branch", "--show-current").strip()
    _git(repo, "branch", "-q", f"--set-upstream-to=origin/{branch}")


def _ahead(repo: Path) -> int:
    return int(_git(repo, "rev-list", "--count", "@{u}..HEAD").strip())


def test_a_refresh_pushes_its_commit_and_any_stranded_one(tmp_path):
    """GOH.HOOKREFRESHPUSH: a refresh committed and pushed none, so six consumers sat ahead of
    origin with only hook commits (2026-11-15). Now each is pushed through the consumer's own push
    gate -- a hook commit an earlier refresh stranded included -- and an owner's commit ahead is
    never pushed by goh: that repo stays as it was, named."""
    root = tmp_path / "estate"
    stranded = _consumer(root, "stranded")
    owned = _consumer(root, "owned")
    for repo in (stranded, owned):
        _tracking_origin(repo, tmp_path)
    # An earlier refresh's commit, never pushed: only `.githooks`, goh's subject.
    rec = stranded / ".githooks" / ".goh-installed" / "commit-msg.sha256"
    rec.parent.mkdir()
    rec.write_text(hashlib.sha256(STOCK["commit-msg"]).hexdigest() + "\n")
    _commit(stranded, "chore: refresh goh's stock hooks (commit-msg) -- goh hooks --refresh")
    (owned / "notes.txt").write_text("the owner's work\n")
    _commit(owned, "the owner's commit")

    r = _run(root, tmp_path, "--refresh")
    out = r.stdout + r.stderr

    assert _ahead(stranded) == 0, out
    assert _git(stranded, "rev-list", "--count", "HEAD").strip() == "3", out  # both, its own too
    assert _ahead(owned) == 2, out  # its own commit and the refresh: left for the owner's push
    assert "owner's" in out, out
