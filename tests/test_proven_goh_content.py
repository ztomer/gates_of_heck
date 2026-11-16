"""The proven cache keys its gates root by CONTENT, the binary's build inputs included.

GOH.PROVENIDENTITY, two holes left by GOH.VERDICTKEYDRIFT:
  (1) gates/_proven.sh kept a goh-root identity of its own: a checkout by its HEAD sha and diff,
      a head export by its PATH. One commit's checkout and its export (the pre-commit's staged-tree
      export and the push's commit export, the land's worktree and the push's export) never reused
      each other's proven steps, and the same bytes proved twice. It now asks lib/gate_identity.py,
      the one formula a land's goh-landed line is keyed by.
  (2) lib/gate_identity.py PARTS left out crates/, yet every gate runs bin/goh built from it: a
      change to goh's Rust alone left the key unchanged, so a step proven under one goh binary was
      reused under another. The build inputs (crates/, Cargo.toml, Cargo.lock, rust-toolchain.toml)
      are in the content now, an explicit GOH_BIN is keyed by its bytes, and a checkout whose build
      inputs moved from HEAD outside GOH_LIVE (it runs HEAD's build) is keyed by HEAD's trees.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env
from test_proven import STEP, _git, env, goh, repo, runs  # noqa: F401  (fixtures)
from timing_bounds import patience

LIB = REPO_ROOT / "lib" / "gate_identity.py"
RUST = "crates/goh/src/lib.rs"
BUILD = {RUST: "pub fn f() {}\n", "Cargo.toml": "[workspace]\n", "Cargo.lock": "version = 4\n",
         "rust-toolchain.toml": "[toolchain]\n"}  # fmt: skip


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body)


def _with_crates(checkout: Path) -> Path:
    """The gates copy, with the binary's build inputs committed beside them."""
    _write(checkout, BUILD)
    _git(checkout, "add", "-A")
    _git(checkout, "commit", "-q", "-m", "the binary's build inputs")
    return checkout


def _export(checkout: Path, tmp_path: Path) -> Path:
    """The head export of the checkout's commit, as gates/_from_head.sh cuts one."""
    sha = _git(checkout, "rev-parse", "HEAD")
    dest = tmp_path / "head" / sha
    dest.mkdir(parents=True)
    archive = subprocess.run(["git", "-C", str(checkout), "archive", "HEAD"], capture_output=True,
                             check=True, stdin=subprocess.DEVNULL).stdout  # fmt: skip
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive, check=True)
    (dest / ".goh-head").write_text(sha + "\n")
    return dest


def _prove(root: Path, repo: Path, env: dict) -> subprocess.CompletedProcess[str]:  # noqa: F811
    """The step, run by the gates at root (a checkout or an export) with GOH_DIR there."""
    e = dict(env, GOH_DIR=str(root), GOH_HEAD_CACHE=str(root.parent))
    return subprocess.run(["bash", str(root / "gates" / "proven.sh"), "--", STEP], cwd=repo, env=e,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          timeout=patience(120), check=False)  # fmt: skip


def test_a_checkout_and_its_commits_export_reuse_each_others_proven_steps(goh, repo, env, tmp_path):  # noqa: F811
    checkout = _with_crates(goh)
    export = _export(checkout, tmp_path)
    first = _prove(checkout, repo, env)
    assert first.returncode == 0 and runs(env) == 1, first.stdout + first.stderr
    second = _prove(export, repo, env)
    assert second.returncode == 0, second.stdout + second.stderr
    assert runs(env) == 1, (
        f"the export re-proved what its commit's checkout proved: {second.stdout}"
    )
    assert "proven on this tree" in second.stdout, second.stdout


