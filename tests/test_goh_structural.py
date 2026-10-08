"""The structural pipeline: `goh structural`, and `gates/structural.sh` that hooks call.

Fixture repos per case, each with its EXPECTED exit code and failing step label in
full and staged modes, so step order, labels, config handling or scope gating
drifting goes red. Until Phase N3 this compared the native tier against the Python
one; the verdicts here are the ones both tiers gave on 2026-10-06 (the last day
there were two), frozen. Agreement between tiers could not see a case both tiers
SKIPPED -- the python-format "red" case was green in both for exactly that reason,
and an expected verdict caught it. The last sections are the other end of the same
contract: where a config key comes FROM, and which binary answers.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import hermetic_env
from _fast_git import fast_init  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STRUCTURAL = ROOT / "gates" / "structural.sh"
FAIL_RE = re.compile(r"structural: (.+) failed")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


def make_repo(tmp_path: Path, files: dict[str, bytes]) -> Path:
    repo = tmp_path
    repo.mkdir(exist_ok=True)
    fast_init(repo)
    for name, content in files.items():
        dest = repo / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    _git(repo, "add", "-A")
    return repo


def _failing(out: str, err: str) -> str | None:
    m = next((FAIL_RE.search(l) for l in (out + err).splitlines() if FAIL_RE.search(l)), None)
    return m.group(1) if m else None


def _run_bash(
    repo: Path, staged: bool, extra: dict[str, str] | None = None, goh: Path | None = None
) -> subprocess.CompletedProcess:
    """The HOOK's entry point: structural.sh, which resolves the binary and execs it."""
    env = hermetic_env(GOH_DIR=str(ROOT), **(extra or {}))
    if goh is not None:
        env["GOH_BIN"] = str(goh)
    cmd = ["bash", str(STRUCTURAL), "--staged"] if staged else ["bash", str(STRUCTURAL)]
    return subprocess.run(cmd, cwd=repo, capture_output=True, text=True, env=env)


