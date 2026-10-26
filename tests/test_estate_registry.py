"""The estate is every repo `install.sh` installed into, not one directory (BACKLOG 6.14).

`~/Projects` WAS the estate: a directory, walked three levels deep. But `install.sh` wires goh into
whatever repository it is pointed at, and one of the estate's consumers lives OUTSIDE that
directory -- `~/.claude/skills` carries the house hooks -- so the audit sweep, the table in
BACKLOG 3.3 and `scripts/estate_audit.py` never read it. It was red on the stdin gate with 35
undeclared calls, and nothing in the house could say so, because nothing knew it was there.

So the estate is a REGISTRY: `install.sh` records the toplevel it installed into, one line per
repo, in goh's own state directory beside `estate_audit.json`; every estate sweep reads that
registry UNIONED with the directory it walks; and a registered path that is GONE is named, never
skipped.

THREE WAYS THIS CAN LIE, each with its own test below:

* a repo installed BEFORE the registry existed -- seeded once from the `~/Projects` walk plus
  `~/.claude/skills`, so the first run is not a smaller estate than the machine;
* a repo MOVED on disk -- the registry is keyed by PATH, so a moved repo is a gone path AND a new
  one: both are named, and nobody guesses which repository "went missing";
* a scratch clone registered by a test -- every test here uses its OWN state directory, so the
  machine's real registry is never written by a test.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env
from test_estate_audit import LOCK, PLANTED, _db, _git

AUDIT = REPO_ROOT / "scripts" / "estate_audit.py"
REGISTRY_CLI = REPO_ROOT / "scripts" / "estate_registry.py"
AGENT_AUDIT = REPO_ROOT / "scripts" / "agent_audit.py"

needs_cargo_audit = pytest.mark.skipif(
    shutil.which("cargo-audit") is None, reason="cargo-audit not installed"
)


def _repo_at(path: Path, crate: str = "goh-fixture-crate") -> Path:
    """A git repository with one tracked Cargo.lock, holding the planted crate."""
    path.mkdir(parents=True)
    (path / "Cargo.lock").write_text(LOCK.format(name=crate, zeros="0" * 64))
    fast_init(path, "main")
    _git(path, "add", "Cargo.lock")
    _git(path, "commit", "-qm", "lock")
    return path


def _install(target: Path, state: Path) -> subprocess.CompletedProcess:
    """`install.sh` with the estate registry in the test's OWN state directory."""
    return subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "install.sh"), str(target)],
        capture_output=True,
        text=True,
        env=hermetic_env(GOH_SKIP_BUILD="1", GOH_ESTATE_STATE=str(state)),
        stdin=subprocess.DEVNULL,
        check=False,
    )


