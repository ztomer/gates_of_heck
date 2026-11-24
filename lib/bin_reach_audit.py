"""The 1-in-10 audit of the binary reach (GOH.BINREACH; lib/bin_reach.py chooses).

One land in EVERY (by its tree, so every re-land of one tree is audited alike) also runs, after its
verdict, the binary tests the stored profiles' reach left out (tools/affected_tests.py `run`). A
test red there is a DISAGREEMENT: the reach did not see that the change reaches it. The pre-step
goes red (a red test is never carried past), a row joins <reuse root>/binreach/audit.jsonl, each
disagreement files a fault item, and `goh land-profile` prints the count (`audit_line`).
GOH_BINREACH_AUDIT=always|never forces it. Pinned by tests/test_bin_reach.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

try:
    from lib import reuse
    from lib.tmp_root import goh_tmp
except ImportError:  # run by path, not as a package
    import reuse  # type: ignore[no-redef]
    from tmp_root import goh_tmp  # type: ignore[no-redef]

AUDIT = "GOH_BINREACH_AUDIT"
EVERY = 10  # one land in EVERY audits the reach
ROWS = 200  # audit rows kept
DROP = ("GOH_LAND_VERDICTS_RECORD", "GOH_LAND_VERDICTS_REUSE")  # the audit is no land's verdict


def audit_due(tree: str) -> bool:
    """This land audits the reach: forced by GOH_BINREACH_AUDIT, else one tree in EVERY."""
    forced = os.environ.get(AUDIT, "")
    if forced in ("always", "never"):
        return forced == "always"
    return int(hashlib.sha256(tree.encode()).hexdigest()[:8], 16) % EVERY == 0


def _ledger() -> Path:
    return reuse.root() / "binreach" / "audit.jsonl"


def audits() -> list[dict]:
    try:
        lines = _ledger().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for ln in lines:
        try:
            row = json.loads(ln)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def record_audit(tree: str, changed: list[str], skipped: list[str], red: list[str]) -> dict:
    """One audit's row: what the reach left out, and the reds among them (the disagreements)."""
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    row = {"at": at, "tree": tree, "changed": changed, "skipped": len(skipped),
           "disagreements": len(red), "red": red}  # fmt: skip
    f = _ledger()
    f.parent.mkdir(parents=True, exist_ok=True)
    rows = [*audits(), row][-ROWS:]
    tmp = f.with_name(f".{f.name}.{os.getpid()}.tmp")
    tmp.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    os.replace(tmp, f)
    return row


def disagreements(rows: list[dict]) -> int:
    return sum(int(r.get("disagreements") or 0) for r in rows)


def audit_line(rows: list[dict] | None = None) -> str:
    """`goh land-profile`'s line: audits run, binary tests they re-ran, disagreements."""
    rows = audits() if rows is None else rows
    red = sorted({n for r in rows for n in r.get("red") or ()})
    named = f": {' '.join(red[:5])}{' ...' if len(red) > 5 else ''}" if red else ""
    return (f"binary reach audit (GOH.BINREACH): {len(rows)} audits re-ran "
            f"{sum(int(r.get('skipped') or 0) for r in rows)} tests the reach left out; "
            f"{disagreements(rows)} disagreements{named}")  # fmt: skip


def file_fault(tree: str, node: str, changed: list[str]) -> str:
    """`loopctl file` one disagreement's fault item; "filed ID", "already filed ID" or why not."""
    try:
        from lib import reuse_fault
    except ImportError:  # run by path, not as a package
        import reuse_fault  # type: ignore[no-redef]
    loopctl = os.environ.get("GOH_LOOPCTL") or str(Path.home() / "Projects/.loop/tools/loopctl.py")
    if not os.path.isfile(loopctl):
        return f"not filed: no loopctl at {loopctl}"
    iid = "GOH.BINREACHMISS" + hashlib.sha256(node.encode()).hexdigest()[:8].upper()
    item = {"id": iid, "repo": "gates_of_heck", "status": "todo", "depends": [],
            "source": "goh binary reach audit (lib/bin_reach.py)",
            "item": f"The binary reach audit re-ran the tests the stored profiles left out of a "
            f"Rust change and {node} went RED: the reach (lib/bin_reach.py `touched`, the stored "
            f"profile of that test) did not see that the change to {', '.join(changed[:5])} "
            f"reaches it, on tree {tree[:12]}. Find the edge the reach missed (a constant, a "
            f"type, a macro, a stale profile) and pin it in tests/test_bin_reach.py.",
            "metric": "binary reach audit disagreements (goh land-profile): a test red in the "
            "full binary set that the reach did not choose",
            "predicted": "the missed edge read: this change chooses the test, 0 disagreements",
            "verify_after": {"lands": 10},
            "done_state": "the reach chooses the test for that change; a test pins the edge",
            "class_of": "none", "class_why": "one disagreement of the reach audit"}  # fmt: skip
    phase = reuse_fault.current_phase(set(item))
    if not phase.startswith("phase "):
        return f"not filed: {phase}"
    code, said = reuse_fault._file(loopctl, {**item, "phase": phase[6:]})
    if code == 0 or f"{iid} already exists" in said:
        return f"{'filed' if code == 0 else 'already filed'} {iid}"
    return f"not filed: {said[-200:]}"


