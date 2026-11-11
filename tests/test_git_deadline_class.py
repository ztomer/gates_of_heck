"""A git process stopped at a deadline strands no lock, and a reader takes none (GOH.GITKILL).

THE CLASS, found in .loop on 2026-11-08 (`4e82af6`, `7ae11d8`): `subprocess.run(["git", ...],
timeout=T)` SIGKILLs git alone at the deadline. A WRITER (commit, add, merge) dies holding
`index.lock` and orphans its hook, so every later commit in the repo is refused; a READER
(`git status`, `git diff`) takes the same lock OPTIONALLY to refresh the index, and dies holding it
just the same. Two cures, both structural here:

* a writer under a deadline runs in its own process group and gets SIGTERM (git's handler removes
  its lock files; its hooks die with the group) before SIGKILL: `lib/gitrun.py run_writer`, over
  `lib/killtree.py stop_group`, which every group kill now goes through;
* a reader runs with `GIT_OPTIONAL_LOCKS=0`, so there is no lock to strand: `run_reader`, the
  `GIT_OPTIONAL_LOCKS=0` prefix on a shell `git status`, `nul_list` in the Rust gitutil.

The sweep of goh found seven Python sites with a deadline on a reader (`ship`, the calibration
probe's two, the compact and discard hooks, `landed_verify`'s three), one SIGKILL-only group kill in
`killtree`, one in the release canary, and four shell readers of the working tree. The AST/line
scans below keep them from coming back: a NEW one is a red test, not a review comment.
"""

from __future__ import annotations

import ast
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
from _own_files import own_source_files
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))

# The two places a git read under a deadline is spelled: lib's, and the checks tier's twin (the
# checks directory is copied whole into fixtures and cannot import lib/).
SEAMS = {"lib/gitrun.py", "checks/_gitutil.py"}
SPAWNERS = {"run", "Popen", "check_output", "call", "check_call"}
# A SIGKILL group kill is allowed in these files only, each with the reason it is not a git writer's.
KILLPG_KILL_ALLOWED = {
    "checks/_gate_slot_probe.py": "a probe's own gate-slot holder (no git), killed after a 30 s wait",
}


def _python_sources() -> list[str]:
    """This checkout's own non-test Python: tests spawn git in scratch repos on purpose."""
    return [
        f
        for f in own_source_files("*.py")
        if not f.startswith(("tests/", "crates/")) and "/target/" not in f
    ]


def _tree(rel: str) -> ast.Module:
    return ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))


def _spawn_calls(tree: ast.Module):
    for node in ast.walk(tree):
        func = node.func if isinstance(node, ast.Call) else None
        if (
            isinstance(func, ast.Attribute)
            and func.attr in SPAWNERS
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
        ):
            yield node


def _starts_with_git(call: ast.Call) -> bool:
    if not call.args or not isinstance(call.args[0], (ast.List, ast.Tuple)):
        return False
    first = call.args[0].elts[:1]
    return bool(first) and isinstance(first[0], ast.Constant) and first[0].value == "git"


def _git_with_deadline() -> list[str]:
    found = []
    for rel in _python_sources():
        if rel in SEAMS:
            continue
        for call in _spawn_calls(_tree(rel)):
            if _starts_with_git(call) and any(k.arg == "timeout" for k in call.keywords):
                found.append(f"{rel}:{call.lineno}")
    return found


def test_no_python_site_runs_git_under_a_bare_subprocess_timeout() -> None:
    """subprocess.run(timeout=) SIGKILLs git alone: it goes through lib/gitrun.py instead."""
    assert _git_with_deadline() == [], (
        "git under subprocess's timeout= (SIGKILL of git alone, an index.lock stranded): "
        "use gitrun.run_reader / gitrun.run_writer"
    )


def test_the_scan_sees_a_planted_site(tmp_path: Path) -> None:
    """The instrument can fail: the same scan reads a planted offender and reports it."""
    planted = ast.parse(
        "import subprocess\nsubprocess.run(['git', 'status'], timeout=5)\n"
        "subprocess.run(['echo'], timeout=5)\nsubprocess.run(['git', 'log'])\n"
    )
    hits = [c.lineno for c in _spawn_calls(planted)
            if _starts_with_git(c) and any(k.arg == "timeout" for k in c.keywords)]  # fmt: skip
    assert hits == [2]


