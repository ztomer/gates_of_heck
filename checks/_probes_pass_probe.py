"""check_probes_pass.py --probe: the gate driven through fixture gates, so it is shown to go red.

Split out of check_probes_pass.py under the 500-line cap. NOT APPLICABLE (lib/not_applicable.py) is
a case of its own, in both directions: off a hosted run a probe that exits 78 and says it is BROKEN
-- that is how a probe could pass silently on the owner's machine -- and on one it is neither a
pass nor a failure (CI.PUBLIC2).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import counted, err, ok


def probe(gate_mod):
    """Four cases: a passing proof, a BROKEN proof, a NOT APPLICABLE one, and a gate that
    declares none."""
    discover, run_one, main = gate_mod.discover, gate_mod.run_one, gate_mod.main
    import tempfile
    import textwrap

    from _calibration_probe import probe as calibration_probe

    bad = 0
    with tempfile.TemporaryDirectory(prefix="goh-probes.") as td:
        tools = os.path.join(td, "tools")
        os.makedirs(tools)

        def gate(name, body):
            with open(os.path.join(tools, name), "w", encoding="utf-8") as handle:
                handle.write(textwrap.dedent(body).lstrip())

        gate(
            "check_good.py",
            """
            import sys
            if "--probe" in sys.argv:
                print("ok")
                sys.exit(0)
            sys.exit(0)
        """,
        )
        gate(
            "check_broken.py",
            """
            import sys
            if "--probe" in sys.argv:
                raise SystemExit("the fixture this probe drove was renamed")
            sys.exit(0)
        """,
        )
        # Declares no self-proof, and MENTIONS --probe in prose -- the false positive the
        # anchored detector exists to refuse.
        gate(
            "check_bare.py",
            '''
            """This gate has no --probe yet; see the backlog."""
            import sys
            sys.exit(0)
        ''',
        )

        # main(argv) taking the list as a PARAMETER is as real a dispatch as reading sys.argv.
        # Requiring the module path marked ZeroThunder's check_mesh_integrity, which ships a
        # genuine --break-probe, as carrying no self-proof at all.
        gate(
            "check_param.py",
            """
            import sys
            def main(argv):
                if "--break-probe" in argv:
                    return 0
                return 0
            sys.exit(main(sys.argv[1:]))
        """,
        )

        probes, total = discover(td)
        names = {os.path.basename(p) for p, _ in probes}

        for label, want, got in (
            ("all four gates are counted", 4, total),
            (
                "every real self-proof is found",
                {"check_good.py", "check_broken.py", "check_param.py"},
                names,
            ),
            ("a main(argv) dispatch counts as a self-proof", True, "check_param.py" in names),
            ("a gate that only MENTIONS --probe is not counted", False, "check_bare.py" in names),
            (
                "a passing proof passes",
                (True, ""),
                run_one(os.path.join(tools, "check_good.py"), "--probe", td),
            ),
            (
                "a BROKEN proof is caught",
                False,
                run_one(os.path.join(tools, "check_broken.py"), "--probe", td)[0],
            ),
            ("the whole run goes red on it", 1, main(["--root", td])),
        ):
            if want != got:
                err(f"probe: {label} (wanted {want!r}, got {got!r})")
                bad += 1
            else:
                ok(f"probe: {label}")

        os.remove(os.path.join(tools, "check_broken.py"))
        if main(["--root", td]) != 0:
            err("probe: a tree whose proofs all pass should be green")
            bad += 1
        else:
            ok("probe: a tree whose proofs all pass is green")

        # NOT APPLICABLE: honoured on a hosted run only. Off one, a probe that exits 78 is BROKEN
        # -- otherwise it could pass silently on the owner's machine, the judge of record.
        na_dir = os.path.join(td, "na")
        os.makedirs(na_dir)
        na_gate = os.path.join(na_dir, "check_na.py")
        with open(na_gate, "w", encoding="utf-8") as handle:
            handle.write(
                'print("NOT APPLICABLE on this hosted runner: x needs y")\nraise SystemExit(78)\n'
            )
        saved = os.environ.get("GITHUB_ACTIONS")
        try:
            for hosted, want in (("", False), ("true", None)):
                os.environ["GITHUB_ACTIONS"] = hosted
                got = run_one(na_gate, "--probe", td)[0]
                label = f"exit 78 {'on' if hosted else 'off'} a hosted run is {want}"
                if got is not want:
                    err(f"probe: {label} (got {got!r})")
                    bad += 1
                else:
                    ok(f"probe: {label}")
        finally:
            if saved is None:
                os.environ.pop("GITHUB_ACTIONS", None)
            else:
                os.environ["GITHUB_ACTIONS"] = saved

        empty = os.path.join(td, "empty")
        os.makedirs(empty)
        if main(["--root", empty]) != 0:
            err("probe: a repo with no gates at all should not fail")
            bad += 1
        else:
            ok("probe: a repo with no gates at all is reported, not failed")

    # The registry reader rides here because it is the half of R1 that discovery cannot see. It is
    # a MODULE's self-proof rather than this gate's, so it is driven and counted here.
    bad += calibration_probe()

    if bad:
        err(f"check_probes_pass --probe: {counted(bad, 'case')} wrong")
        return 1
    ok("check_probes_pass --probe: a broken self-proof goes red, and prose does not count as one")
    return 0
