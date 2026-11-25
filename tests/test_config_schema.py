"""Config-schema drift gate: every GOH_* key the gates read must be documented.

Red-proof: add `GOH_SOMETHING_NEW` to any gate without touching
docs/config/ and this test goes red naming it -- at COMMIT, not only at push: the line below
declares that this file reads the staged sources, so gates/staged_guards.sh runs it over the index
whenever a commit stages a file it scans (GOH.CONFIGKEYCOMMIT; tests/test_staged_guards.py keeps
the list equal to lib/config_ref.py SCAN).
"""

# goh-reads-staged: gates checks lib tools hooks tui install.sh crates scripts bin .github
# goh-reads-staged: docs/config .gatesrc.example

import re
from pathlib import Path

from conftest import REPO_ROOT

from lib import config_ref

KEY = re.compile(r"GOH_[A-Z][A-Z_]*[A-Z_]")
SCAN = ("gates", "checks", "lib", "tools", "hooks", "tui", "install.sh", "crates", "scripts")


def _rust_test(f: Path) -> bool:
    """A Rust test's GOH_* strings are fixtures it plants, not keys a gate reads."""
    return f.suffix == ".rs" and (
        "tests" in f.parts or f.stem == "tests" or f.stem.endswith("_tests")
    )


def _keys_in_source() -> set[str]:
    found: set[str] = set()
    for name in SCAN:
        p = REPO_ROOT / name
        if p.is_file():
            files = [p]
        else:
            files = sorted(f for f in p.rglob("*") if f.is_file())
        for f in files:
            if "__pycache__" in f.parts or f.suffix == ".sha256" or _rust_test(f):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="strict")
            except (UnicodeDecodeError, OSError):
                continue
            for m in KEY.finditer(text):
                if f.suffix in (".pyc",):
                    continue
                found.add(m.group(0))
    return found


def test_config_schema_covers_keys():
    """Every GOH_* key read by gates/checks/lib/tools/hooks/scripts -- and the Rust crates, where a
    key read only by the binary used to ship undocumented (BACKLOG 1.3) -- must appear in the
    reference `goh config` renders from docs/config/."""
    doc = config_ref.Ref(REPO_ROOT).render()
    missing = sorted(k for k in _keys_in_source() if k not in doc)
    assert missing == [], "undeclared GOH_* keys (write docs/config/<KEY>.md):\n" + "\n".join(
        f"  {k}" for k in missing
    )


def test_every_declaration_keeps_the_form():
    assert config_ref.Ref(REPO_ROOT).broken == []


def test_gatesrc_example_keys_are_documented():
    """Starter template must not invent keys the schema does not know."""
    doc = config_ref.Ref(REPO_ROOT).render()
    example = (REPO_ROOT / ".gatesrc.example").read_text(encoding="utf-8")
    unknown = sorted(
        {m.group(0) for m in KEY.finditer(example)} - {m.group(0) for m in KEY.finditer(doc)}
    )
    assert unknown == [], (
        ".gatesrc.example mentions keys docs/config/ does not declare:\n"
        + "\n".join(f"  {k}" for k in unknown)
    )