def _seam_calls_with_hand_c(tree: ast.Module) -> list[int]:
    """`gitrun.run_reader([..."-C"...])` / `run_writer`: a repo named by hand, not by `repo=`."""
    hits = []
    for node in ast.walk(tree):
        func = node.func if isinstance(node, ast.Call) else None
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name not in {"run_reader", "run_writer"} or not node.args:
            continue
        first = node.args[0]
        elts = first.elts[:1] if isinstance(first, (ast.List, ast.Tuple)) else []
        if elts and isinstance(elts[0], ast.Constant) and elts[0].value == "-C":
            hits.append(node.lineno)
    return hits


def test_a_seam_call_names_its_repo_by_keyword_never_by_a_hand_written_dash_c() -> None:
    """GOH.SHIPGITDIR: git ignores `-C` once GIT_DIR is set, and a hook exports it, so a call that
    names a repository goes through `repo=`, which also drops git's local-env list."""
    found = [f"{rel}:{n}" for rel in _python_sources() if rel not in SEAMS
             for n in _seam_calls_with_hand_c(_tree(rel))]  # fmt: skip
    assert found == [], f"pass repo= to gitrun, not a leading '-C': {found}"


def test_the_dash_c_scan_sees_a_planted_site() -> None:
    planted = ast.parse(
        "gitrun.run_reader(['-C', r, 'status'], timeout=5)\n"
        "gitrun.run_reader(['status'], repo=r, timeout=5)\n"
        "run_writer(['-C', r, 'add'], deadline_s=1)\n"
    )
    assert _seam_calls_with_hand_c(planted) == [1, 3]


