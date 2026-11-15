"""gates/required_tools.tsv is the ONE place a house tool's version is written (GOH.CLASS.PARITY).

The class, owner ruling 2026-11-15 ("Yes, close classes"): local, commit, push and CI disagree. Its
version half: the ruff pin was written in gates/ruff_house.py, the hosted workflow and four install
strings; the shellcheck floor in the manifest and a `const` in crates/goh/src/shell_lint.rs; the
swiftlint and shellcheck floors again in a dozen test fakes. Each copy is a place a raise does not
reach, and the day it does not, two places judge with two releases -- divoom_control was red on
GitHub with SIM102 while its local gate, on another ruff, was green (GOH.RUFFVERSION).

Every reader now READS the manifest: gates/ruff_house.py, gates/required_tools.py (`--pin`,
`--floors`), .github/workflows/gate.yml, the goh-floors action, lib/house_python.sh, the Rust
callers (crates/goh/src/tool_versions.rs, `include_str!`) and the tests (tests/_tool_versions.py).
This test is red on a version literal for any tool the manifest versions, written in CODE elsewhere:

  * the manifest's own current version of each tool, as a literal (`echo 0.65.1` in a fake);
  * any version written beside a versioned tool's name (`ruff==0.17.0`, `swiftlint 0.66.0`), which
    catches a stale copy the first rule cannot (its number is no longer the manifest's).

Prose is not a copy: comments and docstrings record what was measured on which release, and stay.
Exempt by path, each for its reason: the manifest itself, Markdown (the changelog and docs are
history), Cargo.lock (crate versions, not tools), and the goh-floors action's checksum table, which
is keyed by the releases it verified (floors.sh picks the newest at or above the manifest's floor).
"""

from __future__ import annotations

import ast
import io
import re
import subprocess
import tokenize
from pathlib import Path

from conftest import REPO_ROOT

MANIFEST = "gates/required_tools.tsv"
EXEMPT = {
    MANIFEST: "the manifest",
    "Cargo.lock": "crate versions, not tool versions",
    ".github/actions/goh-floors/shellcheck.sha256": "checksums keyed by each verified release",
}
_HASH = (".sh", ".bash", ".yml", ".yaml", ".toml", ".tsv", ".cfg", ".txt", ".ini", "")


def versions(manifest: Path) -> dict[str, str]:
    """tool -> its version (a pin's `=` dropped), for every manifest row that carries one."""
    out: dict[str, str] = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        cols = line.split("\t")
        if line.startswith("#") or len(cols) != 6 or cols[5] == "-":
            continue
        out.setdefault(cols[1], cols[5].lstrip("="))
    return out


def _python_code(text: str) -> str:
    """`text` with its comments and docstrings blanked, line numbers kept."""
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            for n in range(node.lineno - 1, (node.end_lineno or node.lineno)):
                lines[n] = ""
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                row, col = tok.start
                lines[row - 1] = lines[row - 1][:col]
    except (tokenize.TokenError, IndentationError):
        pass
    return "\n".join(lines)


def code_of(rel: str, text: str) -> str:
    """The CODE in a tracked file: comments (and Python docstrings) are prose, not a copy."""
    suffix = Path(rel).suffix
    if suffix == ".py":
        return _python_code(text)
    if suffix == ".rs":
        return re.sub(r"(^|\s)//.*$", r"\1", text, flags=re.MULTILINE)
    if suffix in _HASH:
        return re.sub(r"(^|\s)#.*$", r"\1", text, flags=re.MULTILINE)
    return text