def _run_goh(
    goh: Path, repo: Path, staged: bool, extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    env = hermetic_env(GOH_DIR=str(ROOT), **(extra or {}))
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
PYFMT_SRC = b"GOH_MAX_LINES=10\nGOH_PYTHON_FORMATTED=1\n"
# A `.gatesrc` that opts into the prose-claim gate. 500 rather than 10 because the
# claim document below is three lines and the cap has to be out of its way.
CLAIM_SRC = b"GOH_MAX_LINES=500\nGOH_CLAIM_DERIVATION=1\n"

RC_RULES = (
    b'[[rule]]\nname = "placement"\nwhy = "a relaunch can move windows"\nfiles = ["tools/*.py"]\n'
    b'calls = ["relaunch"]\nmust_call = ["window_placement.verify"]\n'
)

FULL_CASES: dict[str, dict[str, bytes]] = {
    "clean": {".gatesrc": GATESRC, "a.py": b"x = 1\n"},
    "emoji": {".gatesrc": GATESRC, "a.py": f"x = 1  # {chr(0x1F389)}\n".encode()},
    "marker": {".gatesrc": GATESRC, "a.py": b"x = 1\n<<<<<<< ours\n"},
    # The python-format step, red and green. Without a red case this step could
    # be absent from ONE tier and the parity test would still pass, because two
    # tiers that both skip a step also agree. That is exactly how the shell side
    # ran it in staged mode while the native side skipped it, and the only thing
    # that noticed was this suite.
    # The step is OPT-IN: until 2026-10-06 both cases declared only GATESRC, so the step was
    # skipped, both tiers agreed on (0, None), and the "red" case was green -- the miss this
    # comment describes, inside the fixture written to catch it. Expected verdicts (below)
    # instead of tier agreement are what exposed it.
    "pyfmt_green": {".gatesrc": PYFMT_SRC, "a.py": b'import os\n\nos.environ.get("X")\n'},
    "pyfmt_red": {".gatesrc": PYFMT_SRC, "a.py": b'import os\nos.environ.get("X")\n'},
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
    # early-exit-pipe runs in EVERY repo: ratchet closed (opt-in) an old racy pipe fails at full
    # scope and the fixed shape passes; ratchet open it is named, not failed (`_named`).
    "early_exit_pipe_red": {
        ".gatesrc": b"GOH_MAX_LINES=10\nGOH_NO_EARLY_EXIT_PIPE=1\n",
        "a.sh": b"#!/bin/bash\nset -euo pipefail\nls | grep -q x\n",
    },
    "early_exit_pipe_green": {
        ".gatesrc": b"GOH_MAX_LINES=10\nGOH_NO_EARLY_EXIT_PIPE=1\n",
        "a.sh": b'#!/bin/bash\nset -euo pipefail\nout="$(ls)"\ngrep -q x <<<"$out"\n',
    },
    "early_exit_pipe_named": {
        ".gatesrc": GATESRC,
        "a.sh": b"#!/bin/bash\nset -euo pipefail\nls | grep -q x\n",
    },
    # dead-after-exec, a hard gate in every repo: code after `exec CMD` fails; the parse-guard passes.
    "dead_exec_red": {".gatesrc": GATESRC, "a.sh": b"#!/bin/sh\nexec true\necho never\n"},
    "dead_exec_green": {".gatesrc": GATESRC, "a.sh": b"#!/bin/sh\n{\nexec true\nexit\n}\n"},
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
    # `goh requires-call`, opt-in by naming its rules file: a tool that relaunches must judge.
    "requires_call_red": {
        ".gatesrc": GATESRC + b"GOH_REQUIRES_CALL='rc.toml'\n",
        "rc.toml": RC_RULES,
        "tools/probe.py": b"def run():\n    relaunch()\n",
    },
    "requires_call_green": {
        ".gatesrc": GATESRC + b"GOH_REQUIRES_CALL='rc.toml'\n",
        "rc.toml": RC_RULES,
        "tools/probe.py": b"import window_placement as p\n\n\ndef run():\n    relaunch()\n    p.verify()\n",
    },
}


# Every case's verdict, `(exit code, first failing step)`. A red case that is not red here is a
# step its fixture never turned on.
EXPECTED: dict[str, tuple[int, str | None]] = {
    "ceiling_green": (0, None),
    "ceiling_missing": (1, "line-cap exemptions carry a ceiling"),
    "ceiling_no_ceiling": (1, "line-cap exemptions carry a ceiling"),
    "ceiling_over": (1, "cap-exempt files within their ceilings"),
    "claim_green": (0, None),
    "claim_red": (1, "prose claims are derived"),
    "requires_call_green": (0, None),
    "requires_call_red": (1, "a file that calls X calls Y"),
    "clean": (0, None),
    "dead_exec_green": (0, None),
    "dead_exec_red": (1, "no code after exec"),
    "early_exit_pipe_green": (0, None),
    "early_exit_pipe_named": (0, None),
    "early_exit_pipe_red": (1, "no early-exit pipe under pipefail"),
    "emoji": (1, "no disallowed emoji"),
    "exclude_warn": (0, None),
    "home_path_green": (0, None),
    "home_path_red": (1, "no hard-coded home paths"),
    "kill_by_name_green": (0, None),
    "kill_by_name_red": (1, "no process kill by name"),
    "lock_green": (0, None),
    "lock_red": (1, "Cargo.lock matches its manifests"),
    "marker": (1, "no conflict markers"),
    "md_link_green": (0, None),
    "md_link_red": (1, "markdown links resolve"),
    "no_gatesrc": (0, None),
    "over_cap": (1, "file length <= 10"),
    "pyfmt_green": (0, None),
    "pyfmt_red": (1, "python is ruff-formatted"),
    "shell_fail": (1, "shell lint"),
}


def test_every_case_has_an_expected_verdict_and_every_red_case_is_red() -> None:
    assert set(EXPECTED) == set(FULL_CASES)
    for name, (rc, step) in EXPECTED.items():
        if name.endswith(("_red", "_missing", "_over", "_no_ceiling", "_fail")):
            assert rc == 1 and step, name


@pytest.mark.parametrize("name", sorted(FULL_CASES))
def test_full_mode_gives_the_expected_verdict(goh: Path, tmp_path: Path, name: str) -> None:
    repo = make_repo(tmp_path, FULL_CASES[name])
    assert run_goh(goh, repo, staged=False) == EXPECTED[name], name


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
        b"GOH_PYTHON_FORMATTED=1\nGOH_CLAIM_DERIVATION=1\nGOH_NO_EARLY_EXIT_PIPE=1\n"
        b"GOH_LINE_EXCLUDE='big.py'\n"
        b"GOH_LINE_BASELINE='base.txt'\nGOH_REQUIRES_CALL='rc.toml'\n"
    ),
    "rc.toml": b'[[rule]]\nname = "b"\nwhy = "y"\nfiles = ["b.py"]\nmust_call = ["print"]\n',
    "b.py": b"print(1)\n",
    ".gates-version-baseline.json": b"[]\n",
    "base.txt": b"600\tbig.py\n",
    "big.py": b"".join(b"# %d\n" % i for i in range(600)),
    "README.md": b"# ok\n\nclaim: 3 lines in README.md\n",
    "a.py": b"x = 1\n",
    **{f"skill-{i}/SKILL.md": _SKILL % (i, i) for i in range(6)},
}


