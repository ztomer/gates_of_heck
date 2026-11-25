"""lib/push_refusal.py (GOH.PINSWEEP): the step a push gate refused at, read from its log; and
lib/push_hold.py: the hold of a repo whose gate would say it again. The log shapes are the 62
sweeps' own, shortened."""

from __future__ import annotations

import subprocess
from pathlib import Path

import push_hold as hold
import push_refusal
import pytest

TAIL = """✗ pre-push: the gate failed on 8de23c1 — nothing pushed
✗ pre-push: the full gate output is kept at /c/x.log
error: failed to push some refs to 'https://example.invalid/x.git'
load: spinners stopped, registry cleared
"""
CI = f"""→ load: gate slot 1/2 taken after 0s
✗ [9/14] FAILED (exit status 1): bash tools/android_gate.sh
✗ the Android build, lint or unit tests failed
✗ rust: dependency currency failed (exit status 1; command: /x/goh deps -- .)
✗ 2 of 14 steps failed:
✗{" "}
  bash tools/android_gate.sh (exit status 1)
  "$GOH_DIR/gates/rust_gate.sh" (exit status 1)
✗{" "}
✗   full output: /t/goh-local-ci.2AC5ol
{TAIL}"""
STRUCTURAL = f"""✗ [roadmap] docs/ROADMAP.md:138: (queue: OW.89) is done: prune the entry
✗ structural: one roadmap, open entries only failed (native /x/roadmap/mod.rs)
{TAIL}"""
PLAIN = f"""✓ coverage >= 70% over every repo file
  executed references to a script this repo no longer ships: 1
    /h/x.plist: cannot be read (not well-formed)
  Update the reference.
{TAIL}"""
MOVED = """✗ pre-push: HEAD moved while it was being gated: gated 6be2, now c9e8
✗   git would send c9e8, which no gate ran on -- nothing pushed. Push again, or pin
error: failed to push some refs to 'https://example.invalid/x.git'
"""
NO_SLOT = "→ waited 4m 30s for a gate slot; still held:\n· slot-1: wt: ./build.sh (pid 5)\n"
NETWORK = "fatal: unable to access 'https://example.invalid/x.git': Could not resolve host\n"
ANDROID, RUST = "bash tools/android_gate.sh", '"$GOH_DIR/gates/rust_gate.sh"'


@pytest.mark.parametrize(("text", "judged", "steps", "named"), [
    (CI, True, (ANDROID, RUST), ("the Android build", "rust: dependency currency failed")),
    (STRUCTURAL, True, ("structural: one roadmap, open entries only",), ("prune the entry",)),
    (PLAIN, True, (), ("no longer ships: 1", "/h/x.plist: cannot be read")),
    (MOVED, False, (), ("HEAD moved while", "no gate ran on")),
    (NO_SLOT, False, (), ()),
    (NETWORK, False, (), ("Could not resolve host",)),
])  # fmt: skip
def test_a_refusal_names_its_step_and_its_fault_never_the_logs_tail(
    text, judged, steps, named
) -> None:
    got = push_refusal.refusal(text)
    assert (got.judged, got.steps) == (judged, steps), got
    assert all(n in got.said for n in (*steps, *named)), got
    assert "load: spinners" not in got.said and "full gate output" not in got.said, got


class Git:
    """estate_commit.git over a list of (sha, subject, files), newest first."""

    def __init__(self) -> None:
        self.log: list[tuple[str, str, str]] = []

    def __call__(self, _repo: Path, *args: str) -> subprocess.CompletedProcess:
        out = ""
        if args[0] == "log":
            out = "".join(f"{sha}\t{said}\n" for sha, said, _ in self.log)
        elif args[0] == "diff-tree":
            out = next(f for sha, _, f in self.log if sha == args[-1])
        return subprocess.CompletedProcess(args, 0, out, "")


PIN = ("p1", "ci: pin goh v0.9.0 in the hosted gate -- x", ".github/workflows/ci.yml\n")


@pytest.fixture
def git(monkeypatch) -> Git:
    fake = Git()
    monkeypatch.setattr(hold.estate_commit, "git", fake)
    return fake


def test_the_tip_is_the_newest_commit_that_is_no_pin(git) -> None:
    git.log = [PIN, ("o1", "feat: work", "src/a.py\n")]
    assert hold.tip(Path("/p")) == "o1"
    git.log = [("p2", PIN[1], "src/a.py\n"), *git.log]  # its subject, and an owner's file in it
    assert hold.tip(Path("/p")) == "p2"
    git.log = [("h1", "chore: refresh goh's stock hooks (1 file)", ".githooks/pre-push\n")]
    assert hold.tip(Path("/p")) == "h1"  # a hook refresh moves the gate
    git.log = [PIN]
    assert hold.tip(Path("/p")) == ""


