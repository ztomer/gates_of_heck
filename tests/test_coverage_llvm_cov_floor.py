"""The rust coverage mode runs on the cargo-llvm-cov its manifest floor names, and says why when
`show-env` refuses.

GOH.LINUXCOVPATH, 2026-11-22: the remote worker installs cargo-llvm-cov at the manifest's floor
(gates/required_tools.tsv, 0.6.24) and every rust coverage export there failed with an EMPTY log.
The gate called `show-env --sh`, an alias a later release added; 0.6.24 knows only its long name
`--export-prefix` (the Mac's 0.9.2 knows both). The refusal went to `2>/dev/null`, so the log said
nothing. Not a macOS assumption: a flag above the floor, hidden by a swallowed stderr.

The floor is emulated by a `cargo-llvm-cov` on PATH that refuses `--sh` the way 0.6.24 does and
hands everything else to the real one.
"""

import json
import os
import shutil
from pathlib import Path

import pytest
from test_coverage_gate import LIB_FULLY_COVERED, _llvm_cov_available, _mk_crate, run_gate

pytestmark = pytest.mark.skipif(not _llvm_cov_available(), reason="cargo llvm-cov not available")

REFUSAL = "error: invalid option '--sh' for subcommand 'show-env'"


def _floor_shim(tmp_path: Path, refuse_all_show_env: bool = False) -> dict[str, str]:
    real = shutil.which("cargo-llvm-cov")
    assert real, "cargo-llvm-cov on PATH"
    bin_ = tmp_path / "floor-bin"
    bin_.mkdir()
    cond = '"$a" = show-env' if refuse_all_show_env else '"$a" = --sh'
    shim = bin_ / "cargo-llvm-cov"
    shim.write_text(
        "#!/bin/bash\n"
        'for a in "$@"; do\n'
        f'  if [ {cond} ]; then echo "{REFUSAL}" >&2; exit 2; fi\n'
        "done\n"
        f'exec "{real}" "$@"\n'
    )
    shim.chmod(0o755)
    return {"PATH": f"{bin_}:{os.environ['PATH']}"}


def _crate(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "repo"
    crate = _mk_crate(root, LIB_FULLY_COVERED)
    (root / "tools").mkdir()
    (root / "tools" / "floors.json").write_text(json.dumps({"file_floor": 100.0, "exempt": {}}))
    return root, crate


def test_the_rust_export_runs_on_the_floor_cargo_llvm_cov(tmp_path: Path) -> None:
    root, crate = _crate(tmp_path)
    r = run_gate(root, "--lang", "rust", "--floors-json", "tools/floors.json", crate.name,
                 env_extra=_floor_shim(tmp_path))  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_refused_show_env_is_named_in_the_export_log(tmp_path: Path) -> None:
    root, crate = _crate(tmp_path)
    r = run_gate(root, "--lang", "rust", "--floors-json", "tools/floors.json", crate.name,
                 env_extra=_floor_shim(tmp_path, refuse_all_show_env=True))  # fmt: skip
    assert r.returncode != 0
    assert REFUSAL in r.stdout + r.stderr, r.stdout + r.stderr