# The whole pipeline, every optional step on: what a fully-opted-in repo runs. NAMED, not
# counted -- a step that does not run prints exactly what a step that passes prints
# (docs/SUPERSOTA.md R4), so the only proof a step runs is its label here. 16 -> 17 on
# 2026-10-03 (`no unreaped spawns in tests`), 18 on 2026-10-04 (`prose claims are derived`),
# 19 on 2026-10-05 (`no credential in a git remote URL`), 20 on 2026-10-06 (`no vendored copies
# of house checkers`, R5), 21-23 on 2026-10-08 (requires-call, early-exit pipe, code after exec),
# 24 the same day (`no tracked file its .gitignore ignores`).
INVENTORY = [
    "a file that calls X calls Y",
    "Cargo.lock matches its manifests",
    "cap-exempt files within their ceilings",
    "file length <= 500",
    "gate self-proofs still pass",
    "gates refuse to pass over an empty tree",
    "line-cap exemptions carry a ceiling",
    "markdown links resolve",
    "no committed secrets",
    "no conflict markers",
    "no credential in a git remote URL",
    "no code after exec",
    "no disallowed emoji",
    "no early-exit pipe under pipefail",
    "no hard-coded home paths",
    "no process kill by name",
    "no tracked file its .gitignore ignores",
    "no unreaped spawns in tests",
    "no vendored copies of house checkers",
    "prose claims are derived",
    "python is ruff-formatted",
    "shell lint",
    "skills corpus",
    "version provenance",
]


def _labels(out: str) -> set[str]:
    return {s.split(" (\u2264")[0] for s in announced_steps(out)}


def test_the_whole_pipeline_runs_every_step(goh: Path, tmp_path: Path) -> None:
    """Through BOTH entry points: `goh structural`, and structural.sh as the hooks call it."""
    repo = make_repo(tmp_path, INVENTORY_CASE)
    for run in (_run_goh(goh, repo, False), _run_bash(repo, False, goh=goh)):
        assert run.returncode == 0, run.stdout + run.stderr
        got = _labels(run.stdout)
        want = set(INVENTORY)
        assert got == want, (
            f"missing: {sorted(want - got)}  new: {sorted(got - want)}\n" + run.stdout
        )


def test_every_announced_step_is_timed_once(goh: Path, tmp_path: Path) -> None:
    """P0's instrument: every announced step leaves exactly one timing line, so no step is
    counted twice (the delegated steps run through bounded_run.py, which also records)."""
    import json

    repo = make_repo(tmp_path, INVENTORY_CASE)
    out = tmp_path / "timings.jsonl"
    r = _run_goh(goh, repo, False, {"GOH_TIMINGS": str(out)})
    assert r.returncode == 0, r.stdout + r.stderr
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert sorted(row["label"] for row in rows) == sorted(_labels(r.stdout)), rows


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
    assert str(ROOT / "crates" / "goh" / "src" / "mdlinks.rs") in out, out
    assert str(ROOT / "docs" / "map.md") in out and str(ROOT / "docs" / "config.md") in out, out


def test_staged_marker_agrees(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {".gatesrc": GATESRC, "a.py": b"x = 1\n"})
    (repo / "a.py").write_bytes(b"x = 1\n<<<<<<< ours\n")
    _git(repo, "add", "a.py")
    assert run_goh(goh, repo, staged=True) == (1, "no conflict markers")


