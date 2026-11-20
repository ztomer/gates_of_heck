"""A ruff on the box newer than goh's pin is a bump the land carries, never a red
(GOH.RUFFCURRENCY).

2026-11-19: ruff 0.17.6 was released at 15:55 UTC and nothing was red until 20:57 EST, when a uv
tool upgrade moved ~/.local/bin/ruff to it. From then every land was refused ("the pinned ruff
0.17.5 is not the only ruff here") until a hand bump (e7bf6d43). Here the NEXT release is faked: a
`ruff` on PATH and an interpreter whose `-m ruff` answer one patch past the manifest's pin, and a
land gate that judges the tree the way tools/pytest.sh does (gates/ruff_agree.py --pin, the tree's
own pin). Before the fix that gate was red; now the land commits the bump on the lane's tip, its own
commit, and lands green.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, git, hermetic_env
from timing_bounds import patience

LAND = REPO_ROOT / "tools" / "land.sh"
CURRENCY = REPO_ROOT / "gates" / "ruff_currency.py"
AGREE = REPO_ROOT / "gates" / "ruff_agree.py"
MANIFEST = REPO_ROOT / "gates" / "required_tools.tsv"

sys.path.insert(0, str(REPO_ROOT / "gates"))
import ruff_currency  # noqa: E402 -- gates/ is put on sys.path above

PIN = ruff_currency.pin_of(MANIFEST.read_text(encoding="utf-8"))


def _next(version: str) -> str:
    """The release after `version`: the fake one this test installs."""
    *head, patch = version.split(".")
    return ".".join([*head, str(int(patch) + 1)])


NEXT = _next(PIN)


def _box(tmp_path: Path, path_ruff: str, interp_ruff: str) -> dict[str, str]:
    """A box: a `ruff` on PATH at `path_ruff`, and GOH_PY, a real python whose
    `-m ruff --version` says the release in its state file (`interp_ruff`) and whose
    `-m pip install ruff==X` moves it."""
    bin_dir = tmp_path / "box" / "bin"
    bin_dir.mkdir(parents=True)
    ruff = bin_dir / "ruff"
    ruff.write_text(f'#!/bin/sh\necho "ruff {path_ruff}"\n')
    ruff.chmod(0o755)
    state = tmp_path / "box" / "interp_ruff"
    state.write_text(interp_ruff)
    py = tmp_path / "box" / "py" / "python3"
    py.parent.mkdir()
    py.write_text(
        "#!/bin/sh\n"
        f'state="{state}"\n'
        'if [ "$1 $2 $3" = "-m ruff --version" ]; then echo "ruff $(cat "$state")"; exit 0; fi\n'
        'if [ "$1 $2 $3" = "-m pip install" ]; then\n'
        '  for a in "$@"; do case "$a" in ruff==*) printf %s "${a#ruff==}" >"$state";; esac; done\n'
        "  exit 0\n"
        "fi\n"
        f'exec "{sys.executable}" "$@"\n'
    )
    py.chmod(0o755)
    return hermetic_env(drop_git=True, GOH_PY=str(py),
                        PATH=os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]))  # fmt: skip


def _goh_tree(repo: Path) -> None:
    """`repo` given a manifest in goh's shape (the pin's comment, ruff rows, another tool's pin) and
    a changelog/ directory, committed on main."""
    (repo / "gates").mkdir()
    (repo / "gates" / "required_tools.tsv").write_text(
        f"#   ruff ={PIN}         PIN: the release the house Python gates judge with.\n"
        f"python\truff\tpip\tpython3 -m pip install ruff\tgates/py_gate.sh\t={PIN}\n"
        f"goh-suite\truff\tpip\tpython3 -m pip install ruff\ttools/pytest.sh\t={PIN}\n"
        f"goh-suite\tpytest\tpip\tpython3 -m pip install pytest\ttools/pytest.sh\t={PIN}\n"
    )
    (repo / "changelog").mkdir()
    (repo / "changelog" / "README.md").write_text("fragments\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "goh's manifest")


def _lane(repo: Path, tmp_path: Path) -> Path:
    wt = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "lane", str(wt))
    (wt / "work.txt").write_text("the lane's work\n")
    git(wt, "add", "work.txt")
    git(wt, "commit", "-q", "--no-verify", "-m", "the lane's work")
    return wt


# The land gate: tools/pytest.sh's ruff check, judging the tree's own pin against the box.
# The manifest's pin as gates/ruff_house.py reads it, from the tree being landed.
TREE_PIN = "awk -F'\\t' '!/^#/ && $2 == \"ruff\" && $6 ~ /^=/ {print substr($6, 2); exit}' "
GATE = f'"$GOH_PY" "{AGREE}" --pin "$({TREE_PIN} gates/required_tools.tsv)"'


def _land(wt: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(LAND)], cwd=wt, env={**env, "GOH_LAND_GATE": GATE},
                          capture_output=True, text=True, timeout=patience(120),
                          stdin=subprocess.DEVNULL, check=False)  # fmt: skip


def test_the_fake_release_reds_the_gate_on_a_tree_still_at_the_pin(repo: Path, tmp_path: Path):
    """The instrument: the gate this file lands through IS red on the 2026-11-19 box shape."""
    _goh_tree(repo)
    env = _box(tmp_path, NEXT, PIN)
    r = subprocess.run(["bash", "-c", GATE], cwd=repo, env=env, capture_output=True, text=True,
                       timeout=patience(60), stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert r.returncode == 1, r.stdout + r.stderr
    assert f"the pinned ruff {PIN} is not the only ruff here" in r.stderr


def test_a_land_that_meets_a_newer_ruff_lands_the_pin_bump_as_its_own_commit(repo, tmp_path):
    _goh_tree(repo)
    wt = _lane(repo, tmp_path)
    env = _box(tmp_path, NEXT, PIN)  # the uv tool moved; the house interpreter did not
    r = _land(wt, env)
    assert r.returncode == 0, r.stdout + r.stderr
    subjects = git(repo, "log", "--format=%s", "-3").splitlines()
    name = ruff_currency.item(NEXT)
    assert subjects == [
        f"{name}: ruff {NEXT} is the house pin",
        "the lane's work",
        "goh's manifest",
    ]
    landed = (repo / "gates" / "required_tools.tsv").read_text(encoding="utf-8")
    assert ruff_currency.pin_of(landed) == NEXT
    assert f"={PIN}" not in "".join(ln for ln in landed.splitlines() if "\truff\t" in ln)
    assert f"#   ruff ={NEXT} " in landed
    assert (repo / "changelog" / f"{name}.md").read_text() == ruff_currency.fragment(PIN, NEXT)
    # The house interpreter was brought to the release, through the manifest's pip line.
    assert (tmp_path / "box" / "interp_ruff").read_text() == NEXT
    bump = git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(bump) == ["changelog/" + f"{name}.md", "gates/required_tools.tsv"]


def test_a_box_at_the_pin_lands_no_bump(repo: Path, tmp_path: Path):
    _goh_tree(repo)
    wt = _lane(repo, tmp_path)
    r = _land(wt, _box(tmp_path, PIN, PIN))
    assert r.returncode == 0, r.stdout + r.stderr
    assert git(repo, "log", "--format=%s", "-2").splitlines() == [
        "the lane's work",
        "goh's manifest",
    ]


def test_an_older_ruff_on_the_box_is_never_a_downgrade_of_the_pin(repo: Path, tmp_path: Path):
    """A stale tool is ruff_agree's red to name, not a pin to move backwards."""
    _goh_tree(repo)
    older = PIN.rsplit(".", 1)[0] + ".0" if not PIN.endswith(".0") else "0.0.1"
    env = _box(tmp_path, older, PIN)
    r = subprocess.run([sys.executable, str(CURRENCY), "--bump-tree", str(repo)], env=env,
                       capture_output=True, text=True, timeout=patience(60),
                       stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert (r.returncode, r.stdout) == (0, ""), r.stderr
    assert ruff_currency.pin_of((repo / "gates/required_tools.tsv").read_text()) == PIN


def test_a_second_lane_carrying_the_same_bump_drops_it_on_rebase(repo: Path, tmp_path: Path):
    """Two lanes that met the release both commit the bump; main keeps one."""
    _goh_tree(repo)
    env = _box(tmp_path, NEXT, PIN)
    first = _lane(repo, tmp_path)
    second = tmp_path / "wt2"
    git(repo, "worktree", "add", "-q", "-b", "lane2", str(second), "HEAD")
    (second / "other.txt").write_text("other\n")
    git(second, "add", "other.txt")
    git(second, "commit", "-q", "--no-verify", "-m", "the other lane")
    assert _land(first, env).returncode == 0
    r = _land(second, env)
    assert r.returncode == 0, r.stdout + r.stderr
    subjects = git(repo, "log", "--format=%s").splitlines()
    assert subjects.count(f"{ruff_currency.item(NEXT)}: ruff {NEXT} is the house pin") == 1, (
        subjects
    )


def test_an_interpreter_that_cannot_take_the_release_commits_nothing(repo: Path, tmp_path: Path):
    _goh_tree(repo)
    env = _box(tmp_path, NEXT, PIN)
    py = Path(env["GOH_PY"])
    py.write_text(py.read_text().replace("  exit 0\nfi", "  echo 'no network' >&2; exit 1\nfi"))
    r = subprocess.run([sys.executable, str(CURRENCY), "--bump-tree", str(repo)], env=env,
                       capture_output=True, text=True, timeout=patience(60),
                       stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert r.returncode == 1
    assert f"without ruff {NEXT}" in r.stderr
    assert git(repo, "status", "--porcelain").strip() == ""
    assert git(repo, "log", "--format=%s", "-1").strip() == "goh's manifest"


def test_bumped_moves_every_ruff_row_and_the_comment_and_nothing_else():
    text = MANIFEST.read_text(encoding="utf-8")
    moved = ruff_currency.bumped(text, PIN, NEXT)
    changed = [(a, b) for a, b in zip(text.splitlines(), moved.splitlines(), strict=True) if a != b]
    rows = [ln for ln in text.splitlines() if not ln.startswith("#") and "\truff\t" in ln]
    assert len(changed) == len(rows) + 1
    assert all(a.replace(f"={PIN}", f"={NEXT}") == b for a, b in changed)
