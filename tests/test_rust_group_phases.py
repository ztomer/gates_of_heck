"""The bookkeeping around a rust proven group is a NAMED step, never a silent gap.

(GOH.RUSTPROVENKEY)

game_asset_factory's push gate of 2026-11-15 spent 107, 148 and 151 s between two named steps: the
key before a group, and the re-key, the kept-rewrite check, the dep-info check and the record after
it. None printed a line, so the step table (GOH_TIMINGS) summed to 21 s of a 406 s layer. Each is
now a row under the group's name (`[rust] <group>: proven key`, `[rust] <group>: proven record`,
tier `phase`), so a slow key shows up as one; a skipped group has its key and no record.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import _fake_cargo
import pytest
from _fast_git import fast_init
from _gatesrc_kit import write_gatesrc
from test_rust_gate_scoped_cache import _crate, _gate, _git, _native, gates  # noqa: F401  # fixtures

pytestmark = pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo not installed")


def _rows(path: Path) -> set[str]:
    return {
        row["label"]
        for row in map(json.loads, path.read_text().splitlines())
        if row.get("tier") == "phase"
    }


def test_the_keying_and_recording_of_each_group_are_named_rows_of_the_step_table(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _crate(repo, "c")
    write_gatesrc(repo, "GOH_DEPS_OFFLINE=1\n")
    (repo / ".gitignore").write_text("target/\n")
    subprocess.run(
        ["cargo", "generate-lockfile", "--offline"], cwd=repo / "crates" / "c", check=True,
        capture_output=True, stdin=subprocess.DEVNULL,
    )  # fmt: skip
    fast_init(repo)
    _git(repo, "add", "-A")
    bin_dir, _log = _fake_cargo.install(tmp_path / "fake-cargo")
    monkeypatch.setenv("SCOPED_CACHE_FAKE_CARGO", str(bin_dir))

    first = tmp_path / "first.jsonl"
    r = _gate(repo, "c", GOH_TIMINGS=str(first))
    assert r.returncode == 0, r.stdout + r.stderr
    named = _rows(first)
    for group in ("crate", "repo"):
        assert f"[rust] {group}: proven key" in named, sorted(named)
        assert f"[rust] {group}: proven record" in named, sorted(named)

    again = tmp_path / "again.jsonl"
    assert _gate(repo, "c", GOH_TIMINGS=str(again)).returncode == 0
    named = _rows(again)
    assert "[rust] crate: proven key" in named, sorted(named)
    assert "[rust] crate: proven record" not in named, "a skipped group records nothing"
