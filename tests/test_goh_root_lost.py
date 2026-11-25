"""A goh binary outside any checkout, with no GOH_DIR, says so and exits 2 (GOH.HOMEPATHHARD): it
used to guess a literal checkout under the owner's home."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def test_a_binary_outside_a_checkout_with_no_goh_dir_names_both(goh: Path, tmp_path: Path) -> None:
    lost = tmp_path / "bin" / "goh"
    lost.parent.mkdir()
    shutil.copy2(goh, lost)
    env = {k: v for k, v in os.environ.items() if k not in ("GOH_DIR", "GOH")}
    out = subprocess.run(
        [str(lost), "--version"],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 2, out
    assert "GOH_DIR" in out.stdout + out.stderr and str(lost) in out.stdout + out.stderr, out
