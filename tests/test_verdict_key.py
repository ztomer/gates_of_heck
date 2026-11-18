"""lib/gate_identity.py: a verdict's key carries everything besides the tree that decided it.

GOH.VERDICTKEY (found by the CIP.CACHING survey): a proven step was keyed on rustc, cargo, python
and swift only, so another shellcheck first on PATH answered "proven ... skipped"; and a land under
a hand-set GOH_LAND_GATE wrote the goh-landed line a full gate writes. One red test per input.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env
from env_probe import print_safe
from test_proven import STEP, env, goh, proven, repo, runs  # noqa: F401  (fixtures)
from timing_bounds import patience

LIB = REPO_ROOT / "lib" / "gate_identity.py"
STUB = "#!/bin/sh\necho '{name} 99.99.99 (a stub)'\n"


def _key(root: Path, mode: str = "full", path_front: Path | None = None) -> str:
    e = hermetic_env(drop_git=True)
    if path_front is not None:
        e["PATH"] = f"{path_front}:{e['PATH']}"
    r = subprocess.run([sys.executable, str(LIB), "key", "--mode", mode, "--root", str(root)],
                       env=e, capture_output=True, text=True, timeout=patience(60),
                       stdin=subprocess.DEVNULL, check=True)  # fmt: skip
    return r.stdout.strip()


def _gates(tmp_path: Path) -> Path:
    root = tmp_path / "gates-root"
    (root / "gates").mkdir(parents=True)
    (root / "gates" / "structural.sh").write_text("#!/bin/sh\nexit 0\n")
    return root


def _stub(tmp_path: Path, name: str) -> Path:
    d = tmp_path / "stub-bin"
    d.mkdir(exist_ok=True)
    (d / name).write_text(STUB.format(name=name))
    (d / name).chmod(0o755)
    return d


@pytest.mark.parametrize("tool", ["shellcheck", "ruff", "cargo-llvm-cov", "xcodebuild"])
def test_another_tool_first_on_path_is_another_identity(tmp_path: Path, tool: str) -> None:
    root = _gates(tmp_path)
    assert _key(root) != _key(root, path_front=_stub(tmp_path, tool)), tool


def test_another_gate_mode_is_another_identity(tmp_path: Path) -> None:
    """A hand-set GOH_LAND_GATE=true is not the full gate."""
    root = _gates(tmp_path)
    assert _key(root, "full") == _key(root, "full")
    assert _key(root, "full") != _key(root, "true")


def test_an_edited_gate_script_is_another_identity(tmp_path: Path) -> None:
    root = _gates(tmp_path)
    before = _key(root)
    (root / "gates" / "structural.sh").write_text("#!/bin/sh\nexit 0  # edited\n")
    assert _key(root) != before
    (root / "gates" / "__pycache__").mkdir()
    (root / "gates" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    assert _key(root) == _key(root), "bytecode is no gate edit"


def test_a_proven_step_runs_again_under_another_shellcheck(goh, repo, env, tmp_path) -> None:  # noqa: F811
    """The survey's reproduction: the same step, the same tree, another shellcheck first on PATH."""
    assert proven(goh, repo, env).returncode == 0 and runs(env) == 1
    again = proven(goh, repo, env)
    assert runs(env) == 1, again.stdout + again.stderr
    stubbed = dict(env, PATH=f"{_stub(tmp_path, 'shellcheck')}:{env['PATH']}")
    third = proven(goh, repo, stubbed, STEP)
    assert third.returncode == 0, third.stdout + third.stderr
    assert runs(env) == 2, f"proven under another shellcheck: {third.stdout}"


# GOH.VERDICTKEYDRIFT: one commit's key was 7d4d1790 from its checkout, ff7a7b7f from its head
# export, 0f232590.. under RUSTUP_TOOLCHAIN=stable and aa4f97f2 in the land that wrote its loaded
# record, so the ship's lookup missed a green verdict. The key is the bytes and the tools, only.

RUSTUP_STUB = """#!/bin/sh
# A rustup proxy: `rustc`/`cargo` link here; `rustup which NAME` resolves the override.
case "$(basename "$0")" in
    rustup) [ "$1" = which ] || exit 1
        case "${RUSTUP_TOOLCHAIN:-stable}" in
            stable|stable-host) echo "$(dirname "$0")/../toolchains/stable-host/$2" ;;
            *) echo "$(dirname "$0")/../toolchains/$RUSTUP_TOOLCHAIN/$2" ;;
        esac ;;
    *) echo "$(basename "$0") 1.0.0 (stub)" ;;
esac
"""


def _rustup(tmp_path: Path) -> Path:
    """A PATH dir whose rustc and cargo are a rustup proxy over toolchains stable-host and other."""
    base = tmp_path / "rustup"
    (base / "bin").mkdir(parents=True)
    (base / "bin" / "rustup").write_text(RUSTUP_STUB)
    (base / "bin" / "rustup").chmod(0o755)
    for tool in ("rustc", "cargo"):
        (base / "bin" / tool).symlink_to("rustup")
        for chain, body in (("stable-host", "stable"), ("other", "other build")):
            f = base / "toolchains" / chain / tool
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"#!/bin/sh\necho {body}\n")
            f.chmod(0o755)
    return base / "bin"


def _ident(root: Path, mode: str = "full", **extra: str) -> str:
    e = hermetic_env(drop_git=True)
    e.pop("RUSTUP_TOOLCHAIN", None)
    front = extra.pop("PATH_FRONT", "")
    if front:
        e["PATH"] = f"{front}:{e['PATH']}"
    e.update(extra)
    r = subprocess.run([sys.executable, str(LIB), "key", "--mode", mode, "--root", str(root)],
                       env=e, cwd=root, capture_output=True, text=True, timeout=patience(60),
                       stdin=subprocess.DEVNULL, check=True)  # fmt: skip
    return r.stdout.strip()


