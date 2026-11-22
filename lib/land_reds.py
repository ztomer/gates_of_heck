"""lib/land_reds.py: each red land record carries its failing tests and cause class (GOH.REDREVIEW).

OWNER, 2026-11-22: "what other metaprocess should we have?" Over 7 days 107 of 362 land records
were red (30%), and a red record's `why` said only `exit 1` (62) or `enqueue pre-step: the
pre-land tests failed` (30), so no cause could be counted. This module is the count's instrument:

    fields(outcome)   `{"red_cause": {cause, rule, tests}}` for a red record, at the moment the
                      lander writes it (tools/landq.py `record`); {} for any other. Never raises:
                      the instrument is never the reason a land fails.
    classify(...)     the deterministic rules below over the record, its log and the failing
                      tests' own source at the record's commit; the first rule that applies wins.
    backfill(q)       classifies every red record in `<q>/done/` already written, read-only over
                      the records: the answers go to the side file `<q>/red_causes.json`.
    report(q, last)   `goh land-reds [--last N] [--json] [--backfill]`: the classes by count over
                      the newest N records, each with example records.

THE CLASSES, in rule order (lib/land_cause.py is the other reading: WHERE a land failed):

    tip-moved          the worktree's tip moved after the enqueue; nothing of the tree was judged
    conflict           the rebase onto main conflicted, or the commit did not stack
    flake              a failing test is in the flake register (lib/flakes.py), or the same item
                       at the same commit landed later (backfill only: a pass on retry)
    consistency-table  a repo-wide judge the diff did not keep: a structural gate step (the map
                       row, the length cap), a `test_every_`/`test_no_` ratchet, or a test whose
                       name or assertion names a table, row, manifest, allowlist, seed or map
    env-leak           a test about the environment, or a test that reads a variable the failing
                       gate step sets (the pre-land ran it without that variable)
    pinned-text        a test that reads a source file's text and runs nothing: it pins old text
    real-bug           any other failing test
    other              no test named: a kill, a timeout, a slot that never came (read the log)
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gitrun  # lib/: git under a deadline, no lock stranded
import land_cause  # lib/: FAILED node ids and the structural step's line

CLASSES = (
    "tip-moved",
    "conflict",
    "flake",
    "consistency-table",
    "env-leak",
    "pinned-text",
    "real-bug",
    "other",
)
SIDE = "red_causes.json"
FAILING = re.compile(r"failing: ([^)]+)\)")
TABLE_NAME = re.compile(
    r"::test_(?:every|no)_|_rows?(?:_|$)|table|manifest|allow(?:list|ed)?_|documented|_map_|"
    r"_seed|registered|_listed|inventory"
)
TABLE_TEXT = re.compile(r"\.tsv\b|map\.md|allowlist|_allow\.|_seed\.json|manifest", re.IGNORECASE)
# A table file an assertion names, whole (`docs/map.md`, `house_scripts.tsv`, an allow list).
TABLE_FILE = re.compile(
    r"[\w./-]*(?:\.tsv|map\.md|allowlist[\w.]*|_allow\.\w+|_seed\.json|manifest\.\w+)"
    r"(?![\w/])"
)
ENV_NAME = re.compile(r"environ|_env(?:_|$)|hermetic|leak")
ENV_TEXT = re.compile(r"os\.environ|monkeypatch\.(?:set|del)env")
STEP_ENV = re.compile(r"^✗ [^:\n]+: external \([^)]*\): (.*) failed \(exit", re.MULTILINE)
ASSIGN = re.compile(r"(?:^|\s)([A-Z][A-Z0-9_]{2,})=")
PINNED_TEXT = re.compile(r"substring not found|ValueError: .*not in list")
READS_SOURCE = re.compile(r"\b(?:REPO_ROOT|ROOT)\b[^\n]*\.read_text\(")
RUNS = re.compile(r"\bsubprocess\.|\brun\(|\bimport_module\(|\bPopen\(")


def tests_of(rec: dict, log: str) -> list[str]:
    """The failing test ids: the record's `failing:` list (the pre-step's red), then its log's
    FAILED lines, once each, in order."""
    out: list[str] = []
    if found := FAILING.search(str(rec.get("why") or "")):
        out = [t.strip() for t in found.group(1).split(",") if t.strip()]
    return out + [t for t in land_cause.failed_tests(log) if t not in out]


def judges_a_table(test: str) -> bool:
    """The consistency-table rule over a test id alone: `classify`'s name rule, and the one
    `goh land --check` selects the tests it runs before an enqueue by (GOH.TABLESEARLY)."""
    return bool(TABLE_NAME.search(test))


def tables(log: str) -> list[str]:
    """The table files the assertion lines of LOG name, once each, in order."""
    found = TABLE_FILE.findall(error_lines(log))
    return list(dict.fromkeys(f for f in found if "..." not in f))  # pytest's cut is no name


def error_lines(log: str) -> str:
    """The assertion lines pytest printed (`E   ...`), joined."""
    return "\n".join(ln for ln in log.splitlines() if ln.startswith("E "))


def step_env(log: str) -> set[str]:
    """The variables the failing gate step's command sets (`VAR=... ./tools/pytest.sh`)."""
    found: set[str] = set()
    for cmd in STEP_ENV.findall(log):
        found |= set(ASSIGN.findall(cmd.split(" (exit")[0]))
    return found


