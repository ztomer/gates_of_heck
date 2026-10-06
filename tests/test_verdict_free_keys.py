"""One list of the GOH_* keys that never change a verdict, and it cannot drift (2026-10-06).

Two cache keys hash "the GOH_* configuration" (gates/_rust_proven.sh's step, checks/_estate_cache.py's
identity), each with its own exclusions, and the internal keys added since -- GOH_RESOLVED_*, and
GOH_PROVEN_IDENTITY_FOR, which holds a $PWD -- went into both: a key varied with where a gate was
started, so a pre-commit proof and a pre-push lookup could not meet. gates/verdict_free_keys.txt is
the one list; every key docs/config.md calls "Not a setting" must be on it, and every key on it must
be documented.
"""

from __future__ import annotations

import re
import subprocess

from conftest import REPO_ROOT, hermetic_env

LIST = REPO_ROOT / "gates" / "verdict_free_keys.txt"


def _listed() -> set[str]:
    return {
        ln.strip() for ln in LIST.read_text().splitlines() if ln.strip() and not ln.startswith("#")
    }


def test_every_internal_key_is_on_the_list() -> None:
    doc = (REPO_ROOT / "docs" / "config.md").read_text()
    internal = set(re.findall(r"^\| `(GOH_[A-Z_]+)` \|[^\n]*Not a setting", doc, re.M))
    assert internal and internal <= _listed(), sorted(internal - _listed())


def test_every_listed_key_is_documented() -> None:
    doc = (REPO_ROOT / "docs" / "config.md").read_text()
    missing = sorted(k for k in _listed() if f"| `{k}` |" not in doc)
    assert not missing, missing


def test_the_config_hash_ignores_verdict_free_keys(tmp_path) -> None:
    """Same configuration, different bookkeeping: the same hash. A real setting still moves it."""

    def config_hash(**env: str) -> str:
        r = subprocess.run(
            ["bash", "-c", f'. "{REPO_ROOT}/gates/_hash.sh"; goh_config_hash'],
            cwd=tmp_path, capture_output=True, text=True,
            env=hermetic_env(**{"GOH_MAX_LINES": "500", **env}),
        )  # fmt: skip
        assert r.stdout.strip(), r.stderr
        return r.stdout

    base = config_hash()
    assert config_hash(GOH_PROVEN_IDENTITY_FOR="/x|y", GOH_RESOLVED_BIN="/b") == base
    assert config_hash(GOH_MAX_LINES="400") != base, "a real setting must still change the key"
