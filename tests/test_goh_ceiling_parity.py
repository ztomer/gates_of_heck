"""Ceiling parity: `goh ceiling` agrees with the structural step's Python.

Each case wires a fixture repo (source files + optional ratchet baseline)
and runs the native subcommand against the same two checker invocations
`step_ceiling` performs: `check_exclusion_has_ceiling.py` and
`check_baseline_ratchet.py --current-from-command loc…`. Asserts identical
exit codes plus identical stdout/stderr — a message, stream, or population
drifting on either side goes red.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKS = ROOT / "checks"
CEILING = CHECKS / "check_exclusion_has_ceiling.py"
RATCHET = CHECKS / "check_baseline_ratchet.py"
LOC = CHECKS / "loc_of_baseline_files.py"


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


def lines(n: int) -> bytes:
    return b"".join(f"# {i}\n".encode() for i in range(n))


# (files, max, line_exclude, unbounded, baseline): the subcommand's args.
CASES: dict[str, tuple[dict[str, bytes], list[str]]] = {
    # Exempt file under the cap: no ceiling needed, both pass.
    "under_cap": ({"src/a.py": lines(8), "base.txt": b"30\tsrc/a.py\n"},
                  ["--max", "10", "--line-exclude", "src/", "--baseline", "base.txt"]),
    # Exempt and over, ceiling carried: both pass, ratchet at ceiling.
    "at_ceiling": ({"src/a.py": lines(15), "base.txt": b"15\tsrc/a.py\n"},
                   ["--max", "10", "--line-exclude", "src/", "--baseline", "base.txt"]),
    # Exempt and over with headroom: both pass with shrink detail.
    "below_ceiling": ({"src/a.py": lines(12), "base.txt": b"15\tsrc/a.py\n"},
                      ["--max", "10", "--line-exclude", "src/", "--baseline", "base.txt"]),
    # Exempt and over, no ceiling: the hole the check exists for.
    "no_ceiling": ({"src/a.py": lines(15), "base.txt": b"15\tsrc/other.py\n"},
                   ["--max", "10", "--line-exclude", "src/", "--baseline", "base.txt"]),
    # Over the ceiling: the ratchet fails.
    "over_ceiling": ({"src/a.py": lines(16), "base.txt": b"15\tsrc/a.py\n"},
                     ["--max", "10", "--line-exclude", "src/", "--baseline", "base.txt"]),
    # JSON baseline, both formats read identically.
    "json_baseline": ({"src/a.py": lines(15), "base.json": b'{"src/a.py": 15}\n'},
                      ["--max", "10", "--line-exclude", "src/", "--baseline", "base.json"]),
    "json_over": ({"src/a.py": lines(16), "base.json": b'{"src/a.py": 15}\n'},
                  ["--max", "10", "--line-exclude", "src/", "--baseline", "base.json"]),
    # Stale exemption list: matches nothing, both fail.
    "stale_exclude": ({"src/a.py": lines(5), "base.txt": b"15\tsrc/a.py\n"},
                      ["--max", "10", "--line-exclude", "gone/", "--baseline", "base.txt"]),
    # Working unbounded waiver.
    "waived": ({"vendor/dump.py": lines(50), "base.txt": b"15\tsrc/a.py\n",
                "src/a.py": lines(5)},
               ["--max", "10", "--line-exclude", "vendor/|src/",
                "--unbounded", "vendor/", "--baseline", "base.txt"]),
    # Waiver matching nothing: stale, both fail.
    "stale_waiver": ({"src/a.py": lines(5), "base.txt": b"15\tsrc/a.py\n"},
                     ["--max", "10", "--line-exclude", "src/",
                      "--unbounded", "vendor/", "--baseline", "base.txt"]),
    # Baseline path missing: named precondition, exit 2.
    "missing_baseline": ({"src/a.py": lines(15)},
                         ["--max", "10", "--line-exclude", "src/",
                          "--baseline", "missing.txt"]),
    # No exemptions at all: the check is vacuous, both pass.
    "no_exemptions": ({"src/a.py": lines(15), "base.txt": b"15\tsrc/a.py\n"},
                      ["--max", "10", "--baseline", "base.txt"]),
}


def run_py(repo: Path, args: list[str]) -> tuple[int, str, str]:
    max_v = args[args.index("--max") + 1]
    baseline = args[args.index("--baseline") + 1]
    cmd = ["python3", str(CEILING), "--max", max_v, "--baseline", baseline]
    if "--line-exclude" in args:
        cmd += ["--line-exclude", args[args.index("--line-exclude") + 1]]
    if "--unbounded" in args:
        cmd += ["--unbounded", args[args.index("--unbounded") + 1]]
    first = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
    out, err, code = first.stdout, first.stderr, first.returncode
    if code == 0 and (repo / baseline).is_file():
        measure = f"python3 '{LOC}' '{baseline}'"
        second = subprocess.run(
            ["python3", str(RATCHET), "--baseline", baseline,
             "--current-from-command", measure],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        out += second.stdout
        err += second.stderr
        code = second.returncode
    return code, out, err


def run_goh(goh: Path, repo: Path, args: list[str]) -> tuple[int, str, str]:
    import os

    env = dict(os.environ, GOH_DIR=str(ROOT))
    r = subprocess.run(
        [str(goh), "ceiling", *args], cwd=repo, capture_output=True, text=True, env=env
    )
    return r.returncode, r.stdout, r.stderr


@pytest.mark.parametrize("name", sorted(CASES))
def test_ceiling_agrees(goh: Path, tmp_path: Path, name: str) -> None:
    files, args = CASES[name]
    repo = make_repo(tmp_path, files)
    assert run_goh(goh, repo, args) == run_py(repo, args), name


def test_warn_branch_agrees(goh: Path, tmp_path: Path) -> None:
    # Exemptions set, no baseline configured: a named warning, exit 0.
    repo = make_repo(tmp_path, {"src/a.py": lines(15)})
    args = ["--max", "10", "--line-exclude", "src/"]
    got = run_goh(goh, repo, args)
    assert got[0] == 0
    assert "GOH_LINE_EXCLUDE is set but GOH_LINE_BASELINE is not" in got[2]
