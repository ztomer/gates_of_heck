"""Structural parity: `goh structural` agrees with `gates/structural.sh`.

Fixture repos per case; identical exit codes and failing step labels in full and
staged modes, so step order, labels, config handling or scope gating drifting goes
red. The last two sections are the OTHER end of the same contract: where a config
key comes FROM, and what happens when the gate's own source is uncommitted — a
gate's inputs, and its source itself, are inputs.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STRUCTURAL = ROOT / "gates" / "structural.sh"
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
    """A child environment with NO inherited `GOH_*` in it. Every step's opt-in here is a
    `GOH_*` PRESENCE test, so an inherited value adds a step the fixture never declared —
    which is how `push_gate.sh`'s `set -a` reached this suite."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GOH_")}
    env.update(overrides)
    return env


def _run_bash(
    repo: Path, staged: bool, extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """The PYTHON pipeline. structural.sh execs the native binary when one is around,
    so this side is pinned to the checkers with GOH_NO_NATIVE: native vs Python,
    never native vs itself."""
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
    """The step labels a run ANNOUNCED, in order — `· label`, both tiers. A step
    announces before it runs, so this is what the pipeline ATTEMPTED: the thing a
    `(rc, failing label)` comparison cannot see, two tiers that both SKIP agreeing."""
    steps = []
    for line in out.splitlines():
        match = STEP_RE.match(ANSI_RE.sub("", line).rstrip())
        if match:
            steps.append(match.group(1))
    return steps


GATESRC = b"GOH_MAX_LINES=10\n"
# A `.gatesrc` that opts into the prose-claim gate. 500 rather than 10 because the
# claim document below is three lines and the cap has to be out of its way.
CLAIM_SRC = b"GOH_MAX_LINES=500\nGOH_CLAIM_DERIVATION=1\n"

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
    # The prose-claim step, BOTH outcomes. It is opt-in, and opt-in means the
    # fixture has to DECLARE it: a case the parity table never turns on is a step
    # both tiers skip, which is the one situation in which they agree perfectly.
    #
    # The `.gatesrc` here carries the key, and the document carries a claim -- and
    # they are the same fixture's two halves on purpose. That step REFUSES a tree
    # that declares the convention and has no marked claim in it, which is the
    # empty-scope rule, so a fixture with the key and no claim exercises the refusal
    # rather than the gate.
    "claim_red": {
        ".gatesrc": CLAIM_SRC,
        "doc.md": b"# t\n\nclaim: 9 lines in doc.md\n",
    },
    "claim_green": {
        ".gatesrc": CLAIM_SRC,
        "doc.md": b"# t\n\nclaim: 3 lines in doc.md\n",
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


# A repo that turns on EVERY optional gate and trips nothing, so both tiers run
# the WHOLE pipeline to the end and their announced steps are the inventories.
# Every optional key `.gatesrc` accepts, a version baseline (so the provenance
# step is not skipped for want of a file), and a length exemption with a ceiling
# (so both ceiling steps run). SETS, not sequences: the two tiers order the same
# steps differently — structural.sh runs the length cap early and the delegated
# checks after it, main.rs runs its own scanners first — and that is a separate
# question from this one.
_SKILL = b"---\nname: skill-%d\ndescription: does a thing worth triggering on.\n---\n\n# skill-%d\n\nbody\n"
INVENTORY_CASE: dict[str, bytes] = {
    ".gatesrc": (
        b"GOH_MAX_LINES=500\nGOH_SKILLS_CORPUS=1\nGOH_NO_HOME_PATHS=1\nGOH_NO_KILL_BY_NAME=1\n"
        b"GOH_PYTHON_FORMATTED=1\nGOH_CLAIM_DERIVATION=1\nGOH_LINE_EXCLUDE='big.py'\n"
        b"GOH_LINE_BASELINE='base.txt'\n"
    ),
    ".gates-version-baseline.json": b"[]\n",
    "base.txt": b"600\tbig.py\n",
    "big.py": b"".join(b"# %d\n" % i for i in range(600)),
    "README.md": b"# ok\n\nclaim: 3 lines in README.md\n",
    "a.py": b"x = 1\n",
    **{f"skill-{i}/SKILL.md": _SKILL % (i, i) for i in range(6)},
}


def test_both_tiers_run_the_same_steps(goh: Path, tmp_path: Path) -> None:
    """The step INVENTORY is compared, which `(rc, failing label)` never was.

    This suite's module docstring claimed to compare step inventories and did
    not: it compared a return code and the label of whichever step failed first,
    so a step present in one tier and absent from the other was invisible unless
    a fixture happened to make it fail. Two tiers that both SKIP a step agree
    perfectly — that is the whole mechanism of the miss. The measured
    consequence is in docs/SUPERSOTA.md R4: a step that does not run prints
    exactly what a step that passes prints.
    """
    repo = make_repo(tmp_path, INVENTORY_CASE)
    bash_run, goh_run = _run_bash(repo, False), _run_goh(goh, repo, False)
    assert bash_run.returncode == 0, bash_run.stdout + bash_run.stderr
    assert goh_run.returncode == 0, goh_run.stdout + goh_run.stderr
    in_bash, in_goh = set(announced_steps(bash_run.stdout)), set(announced_steps(goh_run.stdout))
    # Named so the diff says WHICH step, and not merely that two lists differ.
    only_bash, only_goh = sorted(in_bash - in_goh), sorted(in_goh - in_bash)
    assert not (only_bash or only_goh), (
        f"the tiers run different steps.\n  python only: {only_bash}\n  native only: {only_goh}\n"
        f"  python: {sorted(in_bash)}\n  native: {sorted(in_goh)}\n" + goh_run.stdout
    )
    # An empty comparison proves nothing, so the inventory is pinned too: a step
    # added to ONE tier and not the other is the defect, and a step added to
    # neither is a step nobody runs. 16 -> 17 on 2026-10-03 for
    # `no unreaped spawns in tests`; 17 -> 18 on 2026-10-04 for
    # `prose claims are derived`; 18 -> 19 on 2026-10-05 for
    # `no credential in a git remote URL`.
    assert len(in_bash) == 19, f"the pipeline's step inventory changed: {sorted(in_bash)}"


def test_both_tiers_time_the_same_steps(goh: Path, tmp_path: Path) -> None:
    """P0's instrument, per tier: every announced step leaves exactly one timing line, so a
    native-vs-Python comparison is over the same labels and no step is counted twice (the
    native tier's delegated steps run through bounded_run.py, which also records)."""
    import json

    labels = {}
    for tier in ("python", "native"):
        repo = make_repo(tmp_path / tier, INVENTORY_CASE)
        out = tmp_path / f"{tier}.jsonl"
        extra = {"GOH_TIMINGS": str(out)}
        r = _run_bash(repo, False, extra) if tier == "python" else _run_goh(goh, repo, False, extra)
        assert r.returncode == 0, r.stdout + r.stderr
        rows = [json.loads(line) for line in out.read_text().splitlines()]
        announced = sorted(s.split(" (\u2264")[0] for s in announced_steps(r.stdout))
        assert sorted(row["label"] for row in rows) == announced, (tier, rows)
        labels[tier] = set(announced)
    assert labels["python"] == labels["native"]


def test_a_failing_step_names_the_checker_and_the_docs(goh: Path, tmp_path: Path) -> None:
    """R8: the failing tier must be able to be FOUND from the message it gives.

    This printed `command: python3 check_md_links.py` — a bare filename — from the
    tier that actually runs in every repo, while the Python tier printed an
    absolute path. A session hitting a surprising house gate had no way to find
    the file from what it was told, and neither tier said where the RULE is
    written. Both facts are now in the failure: the path the OS was handed, and
    the docs page that documents the gate.
    """
    repo = make_repo(tmp_path, FULL_CASES["md_link_red"])
    out = (lambda r: r.stdout + r.stderr)(_run_goh(goh, repo, False))
    assert str(ROOT / "checks" / "check_md_links.py") in out, out
    assert str(ROOT / "docs" / "map.md") in out and str(ROOT / "docs" / "config.md") in out, out


def test_staged_marker_agrees(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {".gatesrc": GATESRC, "a.py": b"x = 1\n"})
    (repo / "a.py").write_bytes(b"x = 1\n<<<<<<< ours\n")
    _git(repo, "add", "a.py")
    assert run_goh(goh, repo, staged=True) == run_bash(repo, staged=True)


FORMATTED_SRC = b"GOH_MAX_LINES=500\nGOH_PYTHON_FORMATTED=1\n"


@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff is not installed")
@pytest.mark.parametrize(
    ("staged_bytes", "expected"),
    [(b"x = {  'a':1 }\n", (1, "python is ruff-formatted (staged)")), (b"x = 1\n", (0, None))],
)
def test_staged_python_format_agrees(goh: Path, tmp_path: Path, staged_bytes, expected) -> None:
    """Pre-commit was weaker than pre-push for this check (v0.20.0's refused push):
    both tiers now run it over the staged blobs, and agree on the verdict."""
    repo = make_repo(tmp_path, {".gatesrc": FORMATTED_SRC, "a.py": staged_bytes})
    py = _run_bash(repo, staged=True)
    steps = announced_steps(py.stdout + py.stderr)
    assert any(s.startswith("python is ruff-formatted (staged)") for s in steps), steps
    assert run_goh(goh, repo, staged=True) == (py.returncode, _failing(py.stdout, py.stderr))
    assert (py.returncode, _failing(py.stdout, py.stderr)) == expected


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
# A `.gatesrc` is a repo stating how it is gated; a `GOH_*` from anywhere else is process
# state it never chose, and every optional step is decided by the PRESENCE of one — so an
# inherited value does not fail the gate, it silently ADDS a step. Measured 2026-10-02: `push_gate.sh` sourced `.gatesrc` under `set -a`, the export gate ran
# `tools/gate.sh --full`, and the pytest suite there inherited this repo's real
# `GOH_SKILLS_ROOT` / `GOH_PYTHON_FORMATTED` while every fixture declared neither:
# seven tests red on a green tree. Each value below turns a step ON, exempts a path or
# redirects a corpus, and a declared key is overridden by its own file, so only an
# undeclared one can discriminate.
HOSTILE = {
    "GOH_PYTHON_FORMATTED": "1",
    "GOH_NO_HOME_PATHS": "1",
    "GOH_NO_KILL_BY_NAME": "1",
    "GOH_CLAIM_DERIVATION": "1",
    "GOH_SKILLS_CORPUS": "1",
    "GOH_SKILLS_MAX_WORDS": "1",
    "GOH_EXCLUDE": ".*",
    "GOH_LINE_EXCLUDE": ".*",
    "GOH_ALLOW": "Q",
}


@pytest.mark.parametrize("tier", ["python", "native"])
def test_an_inherited_config_key_cannot_enable_a_step_this_repo_never_declared(
    goh: Path, tmp_path: Path, tier: str
) -> None:
    """A hostile ambient `GOH_*` must change NOTHING about what the gate runs: an inherited
    opt-in runs a check the repo never asked for (false red), and an inherited
    `GOH_EXCLUDE=.*` hides one (false green — the direction nobody sees)."""
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
    """`GOH_EXCLUDE=.*` is the sharp end of that class: exempt everything, and the
    emoji gate reports a clean tree."""
    repo = make_repo(tmp_path, FULL_CASES["emoji"])
    hostile = _run_bash(repo, False, {"GOH_EXCLUDE": ".*"})
    assert hostile.returncode != 0, hostile.stdout + hostile.stderr
    assert "emoji" in (hostile.stdout + hostile.stderr).lower()


def test_goh_is_built_once_per_session(goh: Path, goh_build_count: int) -> None:
    """The class behind 23 errors in one pre-push run (2026-09-22): a session fixture under xdist
    runs once PER WORKER, and each worker's `cargo build` re-linked the binary another worker was
    copying. One build per session is the invariant; more than one is the race coming back."""
    assert goh.exists()
    assert goh_build_count == 1, f"goh was built {goh_build_count} times in one session"
