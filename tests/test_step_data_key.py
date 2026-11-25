"""Every proven key mode covers the goh DATA a step reads, not only its code (GOH.STEPDATAKEY).

GOH.FULLKEYSCRIPTS keyed every CODE file a step runs (run_rule.CODE_PARTS) and dropped the rest:
gates/doctor.sh reads `"$GOH_ROOT"/hooks/*`, `$GOH_ROOT/retired_hooks.sha256` and
`$GOH_ROOT/docs/config/`, and gates/structural.sh's hook currency (lib/hook_currency.py) the
first two, so an edit to a stock hook reused a doctor or structural verdict a consumer had: the
probe (2026-11-19) turned doctor from healthy (exit 0) to "1 problem" (exit 1) by one appended line
in hooks/pre-commit, with every key unchanged. run_rule.DATA is the one declaration of what goh's
code reads of goh's own tree outside its code parts; the whole-content key and each step's closure
read it. And source_closure.names_path matched a substring: `.githooks/pre-push` (the gated
repo's hook, lib/gate_coverage.py) named goh's hooks/pre-push.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import gate_identity
import run_rule
import source_closure
import step_closure as sc

# What the real-tree tests read (lib/declared_reads.py): goh's code, the data it reads, a doc.
TREE_READS = ("gates/", "checks/", "lib/", "tools/", "tui/", "scripts/", "crates/", "hooks/",
              "retired_hooks.sha256", "docs/config/", "docs/BACKLOG.md")  # fmt: skip
DOCTOR = 'bash "$GOH_DIR/gates/doctor.sh" .'
DOCTOR_SH = (
    "#!/usr/bin/env bash\n"
    'for stock in "$GOH_ROOT"/hooks/*; do cmp -s x "$GOH_ROOT/hooks/${stock##*/}"; done\n'
    'grep -q x "$GOH_ROOT/retired_hooks.sha256"\n'
    'keys="$GOH_ROOT/docs/config"; grep -o GOH_ "$keys"/GOH_*.md\n'
)


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


def _fresh() -> None:
    for f in (sc._hashed, sc.runner_closure, sc._code, sc.candidates, sc._data,
              source_closure.tree_files):  # fmt: skip
        f.cache_clear()


def _goh(root: Path) -> Path:
    _write(root, {
        "gates/local_ci.sh": "#!/usr/bin/env bash\n",
        "gates/proven.sh": "#!/usr/bin/env bash\n",
        "gates/doctor.sh": DOCTOR_SH,
        "hooks/pre-commit": "#!/bin/sh\none\n",
        "hooks/claude/registrations.json": "{}\n",
        "retired_hooks.sha256": "abc  pre-commit\n",
        "docs/config/GOH_ONE.md": "# GOH_ONE\n",
        "docs/config/_areas/one.md": "# One\n",
        "docs/BACKLOG.md": "one\n",
    })  # fmt: skip
    return root


def _key(mode: str, goh: Path, repo: Path) -> str:
    """The goh half of the doctor step's proven key under GOH_PROVEN_CLOSURE=`mode` (as
    tests/test_full_key_scripts.py reads it): off the `goh` content lines, full the `closure full`
    line, on the step's own closure line."""
    _fresh()
    if mode == "off":
        return "\n".join(gate_identity.proven([goh], None, repo, closure=False))
    lines = sc.closures([goh], [DOCTOR], None, repo)
    return lines[0] if mode == "full" else lines[1]


MODES = ("off", "full", "on")
READ = ("hooks/pre-commit", "retired_hooks.sha256", "docs/config/GOH_ONE.md",
        "docs/config/GOH_TWO.md", "hooks/pre-rebase")  # fmt: skip


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("rel", READ)
def test_an_edit_to_data_doctor_reads_misses_its_step_key(tmp_path, mode, rel) -> None:
    """A stock hook edited (or added: `hooks/*` is every file directly in hooks/), the retired
    hooks' table or a key's declaration: doctor's verdict may change, so its key must."""
    goh, repo = _goh(tmp_path / "goh"), tmp_path / "repo"
    repo.mkdir()
    before = _key(mode, goh, repo)
    _write(goh, {rel: "edited\n"})
    after = _key(mode, goh, repo)
    assert "closure refused" not in before, before
    assert after != before, f"closure {mode}: an edit to {rel}, which doctor reads, reused its key"


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("rel", ["docs/BACKLOG.md", "hooks/claude/registrations.json",
                                 "docs/config/_areas/one.md"])  # fmt: skip
def test_a_data_file_no_step_reads_moves_no_key(tmp_path, mode, rel) -> None:
    """docs/BACKLOG.md and hooks/claude/ (below hooks/, not a stock hook) decide no step: keying
    them would only miss."""
    goh, repo = _goh(tmp_path / "goh"), tmp_path / "repo"
    repo.mkdir()
    before = _key(mode, goh, repo)
    _write(goh, {rel: "edited\n"})
    assert _key(mode, goh, repo) == before, f"closure {mode}: {rel}, read by no step, moved it"


