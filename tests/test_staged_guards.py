"""A test that declares it reads the repo's source text runs at COMMIT (GOH.CONFIGKEYCOMMIT,
GOH.SWIFTGUARDCOMMIT; gates/staged_guards.sh).

Two guards over source text lived only in their repos' pytest suites, which run at push: goh's
tests/test_config_schema.py let a new GOH_* key nothing declares commit green and be
refused ~20 minutes into a land gate (ba09c92), and token-pool's tests/test_widget_defaults_guard.py
did the same for a WidgetPanel built on the shared xctest defaults domain (ef13840). One property
closes both: a `# goh-reads-staged: <pathspec>...` line in the test file, and the commit gate runs
the file over the index whenever the commit stages a file a pathspec matches.

Red first: on a tree without the step every refusal test below commits green, and
test_config_schema.py carries no directive.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env
from test_commit_gate_structural import _consumer, _git
from timing_bounds import patience

GATE = '#!/usr/bin/env bash\nset -euo pipefail\nexec bash "$GOH_DIR/gates/structural.sh" "$@"\n'
DIRECTIVE = "# goh-reads-staged:"
STEP = "source guards over the index"

# A consumer's guard over its sources, the token-pool shape: no source names FORBIDDEN. The sources
# are `.src`, not `.swift`, so the fixture needs no swiftlint (swift_staged.sh would lint a .swift).
GUARD = "\n".join(
    [
        '"""No source names the forbidden store."""',
        "",
        f"{DIRECTIVE} *.src",
        "",
        "from pathlib import Path",
        "",
        "ROOT = Path(__file__).resolve().parent.parent",
        "",
        "",
        "def test_no_source_names_the_forbidden_store():",
        '    hits = [str(p) for p in sorted(ROOT.rglob("*.src")) if "FORBIDDEN" in p.read_text()]',
        "    assert hits == [], hits",
        "",
    ]
)


def _guarded(tmp_path: Path, guard: str = GUARD) -> Path:
    repo = _consumer(tmp_path, GATE)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_guard.py").write_text(guard)
    (repo / "Sources").mkdir()
    (repo / "Sources" / "A.src").write_text("let value = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "guard")
    return repo


def _guards(repo: Path, staged: str) -> str:
    """The guards' step alone, over STAGED: what a commit's other checks say is not asked here."""
    (repo / staged).write_text("plain\n")
    _git(repo, "add", staged)
    got = subprocess.run(["bash", str(REPO_ROOT / "gates" / "staged_guards.sh"), str(repo)],
                         cwd=repo, env=hermetic_env(GOH_DIR=str(REPO_ROOT)), capture_output=True,
                         text=True, timeout=patience(180), stdin=subprocess.DEVNULL,
                         check=False)  # fmt: skip
    assert got.returncode == 0, got.stdout + got.stderr
    return got.stdout + got.stderr


def _commit(repo: Path, files: dict[str, str], stage: tuple[str, ...] | None = None):
    for rel, body in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(body)
    _git(repo, "add", *(stage if stage is not None else tuple(files)))
    return subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "plant"],
        env=hermetic_env(GOH_DIR=str(REPO_ROOT)),
        capture_output=True,
        text=True,
        timeout=patience(180),
        stdin=subprocess.DEVNULL,
        check=False,
    )


def test_a_commit_staging_nothing_the_guard_reads_does_not_run_it(tmp_path):
    repo = _guarded(tmp_path)
    (repo / "Sources" / "C.src").write_text("let s = FORBIDDEN\n")  # unstaged, and not read
    text = _guards(repo, "notes.md")
    assert STEP not in text, text


def test_the_guard_judges_the_index_not_the_working_tree(tmp_path):
    repo = _guarded(tmp_path)
    (repo / "Sources" / "B.src").write_text("let s = FORBIDDEN\n")
    _git(repo, "add", "Sources/B.src")
    (repo / "Sources" / "B.src").write_text("let s = 2\n")  # the fix is only unstaged
    got = _commit(repo, {}, stage=())
    text = got.stdout + got.stderr
    assert got.returncode != 0 and STEP in text, text
    assert "tests/test_guard.py" in text and "B.src" in text, text


def test_an_unstaged_violation_does_not_refuse_a_clean_commit(tmp_path):
    repo = _guarded(tmp_path)
    (repo / "Sources" / "A.src").write_text("let s = FORBIDDEN\n")  # unstaged
    got = _commit(repo, {"Sources/B.src": "let s = 2\n"})
    text = got.stdout + got.stderr
    assert got.returncode == 0 and f"✓ {STEP}" in text, text


def test_a_directive_with_no_pathspec_runs_on_every_commit(tmp_path):
    repo = _guarded(tmp_path, GUARD.replace(f"{DIRECTIVE} *.src", DIRECTIVE))
    text = _guards(repo, "notes.md")
    assert f"✓ {STEP}" in text, text


def _directive_specs(path: Path) -> list[str]:
    lines = [ln for ln in path.read_text().splitlines() if ln.startswith(DIRECTIVE)]
    assert lines, f"{path.name} declares no {DIRECTIVE} line"
    return [spec for ln in lines for spec in ln[len(DIRECTIVE) :].split()]


def test_the_config_schema_guard_declares_every_tree_it_scans():
    """goh's own instance (GOH.CONFIGKEYCOMMIT): the schema test runs at commit whenever a commit
    stages a file it scans, a declaration, or the starter template."""
    schema = REPO_ROOT / "tests" / "test_config_schema.py"
    specs = set(_directive_specs(schema))
    from lib import config_ref

    missing = sorted({*config_ref.SCAN, "docs/config", ".gatesrc.example"} - specs)
    assert missing == [], f"the # goh-reads-staged: line misses {missing}"


# TP.WEBVISION3: in a uv repo the guard ran `uv run` in the view with the checkout's .venv as its
# environment, so uv synced the view's project into it and the checkout's next `uv run` reinstalled
# its own package. The view now runs with UV_NO_SYNC, and imports the INDEX's modules through
# PYTHONPATH (the environment's editable install points at the checkout, whose bytes are not the
# commit's).
IMPORT_GUARD = "\n".join(
    [
        f"{DIRECTIVE} *.py",
        "import mod",
        "",
        "",
        "def test_the_index_holds_the_new_value():",
        "    assert mod.VALUE == 2",
        "",
    ]
)


def test_a_uv_repos_guard_never_syncs_the_view_and_imports_the_index(tmp_path):
    import os
    import sys

    repo = _guarded(tmp_path, IMPORT_GUARD)
    bindir, log = tmp_path / "bin", tmp_path / "uv.log"
    bindir.mkdir()
    (bindir / "uv").write_text(
        "#!/bin/sh\n"
        f'echo "$(pwd -P)|${{UV_NO_SYNC:-}}|$*" >>"{log}"\n'
        "shift\n"
        'while :; do case "$1" in --python) shift 2 ;; --*) shift ;; *) break ;; esac; done\n'
        f'case "$1" in python) shift; exec "{sys.executable}" "$@" ;;\n'
        # -P: a console script puts no cwd on sys.path, and the view IS the cwd
        f'  pytest|ruff) t="$1"; shift; exec "{sys.executable}" -P -m "$t" "$@" ;; esac\n'
        'exec "$@"\n'
    )
    (bindir / "uv").chmod(0o755)
    (repo / "uv.lock").write_text("version = 1\n")
    (repo / "mod.py").write_text("VALUE = 2\n")
    _git(repo, "add", "uv.lock", "mod.py")
    (repo / "mod.py").write_text("VALUE = 1\n")  # the working tree's copy is not the commit's
    got = subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "plant"],
        env=hermetic_env(GOH_DIR=str(REPO_ROOT), PATH=f"{bindir}:{os.environ['PATH']}"),
        capture_output=True, text=True, timeout=patience(180), stdin=subprocess.DEVNULL,
        check=False,
    )  # fmt: skip
    text = got.stdout + got.stderr
    assert got.returncode == 0, text
    assert f"✓ {STEP}" in text, text
    calls = [line.split("|", 2) for line in log.read_text().splitlines()]
    guard = [c for c in calls if "pytest" in c[2] and "test_guard.py" in c[2]]
    assert guard and all(no_sync == "1" for _, no_sync, _ in guard), calls
