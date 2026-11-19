"""One list of the GOH_* keys that never change a verdict, and it cannot drift (2026-10-06).

Two cache keys hash "the GOH_* configuration" (gates/_rust_proven.sh's step, checks/_estate_cache.py's
identity), each with its own exclusions, and the internal keys added since -- GOH_RESOLVED_*, and
GOH_PROVEN_IDENTITY_FOR, which holds a $PWD -- went into both: a key varied with where a gate was
started, so a pre-commit proof and a pre-push lookup could not meet. gates/verdict_free_keys.txt is
the one list; every key docs/config.md calls "Not a setting" must be on it, and every key on it must
be documented.
"""

from __future__ import annotations

import re
import subprocess

from conftest import REPO_ROOT, hermetic_env

LIST = REPO_ROOT / "gates" / "verdict_free_keys.txt"


def _listed() -> set[str]:
    return {
        ln.strip() for ln in LIST.read_text().splitlines() if ln.strip() and not ln.startswith("#")
    }


def test_every_internal_key_is_on_the_list() -> None:
    doc = (REPO_ROOT / "docs" / "config.md").read_text()
    internal = set(re.findall(r"^\| `(GOH_[A-Z_]+)` \|[^\n]*Not a setting", doc, re.MULTILINE))
    assert internal and internal <= _listed(), sorted(internal - _listed())


# A key the gates EXPORT is bookkeeping for their children, not a setting -- and one that holds a
# pid re-gates every cached step on every run: GOH_BENCH_JOINED did, documented as a setting row
# so the doc-driven check above never saw it (2026-10-08). These are passed on and DO belong in
# a key, each with its reason.
EXPORTED_AND_KEYED = {
    "GOH_DIR": "the gates checkout that judges: a different one can judge differently",
    "GOH_RELEASE_BUFFERED": "release.sh's own self-copy; no gate runs under it",
    "GOH_PY": "the house interpreter every Python helper runs on: another can judge differently",
}

# The same class, passed another way: `GOH_X=v cmd` (inline) and `env GOH_X=v cmd` reach a child's
# environment exactly as an export does, and the first version of this test saw only `export GOH_X`,
# so GOH_STAGED_TWIN_MARK (a new temp file per commit) and GOH_CHANGE_BASE (a new commit per push)
# sat in the key of every proven step and estate record unseen (GOH.VERDICTFREEINLINE; it hid
# GOH.RUSTCOVREUSE the same way). A key passed this way is on the list, or here with its reason.
# A stale entry fails: the passing it excused is gone.
PASSED_AND_KEYED = {
    "GOH_AWK_FLOOR": "release.sh's awk, one release's constant; no cached step under it",
    "GOH_AWK_VER": "release.sh's awk, one release's constant; no cached step under it",
    "GOH_AWK_VER_RE": "release.sh's awk, one release's constant; no cached step under it",
    "GOH_RELEASE_VERSION": "the release kit's archive and verify commands, uncached",
    "GOH_COVERAGE_BIN_DIR": "the coverage measurement's external command: it IS the step",
    "GOH_CROSS_REPO_ROOT": "the gated repo's checkout: one value per repo (the venv refusal)",
    "GOH_LAND_CALLER": "set on the one `git merge` land.sh makes; no gate runs under it",
    "GOH_LAND_PRE": "the release land's empty pre-step, read by land.sh before any gate runs",
    "GOH_LAND_GATE": "the land's gate command, one value per land mode (lib/gate_identity.py)",
    "GOH_LCI_TIMEOUT": "a setting (docs/config.md): a different ceiling can judge differently",
    "GOH_PROVEN": "a setting: gate_profile.sh turns the proven cache off for its measurement",
    "GOH_STAGED_GUARD_RUN": "the constant 1, on the one pytest run of a commit's decision guards",
    "GOH_BENCH_WAIT": "a wait bound for the bench hold's children, no gate verdict",
    "GOH_COMMIT_LINT": "the constant 1: `goh lint` runs the commit gate as a lint, a different run",
    "GOH_LIVE": "which tree judges (working tree or HEAD export): another can judge differently",
    "GOH_TUI_BOOTSTRAP": "the constant 1, for the sourced TUI before the house Python exists",
}

