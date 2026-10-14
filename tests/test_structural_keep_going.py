"""`goh structural --keep-going`: a measurement run that does not stop at the first red.

BACKLOG 6.16. The baseline was operational, not a number: seven consumer agents, up to four rounds
each, each round clearing one red to see the next, because `structural` returns at its first failing
step. Every red after the first was invisible until it was fixed. `--keep-going` runs every step,
names every red, and exits 1 if any was red; the default stays fail-fast, because the commit hook
wants the first red and nothing after it.

THE TWO PLANTED REDS ARE INDEPENDENT, which is the `Lies:` clause on the item and the whole reason
a second red can be trusted as evidence. `dead_exec_red` is found by `deadexec::step` over
`a.sh`; `md_link_red` by `mdlinks::step` over `README.md`. Neither step reads the other's file,
neither writes anything the other reads, and the two run in different halves of the pipeline
(shell_steps is early, mdlinks is late) — so the second's appearance cannot be an artifact of the
first being fixed. That is stated here rather than assumed, because a keep-going test whose two
reds are entangled proves nothing: the second would be reported even if keep-going were broken,
and the flag would look calibrated when it is not.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from conftest import hermetic_env
from goh_structural_cases import FULL_CASES

ROOT = Path(__file__).resolve().parents[1]
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
STEP_RE = re.compile(r"^· (.+)$")
# The FAILURE line, not the announcement: `step_report::fail` prints `structural: <label> failed`
# on stderr, and a step that PASSES still announces its label with a `✓`. Matching the label as a
# substring of the whole output therefore reads a green step as a red one -- which is exactly what
# the first run of this file did, and why the reds are read off this and not off `STEP_RE`.
FAIL_RE = re.compile(r"structural: (.+?) failed \(")

# The two reds, merged into ONE repo: `a.sh` from the early step, `README.md` from the late one.
# Both write the same `.gatesrc`, so neither file's contents depend on the other.
TWO_REDS: dict[str, bytes] = {
    **FULL_CASES["dead_exec_red"],
    **{k: v for k, v in FULL_CASES["md_link_red"].items() if k != ".gatesrc"},
}

# What each step announces when it fails, from EXPECTED's labels. The summary names these.
EARLY_LABEL = "no code after exec"
LATE_LABEL = "markdown links resolve"


def _clean(text: str) -> str:
    return ANSI_RE.sub("", text)


def _run(goh: Path, repo: Path, *flags: str) -> subprocess.CompletedProcess:
    env = hermetic_env(GOH_DIR=str(ROOT))
    return subprocess.run(
        [str(goh), "structural", *flags],
        check=False,
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
    )


def _announced(out: str) -> list[str]:
    """Labels this run ATTEMPTED. The timing suffix ` (≤1800s)` is stripped: it is the step's
    budget, not part of its name, and a label compared with `in` would match either way."""
    steps = []
    for line in _clean(out).splitlines():
        match = STEP_RE.match(line.strip())
        if match:
            steps.append(re.sub(r"\s*\(≤\d+s\)$", "", match.group(1)))
    return steps


def _failed(out: str, err: str) -> list[str]:
    """Labels that actually FAILED, from the failure line rather than the announcement."""
    return [
        m.group(1) for m in (FAIL_RE.search(line) for line in _clean(out + err).splitlines()) if m
    ]


def _repo(tmp_path: Path, files: dict[str, bytes]) -> Path:
    from _fast_git import fast_init

    repo = tmp_path
    repo.mkdir(exist_ok=True)
    fast_init(repo)
    for name, content in files.items():
        dest = repo / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
    subprocess.run(
        ["git", "add", "-A"], cwd=repo, capture_output=True, check=True, stdin=subprocess.DEVNULL
    )
    return repo


def test_the_two_planted_reds_are_the_ones_independently_verified(
    goh: Path, tmp_path: Path
) -> None:
    """The fixture is only evidence if each red is red ON ITS OWN, in the same mode.

    Calibrated both directions: a repo holding only the early red must not report the late step,
    and one holding only the late red must not report the early one. Without this the merged repo
    could be carrying a single defect that both halves happen to print, and every test below would
    be reading the same one finding twice.
    """
    early = _repo(tmp_path / "early", {k: v for k, v in FULL_CASES["dead_exec_red"].items()})
    late = _repo(tmp_path / "late", {k: v for k, v in FULL_CASES["md_link_red"].items()})

    early_out = _run(goh, early, "--full")
    late_out = _run(goh, late, "--full")

    assert _failed(early_out.stdout, early_out.stderr) == [EARLY_LABEL], early_out.stderr
    assert _failed(late_out.stdout, late_out.stderr) == [LATE_LABEL], late_out.stderr


def test_keep_going_names_a_red_the_default_run_stops_before(goh: Path, tmp_path: Path) -> None:
    """THE exit condition: the second red is named in the output.

    Default `--full` returns at the first failing step, so the late red is absent from the output
    ENTIRELY -- it never ran. Under `--keep-going` it ran and failed, and the exit is still 1: a
    run that named everything and exited 0 would be a report, not a gate.
    """
    repo = _repo(tmp_path, TWO_REDS)

    default = _run(goh, repo, "--full")
    kept = _run(goh, repo, "--full", "--keep-going")

    assert default.returncode == 1, _clean(default.stdout + default.stderr)
    assert _failed(default.stdout, default.stderr) == [EARLY_LABEL], default.stderr

    assert kept.returncode == 1, _clean(kept.stdout + kept.stderr)
    assert _failed(kept.stdout, kept.stderr) == [EARLY_LABEL, LATE_LABEL], kept.stderr


def test_keep_going_is_still_a_gate_and_not_a_report(goh: Path, tmp_path: Path) -> None:
    """Both reds named, one summary, exit 1 — and the summary counts what ran.

    The count is the part that cannot be faked by printing both labels: it is the number of steps
    this run ATTEMPTED (`· label`), read from the pipeline's own announcements. A keep-going that
    skipped steps to save time would still name two reds and still exit 1, so the label alone is
    not evidence it kept going.
    """
    repo = _repo(tmp_path, TWO_REDS)

    kept = _run(goh, repo, "--full", "--keep-going")
    text = _clean(kept.stdout + kept.stderr)
    announced = _announced(kept.stdout)

    assert kept.returncode == 1, text
    # The late step's ATTEMPTED list in this same run contains the step that failed -- the two
    # words above are announced by every step, but the failed ones are also FAILURE lines, and
    # this asserts the pipeline reached the late half at all after the early half went red.
    assert LATE_LABEL in announced, announced
    # A summary, and a red count of 2.
    assert re.search(r"2 (steps? failed|red)", text), text


def test_a_clean_repo_still_passes_under_keep_going(goh: Path, tmp_path: Path) -> None:
    """The flag must not turn a green run red: exit 0, and the pass line it would have printed."""
    repo = _repo(tmp_path, FULL_CASES["clean"])

    kept = _run(goh, repo, "--full", "--keep-going")
    text = _clean(kept.stdout + kept.stderr)

    assert kept.returncode == 0, text
    assert "all structural gates passed" in text, text


def test_keep_going_is_refused_at_staged_scope(goh: Path, tmp_path: Path) -> None:
    """`--keep-going` with `--staged` is a usage error, not a silent full run.

    The scope argument here is strict for a recorded reason (structural.sh): a typo used to fall
    through to "check everything" without saying so. A flag that quietly widened or narrowed the
    scope would be the same defect one level down.
    """
    repo = _repo(tmp_path, FULL_CASES["clean"])

    r = _run(goh, repo, "--staged", "--keep-going")
    text = _clean(r.stdout + r.stderr)

    assert r.returncode == 2, text
    # The refusal must be ABOUT THE TWO FLAGS. Before `--keep-going` existed, clap refused this
    # same command line with `unexpected argument '--keep-going'` and the same exit code 2 — so an
    # exit-code assertion alone passes against a binary that has never heard of the flag, which is
    # a test that cannot fail for the reason it names.
    assert "--keep-going" in text and "unexpected argument" not in text, text


def test_a_refusal_stops_the_run_even_under_keep_going(goh: Path, tmp_path: Path) -> None:
    """A step that could not judge the repo ends the run, `--keep-going` or not.

    Exit 2 is how a step says `I cannot read your .gatesrc` / `this filter will not compile` /
    `the red-run timer is already past`. Continuing past one is the worst output this flag could
    produce: every remaining step then reports on a configuration nobody could read, which reads as
    a repo in far worse shape than it is — and the reader of a keep-going run is precisely the
    reader who cannot tell one cause from twenty.

    Calibrated: the repo below is otherwise clean, so the ONLY way this run ends red is the
    refusal, and the only way to tell it stopped is that the steps after it never announced.
    """
    repo = _repo(
        tmp_path,
        {".gatesrc": b"GOH_MAX_LINES=10\nGOH_EXCLUDE='[unclosed'\n", "a.py": b"x = 1\n"},
    )

    kept = _run(goh, repo, "--full", "--keep-going")
    text = _clean(kept.stdout + kept.stderr)
    announced = _announced(kept.stdout)

    assert kept.returncode == 2, text
    # No summary: the run did not reach its end, it stopped.
    assert "steps failed" not in text and "step failed" not in text, text
    # The emoji step is FIRST, and it refuses while BUILDING its filter — before it announces, so
    # nothing ran at all. The empty list is the evidence: any step in it means keep-going carried
    # on past a run that could not read its own configuration.
    assert announced == [], announced
