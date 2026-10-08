"""`goh requires-call`: a file that CALLS X must also CALL Y, read from the AST (BACKLOG 1.2).

ZoneWM grew two repo-local gates of this one shape (`tools/check_probe_placement.py`: a tool that
relaunches the agent must judge window placement; `tools/check_probe_courtesy.py`: a long runner
must keep calling `.check()`) -- generic tooling, so it is a goh check configured by rows. The
first cases are ZoneWM's own selftest, lifted (2026-10-08). The rule is read from the AST because
the first regex version passed with the call DELETED: its docstring named the call.
"""

from __future__ import annotations

import re
from pathlib import Path

from conftest import run_goh

PLACEMENT = """
[[rule]]
name = "placement"
why = "a relaunch can move the owner's windows"
files = ["tools/*.py"]
calls = ["relaunch"]
must_call = ["window_placement.guarded", "window_placement.verify"]

[rule.exempt]
"tools/exempt.py" = "relaunch is stubbed: no agent restarts"
"tools/exempt_stale.py" = "stubbed"
"tools/exempt_judges.py" = "stubbed"
"tools/gone.py" = "stubbed"
"""

PLANTED = {
    "bare.py": "import placement_timing_probe as agent\ndef run():\n    agent.relaunch()\n",
    "guarded.py": "import window_placement as placement\nimport placement_timing_probe as agent\n"
    "def main():\n    return placement.guarded(lambda: agent.relaunch())\n",
    "verified.py": "import window_placement as p\ndef close(s):\n    relaunch()\n"
    "    p.verify(s['placed'])\n",
    "prose.py": '"""We call placement.guarded(run) around the run."""\n'
    "import window_placement as placement\ndef run():\n    relaunch()\n",
    "other_verify.py": "import window_placement\nimport hashlib as h\ndef run():\n    relaunch()\n"
    "    h.verify()\n",
    "string_only.py": 'ANCHOR = "    agent.relaunch()\\n"\n',
    "exempt.py": "def run():\n    relaunch()\n",
    "exempt_stale.py": "def run():\n    pass\n",
    "exempt_judges.py": "import window_placement as p\ndef run():\n    relaunch()\n    p.verify(1)\n",
}


def _estate(repo: Path, rules: str = PLACEMENT, files: dict[str, str] = PLANTED) -> Path:
    (repo / "tools").mkdir(exist_ok=True)
    for name, text in files.items():
        (repo / "tools" / name).write_text(text)
    (repo / "requires_call.toml").write_text(rules)
    return repo


def _check(repo: Path):
    return run_goh(repo, "requires-call", "--rules", "requires_call.toml", ".")


def _named(r) -> str:
    return r.stdout + r.stderr


def test_zonewm_selftest_lifted(repo: Path) -> None:
    r = _check(_estate(repo))
    out = _named(r)
    assert r.returncode == 1, out
    unjudged = set(re.findall(r"tools/(\w+\.py):\d+: calls relaunch and never calls", out))
    # refused: a bare relaunch, a docstring that only NAMES guarded, another module's verify
    assert unjudged == {"bare.py", "prose.py", "other_verify.py"}, out
    # passed, named nowhere: guarded around the run, a teardown's own verify, relaunch named only
    # in a string, an exemption with its reason
    for name in ("guarded.py", "verified.py", "string_only.py", "exempt.py"):
        assert f"tools/{name}" not in out, out
    # stale: an exemption whose file no longer calls X, and one whose file is gone
    assert "tools/exempt_stale.py: stale exemption" in out, out
    assert "tools/gone.py: stale exemption" in out, out
    # stale: an exemption whose file has started judging itself
    assert "tools/exempt_judges.py: stale exemption -- now calls" in out, out


def test_the_violation_names_the_line_and_the_reason(repo: Path) -> None:
    out = _named(_check(_estate(repo)))
    assert "tools/bare.py:3: calls relaunch and never calls window_placement.guarded" in out, out
    assert "a relaunch can move the owner's windows" in out


def test_a_from_import_satisfies_and_a_same_named_function_elsewhere_does_not(repo: Path) -> None:
    files = {
        "from_import.py": "from window_placement import verify as judge\ndef run():\n"
        "    relaunch()\n    judge(1)\n",
        "own_verify.py": "def verify(x):\n    pass\ndef run():\n    relaunch()\n    verify(1)\n",
    }
    rules = PLACEMENT.split("[rule.exempt]")[0]
    out = _named(_check(_estate(repo, rules, files)))
    assert "tools/own_verify.py:4: calls relaunch" in out and "from_import.py" not in out, out


def test_a_clean_estate_passes_naming_what_it_checked(repo: Path) -> None:
    files = {k: PLANTED[k] for k in ("guarded.py", "verified.py", "string_only.py", "exempt.py")}
    rules = PLACEMENT.split("[rule.exempt]")[0] + '[rule.exempt]\n"tools/exempt.py" = "stubbed"\n'
    r = _check(_estate(repo, rules, files))
    assert r.returncode == 0, _named(r)
    assert "placement" in r.stdout and "1 exempt" in r.stdout, r.stdout


def test_finding_no_file_that_calls_x_is_a_floor_not_a_pass(repo: Path) -> None:
    rules = PLACEMENT.split("[rule.exempt]")[0]
    r = _check(_estate(repo, rules, {"quiet.py": "def run():\n    pass\n"}))
    assert r.returncode == 2 and "nothing was checked" in r.stderr, _named(r)


def test_a_rule_with_no_trigger_binds_every_file(repo: Path) -> None:
    """ZoneWM's long runners: a named set of files that must each keep calling `.check()`."""
    rules = """
[[rule]]
name = "long runners recheck"
why = "a run that lasts must keep checking for the owner"
files = ["tools/census_*.py"]
must_call = ["check"]
"""
    files = {
        "census_a.py": "def loop(presence):\n    presence.check()\n",
        "census_b.py": '"""calls presence.check() each round"""\ndef loop(p):\n    pass\n',
    }
    out = _named(_check(_estate(repo, rules, files)))
    assert "tools/census_b.py" in out and "census_a.py" not in out, out


def test_a_file_that_does_not_parse_is_named_not_skipped(repo: Path) -> None:
    rules = PLACEMENT.split("[rule.exempt]")[0]
    files = {"broken.py": "def run(:\n    relaunch()\n", "bare.py": PLANTED["bare.py"]}
    r = _check(_estate(repo, rules, files))
    assert r.returncode == 1 and "tools/broken.py: does not parse" in _named(r), _named(r)


def test_a_bad_rules_file_is_a_config_error(repo: Path) -> None:
    r = _check(_estate(repo, "[[rule]]\nname = 'x'\n", {}))
    assert r.returncode == 2 and "requires_call.toml" in r.stderr, _named(r)