def copies(root: Path, files: list[str]) -> list[str]:
    """Every version literal for a manifest-versioned tool written outside the manifest."""
    table = versions(root / MANIFEST)
    current = {
        tool: re.compile(rf"(?<![\w.]){re.escape(v)}(?![\w.])")
        for tool, v in table.items()
        if v.count(".") >= 2  # `3.15` is too common a token (test interpreters) to read as a copy
    }
    names = "|".join(re.escape(t) for t in sorted(table, key=len, reverse=True))
    beside = re.compile(rf"(?<![\w-])({names})(?:==|@|\s+v?|:\s*\"?)(\d+\.\d+(?:\.\d+)?)\b")
    found: list[str] = []
    for rel in files:
        if rel in EXEMPT or rel.endswith(".md"):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        code = code_of(rel, text)
        # A bare number is a copy of a tool's version only in a file that names that tool: 0.17.0
        # is zig's floor and, in a ruff test, an off-pin ruff.
        named = [t for t in current if re.search(rf"(?i)(?<![a-z-]){re.escape(t)}(?![a-z-])", text)]
        # A test plants versions OFF the manifest on purpose (a fake below the floor, an off-pin
        # ruff), so the beside-the-name rule reads only the code that judges.
        planted = rel.startswith("tests/")
        for n, line in enumerate(code.splitlines(), 1):
            for tool in named:
                if current[tool].search(line):
                    found.append(f"{rel}:{n}: {tool} {table[tool]} written outside {MANIFEST}")
            for m in [] if planted else beside.finditer(line):
                found.append(f"{rel}:{n}: {m.group(1)} {m.group(2)} written outside {MANIFEST}")
    return sorted(set(found))


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
        capture_output=True, check=True, stdin=subprocess.DEVNULL,
    ).stdout.decode()  # fmt: skip
    return [f for f in out.split("\0") if f]


def test_no_tool_version_is_written_outside_the_manifest():
    assert copies(REPO_ROOT, _tracked()) == []


def _plant(tmp_path: Path, rel: str, body: str) -> Path:
    (tmp_path / "gates").mkdir(exist_ok=True)
    (tmp_path / MANIFEST).write_text((REPO_ROOT / MANIFEST).read_text())
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return tmp_path


def test_a_planted_copy_of_the_current_version_is_red(tmp_path):
    swiftlint = versions(REPO_ROOT / MANIFEST)["swiftlint"]
    fake = f"SWIFTLINT = '[ \"$1\" = version ] && echo {swiftlint}'\n"
    root = _plant(tmp_path, "tests/test_x.py", fake)
    assert copies(root, ["tests/test_x.py"]) == [
        f"tests/test_x.py:1: swiftlint {swiftlint} written outside {MANIFEST}"
    ]


def test_a_planted_stale_pin_beside_its_tool_name_is_red(tmp_path):
    # Spelled in pieces: this file is scanned too, and a whole literal here would be a copy.
    root = _plant(tmp_path, "ci.yml", "      run: pip install " + "ruff" + "==0.1.0\n")
    assert copies(root, ["ci.yml"]) == [f"ci.yml:1: ruff 0.1.0 written outside {MANIFEST}"]


def test_a_rust_const_copy_is_red(tmp_path):
    shellcheck = versions(REPO_ROOT / MANIFEST)["shellcheck"]
    root = _plant(tmp_path, "src/x.rs", f'const SHELLCHECK_FLOOR: &str = "{shellcheck}";\n')
    assert len(copies(root, ["src/x.rs"])) == 1


def test_prose_naming_a_release_is_not_a_copy(tmp_path):
    """A comment or docstring records what was measured on which release: history, not a gate."""
    shellcheck = versions(REPO_ROOT / MANIFEST)["shellcheck"]
    body = (
        f'"""Probed against shellcheck {shellcheck}."""\n'
        f"X = 1  # shellcheck {shellcheck} exits 0 here\n"
    )
    root = _plant(tmp_path, "gates/x.py", body)
    _plant(tmp_path, "gates/x.sh", "# " + "swiftlint" + " 0.1.0 refuses this\necho hi\n")
    _plant(tmp_path, "notes.md", "ruff" + "==0.1.0\n")
    assert copies(root, ["gates/x.py", "gates/x.sh", "notes.md"]) == []
