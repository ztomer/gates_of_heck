"""The native binary is resolved once per process tree (roadmap 2.1, 2026-10-06).

`goh_resolve_native` (gates/_goh_bin.sh) costs ~12 spawns per call -- under GOH_LIVE a git diff and
an ls-files for the working tree's Rust delta, then HEAD's stamp and the binary's own -- and every
`goh.sh` call paid it again: 31 ms from the export, 62 ms from the checkout. The first resolution
in a process tree exports its answer (GOH_RESOLVED_FOR, GOH_RESOLVED_BIN); a child for the same
gates root and the same GOH_LIVE mode takes it while the binary is still executable, and asks
again otherwise.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env

REAL_GIT = subprocess.run(
    ["sh", "-c", "command -v git"], capture_output=True, text=True
).stdout.strip()
# A gate's shape: `_from_head.sh` first (it exports git's binding variables once), then a resolution.
PARENT = (
    f'. "{REPO_ROOT}/gates/_from_head.sh"; . "{REPO_ROOT}/gates/_goh_bin.sh"; goh_resolve_native; '
    f'bash "{REPO_ROOT}/gates/goh.sh" emoji; bash "{REPO_ROOT}/gates/goh.sh" emoji'
)


def _shimmed(tmp_path: Path, **env: str):
    shim, log = tmp_path / "shim", tmp_path / "git.log"
    shim.mkdir(exist_ok=True)
    (shim / "git").write_text(
        f'#!/bin/sh\necho "$(ps -o command= -p $PPID) :: $*" >> "{log}"\nexec "{REAL_GIT}" "$@"\n'
    )
    (shim / "git").chmod(0o755)
    full = hermetic_env(**env)
    full["PATH"] = f"{shim}:{full['PATH']}"
    return full, log


def test_a_goh_sh_child_takes_its_parents_resolution(tmp_path: Path) -> None:
    env, log = _shimmed(tmp_path)
    r = subprocess.run(
        ["bash", "-c", PARENT], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout + r.stderr
    callers = [ln.split(" :: ")[0].split() for ln in log.read_text().splitlines()]
    from_children = [c for c in callers if len(c) > 1 and c[1].endswith("/goh.sh")]
    assert any(c[:2] == ["bash", "-c"] for c in callers), "the parent never resolved: no baseline"
    assert not from_children, from_children


def test_an_answer_whose_binary_is_gone_is_asked_again(tmp_path: Path) -> None:
    env, _ = _shimmed(
        tmp_path,
        GOH_RESOLVED_FOR=f"{REPO_ROOT}|live:{hermetic_env().get('GOH_LIVE', '')}",
        GOH_RESOLVED_BIN=str(tmp_path / "no-such-goh"),
    )
    r = subprocess.run(
        [
            "bash",
            "-c",
            f'. "{REPO_ROOT}/gates/_goh_bin.sh"; goh_resolve_native; printf %s "$goh_native"',
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.stdout and "no-such-goh" not in r.stdout, r.stdout + r.stderr


def test_an_answer_for_another_gates_root_is_not_taken(tmp_path: Path) -> None:
    fake = tmp_path / "fake-goh"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    env, _ = _shimmed(tmp_path, GOH_RESOLVED_FOR="/elsewhere|live:1", GOH_RESOLVED_BIN=str(fake))
    r = subprocess.run(
        [
            "bash",
            "-c",
            f'. "{REPO_ROOT}/gates/_goh_bin.sh"; goh_resolve_native; printf %s "$goh_native"',
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.stdout and str(fake) != r.stdout, r.stdout + r.stderr


def test_rust_gate_runs_its_native_steps_without_the_dispatcher(tmp_path: Path) -> None:
    """With the binary resolved, rust_gate's four native steps exec it directly: `bash goh.sh`
    was ~30 ms of start-up per step, four steps per crate, 29 crates a media_server push."""
    crate = tmp_path / "c"
    (crate / "src").mkdir(parents=True)
    (crate / "Cargo.toml").write_text(
        '[package]\nname = "c"\nversion = "0.1.0"\nedition = "2021"\n'
    )
    (crate / "src" / "lib.rs").write_text("pub fn f() -> u64 {\n    1\n}\n")
    (crate / ".gatesrc").write_text("GOH_DEPS_OFFLINE=1\n")
    subprocess.run(["cargo", "generate-lockfile", "-q", "--offline"], cwd=crate, check=True)
    subprocess.run(["git", "-C", str(crate), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(crate), "add", "-A"], check=True)
    shim, log = tmp_path / "shim", tmp_path / "bash.log"
    shim.mkdir()
    (shim / "bash").write_text(f'#!/bin/sh\necho "$*" >> "{log}"\nexec /bin/bash "$@"\n')
    (shim / "bash").chmod(0o755)
    env = hermetic_env(GOH_PROVEN="0")
    env["PATH"] = f"{shim}:{env['PATH']}"
    r = subprocess.run(
        ["/bin/bash", str(REPO_ROOT / "gates" / "rust_gate.sh"), str(crate)],
        cwd=crate, env=env, capture_output=True, text=True, timeout=300,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    dispatched = [ln for ln in log.read_text().splitlines() if "/goh.sh " in f"{ln} "]
    assert not dispatched, dispatched