def _registry(state: Path) -> list[str]:
    p = state.parent / "installed_repos"
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def _audit(root: Path, state: Path, db: Path) -> dict:
    r = subprocess.run(
        [
            sys.executable,
            str(AUDIT),
            "--root",
            str(root),
            "--state",
            str(state),
            "--db",
            str(db),
            "--no-fetch",
        ],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        env=hermetic_env(),
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(state.read_text())


@needs_cargo_audit
def test_the_audit_reads_a_repo_installed_outside_the_estate_root(tmp_path: Path) -> None:
    """THE case: `~/.claude/skills` is a consumer outside `~/Projects`, and the audit never read
    it. A repo installed OUTSIDE the walked root is audited anyway, because the registry says it
    is in the estate.

    Red before the change: the audit walked the root and nothing else, so the planted advisory
    in the outside repo was never found."""
    root = tmp_path / "Projects"
    root.mkdir()
    state = tmp_path / "state" / "estate_audit.json"
    outside = _repo_at(tmp_path / "elsewhere" / "consumer")
    db = _db(tmp_path, planted=True)

    assert _install(outside, state).returncode == 0

    found = _audit(root, state, db)
    locks = found["advisories"].get(PLANTED, {}).get("lockfiles", [])
    assert any(lf.endswith("elsewhere/consumer/Cargo.lock") for lf in locks), found


def test_the_registry_holds_the_repos_toplevel_not_the_directory_installed_into(
    tmp_path: Path,
) -> None:
    """`install.sh` is pointed at a repo, sometimes at a directory inside one. The registry holds
    the REPO: a line per subdirectory would register one consumer three times and would still not
    say which repository it is."""
    state = tmp_path / "state" / "estate_audit.json"
    repo = _repo_at(tmp_path / "consumer")
    inside = repo / "sub" / "dir"
    inside.mkdir(parents=True)

    assert _install(inside, state).returncode == 0

    assert _registry(state) == [str(repo.resolve())]


def test_a_registered_path_that_is_gone_is_named(tmp_path: Path) -> None:
    """A repo that moved leaves its path behind. The registry is keyed by PATH, so the old line
    cannot become the new repo by itself -- the old path is NAMED (the owner is told which path
    went away) and the new path is registered by the next install.

    The alternative -- dropping the line -- is how a consumer leaves the estate without anybody
    deciding that."""
    root = tmp_path / "Projects"
    root.mkdir()
    state = tmp_path / "state" / "estate_audit.json"
    repo = _repo_at(tmp_path / "old")
    db = _db(tmp_path, planted=True)
    assert _install(repo, state).returncode == 0

    repo.rename(tmp_path / "new")

    found = _audit(root, state, db)
    assert any(str(tmp_path / "old") in p for p in found["problems"]), found
    assert not any(str(tmp_path / "new") in p for p in found["problems"]), found


@needs_cargo_audit
def test_a_moved_repo_is_audited_again_once_it_is_installed_where_it_now_lives(
    tmp_path: Path,
) -> None:
    """The other half of a move: the new path is a NEW entry, and it is audited as soon as the
    repo is installed there. Nothing guesses that the moved repo is the old one."""
    root = tmp_path / "Projects"
    root.mkdir()
    state = tmp_path / "state" / "estate_audit.json"
    repo = _repo_at(tmp_path / "old")
    db = _db(tmp_path, planted=True)
    assert _install(repo, state).returncode == 0
    repo.rename(tmp_path / "new")

    assert _install(tmp_path / "new", state).returncode == 0

    found = _audit(root, state, db)
    locks = found["advisories"].get(PLANTED, {}).get("lockfiles", [])
    assert any(lf.endswith("new/Cargo.lock") for lf in locks), found
    assert any(str(tmp_path / "old") in p for p in found["problems"]), found


def test_the_seed_registers_the_walk_and_the_repo_outside_the_root(tmp_path: Path) -> None:
    """A repo installed BEFORE the registry existed is in the estate already. The seed walks the
    estate root and adds the consumer that lives outside it (`~/.claude/skills`), once, so the
    first sweep is not a smaller estate than the machine."""
    root = tmp_path / "Projects"
    walked = _repo_at(root / "walked")
    outside = _repo_at(tmp_path / "skills")
    state = tmp_path / "state" / "estate_audit.json"

    r = subprocess.run(
        [
            sys.executable,
            str(REGISTRY_CLI),
            "seed",
            "--root",
            str(root),
            "--extra",
            str(outside),
            "--state",
            str(state),
        ],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        env=hermetic_env(),
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert set(_registry(state)) == {str(walked.resolve()), str(outside.resolve())}


def test_the_seed_is_additive_and_never_drops_a_line(tmp_path: Path) -> None:
    """Running the seed twice registers the same estate twice over, and a repo the seed did not
    find keeps its line: the seed is how the estate grows, never how it shrinks."""
    root = tmp_path / "Projects"
    _repo_at(root / "walked")
    state = tmp_path / "state" / "estate_audit.json"
    kept = tmp_path / "kept"
    kept.mkdir()
    registry = state.parent / "installed_repos"
    registry.parent.mkdir(parents=True)
    registry.write_text(f"{kept}\n", encoding="utf-8")

    for _ in range(2):
        r = subprocess.run(
            [sys.executable, str(REGISTRY_CLI), "seed", "--root", str(root), "--state", str(state)],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            env=hermetic_env(),
        )
        assert r.returncode == 0, r.stdout + r.stderr

    lines = _registry(state)
    assert lines.count(str(kept)) == 1, lines
    assert len(lines) == len(set(lines)), lines
    assert str((root / "walked").resolve()) in lines, lines


def test_a_test_never_writes_the_machines_registry(tmp_path: Path) -> None:
    """The lie a scratch clone can tell: a test's temporary repository ends up in the machine's
    own estate registry, and the estate then holds a consumer that does not exist. The registry
    lives beside the state file, so a test that names its own state file gets its own registry."""
    state = tmp_path / "state" / "estate_audit.json"
    repo = _repo_at(tmp_path / "scratch")

    assert _install(repo, state).returncode == 0

    machine = Path.home() / ".local" / "state" / "goh" / "installed_repos"
    if machine.exists():
        assert str(repo.resolve()) not in machine.read_text(encoding="utf-8").splitlines()


def test_the_agent_audit_finds_a_sha_in_a_registered_repo_outside_the_root(tmp_path: Path) -> None:
    """The other estate sweep: `scripts/agent_audit.py` looks for a landed sha in the repos under
    the estate root. A repo goh was installed into outside that root is a repo a sha can be in,
    and a sha nobody can find reads as a silent failure."""
    root = tmp_path / "Projects"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    repo = outside / "consumer"
    _repo_at(repo)
    _git(repo, "commit", "-qm", "second", "--allow-empty")
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout.strip()  # fmt: skip

    state = tmp_path / "state" / "estate_audit.json"
    registry = state.parent / "installed_repos"
    registry.parent.mkdir(parents=True)
    registry.write_text(f"{repo.resolve()}\n", encoding="utf-8")

    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "event": "outcome",
                "session_id": "s",
                "tool_use_id": "t1",
                "status": "landed",
                "report": f"Status: landed\n{sha[:9]} fix the leak",
                "tokens": {"total": 1},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    r = subprocess.run(
        [
            sys.executable,
            str(AGENT_AUDIT),
            "--ledger",
            str(ledger),
            "--root",
            str(root),
            "--json",
            "--offline",
        ],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        env=hermetic_env(GOH_ESTATE_STATE=str(state)),
    )
    assert r.returncode == 0, r.stdout + r.stderr
    v = json.loads(r.stdout)["verification"]
    assert v["silent_failure"] == [], v
    assert len(v["unverified"]) == 1, v
