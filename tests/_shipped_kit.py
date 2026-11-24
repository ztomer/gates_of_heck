"""test_ship's project: `make_shipped`, and the `shipped` fixture's template (GOH.FIXTURECOST).

Its own module so test_ship.py stays under the headroom line (tests/test_cap_headroom.py); the
suites that import these names from test_ship still do, through its re-export.
"""

from __future__ import annotations

from pathlib import Path

from _fast_git import fast_init
from _tree_clone import clone_tree, template
from conftest import commit_all, git, write

# The fake loopctl: every call appended to $FAKE_LOOPCTL_LOG; `verify` exits $FAKE_VERIFY_RC.
FAKE_LOOPCTL = """\
import json, os, sys
with open(os.environ["FAKE_LOOPCTL_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1] == "verify":  # $FAKE_CARD_ERR: failing lines, `|`-separated
    print("fake card line 1")
    sys.path.insert(0, os.environ["FAKE_GOH_ROOT"])  # the card's own marks: tui.lib, as loopctl's
    from tui.lib import err
    for line in filter(None, os.environ.get("FAKE_CARD_ERR", "").split("|")):
        err(line)
    sys.exit(int(os.environ.get("FAKE_VERIFY_RC", "0")))
if sys.argv[1] == "ci":  # $FAKE_CI: the verdicts in order (the last repeats), counted in $FAKE_CI_N
    states = os.environ["FAKE_CI"].split(",")
    n_file = os.environ["FAKE_CI_N"]
    n = int(open(n_file).read()) if os.path.exists(n_file) else 0
    open(n_file, "w").write(str(n + 1))
    state = states[min(n, len(states) - 1)]
    print(f"CI {state}: fake verdict {n + 1}")
    if os.environ.get("FAKE_CI_WHY"):  # a red's why lines, `|`-separated, as loopctl's step()
        sys.path.insert(0, os.environ["FAKE_GOH_ROOT"])
        from tui.lib import step
        for line in os.environ["FAKE_CI_WHY"].split("|"):
            step(line)
    sys.exit({"green": 0, "red": 1, "pending": 3, "none": 4, "throttled": 75}[state])
"""


def shipped_clone(tmp_path: Path, factory) -> Path:
    """`make_shipped` over conftest's `repo`, built once per worker and cloned per test; only the
    origin's URL (an absolute path) is written per test (GOH.FIXTURECOST)."""
    built = template(factory, "shipped", _build_shipped)
    for part in ("proj", "remote.git", "loopctl.py"):
        clone_tree(built / part, tmp_path / part)
    git(tmp_path / "proj", "remote", "set-url", "origin", str(tmp_path / "remote.git"))
    return tmp_path / "proj"


def _build_shipped(root: Path) -> None:
    proj = root / "proj"
    fast_init(proj, "main")
    write(proj, "README.md", "# fixture\n")
    commit_all(proj)
    make_shipped(proj, root)
    git(root / "remote.git", "remote", "remove", "origin")  # it named the template's proj
    git(proj, "remote", "set-url", "origin", "set-per-clone")


def make_shipped(repo: Path, tmp_path: Path) -> Path:
    """`repo` with a bare `origin` it tracks, tagged v1.0.0 and pushed, then one commit on top."""
    remote = tmp_path / "remote.git"
    git(repo, "clone", "-q", "--bare", str(repo), str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "tag", "v1.0.0")
    git(repo, "push", "-q", "-u", "origin", "main", "v1.0.0")
    (repo / "f.txt").write_text("change\n")
    git(repo, "add", "f.txt")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "feat: change")
    (tmp_path / "loopctl.py").write_text(FAKE_LOOPCTL)
    return repo
