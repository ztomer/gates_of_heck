"""Only lib/reuse.py reads or writes a verdict store (GOH.ONEVERDICT; docs/reuse.md).

goh grew a store per reuse path, each keyed its own way, and the ship missed the land queue's
verdict because the two never met (koffee_big, 3137.5 s release step). A store is now named in
code by the one API alone: every other code mention of a store's path or its setting is a finding,
except the pairs in TODAY below -- the stores still to move, each entry deleted as its path moves
onto lib/reuse.py (docs/reuse.md, the order). A stale entry fails too, so "merged" is an empty
list, not a judgement. Code is scanned, not prose: Python string constants that are no docstring,
shell and Rust lines outside comments.
"""

from __future__ import annotations

import ast
import re
import subprocess

from conftest import REPO_ROOT

API = ("lib/reuse.py", "lib/reuse_store.py")  # the one API, and where its records live
# A store's path or setting, as code names it: the whole word (`goh-proven-stamp` is a temp name).
STORES = ("goh/reuse", "GOH_REUSE_DIR", "goh-landed", "goh-pushed", "goh-proven", "goh/verdicts",
          "GOH_VERDICT_DIR")  # fmt: skip
_WORD = {s: re.compile(re.escape(s) + r"(?![-\w])") for s in STORES}
SCOPE = ("bin", "checks", "crates", "gates", "hooks", "lib", "scripts", "tools", "tui")
# (file, store) still naming a store outside the API: each a path not yet moved (docs/reuse.md).
TODAY = {
    ("tools/land.sh", "goh-landed"),  # the land record's writer (another lane holds land.sh)
    ("gates/_push_refusals.sh", "goh-landed"),  # GOH_LANDED_ONLY's refusal reads it by sha
    ("gates/_proven.sh", "goh-proven"),  # the proven-step cache, shell face
    ("crates/goh/src/proven.rs", "goh-proven"),  # ... and its Rust face
    ("crates/goh/src/verdict_cache.rs", "goh/verdicts"),  # the per-file checker cache
    ("crates/goh/src/verdict_cache.rs", "GOH_VERDICT_DIR"),
    ("scripts/release_canary.py", "GOH_VERDICT_DIR"),  # the canary's child keeps the suite's cache
}


def _named(rel: str, text: str) -> set[str]:
    if rel.endswith(".py"):
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return set()
        docs = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr)}
        consts = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
                  and isinstance(n.value, str) and id(n) not in docs]  # fmt: skip
        return {s for s in STORES if any(_WORD[s].search(c) for c in consts)}
    mark = "//" if rel.endswith(".rs") else "#"
    code = [ln.split(f" {mark} ")[0] for ln in text.splitlines()
            if not ln.lstrip().startswith(mark)]  # fmt: skip
    return {s for s in STORES if any(_WORD[s].search(ln) for ln in code)}


def found() -> set[tuple[str, str]]:
    listed = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files", "-co", "--exclude-standard",
                             "--", *SCOPE], capture_output=True, text=True, check=True,
                            stdin=subprocess.DEVNULL).stdout.split()  # fmt: skip
    out = set()
    for rel in listed:
        if rel in API or not rel.endswith((".py", ".sh", ".rs")) or not (REPO_ROOT / rel).is_file():
            continue
        text = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        out |= {(rel, s) for s in _named(rel, text)}
    return out


def test_only_the_reuse_api_names_a_verdict_store():
    hits = found()
    assert not hits - TODAY, f"a store named outside {API}: {sorted(hits - TODAY)}"
    assert not TODAY - hits, f"stale TODAY entries (moved: delete them): {sorted(TODAY - hits)}"


def test_the_scan_sees_a_store_named_in_code_and_not_in_prose():
    assert _named("x.py", 'P = "goh-landed"\n') == {"goh-landed"}
    assert _named("x.py", '"""goh-landed is prose."""\n') == set()
    assert _named("x.sh", 'f="$c/goh-pushed"  # goh-proven\n') == {"goh-pushed"}
    assert _named("x.sh", "# goh-pushed, prose\n") == set()
    assert _named("x.rs", '    root("GOH_VERDICT_DIR")\n') == {"GOH_VERDICT_DIR"}
    assert _named("x.sh", 'mktemp "$T/goh-proven-stamp.X"\n') == set()