@pytest.fixture
def trees(tmp_path: Path) -> tuple[Path, Path]:
    """(a consumer, a goh): two scripts of the consumer's, one naming a file of goh's."""
    repo, goh = tmp_path / "repo", tmp_path / "goh"
    for p, body in {
        repo / "tools" / "own.sh": "#!/bin/sh\nexit 1\n",
        repo / "tools" / "uses.sh": '#!/bin/sh\nGOH="${GOH_DIR:-/g}"\n. "$GOH/tui/lib.sh"\n',
        repo / "tools" / "runs.sh": '#!/bin/sh\npython3 "$GOH_DIR/lib/other.py" "$1"\n',
        repo / "tools" / "odd.sh": '#!/bin/sh\nsh "$GOH_DIR/$1"\n',
        goh / "tui" / "lib.sh": "say() { :; }\n", goh / "lib" / "other.py": "X = 1\n",
        goh / "gates" / "local_ci.sh": "#!/bin/sh\n", goh / "Cargo.toml": "[workspace]\n",
    }.items():  # fmt: skip
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return repo, goh


def moved(step: str, trees: tuple[Path, Path], rel: str, monkeypatch) -> bool:
    """Whether a byte more in goh's REL moves STEP's closure."""
    repo, goh = trees
    monkeypatch.setattr(hold.gate_identity, "build_inputs", lambda _g: ["Cargo.toml"])
    was = hold.closure(step, repo, goh)
    assert was is not None and was == hold.closure(step, repo, goh)
    for cached in (hold.step_closure._hashed, hold.step_closure._code):
        cached.cache_clear()
    (goh / rel).write_text((goh / rel).read_text() + "# x\n")
    return hold.closure(step, repo, goh) != was


@pytest.mark.parametrize(("step", "rel", "want"), [
    ("sh tools/own.sh", "lib/other.py", False),  # it reads nothing of goh's
    ("sh tools/own.sh", "gates/local_ci.sh", False),  # nor is the runner its step's
    ("sh tools/uses.sh", "lib/other.py", False),
    ("sh tools/uses.sh", "tui/lib.sh", True),
    ("sh tools/runs.sh", "lib/other.py", True),
    ("sh tools/runs.sh", "tui/lib.sh", False),
    ("sh tools/odd.sh", "lib/other.py", True),  # a path no reading places: all of goh
    (hold.UNNAMED, "lib/other.py", True),
    ("structural: one roadmap", "lib/other.py", True),
    ("goh roadmap", "Cargo.toml", True),  # the binary's build
])  # fmt: skip
def test_a_closure_moves_with_what_its_step_reads(step, rel, want, trees, monkeypatch) -> None:
    assert moved(step, trees, rel, monkeypatch) is want


def test_a_same_tip_is_held_until_the_tip_or_every_step_moves(git, monkeypatch) -> None:
    now = {"a": "c1", "b": "c1"}
    monkeypatch.setattr(hold, "closure", lambda s, _r, _g: now[s])
    git.log = [PIN, ("o1", "feat: work", "src/a.py\n")]
    last = {"status": "refused", "tip": "o1", "steps": {"a": "c1", "b": "c1"}}
    repo = Path("/p")
    said = hold.held(last, repo, repo)
    assert said and "refused at a; b" in said and "o1" in said
    now["a"] = "c2"  # b fails as it did, whatever a does
    assert "moved nothing b reads" in hold.held(last, repo, repo)
    now["b"] = "c2"
    assert hold.held(last, repo, repo) is None
    now.update(a="c1", b="c1")
    git.log = [PIN, ("o2", "fix: it", "src/a.py\n"), *git.log[1:]]
    assert hold.held(last, repo, repo) is None  # the tip moved


@pytest.mark.parametrize("last", [
    None,
    {"status": "pushed", "tip": "o1", "steps": {"a": "c1"}},
    {"status": "refused", "steps": {"a": "c1"}},  # no gate judged it
    {"status": "refused", "tip": "o1"},
    {"status": "refused", "tip": "o1", "steps": {"a": None}},  # no closure was known
])  # fmt: skip
def test_what_no_gate_judged_holds_nothing(last, git, monkeypatch) -> None:
    monkeypatch.setattr(hold, "closure", lambda *_: None)
    git.log = [("o1", "feat: work", "src/a.py\n")]
    assert hold.held(last, Path("/p"), Path("/p")) is None
