"""An unmount or eject of an ENCRYPTED (FileVault) APFS volume is refused before it runs
(LOOP.MISTAKEGUARDS, mistake 2).

THE CASE (2026-11-22). An agent ran `diskutil unmount` on the owner's external drive. The volume
was FileVault-encrypted: unmounting it LOCKED it, and only the owner's password opened it again.
No agent holds that password, so the step cannot be undone by the one who took it.

THE RULE, in the CODE of a Bash command (`lib/shell_segments.py`):
  * `diskutil unmount|umount|unmountDisk|umountDisk|eject` and `diskutil apfs lockVolume`,
    `hdiutil detach|eject`, and `umount`, each with its target (the last operand);
  * the target is read with `diskutil info -plist`: a volume with `FileVault` true is refused;
  * a WHOLE-disk verb (unmountDisk, eject, hdiutil detach) also reads `diskutil apfs list -plist`,
    and refuses when any volume of a container on that disk (or that IS that container) has
    `FileVault` true.
A target diskutil cannot read runs: the unmount would fail on it too. `diskutil` is the program
named by `GOH_DISKUTIL` when set (the tests' fake; nothing here ever unmounts). The escape is
`# unmount-ok: <reason>`.
"""

from __future__ import annotations

import os
import plistlib
import re
import subprocess

import shell_segments

ESCAPE = re.compile(r"#\s*unmount-ok:\s*\S")
VOLUME_VERBS = {"unmount", "umount"}
DISK_VERBS = {"unmountdisk", "umountdisk", "eject"}
KEYWORDS = {"force", "kill"}  # `diskutil unmount force <t>`, `diskutil unmountDisk force <t>`
WHOLE = re.compile(r"^(?:/dev/)?r?(disk\d+)")
WAY_OUT = (
    "Unmounting a FileVault volume LOCKS it, and only the owner's password opens it again (an "
    "unmount on 2026-11-22 locked the owner's drive). Leave it mounted: work on files inside it, "
    "or ask the owner to eject it. To unmount it anyway, add `# unmount-ok: <reason>`."
)


def _diskutil(*args: str) -> dict | None:
    prog = os.environ.get("GOH_DISKUTIL") or "diskutil"
    try:
        r = subprocess.run(
            [prog, *args], capture_output=True, timeout=15, check=False, stdin=subprocess.DEVNULL
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    try:
        data = plistlib.loads(r.stdout)
    except Exception:  # noqa: BLE001 -- unreadable output: the rule cannot judge, so it runs
        return None
    return data if isinstance(data, dict) else None


def _whole(ident: str) -> str:
    m = WHOLE.match(ident or "")
    return m.group(1) if m else ""


def locked_by(target: str, whole_disk: bool) -> list[str]:
    """The FileVault volumes this unmount would lock (names or identifiers), or []."""
    info = _diskutil("info", "-plist", target)
    if info is None:
        return []
    ident = str(info.get("DeviceIdentifier") or "")
    if info.get("FileVault") is True:
        return [str(info.get("VolumeName") or ident or target)]
    if not whole_disk:
        return []
    disk = _whole(str(info.get("ParentWholeDisk") or ident))
    apfs = _diskutil("apfs", "list", "-plist") or {}
    hits = []
    for c in apfs.get("Containers") or []:
        stores = [_whole(str(s.get("DeviceIdentifier", ""))) for s in c.get("PhysicalStores") or []]
        if disk and (disk == _whole(str(c.get("ContainerReference", ""))) or disk in stores):
            hits += [
                str(v.get("Name") or v.get("DeviceIdentifier"))
                for v in c.get("Volumes") or []
                if v.get("FileVault") is True
            ]
    return hits


def _target(prog: str, args: list[str]) -> tuple[str, bool] | None:
    words = [a for a in args if not a.startswith("-")]
    if prog == "diskutil" and words:
        verb = words[0].lower()
        if verb == "apfs" and len(words) > 2 and words[1].lower() == "lockvolume":
            return words[2], False
        rest = [w for w in words[1:] if w.lower() not in KEYWORDS]
        if rest and verb in VOLUME_VERBS | DISK_VERBS:
            return rest[-1], verb in DISK_VERBS
    if prog == "hdiutil" and len(words) > 1 and words[0] in ("detach", "eject"):
        return words[-1], True
    if prog == "umount" and words:
        return words[-1], False
    return None


def judge(command: str) -> str | None:
    """The refusal for an unmount/eject that would lock a FileVault volume, or None."""
    if ESCAPE.search(command):
        return None
    try:
        segments = shell_segments.segments(command)
    except ValueError:
        return None
    for seg in segments:
        prog, args = shell_segments.program(seg)
        found = _target(prog, args)
        if found is None:
            continue
        names = locked_by(*found)
        if names:
            return (
                f"`{' '.join(seg)}` would lock the encrypted (FileVault) volume "
                f"{', '.join(names)}. {WAY_OUT}"
            )
    return None


__all__ = ["ESCAPE", "judge", "locked_by"]
