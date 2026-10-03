"""Structural parity: `goh structural` agrees with `gates/structural.sh`.

Fixture repos per case; asserts identical exit codes and identical failing
step labels in full and staged modes. Either side's step order, labels,
config handling, or scope gating drifting goes red.

The last section is not parity but the OTHER end of the same contract: where
a config key comes FROM. A `.gatesrc` is a repo's declaration of how it is
gated, and a value arriving from anywhere else is ambient state the repo
never chose. The push gate is the producer of that state and `structural.sh`
is a consumer of it, so both are pinned here rather than in the two suites
that happen to be the first to notice.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STRUCTURAL = ROOT / "gates" / "structural.sh"
PUSH_GATE = ROOT / "gates" / "push_gate.sh"
FAIL_RE = re.compile(r"structural: (.+) failed")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


def make_repo(tmp_path: Path, files: dict[str, bytes]) -> Path:
    repo = tmp_path
    repo.mkdir(exist_ok=True)
    _git(repo, "init", "-q")
    for name, content in files.items():
        dest = repo / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    _git(repo, "add", "-A")
    return repo


def _failing(out: str, err: str) -> str | None:
    m = next((FAIL_RE.search(l) for l in (out + err).splitlines() if FAIL_RE.search(l)), None)
    return m.group(1) if m else None


def _hermetic_env(**overrides: str) -> dict[str, str]:
    """A child environment with NO inherited `GOH_*` in it.

    A fixture's answer must be the answer its own `.gatesrc` implies. Anything
    a parent happened to export is process state the fixture never chose, and
    every step's opt-in in this pipeline is a `GOH_*` presence test — so an
    inherited one silently adds a step the fixture never declared. That is how
    `push_gate.sh`'s `set -a` (below) reached this suite at all: the push gate
    exported this repo's `.gatesrc` into the export gate's environment, the
    export gate ran `tools/gate.sh --full`, and the pytest suite inherited the
    machine's real `GOH_SKILLS_ROOT` and `GOH_PYTHON_FORMATTED` while every
    fixture declared neither. Scrubbing here is the fixture half of that fix;
    `test_an_inherited_config_key_...` below is the gate half.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GOH_")}
    env.update(overrides)
    return env


