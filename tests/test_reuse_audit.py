"""The reuse auditor (GOH.REUSEAUDIT, lib/reuse_audit.py): a sampled reuse re-run and compared, a
repeated disagreement switching its path off in one recorded state with a fault filed, a starved
sampler never clean, and a reuse no registered path names refused.

A skip keyed on less than its real inputs makes the gate lie while reading faster (GOH.PROVENBUILD,
GOH.VERDICTKEYDRIFT). The calibration is a PLANTED under-keyed reuse: the estate cache's key with
the corpus stat dropped, so a corpus that changed under it still hits -- the shadow run must catch
it, and the path must stay off.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT
from test_estate_corpus_cache import PLANT, estate  # noqa: F401  (fixture: a git corpus)
from test_reuse_store import SCOPE

sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT / "checks"))
import check_estate_corpus as sweep  # checks/, after its path
import reuse  # lib/, after its path
import reuse_audit

FIELDS = {"repo": "/r/.git", "sha": "a" * 40, "identity": "i" * 64}


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch) -> Path:
    """Each test its own store, so its own audit state and counters."""
    monkeypatch.setenv("GOH_REUSE_DIR", str(tmp_path / "store"))
    monkeypatch.delenv("GOH_REUSE_NOW", raising=False)
    return tmp_path / "store"


@pytest.fixture
def fake_loopctl(tmp_path, monkeypatch) -> Path:
    """A loopctl that logs each call. `file` refuses as rules.class_first does
    (the loop queue's tools/loopq.py class_refusal): an item names its class, or `class_of: none`
    with a `class_why`, or it is not filed; and an id already in the queue (FAKE_ITEMS, one per
    line) is refused as tools/loopq.py file_item refuses it, done or open. A `--queue` file's phase
    `requires` (rules.phase_contracts, the loop's queue_rules.py) refuses an item missing one, and
    a closed phase any open item."""
    log = tmp_path / "loopctl.jsonl"
    script = tmp_path / "loopctl.py"
    script.write_text(
        "import json, os, sys\n"
        f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if 'file' in sys.argv:\n"
        "    new = json.loads(sys.argv[sys.argv.index('--json') + 1])\n"
        "    named = new.get('class_closure') or (new.get('class_of') == 'none'\n"
        "                                         and str(new.get('class_why') or '').strip())\n"
        "    if not named:\n"
        "        print(f\"\\u2717 {new['id']}: name its class (rules.class_first)\")\n"
        "        sys.exit(1)\n"
        "    if '--queue' in sys.argv:\n"
        "        q = json.load(open(sys.argv[sys.argv.index('--queue') + 1]))\n"
        "        for p in q.get('phases', []):\n"
        "            if p['id'] == new.get('phase') and p.get('closed'):\n"
        "                print(f\"\\u2717 phase {p['id']} is closed but holds an open item\")\n"
        "                sys.exit(1)\n"
        "            for key in p.get('requires', []) if p['id'] == new.get('phase') else []:\n"
        "                if not new.get(key):\n"
        "                    print(f\"\\u2717 {new['id']}: phase {p['id']} requires `{key}`\")\n"
        "                    sys.exit(1)\n"
        "    items = os.environ.get('FAKE_ITEMS')\n"
        "    if items and new['id'] in open(items).read().split():\n"
        "        print(f\"\\u2717 {new['id']} already exists; `set` edits an item\")\n"
        "        sys.exit(1)\n"
        "    if items:\n"
        "        open(items, 'a').write(new['id'] + '\\n')\n"
    )
    monkeypatch.setenv("GOH_LOOPCTL", str(script))
    queue = tmp_path / "loop_queue.json"  # never the live queue: an open SPD, as on 2026-11-19
    queue.write_text(json.dumps({"phases": [{"id": "SPD", "requires": ["metric", "predicted"]}]}))
    monkeypatch.setenv("GOH_LOOP_QUEUE", str(queue))
    monkeypatch.delenv("GOH_REUSE_NOW", raising=False)
    return log


def filed(log: Path) -> list[dict]:
    calls = [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    return [json.loads(c[c.index("--json") + 1]) for c in calls if "file" in c]


def every(monkeypatch, name: str, n: int) -> None:
    monkeypatch.setitem(reuse.PATHS, name, dataclasses.replace(reuse.PATHS[name], every=n))


def test_a_planted_under_keyed_reuse_is_caught_and_its_path_switched_off(
    estate,  # noqa: F811
    fake_loopctl,
    monkeypatch,
) -> None:
    every(monkeypatch, "estate", 1)
    monkeypatch.setattr(sweep._estate_cache, "entry_key", lambda *a: "k" * 64)  # the plant
    entry = estate["entry"]
    assert sweep._judged(dict(entry), []) == "verified"  # a miss: judged, and recorded
    unclean = estate["root"] / "pkg" / "m3.py"
    clean = unclean.read_text()
    unclean.write_text(clean + PLANT)  # the corpus turns unclean under the key
    bad: list[str] = []
    assert sweep._judged(dict(entry), bad) == "blind", "the under-keyed hit was reused"
    assert bad == ["markers"]
    rec = reuse_audit.state("estate")
    assert rec["state"] == "off" and rec["cause"] == "disagree", rec
    assert rec["evidence"]["reused"] == "verified" and rec["evidence"]["fresh"] == "blind"
    assert rec["evidence"]["again"] == {"reused": "verified", "fresh": "blind"}
    [fault] = filed(fake_loopctl)
    assert fault["id"] == "GOH.REUSEOFFESTATE" and "'verified'" in fault["item"]
    assert rec["fault"] == "filed GOH.REUSEOFFESTATE"
    built = len(estate["calls"])
    unclean.write_text(clean)  # clean again: judged afresh, never reused
    assert sweep._judged(dict(entry), []) == "verified" and len(estate["calls"]) > built


def test_a_disagreement_the_rerun_does_not_repeat_is_a_flake_and_the_path_stays_on(
    fake_loopctl,
) -> None:
    assert not reuse_audit.compare("landed", FIELDS, "green", "red", lambda: ("green", "green"))
    rec = reuse_audit.state("landed")
    assert rec["state"] == "on" and rec["window"]["flakes"] == 1 and not filed(fake_loopctl)
    assert reuse_audit.compare("landed", FIELDS, "green", "green")
    assert reuse_audit.state("landed")["window"]["agreed"] == 1


def test_one_use_in_every_is_sampled_exactly_and_an_off_path_reuses_nothing(monkeypatch) -> None:
    keys = [{"identity": "i", "entry": "e", "corpus": str(n)} for n in range(25)]
    got = reuse_audit.use_many("estate", keys)
    assert [n for n, d in enumerate(got) if d == "shadow"] == [9, 19]
    assert set(got) == {"reuse", "shadow"}
    with pytest.raises(KeyError):
        reuse_audit.use("estate", {"identity": "i"})  # a partial key is no reuse
    file = dict.fromkeys(reuse.PATHS["file"].fields, "v")
    assert reuse_audit.use_many("file", [file] * 12) == ["reuse"] * 12  # unsampled: no shadow
    monkeypatch.setattr(reuse_audit, "file_fault", lambda *a: "not filed: test")
    rec = reuse_audit._read("estate")
    reuse_audit._off(reuse.PATHS["estate"], rec, "disagree", "planted", {})
    reuse_audit._write(rec)
    assert reuse_audit.use_many("estate", keys[:2]) == ["off", "off"]


def test_a_window_whose_samples_were_never_compared_turns_the_path_off(
    fake_loopctl, monkeypatch
) -> None:
    monkeypatch.setenv("GOH_REUSE_NOW", "1000")
    every(monkeypatch, "landed", 1)
    assert reuse_audit.use("landed", FIELDS) == "shadow"
    assert reuse_audit.state("landed")["state"] == "on"
    monkeypatch.setenv("GOH_REUSE_NOW", str(1000 + reuse_audit.WINDOW_S))
    rec = reuse_audit.state("landed")
    assert rec["state"] == "off" and rec["cause"] == "starved", rec
    assert "0 of 1 sampled" in rec["why"] and filed(fake_loopctl)[0]["id"] == "GOH.REUSEOFFLANDED"
    [row] = [r for r in reuse_audit.report() if r["path"] == "landed"]
    assert row["verdict"].startswith("OFF (starved)")
    with pytest.raises(ValueError, match="--why"):
        reuse_audit.enable("landed", root=REPO_ROOT)
    assert reuse_audit.enable("landed", root=REPO_ROOT, why="the release push runs its sample")
    assert reuse_audit.state("landed")["state"] == "on"


def test_a_window_with_its_samples_compared_stays_on_and_a_thin_one_is_never_clean(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GOH_REUSE_NOW", "1000")
    every(monkeypatch, "landed", 1)
    for _ in range(3):
        assert reuse_audit.use("landed", FIELDS) == "shadow"
        reuse_audit.compare("landed", FIELDS, "green", "green")
    rows = {r["path"]: r for r in reuse_audit.report()}
    assert rows["landed"]["verdict"] == "sampled" and rows["landed"]["window"]["compared"] == 3
    assert rows["estate"]["verdict"].startswith("thin")
    assert rows["file"]["verdict"].startswith("UNSAMPLED")
    monkeypatch.setenv("GOH_REUSE_NOW", str(1000 + reuse_audit.WINDOW_S))
    assert reuse_audit.state("landed")["state"] == "on"


def test_a_disagreement_is_enabled_only_by_a_fix_with_its_calibrated_miss(
    fake_loopctl, tmp_path
) -> None:
    reuse_audit.compare("landed", FIELDS, "green", "red", lambda: ("green", "red"))
    assert reuse_audit.state("landed")["cause"] == "disagree"
    repo = tmp_path / "fix"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_k.py").write_text("def test_the_new_input_misses():\n    pass\n")
    fast_init(repo)
    for args in (
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "f"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                       stdin=subprocess.DEVNULL)  # fmt: skip
    with pytest.raises(ValueError, match="--fix"):
        reuse_audit.enable("landed", root=repo, why="trust me")
    with pytest.raises(ValueError, match="is not a test"):
        reuse_audit.enable("landed", root=repo, fix="HEAD", test="tests/test_k.py::test_other")
    reuse_audit.enable("landed", root=repo, fix="HEAD",
                       test="tests/test_k.py::test_the_new_input_misses")  # fmt: skip
    rec = reuse_audit.state("landed")
    assert rec["state"] == "on" and rec["enabled"]["was"]["cause"] == "disagree"


HOOK = REPO_ROOT / "hooks" / "claude" / "estate_advisories.py"


def _session_start(tmp_path: Path, store: Path) -> str:
    r = subprocess.run([sys.executable, str(HOOK)], input='{"source": "startup"}',
                       capture_output=True, text=True, check=False,
                       env={"GOH_ESTATE_STATE": str(tmp_path / "estate" / "audit.json"),
                            "GOH_REUSE_DIR": str(store), "PATH": "/usr/bin:/bin"})  # fmt: skip
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_an_unfiled_fault_is_named_at_session_start(tmp_path, store, monkeypatch) -> None:
    """A path off whose fault loopctl refused alerted nobody: SessionStart, which every session's
    context carries, names it until it is filed or the path is enabled."""
    monkeypatch.setenv("GOH_LOOPCTL", str(tmp_path / "no-loopctl.py"))
    every(monkeypatch, "carry", 1)
    reuse_audit.compare("carry", dict.fromkeys(reuse.PATHS["carry"].fields, "x"), "green", "red",
                        again=lambda: ("green", "red"))  # fmt: skip
    assert reuse_audit.state("carry")["fault"].startswith("not filed")
    said = [ln for ln in _session_start(tmp_path, store).splitlines() if "reuse" in ln]
    assert len(said) == 1 and "carry" in said[0] and "not filed" in said[0], said
    rec = json.loads((store / "audit" / "carry.json").read_text())
    rec["fault"] = "filed GOH.REUSEOFFCARRY"
    (store / "audit" / "carry.json").write_text(json.dumps(rec))
    assert not [ln for ln in _session_start(tmp_path, store).splitlines() if "reuse" in ln]


# --- the structural check: no reuse but a registered path's ------------------------------------

# lib/ship_suite.py is the suite record's reader, split from lib/reuse.py for its line cap.
API = {"lib/reuse.py", "lib/reuse_audit.py", "lib/ship_suite.py"}
# A call that reads a recorded verdict (lib/reuse.py's readers, the suite record's too: the ship's
# skip on it asked no audit, GOH.ONEVERDICTTREE4) or asks the audit, as code names it.
READS = re.compile(
    r"\breuse\.(?:lookup|lookup_gate|lookup_suite|landed_identity|pushed)\(|"
    r"\bship_suite\.full_suite\(|lookup-gate"
)
# A reader that skips no judgement on what it reads: the record it would MAKE is on disk.
PRODUCERS = {
    "lib/idle_suite.py": "the idle run exists to make main's full record; with one on "
    "disk there is nothing to make",
    "lib/ship_full_run.py": "the ship's run is never skipped (GOH.COVOFFLAND); it reads the record "
    "its own green run made, and a green run with none is no verdict",
}
ASKS = re.compile(r"\b(?:use|use_many|compare)\(\s*\"([\w-]+)\"")


def _code(rel: str, text: str) -> str:
    if not rel.endswith(".py"):
        return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith(("#", "//")))
    tree = ast.parse(text)
    docs = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr)}
    keep = text.splitlines()
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and id(n) in docs:
            for i in range(n.lineno - 1, n.end_lineno or n.lineno):
                keep[i] = ""
    return "\n".join(ln.split("  # ")[0] for ln in keep)


def refusals(rel: str, text: str) -> list[str]:
    """What the check refuses in one file: a reuse no registered path names as its site, an ask of
    an unregistered path, a path registered outside the one table."""
    code = _code(rel, text)
    sites = {s for p in reuse.PATHS.values() for s in p.sites}
    out = [f"{rel}: asks the audit about {name!r}, which no path registers"
           for name in ASKS.findall(code) if name not in reuse.PATHS]  # fmt: skip
    if READS.search(code) and rel not in sites and rel not in API and rel not in PRODUCERS:
        out.append(f"{rel}: reads a recorded verdict but is no registered path's site")
    if re.search(r"\b(?:reuse\.)?register\(\s*\"", code) and rel != "lib/reuse.py":
        out.append(f"{rel}: registers a reuse path outside lib/reuse.py")
    return out


def _tracked() -> list[str]:
    argv = ["git", "-C", str(REPO_ROOT), "ls-files", "-co", "--exclude-standard", "--", *SCOPE,
            "tests/_land_verdicts.py", "tests/_land_audit.py"]  # fmt: skip
    out = subprocess.run(argv, capture_output=True, text=True, check=True,
                         stdin=subprocess.DEVNULL).stdout.split()  # fmt: skip
    return [r for r in out if r.endswith((".py", ".sh", ".rs")) and (REPO_ROOT / r).is_file()]


def test_every_reuse_in_the_tree_is_a_registered_paths():
    bad = [b for rel in _tracked() for b in refusals(rel, (REPO_ROOT / rel).read_text())]
    assert not bad, bad


def test_every_registered_site_exists_and_every_sampled_one_asks_the_audit():
    for p in reuse.PATHS.values():
        assert p.sites, f"{p.name} names no site"
        for site in p.sites:
            assert (REPO_ROOT / site).is_file(), f"{p.name}: no site {site}"
            if p.shadow:  # Python asks `use("NAME", ...)`; shell, the CLI`s `use NAME`
                text = (REPO_ROOT / site).read_text()
                asks = any(f'{verb}("{p.name}"' in text for verb in ("use", "use_many"))
                asks = asks or f'reuse_audit.py" use {p.name} ' in text
                assert asks, (p.name, site)
    sampled = sorted(p.name for p in reuse.PATHS.values() if p.shadow)
    # slice 1, GOH.VERDICTCARRY's `carry`, GOH.REUSEAUDIT2's, GOH.COVCARRY2's `profcarry`,
    # GOH.ONEVERDICTTREE3's `affected` (GOH.ONEVERDICTTREE4's `fullrun` went with the skip it
    # sampled, GOH.COVOFFLAND); the rest say why (`unsampled`)
    want = ["affected", "carry", "estate", "landed", "profcarry", "step", "test"]
    assert sampled == want, sampled


def test_the_check_refuses_an_unregistered_reuse():
    assert refusals("lib/new_cache.py", "rec, _ = reuse.lookup(PATH, fields)\n")
    assert refusals("lib/x.py", 'reuse_audit.use("nope", f)\n')
    assert refusals("tools/x.py", 'reuse.register("mine", ("k",), 1, "d")\n')
    assert refusals("gates/x.sh", 'python3 "$GOH/lib/reuse.py" lookup-gate --sha "$s"\n')
    assert refusals("lib/x.py", "rec, _ = reuse.lookup_suite(repo, tree, tools)\n")
    assert refusals("tools/x.py", "rec, why = ship_suite.full_suite(repo, sha)\n")
    assert not refusals("lib/x.py", '"""reuse.lookup( in prose"""\nx = 1  # reuse.lookup(\n')
    assert not refusals("lib/ship_verdict.py", "found, why = reuse.lookup_gate(repo, sha)\n")
    assert not refusals("lib/x.py", 'reuse_audit.use("estate", f)\n')
