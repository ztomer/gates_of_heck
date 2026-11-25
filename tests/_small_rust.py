"""What the suite builds with rustc, small (GOH.FSEVQUIET): std as the toolchain's own dylib, found
by its rpath, no debug info and no symbols. A test binary is 0.4 MB where it was 1.2, with 0.2 MB
of objects beside it; the coverage map and the profile are sections of their own, and stay."""

from __future__ import annotations

from pathlib import Path

SHARED_STD = ("-C", "prefer-dynamic", "-C", "rpath")
SMALL = (*SHARED_STD, "-C", "debuginfo=0", "-C", "strip=symbols")
# cargo's, for a fixture's own .cargo/config.toml.
CONFIG = f"[build]\nrustflags = [{', '.join(f'"{flag}"' for flag in SMALL)}]\n"


def small_builds(root: Path) -> None:
    """Every cargo build under ROOT is small."""
    (root / ".cargo").mkdir(parents=True, exist_ok=True)
    (root / ".cargo" / "config.toml").write_text(CONFIG)
