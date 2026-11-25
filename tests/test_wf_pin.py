"""`goh wf-pin` names every estate workflow pinning an older goh release, and `--bump` -- which goh
ship runs after each goh release -- moves them (GOH.WFPIN, GOH.CLASS.ROLLOUT).

On 2026-11-12 every estate workflow still pinned goh v0.95.0 or v0.96.0: the copy was one token per
workflow and nothing moved it. The three pin shapes below are the ones the estate carries (monitor,
divoom-control, sys_updater, ztools); a goh checkout with no pin is named, never rewritten.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import consumer_pins
import wf_pin

sys.path.pop(0)

SCRIPT = REPO_ROOT / "scripts" / "wf_pin.py"
SLUG = "owner/gates_of_heck"
CHECKOUT = """jobs:
  gate:
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      # goh at v0.90.0 -- prose, never rewritten
      - name: Check out gates_of_heck
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
          repository: owner/gates_of_heck
          ref: v0.96.0   # a NAMED goh release
          path: goh
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          repository: someone/else
          ref: v0.1.0
"""
ENV_TAG = """env:
  GOH_TAG: v0.71.0
jobs:
  gate:
    steps:
      - run: git clone --branch "$GOH_TAG" https://github.com/owner/gates_of_heck "$GOH_DIR"
"""
CLONE = """jobs:
  gate:
    steps:
      - run: |
          git clone --depth 1 --branch v0.95.0 https://github.com/owner/gates_of_heck "$GOH_DIR"
"""
UNPINNED = """jobs:
  gate:
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          repository: owner/gates_of_heck
          path: goh
      - run: git clone https://github.com/owner/gates_of_heck "$GOH_DIR"
"""

OLD_SHA = "13d906fb78065e701446a943ceed26e5ce919d6a"
ACTION = f"""jobs:
  gate:
    steps:
      - name: Meet goh's tool floors
        uses: owner/gates_of_heck/.github/actions/goh-floors@{OLD_SHA} # v0.71.0
        with:
          goh-dir: ${{{{ env.GOH_DIR }}}}
      - uses: owner/gates_of_heck/.github/actions/goh-floors@v0.96.0
      - uses: owner/gates_of_heck/.github/actions/goh-floors@{OLD_SHA}
      - uses: someone/else/.github/actions/x@{OLD_SHA} # v0.1.0
"""


def _env() -> dict[str, str]:
    return hermetic_env(drop_git=True)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True, env=_env(), stdin=subprocess.DEVNULL).stdout  # fmt: skip


def _repo(root: Path, name: str, flows: dict[str, str]) -> Path:
    repo = root / name
    (repo / ".github" / "workflows").mkdir(parents=True)
    for f, body in flows.items():
        (repo / ".github" / "workflows" / f).write_text(body)
    fast_init(repo)
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "fixture")
    return repo


def _run(root: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = _env()
    env["GOH_ESTATE_STATE"] = str(tmp_path / "state" / "estate_audit.json")
    return subprocess.run(
        ["python3", str(SCRIPT), "--root", str(root), "--slug", SLUG, "--tag", "v0.118.0", *args],
        capture_output=True, text=True, check=False, env=env, timeout=patience(120),
        stdin=subprocess.DEVNULL,
    )  # fmt: skip


def test_every_pin_shape_is_read_and_only_gohs():
    for body, tag in ((CHECKOUT, "v0.96.0"), (ENV_TAG, "v0.71.0"), (CLONE, "v0.95.0")):
        s = wf_pin.scan(body, SLUG)
        assert [p.tag for p in s.pins] == [tag], (body, s)
        assert s.unpinned == [], s
    s = wf_pin.scan(UNPINNED, SLUG)
    assert s.pins == [] and s.unpinned == [6, 8], s


def test_a_bump_moves_the_tag_and_nothing_else():
    s = wf_pin.scan(CHECKOUT, SLUG)
    out = wf_pin.bumped(CHECKOUT, s.pins, "v0.118.0")
    assert out == CHECKOUT.replace("ref: v0.96.0", "ref: v0.118.0"), out
    assert "goh at v0.90.0" in out and "ref: v0.1.0" in out  # prose and other repos untouched


def test_the_listing_names_each_stale_repo_and_writes_nothing(tmp_path):
    root = tmp_path / "estate"
    stale = _repo(root, "stale", {"ci.yml": CHECKOUT, "gate.yml": ENV_TAG})
    _repo(root, "current", {"ci.yml": CLONE.replace("v0.95.0", "v0.118.0")})
    _repo(root, "loose", {"ci.yml": UNPINNED})
    r = _run(root, tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "stale: .github/workflows/ci.yml, .github/workflows/gate.yml pin v0.71.0, v0.96.0" in out
    assert "current" not in out, out
    assert "loose: .github/workflows/ci.yml:6,8 checks out goh at no named release" in out, out
    assert (stale / ".github/workflows/ci.yml").read_text() == CHECKOUT


def test_the_bump_commits_the_workflows_alone_and_leaves_the_tree_clean(tmp_path):
    root = tmp_path / "estate"
    stale = _repo(root, "stale", {"ci.yml": CHECKOUT, "gate.yml": ENV_TAG, "x.yml": CLONE})
    (stale / "notes").write_text("another agent's untracked file\n")
    r = _run(root, tmp_path, "--bump")
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "stale: pinned v0.118.0 in" in out, out
    changed = sorted(_git(stale, "show", "--name-only", "--format=", "HEAD").split())
    assert changed == [f".github/workflows/{f}" for f in ("ci.yml", "gate.yml", "x.yml")], changed
    assert _git(stale, "status", "--porcelain") == "?? notes\n"
    assert "ref: v0.118.0" in (stale / ".github/workflows/ci.yml").read_text()
    again = _run(root, tmp_path)
    assert again.returncode == 0, again.stdout + again.stderr


def test_a_repo_with_an_uncommitted_workflow_is_set_aside_untouched(tmp_path):
    root = tmp_path / "estate"
    stale = _repo(root, "stale", {"ci.yml": CHECKOUT})
    wip = CHECKOUT + "# wip\n"
    (stale / ".github/workflows/ci.yml").write_text(wip)
    head = _git(stale, "rev-parse", "HEAD")
    r = _run(root, tmp_path, "--bump")
    out = r.stdout + r.stderr
    assert "set aside" in out and "uncommitted" in out, out
    assert _git(stale, "rev-parse", "HEAD") == head
    assert (stale / ".github/workflows/ci.yml").read_text() == wip


def test_goh_ship_runs_the_bump_after_each_goh_release():
    """The release that makes the pin stale moves it: goh's ship install step is the bump."""
    gatesrc = (REPO_ROOT / ".gatesrc").read_text()
    assert "GOH_SHIP_INSTALL='tools/ship_install_goh.sh'" in gatesrc
    body = (REPO_ROOT / "tools" / "ship_install_goh.sh").read_text()
    assert '"$GOH/scripts/wf_pin.py" --bump' in body and "GOH_SHIP_VERSION" in body


