"""The environment the gate runs in: where a config key comes FROM, and whether
the gate's own source is committed.

Neither question is `goh structural` versus `gates/structural.sh` — that is
tests/test_goh_structural_parity.py. These are the two things a gate run also
depends on and that no verdict comparison can see: the environment it inherits,
and the working tree its own source is read from.

Measured 2026-10-02, both of them:
  * `push_gate.sh` sourced `.gatesrc` under `set -a`, so a push of one repo
    reached the export gate with that repo's real `GOH_SKILLS_ROOT`,
    `GOH_SKILLS_CORPUS` and `GOH_PYTHON_FORMATTED` in its environment, and the
    pytest suite it runs inherited all three while every fixture declared none.
    Seven tests red on a green tree, and the push gate refused pushes that
    passed locally.
  * The pipeline is read from the working tree, so appending one comment line to
    a checker changed the gate every repo runs — no reinstall, no output, no
    refusal. docs/SUPERSOTA.md §3 calls it the highest-severity gap there.

`_hermetic_env` is the same helper as the parity suite's, not a shared one:
tests/conftest.py is not this file's to edit, and a test that reaches across
into another suite for its environment is how the two drift.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from conftest import hermetic_env


ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


def _porcelain(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True
    ).stdout


# What docs/config.md documents in its STRUCTURAL table that is NOT pipeline config: the
# first two say WHICH BINARY runs, the rest belong to the push gate, install.sh or
# build-goh.sh. Absent from the drop list is safe only while somebody has said so.
#
# GOH_CROSS_REPO_ROOT is here for the same reason as GOH_PUSH_WORKTREES and GOH_PUSH_LOGS: it is
# SET BY push_gate.sh on the one command that needs it (`gate.sh --full` in the export worktree),
# not read from an ambient environment by any structural step. Dropping it is therefore both safe
# and required -- and required rather than merely tidy, because the value names a REAL checkout, so
# an inherited one would silently point a gate at somebody else's tree.
NOT_PIPELINE_CONFIG = set(
    "GOH_BIN GOH_NO_NATIVE GOH_SKIP_BUILD "
    "GOH_EXPORT_KEEP GOH_PUSH_WORKTREES GOH_PUSH_LOGS GOH_TAG_VERSION_SOURCES "
    "GOH_CROSS_REPO_ROOT".split()
)

DROP_LIST_RE = re.compile(r'_goh_config_keys="([^"]+)"', re.S)


def test_every_documented_structural_key_is_dropped_from_the_environment() -> None:
    """structural.sh's drop list, pinned against docs/config.md, both ways. The drift
    that hurts is a key added to structural.sh, to config.md and to no list: an ambient
    value then enables that step again. A key gone stale hides the next one."""
    doc = (ROOT / "docs" / "config.md").read_text(encoding="utf-8")
    section = doc.split("## Structural", 1)[1].split("\n## ", 1)[0]
    documented = set(re.findall(r"`(GOH_[A-Z][A-Z_]*)`", section))
    shell = (ROOT / "gates" / "structural.sh").read_text(encoding="utf-8")
    match = DROP_LIST_RE.search(shell)
    assert match, "structural.sh no longer drops its inherited config keys"
    dropped, pipeline = set(match.group(1).split()), documented - NOT_PIPELINE_CONFIG
    assert not dropped & NOT_PIPELINE_CONFIG, (
        f"structural.sh drops {sorted(dropped & NOT_PIPELINE_CONFIG)}, which config.md "
        "documents as NOT pipeline configuration -- say why above"
    )
    assert dropped == pipeline, (
        "structural.sh's drop list and docs/config.md's structural key set disagree.\n"
        f"  documented, never dropped: {sorted(pipeline - dropped)}\n"
        f"  dropped, never documented: {sorted(dropped - pipeline)}\n"
        "An undocumented value in the environment would enable that step again."
    )


# ── The push gate's own environment ─────────────────────────────────────────


def _committable_repo(tmp_path: Path, gate: str) -> Path:
    """A repo the push gate will gate: a `tools/gate.sh`, a VERSION, one commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "tools").mkdir()
    (repo / "tools" / "gate.sh").write_text(gate)
    (repo / "VERSION").write_text("1.0.0\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "one")
    return repo


def _push(repo: Path, checkout: Path, tmp_path: Path, **env: str):
    """Run the push gate over `repo`'s HEAD, as a pre-push hook is run."""
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return subprocess.run(
        ["bash", str(checkout / "gates" / "push_gate.sh"), "origin"],
        cwd=repo,
        env=hermetic_env(GOH_DIR=str(checkout), GOH_PUSH_LOGS=str(tmp_path / "logs"), **env),
        text=True,
        input=f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n",
        capture_output=True,
    )


def test_the_push_gate_does_not_hand_the_export_gate_this_repos_gatesrc(
    tmp_path: Path,
) -> None:
    """The producer, end to end: a repo's `.gatesrc` keys stay in the push gate. The
    fixture's `tools/gate.sh` IS the export gate and records what it was given;
    push_gate.sh reads ONE key and gives the tag check its environment in a subshell of
    its own. Under `set -a` this was the repo's own configuration."""
    keys = (
        "GOH_MAX_LINES GOH_SKILLS_CORPUS GOH_SKILLS_ROOT GOH_PYTHON_FORMATTED "
        "GOH_NO_KILL_BY_NAME GOH_LINE_BASELINE"
    )
    repo = _committable_repo(
        tmp_path,
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f'for k in {keys}; do printf \'%s=%s\\n\' "$k" "${{!k-UNSET}}" >> "$GATE_REPORT"; done\n',
    )
    (repo / ".gatesrc").write_text(
        "GOH_MAX_LINES=500\nGOH_SKILLS_CORPUS=1\nGOH_NO_KILL_BY_NAME=1\n"
        "GOH_SKILLS_ROOT=/somewhere/else\nGOH_PYTHON_FORMATTED=1\nGOH_LINE_BASELINE=base.txt\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "config")

    report = tmp_path / "report.txt"
    proc = _push(repo, ROOT, tmp_path, GATE_REPORT=str(report))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    seen = dict(line.split("=", 1) for line in report.read_text().splitlines() if "=" in line)
    leaked = {k: v for k, v in seen.items() if v != "UNSET"}
    assert not leaked, f"the export gate inherited the repo's configuration: {leaked}"


# ── Uncommitted gate source is a DIFFERENT GATE ──────────────────────────────
# Not a build artefact: `structural.sh` and every checker under `checks/` are read from
# this checkout's WORKING TREE as the gate runs, so one uncommitted line changes every
# repo's verdict silently, with no reinstall to hint at it — SUPERSOTA §3's top gap.
SOURCE_PATH_LISTS = {
    "gates/structural.sh": r'_goh_gate_source_paths="([\w ]+)"',
    "gates/push_gate.sh": r"--\s+(gates checks lib tui)\s+2>",
}
# scripts/build-goh.sh left this list in C3: it builds bin/goh from an export of HEAD, so it
# publishes no working-tree source at all, and has nothing of this kind to warn on.


def test_all_gate_source_path_lists_are_the_same_list() -> None:
    """Two files warn or refuse on uncommitted gate source, each spelling the paths out.
    A helper is the obvious dedup and the wrong one: it would be gate source itself, and
    a check whose list is written by what it checks cannot vouch for it."""
    lists = {}
    for name, pattern in SOURCE_PATH_LISTS.items():
        found = re.search(pattern, (ROOT / name).read_text(encoding="utf-8"))
        assert found, f"{name} no longer names the uncommitted-source paths it is checked on"
        lists[name] = set(found.group(1).split())
    shared = set.intersection(*lists.values())
    assert all(entries == shared for entries in lists.values()), (
        "the uncommitted-gate-source path lists disagree:\n"
        + "\n".join(f"  {name}: {sorted(entries)}" for name, entries in lists.items())
    )
    assert shared == {"gates", "checks", "lib", "tui"}, sorted(shared)


def _clean_checkout_of_todays_gates(tmp_path: Path) -> Path:
    """A CLEAN git checkout of the gate as it stands, uncommitted work included. A clone
    of HEAD is wrong twice: it does not carry the gate under test mid-change, and every
    assertion would answer with the live tree."""
    checkout = tmp_path / "gates_of_heck"
    subprocess.run(
        ["git", "clone", "-q", "--no-hardlinks", str(ROOT), str(checkout)],
        check=True,
        capture_output=True,
    )
    work = subprocess.run(
        ["git", "-C", str(ROOT), "diff", "HEAD", "--"], check=True, capture_output=True
    ).stdout
    if work:
        subprocess.run(["git", "-C", str(checkout), "apply", "-"], input=work, check=True)
    # ...and the NEW files: `git diff HEAD` leaves out an untracked file, so a gate whose change
    # adds one (gates/_from_head.py did) was judged here without it -- and failed on the import.
    untracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
    ).stdout.split(b"\0")
    for rel in filter(None, (u.decode() for u in untracked)):
        (checkout / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, checkout / rel)
    _git(checkout, "add", "-A")
    # A clean working tree has NOTHING to commit and `git commit` says so with a
    # non-zero exit — and this fixture is built from a diff, so it is empty
    # exactly when the session has committed its work, which is the state this
    # file is developed in half the time. Commit only when there is something,
    # then insist the result is clean either way.
    if _porcelain(checkout):
        _git(checkout, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "today")
    clean = _porcelain(checkout)
    assert not clean, f"the fixture checkout is not clean: {clean}"
    return checkout


def test_uncommitted_gate_source_is_named_by_every_gate_that_runs_one(tmp_path: Path) -> None:
    """Both gates name it; neither refuses. One checkout, clean then dirty.

    The severity split is the design and it is the opposite of the obvious one. Failing
    here looked right and was wrong twice, both found by doing it: `structural.sh` runs
    under 30 repos' pre-commit hooks, and every test that spawns a gate with `GOH_DIR`
    at a real checkout goes red the moment a session has uncommitted work here
    (measured: 22, from one session's own box halfway through). So certify NAMES itself
    and publish REFUSES — `scripts/build-goh.sh`, which `install.sh` calls, is the only
    place a reinstall happens and the only refusal nothing observes by accident. The
    clean half runs first on purpose: a warning on every run of every repo's gate is one
    nobody reads."""
    checkout = _clean_checkout_of_todays_gates(tmp_path)
    repo = _committable_repo(tmp_path, "#!/usr/bin/env bash\nexit 0\n")

    def commit_gate() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(checkout / "gates" / "structural.sh"), "--staged"],
            cwd=repo,
            capture_output=True,
            text=True,
            env=hermetic_env(GOH_DIR=str(checkout), GOH_BIN=str(ROOT / "bin" / "goh")),
        )

    def publish_gate() -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(checkout / "scripts" / "build-goh.sh")],
            cwd=checkout,
            capture_output=True,
            text=True,
            env=dict(os.environ, GOH_BUILD_PUBLISH_CHECK_ONLY="1"),
        )

    quiet = [commit_gate(), _push(repo, checkout, tmp_path), publish_gate()]
    assert [r.returncode for r in quiet] == [0, 0, 0], [r.stdout + r.stderr for r in quiet]
    assert not any("NOT committed" in r.stdout + r.stderr for r in quiet), quiet

    # One appended comment line: the smallest possible change to the gate every repo
    # runs, and the shape docs/SUPERSOTA.md §3 describes.
    victim = checkout / "checks" / "check_no_emoji.py"
    victim.write_text(victim.read_text(encoding="utf-8") + "# planted\n")

    # Named, by path, and the run still happened: the gate is not refused.
    for run, phrase in ((commit_gate(), "NOT committed"), (_push(repo, checkout, tmp_path), None)):
        out = run.stdout + run.stderr
        assert "check_no_emoji.py" in out, out
        if phrase is not None:
            assert phrase in out and run.returncode == 0, out
        else:
            assert run.returncode == 0 and "gating refs/heads/main" in out, (
                f"the push gate refused instead of naming — the 22-red-tests mistake: {out}"
            )
    # ...and the binary build is not affected at all: since C3 it builds an export of HEAD, so a
    # dirty checks/ is nothing it publishes (it used to be the one place that refused).
    built = publish_gate()
    assert built.returncode == 0 and "committed source at HEAD" in built.stdout, built.stdout
