#!/usr/bin/env python3
"""lib/goh-rustc wired machine-wide, owned here and nowhere else (GOH.RUSTCWRAPWIRE).

goh's rustc wrapper (lib/goh-rustc: cargo's own variables out of rustc's environment, path-free
proc-macro and OUT_DIR keys, GOH.SCCACHEENV and GOH.SCCACHEPATHS) only helps the builds that run
it. Two settings name cargo's wrapper: `RUSTC_WRAPPER` in ~/.zshenv, which outranks the config for
every zsh-launched build, and `build.rustc-wrapper` in ~/.cargo/config.toml (IDEs, launchd, other
shells). Both named plain sccache. `apply` points both at one trampoline (~/.local/bin/goh-rustc)
that runs the main checkout's lib/goh-rustc, or plain sccache when that is gone, so a moved or
missing goh never fails a build; the zshenv line falls back to sccache when the trampoline itself
is gone. Env changes live only in the zsh files: ~/.bashrc imports them. `check` exits 1 naming
each disagreement.

usage: rustc_wrap.py apply|check     (ZDOTDIR, CARGO_HOME and GOH_RUSTC_BIN name other places)
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # a copy outside any checkout: run as is
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

MARK = "# goh lib/rustc_wrap.py (GOH.RUSTCWRAPWIRE)"
_TABLE = re.compile(r"^\s*\[([^\]]+)\]")
_KEY = re.compile(r"^\s*rustc-wrapper\s*=")


@dataclass(frozen=True)
class Paths:
    zshenv: Path
    cargo_config: Path
    bin: Path
    goh: Path


def main_checkout(root: Path) -> Path:
    """The main checkout of ROOT's repository (a lane worktree goes away); ROOT if none."""
    got = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    common = Path(got.stdout.strip())
    return common.parent if got.returncode == 0 and common.name == ".git" else root


def default_paths() -> Paths:
    home = Path.home()
    here = Path(os.environ.get("GOH_LIVE_ROOT") or Path(__file__).resolve().parent.parent)
    cargo = Path(os.environ.get("CARGO_HOME") or home / ".cargo")
    return Paths(
        zshenv=Path(os.environ.get("ZDOTDIR") or home) / ".zshenv",
        cargo_config=cargo / "config.toml",
        bin=Path(os.environ.get("GOH_RUSTC_BIN") or home / ".local" / "bin" / "goh-rustc"),
        goh=main_checkout(here),
    )


def trampoline(goh: Path) -> str:
    w = shlex.quote(str(goh / "lib" / "goh-rustc"))
    return (
        "#!/bin/sh\n"
        "# cargo's rustc-wrapper, written by goh's lib/rustc_wrap.py (GOH.RUSTCWRAPWIRE).\n"
        f"# goh's wrapper when its checkout has one, else plain sccache: never a failing build.\n"
        f'w={w}\n[ -x "$w" ] && exec "$w" "$@"\nexec "${{GOH_RUSTC_NEXT:-sccache}}" "$@"\n'
    )


def zshenv_line(bin_path: Path) -> str:
    b = shlex.quote(str(bin_path))
    return f"[ -x {b} ] && export RUSTC_WRAPPER={b} || export RUSTC_WRAPPER=sccache  {MARK}"


def _sets_wrapper(line: str) -> bool:
    s = line.lstrip()
    return not s.startswith("#") and "RUSTC_WRAPPER=" in s


def _zshenv_after(lines: list[str], want: str) -> list[str]:
    out, placed = [], False
    for line in lines:
        if _sets_wrapper(line):
            if not placed:
                out.append(want)
                placed = True
            continue
        out.append(line)
    return out if placed else [*out, want]


def _config_after(lines: list[str], want: str) -> list[str]:
    out, table, head, placed = [], None, None, False
    for line in lines:
        if m := _TABLE.match(line):
            table = m.group(1).strip()
            if table == "build":
                head = len(out)
        elif table == "build" and _KEY.match(line):
            if not placed:
                out.append(want)
                placed = True
            continue
        out.append(line)
    if placed:
        return out
    if head is not None:
        return [*out[: head + 1], want, *out[head + 1 :]]
    return [*out, *([""] if out else []), "[build]", want]


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _write(path: Path, text: str, mode: int = 0o644) -> None:
    """PATH (through a symlink to its target) holds TEXT, swapped in whole."""
    real = path.resolve()
    real.parent.mkdir(parents=True, exist_ok=True)
    if real.exists():
        mode = real.stat().st_mode & 0o7777
    tmp = real.with_name(f".{real.name}.goh.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    tmp.chmod(mode)
    os.replace(tmp, real)


def _wanted(p: Paths) -> tuple[str, list[str], list[str]]:
    key = f"rustc-wrapper = {json.dumps(str(p.bin))}   {MARK}"
    zsh = _zshenv_after(_lines(p.zshenv), zshenv_line(p.bin))
    return key, zsh, _config_after(_lines(p.cargo_config), key)


def apply(p: Paths) -> None:
    """The trampoline written, and both settings naming it; every other line kept."""
    _write(p.bin, trampoline(p.goh), 0o755)
    _, zsh, cfg = _wanted(p)
    _write(p.zshenv, "\n".join(zsh) + "\n")
    _write(p.cargo_config, "\n".join(cfg) + "\n")


def check(p: Paths) -> list[str]:
    """Each disagreement with what `apply` writes, said; [] when goh's wrapper is wired."""
    found = []
    if not p.bin.exists() or p.bin.read_text(encoding="utf-8") != trampoline(p.goh):
        found.append(f"{p.bin}: not the trampoline to {p.goh}/lib/goh-rustc")
    _, zsh, cfg = _wanted(p)
    if _lines(p.zshenv) != zsh:
        found.append(f"{p.zshenv}: RUSTC_WRAPPER is not set once, to {p.bin}")
    if _lines(p.cargo_config) != cfg:
        found.append(f"{p.cargo_config}: [build] rustc-wrapper is not {p.bin}")
    return found


if __name__ == "__main__":
    paths = default_paths()
    if sys.argv[1:] == ["apply"]:
        apply(paths)
        print(f"rustc wrapper: {paths.zshenv} and {paths.cargo_config} name {paths.bin}")
        sys.exit(0)
    if sys.argv[1:] == ["check"]:
        problems = check(paths)
        for problem in problems:
            print(f"rustc wrapper: {problem}")
        sys.exit(1 if problems else 0)
    sys.exit("usage: rustc_wrap.py apply|check")