def test_one_changed_gate_byte_in_the_export_still_reruns(goh, repo, env, tmp_path):  # noqa: F811
    """Calibration of the reuse above: the shared key still sees a gate byte."""
    export = _export(_with_crates(goh), tmp_path)
    assert _prove(export, repo, env).returncode == 0 and runs(env) == 1
    with open(export / "gates" / "local_ci.sh", "a") as f:
        f.write("# one byte more\n")
    again = _prove(export, repo, env)
    assert again.returncode == 0 and runs(env) == 2, again.stdout + again.stderr


def test_a_crates_source_change_alone_reruns_a_proven_step(goh, repo, env, tmp_path):  # noqa: F811
    """Hole (2): the gates run the build of crates/; a Rust edit is a new binary, no old proof."""
    export = _export(_with_crates(goh), tmp_path)
    assert _prove(export, repo, env).returncode == 0 and runs(env) == 1
    (export / RUST).write_text("pub fn f() -> u8 { 1 }\n")
    again = _prove(export, repo, env)
    assert again.returncode == 0, again.stdout + again.stderr
    assert runs(env) == 2, f"a proven step was reused across a goh source change: {again.stdout}"


def _key(root: Path, **extra: str) -> str:
    e = hermetic_env(drop_git=True)
    for k, v in extra.items():
        if v:
            e[k] = v
        else:
            e.pop(k, None)
    r = subprocess.run([sys.executable, str(LIB), "key", "--mode", "full", "--root", str(root)],
                       env=e, cwd=root.parent, capture_output=True, text=True, timeout=patience(60),
                       stdin=subprocess.DEVNULL, check=True)  # fmt: skip
    return r.stdout.strip()


def _gates_root(tmp_path: Path) -> Path:
    root = tmp_path / "gates-root"
    _write(root, {"gates/run.sh": "#!/bin/sh\nexit 0\n", "docs/x.md": "not a gate\n", **BUILD})
    return root


def test_each_build_input_of_the_binary_is_in_the_land_key(tmp_path: Path) -> None:
    root = _gates_root(tmp_path)
    for rel in BUILD:
        before = _key(root)
        with open(root / rel, "a") as f:
            f.write("\n")
        assert _key(root) != before, f"{rel} is a build input of the goh the gates run"
    before = _key(root)
    (root / "docs" / "x.md").write_text("still not a gate\n")
    assert _key(root) == before, "a doc is no input of a gate"


def test_an_explicit_goh_bin_is_keyed_by_its_bytes(tmp_path: Path) -> None:
    root = _gates_root(tmp_path)
    binary = tmp_path / "goh"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    named = _key(root, GOH_BIN=str(binary))
    assert named != _key(root, GOH_BIN=""), "a named binary is not the content's build"
    binary.write_text("#!/bin/sh\necho another build\n")
    assert _key(root, GOH_BIN=str(binary)) != named, "another binary under the same name"


def test_a_checkout_running_heads_build_keys_heads_trees(tmp_path: Path, monkeypatch) -> None:
    """Outside GOH_LIVE a checkout with uncommitted Rust runs HEAD's build (gates/_goh_bin.sh): its
    identity is not that of a copy holding the edited bytes (which runs THEIR build). In-process: a
    gate run outside GOH_LIVE from a test is refused (gates/_from_head.py)."""
    sys.path.insert(0, str(LIB.parent))
    import gate_identity as gi

    root = _gates_root(tmp_path)
    fast_init(root)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "c")
    (root / RUST).write_text("pub fn f() -> u8 { 1 }\n")
    plain = tmp_path / "plain"
    shutil.copytree(root, plain, ignore=shutil.ignore_patterns(".git"))
    monkeypatch.setenv("GOH_LIVE", "1")
    assert gi.identity(root) == gi.identity(plain), "GOH_LIVE builds the edit"
    monkeypatch.delenv("GOH_LIVE")
    assert gi.identity(root) != gi.identity(plain), "HEAD's build is no edit's"
    _git(root, "commit", "-q", "-am", "the edit, committed")
    assert gi.identity(root) == gi.identity(plain), "HEAD's build IS the edit's"
