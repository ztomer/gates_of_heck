"""`_hook_inproc.run_hook` gives a hook what a subprocess start would (GOH.HOOKINPROC).

The seam replaces ~500 interpreter starts per suite, so its contract is pinned: stdin is the text
given, `os.environ` is REPLACED for the call and restored after (even when the hook raises), the
exit code is `main()`'s return or `SystemExit`'s code, an uncaught exception is a returncode 1 with
the traceback on stderr (a subprocess's), and stdout/stderr are captured, not printed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from _hook_inproc import run_hook

HOOK = """\
import os
import sys


def main():
    mode = sys.stdin.read()
    goh = sorted(k for k in os.environ if k.startswith("GOH_"))
    print("out:", os.environ.get("HOOK_PROBE"), goh)
    print("err", file=sys.stderr)
    if mode == "raise":
        raise RuntimeError("boom")
    if mode == "exit3":
        sys.exit(3)
    if mode == "exitmsg":
        sys.exit("a message")
    if mode == "exitnone":
        sys.exit()
    return 7 if mode == "seven" else 0
"""


@pytest.fixture
def hook(tmp_path: Path) -> Path:
    p = tmp_path / "probe_hook.py"
    p.write_text(HOOK)
    return p


def test_stdin_stdout_stderr_and_the_return_code_are_the_subprocess_shape(hook: Path) -> None:
    r = run_hook(hook, "seven", {"HOOK_PROBE": "x"})
    assert (r.returncode, r.stdout, r.stderr) == (7, "out: x []\n", "err\n")


def test_the_env_replaces_ours_during_the_call_and_is_restored_after(
    hook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOH_HOOK_INPROC_PROBE", "ours")
    monkeypatch.setenv("HOOK_PROBE", "ours")
    r = run_hook(hook, "", {"HOOK_PROBE": "theirs"})
    assert r.stdout == "out: theirs []\n"  # our GOH_* did not leak in
    assert os.environ["HOOK_PROBE"] == "ours" and os.environ["GOH_HOOK_INPROC_PROBE"] == "ours"


def test_no_env_is_the_inherited_one(hook: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOOK_PROBE", "inherited")
    assert run_hook(hook, "").stdout.startswith("out: inherited")


@pytest.mark.parametrize(
    ("mode", "code"), [("exit3", 3), ("exitmsg", 1), ("exitnone", 0), ("raise", 1)]
)
def test_an_exit_or_a_raise_is_a_returncode_and_the_env_comes_back(
    hook: Path, mode: str, code: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOOK_PROBE", "ours")
    r = run_hook(hook, mode, {"HOOK_PROBE": "theirs"})
    assert r.returncode == code
    assert ("RuntimeError: boom" in r.stderr) == (mode == "raise"), r.stderr
    assert os.environ["HOOK_PROBE"] == "ours"


def test_a_hook_is_imported_once_and_its_dir_leaves_sys_path(hook: Path) -> None:
    before = list(sys.path)
    run_hook(hook, "")
    run_hook(hook, "")
    assert sys.path == before
