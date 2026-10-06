"""A marked claim -> the number the tree actually says, and the COMMAND that says it.

    ```
    claim: 321 lines in tools/necrohand_mcp_server.py     -> 321
    `70` gates in `tests/e2e/verify.py`                   -> 70, parsed by AST
    ```

(Fenced, because these are the two spellings quoted from another estate and a
fenced block is where this gate keeps its own syntax.)

Four derivations, and each one is a FUNCTION OF THE TREE with no second
implementation to drift from it:

* `lines`      `checks/_gitutil.line_count` on the file's bytes -- the SAME
               definition the file-length cap and its ratchet enforce, so a claim
               and a ceiling can never disagree about one file. The printed
               command is `awk 'END{print NR}'`, not `wc -l`, and that is measured
               rather than stylistic: `line_count` counts a trailing partial line
               the way a reader counts lines, so `wc -l` reads a file with no
               final newline as one line SHORT. Printing `wc -l` next to a finding
               would hand the reader a command that disagrees with the gate.
* `tests`      test functions (sync or async, at any nesting depth) counted by
               `ast`, over the named file or every `.py` file under the named
               directory. A PARAMETRISED case counts as one: this is the
               `EXPECTED_TESTS`-style quantity, not `pytest --collect-only`'s,
               and the printed command is the `ast` one so the reader is not sent
               to a number that is a different question.
* `declared`   elements of ONE named module-level assignment in a Python file,
               selected BY NAME, never by a line regex. This is the exact case the
               reviews found: `roadmap_state.py` counted gate labels with
               `re.findall(r'"(P\\d+ [a-z_0-9]+)"', text)`, which cannot match a
               label carrying a second space, and read 64 against 70 declared
               steps. A regex that cannot match a label is not a smaller count,
               it is a wrong one. With no `:NAME`, a file holding exactly ONE such
               assignment is used and a file holding several is REFUSED -- the
               gate says which candidates it found rather than picking one.
* `files`      the length of `git ls-files`'s own answer for `<dir>/*.EXT`. The
               count IS git's answer; there is no second glob implementation in
               Python to disagree with it, and the printed command is that `git`
               call verbatim.

Every refusal is a `Unresolved` carrying the reason, never a silent pass: a claim
this gate cannot re-derive is a number nobody is checking, which is the state this
whole gate exists to end.
"""

from __future__ import annotations

import ast
import os
import shlex
import subprocess
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import content_bytes, foreign_repo_env, line_count

# The printed commands, as shell text. `derive_*` builds its answer with the same
# expression each of these runs, and `check_claim_derivation --probe` EXECUTES
# them against the fixtures and compares, so "here is the command" cannot decay
# into a command that would print something else.
TEST_COUNT_CMD = (
    "python3 -c 'import ast,sys;print(sum(sum(1 for x in ast.walk(ast.parse(open(p).read()))"
    ' if x.__class__.__name__.endswith("FunctionDef") and x.name.startswith("test_")'
    ") for p in sys.argv[1:]))'"
)
DECLARED_COUNT_CMD = (
    "python3 -c 'import ast,sys;b=ast.parse(open(sys.argv[1]).read()).body;"
    "a=[n for n in b if isinstance(n,ast.Assign) and any(getattr(t,"
    '"id",None)==sys.argv[2] for t in n.targets)];v=a[0].value;'
    "print(len(v.keys) if isinstance(v,ast.Dict) else len(v.elts))'"
)

# Per-file ceiling on any git or awk call this module makes on behalf of a claim.
# Nothing here should ever take long; a hang means the git call wedged, and a
# claim must not be able to hang a commit gate.
CMD_TIMEOUT = 30


class Unresolved(Exception):
    """A marked claim this gate cannot re-derive, and why.

    An exception rather than a sentinel, because every derivation here has a
    second failure mode (a file that does not parse, a name that matches nothing)
    and a sentinel makes those two indistinguishable from a count of zero.
    """


