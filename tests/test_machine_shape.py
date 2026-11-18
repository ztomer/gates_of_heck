"""A hermetic fixture child sees ONE machine shape on every box (GOH.CIRED0136).

The 3-core GitHub runner gave a fixture gate 1 gate slot and a worker share of 3 where the owner's
16-core box gives 2 and 16, so three tests were red on CI only (v0.136.0): suite_home.MACHINE_SHAPE
pins both in hermetic_env. The last line of the test is the instrument's calibration: without the
pin the box's own core count still decides, so the test could see a box leak.
"""

from __future__ import annotations

import os
import sys

import pytest
from conftest import REPO_ROOT, hermetic_env

sys.path.insert(0, str(REPO_ROOT / "lib"))
import admission
import worker_share


@pytest.mark.parametrize("box_cores", [3, 16, 64])
def test_a_hermetic_child_sees_one_machine_shape_on_every_box(monkeypatch, box_cores):
    monkeypatch.setattr(os, "cpu_count", lambda: box_cores)
    monkeypatch.setattr(os, "sysconf", lambda _name: box_cores)
    child = hermetic_env(drop_git=True)
    slots, cores = admission.configured(child)[1], worker_share.cores(child)
    unpinned = admission.configured({**child, "GOH_GATE_SLOTS": ""})[1]
    assert (slots, cores) == (2, 16)
    assert unpinned == max(1, box_cores // 8)