def test_a_goh_action_pin_is_read_and_its_commit_and_tag_move_together():
    """GOH.WFPINACTION: homebrew-tap, monitor and four more used goh-floors by commit, v0.54.1 to
    v0.96.0, and no bump moved them. The commit and the release beside it move as one; a commit
    naming no release is unpinned, never guessed."""
    s = wf_pin.scan(ACTION, SLUG)
    assert [(p.line, p.tag, p.commit) for p in s.pins] == [
        (5, "v0.71.0", True),
        (5, "v0.71.0", False),
        (8, "v0.96.0", False),
    ], s
    assert s.unpinned == [9], s
    new = "a" * 40
    out = consumer_pins.rewrite(ACTION, [(p, new if p.commit else "v0.118.0") for p in s.pins])
    assert f"goh-floors@{new} # v0.118.0\n" in out, out
    assert "goh-floors@v0.118.0\n" in out, out
    assert f"goh-floors@{OLD_SHA}\n" in out and f"x@{OLD_SHA} # v0.1.0" in out, out


def test_the_bump_moves_a_goh_action_to_the_releases_commit(tmp_path):
    root = tmp_path / "estate"
    tag = "v0.118.0"
    sha = _git(REPO_ROOT, "rev-parse", f"{tag}^{{commit}}").strip()
    stale = _repo(root, "stale", {"ci.yml": ACTION.replace(f"@{OLD_SHA}\n", "@v0.1.0\n")})
    r = _run(root, tmp_path, "--bump", "--sweep", "none")
    assert r.returncode == 0, r.stdout + r.stderr
    text = (stale / ".github/workflows/ci.yml").read_text()
    assert f"goh-floors@{sha} # {tag}\n" in text, text
    assert f"goh-floors@{tag}\n" in text, text


def test_the_owners_listing_names_whose_unpushed_commit_holds_a_pin_back(
    tmp_path, monkeypatch, capsys
):
    """GOH.PINSWEEP: a release commits its pin beside an owner's unpushed commit and can push
    neither; goh ship's preflight names those before it starts, and never refuses for one."""
    ahead = {
        "held": [("p", "ci: pin"), ("o2", "feat: newest"), ("o1", "feat: older")],
        "clear": [("p", "ci: pin")],
        "unpinned": [("o1", "feat: theirs")],
    }
    repos = [tmp_path / n for n in (*ahead, "goh")]
    monkeypatch.setattr(wf_pin, "GOH", tmp_path / "goh")
    monkeypatch.setattr(wf_pin.estate_registry, "walk_repos", lambda *_: repos)
    monkeypatch.setattr(wf_pin.estate_registry, "union", lambda found, _reg: (found, []))
    monkeypatch.setattr(wf_pin, "stale", lambda r, _s, _t: (
        {"ci.yml": [1]} if r.name in ("held", "clear") else {}, {}))  # fmt: skip
    monkeypatch.setattr(wf_pin.consumer_pins, "stale_ruff", lambda *_: {})
    monkeypatch.setattr(wf_pin, "workflows", lambda _r: [])
    monkeypatch.setattr(wf_pin.consumer_pins, "ahead", lambda r: ("origin/main", ahead[r.name]))
    monkeypatch.setattr(wf_pin.consumer_pins, "is_pin_commit", lambda _r, sha, _s: sha == "p")
    argv = ["--root", str(tmp_path), "--slug", SLUG, "--tag", "v0.118.0", "--owners"]
    assert wf_pin.main(argv) == 0
    said = "".join(capsys.readouterr())
    assert "held: 2 commits ahead of origin/main are its owner's (feat: newest)" in said, said
    assert "clear" not in said and "unpinned" not in said and len(said.splitlines()) == 1, said


def test_a_schedule_that_is_no_number_refuses_before_anything_is_committed(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("GOH_PIN_SWEEP_QUIET", "six hours")
    monkeypatch.setattr(wf_pin.estate_registry, "walk_repos", lambda *_: [])
    monkeypatch.setattr(wf_pin.estate_registry, "union", lambda found, _reg: (found, []))
    argv = ["--root", str(tmp_path), "--slug", SLUG, "--tag", "v0.118.0", "--bump"]
    assert wf_pin.main(argv) == 2
    assert "GOH_PIN_SWEEP_QUIET='six hours' is not seconds" in "".join(capsys.readouterr())
