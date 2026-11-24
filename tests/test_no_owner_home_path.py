"""No tracked file names a real account's home directory (GOH.HOMEPATHHARD).

goh is public, and `crates/goh/src/main.rs` `goh_root()` fell back to a literal checkout under the
owner's home: right on one machine, wrong on every other, and a private fact in a public tree. A
path under `/Users/<name>` or `/home/<name>` is refused unless `<name>` is one of the PLACEHOLDERS
a fixture or a comment spells for "some account" (`/Users/me`, `/home/u`, `/Users/...`), or the
macOS shared directory. `goh home-paths` is the consumer gate for the wider shapes (`~/Projects/`,
`$HOME/Projects/`); this one is goh's own, for the shape that names a person.

ALLOWED is a ratchet (close-the-class): the launchd plists `tools/install_launchagent.sh` copies by
BYTES still carry their absolute paths, because launchd expands no variable in them; each entry
names why, and an entry whose file is clean again is stale and red.
"""

from __future__ import annotations

import re
import subprocess

from conftest import REPO_ROOT

# Its whole read: every tracked file (lib/declared_reads.py JUDGE_READS).
JUDGE_READS = ("*",)

SELF = "tests/test_no_owner_home_path.py"  # its calibration cases name accounts, by design
PLACEHOLDERS = {"me", "m", "u", "x", "someone", "...", "Shared", "linuxbrew", "deploy"}
_HOME = re.compile(r"(?<![\w.])/(?:Users|home)/([A-Za-z0-9_.-]+)")
_LAUNCHD = "launchd expands no variable; the installer copies the plist by bytes"
ALLOWED = {
    "launchagents/com.ztomer.goh-actions-sweep.plist": _LAUNCHD,
    "launchagents/com.ztomer.goh-cache-sweep.plist": _LAUNCHD,
    "launchagents/com.ztomer.goh-load-reap.plist": _LAUNCHD,
    "launchagents/com.ztomer.goh-load-sources.plist": _LAUNCHD,
}


def homes(text: str) -> list[tuple[int, str]]:
    """(line, account) for each home path in `text` naming an account no placeholder stands for."""
    return [
        (n, m.group(1))
        for n, line in enumerate(text.splitlines(), 1)
        for m in _HOME.finditer(line)
        if m.group(1) not in PLACEHOLDERS
    ]


def _tracked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
    ).stdout
    return [p for p in out.decode().split("\0") if p]


def test_the_scan_names_an_account_and_passes_a_placeholder() -> None:
    assert homes('PathBuf::from("/Users/alice/Projects/gates_of_heck")') == [(1, "alice")]
    assert homes("x\n/home/bob/.cache") == [(2, "bob")]
    for clean in (
        "/Users/me/x",
        "/home/u/.cache",
        "/Users/.../App.swift",
        "/Users/Shared/wt",
        "file:///Users/x/thing",
        "a/Users/carol/x",
        "$HOME/.cache",
        "/Users/",
    ):
        assert homes(clean) == [], clean


def test_no_tracked_file_names_a_real_home() -> None:
    found, clean_allowed = [], []
    for rel in _tracked():
        if rel == SELF:
            continue
        path = REPO_ROOT / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # a binary or a deleted path: no source line to name
        hits = homes(text)
        if rel in ALLOWED:
            if not hits:
                clean_allowed.append(rel)
            continue
        found += [f"{rel}:{n}: /{acct}" for n, acct in hits]
    assert found == [], "a literal home path in a public tree:\n" + "\n".join(found)
    assert clean_allowed == [], f"stale ALLOWED entries, delete them: {clean_allowed}"
