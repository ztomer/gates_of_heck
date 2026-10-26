"""A goh release must not touch the lock of a repo that consumes `goh-tui` by path (queue G8).

antiknob takes `goh-tui = { path = "../gates_of_heck/crates/goh-tui" }`, and its Cargo.lock
records goh-tui's version. While goh-tui inherited `[workspace.package] version`, EVERY goh
release moved that version, so every release left antiknob's lock stale and its `--locked` push
gate (and CI, which checks out goh's main) red -- refreshed by hand twice (0.48.0 -> 0.54.1 -> 0.56.0,
antiknob 8bdb5b2). The class: a path-consumed crate whose version moves for a reason that is not
its own. The fix: goh-tui keeps its OWN version line, moved only when its API or dependencies move,
so a consumer's lock moves exactly then and deliberately.

The end-to-end test is the release simulated on a scratch copy: a consumer is locked against
goh-tui, the workspace version is bumped the way a release bumps it, and the consumer's
`cargo build --locked` must stay green. The same simulation with goh-tui put back on
`version.workspace = true` must go red, or the test could not tell the two setups apart.
"""

import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

GOH = Path(__file__).resolve().parent.parent
TUI = GOH / "crates" / "goh-tui"
WS_VERSION = re.compile(r'(?m)^(\[workspace\.package\]\n(?:[^\[\n].*\n)*?version = ")([^"]+)(")')

needs_cargo = pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo not installed")


def _workspace_version(text: str) -> str:
    return tomllib.loads(text)["workspace"]["package"]["version"]


def test_goh_tui_keeps_its_own_version_line():
    """Inheriting the workspace version is the stale-lock class itself. And a literal EQUAL to the
    workspace version is one find-and-replace of a release away from being moved with it."""
    package = tomllib.loads((TUI / "Cargo.toml").read_text())["package"]
    version = package.get("version")
    assert isinstance(version, str), (
        'crates/goh-tui/Cargo.toml must declare its own `version = "x.y.z"`, not inherit the '
        "workspace's: every goh release would then stale every path consumer's Cargo.lock (G8)"
    )
    release = _workspace_version((GOH / "Cargo.toml").read_text())
    assert version != release, (
        f"goh-tui's own version {version} equals goh's release version {release}: a release that "
        "rewrites the number would move it too -- give goh-tui a number of its own"
    )


def _scratch(tmp_path: Path, inherit: bool) -> tuple[Path, Path]:
    """A copy of goh's workspace root with goh-tui as its only member, and a consumer beside it
    laid out the way antiknob is (`../gates_of_heck/crates/goh-tui`)."""
    goh = tmp_path / "gates_of_heck"
    root = (GOH / "Cargo.toml").read_text()
    root = re.sub(r"(?m)^members = \[.*\]$", 'members = ["crates/goh-tui"]', root)
    (goh / "tui").mkdir(parents=True)
    (goh / "Cargo.toml").write_text(root)
    shutil.copy2(GOH / "tui" / "stylerc", goh / "tui" / "stylerc")
    shutil.copytree(TUI, goh / "crates" / "goh-tui", ignore=shutil.ignore_patterns("target"))
    if inherit:
        manifest = goh / "crates" / "goh-tui" / "Cargo.toml"
        text = re.sub(r'(?m)^version = "[^"]+"$', "version.workspace = true", manifest.read_text())
        manifest.write_text(text)
    consumer = tmp_path / "consumer"
    (consumer / "src").mkdir(parents=True)
    (consumer / "Cargo.toml").write_text(
        '[package]\nname = "consumer"\nversion = "0.1.0"\nedition = "2021"\n\n'
        '[dependencies]\ngoh-tui = { path = "../gates_of_heck/crates/goh-tui" }\n'
    )
    (consumer / "src" / "main.rs").write_text('fn main() {\n    println!("{}", goh_tui::OK);\n}\n')
    return goh, consumer


def _release_bump(goh: Path) -> None:
    """What a goh release does to the manifests: `[workspace.package] version` moves on."""
    manifest = goh / "Cargo.toml"
    text = manifest.read_text()
    major, minor, _ = _workspace_version(text).split(".")
    bumped, count = WS_VERSION.subn(rf"\g<1>{major}.{int(minor) + 1}.0\g<3>", text)
    assert count == 1, "the scratch workspace manifest has no `[workspace.package] version` line"
    manifest.write_text(bumped)


def _cargo(consumer: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["cargo", *args, "--offline", "--quiet"],
        cwd=consumer,
        env={**os.environ, "CARGO_TARGET_DIR": str(consumer / "target")},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


@needs_cargo
@pytest.mark.parametrize(("inherit", "stays_green"), [(False, True), (True, False)])
def test_a_goh_release_leaves_a_path_consumers_lock_valid(tmp_path, inherit, stays_green):
    goh, consumer = _scratch(tmp_path, inherit=inherit)
    locked = _cargo(consumer, "generate-lockfile")
    assert locked.returncode == 0, locked.stderr
    before = _cargo(consumer, "build", "--locked")
    assert before.returncode == 0, before.stderr
    _release_bump(goh)
    after = _cargo(consumer, "build", "--locked")
    if stays_green:
        assert after.returncode == 0, f"a goh release staled the consumer's lock:\n{after.stderr}"
    else:
        assert after.returncode != 0, "the inheriting setup stayed green: the simulation is blind"
        assert "lock file" in after.stderr, after.stderr