def tree_of(root: Path) -> str:
    r = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD^{tree}"], capture_output=True,
                       text=True, stdin=subprocess.DEVNULL, check=False,
                       env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})  # fmt: skip
    return r.stdout.strip()


def red_nodes(junit: Path, root: Path) -> list[str]:
    """The node ids a pytest junit report holds as failed or errored."""
    try:
        cases = ET.parse(junit).getroot().iter("testcase")
    except (OSError, ET.ParseError):
        return []
    out = []
    for case in cases:
        if case.find("failure") is None and case.find("error") is None:
            continue
        parts, name = (case.get("classname") or "").split("."), case.get("name") or ""
        node = ".".join(parts) + "::" + name
        for k in range(len(parts), 0, -1):
            if (root / ("/".join(parts[:k]) + ".py")).is_file():
                node = "::".join(["/".join(parts[:k]) + ".py", *parts[k:], name])
                break
        out.append(node)
    return sorted(set(out))


def audit(
    root: Path, changed: list[str], skipped: list[str], runner: Callable[[list[str], Path], int]
) -> tuple[int, str]:
    """(1 on a disagreement else 0, the line to print) -- when this tree is due, SKIPPED run
    through RUNNER(nodes, junit report path) outside the land's verdict record, recorded, and
    each red filed; (0, "") when nothing was left out or the tree is not due."""
    tree = tree_of(root) if skipped else ""
    if not skipped or not audit_due(tree):
        return 0, ""
    tmp = Path(tempfile.mkdtemp(prefix="goh-binreach-", dir=goh_tmp()))
    saved = {k: os.environ.pop(k) for k in DROP if k in os.environ}
    # an id whose file this tree has not would fail pytest's whole collection (exit 4): named red
    absent = [n for n in skipped if not (root / n.split("::")[0]).is_file()]
    present = [n for n in skipped if n not in absent]
    try:
        rc = runner(present, tmp / "audit.xml") if present else 0
        red = red_nodes(tmp / "audit.xml", root)
    finally:
        os.environ.update(saved)
        shutil.rmtree(tmp, ignore_errors=True)
    if rc and not red:
        red = [f"(the audit run exited {rc} naming no failed test)"]
    red += [f"{n} (not in this tree)" for n in absent]
    record_audit(tree, changed, skipped, red)
    filed = [file_fault(tree, n, changed) for n in red]
    said = (f"binary reach audit: {len(skipped)} tests the reach left out ran; "
            f"{len(red)} disagreements")  # fmt: skip
    if red:
        said += ": " + "; ".join(f"{n} ({f})" for n, f in zip(red, filed, strict=True))
    return (1 if red else 0), said


def after(
    root: Path, changed: list[str], skipped: list[str], chosen_for: list | None = None
) -> int:
    """tools/affected_tests.py `run`, after its verdict: the audit when due, SKIPPED run with the
    tree's tools/pytest.sh as a timed row of its own; 1 on a disagreement, said. CHOSEN_FOR: the
    (root, change) the choice that left SKIPPED out was made for; a run over another audits none
    of it (GOH.LANDEREXIT1: a test's run over the checkout audited a fixture tree's ids)."""
    if chosen_for is not None and chosen_for != [(Path(root).resolve(), tuple(changed))]:
        skipped = []
    from lib.step_timings import timed
    from tui.lib import err, info

    def runner(nodes: list[str], junit: Path) -> int:
        argv = ["bash", str(root / "tools" / "pytest.sh"), "-q", f"--junitxml={junit}", *nodes]
        return timed(f"pre-land: {len(nodes)} tests the binary reach left out (its audit)", argv)

    rc, said = audit(root, changed, skipped, runner)
    if said:
        (err if rc else info)(f"pre-land: {said}")
    return rc
