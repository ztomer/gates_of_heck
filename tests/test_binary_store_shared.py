"""One build of goh per SOURCE, machine-wide (G2e, 2026-10-25).

The stamp-keyed store (`bin/by-stamp/`, gates/_goh_bin.sh) lived inside each gates root, and every
root a gate makes starts empty: the push worktree, its C4 export, path_invariance.py's clone, each
clean checkout a test takes. One gate run of 3a63301 built the same source's release binary five
times. The store every root shares (`GOH_BIN_STORE`, default `~/.cache/goh/by-stamp`) is read
before a build and written by every adoption, so a second root of the same source builds nothing.
"""

from __future__ import annotations

from pathlib import Path

from test_binary_source_identity import (
    _fake_builder,
    _head_tree,
    _no_store,
    _structural,
    _version,
    private_store,
)
from test_gate_environment import _clean_checkout_of_todays_gates


def test_a_second_root_of_the_same_source_takes_the_first_ones_build(tmp_path: Path) -> None:
    first = _clean_checkout_of_todays_gates(tmp_path / "one")
    second = _clean_checkout_of_todays_gates(tmp_path / "two")
    for root in (first, second):
        _no_store(root)
        (root / "bin").mkdir(exist_ok=True)  # gitignored: a clean checkout has none
    shared = tmp_path / "store"
    stamp, version = _head_tree(first), _version(first)
    assert _head_tree(second) == stamp, "two checkouts of one commit are one source"

    built = _fake_builder(first, version, stamp)
    got = _structural(first, tmp_path / "one", GOH_BIN_STORE=str(shared))
    assert "rebuilt native ran" in got.stdout, got.stdout + got.stderr
    assert built.read_text().split() == ["--if-stale"], built.read_text()
    assert any(shared.glob("*/goh")), "the build never reached the shared store"

    again = _fake_builder(second, version, stamp)
    got = _structural(second, tmp_path / "two", GOH_BIN_STORE=str(shared))
    assert "rebuilt native ran" in got.stdout, got.stdout + got.stderr
    assert not again.exists(), f"the second root rebuilt one source: {again.read_text()}"
    # bin/goh still follows HEAD (C3) in the root that took the shared build.
    assert "rebuilt native ran" in (second / "bin" / "goh").read_text()


def test_a_shared_binary_of_another_source_is_never_taken(tmp_path: Path) -> None:
    """The stamp is what is trusted, read from the binary itself: a stale one under the right key
    is rebuilt over, exactly as a stale bin/goh is."""
    checkout = _clean_checkout_of_todays_gates(tmp_path)
    _no_store(checkout)
    (checkout / "bin").mkdir(exist_ok=True)
    stamp, version = _head_tree(checkout), _version(checkout)
    import hashlib

    planted = private_store(checkout) / hashlib.sha1(stamp.encode()).hexdigest() / "goh"
    planted.parent.mkdir(parents=True)
    planted.write_text(
        "#!/bin/bash\n"
        f'[ "$1" = --version ] && {{ echo "goh {version}"; exit 0; }}\n'
        '[ "$1" = source-tree ] && { echo "0000 0000 0000 0000"; exit 0; }\n'
        'echo "the stale shared binary ran"\n'
    )
    planted.chmod(0o755)
    calls = _fake_builder(checkout, version, stamp)
    got = _structural(checkout, tmp_path)
    assert "the stale shared binary ran" not in got.stdout, got.stdout
    assert "rebuilt native ran" in got.stdout, got.stdout + got.stderr
    assert calls.read_text().split() == ["--if-stale"]
