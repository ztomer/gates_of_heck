"""tools/profiling holds only what any repo can use: the trace ranker, never one app's harness.

WHY. The CadGoose soak/trail/profile scripts were canonized here while CadGoose and CadGoose2
carried byte-identical copies. CadGoose2 went away and the scripts stayed: one repo's harness in
the house suite, where a fix to it (the trail test waited on a socket the app binds only with MCP
on, and skipped restoring the desktop when a test failed) had to be made for a single consumer.
They moved back to CadGoose (v0.40.0). This pins the directory to its generic contents, so a
consumer script returning here is a decision someone makes in this file, not a drift.

Red proof: with the eleven CadGoose scripts and `_target_root.sh` still here, both tests failed.
"""

from __future__ import annotations

from conftest import REPO_ROOT

PROFILING = REPO_ROOT / "tools" / "profiling"

# The generic Time Profiler ranker (`analyze_trace.sh` + `xctrace_profile.py`, pinned by
# test_xctrace_profile.py), its README and the .gitignore for traces. Anything else here is one
# repo's script.
GENERIC = {".gitignore", "README.md", "analyze_trace.sh", "xctrace_profile.py"}


def test_the_directory_holds_only_the_generic_ranker():
    present = {p.name for p in PROFILING.iterdir() if p.name != "__pycache__"}
    assert present - GENERIC == set(), (
        f"one repo's scripts in the house suite: {sorted(present - GENERIC)}"
    )


def test_no_file_names_one_consumer_app():
    for path in PROFILING.iterdir():
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert "CadGoose" not in text and "cadgoose" not in text, f"{path.name} names CadGoose"
