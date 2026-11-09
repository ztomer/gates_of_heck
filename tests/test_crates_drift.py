"""A crates.io release alone never turns a repository red; a change that locks a crate behind does
(GOH.CRATESDRIFT, docs/decisions/D-0001-crates-behind-judges-the-change.md).

`goh crates-behind` once counted every lock entry behind the LIVE registry against a ratchet seed:
on 2026-11-09 a day of upstream releases turned antiknob, app-mcp-bridge, monitor and webcam_tune
red and refused routines' push, none of them having moved. Now a behind entry is red only when the
change being judged locked it (its name and version are not in the base's lock) AND a newer
compatible release was out before the change was committed (the index's `pubtime`). Every other
behind entry is drift: a warning, every run, until `cargo update`. `--live` is the strict sweep.

Hermetic: a fake `cargo` (the hidden `--cargo` seam) prints the moves, and the index is a
`file://` directory (the hidden `--index` seam) laid out as index.crates.io is.
"""

import json
import os
import subprocess
from pathlib import Path

from _fast_git import fast_init
from conftest import git, hermetic_env, run_goh

# A commit made at this instant; releases are dated either side of it.
COMMITTED = "2026-06-01T12:00:00Z"
BEFORE = "2026-05-01T00:00:00Z"
AFTER = "2026-07-01T00:00:00Z"


def _lock(*pkgs: tuple[str, str]) -> str:
    out = "version = 4\n"
    for name, version in pkgs:
        out += (
            f'\n[[package]]\nname = "{name}"\nversion = "{version}"\n'
            'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
        )
    return out + '\n[[package]]\nname = "fx"\nversion = "0.1.0"\n'


def _commit(repo: Path, msg: str, when: str = COMMITTED) -> str:
    git(repo, "add", "-A")
    env = {**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when}
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", msg],
        check=True,
        env=env,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    return git(repo, "rev-parse", "HEAD").strip()


def _repo(tmp: Path, base_lock: str) -> tuple[Path, str]:
    repo = tmp / "fx"
    fast_init(repo, "main")
    (repo / "Cargo.lock").write_text(base_lock)
    (repo / "README.md").write_text("fixture\n")
    return repo, _commit(repo, "base")


def _cargo(tmp: Path, moves: str) -> Path:
    """A cargo whose `update --dry-run` would make `moves`, and whose graph has no duplicate."""
    (tmp / "moves.txt").write_text(moves)
    fake = tmp / "cargo"
    fake.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = update ]; then cat "{tmp}/moves.txt" >&2; '
        "echo 'warning: not updating lockfile due to dry run' >&2; fi\nexit 0\n"
    )
    fake.chmod(0o755)
    return fake


def _index(tmp: Path, name: str, versions: list[tuple[str, str | None]]) -> str:
    """An index.crates.io layout holding `name`'s versions with their publish times."""
    root = tmp / "index"
    d = root / name[:2] / name[2:4]
    d.mkdir(parents=True)
    lines = []
    for vers, pubtime in versions:
        e = {"name": name, "vers": vers, "deps": [], "cksum": "0", "features": {}, "yanked": False}
        if pubtime:
            e["pubtime"] = pubtime
        lines.append(json.dumps(e))
    (d / name).write_text("\n".join(lines) + "\n")
    return f"file://{root}"


def _behind(repo: Path, cargo: Path, index: str, *args: str, **env: str):
    return run_goh(
        repo,
        "crates-behind",
        "--root",
        str(repo),
        "--cargo",
        str(cargo),
        "--index",
        index,
        *args,
        env=hermetic_env(GOH_SEED_DIR=str(repo.parent / "store"), **env),
    )


ITOA_19_BEFORE = [("1.0.18", None), ("1.0.19", BEFORE)]


def test_a_release_alone_leaves_the_gate_green_and_says_the_drift(tmp_path):
    repo, _ = _repo(tmp_path, _lock(("itoa", "1.0.18")))
    (repo / "README.md").write_text("a change that touches no lock entry\n")
    _commit(repo, "docs")
    cargo = _cargo(tmp_path, "    Updating itoa v1.0.18 -> v1.0.19\n")
    r = _behind(repo, cargo, _index(tmp_path, "itoa", ITOA_19_BEFORE))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "itoa v1.0.18 -> v1.0.19" in r.stderr, r.stderr
    assert "not by this change" in r.stderr, r.stderr
    assert "every Cargo.lock is at its newest" not in r.stdout, "drift is no clean bill"
    # ONE clean sentence (GOH.GATEHONEST 5): it read "1 crate behind its newest compatible release
    # behind by releases, not this change's", and the OK line "... release by later releases".
    drift = next(ln for ln in r.stderr.splitlines() if "itoa v1.0.18" in ln)
    assert (
        "Cargo.lock: 1 crate is behind its newest compatible release, not by this change (" in drift
    ), drift
    assert drift.count(" behind ") == 1, drift
    assert "no crate this change locked is behind; 1 other is (above)" in r.stdout, r.stdout


def test_a_change_that_adds_a_crate_behind_is_red(tmp_path):
    repo, _ = _repo(tmp_path, _lock())
    (repo / "Cargo.lock").write_text(_lock(("itoa", "1.0.18")))
    _commit(repo, "add itoa, held back")
    cargo = _cargo(tmp_path, "    Updating itoa v1.0.18 -> v1.0.19\n")
    r = _behind(repo, cargo, _index(tmp_path, "itoa", ITOA_19_BEFORE))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Cargo.lock: itoa v1.0.18 -> v1.0.19" in r.stderr, r.stderr
    assert "cargo update" in r.stderr, "the fix is named"


