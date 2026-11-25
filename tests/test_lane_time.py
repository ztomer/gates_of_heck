"""lib/lane_time.py (GOH.TIMEACCOUNT reading 5): a lane's time by kind, from transcript events."""

from __future__ import annotations

import lane_time as lt
import pytest


def ev(sec: int, *content: dict) -> dict:
    return {"timestamp": f"2026-11-25T10:00:{sec:02d}Z", "message": {"content": list(content)}}


def use(i: str, cmd: str, name: str = "Bash") -> dict:
    return {"type": "tool_use", "id": i, "name": name, "input": {"command": cmd}}


def res(i: str) -> dict:
    return {"type": "tool_result", "tool_use_id": i}


def test_calls_are_counted_once_and_the_uncovered_rest_is_model() -> None:
    events = [ev(0), ev(5, use("a", "./tools/pytest.sh tests/x.py")), ev(15, res("a")),
              ev(16, use("b", "sleep 5"), use("c", "x", "Read")), ev(20, res("c")),
              ev(21, res("b")), ev(30, use("d", "ls")), ev(32, res("d")), ev(40)]  # fmt: skip
    got = lt.lane(events)
    assert got == pytest.approx({"tests": 10.0, "waits": 1.0, "files and tools": 4.0,
                                 "other shell": 2.0, "model": 23.0})  # fmt: skip
    assert sum(got.values()) == 40.0


def test_kinds_are_configuration(monkeypatch) -> None:
    monkeypatch.setenv(lt.ENV, "faults=mutat")
    assert [k for k, _ in lt.kinds()] == ["faults"]
    assert lt.kind_of("Bash", "python3 mutate.py", lt.kinds()) == "faults"
    with pytest.raises(ValueError, match="no kind=regex"):
        lt.kinds("x")
    assert lt.lane([]) == {}
