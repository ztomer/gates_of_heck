"""goh ship's release step checks its own effect (GOH.RELEASEUNPUSHED, lib/ship_pushed.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_ship import _calls, _result, _ship, make_shipped


@pytest.fixture
def shipped(repo: Path, tmp_path: Path) -> Path:
    return make_shipped(repo, tmp_path)


# A kit that commits and tags but never pushes (necrohand's tools/release.sh, ship
# 20261112T081926Z-307f9eff): its exit 0 was believed and the ship failed one step later at verify.
UNPUSHED_KIT = ('git -c user.name=t -c user.email=t@t commit -q --allow-empty -m "release" && '
                'git tag "v$GOH_SHIP_VERSION"')  # fmt: skip


@pytest.mark.parametrize("kit,missing", [
    (UNPUSHED_KIT, ("unpushed", "tag v1.1.0")),
    ('git tag "v$GOH_SHIP_VERSION"', ("tag v1.1.0",)),
])  # fmt: skip
def test_a_release_kit_that_does_not_push_fails_the_release_step_naming_what(
    shipped, tmp_path, kit, missing
):
    r = _ship(shipped, tmp_path, "--wait", "--item", "X.ONE", GOH_SHIP_RELEASE=kit)
    assert r.returncode == 1, r.stdout + r.stderr
    res = _result(tmp_path)
    assert res["failed_step"] == "release", res
    said = "\n".join(res["tail"])
    for word in missing:
        assert word in said, said
    assert "GOH_SHIP_RELEASE" in said, said  # the fix is named
    assert not any(c[0] == "verify" for c in _calls(tmp_path))