def _run_bash(
    repo: Path, staged: bool, extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """The PYTHON pipeline. structural.sh execs the native binary when one is
    around, so this side is pinned to the checkers with GOH_NO_NATIVE — the
    comparison is native vs Python, never native vs itself."""
    env = _hermetic_env(GOH_NO_NATIVE="1", GOH_DIR=str(ROOT), **(extra or {}))
    cmd = ["bash", str(STRUCTURAL), "--staged"] if staged else ["bash", str(STRUCTURAL)]
    return subprocess.run(cmd, cwd=repo, capture_output=True, text=True, env=env)


def run_bash(repo: Path, staged: bool) -> tuple[int, str | None]:
    r = _run_bash(repo, staged)
    return r.returncode, _failing(r.stdout, r.stderr)


def _run_goh(
    goh: Path, repo: Path, staged: bool, extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    env = _hermetic_env(GOH_DIR=str(ROOT), **(extra or {}))
    cmd = [str(goh), "structural", "--staged"] if staged else [str(goh), "structural"]
    return subprocess.run(cmd, cwd=repo, capture_output=True, text=True, env=env)


def run_goh(goh: Path, repo: Path, staged: bool) -> tuple[int, str | None]:
    r = _run_goh(goh, repo, staged)
    return r.returncode, _failing(r.stdout, r.stderr)


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
STEP_RE = re.compile(r"^· (.+)$")


def announced_steps(out: str) -> list[str]:
    """The step labels a run ANNOUNCED, in order — `· label`, both tiers.

    A step announces before it runs, so this is the inventory of what the
    pipeline ATTEMPTED, which is the thing a `(rc, failing label)` comparison
    cannot see: two tiers that both skip a step agree perfectly. See
    `test_both_tiers_run_the_same_steps`.
    """
    steps = []
    for line in out.splitlines():
        match = STEP_RE.match(ANSI_RE.sub("", line).rstrip())
        if match:
            steps.append(match.group(1))
    return steps


GATESRC = b"GOH_MAX_LINES=10\n"

FULL_CASES: dict[str, dict[str, bytes]] = {
    "clean": {".gatesrc": GATESRC, "a.py": b"x = 1\n"},
    "emoji": {".gatesrc": GATESRC, "a.py": f"x = 1  # {chr(0x1F389)}\n".encode()},
    "marker": {".gatesrc": GATESRC, "a.py": b"x = 1\n<<<<<<< ours\n"},
    # The python-format step, red and green. Without a red case this step could
    # be absent from ONE tier and the parity test would still pass, because two
    # tiers that both skip a step also agree. That is exactly how the shell side
    # ran it in staged mode while the native side skipped it, and the only thing
    # that noticed was this suite.
    "pyfmt_green": {".gatesrc": GATESRC, "a.py": b"import os\n\nos.environ.get('X')\n"},
    "pyfmt_red": {".gatesrc": GATESRC, "a.py": b"import os\nos.environ.get('X')\n"},
    "over_cap": {".gatesrc": GATESRC, "a.py": b"x = 1\n" * 20},
    "no_gatesrc": {"a.py": b"x = 1\n"},
    "exclude_warn": {
        ".gatesrc": b"GOH_MAX_LINES=10\nGOH_LINE_EXCLUDE='a.py'\n",
        "a.py": b"x = 1\n",
    },
    "shell_fail": {".gatesrc": GATESRC, "bad.sh": b"if then\n"},
    # The opt-in home-paths step, both outcomes -- an opt-in step the table
    # never turns on is one the parity proof never sees.
    "home_path_red": {
        ".gatesrc": b"GOH_MAX_LINES=10\nGOH_NO_HOME_PATHS=1\n",
        "NOTES.md": b"run from ~/Projects/x\n",
    },
    "home_path_green": {
        ".gatesrc": b"GOH_MAX_LINES=10\nGOH_NO_HOME_PATHS=1\n",
        "NOTES.md": b"run from the repo root\n",
    },
    # The opt-in kill-by-name step, both outcomes. The command name is built from
    # parts because that gate reads this file too.
    #
    # CONCATENATED WITH `+`, NOT with adjacent literals, and that is the whole
    # point of this note. The original was two adjacent bytes literals
    # (`b'...["p' b'kill"...]'`), which the first `ruff format` run in this repo
    # MERGED into one -- putting the literal text `pkill` back into this file and
    # turning the gate red on a fixture that kills nothing. An evasion that
    # depends on the formatter leaving your source alone is not an evasion, and
    # the formatter is not going to be told to leave it alone.
    "kill_by_name_red": {
        ".gatesrc": b"GOH_MAX_LINES=10\nGOH_NO_KILL_BY_NAME=1\n",
        # The payload is written to a fixture repo and must itself be
        # ruff-formatted, or the python-format step -- which runs BEFORE this
        # one -- fails first and this case stops testing what it is for. The
        # blank line after the import is what `ruff format` wants, and the
        # command name is still built with `+` so this file never spells it.
        "run.py": b'import subprocess\n\nsubprocess.run(["p' + b'kill", "-f", "helper"])\n',
    },
    "kill_by_name_green": {
        ".gatesrc": b"GOH_MAX_LINES=10\nGOH_NO_KILL_BY_NAME=1\n",
        "run.py": b"import os\nos.killpg(os.getpgid(0), 15)\n",
    },
    # The ceiling steps, every branch -- an exempt-over-cap file with and
    # without its ceiling, growth past the ceiling, and a dangling baseline.
    "ceiling_green": {
        ".gatesrc": b"GOH_MAX_LINES=10\nGOH_LINE_EXCLUDE='a.py'\nGOH_LINE_BASELINE='base.txt'\n",
        "a.py": b"x = 1\n" * 15,
        "base.txt": b"15\ta.py\n",
    },
    "ceiling_no_ceiling": {
        ".gatesrc": b"GOH_MAX_LINES=10\nGOH_LINE_EXCLUDE='a.py'\nGOH_LINE_BASELINE='base.txt'\n",
        "a.py": b"x = 1\n" * 15,
        "base.txt": b"15\tother.py\n",
    },
    "ceiling_over": {
        ".gatesrc": b"GOH_MAX_LINES=10\nGOH_LINE_EXCLUDE='a.py'\nGOH_LINE_BASELINE='base.txt'\n",
        "a.py": b"x = 1\n" * 16,
        "base.txt": b"15\ta.py\n",
    },
    "ceiling_missing": {
        ".gatesrc": b"GOH_MAX_LINES=10\nGOH_LINE_EXCLUDE='a.py'\nGOH_LINE_BASELINE='missing.txt'\n",
        "a.py": b"x = 1\n" * 15,
    },
    # The two steps added with the 2026-10-01 audit, BOTH outcomes each.
    # structural.sh EXECs the native binary, so a step present in one pipeline
    # and not the other runs in exactly one of them -- and a case the parity
    # table never exercises is the only place that drift is visible.
    "md_link_red": {".gatesrc": GATESRC, "README.md": b"[x](gone.md)\n"},
    "md_link_green": {
        ".gatesrc": GATESRC,
        "README.md": b"[x](there.md)\n",
        "there.md": b"# here\n",
    },
    "lock_red": {
        ".gatesrc": GATESRC,
        "Cargo.toml": b'[package]\nname = "app"\nversion = "1.2.3"\n',
        "Cargo.lock": b'[[package]]\nname = "app"\nversion = "1.2.2"\n',
    },
    "lock_green": {
        ".gatesrc": GATESRC,
        "Cargo.toml": b'[package]\nname = "app"\nversion = "1.2.3"\n',
        "Cargo.lock": b'[[package]]\nname = "app"\nversion = "1.2.3"\n',
    },
}


def test_the_kill_by_name_red_case_is_red_in_both_tiers(goh: Path, tmp_path: Path) -> None:
    # Two tiers that both skip the step also "agree"; the red case must fail it.
    repo = make_repo(tmp_path, FULL_CASES["kill_by_name_red"])
    for rc, step in (run_goh(goh, repo, staged=False), run_bash(repo, staged=False)):
        assert rc != 0 and step and "kill by name" in step, (rc, step)


@pytest.mark.parametrize("name", sorted(FULL_CASES))
def test_full_mode_agrees(goh: Path, tmp_path: Path, name: str) -> None:
    repo = make_repo(tmp_path, FULL_CASES[name])
    assert run_goh(goh, repo, staged=False) == run_bash(repo, staged=False), name


def test_staged_marker_agrees(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {".gatesrc": GATESRC, "a.py": b"x = 1\n"})
    (repo / "a.py").write_bytes(b"x = 1\n<<<<<<< ours\n")
    _git(repo, "add", "a.py")
    assert run_goh(goh, repo, staged=True) == run_bash(repo, staged=True)


def test_staged_ignores_unstaged_dirt(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {".gatesrc": GATESRC, "a.py": b"x = 1\n"})
    (repo / "a.py").write_bytes(b"x = 1\n<<<<<<< ours\n")
    assert run_goh(goh, repo, staged=True) == run_bash(repo, staged=True) == (0, None)


def test_structural_sh_execs_the_native_binary_when_told_where_it_is(goh, tmp_path):
    """The hooks call structural.sh; with a binary the Python never runs. A
    bad emoji proves which side answered: the native step label is the same,
    but the Python fallback notice must be absent."""
    repo = make_repo(tmp_path, {"a.md": "ok\n".encode(), ".gatesrc": GATESRC})

    env = _hermetic_env(GOH_BIN=str(goh), GOH_DIR=str(ROOT))
    r = subprocess.run(["bash", str(STRUCTURAL)], cwd=repo, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "goh binary not built" not in r.stderr
    # An explicit pointer at nothing is reported, once, and the Python runs.
    env = _hermetic_env(GOH_BIN="/nonexistent/goh", GOH_DIR=str(ROOT))
    env.pop("GOH_NO_NATIVE", None)
    r = subprocess.run(["bash", str(STRUCTURAL)], cwd=repo, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stderr.count("GOH_BIN=/nonexistent/goh is not an executable") == 1, r.stderr


# ── Where a config key comes FROM ─────────────────────────────────────────────
#
# A `.gatesrc` is a repo stating how it is gated. A `GOH_*` value arriving from
# anywhere else is process state the repo never chose, and every optional step
# in this pipeline is decided by the PRESENCE of one — so an inherited value
# does not fail the gate, it silently adds a step to it. Measured 2026-10-02:
# `gates/push_gate.sh` sourced `.gatesrc` under `set -a`, which exported this
# repo's real keys into the environment of the export gate; the export gate ran
# `tools/gate.sh --full`; and the pytest suite that runs there inherited
# `GOH_SKILLS_ROOT=~/.claude/skills` and `GOH_PYTHON_FORMATTED=1` while every
# one of its fixture repos declared neither. Seven tests went red on a tree that
# was green, and the push gate refused pushes that passed locally.

# Every value here turns a step ON, exempts a path, or redirects a corpus. One
# key the repo declared would be overridden by the file anyway, so a
# discriminating value has to be for a key the repo does NOT declare.
HOSTILE = {
    "GOH_PYTHON_FORMATTED": "1",
    "GOH_NO_HOME_PATHS": "1",
    "GOH_NO_KILL_BY_NAME": "1",
    "GOH_SKILLS_CORPUS": "1",
    "GOH_SKILLS_MAX_WORDS": "1",
    "GOH_EXCLUDE": ".*",
    "GOH_LINE_EXCLUDE": ".*",
    "GOH_ALLOW": "Q",
}


# The keys docs/config.md documents in its STRUCTURAL table that are NOT pipeline
# configuration, each with why: they choose WHICH binary runs, or they belong to
# another script entirely. "Absent from the list" is only safe while somebody has
# said so here, which is what the list below is.
NOT_PIPELINE_CONFIG = {
    "GOH_BIN": "which binary runs the pipeline, not which checks",
    "GOH_NO_NATIVE": "the same question, answered 'neither'",
    "GOH_SKIP_BUILD": "install.sh",
    "GOH_BUILD_DIRTY": "scripts/build-goh.sh",
    "GOH_EXPORT_KEEP": "which ignored files the pre-push export carries",
    "GOH_PUSH_WORKTREES": "where that export is checked out",
    "GOH_PUSH_LOGS": "where its output is kept",
    "GOH_TAG_VERSION_SOURCES": "the pre-push tag check, which is not a step",
}

DROP_LIST_RE = re.compile(r'_goh_config_keys="([^"]+)"', re.S)


def test_every_documented_structural_key_is_dropped_from_the_environment() -> None:
    """The drop list in structural.sh, pinned against docs/config.md.

    `structural.sh` drops its inherited config keys by NAME, so the list and the
    documented key set are two things that can drift, and the drift is silent in
    the direction that hurts: a new `.gatesrc` key added to structural.sh, to
    config.md and to no list is an ambient value that silently enables it again.
    Checked both ways -- a key in the list that no longer exists is a name that
    has gone stale and will hide the next gate added under it.
    """
    doc = (ROOT / "docs" / "config.md").read_text(encoding="utf-8")
    section = doc.split("## Structural", 1)[1].split("\n## ", 1)[0]
    documented = set(re.findall(r"`(GOH_[A-Z][A-Z_]*)`", section))
    match = DROP_LIST_RE.search((ROOT / "gates" / "structural.sh").read_text(encoding="utf-8"))
    assert match, "structural.sh no longer drops its inherited config keys at all"
    dropped = set(match.group(1).split())

    not_config = documented & set(NOT_PIPELINE_CONFIG)
    pipeline = documented - set(NOT_PIPELINE_CONFIG)
    assert not (dropped & not_config), (
        f"structural.sh drops {sorted(dropped & not_config)}, which config.md documents as "
        "something other than pipeline configuration -- say why in NOT_PIPELINE_CONFIG"
    )
    assert dropped == pipeline, (
        "structural.sh's drop list and docs/config.md's structural key set disagree.\n"
        f"  documented, never dropped: {sorted(pipeline - dropped)}\n"
        f"  dropped, never documented: {sorted(dropped - pipeline)}\n"
        "An undocumented value in the environment would enable that step again."
    )


@pytest.mark.parametrize("tier", ["python", "native"])
def test_an_inherited_config_key_cannot_enable_a_step_this_repo_never_declared(
    goh: Path, tmp_path: Path, tier: str
) -> None:
    """A hostile ambient `GOH_*` must change NOTHING about what the gate runs.

    Both directions are the same defect. An inherited opt-in runs a check the
    repo never asked for — a false red, and on a fixture, a verdict about
    files that fixture does not contain. An inherited `GOH_EXCLUDE=.*` hides a
    real violation — a false green, which is the direction nobody notices.
    """
    repo = make_repo(tmp_path, FULL_CASES["clean"])
    runner = (
        (lambda r: _run_bash(repo, False, r))
        if tier == "python"
        else (lambda r: _run_goh(goh, repo, False, r))
    )
    quiet, hostile = runner({}), runner(HOSTILE)
    ran_quietly, ran_hostile = announced_steps(quiet.stdout), announced_steps(hostile.stdout)
    assert ran_hostile == ran_quietly, (
        f"ambient GOH_* changed which steps the {tier} tier ran:\n"
        f"  quiet:   {ran_quietly}\n  hostile: {ran_hostile}\n" + hostile.stdout + hostile.stderr
    )
    assert hostile.returncode == quiet.returncode, hostile.stdout + hostile.stderr


def test_an_inherited_exemption_does_not_hide_a_violation(tmp_path: Path) -> None:
    """`GOH_EXCLUDE=.*` in the environment is the sharp end of the same class:
    exempt everything, and the emoji gate reports a clean tree."""
    repo = make_repo(tmp_path, FULL_CASES["emoji"])
    hostile = _run_bash(repo, False, {"GOH_EXCLUDE": ".*"})
    assert hostile.returncode != 0, hostile.stdout + hostile.stderr
    assert "emoji" in (hostile.stdout + hostile.stderr).lower()


def test_the_push_gate_does_not_hand_the_export_gate_this_repos_gatesrc(
    tmp_path: Path,
) -> None:
    """The producer, end to end: a repo's `.gatesrc` keys stay in the push gate.

    The fixture's `tools/gate.sh` IS the export gate, and it records what it was
    given. `push_gate.sh` reads one key out of `.gatesrc` — `GOH_EXPORT_KEEP` —
    and hands the tag check its environment in a subshell of its own; nothing
    else escapes into the gate the push actually runs. Before that, `set -a`
    around the source exported every key, so this report was the pushing repo's
    real configuration and the gate run on it inherited it.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "tools").mkdir()
    (repo / "tools" / "gate.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "for k in GOH_MAX_LINES GOH_SKILLS_CORPUS GOH_SKILLS_ROOT "
        "GOH_PYTHON_FORMATTED GOH_NO_KILL_BY_NAME GOH_LINE_BASELINE; do\n"
        '  printf \'%s=%s\\n\' "$k" "${!k-UNSET}" >> "$GATE_REPORT"\n'
        "done\n"
    )
    (repo / "VERSION").write_text("1.0.0\n")
    (repo / ".gatesrc").write_text(
        "GOH_MAX_LINES=500\nGOH_SKILLS_CORPUS=1\nGOH_NO_KILL_BY_NAME=1\n"
        "GOH_SKILLS_ROOT=/somewhere/else\nGOH_PYTHON_FORMATTED=1\n"
        "GOH_LINE_BASELINE=base.txt\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one")
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()

    report = tmp_path / "report.txt"
    env = _hermetic_env(
        GATE_REPORT=str(report), GOH_DIR=str(ROOT), GOH_PUSH_LOGS=str(tmp_path / "logs")
    )
    proc = subprocess.run(
        ["bash", str(PUSH_GATE)],
        cwd=repo,
        env=env,
        text=True,
        input=f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n",
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    seen = dict(line.split("=", 1) for line in report.read_text().splitlines() if "=" in line)
    leaked = {k: v for k, v in seen.items() if v != "UNSET"}
    assert not leaked, f"the export gate inherited this repo's configuration: {leaked}"


def test_goh_is_built_once_per_session(goh: Path, goh_build_count: int) -> None:
    """The class behind 23 errors in one pre-push run (2026-09-22): a session fixture under xdist
    runs once PER WORKER, and each worker's `cargo build` re-linked the binary another worker was
    copying. One build per session is the invariant; more than one is the race coming back."""
    assert goh.exists()
    assert goh_build_count == 1, f"goh was built {goh_build_count} times in one session"