def test_repo_drops_a_leaked_git_dir(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live GIT_DIR naming another repo: `-C` alone answers for THAT one; `repo=` does not."""
    import shutil

    import gitrun

    other = tmp_path / "other"
    shutil.copytree(repo, other, symlinks=True)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    ask = ["rev-parse", "--absolute-git-dir"]
    leaked = gitrun.run_reader(["-C", str(repo), *ask], timeout=10)
    assert (
        Path(leaked.stdout.strip()) == (other / ".git").resolve()
    )  # calibration: the leak is real
    named = gitrun.run_reader(ask, repo=repo, timeout=10)
    assert Path(named.stdout.strip()) == (repo / ".git").resolve()


def test_a_direct_git_status_or_diff_carries_the_no_lock_env() -> None:
    """A literal `["git", ..., "status"|"diff"]` (no --cached) names GIT_OPTIONAL_LOCKS in its env."""
    bad = []
    for rel in _python_sources():
        for call in _spawn_calls(_tree(rel)):
            if not _starts_with_git(call):
                continue
            words = [e.value for e in call.args[0].elts if isinstance(e, ast.Constant)]
            if not ({"status", "diff"} & set(words)) or {"--cached", "--staged"} & set(words):
                continue
            env = next((ast.unparse(k.value) for k in call.keywords if k.arg == "env"), "")
            if "OPTIONAL_LOCKS" not in env and "reader_env" not in env:
                bad.append(f"{rel}:{call.lineno}")
    assert bad == []


def test_a_group_kill_is_term_first_everywhere() -> None:
    """No os.killpg(.., SIGKILL) outside the named probe: lib/killtree.py stop_group TERMs first."""
    seen = set()
    for rel in _python_sources():
        for node in ast.walk(_tree(rel)):
            if (
                isinstance(node, ast.Call)
                and ast.unparse(node.func) == "os.killpg"
                and "SIGKILL" in ast.unparse(node)
            ):
                seen.add(rel)
    assert seen == set(KILLPG_KILL_ALLOWED), (
        f"a SIGKILL group kill outside killtree.stop_group: {sorted(seen - set(KILLPG_KILL_ALLOWED))};"
        f" a stale allowance: {sorted(set(KILLPG_KILL_ALLOWED) - seen)}"
    )


SHELL_SUFFIXES = (".sh", ".bash")
OPTIONAL = re.compile(r"\bgit\b[^|;&#\n]*\b(status|diff)\b")


def _shell_sources() -> list[str]:
    out = []
    for rel in own_source_files():
        if rel.startswith(("tests/", "crates/")) or "/target/" in rel:
            continue
        path = REPO_ROOT / rel
        if rel.endswith(SHELL_SUFFIXES) or (
            path.is_file() and path.stat().st_mode & stat.S_IXUSR and _shebang_is_shell(path)
        ):
            out.append(rel)
    return out


def _shebang_is_shell(path: Path) -> bool:
    try:
        return (
            path.open("rb")
            .read(40)
            .startswith((b"#!/bin/sh", b"#!/bin/bash", b"#!/usr/bin/env bash"))
        )
    except OSError:
        return False


def test_a_shell_git_status_or_diff_of_the_work_tree_takes_no_optional_lock() -> None:
    bad = []
    for rel in _shell_sources():
        for n, line in enumerate((REPO_ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("  #", 1)[0]
            if line.lstrip().startswith("#") or not OPTIONAL.search(code):
                continue
            if re.search(r"--(cached|staged)\b", code):
                continue
            if "GIT_OPTIONAL_LOCKS=0" not in code and "--no-optional-locks" not in code:
                bad.append(f"{rel}:{n}: {line.strip()[:90]}")
    assert bad == [], "\n".join(bad)


def test_no_shell_runs_git_under_the_timeout_command() -> None:
    bad = []
    for rel in _shell_sources():
        for n, line in enumerate((REPO_ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
            if not line.lstrip().startswith("#") and re.search(
                r"\b(g?timeout)\b[^|;&]*\bgit\b", line
            ):
                bad.append(f"{rel}:{n}")
    assert bad == []


def test_the_rust_git_readers_take_no_optional_lock() -> None:
    """Every `git diff` (not --cached) and `git status` the crates spawn names the no-lock setting
    in its file: gitutil's nul_list sets GIT_OPTIONAL_LOCKS, proven.rs passes --no-optional-locks."""
    bad = []
    for rel in own_source_files("crates/*.rs"):
        if "/tests/" in rel or rel.endswith(("tests.rs", "_tests.rs")) or "/target/" in rel:
            continue
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        lines = text.splitlines()
        for n, line in enumerate(lines):
            if re.search(r'"(status|diff)"', line) and "git" in text:
                window = " ".join(lines[max(0, n - 1) : n + 4])
                if "--cached" in window or "--staged" in window:
                    continue
                if re.search(r'get\("(status|diff)"\)|"(status|diff)"\s*:', line):  # JSON, not git
                    continue
                if "OPTIONAL_LOCKS" not in text and "no-optional-locks" not in text:
                    bad.append(f"{rel}:{n + 1}")
    assert bad == [], bad


# --- the behaviour: a real git shim, a real deadline --------------------------------------------


def _shim(tmp_path: Path, body: str) -> Path:
    shim = tmp_path / "shim"
    shim.mkdir(exist_ok=True)
    (shim / "git").write_text(f"#!/bin/bash\n{body}\n")
    (shim / "git").chmod(0o755)
    return shim


def _with_path(shim: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", f"{shim}:{os.environ['PATH']}")


def test_run_reader_sets_the_no_lock_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import gitrun

    log = tmp_path / "env.txt"
    _with_path(_shim(tmp_path, f'echo "${{GIT_OPTIONAL_LOCKS-unset}}" >> "{log}"'), monkeypatch)
    monkeypatch.delenv("GIT_OPTIONAL_LOCKS", raising=False)
    done = gitrun.run_reader(["status"], cwd=tmp_path, timeout=10)
    assert done.returncode == 0
    assert log.read_text().split() == ["0"]


def test_the_checks_tier_twin_sets_the_same_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sys.path.insert(0, str(REPO_ROOT / "checks"))
    import _gitutil

    log = tmp_path / "env.txt"
    _with_path(_shim(tmp_path, f'echo "${{GIT_OPTIONAL_LOCKS-unset}}" >> "{log}"'), monkeypatch)
    monkeypatch.delenv("GIT_OPTIONAL_LOCKS", raising=False)
    assert _gitutil.git_read(["status"], timeout=10).returncode == 0
    assert log.read_text().split() == ["0"]


def test_run_writer_terms_a_writer_at_its_deadline_and_its_lock_goes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shim is a git that holds index.lock and removes it on SIGTERM, as real git does."""
    import gitrun

    lock = tmp_path / "index.lock"
    body = f'touch "{lock}"; trap \'rm -f "{lock}"; exit 143\' TERM; while :; do sleep 0.05; done'
    _with_path(_shim(tmp_path, body), monkeypatch)
    done = gitrun.run_writer(["commit", "-q"], cwd=tmp_path, deadline_s=1.0)
    assert done is None, "a writer past its deadline reports it was stopped"
    assert not lock.exists(), "SIGKILL stranded index.lock: the writer was not TERMed first"


def test_run_writer_kills_a_git_that_ignores_term_after_the_grace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gitrun

    pid_file = tmp_path / "pid"
    body = f'echo $$ > "{pid_file}"; trap "" TERM; while :; do sleep 0.05; done'
    _with_path(_shim(tmp_path, body), monkeypatch)
    done = gitrun.run_writer(["commit", "-q"], cwd=tmp_path, deadline_s=0.5, grace_s=1.0)
    assert done is None
    time.sleep(0.2)
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_run_writer_returns_the_result_of_a_writer_that_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gitrun

    _with_path(_shim(tmp_path, 'echo "done $*"'), monkeypatch)
    done = gitrun.run_writer(["commit", "-q"], cwd=tmp_path, deadline_s=10)
    assert done is not None and done.returncode == 0 and done.stdout.strip() == "done commit -q"


def test_run_writer_stops_the_whole_group_a_hook_included(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gitrun

    hook_pid = tmp_path / "hook.pid"
    body = f"sh -c 'echo $$ > \"{hook_pid}\"; exec sleep 300' & wait"
    _with_path(_shim(tmp_path, body), monkeypatch)
    assert gitrun.run_writer(["commit"], cwd=tmp_path, deadline_s=0.5, grace_s=1.0) is None
    time.sleep(0.3)
    with pytest.raises(ProcessLookupError):
        os.kill(int(hook_pid.read_text()), 0)


def test_killtree_terms_before_it_kills(tmp_path: Path) -> None:
    import killtree

    marker = tmp_path / "termed"
    script = f"trap 'echo yes > \"{marker}\"; exit 0' TERM; while :; do sleep 0.05; done"
    with pytest.raises(subprocess.TimeoutExpired):
        killtree.run_captured(["bash", "-c", script], timeout=1)
    assert marker.read_text().strip() == "yes", "the group was SIGKILLed without a SIGTERM first"


# --- the sweep's call sites, each driven through a real git shim -------------------------------


def _logging_shim(tmp_path: Path) -> tuple[Path, Path]:
    log = tmp_path / "calls.txt"
    shim = _shim(
        tmp_path,
        f'echo "$1 $2 $3 | ${{GIT_OPTIONAL_LOCKS-unset}}" >> "{log}"; '
        'case " $* " in *" --show-toplevel "*) echo /x;; *" --is-inside-work-tree "*) echo true;; '
        '*" --git-dir "*) echo .git;; esac; exit 0',
    )
    return shim, log


def _lock_envs(log: Path, verb: str) -> list[str]:
    return [
        ln.rsplit("|", 1)[1].strip()
        for ln in log.read_text().splitlines()
        if f" {verb} " in f" {ln} "
    ]


def test_the_compact_hook_reads_status_without_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shim, log = _logging_shim(tmp_path)
    _with_path(shim, monkeypatch)
    monkeypatch.delenv("GIT_OPTIONAL_LOCKS", raising=False)
    spec = importlib_load("compact_restore", "hooks/claude/compact_restore.py")
    spec._git(str(tmp_path), "status", "--short")
    assert _lock_envs(log, "status") == ["0"], log.read_text()


def test_the_discard_guard_reads_the_work_tree_without_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shim, log = _logging_shim(tmp_path)
    _with_path(shim, monkeypatch)
    monkeypatch.delenv("GIT_OPTIONAL_LOCKS", raising=False)
    mod = importlib_load("discard_guard", "hooks/claude/discard_guard.py")
    mod._git(str(tmp_path), "diff", "--name-only", "HEAD", "--", "f")
    assert _lock_envs(log, "diff") == ["0"], log.read_text()


def test_ship_reads_status_without_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shim, log = _logging_shim(tmp_path)
    _with_path(shim, monkeypatch)
    monkeypatch.delenv("GIT_OPTIONAL_LOCKS", raising=False)
    mod = importlib_load("ship_under_test", "tools/ship.py")
    mod.git(tmp_path, "status", "--porcelain", "--untracked-files=no")
    assert _lock_envs(log, "status") == ["0"], log.read_text()


def importlib_load(name: str, rel: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod
