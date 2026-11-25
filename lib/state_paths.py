"""lib/state_paths.py -- the roots goh's stored paths start at, and the one codec of their two forms
(GOH.REPOKEY, the data plan's S2b, rule R9 and section 6).

    state_root(env)   GOH_STATE_ROOT, else goh's config.toml `state_root`, else
                      $XDG_STATE_HOME/goh (absolute), else ~/.local/state/goh
    repos_root(env)   GOH_REPOS_ROOT, else `repos_root`, else ~/Projects
    loop_queue(env)   GOH_LOOP_QUEUE, else `loop_queue`, else repos_root/.loop/queue.json
    landq_dir() loopctl() seed_dir() estate_root()   GOH_LANDQ_DIR, GOH_LOOPCTL, GOH_SEED_DIR,
                      GOH_ESTATE_ROOT, else under those
    encode(v) decode(v)        a PATH: absolute <-> `$STATE/..`, `$REPOS/..`, `$HOMEDIR/..`
    repo_key(v) repo(v)        a REPO: absolute <-> `group/name` in the repos root, `~/..` outside
    forms(p) repo_forms(p)     every spelling a row may hold P under, absolute first
    where(col, forms)          (`col IN (?, ?)`, args) to look such a row up by
    column(con, v), repo_column(con, v)     V as a column of CON's file stores it
    put(con, store, rec)       REC as STORE's document stores it in CON's file
    got(store, rec)            REC with STORE's paths absolute on this host; also a list's records

THE CONFIG is $XDG_CONFIG_HOME/goh/config.toml (~/.config): keys state_root, repos_root,
loop_queue, each absolute or `~/..`. crates/goh/src/goh_config.rs reads the same keys, and .loop's
tools/loop_roots.py, with the same meaning. A file or value that says nothing usable is BadConfig,
never a silent default.

TWO FORMS. A token stands only at the START of a path FIELD, named per store in PATHS/REPOS/NAMES
below: text is never touched, whatever it starts with. Readers take both forms. Writers write the
absolute form until the file's `meta.paths` is `token` (goh.db's rekey, the plan's S7, sets it in
the transaction that moves every row); each store's own insert asks, so no caller knows.
Pinned by tests/test_state_paths.py and tests/test_state_paths_stores.py.
"""

from __future__ import annotations

import functools
import os
import re
import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

TOKEN = "token"  # meta.paths once the file's rows are tokens
REPO = re.compile(r"(~/)?[\w.][\w.+-]*(/[\w.+-]+)*")


class BadConfig(RuntimeError):
    """config.toml, or a root's environment override, is there and unusable."""


# -- the roots -----------------------------------------------------------------------------------


def _home(env: Mapping[str, str]) -> Path:
    return Path(env.get("HOME") or os.path.expanduser("~"))


