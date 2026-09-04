"""Config-schema drift gate: every GOH_* key the gates read must be documented.

Red-proof: add `GOH_SOMETHING_NEW` to any gate without touching
docs/config.md and this test goes red naming it.
"""

import re
from pathlib import Path

from conftest import REPO_ROOT

KEY = re.compile(r"GOH_[A-Z][A-Z_]*[A-Z_]")
SCAN = ("gates", "checks", "lib", "tools", "hooks", "tui", "install.sh")


def _keys_in_source() -> set[str]:
    found: set[str] = set()
    for name in SCAN:
        p = REPO_ROOT / name
        if p.is_file():
            files = [p]
        else:
            files = sorted(f for f in p.rglob("*") if f.is_file())
        for f in files:
            if "__pycache__" in f.parts or f.suffix == ".sha256":
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="strict")
            except (UnicodeDecodeError, OSError):
                continue
            if f.suffix not in (".sh", ".py", "") and f.name != "install.sh":
                if ".sh" not in f.name and ".py" not in f.name and "/" not in str(f):
                    pass
            for m in KEY.finditer(text):
                if f.suffix in (".pyc",):
                    continue
                found.add(m.group(0))
    return found


def test_config_schema_covers_keys():
    """Every GOH_* key read by gates/checks/lib/tools/hooks must appear in
    docs/config.md (user keys with defaults, internal keys in the
    internal-only row)."""
    doc = (REPO_ROOT / "docs" / "config.md").read_text(encoding="utf-8")
    missing = sorted(k for k in _keys_in_source() if k not in doc)
    assert missing == [], (
        "undocumented GOH_* keys (add them to docs/config.md):\n"
        + "\n".join(f"  {k}" for k in missing)
    )


def test_gatesrc_example_keys_are_documented():
    """Starter template must not invent keys the schema does not know."""
    doc = (REPO_ROOT / "docs" / "config.md").read_text(encoding="utf-8")
    example = (REPO_ROOT / ".gatesrc.example").read_text(encoding="utf-8")
    unknown = sorted(
        {m.group(0) for m in KEY.finditer(example)} - {m.group(0) for m in KEY.finditer(doc)}
    )
    assert unknown == [], (
        ".gatesrc.example mentions keys missing from docs/config.md:\n"
        + "\n".join(f"  {k}" for k in unknown)
    )
