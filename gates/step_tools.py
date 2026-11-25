"""step_tools.py -- a gate step's tool is in a table, or the step is refused (GOH.REQTOOLS).

gates/required_tools.py derives what a repo needs from the tables it knows, so a step that runs a
tool no table names is installed by nobody on a hosted runner and fails there while it passes here:
ztools' `cargo deny` stayed alive only through a hand list in its own workflow (tools/ci_tools.tsv),
the copy the manifest exists to replace. So a tool enters through a table or not at all:

  * gates/required_tools.tsv, the tools a goh gate runs;
  * SYSTEM below, what every runner image carries;
  * the repo's own `GOH_STEP_TOOLS` in `.gatesrc` (space-separated), the tools it installs itself.

What a step runs: its leading command (after `[tags]`, `VAR=value` and `env`/`exec`/`time`), the
module of a `python3 -m X`, and the `cargo-X` of a `cargo X` that is not cargo's own. The STEPS
alone: read for arbitrary words, a repo script's prose became tools (`cargo actually`, `cargo
reports`; three repos, 2026-11-15). A path, a variable and a repo script are not tools.

From 2026-11-23 (`crates/goh/src/grace.rs`, the ambient `GOH_GRACE`) an unnamed tool is
refused: `required_tools.py --repo . --unnamed` and every `--repo` mode exit 1 naming it, and
local_ci.sh runs `--unnamed` before its steps, so the local gate and the hosted install refuse the
same step. Before it, the same lines warn. Pinned by tests/test_step_tools.py.

Helper of `required_tools.py`: a `GOH_CI_STEPS` step's tool (its leading command, a `python3 -m X`
module, a non-builtin `cargo X`'s `cargo-X`) must be in a table: `required_tools.tsv`, `SYSTEM`, or
the repo's `GOH_STEP_TOOLS` (GOH.REQTOOLS). From 2026-11-23 (`grace.rs`) `required_tools.py --repo`
and `local_ci.sh` (via `_steps.sh` `lci_step_tools`) refuse an unnamed one; a warning before.
Invoked by via required_tools.py, local_ci.sh.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "checks"))
from _gitutil import foreign_repo_env  # checks/ is the one copy

# What every runner image the house uses carries (macOS with the Xcode command-line tools, and
# ubuntu), so a step may run it with no table row: the shell and its utilities, git, make, the
# Apple toolchain drivers, and rustup's own binaries (a tool the manifest names is not repeated). A
# tool NOT on every image belongs in the manifest or in the repo's GOH_STEP_TOOLS, never here.
SYSTEM = frozenset(
    [
        "bash",
        "sh",
        "env",
        "exec",
        "time",
        "cd",
        "test",
        "true",
        "false",
        "echo",
        "printf",
        "cat",
        "cp",
        "mv",
        "rm",
        "mkdir",
        "ln",
        "ls",
        "grep",
        "sed",
        "awk",
        "find",
        "xargs",
        "sort",
        "tee",
        "touch",
        "chmod",
        "tar",
        "gzip",
        "curl",
        "git",
        "make",
        "swift",
        "xcodebuild",
        "rustup",
        "rustc",
    ]
)
# cargo's own subcommands and rustup's components: `cargo X` for these installs nothing.
CARGO_OWN = frozenset(
    [
        "add",
        "bench",
        "build",
        "check",
        "clean",
        "clippy",
        "doc",
        "fetch",
        "fix",
        "fmt",
        "generate-lockfile",
        "help",
        "init",
        "install",
        "locate-project",
        "login",
        "metadata",
        "new",
        "owner",
        "package",
        "pkgid",
        "publish",
        "remove",
        "report",
        "run",
        "rustc",
        "rustdoc",
        "search",
        "test",
        "tree",
        "uninstall",
        "update",
        "vendor",
        "verify-project",
        "version",
        "yank",
    ]
)
_WRAPPERS = {"env", "exec", "time", "nice", "command"}
_TAG = re.compile(r"^\[[^\]]*\]\s*")
_ASSIGN = re.compile(r"^[A-Za-z_]\w*=")


def _steps(repo: str) -> tuple[list[str], set[str]]:
    """(.gatesrc's GOH_CI_STEPS, its GOH_STEP_TOOLS), read the way local_ci.sh reads them: the file
    sourced in a child with GOH_DIR unexpanded, so a step names goh's gates as `$GOH_DIR/...`."""
    if not os.path.isfile(os.path.join(repo, ".gatesrc")):
        return [], set()
    script = (
        "unset GOH_CI_STEPS GOH_STEP_TOOLS; GOH_DIR='$GOH_DIR'; . ./.gatesrc >/dev/null 2>&1"
        ' </dev/null; printf "%s\\0%s" "${GOH_CI_STEPS-}" "${GOH_STEP_TOOLS-}"'
    )
    out = subprocess.run(
        ["bash", "-c", script], cwd=repo, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, check=False,
        env=foreign_repo_env(),  # sourcing .gatesrc needs no repository bound
    ).stdout  # fmt: skip
    steps, _, declared = out.partition("\0")
    return [s for s in steps.split(":") if s.strip()], set(declared.split())


def _step_tools(step: str) -> set[str]:
    """The tools one step runs: its leading command, a `python3 -m X`'s module, a `cargo X`'s
    `cargo-X`. A path, a variable or a repo script is not a tool."""
    try:
        words = shlex.split(_TAG.sub("", step.strip()))
    except ValueError:
        words = step.split()
    while words and (_ASSIGN.match(words[0]) or words[0] in _WRAPPERS):
        words = words[1:]
    out: set[str] = set()
    if not words or "/" in words[0] or "$" in words[0]:
        return out
    head = words[0]
    if head in ("python3", "python") and len(words) > 2 and words[1] == "-m":
        out.add(words[2])
    elif head == "cargo" and len(words) > 1 and not words[1].startswith("-"):
        if words[1] not in CARGO_OWN:
            out.add(f"cargo-{words[1]}")
    elif head not in ("python3", "python"):
        out.add(head)
    return out


def unnamed(repo: str, manifest: set[str]) -> list[str]:
    """The tools `repo`'s GOH_CI_STEPS run that no table names (`manifest`: the tools
    gates/required_tools.tsv names), each with the step that runs it."""
    steps, declared = _steps(repo)
    named = manifest | SYSTEM | declared
    found: dict[str, str] = {}
    for step in steps:
        for tool in sorted(_step_tools(step) - named):
            found.setdefault(tool, step.strip())
    return [f"{tool} (step: {step})" for tool, step in sorted(found.items())]


def over_from(root: str | None = None) -> int | None:
    """crate::grace::OVER_FROM, read from `crates/goh/src/grace.rs` (its one home) in the gates tree
    `root` (default: this one); None when that tree has no grace."""
    root = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "crates", "goh", "src", "grace.rs")
    try:
        with open(path, encoding="utf-8") as fh:
            match = re.search(r"pub const OVER_FROM: u64 = ([\d_]+);", fh.read())
    except OSError:
        return None
    return int(match.group(1).replace("_", "")) if match else None


def grace_over(now: float | None = None) -> bool:
    """crate::grace::over() in Python: GOH_GRACE `over`/`within`, else the day (over_from)."""
    seam = os.environ.get("GOH_GRACE")
    if seam in ("over", "within"):
        return seam == "over"
    when = over_from()
    if when is None:
        raise SystemExit("required_tools: no OVER_FROM in crates/goh/src/grace.rs")
    return (time.time() if now is None else now) >= when


def refuse(repo: str, manifest: set[str]) -> int:
    """1 when `repo` runs a tool no table names and the grace is over; the names said either way."""
    found = unnamed(repo, manifest)
    if not found:
        return 0
    over = grace_over()
    word = "refused" if over else "refused from 2026-11-23"
    for line in found:
        print(f"required_tools: {word}: {line} -- no table names it", file=sys.stderr)
    print(
        "required_tools: name it in gates/required_tools.tsv (a tool a goh gate runs) or in this"
        " repo's .gatesrc GOH_STEP_TOOLS (a tool the repo installs itself), or SYSTEM if every"
        " runner image carries it",
        file=sys.stderr,
    )
    return 1 if over else 0