def _git_ls(root: str, staged: bool, pathspec: str, nul: bool = False) -> str:
    """The `git` invocation this gate answers tree questions with, as shell text.

    Spelled out rather than derived from `_gitutil.listed_files`, because the
    printed command has to BE the command the gate ran -- a reader who re-runs it
    and gets a different number has been handed a rumour. The two are pinned
    together by `--probe`, which executes this string against a fixture and
    compares it with the count returned beside it.

    `nul` adds `-z`, and only the `tests` command needs it: that one pipes the
    listing into `xargs -0`, and word splitting on a filename holding a space
    would silently drop a test module from the count the reader is reproducing.
    The `files` count prints plain `| wc -l`, which a person can read at a glance;
    a filename containing a NEWLINE would miscount there, and that is stated
    rather than defended -- git itself assumes such a name in every other place
    this estate lists files.
    """
    z = " -z" if nul else ""
    if staged:
        return f"git ls-files{z} --cached -- {shlex.quote(pathspec)}"
    base = f"git ls-files{z} --cached --others --exclude-standard"
    return f"{base} -- {shlex.quote(pathspec)}"


def tree_files(root: str, staged: bool, pathspec: str = "*") -> list[str]:
    """Every path the TREE this scope judges holds, matching `pathspec`.

    NOT `checks/_gitutil.listed_files(root, staged=True)`: that lists the staged
    DIFF, which is the right question for "which files changed" and the wrong one
    for "what does this commit contain". A `files` claim re-derived at `--staged`
    against the diff would count the staged subset and call it the tree -- a false
    red on every commit that touches one file of forty.

    So this asks git the tree question directly, and at `--staged` that is
    `ls-files --cached`: the INDEX, which is what the commit will hold. Untracked
    files are in the worktree answer and cannot be in the index one, which is the
    difference between the two scopes rather than an inconsistency.
    """
    cmd = (
        ["git", "-C", root, "ls-files", "-z", "--cached"]
        if staged
        else ["git", "-C", root, "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    )
    cmd += ["--", pathspec]
    result = subprocess.run(
        cmd, capture_output=True, timeout=CMD_TIMEOUT, check=False, env=foreign_repo_env()
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace").strip()
        raise Unresolved(
            f"git could not list the tree: {stderr.splitlines()[-1] if stderr else 'no output'}"
        )
    return sorted(n.decode("utf-8", "replace") for n in result.stdout.split(b"\0") if n)


def pathspec(directory: str, extension: str | None) -> str:
    """`DIR/*.EXT` -- git's own pathspec, which is what the count is measured in."""
    return f"{directory}/*" if not extension else f"{directory}/*.{extension.lstrip('.')}"


def _check_target(target: str) -> None:
    """Refuse a target that cannot be a repo-relative path."""
    if not target or target.startswith(("/", "-")):
        raise Unresolved(f"{target!r} is not a repo-relative path")
    if ".." in target.split("/"):
        raise Unresolved(f"{target!r} points outside this repository")


def _read(root: str, staged: bool, rel: str) -> bytes:
    """The bytes this scope judges, or a refusal.

    At `--staged` a target that is not in the INDEX is refused rather than read
    from the worktree: the claim is going into a commit and the file is not in it,
    so the number it asserts cannot be about that commit. `_gitutil.content_bytes`
    falls back to the worktree in exactly that case, which is right for a checker
    policing a file's own content and wrong here.
    """
    if staged and rel not in set(tree_files(root, True)):
        raise Unresolved(
            f"{rel} is not in the index at --staged, so no claim about it can be re-derived"
        )
    blob = content_bytes(root, rel, staged=staged)
    if blob is None:
        raise Unresolved(f"{rel} cannot be read")
    return blob


def _kind(root: str, staged: bool, target: str) -> str:
    """`file` | `dir`, as the tree this scope judges sees it. Refuses a target
    that is neither -- prose naming a path that is not there is the defect this
    whole gate exists for, so it is a finding and not a skip."""
    _check_target(target)
    if target in set(tree_files(root, staged)):
        return "file"
    if tree_files(root, staged, f"{target}/*"):
        return "dir"
    # At --staged the listing above is the INDEX, so "not there" has a specific
    # and much more actionable name than "not there": the claim is going into a
    # commit and the file it names is not in that commit.
    if staged:
        raise Unresolved(
            f"{target} is not in the index at --staged, so no claim about it can be re-derived"
        )
    raise Unresolved(f"{target} names no path in this repo")


def _python_files(root: str, staged: bool, target: str) -> list[str]:
    """The `.py` files a `tests`/`declared` claim is measured over."""
    kind = _kind(root, staged, target)
    if kind == "file":
        if os.path.splitext(target)[1] != ".py":
            raise Unresolved(f"{target} is not a Python module; tests are counted by ast")
        return [target]
    return tree_files(root, staged, pathspec(target, "py"))


def _parse(blob: bytes, rel: str) -> ast.Module:
    try:
        with warnings.catch_warnings():  # the module's own invalid escapes are not our report
            warnings.simplefilter("ignore")
            return ast.parse(blob.decode("utf-8", "replace"))
    except SyntaxError as exc:
        raise Unresolved(f"{rel} does not parse as Python: {exc.msg} at line {exc.lineno}") from exc


def _count_tests(blob: bytes, rel: str) -> int:
    """Test functions in one module: sync or async, at any nesting depth.

    `__class__.__name__.endswith("FunctionDef")` covers `FunctionDef` and
    `AsyncFunctionDef` in one predicate -- and it is the predicate the printed
    command uses too, so the two cannot drift.
    """
    tree = _parse(blob, rel)
    return sum(
        1
        for node in ast.walk(tree)
        if node.__class__.__name__.endswith("FunctionDef") and node.name.startswith("test_")
    )


def _declared_lists(tree: ast.Module) -> dict[str, object]:
    """{name: node} for every module-level assignment holding string literals.

    A List/Tuple/Set of string constants, or a Dict whose keys are string
    constants. Anything else is not a DECLARED LIST: a comprehension
    (`steps = [s for s in ... if ...]`, which `verify.py` has as its second
    `steps` assignment) has no elements to count, and picking it would report a
    count of a filter rather than of the roster.
    """
    out: dict[str, object] = {}
    for node in getattr(tree, "body", []):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, (ast.Dict, ast.List)):
            continue
        value = node.value
        # Each element (a dict's KEY) must itself be a str constant. Reading `.value` off every
        # element raised AttributeError on a `Name` (`X = [name, "b"]`) or a `**spread` key
        # (None), and the whole gate died with a traceback over an ordinary module.
        keys = value.keys if isinstance(value, ast.Dict) else value.elts
        if keys and all(isinstance(k, ast.Constant) and isinstance(k.value, str) for k in keys):
            for target in node.targets:
                if getattr(target, "id", None):
                    out.setdefault(target.id, value)
    return out


def _declared_count(tree: ast.Module, rel: str, name: str) -> tuple[int, str]:
    """(count, name_resolved) for the structure this claim counts.

    The resolved name comes back because the PRINTED command takes it as an
    argument: a claim that named no structure is resolved here by there being
    exactly one candidate, and the command has to name the same one the count came
    from or it prints something else.
    """
    lists = _declared_lists(tree)
    if name:
        if name not in lists:
            raise Unresolved(
                f"{rel} declares no module-level string list named {name!r} "
                f"(it has {', '.join(sorted(lists)) or 'none'})"
            )
        return len(
            lists[name].keys if isinstance(lists[name], ast.Dict) else lists[name].elts
        ), name
    if len(lists) != 1:
        raise Unresolved(
            f"{rel} declares {len(lists)} module-level string lists "
            f"({', '.join(sorted(lists)) or 'none'}), so the claim does not say which one is "
            "counted -- name it as PATH:NAME, or stop writing the number down"
        )
    only = min(lists)
    return len(lists[only].keys if isinstance(lists[only], ast.Dict) else lists[only].elts), only


def derive_lines(root: str, staged: bool, target: str, name: str, glob: str | None):
    """`N lines in PATH` -- the file-length cap's own definition of a line."""
    kind = _kind(root, staged, target)
    if kind == "dir":
        raise Unresolved(f"{target} is a directory; a `lines` claim names one file")
    blob = _read(root, staged, target)
    return line_count(blob), f"awk 'END{{print NR}}' {shlex.quote(target)}"


def derive_tests(root: str, staged: bool, target: str, name: str, glob: str | None):
    """`N tests in PATH` -- test functions, counted by ast."""
    files = _python_files(root, staged, target)
    if not files:
        raise Unresolved(f"{target} holds no .py file to count tests in")
    total = 0
    for rel in files:
        total += _count_tests(_read(root, staged, rel), rel)
    if len(files) == 1:
        command = f"{TEST_COUNT_CMD} {shlex.quote(files[0])}"
    else:
        # `-z`/`xargs -0` rather than `$(...)`: word splitting on a filename
        # holding a space would silently drop a test module from the count the
        # reader is being asked to reproduce.
        listing = _git_ls(root, staged, pathspec(target, "py"), nul=True)
        command = f"{listing} | xargs -0 {TEST_COUNT_CMD}"
    return total, command


def derive_declared(root: str, staged: bool, target: str, name: str, glob: str | None):
    """`N gates in PATH[:NAME]` -- elements of a named module-level list."""
    kind = _kind(root, staged, target)
    if kind == "dir":
        raise Unresolved(f"{target} is a directory; name the file that declares the list")
    tree = _parse(_read(root, staged, target), target)
    count, resolved = _declared_count(tree, target, name)
    command = f"{DECLARED_COUNT_CMD} {shlex.quote(target)} {shlex.quote(resolved)}"
    return count, command


def derive_files(root: str, staged: bool, target: str, name: str, glob: str | None):
    """`N *.EXT files under DIR` -- the length of git's own answer."""
    if not glob:
        raise Unresolved("a `files` claim must name the extension it counts")
    if _kind(root, staged, target) == "file":
        raise Unresolved(f"{target} is a file; a `files` claim names the directory holding them")
    spec = pathspec(target, glob.lstrip("*."))
    return len(tree_files(root, staged, spec)), f"{_git_ls(root, staged, spec)} | wc -l"


def run_command(command: str, cwd: str) -> str:
    """Run a printed command and return its stdout. For `--probe` only.

    Every git call inside this module on someone else's tree takes
    `foreign_repo_env()` (contract 12): a hook's GIT_DIR would otherwise point a
    probe's `git` at the real repository.
    """
    result = subprocess.run(
        command,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=CMD_TIMEOUT,
        check=False,
        env=foreign_repo_env(),
    )
    if result.returncode != 0:
        raise Unresolved(
            f"the printed command failed (exit {result.returncode}): "
            f"{result.stderr.strip().splitlines()[-1] if result.stderr.strip() else 'no output'}"
        )
    return result.stdout.strip()


# The derivations this gate can perform, by the `kind` `_claim_text` maps a unit
# word onto. Four entries, and the COUNT is load-bearing rather than decorative:
# `check_claim_derivation`'s docstring marks a claim about this very table and
# re-derives it on every run, so adding a fifth derivation without updating the
# prose makes the gate report its own docstring as stale -- the class this gate
# exists for, applied to itself. Declared after the functions because a table of
# names cannot be built before they exist.
CHECKS = {
    "lines": derive_lines,
    "tests": derive_tests,
    "declared": derive_declared,
    "files": derive_files,
}