def test_body(source: str, test: str) -> str:
    """The text of TEST's function in SOURCE (its file at the record's commit), or ""."""
    name = test.split("::")[-1].split("[")[0]
    m = re.search(rf"^([ \t]*)def {re.escape(name)}\(", source, re.MULTILINE)
    if not m:
        return ""
    indent = len(m.group(1))
    lines = [source[m.start() :].splitlines()[0]]
    for ln in source[m.end() :].splitlines()[1:]:
        if ln.strip() and len(ln) - len(ln.lstrip()) <= indent and not ln.lstrip().startswith(")"):
            break
        lines.append(ln)
    return "\n".join(lines)


def classify(
    rec: dict,
    log: str,
    *,
    sources: dict[str, str] | None = None,
    flaky: set[str] | frozenset[str] = frozenset(),
    retried: bool = False,
) -> dict:
    """One red record's `{cause, rule, tests}`. SOURCES maps a test id to its function's text at
    the record's commit; FLAKY is the failing tests the flake register holds; RETRIED says the
    same item at the same commit landed later."""
    why = str(rec.get("why") or "")
    tests = tests_of(rec, log)
    sources = sources or {}

    def out(cause: str, rule: str) -> dict:
        return {"cause": cause, "rule": rule, "tests": tests}

    if "tip moved" in why or "HEAD moved" in why:
        return out("tip-moved", "the tip moved after the enqueue")
    if why.startswith("conflict") or "conflicts with main" in why + log or "did not stack" in why:
        return out("conflict", "the rebase onto main conflicted")
    if hit := [t for t in tests if t in flaky]:
        return out("flake", f"{hit[0]} is in the flake register")
    if retried:
        return out("flake", "the same commit landed on a re-enqueue")
    errors = error_lines(log)
    if land_cause.STRUCTURAL.search(log):
        return out("consistency-table", "a structural gate step failed")
    if hit := [t for t in tests if judges_a_table(t)]:
        return out("consistency-table", f"{hit[0]} judges a table or the whole tree")
    if tests and TABLE_TEXT.search(errors):
        return out("consistency-table", "its assertion names a table file")
    if hit := [t for t in tests if ENV_NAME.search(t.split("::")[-1])]:
        return out("env-leak", f"{hit[0]} judges the environment")
    env = step_env(log)
    for t in tests:
        body = sources.get(t, "")
        if read := sorted(v for v in env if v in body):
            return out("env-leak", f"the gate step sets {read[0]}, which {t} reads")
        if ENV_TEXT.search(body):
            return out("env-leak", f"{t} sets the environment")
    if tests and PINNED_TEXT.search(errors):
        return out("pinned-text", "its assertion looked for text that is gone")
    for t in tests:
        body = sources.get(t, "")
        if READS_SOURCE.search(body) and not RUNS.search(body):
            return out("pinned-text", f"{t} reads a source file's text and runs nothing")
    if tests:
        return out("real-bug", "a test failed and no other rule applies")
    for said in ("exit -15", "exit -9", "timed out", "deadline of", "the gate never ran"):
        if said in why + log:
            return out("other", f"no test named ({said})")
    return out("other", "no test named")


def _sources(rec: dict, tests: list[str]) -> dict[str, str]:
    """Each test's function text at the record's commit, read from its repo; {} when unreadable."""
    repo, sha = str(rec.get("repo") or ""), str(rec.get("sha") or "")
    files: dict[str, str] = {}
    out: dict[str, str] = {}
    for t in tests:
        path = t.split("::")[0]
        if not (repo and sha and path.endswith(".py")):
            continue
        if path not in files:
            try:
                files[path] = gitrun.run_reader(
                    ["show", f"{sha}:{path}"], repo=repo, timeout=10
                ).stdout
            except (OSError, subprocess.SubprocessError):
                files[path] = ""
        out[t] = test_body(files[path], t)
    return out


def _flaky(tests: list[str]) -> set[str]:
    try:
        import flakes  # lib/: the flake register

        return {t for t in tests if flakes.recorded(t)}
    except Exception:  # noqa: BLE001 -- a register that cannot be read holds no flake
        return set()