def test_a_change_that_moves_a_crate_further_behind_is_red(tmp_path):
    repo, _ = _repo(tmp_path, _lock(("itoa", "1.0.19")))
    (repo / "Cargo.lock").write_text(_lock(("itoa", "1.0.18")))
    _commit(repo, "downgrade itoa")
    cargo = _cargo(tmp_path, "    Updating itoa v1.0.18 -> v1.0.19\n")
    r = _behind(repo, cargo, _index(tmp_path, "itoa", ITOA_19_BEFORE))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Cargo.lock: itoa v1.0.18 -> v1.0.19" in r.stderr, r.stderr


def test_a_release_after_the_change_was_committed_does_not_make_it_red(tmp_path):
    """The change locked the newest release of its day; one published later is drift, so a CI re-run
    or a late push of the same commit is judged as it was."""
    repo, _ = _repo(tmp_path, _lock())
    (repo / "Cargo.lock").write_text(_lock(("itoa", "1.0.18")))
    _commit(repo, "add itoa at its newest")
    cargo = _cargo(tmp_path, "    Updating itoa v1.0.18 -> v1.0.19\n")
    r = _behind(repo, cargo, _index(tmp_path, "itoa", [("1.0.18", BEFORE), ("1.0.19", AFTER)]))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "itoa v1.0.18 -> v1.0.19" in r.stderr and "not by this change" in r.stderr, r.stderr


def test_the_change_is_the_whole_pushed_range_when_the_base_is_named(tmp_path):
    """The push gate names the remote's tip (GOH_CHANGE_BASE): a crate locked two commits back is
    still this push's, though the last commit alone did not touch it."""
    repo, base = _repo(tmp_path, _lock())
    (repo / "Cargo.lock").write_text(_lock(("itoa", "1.0.18")))
    _commit(repo, "add itoa, held back")
    (repo / "README.md").write_text("then a docs commit\n")
    _commit(repo, "docs")
    cargo = _cargo(tmp_path, "    Updating itoa v1.0.18 -> v1.0.19\n")
    index = _index(tmp_path, "itoa", ITOA_19_BEFORE)
    assert _behind(repo, cargo, index).returncode == 0, "the last commit alone moved nothing"
    r = _behind(repo, cargo, index, GOH_CHANGE_BASE=base)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Cargo.lock: itoa v1.0.18 -> v1.0.19" in r.stderr, r.stderr


def test_live_is_the_strict_sweep_every_behind_entry_red(tmp_path):
    repo, _ = _repo(tmp_path, _lock(("itoa", "1.0.18")))
    (repo / "README.md").write_text("docs\n")
    _commit(repo, "docs")
    cargo = _cargo(tmp_path, "    Updating itoa v1.0.18 -> v1.0.19\n")
    r = _behind(repo, cargo, _index(tmp_path, "itoa", ITOA_19_BEFORE), "--live")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "Cargo.lock: itoa v1.0.18 -> v1.0.19" in r.stderr, r.stderr


def test_an_unreadable_index_keeps_the_live_verdict_and_says_so(tmp_path):
    repo, _ = _repo(tmp_path, _lock())
    (repo / "Cargo.lock").write_text(_lock(("itoa", "1.0.18")))
    _commit(repo, "add itoa, held back")
    cargo = _cargo(tmp_path, "    Updating itoa v1.0.18 -> v1.0.19\n")
    r = _behind(repo, cargo, f"file://{tmp_path}/no-index")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "release dates unreadable" in r.stderr, r.stderr


def test_a_retired_seed_file_is_named_to_delete_never_red(tmp_path):
    repo, _ = _repo(tmp_path, _lock(("itoa", "1.0.18")))
    root = git(repo, "rev-list", "--max-parents=0", "HEAD").strip()
    seed = {
        "entries": [{"root": root, "repo": "fx", "path": "Cargo.lock", "count": 1, "reason": "x"}]
    }
    (repo / "crates_behind_seed.json").write_text(json.dumps(seed))
    _commit(repo, "seed")
    cargo = _cargo(tmp_path, "")
    r = _behind(repo, cargo, _index(tmp_path, "itoa", ITOA_19_BEFORE))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "crates_behind_seed.json is retired" in r.stderr, r.stderr
    assert "every Cargo.lock is at its newest compatible releases" in r.stdout, r.stdout


def test_the_push_gate_names_the_pushed_ranges_base(tmp_path):
    """`push_change_base` (lib/push_runs.sh) gives the gate the remote's tip, or for a new branch
    the merge base with the remote's default branch; nothing known is empty, never a guess."""
    repo, base = _repo(tmp_path, _lock())
    (repo / "README.md").write_text("next\n")
    head = _commit(repo, "next")
    git(repo, "update-ref", "refs/remotes/origin/HEAD", base)
    zero = "0" * 40
    lib = Path(__file__).resolve().parent.parent / "lib" / "push_runs.sh"

    def ask(remote_sha: str, remote: str) -> str:
        r = subprocess.run(
            [
                "bash",
                "-c",
                f'. "{lib}"; push_change_base "$@"',
                "_",
                str(repo),
                head,
                remote_sha,
                remote,
            ],
            capture_output=True,
            text=True,
            check=True,
            stdin=subprocess.DEVNULL,
        )
        return r.stdout.strip()

    assert ask(base, "origin") == base, "an existing ref: the remote's tip"
    assert ask(zero, "origin") == base, "a new branch: the merge base with the remote's HEAD"
    assert ask(zero, "") == "", "no remote named: no base"
