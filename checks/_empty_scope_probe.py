"""The empty-scope probe: every way this gate can be wrong, exercised on a fixture repo.

Split out of `check_empty_scope.py` for the 500-line rule, and by CONCERN: that file SWEEPS a real
estate, this one proves the sweep reads the results correctly. Keeping them apart matters because
the probe builds a repo of deliberate, known-wrong gates — if it lived beside the sweep, a reader
would have to hold both in their head to tell a fixture from a finding.

Run it directly: `python3 checks/_empty_scope_probe.py`. `check_empty_scope.py --probe` calls
`probe()` here, so the gate's own proof is the same file the tests import.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.environ.get("GOH_DIR", os.path.expanduser("~/Projects/gates_of_heck")))

from _empty_scope_usage import is_usage_error  # noqa: E402,F401
from _gitutil import foreign_repo_env  # noqa: E402,F401
from tui.lib import err, info, ok  # noqa: E402

from check_empty_scope import (  # noqa: E402
    main,
    scratch_git,
    ALLOW_FILE,
    NOT_APPLICABLE,
    allowed,
    build_skeleton,
    err,
    gate_names,
    ok,
    sweep,
)


def probe():
    """A blind gate, a guarded one, an excused one, and the stale excuse."""
    bad = 0
    with tempfile.TemporaryDirectory() as td:
        root = os.path.join(td, "repo")
        tools = os.path.join(root, "tools")
        os.makedirs(os.path.join(root, "Sources", "Deep"))
        os.makedirs(tools)
        scratch_git("init", "-q", root, check=True)

        def gate(name, body):
            with open(os.path.join(tools, name), "w", encoding="utf-8") as handle:
                handle.write(body)

        # Counts files under Sources and reports success regardless -- the class, exactly.
        gate(
            "check_blind.py",
            "import os, sys\n"
            "n = sum(len(f) for _, _, f in os.walk('Sources'))\n"
            "print(f'ok {n} files clean')\n"
            "sys.exit(0)\n",
        )
        # Same scan, with the guard.
        gate(
            "check_guarded.py",
            "import os, sys\n"
            "n = sum(len(f) for _, _, f in os.walk('Sources'))\n"
            "if not n:\n"
            "    print('x scanned nothing')\n"
            "    sys.exit(1)\n"
            "sys.exit(0)\n",
        )

        # A gate that does not apply on this host and SAYS so, exiting 0: a
        # named non-run, not a pass (a macOS-only check on a Linux runner).
        gate(
            "check_foreign_host.py",
            "import sys\n"
            "print('foreign-host: not applicable on this OS (nothing to sign here)')\n"
            "sys.exit(0)\n",
        )

        names = gate_names(tools)
        with tempfile.TemporaryDirectory() as work:
            skeleton = build_skeleton(root, tools, os.path.join(work, "s"))
            blind, unrunnable = sweep(skeleton, "tools", names)

        cases = [
            (
                "all gates are discovered",
                ["check_blind.py", "check_foreign_host.py", "check_guarded.py"],
                names,
            ),
            ("the unguarded gate is caught passing over nothing", True, "check_blind.py" in blind),
            ("the guarded gate is not", False, "check_guarded.py" in blind),
            ("a named not-applicable skip is not a pass", False, "check_foreign_host.py" in blind),
            ("the sweep goes red", 1, main(["--root", root])),
        ]
        for label, want, got in cases:
            if want != got:
                err(f"probe: {label} (wanted {want!r}, got {got!r})")
                bad += 1
            else:
                ok(f"probe: {label}")

        # Excused: the sweep must go quiet.
        with open(os.path.join(tools, ALLOW_FILE), "w", encoding="utf-8") as handle:
            json.dump(
                {"known_blind": {"check_blind.py": "a fixture, excused to prove the list works"}},
                handle,
            )
        if main(["--root", root]) != 0:
            err("probe: an EXCUSED blind gate still failed the sweep")
            bad += 1
        else:
            ok("probe: an excused gate is skipped")

        # Stale: excuse the GUARDED one, which does not pass on nothing.
        with open(os.path.join(tools, ALLOW_FILE), "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "known_blind": {"check_blind.py": "still blind"},
                    "legitimate": {"check_guarded.py": "excused, but it was fixed"},
                },
                handle,
            )
        if main(["--root", root]) == 0:
            err("probe: a STALE excuse passed -- the allowlist is a rug, not a ratchet")
            bad += 1
        else:
            ok("probe: an excuse for a gate that now fails is reported")

        # A gate that TIMES OUT is UNRUNNABLE, and its silence must never read as a verdict. This
        # is the false report that started it: an excused gate that outran the budget was told it
        # "now FAILS on an empty tree -- delete its excuse", which is a claim about what it said,
        # from a gate that said nothing. Deleting that excuse silences a real guard over a slow
        # machine, so the two must be told apart.
        with open(os.path.join(tools, "check_hang.py"), "w", encoding="utf-8") as handle:
            handle.write("import time\ntime.sleep(30)\n")
        with open(os.path.join(tools, ALLOW_FILE), "w", encoding="utf-8") as handle:
            json.dump(
                {"legitimate": {"check_hang.py": "slow but correct on a loaded machine"}}, handle
            )
        import check_empty_scope as ces

        saved_timeout = ces.TIMEOUT
        ces.TIMEOUT = 1
        try:
            buf = io.StringIO()
            # err() writes to stderr and warn() to stdout, and this case reads both.
            with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
                hang_rc = main(["--root", root])
            hang_err = buf.getvalue()
        finally:
            ces.TIMEOUT = saved_timeout
        for label, want, got in [
            ("a gate that never reports does not pass the sweep", 1, hang_rc),
            (
                "its silence is reported as no verdict, not as a pass",
                True,
                "gave NO verdict" in hang_err + str(hang_rc),
            ),
            (
                "and it is NOT told to delete a correct excuse",
                False,
                "delete its excuse" in hang_err,
            ),
        ]:
            if want != got:
                err(f"probe: {label} (wanted {want!r}, got {got!r})")
                bad += 1
            else:
                ok(f"probe: {label}")
        os.remove(os.path.join(tools, "check_hang.py"))

        # A gate that DEMANDS an argument never looked, and must not be scored as a refusal —
        # that is silent and reads as deliberate. `check_display_seam.py` requires `--policy` and
        # landed in the healthy column for exactly this reason. Built and removed here so the
        # cases above still see a sweep that can come back clean.
        gate(
            "check_demanding.py",
            "import sys\n"
            "if '--policy' not in sys.argv:\n"
            "    print('usage: check_demanding.py [--policy P]', file=sys.stderr)\n"
            "    print('error: --policy is required', file=sys.stderr)\n"
            "    sys.exit(2)\n"
            "sys.exit(0)\n",
        )
        try:
            with tempfile.TemporaryDirectory() as work:
                demanding_skeleton = build_skeleton(root, tools, os.path.join(work, "s"))
                d_blind, d_unrunnable = sweep(demanding_skeleton, "tools", ["check_demanding.py"])
            for label, want, got in [
                (
                    "a gate demanding an argument is not scored as a pass",
                    False,
                    "check_demanding.py" in d_blind,
                ),
                (
                    "it is reported as never having looked",
                    True,
                    "check_demanding.py" in d_unrunnable
                    and "argument" in d_unrunnable["check_demanding.py"],
                ),
            ]:
                if want != got:
                    err(f"probe: {label} (wanted {want!r}, got {got!r})")
                    bad += 1
                else:
                    ok(f"probe: {label}")
        finally:
            os.remove(os.path.join(tools, "check_demanding.py"))

        # A flat file predates the split; it must read as DEFECTS, never as legitimate.
        with open(os.path.join(tools, ALLOW_FILE), "w", encoding="utf-8") as handle:
            json.dump({"check_blind.py": "an old flat entry"}, handle)
        flat_legit, flat_blind = allowed(tools)
        if flat_legit or list(flat_blind) != ["check_blind.py"]:
            err("probe: a flat allowlist was not read conservatively as known-blind")
            bad += 1
        else:
            ok("probe: a pre-split flat allowlist reads as defects, not as legitimate")

    if bad:
        err(f"check_empty_scope --probe: {bad} case(s) wrong")
        return 1
    ok("check_empty_scope --probe: a blind gate is caught, a guarded one is not, both ways")
    return 0