def _committed_gates(tmp_path: Path) -> tuple[Path, Path]:
    """A checkout holding gates of every kind (a script, an executable, a link, a nested lib, an
    ignored build file) and the head export of its commit, as gates/_from_head.sh makes one."""
    root = tmp_path / "checkout"
    files = {"gates/run.sh": "#!/bin/sh\nexit 0\n", "lib/a.py": "A = 1\n",
             "lib/deep/b.py": "B = 2\n", "tui/lib.sh": "ok() { :; }\n",
             "README.md": "not a gate\n", ".gitignore": "lib/built.out\n"}  # fmt: skip
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body)
    (root / "gates" / "run.sh").chmod(0o755)
    (root / "tools").mkdir()
    (root / "tools" / "run").symlink_to("../gates/run.sh")
    fast_init(root)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True, stdin=subprocess.DEVNULL)
    ident = ["-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "-C", str(root), *ident, "commit", "-q", "--no-verify", "-m", "gates"],
                   check=True, stdin=subprocess.DEVNULL)  # fmt: skip
    (root / "lib" / "built.out").write_text("ignored build output\n")
    export = tmp_path / "head" / "sha"
    export.mkdir(parents=True)
    archive = subprocess.run(["git", "-C", str(root), "archive", "HEAD"], check=True,
                             capture_output=True, stdin=subprocess.DEVNULL).stdout  # fmt: skip
    subprocess.run(["tar", "-x", "-C", str(export)], input=archive, check=True)
    (export / ".goh-head").write_text("sha\n")
    (export / "lib" / "__pycache__").mkdir()
    (export / "lib" / "__pycache__" / "a.cpython.pyc").write_bytes(b"\0")
    return root, export


def test_a_checkout_and_its_commits_head_export_key_alike(tmp_path: Path) -> None:
    root, export = _committed_gates(tmp_path)
    assert _ident(root) == _ident(export), "one commit, two gate identities"


def test_one_changed_gate_byte_changes_the_key_wherever_it_is_read(tmp_path: Path) -> None:
    """Calibration of the test above: the formula still sees a byte, uncommitted or exported."""
    root, export = _committed_gates(tmp_path)
    before = _ident(export)
    (export / "lib" / "a.py").write_text("A = 2\n")
    assert _ident(export) != before
    (root / "lib" / "a.py").write_text("A = 2\n")  # the same edit, uncommitted in the checkout
    assert _ident(root) == _ident(export)
    (root / "gates" / "run.sh").chmod(0o644)  # the mode is part of what runs
    assert _ident(root) != _ident(export)


def test_a_rustup_override_that_names_the_resolved_toolchain_is_the_same_identity(
    tmp_path: Path,
) -> None:
    root = _gates(tmp_path)
    proxy = str(_rustup(tmp_path))
    plain = _ident(root, PATH_FRONT=proxy)
    for spelling in ("stable", "stable-host"):
        assert _ident(root, PATH_FRONT=proxy, RUSTUP_TOOLCHAIN=spelling) == plain, spelling
    assert _ident(root, PATH_FRONT=proxy, RUSTUP_TOOLCHAIN="other") != plain, "another toolchain"


def test_a_swapped_toolchain_binary_behind_the_proxy_changes_the_key(tmp_path: Path) -> None:
    """The proxy is the same file either way: what it runs is the tool that judged."""
    root = _gates(tmp_path)
    proxy = _rustup(tmp_path)
    before = _ident(root, PATH_FRONT=str(proxy))
    rustc = proxy.parent / "toolchains" / "stable-host" / "rustc"
    rustc.write_text("#!/bin/sh\necho stable, rebuilt with more bytes\n")
    assert _ident(root, PATH_FRONT=str(proxy)) != before


@pytest.mark.skipif(shutil.which("rustup") is None, reason="no rustup on this machine")
def test_the_real_rustup_keys_its_resolved_toolchain_not_the_spelling(tmp_path: Path) -> None:
    root = _gates(tmp_path)
    e = hermetic_env(drop_git=True)
    e.pop("RUSTUP_TOOLCHAIN", None)
    shown = subprocess.run(["rustup", "show", "active-toolchain"], cwd=root, env=e, text=True,
                           capture_output=True, stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    name = (shown.stdout.split() or [""])[0]
    if shown.returncode != 0 or not name:
        pytest.skip(f"rustup names no active toolchain: {shown.stderr.strip()}")
    plain = _ident(root)
    assert _ident(root, RUSTUP_TOOLCHAIN=name) == plain, name
    channel = name.split("-")[0]
    if channel in {"stable", "beta", "nightly"}:
        assert _ident(root, RUSTUP_TOOLCHAIN=channel) == plain, channel


def test_proven_reuses_a_step_under_a_second_spelling(goh, repo, env, tmp_path) -> None:  # noqa: F811
    """gates/_proven.sh keys a step by gate_identity.py tools: the spelling is no new identity."""
    proxied = print_safe(dict(env, PATH=f"{_rustup(tmp_path)}:{env['PATH']}"))
    proxied.pop("RUSTUP_TOOLCHAIN", None)
    assert proven(goh, repo, proxied).returncode == 0 and runs(env) == 1
    spelled = proven(goh, repo, dict(proxied, RUSTUP_TOOLCHAIN="stable-host"))
    assert spelled.returncode == 0, spelled.stdout + spelled.stderr
    assert runs(env) == 1, f"re-ran under another spelling of one toolchain: {spelled.stdout}"
    other = proven(goh, repo, dict(proxied, RUSTUP_TOOLCHAIN="other"))
    assert other.returncode == 0 and runs(env) == 2, "another toolchain is another identity"
