"""checks/_crates_io.py -- one crates.io lookup per crate NAME per TTL, shared, concurrent (P1b).

Measured 2026-10-05: `dependency currency` cost 108 job-seconds of one media_server push -- one
uncached, serial HTTPS request per dependency, repeated in each of 29 crates, for an arm that
only REPORTS. So: a shared on-disk answer per crate name, and concurrent lookups. What must
hold, each tested here because none of it was before (every existing test runs offline):

* the fatal arm (a pin below the graph) never touches the network or the cache;
* an UNREACHED lookup is never cached -- "could not ask" must not become "asked, current";
* an answer older than the TTL is asked again; a corrupt cache entry is asked again;
* `GOH_CRATES_IO_CACHE=off` asks every time.
"""

from __future__ import annotations

import importlib
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "checks"))


@pytest.fixture
def cio(tmp_path, monkeypatch):
    monkeypatch.setenv("GOH_CRATES_IO_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("GOH_CRATES_IO_TTL_S", raising=False)
    import _crates_io

    mod = importlib.reload(_crates_io)
    calls: list[str] = []
    answers = {"serde": "1.0.300", "toml": "1.1.6"}

    def fake(name):
        calls.append(name)
        return answers.get(name)

    monkeypatch.setattr(mod, "_fetch", fake)
    mod.calls = calls
    mod.answers = answers
    return mod


def test_a_second_lookup_is_served_from_the_cache(cio):
    assert cio.latest_stable("serde") == "1.0.300"
    assert cio.latest_stable("serde") == "1.0.300"
    assert cio.calls == ["serde"]


def test_an_unreached_lookup_is_never_cached(cio):
    assert cio.latest_stable("nosuch") is None
    assert cio.latest_stable("nosuch") is None
    assert cio.calls == ["nosuch", "nosuch"]


def test_an_answer_older_than_the_ttl_is_asked_again(cio, monkeypatch):
    cio.latest_stable("toml")
    monkeypatch.setenv("GOH_CRATES_IO_TTL_S", "0")
    cio.answers["toml"] = "2.0.0"
    assert cio.latest_stable("toml") == "2.0.0"
    assert cio.calls == ["toml", "toml"]


def test_a_corrupt_cache_entry_is_asked_again(cio, tmp_path):
    cio.latest_stable("serde")
    for f in (tmp_path / "cache").iterdir():
        f.write_text("{not json")
    assert cio.latest_stable("serde") == "1.0.300"
    assert cio.calls == ["serde", "serde"]


def test_cache_off_asks_every_time(cio, monkeypatch):
    monkeypatch.setenv("GOH_CRATES_IO_CACHE", "off")
    cio.latest_stable("serde")
    cio.latest_stable("serde")
    assert cio.calls == ["serde", "serde"]


def test_a_hostile_name_cannot_escape_the_cache_dir(cio, tmp_path):
    cio.answers["../../x"] = "9.9.9"
    cio.latest_stable("../../x")
    assert not (tmp_path / "x.json").exists()
    cache = tmp_path / "cache"
    assert not cache.exists() or not list(cache.iterdir())
    assert cio.latest_stable("../../x") == "9.9.9" and cio.calls == ["../../x", "../../x"]


def test_lookups_run_concurrently(cio, monkeypatch):
    """20 names at 0.2 s each is 4 s serial; concurrent it must be well under 1.5 s."""
    live = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def slow(name):
        with lock:
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
        time.sleep(0.2)
        with lock:
            live["now"] -= 1
        return "1.0.0"

    monkeypatch.setattr(cio, "_fetch", slow)
    t0 = time.monotonic()
    got = cio.latest_many([f"c{i}" for i in range(20)])
    assert time.monotonic() - t0 < 1.5
    assert live["peak"] > 1 and len(got) == 20


def test_the_fatal_arm_never_touches_the_network(tmp_path, monkeypatch):
    """A pin below the graph must go red with the network seam BROKEN: the cache can only ever
    change the report-only arm."""
    import check_dep_currency as mod
    import _crates_io

    def boom(name):
        raise AssertionError("the fatal arm reached the network")

    monkeypatch.setattr(_crates_io, "_fetch", boom)
    monkeypatch.setattr(mod, "latest_many", lambda names: {n: None for n in names})
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "x"\nversion = "0.1.0"\n\n[dependencies]\nureq = "2"\n'
    )
    (tmp_path / "Cargo.lock").write_text(
        'version = 3\n\n[[package]]\nname = "ureq"\nversion = "2.12.1"\n\n'
        '[[package]]\nname = "ureq"\nversion = "3.4.2"\n'
    )
    rep = mod.run(tmp_path, offline=False, ratchet=None)
    assert [f.name for f in rep.findings if f.severity == "pinned-below-graph"] == ["ureq"]
