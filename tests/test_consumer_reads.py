"""A change to a goh path a consumer names runs the release canary at pre-step (GOH.LANDMISS1119).

GOH.PYTESTPIN (2026-11-19) moved pytest from a floor to a pin in gates/required_tools.tsv, which
dropped it from `gates/required_tools.py --floors`. sys_updater's tools/check_gate_floors.sh runs
`"$GOH/gates/required_tools.py" --floors` and refuses without pytest's floor; no goh test runs a
consumer's script, so the pre-step's tests were green and the land gate's release canary refused
after 502 s. Pinned here: the miss's own changed-file set, replayed against a fixture estate,
chooses the canary for the consumer naming the path; what a consumer only mentions in prose, or
names of its OWN tree, or what no gate runs (tests/, docs/, *.md) chooses nothing; and the
pre-step's `--run` hands the canary over only when a consumer reads the change.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "tools"))
import affected_tests as at

from lib import consumer_reads

# GOH.PYTESTPIN's first land (d0aa17a3, red at the release canary): every path it changed.
PYTESTPIN = [
    ".github/workflows/gate.yml", "CHANGELOG.md", "changelog/GOH.CIRED01396.md",
    "changelog/GOH.ENABLEROOT.md", "changelog/GOH.LANDLEAKS.md", "changelog/GOH.MIDNIGHTDATE.md",
    "changelog/GOH.PROFKEYMISS.md", "changelog/GOH.PYTESTPIN.md", "docs/map.md", "docs/reuse.md",
    "gates/required_pins.py", "gates/required_tools.py", "gates/required_tools.tsv",
    "lib/reuse_audit.py", "tests/test_ci_install_pins.py", "tests/test_consumer_pins.py",
    "tests/test_required_tools_floors.py", "tests/test_required_tools_manifest.py",
    "tests/test_reuse_audit_root.py", "tools/pytest.sh",
]  # fmt: skip
# sys_updater's read, as its tools/check_gate_floors.sh spells it.
FLOORS = """GOH="${GOH_DIR:-${GOH:?the harness}}"
table="$(bash "$GOH/gates/python.sh" "$GOH/gates/required_tools.py" --floors)"
"""


def _repo(path: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text)
    fast_init(path)
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, stdin=subprocess.DEVNULL)
    return path


@pytest.fixture
def estate(tmp_path: Path) -> tuple[Path, list[Path]]:
    """(a goh tree, its consumers): one reading --floors, one naming its own tools/ and only
    MENTIONING goh's gates in its README."""
    goh = _repo(tmp_path / "goh", dict.fromkeys(PYTESTPIN, "x\n") | {"gates/python.sh": "x\n"})
    reader = _repo(tmp_path / "sys_updater", {".gatesrc": "", "tools/check_gate_floors.sh": FLOORS})
    other = _repo(
        tmp_path / "other",
        {
            ".gatesrc": "",
            "tools/pytest.sh": "x\n",
            "tools/gate.sh": '"$ROOT/tools/pytest.sh" -q\n',  # its own tools/pytest.sh
            "README.md": "runs `$GOH_DIR/gates/required_tools.py --floors` in CI\n",  # prose
        },
    )
    return goh, [reader, other]


def test_the_pytestpin_change_chooses_the_canary_for_the_consumer_reading_floors(estate) -> None:
    goh, repos = estate
    found = consumer_reads.readers(goh, PYTESTPIN, repos)
    assert found == {str(repos[0]): ["gates/required_tools.py"]}


def test_own_paths_prose_and_paths_no_gate_runs_choose_nothing(estate) -> None:
    goh, repos = estate
    assert consumer_reads.readers(goh, ["tools/pytest.sh"], repos) == {}  # the consumer's own
    docs = ["docs/map.md", "tests/test_required_tools_floors.py", "CHANGELOG.md"]
    assert consumer_reads.looked_for(docs) == []
    assert consumer_reads.readers(goh, docs, repos) == {}


def test_a_variable_named_any_way_and_the_checkout_by_name_both_read(tmp_path: Path) -> None:
    goh = _repo(tmp_path / "goh", {"gates/required_tools.py": "x\n", "tui/lib.sh": "x\n"})
    a = _repo(tmp_path / "a", {".gatesrc": "", "t.sh": '. "${HARNESS}/tui/lib.sh"\n'})
    b = _repo(tmp_path / "b", {".gatesrc": "", "t.py": "P = '/srv/gates_of_heck/tui/lib.sh'"})
    c = _repo(tmp_path / "c", {".gatesrc": "", "t.sh": '"$GOH/tui/lib.shx"\n'})  # another file
    found = consumer_reads.readers(goh, ["tui/lib.sh"], [a, b, c])
    assert found == {str(a): ["tui/lib.sh"], str(b): ["tui/lib.sh"]}


def test_the_pre_step_runs_the_canary_only_when_a_consumer_reads_the_change(
    estate, monkeypatch: pytest.MonkeyPatch
) -> None:
    goh, repos = estate
    (goh / "scripts").mkdir()
    (goh / "scripts" / "release_canary.py").write_text("")
    ran: list[list[str]] = []
    monkeypatch.setattr(at, "timed", lambda label, argv, **_: ran.append(argv) or 7)
    monkeypatch.delenv("GATES_OF_HECK_SUITE_RUNNER", raising=False)
    assert at.canary(goh, ["tests/_drift_guard.py", "lib/x.py"], repos) == 0  # GOH.CIWALL's kind
    assert ran == []
    assert at.canary(goh, PYTESTPIN, repos) == 7  # the canary's verdict is the pre-step's
    assert ran == [[sys.executable, str(goh / "scripts" / "release_canary.py")]]
    monkeypatch.setenv("GATES_OF_HECK_SUITE_RUNNER", "1")  # a fixture land inside a goh suite
    assert at.canary(goh, PYTESTPIN, repos) == 0
    assert len(ran) == 1