# Every way a shell or Python source hands a GOH_* key to a child. Quoted strings, substitutions and
# `$var`s are blanked first, so a value cannot hide a key and a message that merely names one
# (`warn "... GOH_X=1 ..."`) is not read as passing it.
_START = r"(?:^[ \t]*|[;&|({]\s*|\b(?:then|do|else)\s+)"
_EXPORT = re.compile(
    _START + r"(?:export|declare\s+-x|typeset\s+-x)\s+((?:[-\w=.]+\s*)+)", re.MULTILINE
)
_ASSIGN = r"[A-Za-z_]\w*=\S*"
_INLINE = re.compile(
    _START
    + rf"((?:{_ASSIGN}[ \t]+)*GOH_[A-Z0-9_]+=\S*(?:[ \t]+{_ASSIGN})*)[ \t]+(?=[A-Za-z_./\"'$])",
    re.MULTILINE,
)
_ENV = re.compile(r"\benv[ \t]+((?:-\S+[ \t]+)*(?:[A-Za-z_]\w*=\S*[ \t]+)+)")
_PY_FORMS = (
    re.compile(r"\b(?:os\.environ|env)\[[\"'](GOH_[A-Z0-9_]+)[\"']\]\s*=(?!=)"),
    re.compile(r"\bdict\((?:os\.)?environ,((?:[^()]|\([^()]*\))*)\)"),
    re.compile(r"\*\*os\.environ,\s*[\"'](GOH_[A-Z0-9_]+)[\"']"),
    re.compile(r"\benviron\.update\(([^)]*)\)"),
)
_KEY = re.compile(r"\b(GOH_[A-Z0-9_]+)(?==|$)")  # a kwarg or the lone subscript, never a value read


def _bare(text: str) -> str:
    t = re.sub(r"\\\n", " ", text)
    t = re.sub(r"'[^'\n]*'", "V", t)
    t = re.sub(r'"(?:[^"\\\n]|\\.)*"', "V", t)
    for _ in range(3):
        t = re.sub(r"\$\((?:[^()\n]|\([^()\n]*\))*\)", "V", t)
    t = re.sub(r"\$\{[^}\n]*\}", "V", t)
    t = re.sub(r"\$[A-Za-z_]\w*", "V", t)
    return "\n".join(ln for ln in t.split("\n") if not ln.lstrip().startswith("#"))


def passed_keys(text: str, python: bool = False) -> dict[str, set[str]]:
    """GOH_* key -> the forms ("export", "inline", "env", "python") one source passes it by."""
    found: dict[str, set[str]] = {}
    if python:
        for rx in _PY_FORMS:
            for m in rx.finditer(text):
                for k in _KEY.findall(m.group(1)):
                    found.setdefault(k, set()).add("python")
        return found
    bare = _bare(text)
    for form, rx, keyrx in (
        ("export", _EXPORT, r"\bGOH_[A-Z0-9_]+"),
        ("inline", _INLINE, r"\bGOH_[A-Z0-9_]+(?==)"),
        ("env", _ENV, r"\bGOH_[A-Z0-9_]+(?==)"),
    ):
        for m in rx.finditer(bare):
            for k in re.findall(keyrx, m.group(1)):
                found.setdefault(k, set()).add(form)
    return found


def _passed_in_tree() -> dict[str, set[str]]:
    listing = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "gates", "lib", "tools", "hooks"],
        capture_output=True, text=True, env=hermetic_env(drop_git=True),
        stdin=subprocess.DEVNULL, check=False,
    )  # fmt: skip
    files = listing.stdout.split()
    found: dict[str, set[str]] = {}
    for rel in files:
        if rel.endswith((".md", ".rs", ".json", ".txt", ".toml", ".tsv", ".ts", ".mjs", ".plist")):
            continue
        try:
            text = (REPO_ROOT / rel).read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for k, forms in passed_keys(text, python=rel.endswith(".py")).items():
            found.setdefault(k, set()).update(forms)
    return found


def _strays(found: dict[str, set[str]]) -> list[str]:
    return sorted(set(found) - _listed() - set(EXPORTED_AND_KEYED) - set(PASSED_AND_KEYED))


