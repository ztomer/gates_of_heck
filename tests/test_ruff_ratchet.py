"""gates/ruff_ratchet.py: a repo's declared ruff rule set is gated whole, its debt a per-rule count
that only falls (G17c).

Both directions, against a real ruff over a throwaway tree: a rule over its count, or a rule with
findings and no count, is red and names every finding; a count above the truth is red too (it is
room for the next regression); a clean tree with no counts is green; --lower records a shrink and
refuses while anything rose; --seed is first adoption only; a missing or malformed table, or a ruff
that refused its config, is a precondition (exit 2), never a pass.
"""

import importlib.util
import json
import shutil

import pytest
from conftest import REPO_ROOT

pytestmark = pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not available")

_SPEC = importlib.util.spec_from_file_location(
    "ruff_ratchet", REPO_ROOT / "gates" / "ruff_ratchet.py"
)
rr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rr)

TWO_UNUSED = "import os\nimport sys\n"


@pytest.fixture()
def tree(tmp_path, monkeypatch):
    (tmp_path / "ruff.toml").write_text('[lint]\nselect = ["F401", "F841"]\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def table(tree, counts):
    (tree / rr.TABLE).write_text(json.dumps(counts), encoding="utf-8")


def test_a_rule_at_its_count_passes(tree, capsys):
    (tree / "a.py").write_text(TWO_UNUSED, encoding="utf-8")
    table(tree, {"F401": 2})
    assert rr.main([]) == 0
    assert "2 finding(s) owed under 1 counted rule(s)" in capsys.readouterr().out


def test_a_rule_over_its_count_is_red_and_names_the_findings(tree, capsys):
    (tree / "a.py").write_text(TWO_UNUSED, encoding="utf-8")
    table(tree, {"F401": 1})
    assert rr.main([]) == 1
    err = capsys.readouterr().err
    assert "F401: 2 finding(s) against a count of 1" in err
    assert " a.py:1:8: F401" in err and " a.py:2:8: F401" in err  # repo-relative


def test_a_rule_with_findings_and_no_count_is_red(tree, capsys):
    (tree / "a.py").write_text("def f():\n    x = 1\n", encoding="utf-8")
    table(tree, {})
    assert rr.main([]) == 1
    assert "F841: 1 finding(s), and no count" in capsys.readouterr().err


def test_a_count_above_the_truth_is_red(tree, capsys):
    (tree / "a.py").write_text("import os\n", encoding="utf-8")
    table(tree, {"F401": 2})
    assert rr.main([]) == 1
    assert "F401: 1 finding(s), count 2 -- lower it" in capsys.readouterr().err


def test_a_count_for_a_cleared_rule_is_stale(tree, capsys):
    (tree / "a.py").write_text("print(1)\n", encoding="utf-8")
    table(tree, {"F841": 3})
    assert rr.main([]) == 1
    assert "F841: 0 finding(s), count 3" in capsys.readouterr().err


def test_a_clean_tree_with_no_counts_passes(tree, capsys):
    (tree / "a.py").write_text("print(1)\n", encoding="utf-8")
    table(tree, {})
    assert rr.main([]) == 0
    assert "clean" in capsys.readouterr().out


def test_lower_records_a_shrink(tree):
    (tree / "a.py").write_text("import os\n", encoding="utf-8")
    table(tree, {"F401": 2, "F841": 1})
    assert rr.main(["--lower"]) == 0
    assert json.loads((tree / rr.TABLE).read_text(encoding="utf-8")) == {"F401": 1}
    assert rr.main([]) == 0


def test_lower_refuses_while_a_rule_rose(tree):
    (tree / "a.py").write_text(TWO_UNUSED, encoding="utf-8")
    table(tree, {"F401": 1})
    assert rr.main(["--lower"]) == 1
    assert json.loads((tree / rr.TABLE).read_text(encoding="utf-8")) == {"F401": 1}


def test_seed_writes_the_table_once(tree):
    (tree / "a.py").write_text(TWO_UNUSED, encoding="utf-8")
    assert rr.main(["--seed"]) == 0
    assert json.loads((tree / rr.TABLE).read_text(encoding="utf-8")) == {"F401": 2}
    assert rr.main(["--seed"]) == 2


@pytest.mark.parametrize(
    "body",
    [None, "not json", '["F401"]', '{"F401": -1}', '{"F401": 1.5}', '{"F401": true}', '{"x": 1}'],
    ids=["missing", "unparseable", "not-an-object", "negative", "fraction", "bool", "not-a-rule"],
)
def test_a_missing_or_malformed_table_is_a_precondition(tree, body):
    (tree / "a.py").write_text("print(1)\n", encoding="utf-8")
    if body is not None:
        (tree / rr.TABLE).write_text(body, encoding="utf-8")
    assert rr.main([]) == 2


def test_a_ruff_that_refused_its_config_is_a_precondition(tree):
    (tree / "ruff.toml").write_text('[lint]\nselect = ["NOPE999"]\n', encoding="utf-8")
    (tree / "a.py").write_text("print(1)\n", encoding="utf-8")
    table(tree, {})
    assert rr.main([]) == 2


def test_list_prints_every_finding(tree, capsys):
    (tree / "a.py").write_text(TWO_UNUSED, encoding="utf-8")
    table(tree, {"F401": 2})
    assert rr.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert out.count("F401 ") == 2 and "2 finding(s) owed" in out