def _log(rec: dict) -> str:
    try:
        return Path(str(rec.get("log") or "")).read_text(errors="replace")
    except OSError:
        return ""


def judge(rec: dict, retried: bool = False) -> dict:
    """REC's `{cause, rule, tests}` from its log, its tests' source and the flake register."""
    log = _log(rec)
    tests = tests_of(rec, log)
    return classify(rec, log, sources=_sources(rec, tests), flaky=_flaky(tests), retried=retried)


def fields(outcome: dict) -> dict:
    """`{"red_cause": ...}` for a red OUTCOME; {} otherwise or when it cannot be judged."""
    if outcome.get("state") != "red":
        return {}
    try:
        return {"red_cause": judge(outcome)}
    except Exception as e:  # noqa: BLE001 -- never the reason a land fails
        return {"red_cause": {"cause": "other", "rule": f"not judged: {e}", "tests": []}}


def records(q: Path) -> list[tuple[str, dict]]:
    """`(name, record)` for every done/ record, oldest first."""
    out = []
    for path in sorted((q / "done").glob("*.json")):
        try:
            rec = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict):
            out.append((path.name, rec))
    return out


def _item(name: str, rec: dict) -> str:
    return str(rec.get("item") or name.removesuffix(".json").partition("-")[2])


def backfill(q: Path) -> int:
    """Classify every red done/ record into `<q>/red_causes.json` (the records are never
    written); how many it holds."""
    recs = records(q)
    side: dict[str, dict] = {}
    for i, (name, rec) in enumerate(recs):
        if rec.get("state") != "red":
            continue
        later = recs[i + 1 :]
        retried = any(
            r.get("state") == "landed"
            and _item(n, r) == _item(name, rec)
            and (r.get("enqueued") or r.get("sha")) == rec.get("sha")
            for n, r in later
        )
        side[name] = judge(rec, retried=retried)
    tmp = q / f".{SIDE}.{os.getpid()}"
    tmp.write_text(json.dumps(side, indent=1, sort_keys=True) + "\n")
    tmp.replace(q / SIDE)
    return len(side)


def report(q: Path, last: int = 0) -> dict:
    """The newest LAST records (all, when 0): how many were red, and the classes by count, each
    with up to three example records. A record's own `red_cause` wins over the side file's."""
    recs = records(q)
    recs = recs[-last:] if last else recs
    try:
        side = json.loads((q / SIDE).read_text())
    except (OSError, ValueError):
        side = {}
    classes: dict[str, dict] = {}
    red = 0
    for name, rec in recs:
        if rec.get("state") != "red":
            continue
        red += 1
        cause = rec.get("red_cause") or side.get(name) or judge(rec)
        row = classes.setdefault(cause["cause"], {"count": 0, "examples": []})
        row["count"] += 1
        if len(row["examples"]) < 3:
            row["examples"].append(
                {
                    "record": name,
                    "item": _item(name, rec),
                    "rule": cause.get("rule", ""),
                    "tests": cause.get("tests", []),
                }
            )
    ranked = dict(sorted(classes.items(), key=lambda kv: (-kv[1]["count"], CLASSES.index(kv[0]))))
    return {"records": len(recs), "red": red, "classes": ranked}


def render(rep: dict) -> list[str]:
    n, red = rep["records"], rep["red"]
    pct = f"{100 * red / n:.0f}%" if n else "-"
    lines = [f"{red} of {n} land records red ({pct})"]
    for cause, row in rep["classes"].items():
        lines.append(f"  {row['count']:4d}  {cause}")
        for ex in row["examples"]:
            named = any(t in ex["rule"] for t in ex["tests"])
            test = "" if named or not ex["tests"] else f"{ex['tests'][0]}: "
            lines.append(f"          {ex['item']}: {test}{ex['rule']}")
    return lines


def qdir() -> Path:
    """The landing queue's state dir, as tools/landq.py `qdir` reads it."""
    return Path(os.environ.get("GOH_LANDQ_DIR") or Path.home() / ".local/state/goh/landq")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="goh land-reds", description=__doc__.split("\n")[0])
    ap.add_argument("--last", type=int, default=0, help="only the newest N records (default all)")
    ap.add_argument("--json", action="store_true", help="the report as JSON")
    ap.add_argument(
        "--backfill", action="store_true", help=f"classify every red record into <landq>/{SIDE}"
    )
    args = ap.parse_args(argv)
    q = qdir()
    if args.backfill:
        sys.stderr.write(f"{backfill(q)} red records classified into {q / SIDE}\n")
    rep = report(q, args.last)
    if args.json:
        sys.stdout.write(json.dumps(rep, indent=1) + "\n")
    else:
        sys.stdout.write("\n".join(render(rep)) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