def test_every_key_the_gates_export_is_verdict_free() -> None:
    out = subprocess.run(["git", "-C", str(REPO_ROOT), "grep", "-ohE", r"export +GOH_[A-Z0-9_]+",
                          "--", "gates", "lib", "tools", "hooks"],
                         capture_output=True, text=True, env=hermetic_env(drop_git=True), stdin=subprocess.DEVNULL, check=False).stdout  # fmt: skip
    exported = {w.split()[-1] for w in out.split("\n") if w}
    assert exported, "found no exported GOH_ key: the pattern moved"
    stray = sorted(exported - _listed() - set(EXPORTED_AND_KEYED))
    assert stray == [], f"exported, so internal -- add to gates/verdict_free_keys.txt: {stray}"


def test_every_key_the_gates_pass_on_is_verdict_free() -> None:
    """Export, inline `GOH_X=v cmd`, `env GOH_X=v cmd` and Python's environ forms: all one class."""
    found = _passed_in_tree()
    assert {"export", "inline", "env"} <= {f for forms in found.values() for f in forms}, (
        "a form went blind"
    )
    stray = _strays(found)
    assert stray == [], (
        f"passed to a child, so internal -- add to gates/verdict_free_keys.txt: {stray}"
    )


def test_the_passed_and_keyed_exemptions_are_all_still_passed() -> None:
    found = _passed_in_tree()
    stale = sorted(set(PASSED_AND_KEYED) - set(found))
    assert stale == [], f"no source passes these any more; delete their exemption: {stale}"
    assert not set(PASSED_AND_KEYED) & _listed(), "on the list already: the exemption is dead"


def test_a_fixture_with_an_inline_per_push_key_goes_red() -> None:
    """The instrument can fail: an inline key nobody listed is a stray, each way of passing it."""
    for line, form in (
        ('GOH_PER_PUSH_SHA="$sha" bash "$gate" --full', "inline"),
        ("    GOH_A_MARK=1 GOH_PER_PUSH_SHA=$(git rev-parse HEAD) cmd", "inline"),
        ('env -i PATH=/bin GOH_PER_PUSH_SHA="$sha" bash x', "env"),
        ("export GOH_OTHER=1 GOH_PER_PUSH_SHA=1", "export"),
        ("(cd d && GOH_PER_PUSH_SHA=1 ./run)", "inline"),
    ):
        found = passed_keys(line + "\n")
        assert form in found.get("GOH_PER_PUSH_SHA", set()), (line, found)
        assert "GOH_PER_PUSH_SHA" in _strays(found), line
    py = 'env = dict(os.environ, GOH_PER_PUSH_SHA=sha)\nos.environ["GOH_PER_PUSH_SHA"] = sha\n'
    assert "GOH_PER_PUSH_SHA" in _strays(passed_keys(py, python=True))
    # What is NOT a pass: a plain assignment, a message naming a key, a comment, a listed key.
    quiet = 'GOH_PER_PUSH_SHA=1\nwarn "run GOH_PER_PUSH_SHA=1 cmd"\n# GOH_PER_PUSH_SHA=1 cmd\n'
    assert passed_keys(quiet) == {}
    assert _strays(passed_keys('GOH_STRUCTURAL_MARK="$m" bash g\n')) == []


def test_every_listed_key_is_documented() -> None:
    doc = (REPO_ROOT / "docs" / "config.md").read_text()
    missing = sorted(k for k in _listed() if f"| `{k}` |" not in doc)
    assert not missing, missing


def test_the_config_hash_ignores_verdict_free_keys(tmp_path) -> None:
    """Same configuration, different bookkeeping: the same hash. A real setting still moves it."""

    def config_hash(**env: str) -> str:
        r = subprocess.run(
            ["bash", "-c", f'. "{REPO_ROOT}/gates/_hash.sh"; goh_config_hash'],
            cwd=tmp_path, capture_output=True, text=True,
            env=hermetic_env(**{"GOH_MAX_LINES": "500", **env}), stdin=subprocess.DEVNULL, check=False,
        )  # fmt: skip
        assert r.stdout.strip(), r.stderr
        return r.stdout

    base = config_hash()
    assert config_hash(GOH_PROVEN_IDENTITY_FOR="/x|y", GOH_RESOLVED_BIN="/b") == base
    assert config_hash(GOH_MAX_LINES="400") != base, "a real setting must still change the key"
