"""_cargo_config.py -- what cargo's config files make of a process cargo would run (GOH.COVRUNNER).

`gates/_cov_run_tests.py` starts the built test binaries itself (GOH.COVPARBINS), so everything
`cargo test` adds from `.cargo/config.toml` has to be added by it: the `[env]` table
(`_cargo_env_table`, GOH.SWEEPREDS130) and the RUNNER a binary is started through
(`_cargo_runner`; ztools' HOME sandbox never ran under the coverage gate, GOH.COVRUNNER).
"""

from __future__ import annotations

import subprocess
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path

Cfgs = Mapping[str, "set[str] | None"]


def _cargo_env_table(cwd: Path, environ: Mapping[str, str], cargo_home: Path) -> dict[str, str]:
    """The variables cargo's `[env]` table sets for a process it runs from `cwd` (GOH.SWEEPREDS130).

    Every `.cargo/config.toml` (else the legacy `.cargo/config`) from `cwd` up to the root, then
    `cargo_home/config.toml`; a deeper file wins a key. An entry is a string or a table
    `{value, force, relative}`: it yields to a variable already in `environ` unless `force`, and a
    `relative` value is joined to the directory holding that `.cargo`. A file that is no TOML is
    refused naming it (ValueError), as cargo refuses it.
    """
    table: dict[str, tuple[object, Path]] = {}
    for f in reversed(_config_files(cwd, cargo_home)):  # the lowest first: a deeper file overwrites
        env = _load(f).get("env", {})
        for key, entry in env.items():
            table[key] = (entry, f.parent.parent)
    out: dict[str, str] = {}
    for key, (entry, base) in table.items():
        spec = entry if isinstance(entry, dict) else {"value": entry}
        if key in environ and not spec.get("force", False):
            continue
        value = str(spec.get("value", ""))
        out[key] = str(base / value) if spec.get("relative", False) else value
    return out


def _config_files(cwd: Path, cargo_home: Path) -> list[Path]:
    """Cargo's config files for a process run from `cwd`, the DEEPEST first: every
    `.cargo/config.toml` (else the legacy `.cargo/config`) from `cwd` up to the root, then
    `cargo_home/config.toml`."""
    files = []
    for d in (cwd.resolve(), *cwd.resolve().parents):
        found = [
            d / ".cargo" / n for n in ("config.toml", "config") if (d / ".cargo" / n).is_file()
        ]
        files.extend(found[:1])
    home = cargo_home / "config.toml"
    if home.is_file() and home.resolve() not in {f.resolve() for f in files}:
        files.append(home)
    return files


def _load(f: Path) -> dict:
    try:
        return tomllib.loads(f.read_text())
    except (tomllib.TOMLDecodeError, OSError) as e:
        raise ValueError(f"{f}: {e}") from e


def _cfg_tokens(text: str) -> list[str]:
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if c.isspace():
            i += 1
        elif c in "(),=":
            out.append(c)
            i += 1
        elif c == '"':
            j = text.index('"', i + 1)
            out.append(text[i : j + 1])
            i = j + 1
        else:
            j = i
            while j < len(text) and (text[j].isalnum() or text[j] == "_"):
                j += 1
            if j == i:
                raise ValueError(f"cfg expression {text!r}: unexpected {c!r}")
            out.append(text[i:j])
            i = j
    return out


def _cfg_matches(key: str, cfgs: Mapping[str, set[str] | None]) -> bool:
    """Whether a `cfg(..)` target key matches the target's cfgs (`rustc --print cfg`): a name
    (`unix`), a `name = "value"` pair, `all(..)`, `any(..)` and `not(..)`, as cargo-platform reads
    them. `cfgs` maps a name to None (a bare cfg) or the set of its values."""
    toks = _cfg_tokens(key)
    pos = 0

    def take(want: str | None = None) -> str:
        nonlocal pos
        if pos >= len(toks) or (want is not None and toks[pos] != want):
            raise ValueError(f"cfg expression {key!r}: expected {want or 'more'}")
        pos += 1
        return toks[pos - 1]

    def expr() -> bool:
        name = take()
        if name in ("all", "any", "not") and pos < len(toks) and toks[pos] == "(":
            take("(")
            vals = []
            while toks[pos] != ")":
                vals.append(expr())
                if toks[pos] == ",":
                    take(",")
            take(")")
            if name == "not":
                if len(vals) != 1:
                    raise ValueError(f"cfg expression {key!r}: not() takes one predicate")
                return not vals[0]
            return all(vals) if name == "all" else any(vals)
        if pos < len(toks) and toks[pos] == "=":
            take("=")
            value = take()
            if not value.startswith('"'):
                raise ValueError(f"cfg expression {key!r}: a value is a string")
            return value[1:-1] in (cfgs.get(name) or set())
        return name in cfgs

    take("cfg")
    take("(")
    got = expr()
    take(")")
    if pos != len(toks):
        raise ValueError(f"cfg expression {key!r}: trailing input")
    return got