def test_githooks_does_not_name_hooks() -> None:
    """lib/gate_coverage.py's ENTRIES holds `.githooks/pre-push`, the gated repo's own hook."""
    named = source_closure.names_path
    assert not named('ENTRIES = ("tools/gate.sh", ".githooks/pre-push")', "hooks/pre-push")
    assert not named('x = "tools/hooks/pre-commit"', "hooks/pre-commit")
    assert not named('x = "lib/x.py.bak"', "lib/x.py")
    for code in ('"$GOH_ROOT/hooks/pre-push"', '"$GOH_ROOT"/hooks/pre-push', '"hooks/pre-push"',
                 'f"{root}/hooks/pre-push"', '"$HERE/../hooks/pre-push"', "(hooks/pre-push)",
                 'GOH / "hooks" / "pre-push"', "./hooks/pre-push",
                 '"a\\thooks/pre-push\\n"'):  # fmt: skip
        assert named(code, "hooks/pre-push"), code


# THE REAL TREE. What doctor and structural's hook currency read of goh's own tree.
DOCTOR_READS = {"retired_hooks.sha256", "docs/config/GOH_BIN.md"}


def _stock() -> set[str]:
    return {f"hooks/{p.name}" for p in (REPO_ROOT / "hooks").iterdir() if p.is_file()}


def test_the_real_whole_content_key_covers_what_doctor_reads_and_no_more() -> None:
    _fresh()
    keyed = set(gate_identity._files(REPO_ROOT, gate_identity.PARTS, data=True))
    assert _stock() and _stock() | DOCTOR_READS <= keyed, sorted(_stock() | DOCTOR_READS - keyed)
    assert "docs/BACKLOG.md" not in keyed
    assert not [r for r in keyed if r.startswith("hooks/claude/")]


def test_the_real_structural_closure_keys_the_stock_hooks() -> None:
    """gates/structural.sh runs lib/hook_currency.py (gates/_hook_currency.sh): its closure reads
    the stock hooks and the retired table, and the step's keyed files hold them."""
    _fresh()
    files, _ = sc.step_closure("./gates/structural.sh --full", REPO_ROOT, REPO_ROOT)
    assert _stock() | {"retired_hooks.sha256"} <= set(files)


# A goh-rooted path of goh's code outside its keyed parts that is NO read: `<file>: <path>` ->
# why. A ratchet both ways with run_rule.DATA: a new read fails until declared, a gone one fails.
MENTIONS = {
    "gates/doctor.sh: install.sh": "a fix message names it; doctor never runs it",
    "gates/push_gate.sh: install.sh": "a die message names it",
    "gates/structural.sh: install.sh": "a message names it",
    "gates/structural.sh: .goh-head": "an export's own commit (lib/canary_sides.py writes it): "
    "the version a binary is checked against, not content of the gates",
    "scripts/hooks_refresh.py: install.sh": "`goh hooks --refresh`, an owner command: no step "
    "runs it",
}
_SH = re.compile(
    r'\$\{?(?:GOH_ROOT|GOH_DIR|GOH|PROVEN_GOH_ROOT)\}?"?/([A-Za-z0-9_.*-]+(?:/[A-Za-z0-9_.*-]+)*)'
)
_PY = re.compile(r'\b(?:goh|GOH_ROOT|GOH_DIR|GOH)\s*/\s*"([^"]+)"((?:\s*/\s*"[^"]+")*)')


def _rooted_reads() -> set[str]:
    out = set()
    for rel in source_closure.tree_files(REPO_ROOT):
        if rel.split("/")[0] not in run_rule.CODE_PARTS or rel.startswith("tests/"):
            continue
        is_py = rel.endswith(".py")
        if not (is_py or source_closure._is_shell(REPO_ROOT / rel)):
            continue
        code = source_closure.code_only(rel, (REPO_ROOT / rel).read_text(errors="replace"))
        for m in (_PY if is_py else _SH).finditer(code):
            path = m.group(1)
            if is_py:
                path = "/".join([path, *re.findall(r'"([^"]+)"', m.group(2))])
            if path.split("/")[0] not in gate_identity.PARTS:
                out.add(f"{rel}: {path.rstrip('/')}")
    return out


def _covers(path: str, pat: str) -> bool:
    """A rooted path read covers a DATA pattern: the same, its directory (`"$GOH_ROOT/hooks/$h"`
    reads `hooks`, a `goh / "hooks" / HOOK` too)."""
    return path in (pat, pat.removesuffix("/*")) or pat.startswith(path + "/")


def _declared(entry: str) -> bool:
    reader, path = entry.split(": ", 1)
    return any(_covers(path, p) for p in run_rule.DATA.get(reader, ()))


def test_every_goh_rooted_read_outside_the_code_is_declared() -> None:
    """The real tree, every code file of goh: a path it reads under the goh checkout outside the
    code parts is declared in run_rule.DATA under that reader, or is a MENTIONS name."""
    _fresh()
    found = _rooted_reads()
    undeclared = sorted(e for e in found if not _declared(e) and e not in MENTIONS)
    assert not undeclared, f"goh data a step may read that no key covers: {undeclared}"
    assert not sorted(set(MENTIONS) - found), "MENTIONS the tree no longer holds"
    stale = [
        f"{r}: {p}"
        for r, ps in run_rule.DATA.items()
        for p in ps
        if not any(e.startswith(f"{r}: ") and _covers(e.split(": ", 1)[1], p) for e in found)
    ]
    assert not stale, f"run_rule.DATA its reader no longer reads: {stale}"
