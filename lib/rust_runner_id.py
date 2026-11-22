"""What runs a Rust group keyed on its own inputs, as identity lines (GOH.GATEFLOOR).

    rust_runner_id.py --root GOH... --repo R [--pythonpath PP]

gates/_rust_inputs.sh keys rust_gate.sh's `compile` and `tests` groups with these lines beside the
group's own inputs. Split from lib/gate_identity.py (its formulas) for the line headroom. Pinned by
tests/test_rust_gate_floor.py.
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_identity as gi  # lib/, beside this file: the one formula of every identity line


def rust_runner(roots: list[Path], repo: Path, path: str | None = None) -> list[str]:
    """What runs a Rust group keyed on its own inputs (gates/_rust_inputs.sh, GOH.GATEFLOOR): each
    gates root's rust_gate.sh closure by content (rust_gate_scripts, the coverage measurement's
    instrument), PYTHONPATH as those scripts read it, the tools. Not the whole goh content: a goh
    land's gates are the head export of main, and keyed by all of it every Python-only land re-ran
    clippy and cargo test on unchanged Rust. LookupError on a hole in a closure."""
    lines = [f"rust-runner v1 {gi.content(r, s)}"
             for r in dict.fromkeys(roots) if (s := gi.rust_gate_scripts(r))]  # fmt: skip
    if path is not None:
        lines.append(f"pythonpath {gi.pythonpath(path, repo, gi.rust_gate_scripts)}")
    return [*lines, *gi.tools()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, action="append", required=True)
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--pythonpath", help="PYTHONPATH as the gate has it (absent: unset)")
    a = ap.parse_args(argv)
    try:
        print("\n".join(rust_runner([r.resolve() for r in a.root], a.repo.resolve(), a.pythonpath)))
    except LookupError as e:
        print(f"rust_runner_id: no Rust runner identity: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
