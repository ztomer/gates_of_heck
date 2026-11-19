"""required_pins.py -- does THIS interpreter hold the manifest's pip pins? (GOH.PYTESTPIN)

`gates/required_tools.py --layer L --pins-held` asks it. Hosted CI installs every pinned pip row
at exactly its pin (`required_tools.py --install` writes `tool==X.Y.Z`); a box on another release
runs another gate. v0.139.6's release CI was red on a test id pytest 9.2.0 changed while the box
ran 9.1.1, and the local gate had passed the tree. tools/pytest.sh refuses to run on a box that
fails this, so goh's local suite is CI's.
"""

from __future__ import annotations

import re
import sys
import types
from importlib import metadata


def off_pin(row: dict, found: str) -> str | None:
    """Why `found` (a release, "" when the distribution is missing) is not `row`'s pin, naming both;
    None when it is the pin. The ONE judgement: `held` asks it of this interpreter's distribution,
    `required_tools.py --check` of a version the gate read through its own runner
    (GOH.PINCHECKFLOOR)."""
    if found == row["pin"]:
        return None
    return (
        f"{row['tool']} is {found or 'not installed'} in {sys.executable} and the manifest "
        f"pins {row['pin']}, the release hosted CI installs -- {row['install']}"
    )


def held(selected: list[dict]) -> int:
    """Red (1), naming both releases, for each pinned pip row of `selected` whose distribution in
    this interpreter is missing or is not exactly its pin; else 0."""
    failures = []
    for row in {r["tool"]: r for r in selected if r["kind"] == "pip" and r["pin"]}.values():
        try:
            found = metadata.version(row["tool"])
        except metadata.PackageNotFoundError:
            found = ""
        reason = off_pin(row, found)
        if reason:
            failures.append(reason)
    for reason in failures:
        print(f"required_tools: {reason}", file=sys.stderr)
    return 1 if failures else 0


# ---- the pytest plugin set (GOH.PLUGINPARITY) ---------------------------------------------------
# pytest auto-loads every installed distribution's `pytest11` entry point, so a plugin on the box
# and not on the runner (pytest-asyncio, pytest-cov, anyio, platformdirs: 2026-11-19) made the
# local suite another harness than CI's. goh's runs load NO entry point (pyproject.toml's
# `--disable-plugin-autoload`) and name each plugin with `-p`; these hold that set to the manifest.


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pinned(selected: list[dict]) -> set[str]:
    return {_norm(r["tool"]) for r in selected if r["kind"] == "pip" and r["pin"]}


def plugin_dists(plugins) -> dict[str, str]:
    """name -> owning distribution of each registered plugin (`(name, plugin)` pairs, as
    `pluginmanager.list_name_plugin()` gives them) that an installed distribution ships; a
    conftest or a test-tree module, which no distribution ships, is the tree's own."""
    owners = metadata.packages_distributions()
    out = {}
    for name, plugin in plugins:
        mod = plugin.__name__ if isinstance(plugin, types.ModuleType) else type(plugin).__module__
        dists = owners.get((mod or "").split(".")[0])
        if dists:
            out[str(name or mod)] = _norm(dists[0])
    return out


def plugins_off_manifest(plugins, selected: list[dict]) -> list[str]:
    """Each loaded plugin no pinned pip row of `selected` installs -- one hosted CI does not
    load -- and each pinned pip row's `pytest11` plugin that is NOT loaded, by name."""
    pinned, dists = _pinned(selected), plugin_dists(plugins)
    found = [
        f"pytest plugin {name} ({dist}) is loaded and is no pinned pip row of the manifest; "
        f"hosted CI does not install it"
        for name, dist in sorted(dists.items())
        if dist not in pinned
    ]
    loaded = set(dists.values())
    found += [
        f"pytest plugin {ep} ({dist}) is a manifest pin's and is not loaded -- name it with -p "
        f"in pyproject.toml's addopts"
        for ep, dist in manifest_plugins(selected)
        if dist not in loaded
    ]
    return found


def manifest_plugins(selected: list[dict]) -> list[tuple[str, str]]:
    """(module, distribution) of each `pytest11` entry point a pinned pip row of `selected` has."""
    pinned = _pinned(selected)
    return sorted(
        (ep.value, _norm(ep.dist.name))
        for ep in metadata.entry_points(group="pytest11")
        if ep.dist is not None and _norm(ep.dist.name) in pinned
    )
