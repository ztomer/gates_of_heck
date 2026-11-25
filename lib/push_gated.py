"""Whether a checkout's pre-push hook runs goh's push gate: the ONE reading (GOH.SHIMPUSHGATED).

A push-gated checkout is the local record that a landed commit passed the full gate before it
could be pushed, so a hosted CI run still queued or running is ADVISORY over it (owner ruling
2026-11-11; .loop LOOP.CIADVISORY). Three readers asked: the release kit's CI step
(tools/release-kit/ci_step.sh), goh ship's verify (lib/ship_verify.py) and `loopctl done` (.loop
tools/loopadvisory.py). Each grepped the hook for the text `push_gate.sh`, so when the stock hook
became a shim that execs gates/hook.sh (GOH.CLASS.ROLLOUT, 7223a9e) every shimmed checkout read as
ungated, and the gates_of_heck v0.119.0 ship failed verify on its own shim. A grep also passed any
hook that merely MENTIONS the name (a comment, a stand-in that `exit 0`s).

So a hook is judged by its BYTES, never by a name in them. The hook git resolves for `pre-push`
(core.hooksPath, else the common hooks dir) runs goh's push gate when it is:

  * today's stock shim, byte for byte (`hooks/pre-push`; it execs gates/hook.sh, which dispatches
    pre-push to gates/push_gate.sh and is pinned by tests/test_hook_shim.py); or
  * a stock pre-push goh once shipped that ran the push gate: its `retired_hooks.sha256` line
    carries the `push-gate` tag, the third field. Consumers keep those until the hooks are
    refreshed, and the first stock pre-push (73391f0) ran the repo's gate in the working tree, so it
    is untagged and reads ungated, as it always did.

A hook a repo edited matches neither and reads ungated: nothing says what it runs. Retiring a stock
pre-push (an ABI move of the shim) appends its hash tagged `push-gate`; tests/test_push_gated.py
refuses an untagged pre-push line it has not been told about, and refuses a new reader that greps
a hook for a gate script's name.

    push_gated.py [--goh ROOT] [REPO]   # prints the hook (as git names it) when gated, else nothing

THE reading of whether a checkout's pre-push hook runs goh's push gate (GOH.SHIMPUSHGATED): the
hook git resolves is judged by its BYTES -- today's stock shim, or a retired stock pre-push tagged
`push-gate` in `retired_hooks.sha256` -- never by a gate script's name grepped out of it (the shim
names none). The CLI for `tools/release-kit/ci_step.sh`, `push_gated` for `lib/ship_verify.py` and
.loop's `loopadvisory.py`; `name_greps` is the class check that refuses a new name grep.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    import contextlib as _contextlib

    with _contextlib.suppress(ModuleNotFoundError):  # a copy outside any checkout: run it as is
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

# Appended, never first: .loop loads this file by path, and none of its own modules is shadowed.
sys.path.append(str(Path(__file__).resolve().parent))
import gitrun  # beside this file

GOH_ROOT = Path(__file__).resolve().parent.parent
HOOK = "pre-push"
TAG = "push-gate"
# The class this replaced: a code line that searches text for a gate entry point's NAME.
_GATE_NAME = re.compile(
    r"\b(?:push_gate|commit_gate|commit_msg|sequencer_gate|hook)\\?\.sh\b|\bPUSH_GATE\b"
)
_SEARCH = re.compile(r"\bgrep\b|\sin\s|\.find\(|re\.(?:search|match)\(|\.contains\(|\.count\(|=~")
_COMMENT = re.compile(r"(?:^|\s)(?:#|//).*$")
_OK = "hook-bytes-ok:"
_CODE = (".py", ".sh", ".bash", ".mjs", ".cjs", ".js", ".rs")


def shim(goh: Path = GOH_ROOT) -> bytes:
    """Today's stock pre-push: the shim, the same bytes under every hook name."""
    try:
        return (goh / "hooks" / HOOK).read_bytes()
    except OSError:
        return b""


def retired_push_gates(goh: Path = GOH_ROOT) -> set[str]:
    """The sha256 of every stock pre-push goh once shipped that ran the push gate."""
    try:
        lines = (goh / "retired_hooks.sha256").read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    out = set()
    for line in lines:
        parts = line.split()
        if len(parts) >= 3 and not line.startswith("#") and parts[1] == HOOK and parts[2] == TAG:
            out.add(parts[0])
    return out


def runs_push_gate(data: bytes, goh: Path = GOH_ROOT) -> bool:
    """DATA, a pre-push hook's bytes, is a stock hook goh shipped that runs its push gate."""
    if not data:
        return False
    if data == shim(goh):
        return True
    return hashlib.sha256(data).hexdigest() in retired_push_gates(goh)


def resolve(repo: Path) -> str:
    """The pre-push hook git runs for REPO, as git names it (relative to REPO when inside it)."""
    try:
        done = gitrun.run_reader(["rev-parse", "--git-path", f"hooks/{HOOK}"], repo=repo,
                                 timeout=30)  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def push_gated(repo: str | Path, goh: Path = GOH_ROOT) -> str:
    """REPO's pre-push hook (as git names it) when it runs goh's push gate, else ""."""
    repo = Path(repo)
    rel = resolve(repo)
    if not rel:
        return ""
    try:
        data = (repo / rel).read_bytes()
    except OSError:
        return ""
    return rel if runs_push_gate(data, goh) else ""


def name_greps(root: Path, files: list[str]) -> list[str]:
    """`path:line: text` for each code line of the source FILES (relative to ROOT) that searches
    for a gate script's name: how a reader decided "this hook runs goh's gate" before this module,
    blind to the shim, which names none. Comments are not code; `# hook-bytes-ok: <reason>` excuses
    a line. Tests are the callers' to exclude: they write stand-in hooks and assert on output."""
    sys.path.append(str(GOH_ROOT / "checks"))  # here alone: .loop loads this module by path
    import _prose_mask

    found = []
    for rel in (f for f in files if f.endswith(_CODE)):
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not _GATE_NAME.search(text):
            continue
        prose = rel.endswith(".py")  # a docstring that names the gate searches nothing
        lines = (_prose_mask.python_code_only(text) if prose else text).split("\n")
        for n, (line, read) in enumerate(zip(text.split("\n"), lines, strict=True), 1):
            if _OK in line:
                continue
            code = _COMMENT.sub("", read)
            if _GATE_NAME.search(code) and _SEARCH.search(code):
                found.append(f"{rel}:{n}: {line.strip()}")
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--goh", type=Path, default=GOH_ROOT)
    ap.add_argument("repo", type=Path, nargs="?", default=Path.cwd())
    a = ap.parse_args(argv)
    hook = push_gated(a.repo, a.goh)
    if hook:
        print(hook)
    return 0


if __name__ == "__main__":
    sys.exit(main())
