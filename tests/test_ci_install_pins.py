"""Every package hosted CI installs is a manifest row, and every pip install is the manifest's PIN.

GOH.PYTESTPIN, 2026-11-19: gate.yml's `pip install pytest-xdist` carried no version and the
manifest's pytest rows were floors, so the runner installed the newest pytest, 9.2.0, while this
box ran 9.1.1. 9.2.0 ids a string parameter over 100 characters as argname+index, and v0.139.6's
release CI was red on a test id the local gate never saw (GOH.CIRED01396). The local gate is CI's
gate only when both install the same release of every tool.

So, structurally:
  * every pip row of gates/required_tools.tsv is a pin (`=X.Y.Z`), which `--install` writes as
    `tool==X.Y.Z`: the manifest's own install lines cannot be unpinned;
  * every `pip install` written in .github/ names a manifest pip row at exactly its pin, as a
    literal or as a variable the same file reads from `required_tools.py --pin <tool>`;
  * every `brew install` / `cargo install` written in .github/ names a manifest row (those kinds
    are floors: brew has no version to pin, and the manifest's cargo rows install the newest at or
    above the floor), or an exemption below with its reason, and a stale exemption is red.
tools/pytest.sh holds the box to the same pins (`required_tools.py --layer goh-suite --pins-held`).
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from conftest import REPO_ROOT

from lib import source_closure

MANIFEST = "gates/required_tools.tsv"
WF = ".github/workflows/ci.yml"  # the calibration trees' workflow
# What the scan reads from the real tree: every workflow and action file, and the manifest.
TREE_READS = (".github/*", MANIFEST)
# (kind, package) -> why a CI install of it is not a manifest row.
EXEMPT = {
    ("brew", "bash"): "the bash >= 5 floor is lib/bash5.sh's, which the goh-floors action meets",
}
_INSTALL = re.compile(r"(?<![\w-])(pip3?|brew|cargo)\s+install\s+(.*)$")
_PIN_VAR = re.compile(r"(\w+)=\"?\$\([^)]*required_tools\.py\"?\s+--pin\s+([\w.-]+)\)")


def manifest(root: Path) -> list[dict]:
    rows = []
    for line in (root / MANIFEST).read_text(encoding="utf-8").splitlines():
        cols = line.split("\t")
        if line.startswith("#") or len(cols) != 6:
            continue
        rows.append({"tool": cols[1], "kind": cols[2], "floor": cols[5]})
    return rows


def unpinned_rows(root: Path) -> list[str]:
    """Every pip row whose floor column is not a pin."""
    return [
        f"{MANIFEST}: pip row {r['tool']} is {r['floor']}, not a pin (=X.Y.Z)"
        for r in manifest(root)
        if r["kind"] == "pip" and not r["floor"].startswith("=")
    ]


def _ci_files(root: Path) -> list[Path]:
    gh = root / ".github"
    return sorted(p for p in gh.rglob("*") if p.suffix in {".yml", ".yaml", ".sh"} and p.is_file())


def _packages(args: str) -> list[str]:
    """The package arguments of one install command (options and their values dropped)."""
    try:
        words = shlex.split(args.split("#", 1)[0])
    except ValueError:
        words = args.split()
    out, skip = [], False
    for w in words:
        if skip:
            skip = False
            continue
        if w in {"&&", "||", ";", "|"}:
            break
        if w.startswith("-"):
            skip = w in {"--version", "--root", "--target", "-r", "--requirement", "-c"}
            continue
        out.append(w)
    return out


def findings(root: Path) -> list[str]:
    rows = manifest(root)
    pins = {r["tool"]: r["floor"][1:] for r in rows if r["kind"] == "pip" and r["floor"][:1] == "="}
    named = {(r["kind"], r["tool"]) for r in rows}
    found = unpinned_rows(root)
    used: set[tuple[str, str]] = set()
    for path in _ci_files(root):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        from_pin = {m.group(1): m.group(2) for m in _PIN_VAR.finditer(text)}
        for n, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            m = _INSTALL.search(line)
            if not m:
                continue
            kind = "pip" if m.group(1).startswith("pip") else m.group(1)
            for pkg in _packages(m.group(2)):
                name, _, version = pkg.partition("==")
                where = f"{rel}:{n}: {kind} install {pkg}"
                if (kind, name) in EXEMPT:
                    used.add((kind, name))
                    continue
                if kind != "pip":
                    if (kind, name) not in named:
                        found.append(f"{where}: no {MANIFEST} {kind} row names {name}")
                    continue
                if name not in pins:
                    found.append(f"{where}: no pinned {MANIFEST} pip row names {name}")
                elif not version:
                    found.append(f"{where}: unpinned; the manifest pins {name} {pins[name]}")
                elif version.startswith("$"):
                    var = version.strip("${}")
                    if from_pin.get(var) != name:
                        found.append(
                            f"{where}: ${var} is not read from required_tools.py --pin {name}"
                        )
                elif version != pins[name]:
                    found.append(f"{where}: differs from the manifest's pin {pins[name]}")
    found += [
        f"EXEMPT {kind} {name}: no CI install names it -- delete the stale exemption"
        for kind, name in sorted(set(EXEMPT) - used)
    ]
    return found


def test_every_ci_install_is_a_manifest_pin():
    assert findings(REPO_ROOT) == []


# ---- calibration: each shape goes red, from a copy of this tree's manifest ---------------------


def _tree(tmp_path: Path, workflow: str) -> Path:
    source_closure.copy_closure(tmp_path, MANIFEST)
    wf = tmp_path / ".github" / "workflows" / "ci.yml"
    wf.parent.mkdir(parents=True)
    wf.write_text(workflow + "\n          brew install bash\n", encoding="utf-8")
    return tmp_path


def _pin(tool: str) -> str:
    return next(r["floor"][1:] for r in manifest(REPO_ROOT) if r["tool"] == tool)


def test_an_unpinned_pip_install_is_red(tmp_path):
    root = _tree(tmp_path, '          "$py" -m pip install pytest-xdist')
    assert findings(root) == [
        (
            f"{WF}:1: pip install pytest-xdist: unpinned; the manifest pins "
            f"pytest-xdist {_pin('pytest-xdist')}"
        )
    ]


def test_a_pip_install_off_the_pin_is_red_and_at_it_is_green(tmp_path):
    pin = _pin("pytest")
    off = ".".join([*pin.split(".")[:-1], str(int(pin.split(".")[-1]) + 1)])
    root = _tree(tmp_path, f"          python3 -m pip install --upgrade pytest=={off} coverage")
    assert findings(root) == [
        (f"{WF}:1: pip install pytest=={off}: differs from the manifest's pin {pin}"),
        (f"{WF}:1: pip install coverage: unpinned; the manifest pins coverage {_pin('coverage')}"),
    ]
    root = _tree(tmp_path / "ok", f"          pip install pytest=={pin}")
    assert findings(root) == []


def test_a_pin_through_a_variable_must_be_read_from_the_manifest(tmp_path):
    read = (
        '          V="$("$py" gates/required_tools.py --pin ruff)"\n'
        '          "$py" -m pip install "ruff==$V"'
    )
    assert findings(_tree(tmp_path, read)) == []
    hand = '          V=0.1.0\n          "$py" -m pip install "ruff==$V"'
    assert findings(_tree(tmp_path / "hand", hand)) == [
        (f"{WF}:2: pip install ruff==$V: $V is not read from required_tools.py --pin ruff")
    ]


def test_a_tool_the_manifest_does_not_name_is_red(tmp_path):
    root = _tree(
        tmp_path, "          pip install requests==1.0\n          cargo install cargo-nextest"
    )
    assert findings(root) == [
        (f"{WF}:1: pip install requests==1.0: no pinned {MANIFEST} pip row names requests"),
        (f"{WF}:2: cargo install cargo-nextest: no {MANIFEST} cargo row names cargo-nextest"),
    ]


def test_an_unpinned_manifest_pip_row_and_a_stale_exemption_are_red(tmp_path):
    root = _tree(tmp_path, "          echo none")
    (root / ".github/workflows/ci.yml").write_text("          echo none\n", encoding="utf-8")
    text = (root / MANIFEST).read_text(encoding="utf-8")
    (root / MANIFEST).write_text(
        text + "goh-suite\tsix\tpip\tpython3 -m pip install six\ttools/pytest.sh\t-\n",
        encoding="utf-8",
    )
    assert findings(root) == [
        f"{MANIFEST}: pip row six is -, not a pin (=X.Y.Z)",
        "EXEMPT brew bash: no CI install names it -- delete the stale exemption",
    ]


def test_the_box_is_held_to_the_same_pins(capsys):
    """`required_tools.py --pins-held` (gates/required_pins.py): an off-pin module is red, naming
    both releases; the running pytest at its own release is green."""
    import sys

    import pytest

    sys.path.insert(0, str(REPO_ROOT / "gates"))
    try:
        import required_pins
    finally:
        sys.path.remove(str(REPO_ROOT / "gates"))
    row = {"tool": "pytest", "kind": "pip", "install": "pip install pytest", "pin": "0.0.1"}
    assert required_pins.held([row]) == 1
    assert f"pytest is {pytest.__version__}" in capsys.readouterr().err
    assert required_pins.held([{**row, "pin": pytest.__version__}]) == 0
    assert required_pins.held([{**row, "tool": "no-such-dist-goh"}]) == 1
    assert "not installed" in capsys.readouterr().err


def _gates():
    import sys

    sys.path.insert(0, str(REPO_ROOT / "gates"))
    try:
        import required_pins
        import required_tools
    finally:
        sys.path.remove(str(REPO_ROOT / "gates"))
    return required_pins, [r for r in required_tools.rows() if r["layer"] == "goh-suite"]


def test_the_suite_loads_exactly_the_manifests_pytest_plugins(request):
    """GOH.PLUGINPARITY: pytest auto-loaded the box's pytest-asyncio, pytest-cov, anyio and
    platformdirs, none of which hosted CI installs, so the box ran another harness. THIS run's
    plugins are the manifest's goh-suite pins' and no others, and each pin's plugin is loaded."""
    required_pins, rows = _gates()
    assert (
        required_pins.plugins_off_manifest(request.config.pluginmanager.list_name_plugin(), rows)
        == []
    )


def test_a_plugin_off_the_manifest_is_red_and_a_pins_unloaded_plugin_is_red():
    """Calibration: iniconfig (pytest's own dependency, so on every runner, and no manifest row)
    registered as a plugin is red by name; an empty list misses every pin's plugin (xdist's)."""
    import iniconfig
    import pytest_cov.plugin
    import xdist.plugin

    required_pins, rows = _gates()
    found = required_pins.plugins_off_manifest(
        [("ini", iniconfig), ("x", xdist.plugin), ("c", pytest_cov.plugin)], rows
    )
    assert len(found) == 1 and "pytest plugin ini (iniconfig) is loaded" in found[0], found
    found = required_pins.plugins_off_manifest([], rows)
    assert found and all("is not loaded" in f for f in found), found
    assert any("xdist.plugin (pytest-xdist)" in f for f in found), found
