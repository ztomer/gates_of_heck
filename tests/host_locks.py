"""The machine-wide locks, kept out of every test's reach (registered by conftest.py's import)."""

from __future__ import annotations

import pytest

# The machine-wide locks, by the variable that moves each one. A test that reached the REAL
# lock waited on whatever the host was doing: two bench_lock tests ran `quiet.sh`, which took the
# real GPU lock while another session's model sweep held it, and failed after 60 s at every load
# (2026-10-10). Only the GPU lock's own tests had redirected it.
HOST_LOCKS = (
    "ZTOOLS_GPU_LOCK_DIR",
    "GOH_BENCH_LOCK_DIR",
    "GOH_BENCH_DESKTOP_LOCK_DIR",
    "GOH_GATE_SLOT_DIR",
)


@pytest.fixture(autouse=True)
def no_test_reaches_a_host_lock(tmp_path_factory, monkeypatch):
    """Every test, and every process it starts, gets private copies of the machine's locks."""
    root = tmp_path_factory.mktemp("host-locks")
    for name in HOST_LOCKS:
        monkeypatch.setenv(name, str(root / name.lower()))
    monkeypatch.delenv("ZTOOLS_GPU_LOCK_OWNER", raising=False)
    # A slot the suite's own push holds is not one a fixture gate is inside (lib/gate_slot.py).
    monkeypatch.delenv("GOH_GATE_SLOT", raising=False)