def _path_and_args(value: object, base: Path, where: str) -> list[str]:
    """Cargo's PathAndArgs: a string split on whitespace or an array; a program holding a slash is
    relative to `base` (the directory holding `.cargo`, or the cwd for an environment variable),
    a bare name is looked up on PATH as cargo leaves it."""
    if isinstance(value, str):
        parts = value.split()
    elif isinstance(value, list) and all(isinstance(v, str) for v in value):
        parts = list(value)
    else:
        raise ValueError(f"{where}: a runner is a string or an array of strings")
    if not parts:
        raise ValueError(f"{where}: an empty runner")
    if "/" in parts[0]:
        parts[0] = str(base / parts[0])
    return parts


def _cargo_runner(
    cwd: Path,
    environ: Mapping[str, str],
    cargo_home: Path,
    target_info: Callable[[], tuple[str, Cfgs]],
) -> list[str] | None:
    """The runner cargo starts a test binary through (GOH.COVRUNNER; cargo's `target_runner`):
    `target.<triple>.runner` (CARGO_TARGET_<TRIPLE>_RUNNER winning over every file), else the ONE
    `target.'cfg(..)'.runner` whose cfg matches -- two matching are refused, as cargo refuses
    them. A deeper config file wins a key. None: cargo runs the binary itself. `target_info` (the
    triple and its cfgs, from rustc) is asked only when some runner is declared at all: a repo
    with none needs no rustc.
    """
    declared = any(k.startswith("CARGO_TARGET_") and k.endswith("_RUNNER") for k in environ)
    files = list(reversed(_config_files(cwd, Path(cargo_home))))  # the lowest first
    tables = [(f, _load(f).get("target", {})) for f in files]
    declared = declared or any(
        isinstance(t, dict) and "runner" in t for _, tt in tables for t in tt.values()
    )
    if not declared:
        return None
    target, cfgs = target_info()
    var = "CARGO_TARGET_" + target.upper().replace("-", "_").replace(".", "_") + "_RUNNER"
    if environ.get(var, "").strip():
        return _path_and_args(environ[var], cwd.resolve(), var)
    triple: tuple[object, Path, Path] | None = None
    by_cfg: dict[str, tuple[object, Path, Path]] = {}
    for f, targets in tables:
        for key, table in targets.items():
            if not isinstance(table, dict) or "runner" not in table:
                continue
            entry = (table["runner"], f.parent.parent, f)
            if key == target:
                triple = entry
            elif key.startswith("cfg("):
                by_cfg[key] = entry
    if triple is not None:
        return _path_and_args(triple[0], triple[1], f"{triple[2]}: target.{target}.runner")
    hits = sorted(k for k in by_cfg if _cfg_matches(k, cfgs))
    if len(hits) > 1:
        raise ValueError(
            "several matching instances of `target.'cfg(..)'.runner` in configurations: "
            + ", ".join(f"{k} ({by_cfg[k][2]})" for k in hits)
        )
    if hits:
        value, base, f = by_cfg[hits[0]]
        return _path_and_args(value, base, f"{f}: target.'{hits[0]}'.runner")
    return None


def _build_target(cwd: Path, environ: Mapping[str, str], cargo_home: Path) -> str | None:
    """`build.target` (CARGO_BUILD_TARGET, else the deepest config naming one); None: the host."""
    if environ.get("CARGO_BUILD_TARGET"):
        return environ["CARGO_BUILD_TARGET"]
    for f in _config_files(cwd, cargo_home):
        value = _load(f).get("build", {}).get("target")
        if isinstance(value, str):
            return value
    return None


def _rustflags(environ: Mapping[str, str]) -> list[str]:
    """The flags cargo hands rustc when it asks for the target's cfgs: CARGO_ENCODED_RUSTFLAGS
    (unit-separated), else RUSTFLAGS (whitespace-separated)."""
    if environ.get("CARGO_ENCODED_RUSTFLAGS"):
        return [f for f in environ["CARGO_ENCODED_RUSTFLAGS"].split("\x1f") if f]
    return environ.get("RUSTFLAGS", "").split()


def _rustc_target(
    target: str | None, environ: Mapping[str, str]
) -> tuple[str, dict[str, set[str] | None]]:
    """(the target triple, its cfgs) from rustc: `-vV`'s host line, `--print cfg` under the
    environment's rustflags, as cargo asks it (a `--cfg` there is a cfg a runner key can match)."""
    triple = target
    if triple is None:
        out = subprocess.run(
            ["rustc", "-vV"], capture_output=True, text=True, stdin=subprocess.DEVNULL, check=True
        ).stdout
        triple = next(
            ln.split(":", 1)[1].strip() for ln in out.splitlines() if ln.startswith("host:")
        )
    out = subprocess.run(
        ["rustc", "--print", "cfg", "--target", triple, *_rustflags(environ)],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, check=True,
    ).stdout  # fmt: skip
    cfgs: dict[str, set[str] | None] = {}
    for ln in out.splitlines():
        name, eq, value = ln.partition("=")
        if eq:
            vals = cfgs.get(name) or set()
            vals.add(value.strip('"'))
            cfgs[name] = vals
        else:
            cfgs.setdefault(name, None)
    return triple, cfgs