FORMATTED_SRC = b"GOH_MAX_LINES=500\nGOH_PYTHON_FORMATTED=1\n"


@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff is not installed")
@pytest.mark.parametrize(
    ("staged_bytes", "expected"),
    [(b"x = {  'a':1 }\n", (1, "python is ruff-formatted (staged)")), (b"x = 1\n", (0, None))],
)
def test_staged_python_format_agrees(goh: Path, tmp_path: Path, staged_bytes, expected) -> None:
    """Pre-commit was weaker than pre-push for this check (v0.20.0's refused push): it runs
    over the staged blobs."""
    repo = make_repo(tmp_path, {".gatesrc": FORMATTED_SRC, "a.py": staged_bytes})
    r = _run_goh(goh, repo, staged=True)
    steps = announced_steps(r.stdout + r.stderr)
    assert any(s.startswith("python is ruff-formatted (staged)") for s in steps), steps
    assert (r.returncode, _failing(r.stdout, r.stderr)) == expected


def test_staged_ignores_unstaged_dirt(goh: Path, tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {".gatesrc": GATESRC, "a.py": b"x = 1\n"})
    (repo / "a.py").write_bytes(b"x = 1\n<<<<<<< ours\n")
    assert run_goh(goh, repo, staged=True) == (0, None)


def test_structural_sh_execs_the_native_binary_when_told_where_it_is(goh, tmp_path):
    """The hooks call structural.sh, which execs the binary it is told about. A pointer at
    nothing is reported once and REFUSED: the Python tier it used to fall back to is retired
    (Phase N3), and running anything else would be a different gate, unannounced."""
    repo = make_repo(tmp_path, {"a.md": "ok\n".encode(), ".gatesrc": GATESRC})

    env = hermetic_env(GOH_BIN=str(goh), GOH_DIR=str(ROOT))
    r = subprocess.run(["bash", str(STRUCTURAL)], cwd=repo, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "goh binary not built" not in r.stderr
    env = hermetic_env(GOH_BIN="/nonexistent/goh", GOH_DIR=str(ROOT))
    r = subprocess.run(["bash", str(STRUCTURAL)], cwd=repo, capture_output=True, text=True, env=env)
    assert r.returncode == 1, r.stdout + r.stderr
    assert r.stderr.count("GOH_BIN=/nonexistent/goh is not an executable") == 1, r.stderr
    assert "== structural gate ==" not in r.stdout, "something ran in the binary's place"


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


@pytest.mark.parametrize("tier", ["structural.sh", "goh structural"])
def test_an_inherited_config_key_cannot_enable_a_step_this_repo_never_declared(
    goh: Path, tmp_path: Path, tier: str
) -> None:
    """A hostile ambient `GOH_*` must change NOTHING about what the gate runs: an inherited
    opt-in runs a check the repo never asked for (false red), and an inherited
    `GOH_EXCLUDE=.*` hides one (false green — the direction nobody sees)."""
    repo = make_repo(tmp_path, FULL_CASES["clean"])
    runner = (
        (lambda r: _run_bash(repo, False, r, goh=goh))
        if tier == "structural.sh"
        else (lambda r: _run_goh(goh, repo, False, r))
    )
    quiet, hostile = runner({}), runner(HOSTILE)
    ran_quietly, ran_hostile = announced_steps(quiet.stdout), announced_steps(hostile.stdout)
    assert ran_hostile == ran_quietly, (
        f"ambient GOH_* changed which steps {tier} ran:\n"
        f"  quiet:   {ran_quietly}\n  hostile: {ran_hostile}\n" + hostile.stdout + hostile.stderr
    )
    assert hostile.returncode == quiet.returncode, hostile.stdout + hostile.stderr


def test_an_inherited_exemption_does_not_hide_a_violation(goh: Path, tmp_path: Path) -> None:
    """`GOH_EXCLUDE=.*` is the sharp end of that class: exempt everything, and the
    emoji gate reports a clean tree."""
    repo = make_repo(tmp_path, FULL_CASES["emoji"])
    hostile = _run_bash(repo, False, {"GOH_EXCLUDE": ".*"}, goh=goh)
    assert hostile.returncode != 0, hostile.stdout + hostile.stderr
    assert "emoji" in (hostile.stdout + hostile.stderr).lower()
