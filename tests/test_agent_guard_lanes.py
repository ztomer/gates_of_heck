"""A write spawn starts only while the machine has room for another lane (GOH.ADAPTLANES).

Lane counts were hand-set constants and every session decided alone, so lanes started into a box
whose two gate slots were already held, at a load of 40-130 on 16 cores. agent_guard asks
lib/capacity.py's reading -- the one `goh capacity --json` prints -- on every write spawn of every
session (GOH_LANE_ADMIT: `refuse`, the default since GOH.ADAPTLANES2, defers it naming the reading,
`warn` says so and admits, `off` asks nothing), never on a read-only one, admits an `Owner-asked:`
spawn only while there is room (with none it is deferred; above the hard ceiling, refused) and, with
a warning, any spawn whose reading fails, and records every judged spawn in
goh.db's `lane_starts` for the weekly review (GOH.STATEDBS4).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from _goh_db_fixture import ledger
from _hook_inproc import run_hook
from conftest import hermetic_env
from test_capacity import CORES, Planted
from test_claude_agent_guard import GOOD, GUARD, _allowed, _reason, _spawn

READ_ONLY = {"description": "look", "subagent_type": "Explore", "prompt": "Where is foo defined?"}


def _run(event: Any, tmp: Path, env: dict[str, str]) -> dict[str, Any] | None:
    r = run_hook(
        GUARD, json.dumps(event),
        hermetic_env(XDG_CACHE_HOME=str(tmp / "cache"), XDG_STATE_HOME=str(tmp / "state"),
                     GOH_DB=str(tmp / "goh.db"), **env),
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else None


def _lanes(tmp: Path) -> list[dict[str, Any]]:
    return ledger(tmp / "goh.db", "lane_starts")


def _judge(tmp: Path, mode: str, *, room: bool, ti: dict[str, Any] = GOOD, **extra: str):
    p = Planted(tmp, waiting=0 if room else 2, idle=80 if room else 10)
    if room:
        p.env["GOH_LOADAVG_READING"] = "0 0 0"
    try:
        return _run(_spawn(ti), tmp, {**p.env, "GOH_LANE_ADMIT": mode, **extra})
    finally:
        p.close()


def test_refuse_mode_refuses_a_write_spawn_with_no_room_naming_the_reading(tmp_path) -> None:
    reason = _reason(_judge(tmp_path, "refuse", room=False))
    for needle in ("no room for another write lane", "2 gates waiting for a slot",
                   "% idle", "goh capacity", "GOH_LANE_ADMIT"):  # fmt: skip
        assert needle in reason, (needle, reason)
    (line,) = _lanes(tmp_path)
    assert (line["verdict"], line["mode"], line["subagent_type"]) == ("refused", "refuse",
                                                                      "repo-worker")  # fmt: skip
    assert line["reading"]["waiting"]["long"] == 2 and line["why"], line


def test_refuse_mode_admits_a_write_spawn_when_there_is_room(tmp_path) -> None:
    out = _judge(tmp_path, "refuse", room=True)
    _allowed(out)
    assert "no room" not in (out or {}).get("systemMessage", "")
    (line,) = _lanes(tmp_path)
    assert line["verdict"] == "room" and line["why"] == [], line


def test_warn_mode_admits_with_no_room_and_says_so(tmp_path) -> None:
    out = _judge(tmp_path, "warn", room=False)
    _allowed(out)
    assert "no room for another write lane" in out["systemMessage"], out
    assert _lanes(tmp_path)[0]["verdict"] == "warned"


def test_a_reading_that_fails_admits_with_a_warning_even_in_refuse_mode(tmp_path) -> None:
    out = _judge(tmp_path, "refuse", room=False, GOH_LOADAVG_READING="garbage")
    _allowed(out)
    assert "could not be read" in out["systemMessage"] and "admitted" in out["systemMessage"]
    assert _lanes(tmp_path)[0]["verdict"] == "unread"


def test_a_read_only_spawn_is_never_judged_or_refused(tmp_path) -> None:
    out = _judge(tmp_path, "refuse", room=False, ti=READ_ONLY)
    assert out is None or "permissionDecision" not in out.get("hookSpecificOutput", {}), out
    assert _lanes(tmp_path) == []


def test_off_asks_nothing(tmp_path) -> None:
    _allowed(_judge(tmp_path, "off", room=False))
    assert _lanes(tmp_path) == []


def test_an_unknown_mode_is_warn_and_says_so(tmp_path) -> None:
    out = _judge(tmp_path, "refuze", room=False)
    _allowed(out)
    assert "GOH_LANE_ADMIT='refuze'" in out["systemMessage"], out
    assert _lanes(tmp_path)[0]["verdict"] == "warned"


OWNER = dict(GOOD, prompt=f'{GOOD["prompt"]}\nOwner-asked: "Yes, make it enforce" (2026-11-17)')


def test_the_default_refuses_a_plain_write_spawn_with_no_room_as_deferred(tmp_path) -> None:
    reason = _reason(_judge(tmp_path, "", room=False))  # GOH_LANE_ADMIT unset: the default
    for needle in ("no room for another write lane", "2 gates waiting for a slot", "% idle",
                   "deferred", "re-dispatch", "Owner-asked:"):  # fmt: skip
        assert needle in reason, (needle, reason)
    (line,) = _lanes(tmp_path)
    assert (line["verdict"], line["mode"]) == ("refused", "refuse"), line


def test_an_owner_asked_spawn_with_no_room_is_deferred_naming_the_numbers(tmp_path) -> None:
    # GOH.OWNERASKCAP (mistakes.json M5, 2026-11-22): the owner asking for the work is not the
    # owner asking for it to run at once -- an Owner-asked spawn past the refusal let gate-heavy
    # lanes start and load reached 130 on 16 cores.
    reason = _reason(_judge(tmp_path, "", room=False, ti=OWNER))
    for needle in ("Deferred", "owner-asked", "no room for another write lane",
                   "2 gates waiting for a slot", "% idle", "goh capacity"):  # fmt: skip
        assert needle in reason, (needle, reason)
    (line,) = _lanes(tmp_path)
    assert line["verdict"] == "owner-deferred" and line["why"], line
    assert line["owner"] == '"Yes, make it enforce" (2026-11-17)', line
    # the words must open a line of their own: a brief that merely quotes the marker is plain
    quoted = dict(GOOD, prompt=f"{GOOD['prompt']}\nThe marker is `Owner-asked: <words>`.")
    (tmp_path / "q").mkdir()
    _reason(_judge(tmp_path / "q", "", room=False, ti=quoted))
    assert _lanes(tmp_path / "q")[0]["verdict"] == "refused"


def test_an_owner_asked_spawn_in_warn_mode_starts_below_the_hard_ceiling(tmp_path) -> None:
    out = _judge(tmp_path, "warn", room=False, ti=OWNER)
    _allowed(out)
    assert "owner-asked" in out["systemMessage"], out
    assert _lanes(tmp_path)[0]["verdict"] == "owner"


def test_an_owner_asked_spawn_above_the_hard_ceiling_is_refused_in_any_mode(tmp_path) -> None:
    cores = CORES
    for mode, load, idle in (("", cores * 4 + 1, 10), ("warn", 0, 3)):
        tmp = tmp_path / (mode or "default")
        tmp.mkdir()
        p = Planted(tmp, waiting=2, idle=idle)
        p.env["GOH_LOADAVG_READING"] = f"{load} {load} {load}"
        try:
            out = _run(_spawn(OWNER), tmp, {**p.env, "GOH_LANE_ADMIT": mode})
        finally:
            p.close()
        reason = _reason(out)
        assert "Refused" in reason and "hard ceiling" in reason, reason
        want = f"load1 {load:g} over {cores * 4}" if load else "3% idle, under 5%"
        assert want in reason, (want, reason)
        assert _lanes(tmp)[0]["verdict"] == "owner-refused"


def test_an_unreadable_reading_admits_under_the_default(tmp_path) -> None:
    out = _judge(tmp_path, "", room=False, GOH_LOADAVG_READING="garbage")
    _allowed(out)
    assert "could not be read" in out["systemMessage"], out
    assert _lanes(tmp_path)[0]["verdict"] == "unread"
