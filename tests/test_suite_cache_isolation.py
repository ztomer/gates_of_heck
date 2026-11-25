"""goh's suite never writes the user's push caches (G11, 2026-10-26).

THE CLASS. A test ran gates/push_gate.sh with its GOH_* scrubbed, so the gate fell back to the
REAL ~/.cache/goh/push-logs and left a zero-byte `repo-<sha>-<time>.log` there at every suite run.
Closed three ways: the suite runs in its own HOME and XDG_CACHE_HOME (tests/suite_home.py, G12), every
test gets private push roots that every env scrub keeps (tests/host_locks.py, `scrubbed`), and each
cache writer refuses the user's ~/.cache/goh under the suite (lib/suite_cache_guard.sh) -- so a new
test that slips past the first two is red, not a quiet write to the user's cache.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _fast_git import fast_init
from host_locks import CACHE_ROOTS, HOST_LOCKS, scrubbed

REPO_ROOT = Path(__file__).resolve().parent.parent
PUSH_GATE = REPO_ROOT / "gates" / "push_gate.sh"


def test_every_test_gets_private_push_roots() -> None:
    home_cache = os.path.realpath(os.path.expanduser("~/.cache/goh"))
    for name in CACHE_ROOTS:
        value = os.environ.get(name, "")
        assert value, f"{name} is not set for this test"
        assert not os.path.realpath(value).startswith(home_cache + "/push"), (name, value)


def test_a_private_root_is_named_not_made(tmp_path_factory) -> None:
    """GOH.FSEVQUIET: a directory per test for roots nearly no test writes was 11,058 file events a
    pre-step. Every root is a name of this test's own in the worker's ONE directory, made by
    whoever writes it."""
    home = tmp_path_factory.getbasetemp() / "host-locks"
    mine = [Path(os.environ[n]) for n in (*HOST_LOCKS, "GOH_PUSH_LOGS", "GOH_REUSE_DIR")]
    assert {p.parent for p in mine} == {home} and home.is_dir()
    assert len(set(mine)) == len(mine) and not [p for p in mine if p.exists()]


def test_an_env_scrub_keeps_the_private_roots() -> None:
    env = scrubbed(os.environ, ("GOH_", "GIT_"))
    for name in CACHE_ROOTS:
        assert env.get(name) == os.environ[name], name


# G12: the suite's own HOME, and every cache writer refusing the user's cache under the suite. Each
# writer is driven at a STAND-IN "real" home under tmp_path (HOME and the marker both name it), so a
# guard that does not fire writes into tmp_path, never into the user's cache.
REAL_MARKER = "GATES_OF_HECK_SUITE_REAL_HOME"


def _as_if_real(tmp_path: Path, *drop: str) -> tuple[Path, dict[str, str]]:
    real = tmp_path / "real-home"
    real.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in drop and k != "XDG_CACHE_HOME"}
    env.update(HOME=str(real), **{REAL_MARKER: str(real)})
    return real, env


def test_the_suite_runs_in_its_own_home() -> None:
    real = os.environ[REAL_MARKER]
    assert os.path.realpath(os.path.expanduser("~")) != real
    assert os.environ["XDG_CACHE_HOME"].startswith(f"{real}/.cache/goh/suite/")
    ident = subprocess.run(["git", "config", "--global", "user.email"], capture_output=True,
                           text=True, check=True, stdin=subprocess.DEVNULL).stdout.strip()  # fmt: skip
    assert ident == "suite@goh.test", "the suite's git identity is not its own minimal one"


def test_the_suites_git_writes_no_more_than_its_tests_read(tmp_path: Path) -> None:
    """GOH.FSEVQUIET: each `git init`, `git clone` and fetch of a test made git's sample hooks, a
    loose object per object fetched and a sync of it all to the SSD."""

    def git(*args: str) -> str:
        return subprocess.run(["git", *args], capture_output=True, text=True, check=True,
                              cwd=tmp_path, stdin=subprocess.DEVNULL).stdout  # fmt: skip

    git("init", "-q", "a")
    assert not list((tmp_path / "a/.git/hooks").iterdir()) and (tmp_path / "a/.git/info").is_dir()
    git("-C", "a", "commit", "-q", "--allow-empty", "-m", "x")
    git("init", "-q", "b")
    git("-C", "b", "fetch", "-q", "../a")
    kept = [p for p in (tmp_path / "b/.git/objects").rglob("*") if p.is_file()]
    assert sorted(p.suffix for p in kept) == [".idx", ".pack"], kept
    assert git("config", "core.fsync").strip() == "none"


def test_the_push_gate_refuses_the_users_push_cache(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    fast_init(repo, "main")
    (repo / "tools").mkdir()
    (repo / "tools" / "gate.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    real, env = _as_if_real(tmp_path, *CACHE_ROOTS)
    env["GOH_DIR"] = str(REPO_ROOT)
    proc = subprocess.run(["bash", str(PUSH_GATE)], cwd=repo, env=env, capture_output=True,
                          input=f"refs/heads/main {'1' * 40} refs/heads/main {'0' * 40}\n",
                          text=True, timeout=60, check=False)  # fmt: skip
    assert proc.returncode != 0 and "the push log" in proc.stderr, proc.stderr
    assert not (real / ".cache" / "goh" / "push-logs").exists()


def test_the_head_export_refuses_the_users_head_cache(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    fast_init(repo, "main")
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "c"],
                   check=True, stdin=subprocess.DEVNULL)  # fmt: skip
    real, env = _as_if_real(tmp_path, "GOH_LIVE", "GOH_HEAD_CACHE", "PYTEST_CURRENT_TEST")
    script = f'. "{REPO_ROOT}/gates/_from_head.sh"; _goh_head_dir "$1"; echo "rc=$?"'
    proc = subprocess.run(["bash", "-c", script, "_", str(repo / "x.sh")], env=env,
                          capture_output=True, text=True, timeout=60, check=False,
                          stdin=subprocess.DEVNULL)  # fmt: skip
    assert proc.returncode == 2 and "the head export cache" in proc.stderr, (
        proc.stdout + proc.stderr
    )
    assert not (real / ".cache" / "goh" / "head").exists()


def test_the_store_refuses_the_users_shared_store(tmp_path: Path) -> None:
    binary = tmp_path / "goh"
    binary.write_text("#!/bin/sh\necho stamp-g12\n")
    binary.chmod(0o755)
    real, env = _as_if_real(tmp_path, "GOH_BIN_STORE")
    store = tmp_path / "root" / "bin" / "by-stamp" / "x" / "goh"
    script = f'. "{REPO_ROOT}/gates/_goh_bin.sh"; goh_store_adopt "$1" "$2"; echo "rc=$?"'
    proc = subprocess.run(["bash", "-c", script, "_", str(binary), str(store)], env=env,
                          capture_output=True, text=True, timeout=60, check=False,
                          stdin=subprocess.DEVNULL)  # fmt: skip
    assert proc.returncode == 2 and "the shared goh store" in proc.stderr, proc.stdout + proc.stderr
    assert not (real / ".cache" / "goh" / "by-stamp").exists()


def test_agent_guard_refuses_the_users_spawn_ledger(tmp_path: Path) -> None:
    """The spawn ledger is goh.db's (GOH.STATEDBS4): under the suite, the real home's is refused."""
    real, env = _as_if_real(tmp_path, "XDG_STATE_HOME", "GOH_DB")
    code = (f"import sys; sys.path.insert(0, {str(REPO_ROOT / 'hooks' / 'claude')!r}); "
            "import agent_guard; print(agent_guard._append({'event': 't'}))")  # fmt: skip
    proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                          timeout=60, check=False, stdin=subprocess.DEVNULL)  # fmt: skip
    assert proc.returncode != 0 and "RealHomeOpen" in proc.stderr, proc.stdout + proc.stderr
    assert not (real / ".local" / "state" / "goh").exists()


# The toolchain a scrubbed environment reaches (queue GOH.EA2, item 2). The suite keeps CARGO_HOME,
# RUSTUP_HOME and SCCACHE_DIR by NAME, but a test that hands its child only `PATH` and `HOME`
# drops the names, and rustup, cargo and sccache fall back to their defaults under HOME -- the
# suite's throwaway home. There rustup found no toolchain and INSTALLED stable into it on every run
# (a download and a cold sccache compiler inside a timed test), and the first land of GOH.EA2 went
# red in test_a_broken_uncommitted_edit_never_reaches_the_published_binary: sccache exit 254 and
# "could not parse/generate dep info" from a compiler born seconds before. So each kept variable's
# default under HOME is a link to the same place: the scrubbed child gets the suite's toolchain.
def test_a_scrubbed_environment_reaches_the_kept_toolchain_and_caches() -> None:
    from suite_home import HOME_DEFAULTS

    home = Path(os.path.expanduser("~"))
    for name, rel in HOME_DEFAULTS.items():
        kept = os.environ.get(name)
        if not kept:
            continue
        assert os.path.realpath(home / rel) == os.path.realpath(kept), (name, rel)


def test_a_kept_cache_the_runner_has_not_made_yet_is_linked_too(tmp_path: Path) -> None:
    """GOH.CIRUSTGATE (advisory CI run 43624157558 on 7373ebe0): a fresh hosted runner has no
    ~/Library/Caches/Mozilla.sccache yet, but the gate pins SCCACHE_DIR to it (lib/sccache_dir.sh),
    and the suite home linked a kept variable's default only when its target EXISTED -- so the
    home's default was a separate, empty cache and the test above went red there, never here,
    where the cache always exists. The suite home makes the kept place and links it."""
    real = tmp_path / "real-home"
    real.mkdir()
    kept = real / "Library" / "Caches" / "Mozilla.sccache"
    env = {k: v for k, v in os.environ.items() if k not in (REAL_MARKER, "XDG_CACHE_HOME")}
    env.update(HOME=str(real), SCCACHE_DIR=str(kept))
    tests = str(REPO_ROOT / "tests")
    code = (f"import os, sys; sys.path.insert(0, {tests!r}); import suite_home; "
            "print(os.path.realpath(os.path.expanduser('~/' + suite_home.HOME_DEFAULTS["
            "'SCCACHE_DIR'])))")  # fmt: skip
    proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                          timeout=60, check=False, stdin=subprocess.DEVNULL)  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip() == os.path.realpath(kept), proc.stdout + proc.stderr


def test_rustup_under_a_scrubbed_environment_installs_nothing() -> None:
    if (
        not os.environ.get("RUSTUP_HOME")
        or subprocess.run(
            ["which", "rustup"], capture_output=True, check=False, stdin=subprocess.DEVNULL
        ).returncode
    ):
        return
    env = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]}
    said = subprocess.run(["rustup", "show", "home"], capture_output=True, text=True, env=env,
                          check=True, stdin=subprocess.DEVNULL).stdout.strip()  # fmt: skip
    assert os.path.realpath(said) == os.path.realpath(os.environ["RUSTUP_HOME"]), said


def test_the_suite_home_reaches_the_estate_but_never_the_live_loop(tmp_path, monkeypatch) -> None:
    """GOH.CIRED01310 (v0.139.10, CI run 44519781826): the home linked the real ~/Projects whole, so
    lib/reuse_fault.py's default queue was the owner's LIVE queue (`.loop/queue.json` under the
    real Projects) -- a phase table on this box, "no such file" on a hosted runner, and a test
    filing a fault went red only there. `Projects` is a directory of links, one per entry, with
    none for the loop's checkout."""
    import suite_home

    real = tmp_path / "real-home"
    for rel in ("Projects/.loop/tools", "Projects/repo", "Projects/games/g"):
        (real / rel).mkdir(parents=True)
    (real / "Projects/.loop/queue.json").write_text('{"phases": [{"id": "SPD"}]}')
    monkeypatch.setenv(suite_home.MARKER, str(real))
    home = suite_home.furnish(tmp_path / "home", suite_home.READ)
    assert (home / "Projects/repo").resolve() == (real / "Projects/repo").resolve()
    assert (home / "Projects/games/g").is_dir()
    assert not os.path.lexists(home / "Projects/.loop")
    assert not (Path.home() / "Projects" / ".loop").exists(), "this suite's own home reaches it"


def test_a_nested_home_links_an_already_hidden_projects_whole(tmp_path, monkeypatch) -> None:
    """GOH.FSCHURN3: a session entering afresh under the suite's home furnished its own `Projects`
    as ~45 links again, though that home's `Projects` already hides the loop: one link reaches the
    same entries and still never the loop."""
    import suite_home

    real = tmp_path / "suite-home"
    (real / "Projects/repo").mkdir(parents=True)
    monkeypatch.setenv(suite_home.MARKER, str(real))
    home = suite_home.furnish(tmp_path / "nested", suite_home.READ)
    assert (home / "Projects").is_symlink()
    assert (home / "Projects/repo").resolve() == (real / "Projects/repo").resolve()
    assert not os.path.lexists(home / "Projects/.loop")
