"""The GOH_* configuration the Rust coverage measurement READS, by value (GOH.ONEVERDICTTREE).

THE MISS. The measurement's record was keyed on EVERY GOH_* in the environment (the step's
`env=` hash, gates/verdict_free_keys.txt aside), so any variable a caller happened to export voided
it, whether or not anything the measurement runs ever read it. lander.log on 2026-11-19:
coverage measured 26 times and reused 7; 14 of the misses said "under another GOH_*
configuration", and so did 9 of the last 9 release pushes, each re-measuring for 380 to 980 s what
the land had just measured on the same inputs. The land gate's environment carries its own
bookkeeping (tools/land.sh's profile switch, the lander's inherited lane variables) and the release
push's does not. (This file is in the closure it scans, so it spells no variable it does not mean.)

THE KEY NOW. The measurement is keyed on the GOH_* it can read, each by its value:

  * every GOH_ name the keyed files' BYTES spell -- the Rust build inputs (build.rs, `env!`, a Rust
    test's `std::env::var`), the coverage scripts' CODE files (a data file reads none:
    lib/rust_cov_scripts.py env_readers) and the suite's runner (lib/gate_identity.py `rust`,
    the files its content hashes): code reads a variable by naming it, so a new read
    is in the key the moment its line is, with no list to keep. A name ending in `_` (GOH_SHARE_,
    `compgen -v GOH_COV_`) is a prefix: every variable under it is keyed;
  * the suite's DECLARED inputs (tests/suite_home.py CALLER_INPUTS): the suite drops every other
    GOH_* at its entry, before a test runs (tests/test_suite_ship_env.py pins that), so nothing
    else of the caller's reaches a test or the binary under it. A repo whose suite declares none
    keys every GOH_* it holds, as before (`goh-env-scope all`).

Left out: GOH_DIR (its gates are keyed by content), GOH_COV_* (the `cov` lines key them,
gates/_rust_proven.sh), the proven cache's own settings (GOH_PROVEN*: whether a key exists and how
long it lives, never what is measured -- GOH_PROVEN=0 makes no key, and only the cache's code, not
the measurement's, spells them; tools/gate_profile.sh sets GOH_PROVEN=1 for the land and the push
leaves it unset) and the verdict-free keys (gates/verdict_free_keys.txt). The bare `GOH_`
(`startswith("GOH_")`, prose "GOH_*") is no read: in these files it only DROPS or hashes the
configuration (WHOLESALE, pinned by tests/test_rust_cov_env.py, so a new wholesale read is seen).
The suite's shape (tests/suite_home.py `shape`) is not keyed, by the owner's rule that the suite
is no input of the measurement (GOH.RUSTCOVSKIP): it moves what is exercised, and the plain run
of every reuse runs the whole suite in its own shape, so a red test is still red.

The GOH_* the Rust coverage measurement can READ, by value (GOH.ONEVERDICTTREE): every GOH_ name
the keyed files' bytes spell (`gate_identity.py rust`'s inputs, scripts and runner; a trailing `_`
is a prefix) plus the suite's declared `CALLER_INPUTS` (`tests/suite_home.py`), less GOH_DIR,
GOH_COV_* and the verdict-free keys; a suite that declares none keys every GOH_*.
`WHOLESALE`/`BUILT` pin where a bare or built name appears.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

FORMAT = "goh-env"
_NAME = re.compile(rb"GOH_[A-Z0-9_]*")
SUITE_DECL = ("tests/suite_home.py", "CALLER_INPUTS")
ELSEWHERE = ("GOH_COV_", "GOH_PROVEN")  # keyed by the `cov` lines; the reuse cache's own switches
# The files of goh's measurement whose bytes spell a bare `GOH_`, and what it does there: none
# reads a variable it does not name (tests/test_rust_cov_env.py, a ratchet both ways).
WHOLESALE = {
    "checks/check_raw_audit.py": "DROPS every GOH_* from the audit's foreign-repo environment",
    "crates/goh/src/gatesrc.rs": "prose",
    "crates/goh/src/step_report.rs": "a message naming docs/config.md's keys",
    "crates/goh/src/subject.rs": "prose: the floors' key shape, read from .gatesrc's text",
    "crates/goh/src/subject_tests.rs": "a check of those keys' spelling",
    "gates/_common.sh": "prose",
    "gates/_hash.sh": "goh_config_hash: the other groups' steps' key, never the measurement's",
    "gates/_rust_proven.sh": "prose, and goh_config_hash for the other groups' steps",
    "gates/rust_gate.sh": "prose",
    "lib/bench_lock.sh": "prose",
    "lib/cov_env.py": "this scan's own pattern and prose",
    "lib/gate_identity.py": "prose",
    "lib/gate_slot.py": "prose",
    "lib/suite_cache_guard.sh": "prose",
    "tools/pytest.sh": "prose",
}
# A name built at run time (`"GOH_COV_FLOOR_$LANG"`) is under a prefix keyed whole: GOH_COV_ only.
BUILT = re.compile(rb"GOH_[A-Z0-9_]*(?:\$|\{)")


def free_keys(root: Path) -> frozenset[str]:
    """gates/verdict_free_keys.txt under root: the keys that never change a verdict."""
    text = (root / "gates" / "verdict_free_keys.txt").read_text(encoding="utf-8")
    return frozenset(ln.strip() for ln in text.splitlines() if ln.strip() and ln[0] != "#")


def spelled(blobs: Iterable[bytes]) -> set[str]:
    """Every GOH_ name the bytes spell, the bare prefix aside."""
    return {m.decode() for b in blobs for m in _NAME.findall(b)} - {"GOH_"}


def declared(repo: Path) -> tuple[str, ...] | None:
    """The suite's declared GOH_* inputs (SUITE_DECL, a literal tuple of strings); None: none."""
    rel, name = SUITE_DECL
    try:
        tree = ast.parse((repo / rel).read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                return None
            if isinstance(value, tuple) and all(isinstance(v, str) for v in value):
                return value
    return None


def keyed(names: set[str] | None, env: Mapping[str, str], free: frozenset[str]) -> list[str]:
    """The GOH_* of env the measurement reads (names; None: every one), sorted, without the ones
    keyed elsewhere or never a verdict's."""
    out = set()
    for k in env:
        if not k.startswith("GOH_") or k in free or k == "GOH_DIR" or k.startswith(ELSEWHERE):
            continue
        if names is None or k in names or any(n.endswith("_") and k.startswith(n) for n in names):
            out.add(k)
    return sorted(out)


def lines(blobs: Iterable[bytes], repo: Path, root: Path, env: Mapping[str, str]) -> list[str]:
    """The key's lines: which scope, then `goh-env NAME=VALUE` per variable it reads that is set."""
    suite = declared(repo)
    names = None if suite is None else spelled(blobs) | set(suite)
    scope = "all (no suite declaration)" if names is None else f"declared ({SUITE_DECL[0]})"
    return [f"{FORMAT}-scope {scope}",
            *(f"{FORMAT} {k}={env[k]}" for k in keyed(names, env, free_keys(root)))]  # fmt: skip
