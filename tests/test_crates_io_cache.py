"""`goh deps` -- one crates.io lookup per crate NAME per TTL, shared, concurrent (P1b).

Measured 2026-10-05: `dependency currency` cost 108 job-seconds of one media_server push -- one
uncached, serial HTTPS request per dependency, repeated in each of 29 crates, for an arm that
only REPORTS. So: a shared on-disk answer per crate name, and concurrent lookups. What must
hold, each tested here because none of it was before (every existing test runs offline):

* the fatal arm (a pin below the graph) never needs the network or the cache;
* an UNREACHED lookup is never cached -- "could not ask" must not become "asked, current";
* an answer older than the TTL is asked again; a corrupt cache entry is asked again;
* `GOH_CRATES_IO_CACHE=off` asks every time.

The network is `curl` (crates/goh/src/deps/cratesio.rs), so the seam is a fake `curl` first on
`PATH`: it logs each name it is asked and answers from a file. (These ran in-process against the
Python `_crates_io` until Phase N3 retired it.)
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

FAKE_CURL = """#!/bin/sh
for last; do :; done
name="${last##*/}"
echo "$name" >> "$CALLS"
sleep "${DELAY:-0}"
v=$(grep -F "$name=" "$ANSWERS" | head -1 | cut -d= -f2)
[ -n "$v" ] || exit 22
printf '{"crate": {"max_stable_version": "%s"}}' "$v"
"""


class Seam:
    def __init__(self, tmp: Path, goh: Path) -> None:
        self.tmp, self.goh = tmp, goh
        self.bin = tmp / "bin"
        self.bin.mkdir()
        (self.bin / "curl").write_text(FAKE_CURL)
        (self.bin / "curl").chmod(0o755)
        self.calls, self.answers, self.cache = tmp / "calls", tmp / "answers", tmp / "cache"
        self.calls.write_text("")
        self.env = {
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "HOME": str(tmp),
            "CALLS": str(self.calls),
            "ANSWERS": str(self.answers),
            "GOH_CRATES_IO_CACHE": str(self.cache),
        }
        self.answer(serde="1.0.300", toml="1.1.6")

    def answer(self, **latest: str) -> None:
        self.answers.write_text("".join(f"{k}={v}\n" for k, v in latest.items()))

    def repo(self, *names: str) -> Path:
        root = self.tmp / "repo"
        root.mkdir(exist_ok=True)
        deps = "".join(f'"{n}" = "1"\n' for n in names)
        lock = "".join(f'\n[[package]]\nname = "{n}"\nversion = "1.0.0"\n' for n in names)
        (root / "Cargo.toml").write_text(
            f'[package]\nname = "x"\nversion = "0.1.0"\n\n[dependencies]\n{deps}'
        )
        (root / "Cargo.lock").write_text("version = 3\n" + lock)
        return root

    def deps(self, root: Path, **env: str) -> dict:
        r = subprocess.run(
            [str(self.goh), "deps", "--root", str(root), "--json"],
            capture_output=True,
            text=True,
            env={**self.env, **env},
            timeout=60,
        )
        assert r.returncode in (0, 1), r.stdout + r.stderr
        return json.loads(r.stdout)

    def asked(self) -> list[str]:
        return self.calls.read_text().split()


@pytest.fixture
def seam(tmp_path: Path, goh: Path) -> Seam:
    return Seam(tmp_path, goh)


def test_a_second_lookup_is_served_from_the_cache(seam: Seam) -> None:
    root = seam.repo("serde")
    for _ in range(2):
        doc = seam.deps(root)
        assert "1.0.300" in json.dumps(doc["minor_behind"] + doc["major_behind"]), doc
    assert seam.asked() == ["serde"]


def test_an_unreached_lookup_is_never_cached(seam: Seam) -> None:
    root = seam.repo("nosuch")
    for _ in range(2):
        doc = seam.deps(root)
        assert any("unreachable" in n for n in doc["notes"]), doc
    assert seam.asked() == ["nosuch", "nosuch"]


def test_an_answer_older_than_the_ttl_is_asked_again(seam: Seam) -> None:
    root = seam.repo("toml")
    seam.deps(root)
    seam.answer(toml="2.0.0")
    doc = seam.deps(root, GOH_CRATES_IO_TTL_S="0")
    assert "2.0.0" in json.dumps(doc["major_behind"]), doc
    assert seam.asked() == ["toml", "toml"]


def test_a_corrupt_cache_entry_is_asked_again(seam: Seam) -> None:
    root = seam.repo("serde")
    seam.deps(root)
    for f in seam.cache.iterdir():
        f.write_text("{not json")
    seam.deps(root)
    assert seam.asked() == ["serde", "serde"]


def test_cache_off_asks_every_time(seam: Seam) -> None:
    root = seam.repo("serde")
    seam.deps(root, GOH_CRATES_IO_CACHE="off")
    seam.deps(root, GOH_CRATES_IO_CACHE="off")
    assert seam.asked() == ["serde", "serde"]


def test_a_hostile_name_cannot_escape_the_cache_dir(seam: Seam) -> None:
    """A manifest key is any TOML string; one that walks out of the cache directory is asked, never
    written to disk under that name."""
    seam.answer(**{"x": "9.9.9"})
    root = seam.repo("../../x")
    seam.deps(root)
    seam.deps(root)
    assert not (seam.tmp / "x.json").exists()
    assert not seam.cache.exists() or not list(seam.cache.iterdir())
    assert seam.asked() == ["x", "x"]


def test_lookups_run_concurrently(seam: Seam) -> None:
    """20 names at 0.3 s each add 19 x 0.3 s to one lookup if they run one after another, and
    little if they overlap. One lookup is timed first, in the same test, so the box's load is in
    both; a fixed 3 s was a bound on the box's spawn speed as much as on the overlap."""
    names = [f"c{i}" for i in range(20)]
    seam.answer(solo="1.0.0", **{n: "1.0.0" for n in names})
    t0 = time.monotonic()
    seam.deps(seam.repo("solo"), DELAY="0.3")
    alone = time.monotonic() - t0
    root = seam.repo(*names)
    t0 = time.monotonic()
    seam.deps(root, DELAY="0.3")
    added = time.monotonic() - t0 - alone
    assert added < 19 * 0.3 / 2, f"19 more lookups added {added:.1f} s: they ran in series"
    assert sorted(seam.asked()) == sorted(["solo", *names])


def test_the_fatal_arm_never_needs_the_network(seam: Seam) -> None:
    """A pin below the graph must go red with the network BROKEN: the cache can only ever change
    the report-only arm."""
    root = seam.tmp / "pin"
    root.mkdir()
    (root / "Cargo.toml").write_text(
        '[package]\nname = "x"\nversion = "0.1.0"\n\n[dependencies]\nureq = "2"\n'
    )
    (root / "Cargo.lock").write_text(
        'version = 3\n\n[[package]]\nname = "ureq"\nversion = "2.12.1"\n\n'
        '[[package]]\nname = "ureq"\nversion = "3.4.2"\n'
    )
    seam.answer()  # every lookup fails
    doc = seam.deps(root)
    assert [f["name"] for f in doc["fatal"] if f["severity"] == "pinned-below-graph"] == ["ureq"]