def config_path(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME") or ""
    return (Path(base) if os.path.isabs(base) else _home(env) / ".config") / "goh" / "config.toml"


@functools.lru_cache(maxsize=8)
def _parsed(at: str, _mtime_ns: int, _size: int) -> dict[str, Any]:
    import tomllib  # only where there is a config: a hook's start-up pays nothing for it

    try:
        return tomllib.loads(Path(at).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BadConfig(f"{at}: {e}") from None


def _config(key: str, env: Mapping[str, str]) -> str | None:
    at = config_path(env)
    try:
        st = os.stat(at)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as e:
        raise BadConfig(f"{at}: {e}") from None
    raw = _parsed(str(at), st.st_mtime_ns, st.st_size).get(key)
    if raw is not None and not (isinstance(raw, str) and raw.startswith(("/", "~/"))):
        raise BadConfig(f"{at}: {key} is {raw!r}, not an absolute path")
    return raw


def _named(var: str, key: str, env: Mapping[str, str]) -> Path | None:
    """VAR's value, else the config's KEY, as a path; None when neither says."""
    raw = env.get(var) or ""
    if raw and not raw.startswith(("/", "~/")):
        raise BadConfig(f"{var}={raw!r} is not an absolute path")
    raw = raw or _config(key, env) or ""
    if not raw:
        return None
    return _home(env) / raw[2:] if raw.startswith("~/") else Path(raw.rstrip("/") or "/")


def state_root(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    if at := _named("GOH_STATE_ROOT", "state_root", env):
        return at
    xdg = env.get("XDG_STATE_HOME") or ""
    return (Path(xdg) if os.path.isabs(xdg) else _home(env) / ".local" / "state") / "goh"


def repos_root(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    return _named("GOH_REPOS_ROOT", "repos_root", env) or _home(env) / "Projects"


def loop_queue(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    if raw := env.get("GOH_LOOP_QUEUE"):
        return Path(raw)
    return _named("", "loop_queue", env) or repos_root(env) / ".loop" / "queue.json"


def landq_dir() -> Path:
    return Path(os.environ.get("GOH_LANDQ_DIR") or state_root() / "landq")


def loopctl() -> Path:
    return Path(os.environ.get("GOH_LOOPCTL") or repos_root() / ".loop" / "tools" / "loopctl.py")


def estate_root() -> Path:
    """Where the estate's repos are walked from."""
    return Path(os.environ.get("GOH_ESTATE_ROOT") or repos_root())


def seed_dir() -> Path:
    """The owner's private seed store (docs/config.md)."""
    return Path(os.environ.get("GOH_SEED_DIR") or state_root() / "seeds")


# -- one value -----------------------------------------------------------------------------------


def _roots() -> list[tuple[str, str]]:
    """(root, token), the longest root first; a root that is a link under its real path too."""
    named = ((state_root(), "$STATE"), (repos_root(), "$REPOS"), (_home(os.environ), "$HOMEDIR"))
    both = {(r, t) for p, t in named for r in (str(p), os.path.realpath(p))}
    return sorted(both, key=lambda rt: (-len(rt[0]), rt))


def _swap(text: str, start: str, to: str) -> str | None:
    return to + text[len(start) :] if text == start or text.startswith(start + "/") else None


def decode(v: Any) -> Any:
    """V as an absolute path on this host; anything that starts with no token is V itself."""
    if not isinstance(v, str) or not v.startswith("$"):
        return v
    for token, root in (("$STATE", state_root), ("$REPOS", repos_root)):
        if (p := _swap(v, token, str(root()))) is not None:
            return p
    p = _swap(v, "$HOMEDIR", str(_home(os.environ)))
    return v if p is None else p


def encode(v: Any) -> Any:
    """V in token form; a path under no root is V itself."""
    if not isinstance(v, str) or not v.startswith("/"):
        return v
    return next((p for r, t in _roots() if (p := _swap(v, r, t)) is not None), v)


def repo(v: Any) -> Any:
    """V, a repo's key or path, as its absolute path; what is neither is V itself."""
    full = decode(v)
    if not isinstance(full, str) or not REPO.fullmatch(full):
        return full
    home = _home(os.environ)
    return str(home / full[2:] if full.startswith("~/") else repos_root() / full)


def repo_key(v: Any) -> Any:
    """V's key: `group/name` under the repos root, `~/..` under the home, else V itself."""
    full = decode(v)
    if not isinstance(full, str) or not full.startswith("/"):
        return full
    for root, lead in ((repos_root(), ""), (_home(os.environ), "~/")):
        for r in dict.fromkeys((str(root), os.path.realpath(root))):
            if rest := _swap(full, r, ""):
                return lead + rest[1:]
    return full


def _home_name(v: Any) -> Any:
    """A repo NAME as documents hold it (`group/name`, or an absolute path outside the root)."""
    full = decode(v)
    return repo(full) if isinstance(full, str) and full.startswith("~/") else full


def forms(p: str | Path) -> list[str]:
    full = str(decode(str(p)))
    return list(dict.fromkeys((full, encode(full))))


def repo_forms(p: str | Path) -> list[str]:
    """Absolute, key, token, and the spelling a ship's row had (its directory: `group_name`)."""
    key = str(repo_key(str(p)))
    full = str(repo(key))
    return list(dict.fromkeys((full, key, encode(full), key.replace("/", "_"))))


def where(col: str, values: list[str]) -> tuple[str, tuple[str, ...]]:
    return f"{col} IN ({', '.join('?' * len(values))})", tuple(values)


# -- one row -------------------------------------------------------------------------------------


def tokens(con: sqlite3.Connection) -> bool:
    """Whether CON's file holds tokens (`meta.paths`)."""
    try:
        row = con.execute("SELECT value FROM meta WHERE key = 'paths'").fetchone()
    except sqlite3.OperationalError as e:
        if "no such table" not in str(e):
            raise
        return False  # a file from before `meta`
    return row is not None and row[0] == TOKEN


def column(con: sqlite3.Connection, v: Any) -> Any:
    return encode(v) if tokens(con) else v


def repo_column(con: sqlite3.Connection, v: Any) -> Any:
    return repo_key(v) if tokens(con) else v


# Each store's path fields: `a.b` a key, `*` every value, `[]` every item, `{}` every key. PATHS are
# paths; REPOS are repos (a path today, a key after); NAMES are repos named by key today already.
PATHS: dict[str, tuple[str, ...]] = {
    "lands": ("log", "red_logs.[]", "worktree", "lane", "pre_keep"),
    "requests": ("worktree", "lane", "pre_keep", "log", "slot.worktree"),
    "ships": ("result", "loopctl", "verdicts", "before_clock.log", "steps.[].log",
              "steps.[].timings", "retried.[].log", "consumer_pins.sweep"),
    "ship_pins": ("sweep",),
    "agent_spawns": ("apps.[]", "apps.*.[]"),
    "lane_starts": ("reading.disk.path", "reading.slots.dir"),
    "commit_gated": ("repo",),
    "flakes": ("log.first", "log.second"),
    "reuse_shadow": ("fields.repo",),
    "reuse_audit": ("evidence.fields.repo", "enabled.was.evidence.fields.repo"),
    "pin_push": ("repos.*.sweep",),
    "pin_sweeps": ("sweeps.[].path",),
    "wf_pin.pins": ("sweep",),
    "estate_audit": ("registry", "root"),
    "model-choice-watch": ("candidates_file", "rules"),
    "hook-ledgers.imported": ("{}",),
}  # fmt: skip
REPOS: dict[str, tuple[str, ...]] = {
    "lands": ("repo",),
    "guards": ("repo",),
    "requests": ("repo",),
    "ships": ("repo",),
    "flakes": ("repo",),
    "pin_push": ("repos.{}",),
    "pin_sweeps": ("sweeps.[].repos.[]",),
}
NAMES: dict[str, tuple[str, ...]] = {
    "ships": ("grace_over.repos.{}",),
}


def _map(v: Any, at: list[str], fn: Callable[[Any], Any]) -> Any:
    """V with FN over what AT names in it (a copy where it changes; another shape is V itself)."""
    if not at:
        return fn(v)
    step, rest = at[0], at[1:]
    if step == "[]":
        return [_map(x, rest, fn) for x in v] if isinstance(v, list) else v
    if not isinstance(v, dict):
        return v
    if step == "{}":
        return {fn(k): x for k, x in v.items()}
    if step == "*":
        return {k: _map(x, rest, fn) for k, x in v.items()}
    return {**v, step: _map(v[step], rest, fn)} if step in v else v


def _each(store: str, rec: Any, path: Callable, rep: Callable, name: Callable) -> Any:
    for table, fn in ((PATHS, path), (REPOS, rep), (NAMES, name)):
        for spec in table.get(store, ()):
            rec = _map(rec, spec.split("."), fn)
    return rec


def put(con: sqlite3.Connection, store: str, rec: Any) -> Any:
    return _each(store, rec, encode, repo_key, repo_key) if tokens(con) else rec


def got(store: str, rec: Any) -> Any:
    if isinstance(rec, list):
        return [got(store, r) for r in rec]
    return _each(store, rec, decode, repo, _home_name)
