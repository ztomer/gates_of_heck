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
    "GOH_SKILLS_CORPUS": "1",
    "GOH_SKILLS_MAX_WORDS": "1",
    "GOH_EXCLUDE": ".*",
    "GOH_LINE_EXCLUDE": ".*",
    "GOH_ALLOW": "Q",
}

# What docs/config.md documents in its STRUCTURAL table that is NOT pipeline config: the
# first two say WHICH BINARY runs, the rest belong to the push gate, install.sh or
# build-goh.sh. Absent from the drop list is safe only while somebody has said so.
NOT_PIPELINE_CONFIG = set(
    "GOH_BIN GOH_NO_NATIVE GOH_SKIP_BUILD GOH_BUILD_DIRTY "
    "GOH_EXPORT_KEEP GOH_PUSH_WORKTREES GOH_PUSH_LOGS GOH_TAG_VERSION_SOURCES".split()
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
        env=_hermetic_env(GOH_DIR=str(checkout), GOH_PUSH_LOGS=str(tmp_path / "logs"), **env),
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


def test_goh_is_built_once_per_session(goh: Path, goh_build_count: int) -> None:
    """The class behind 23 errors in one pre-push run (2026-09-22): a session fixture under xdist
    runs once PER WORKER, and each worker's `cargo build` re-linked the binary another worker was
    copying. One build per session is the invariant; more than one is the race coming back."""
    assert goh.exists()
    assert goh_build_count == 1, f"goh was built {goh_build_count} times in one session"


# ── Uncommitted gate source is a DIFFERENT GATE ──────────────────────────────
# Not a build artefact: `structural.sh` and every checker under `checks/` are read from
# this checkout's WORKING TREE as the gate runs, so one uncommitted line changes every
# repo's verdict silently, with no reinstall to hint at it — SUPERSOTA §3's top gap.
SOURCE_PATH_LISTS = {
    "gates/structural.sh": r'_goh_gate_source_paths="([\w ]+)"',
    "gates/push_gate.sh": r"--\s+(gates checks lib tui)\s+2>",
    "scripts/build-goh.sh": r"--\s+crates Cargo\.toml Cargo\.lock ([\w ]+?)\s*2>",
}


def test_all_three_gate_source_path_lists_are_the_same_list() -> None:
    """Three files warn or refuse on uncommitted gate source, each spelling the paths out.
    A helper is the obvious dedup and the wrong one: it would be gate source itself, and
    a check whose list is written by what it checks cannot vouch for it."""
    lists = {}
    for name, pattern in SOURCE_PATH_LISTS.items():
        found = re.search(pattern, (ROOT / name).read_text(encoding="utf-8"))
        assert found, f"{name} no longer names the uncommitted-source paths it is checked on"
        lists[name] = set(found.group(1).split())
    shared = set.intersection(*lists.values())
    assert all(entries == shared for entries in lists.values()), (
        "the three uncommitted-gate-source path lists disagree:\n"
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
    _git(checkout, "add", "-A")
    _git(checkout, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "today")
    clean = subprocess.run(
        ["git", "-C", str(checkout), "status", "--porcelain"], capture_output=True, text=True
    ).stdout
    assert not clean.strip(), f"the fixture checkout is not clean: {clean}"
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
            env=_hermetic_env(GOH_DIR=str(checkout), GOH_BIN=str(ROOT / "bin" / "goh")),
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
    # ...and the one place that may refuse, does.
    refused = publish_gate()
    out = refused.stdout + refused.stderr
    assert refused.returncode == 1, out
    assert "refusing to publish bin/goh" in out and "check_no_emoji.py" in out, out
