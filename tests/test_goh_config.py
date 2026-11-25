"""`goh config` (lib/config_ref.py, GOH.CONFIGMD): a file declares a key, code says who reads it.

docs/config.md was one 200 KB table every lane edited, and its gate asked whether a key was a
substring of it: a key that is the prefix of a declared one passed, a digit ended the name, .github
was not read and a key no code reads any more stayed. These tests hold each refusal on a fixture
tree, the render, the old commits `--rev` still answers for, and the proof that two lanes that each
add a key share no file and need no merge driver.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, commit_all, git

from lib import config_ref

TREE_READS = ("docs/config.md", "docs/config/")


def decl(
    key: str, kind: str = "setting", area: str = "one", order: str = "", text: str = "It."
) -> str:
    place = f"Order: {order}\n" if order else ""
    return f"# {key}\nArea: {area}\nKind: {kind}\n{place}Default: unset\n\n{text}\n"


AREA = (
    "# One (`gates/a.sh`)\nOrder: 10\nLevel: 2\n\nBefore.\n\n"
    "| Key | Default | Meaning |\n|---|---|---|\n<!-- rows -->\n\nAfter.\n"
)
FILES = {
    "docs/config/_areas/one.md": AREA,
    "docs/config/GOH_SH.md": decl("GOH_SH", order="20", text="Wrapped\nover lines \\| kept."),
    "docs/config/GOH_PY.md": decl("GOH_PY, GOH_RS", order="10"),
    "gates/a.sh": 'echo "${GOH_SH:-}"\n',
    "lib/b.py": 'import os\n\nx = os.environ["GOH_PY"]\n',
    "crates/goh/src/c.rs": 'const K: &str = "GOH_RS";\n',
    "tests/test_a.py": "# names GOH_SH\n",
}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    for rel, text in FILES.items():
        put(tmp_path, rel, text)
    return tmp_path


def put(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")


def faults(root: Path) -> list[str]:
    return config_ref.faults(root, {})


def test_the_reference_is_rendered_from_the_files(root: Path) -> None:
    assert faults(root) == []
    assert config_ref.Ref(root).render() == (
        "## One (`gates/a.sh`)\n\nBefore.\n\n| Key | Default | Meaning |\n|---|---|---|\n"
        "| `GOH_PY`, `GOH_RS` | unset | It. |\n"
        "| `GOH_SH` | unset | Wrapped over lines \\| kept. |\n\nAfter.\n"
    )


def test_a_row_of_no_key_keeps_its_place_and_an_area_of_level_0_has_no_heading(root) -> None:
    put(root, "docs/config/_areas/one.md",
        AREA.replace("<!-- rows -->", "<!-- rows to 10 -->\n| (file) x | - | X. |\n<!-- rows -->")
        .replace("Level: 2", "Level: 0"))  # fmt: skip
    put(root, "docs/config/GOH_LATE.md", decl("GOH_LATE"))  # no Order: last
    out = config_ref.Ref(root).render()
    rows = [ln for ln in out.splitlines() if ln.startswith("|")][2:]
    assert [r.split(" | ")[0] for r in rows] == [
        "| `GOH_PY`, `GOH_RS`", "| (file) x", "| `GOH_SH`", "| `GOH_LATE`"]  # fmt: skip
    assert "#" not in out


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        ("gates/n.sh", 'x="${GOH_NEW:-1}"\n'),
        ("gates/n.sh", "python3 - <<'PY'\nimport os\nos.environ['GOH_NEW']\nPY\n"),
        ("hooks/claude/n.py", 'import os\nos.environ.get("GOH_NEW")\n'),
        ("crates/goh/src/n.rs", '// GOH_OLD\nlet u = "http://x"; let k = "GOH_NEW";\n'),
        (".github/workflows/n.yml", "env:\n  GOH_NEW: 1  # GOH_OLD\n"),
        ("install.sh", "export GOH_NEW=1\n"),
        ("hooks/claude/n.json", '{"env": {"GOH_NEW": "1"}}\n'),
        ("bin/n", "#!/usr/bin/env python3\nK = 'GOH_NEW'\n"),
    ],
)
def test_a_read_that_nothing_declares_is_refused_naming_the_file_to_write(root, rel, text) -> None:
    put(root, rel, text)
    said = "write docs/config/GOH_NEW.md (the form: docs/config.md)"
    assert faults(root) == [f"GOH_NEW is read by {rel} and declared nowhere: {said}"]


@pytest.mark.parametrize("key", ["GOH_S", "GOH_SH2", "GOH_SH_B"])
def test_a_prefix_or_an_extension_of_a_declared_key_is_another_key(root, key) -> None:
    put(root, "lib/n.py", f'K = "{key}"\n')
    assert len(faults(root)) == 1 and faults(root)[0].startswith(f"{key} is read by lib/n.py")


@pytest.mark.parametrize(
    ("rel", "text"),
    [
        ("lib/n.py", '"""GOH_NEW."""\n# GOH_NEW\nx = 1  # GOH_NEW\n'),
        ("gates/n.sh", "# GOH_NEW\ntrue  # GOH_NEW\n"),
        ("crates/goh/src/n.rs", "//! GOH_NEW\nfn f() {} // GOH_NEW\n/* GOH_NEW */\n"),
        ("crates/goh/src/n_tests.rs", 'const K: &str = "GOH_NEW";\n'),
        ("crates/goh/tests/n.rs", 'const K: &str = "GOH_NEW";\n'),
        ("tests/test_n.py", 'K = "GOH_NEW"\n'),
        ("tests/_kit.py", 'K = "GOH_NEW"\n'),
        ("docs/n.md", "`GOH_NEW`\n"),
        ("gates/n_free_keys.txt", "GOH_NEW\n"),
        ("gates/n.sh", "echo 'GOH_NEW_* and GOH_NEW_'\n"),
    ],
)
def test_prose_a_test_a_key_list_and_a_pattern_are_no_read(root, rel, text) -> None:
    put(root, rel, text)
    assert faults(root) == []
    # nor do they keep a declared key alive
    put(root, "docs/config/GOH_NEW.md", decl("GOH_NEW"))
    kept = rel == "tests/_kit.py"  # a support module runs for real: what it reads is read
    assert (faults(root) == []) is kept, faults(root)


def test_a_declared_key_no_code_reads_is_dead_unless_its_reader_is_elsewhere(root) -> None:
    put(root, "docs/config/GOH_GONE.md", decl("GOH_GONE"))
    said = "no code reads GOH_GONE; delete the declaration with its last reader"
    assert faults(root) == [f"docs/config/GOH_GONE.md: {said}"]
    put(root, "crates/goh/src/gatesrc.rs",
        'pub const PIPELINE_KEYS: [&str; 1] = [\n    "GOH_GONE",\n];\n'
        'pub const OPT_IN_KEYS: [&str; 1] = ["GOH_GONE"];\n')  # fmt: skip
    assert len(faults(root)) == 1, "a list of keys reads none of them"
    put(root, "docs/config/GOH_GONE.md", decl("GOH_GONE", kind="external ~/x.py"))
    assert faults(root) == []


def test_a_name_built_at_run_time_reads_its_declared_family(root) -> None:
    put(root, "gates/n.sh", 'V="GOH_FAM_$lang"\n')
    assert faults(root)[0].startswith("GOH_FAM_ is read by gates/n.sh")
    put(root, "docs/config/GOH_FAM_PY.md", decl("GOH_FAM_PY"))
    assert faults(root) == []


@pytest.mark.parametrize(
    ("rel", "text", "why"),
    [
        ("GOH_X.md", decl("GOH_SH"), "GOH_SH is declared in docs/config/GOH_SH.md too"),
        ("GOH_X.md", decl("GOH_SH"), "its name is GOH_SH.md"),
        ("GOH_SH.md", decl("GOH_SH", area="two"), "Area 'two' is no file"),
        ("GOH_SH.md", decl("GOH_SH", kind="maybe"), "Kind is setting"),
        ("GOH_SH.md", decl("GOH_SH", kind="external"), "Kind is setting"),
        ("GOH_SH.md", decl("GOH_SH", order="last"), "Order is no number"),
        ("GOH_SH.md", decl("GOH_SH").replace("Default: unset\n", ""), "no Default"),
        ("GOH_SH.md", decl("GOH_SH").replace("Kind", "Sort"), "no field of the form"),
        ("GOH_SH.md", decl("GOH_SH").replace("Area: one", "Area: one\nArea: one"), "said twice"),
        ("GOH_SH.md", decl("GOH_SH", text="A.\n\nB."), "one paragraph"),
        ("GOH_SH.md", decl("GOH_SH", text=""), "one paragraph"),
        ("GOH_SH.md", decl("GOH_SH")[2:], "no `# ` title"),
        ("notes.md", "Notes.\n", "its name is"),
        ("_areas/one.md", AREA.replace("Order: 10\n", ""), "no Order"),
        ("_areas/one.md", AREA.replace("Level: 2", "Level: 7"), "Level is 0 to 3"),
        ("_areas/one.md", AREA.replace("<!-- rows -->\n", ""), "no `<!-- rows -->`"),
    ],
)
def test_a_file_that_breaks_the_form_is_refused(root, rel, text, why) -> None:
    put(root, f"docs/config/{rel}", text)
    found = [f for f in faults(root) if why in f]
    assert len(found) == 1 and found[0].startswith("docs/config/"), faults(root)


def test_a_stale_placeholder_is_refused(root) -> None:
    put(root, "lib/n.py", 'P = "GOH_A"\n')
    assert config_ref.faults(root, {"GOH_A": "a pattern's own name"}) == []
    for k, why in (("GOH_B", "is in no code"), ("GOH_SH", "is declared")):
        found = config_ref.faults(root, {"GOH_A": "x", k: "x"})
        assert found == [f"lib/config_ref.py: PLACEHOLDERS names {k}, which {why}"]


def test_the_starter_template_sets_only_declared_settings(root) -> None:
    put(root, ".gatesrc.example", "# GOH_SH='on'  # see GOH_PY\n")
    assert faults(root) == []
    put(root, ".gatesrc.example", "# GOH_NOPE=1\n")
    assert faults(root) == [".gatesrc.example: GOH_NOPE is no declared setting"]
    put(root, ".gatesrc.example", "GOH_SH=1\n")
    put(root, "docs/config/GOH_SH.md", decl("GOH_SH", kind="internal"))
    assert faults(root) == [".gatesrc.example: GOH_SH is no declared setting"]


def test_a_key_prints_its_declaration_its_readers_and_its_tests(root, capsys) -> None:
    put(root, "lib/prose.py", '"""GOH_SH in prose only."""\n')
    assert config_ref.show(root, ["GOH_SH"]) == 0
    out = capsys.readouterr().out
    assert "Area: one" in out and "Read by: gates/a.sh\nTests: test_a.py\n" in out, out
    assert config_ref.show(root, ["GOH_RS"]) == 0  # a file's second key
    assert "## docs/config/GOH_PY.md" in capsys.readouterr().out
    assert config_ref.show(root, ["GOH_NOPE"]) == 1
    assert "no file of docs/config/ declares GOH_NOPE" in capsys.readouterr().err


def test_a_rev_before_the_split_answers_from_its_committed_table(repo, capsys) -> None:
    put(repo, "docs/config.md", "| Key | V |\n|---|---|\n| `GOH_A` | 1 |\n| `GOH_AB` | 2 |\n")
    commit_all(repo)
    out = repo.parent / "out"
    out.mkdir()
    assert config_ref.map_table.at_rev(repo, "HEAD", out) == ""
    assert config_ref.show(out, ["GOH_A"]) == 0
    assert capsys.readouterr().out == "| `GOH_A` | 1 |\n"
    assert config_ref.show(out, ["GOH_NOPE"]) == 1


def test_the_command_judges_the_checkout_its_export_runs_for(root, monkeypatch, capsys) -> None:
    export = root / "export"
    export.mkdir()
    (export / ".goh-head").write_text("")
    monkeypatch.setattr(config_ref, "ROOT", export)
    monkeypatch.setenv("GOH_LIVE_ROOT", str(root))
    assert config_ref.main(["GOH_SH"]) == 0
    assert "docs/config/GOH_SH.md" in capsys.readouterr().out
    put(root, "lib/n.py", 'K = "GOH_NEW"\n')
    assert config_ref.main(["--check"]) == 1
    assert "GOH_NEW is read by lib/n.py" in capsys.readouterr().err


def test_a_rev_is_rendered_from_its_own_files(repo, monkeypatch, capsys) -> None:
    for rel, text in FILES.items():
        put(repo, rel, text)
    commit_all(repo)
    put(repo, "docs/config/GOH_SH.md", decl("GOH_SH", text="Newer."))
    monkeypatch.setattr(config_ref, "ROOT", repo)
    assert config_ref.main(["--rev", "HEAD", "GOH_SH"]) == 0
    out = capsys.readouterr().out
    assert "Newer" not in out and "Read by: gates/a.sh" in out, out
    assert config_ref.main(["--rev", "no-such"]) == 2 and config_ref.main(["--rev"]) == 2


def test_two_branches_that_each_add_a_key_merge_clean_with_no_driver(repo) -> None:
    for rel, text in FILES.items():
        put(repo, rel, text)
    commit_all(repo)
    base = git(repo, "rev-parse", "HEAD").strip()
    for name in ("ONE", "TWO"):
        git(repo, "checkout", "-q", "-b", name, base)
        put(repo, f"docs/config/GOH_{name}.md", decl(f"GOH_{name}"))
        put(repo, f"lib/{name.lower()}.py", f'K = "GOH_{name}"\n')
        assert faults(repo) == []
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "--no-verify", "-m", name)
    git(repo, "merge", "-q", "--no-verify", "-m", "both", "ONE")  # raises on a conflict
    assert faults(repo) == []
    out = config_ref.Ref(repo).render()
    assert "| `GOH_ONE` | unset | It. |\n| `GOH_TWO` |" in out, out


def test_the_page_is_a_pointer_with_no_row_to_edit() -> None:
    page = (REPO_ROOT / "docs" / "config.md").read_text(encoding="utf-8")
    rows = [ln for ln in page.splitlines() if ln.lstrip().startswith("|")]
    assert not rows, (
        f"docs/config.md holds no table: write docs/config/<KEY>.md, not a row here: {rows}"
    )
    assert len(page.splitlines()) < 20 and "goh config" in page and "Default:" in page, page
    assert "config.md" not in (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")


def test_help_names_every_form() -> None:
    argv = [sys.executable, str(REPO_ROOT / "lib/config_ref.py"), "--help"]
    done = subprocess.run(
        argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False
    )
    assert done.returncode == 0 and "--rev" in done.stdout and "--check" in done.stdout, done
