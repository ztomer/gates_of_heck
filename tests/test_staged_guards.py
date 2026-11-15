"""A test that declares it reads the repo's source text runs at COMMIT (GOH.CONFIGKEYCOMMIT,
GOH.SWIFTGUARDCOMMIT; gates/staged_guards.sh).

Two guards over source text lived only in their repos' pytest suites, which run at push: goh's
tests/test_config_schema.py let a new GOH_* key with no docs/config.md row commit green and be
refused ~20 minutes into a land gate (ba09c92), and token-pool's tests/test_widget_defaults_guard.py
did the same for a WidgetPanel built on the shared xctest defaults domain (ef13840). One property
closes both: a `# goh-reads-staged: <pathspec>...` line in the test file, and the commit gate runs
the file over the index whenever the commit stages a file a pathspec matches.

Red first: on a tree without the step every refusal test below commits green, and
test_config_schema.py carries no directive.
"""

from __future__ import annotations

import re
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


def test_a_staged_source_the_guard_refuses_is_refused_at_commit(tmp_path):
    repo = _guarded(tmp_path)
    got = _commit(repo, {"Sources/B.src": "let s = FORBIDDEN\n"})
    text = got.stdout + got.stderr
    assert got.returncode != 0, text
    assert STEP in text and "tests/test_guard.py" in text and "B.src" in text, text


def test_a_clean_staged_source_runs_the_guard_and_commits(tmp_path):
    got = _commit(_guarded(tmp_path), {"Sources/B.src": "let s = 2\n"})
    text = got.stdout + got.stderr
    assert got.returncode == 0, text
    assert f"✓ {STEP}" in text, text


def test_a_commit_staging_nothing_the_guard_reads_does_not_run_it(tmp_path):
    repo = _guarded(tmp_path)
    (repo / "Sources" / "C.src").write_text("let s = FORBIDDEN\n")  # unstaged, and not read
    got = _commit(repo, {"notes.md": "plain\n"})
    text = got.stdout + got.stderr
    assert got.returncode == 0, text
    assert STEP not in text, text


def test_the_guard_judges_the_index_not_the_working_tree(tmp_path):
    repo = _guarded(tmp_path)
    (repo / "Sources" / "B.src").write_text("let s = FORBIDDEN\n")
    _git(repo, "add", "Sources/B.src")
    (repo / "Sources" / "B.src").write_text("let s = 2\n")  # the fix is only unstaged
    got = _commit(repo, {}, stage=())
    text = got.stdout + got.stderr
    assert got.returncode != 0 and STEP in text, text


def test_an_unstaged_violation_does_not_refuse_a_clean_commit(tmp_path):
    repo = _guarded(tmp_path)
    (repo / "Sources" / "A.src").write_text("let s = FORBIDDEN\n")  # unstaged
    got = _commit(repo, {"Sources/B.src": "let s = 2\n"})
    text = got.stdout + got.stderr
    assert got.returncode == 0 and f"✓ {STEP}" in text, text


def test_a_directive_with_no_pathspec_runs_on_every_commit(tmp_path):
    repo = _guarded(tmp_path, GUARD.replace(f"{DIRECTIVE} *.src", DIRECTIVE))
    got = _commit(repo, {"notes.md": "plain\n"})
    text = got.stdout + got.stderr
    assert got.returncode == 0 and f"✓ {STEP}" in text, text


def _directive_specs(path: Path) -> list[str]:
    lines = [ln for ln in path.read_text().splitlines() if ln.startswith(DIRECTIVE)]
    assert lines, f"{path.name} declares no {DIRECTIVE} line"
    return [spec for ln in lines for spec in ln[len(DIRECTIVE) :].split()]


def test_the_config_schema_guard_declares_every_tree_it_scans():
    """goh's own instance (GOH.CONFIGKEYCOMMIT): the schema test runs at commit whenever a commit
    stages a file it scans, or the two files it reads the keys' rows from."""
    schema = REPO_ROOT / "tests" / "test_config_schema.py"
    specs = set(_directive_specs(schema))
    scan = re.search(r"^SCAN = \(([^)]*)\)", schema.read_text(), re.MULTILINE)
    assert scan, "test_config_schema.py lost its SCAN tuple"
    scanned = set(re.findall(r'"([^"]+)"', scan.group(1)))
    assert scanned, "SCAN names nothing"
    missing = sorted((scanned | {"docs/config.md", ".gatesrc.example"}) - specs)
    assert missing == [], f"the # goh-reads-staged: line misses {missing}"
