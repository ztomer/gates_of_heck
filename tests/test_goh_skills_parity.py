"""Skills parity: `goh skills` agrees with `check_skills_corpus.py`.

Fixture corpora per case, each exercised with identical flags on both
sides; asserts identical exit codes plus identical stdout/stderr. A
check, message, count, or stream drifting on either side goes red.
Also pins the structural step: a repo opting in with `GOH_SKILLS_ROOT`
pointed at a fixture corpus gets the same verdict natively and via
the Python pipeline.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "checks" / "check_skills_corpus.py"
STRUCTURAL = ROOT / "gates" / "structural.sh"
FAIL_RE = re.compile(r"structural: (.+) failed")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


def skill(name: str, body: str = "Body text here.\n") -> tuple[str, str]:
    return (
        f"{name}/SKILL.md",
        f"---\nname: {name}\ndescription: d\n---\n# {name}\n{body}",
    )


def make_corpus(base: Path, files: dict[str, str]) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        dest = base / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
    return base


def run_py(corpus: Path, *args: str) -> tuple[int, str, str]:
    r = subprocess.run(
        ["python3", str(CHECK), "--root", str(corpus), *args],
        capture_output=True,
        text=True,
    )
    return r.returncode, r.stdout, r.stderr


def run_goh(goh: Path, corpus: Path, *args: str) -> tuple[int, str, str]:
    import os

    env = dict(os.environ, GOH_DIR=str(ROOT))
    r = subprocess.run(
        [str(goh), "skills", "--root", str(corpus), *args],
        capture_output=True,
        text=True,
        env=env,
    )
    return r.returncode, r.stdout, r.stderr


def clean_corpus() -> dict[str, str]:
    return dict([skill("alpha"), skill("beta")])


def messy_corpus() -> dict[str, str]:
    big = "word " * 60
    return {
        "bare/SKILL.md": "Just prose, no fence.\n",
        **dict(
            [
                skill(
                    "messy",
                    f"See [[ghost]] and [doc](references/gone.md).\n{big}\n",
                )
            ]
        ),
    }


CASES: dict[str, dict[str, str]] = {
    "clean": clean_corpus(),
    "messy": messy_corpus(),
}


@pytest.mark.parametrize("name", sorted(CASES))
@pytest.mark.parametrize(
    "flags",
    [
        ["--min-skills", "1"],
        ["--max-words", "50", "--min-skills", "1"],
    ],
)
def test_skills_agrees(goh: Path, tmp_path: Path, name: str, flags: list[str]) -> None:
    corpus = make_corpus(tmp_path / name, CASES[name])
    assert run_goh(goh, corpus, *flags) == run_py(corpus, *flags), (name, flags)


def test_scope_floor_agrees(goh: Path, tmp_path: Path) -> None:
    # Fewer skills than the floor: exit 2 on both. The message names the
    # absolute root, so compare codes plus the stable tail only.
    corpus = make_corpus(tmp_path / "tiny", clean_corpus())
    code_goh, out_goh, _ = run_goh(goh, corpus)
    code_py, out_py, _ = run_py(corpus)
    assert (code_goh, code_py) == (2, 2)
    assert "floor 5" in out_goh and "floor 5" in out_py


def test_update_baseline_agrees(goh: Path, tmp_path: Path) -> None:
    flags = ["--max-words", "50", "--min-skills", "1", "--update-baseline"]
    corpus_py = make_corpus(tmp_path / "py", messy_corpus())
    corpus_goh = make_corpus(tmp_path / "goh", messy_corpus())
    assert run_py(corpus_py, *flags) == run_goh(goh, corpus_goh, *flags)
    py_base = json.loads((corpus_py / "skills_size_baseline.json").read_text())
    goh_base = json.loads((corpus_goh / "skills_size_baseline.json").read_text())
    assert py_base == goh_base, (py_base, goh_base)


def failing_label(out: str, err: str) -> str | None:
    m = next(
        (FAIL_RE.search(l) for l in (out + err).splitlines() if FAIL_RE.search(l)),
        None,
    )
    return m.group(1) if m else None


def test_structural_corpus_step_agrees(goh: Path, tmp_path: Path) -> None:
    import os

    for variant in ("clean", "messy"):
        repo = tmp_path / variant
        repo.mkdir(exist_ok=True)
        _git(repo, "init", "-q")
        files = clean_corpus() if variant == "clean" else messy_corpus()
        for name, content in files.items():
            dest = repo / "corpus" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
        (repo / ".gatesrc").write_text(
            f"GOH_MAX_LINES=500\nGOH_SKILLS_CORPUS=1\nGOH_SKILLS_ROOT={repo / 'corpus'}\n"
        )
        (repo / "a.py").write_text("x = 1\n")
        _git(repo, "add", "-A")
        env = dict(os.environ, GOH_DIR=str(ROOT))
        native = subprocess.run(
            [str(goh), "structural", "--full"], cwd=repo, capture_output=True, text=True, env=env
        )
        python = subprocess.run(
            ["bash", str(STRUCTURAL), "--full"],
            cwd=repo,
            capture_output=True,
            text=True,
            env=dict(env, GOH_NO_NATIVE="1"),
        )
        assert (native.returncode, failing_label(native.stdout, native.stderr)) == (
            python.returncode,
            failing_label(python.stdout, python.stderr),
        ), (
            variant,
            native.stdout + native.stderr,
            python.stdout + python.stderr,
        )
