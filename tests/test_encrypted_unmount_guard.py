"""An unmount or eject of a FileVault APFS volume is refused at the tool boundary
(LOOP.MISTAKEGUARDS, mistake 2).

THE CASE (2026-11-22). `diskutil unmount` of the owner's encrypted external drive LOCKED it, and
only the owner's password opened it again. The Bash guard (`lib/encrypted_unmount.py`) reads the
target with `diskutil info -plist` (and, for a whole-disk verb, `diskutil apfs list -plist`) and
refuses when the volume, or any volume on that disk, has FileVault on. Escape:
`# unmount-ok: <reason>`. `diskutil` here is a FAKE (`GOH_DISKUTIL`) answering from a fixture: no
test runs the real one, and nothing is ever unmounted.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from _hook_inproc import run_hook
from conftest import REPO_ROOT, hermetic_env

GUARD = REPO_ROOT / "hooks" / "claude" / "discard_guard.py"

# disk6: a physical disk whose APFS container disk7 holds the FileVault volume "Vault" (disk7s1)
# and a plain one "Plain" (disk7s2); disk9s1 is an unencrypted HFS volume on its own disk.
INFO = {
    "disk7s1": {"DeviceIdentifier": "disk7s1", "VolumeName": "Vault", "FileVault": True,
                "ParentWholeDisk": "disk7"},
    "/Volumes/Vault": {"DeviceIdentifier": "disk7s1", "VolumeName": "Vault", "FileVault": True,
                       "ParentWholeDisk": "disk7"},
    "disk7s2": {"DeviceIdentifier": "disk7s2", "VolumeName": "Plain", "FileVault": False,
                "ParentWholeDisk": "disk7"},
    "disk6": {"DeviceIdentifier": "disk6", "WholeDisk": True, "ParentWholeDisk": "disk6"},
    "/dev/disk6": {"DeviceIdentifier": "disk6", "WholeDisk": True, "ParentWholeDisk": "disk6"},
    "disk7": {"DeviceIdentifier": "disk7", "WholeDisk": True, "ParentWholeDisk": "disk7"},
    "disk9s1": {"DeviceIdentifier": "disk9s1", "VolumeName": "Ext", "ParentWholeDisk": "disk9"},
    "disk9": {"DeviceIdentifier": "disk9", "WholeDisk": True, "ParentWholeDisk": "disk9"},
}  # fmt: skip
APFS = {
    "Containers": [
        {"ContainerReference": "disk7", "PhysicalStores": [{"DeviceIdentifier": "disk6s2"}],
         "Volumes": [{"DeviceIdentifier": "disk7s1", "Name": "Vault", "FileVault": True},
                     {"DeviceIdentifier": "disk7s2", "Name": "Plain", "FileVault": False}]},
    ]
}  # fmt: skip

FAKE = """\
import json, plistlib, sys
data = json.load(open({fixture!r}))
log = open({log!r}, "a"); log.write(" ".join(sys.argv[1:]) + "\\n"); log.close()
args = sys.argv[1:]
if args[:2] == ["info", "-plist"] and args[2] in data["info"]:
    sys.stdout.buffer.write(plistlib.dumps(data["info"][args[2]]))
elif args == ["apfs", "list", "-plist"]:
    sys.stdout.buffer.write(plistlib.dumps(data["apfs"]))
else:
    sys.exit(1)
"""


@pytest.fixture
def fake(tmp_path: Path) -> Path:
    fixture, log = tmp_path / "disks.json", tmp_path / "calls.log"
    fixture.write_text(json.dumps({"info": INFO, "apfs": APFS}))
    script = tmp_path / "diskutil"
    script.write_text(f"#!{sys.executable}\n" + FAKE.format(fixture=str(fixture), log=str(log)))
    script.chmod(0o755)
    return script


def _decide(command: str, fake: Path) -> str | None:
    r = run_hook(
        GUARD,
        json.dumps({"tool_name": "Bash", "tool_input": {"command": command},
                    "cwd": str(fake.parent)}),
        hermetic_env(drop_git=True, GOH_DISKUTIL=str(fake)),
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    if not r.stdout.strip():
        return None
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny", out
    return out["permissionDecisionReason"]


@pytest.mark.parametrize(
    "command",
    [
        "diskutil unmount /Volumes/Vault",  # the incident
        "diskutil unmount force disk7s1",
        "diskutil umount disk7s1",
        "diskutil unmountDisk disk6",  # the whole disk under the container
        "diskutil unmountDisk force /dev/disk6",
        "diskutil eject disk7",  # the container itself
        "hdiutil detach disk6",
        "sudo umount /Volumes/Vault",
        "diskutil apfs lockVolume disk7s1",
        "sync && diskutil eject disk6",
    ],
)
def test_unmounting_a_filevault_volume_is_refused_and_names_it(fake, command):
    reason = _decide(command, fake)
    assert reason is not None, command
    assert "Vault" in reason and "unmount-ok" in reason, reason


@pytest.mark.parametrize(
    "command",
    [
        "diskutil unmount disk7s2",  # a plain volume beside the vault: the vault stays mounted
        "diskutil unmount disk9s1",
        "diskutil eject disk9",
        "diskutil unmount disk99s1",  # diskutil cannot read it: the unmount would fail too
        "diskutil info disk7s1",
        "diskutil list",
        "echo 'diskutil unmount /Volumes/Vault'",  # text, not code
        "diskutil unmount /Volumes/Vault # unmount-ok: the owner asked, and is at the keyboard",
    ],
)
def test_what_locks_no_filevault_volume_runs(fake, command):
    assert _decide(command, fake) is None, command


def test_the_guard_only_reads_disks(fake):
    """The fake logs every call: the guard asks `info` and `apfs list`, never a verb that acts."""
    _decide("diskutil unmountDisk disk6", fake)
    calls = (fake.parent / "calls.log").read_text().split("\n")
    assert {c.split(" ")[0] for c in calls if c} <= {"info", "apfs"}, calls
    assert all(c.startswith(("info -plist", "apfs list -plist")) for c in calls if c), calls


def test_no_diskutil_on_the_machine_runs_the_command(tmp_path):
    r = run_hook(
        GUARD,
        json.dumps({"tool_name": "Bash", "tool_input": {"command": "diskutil unmount disk7s1"},
                    "cwd": str(tmp_path)}),
        hermetic_env(drop_git=True, GOH_DISKUTIL=str(tmp_path / "absent")),
    )  # fmt: skip
    assert r.returncode == 0 and not r.stdout.strip(), r
