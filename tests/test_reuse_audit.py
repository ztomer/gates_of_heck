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
    """A loopctl that logs each call; `ci` answers the exit FAKE_CI names (default 0, green)."""
    log = tmp_path / "loopctl.jsonl"
    script = tmp_path / "loopctl.py"
    script.write_text(
        "import json, os, sys\n"
        f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "sys.exit(int(open(os.environ['FAKE_CI']).read()) if 'ci' in sys.argv[1:2] else 0)\n"
    )
    ci = tmp_path / "ci_exit"
    ci.write_text("0")
    monkeypatch.setenv("FAKE_CI", str(ci))
    monkeypatch.setenv("GOH_LOOPCTL", str(script))
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
    step = dict.fromkeys(reuse.PATHS["step"].fields, "v")
    assert reuse_audit.use_many("step", [step] * 12) == ["reuse"] * 12  # unsampled: no shadow
    monkeypatch.setattr(reuse_audit, "file_fault", lambda *a: "not filed: test")
    rec = reuse_audit._read("estate")
    reuse_audit._off(reuse.PATHS["estate"], rec, "disagree", "planted", {})
    reuse_audit._write(rec)
    assert reuse_audit.use_many("estate", keys[:2]) == ["off", "off"]


def test_a_window_whose_samples_were_never_compared_turns_the_path_off(
    fake_loopctl, monkeypatch
) -> None:
    monkeypatch.setenv("GOH_REUSE_NOW", "1000")
    assert reuse_audit.use("landed", FIELDS) == "shadow"  # every=1: a ship's reuse is sampled
    assert reuse_audit.state("landed")["state"] == "on"
    monkeypatch.setenv("GOH_REUSE_NOW", str(1000 + reuse_audit.WINDOW_S))
    rec = reuse_audit.state("landed")
    assert rec["state"] == "off" and rec["cause"] == "starved", rec
    assert "0 of 1 sampled" in rec["why"] and filed(fake_loopctl)[0]["id"] == "GOH.REUSEOFFLANDED"
    [row] = [r for r in reuse_audit.report() if r["path"] == "landed"]
    assert row["verdict"].startswith("OFF (starved)")
    with pytest.raises(ValueError, match="--why"):
        reuse_audit.enable("landed", root=REPO_ROOT)
    assert reuse_audit.enable("landed", root=REPO_ROOT, why="the lander drains")
    assert reuse_audit.state("landed")["state"] == "on"


def test_a_window_with_its_samples_compared_stays_on_and_a_thin_one_is_never_clean(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GOH_REUSE_NOW", "1000")
    for _ in range(3):
        assert reuse_audit.use("landed", FIELDS) == "shadow"
        reuse_audit.compare("landed", FIELDS, "green", "green")
    rows = {r["path"]: r for r in reuse_audit.report()}
    assert rows["landed"]["verdict"] == "sampled" and rows["landed"]["window"]["compared"] == 3
    assert rows["estate"]["verdict"].startswith("thin")
    assert rows["step"]["verdict"].startswith("UNSAMPLED")
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


def test_a_ci_shadow_is_compared_on_its_read_and_a_red_one_only_on_a_second(
    fake_loopctl, tmp_path
) -> None:
    ci = tmp_path / "ci_exit"
    reuse_audit.enqueue("landed", FIELDS, "green", slug="o/n", sha="b" * 40)
    ci.write_text("3")  # pending: kept, never compared
    assert "kept" in reuse_audit.drain()[0]
    ci.write_text("0")
    reuse_audit.drain()
    assert reuse_audit.state("landed")["window"]["agreed"] == 1
    reuse_audit.enqueue("landed", FIELDS, "green", slug="o/n", sha="c" * 40)
    ci.write_text("1")
    assert "read again" in reuse_audit.drain()[0]
    assert reuse_audit.state("landed")["state"] == "on"
    reuse_audit.drain()
    rec = reuse_audit.state("landed")
    assert rec["state"] == "off" and rec["evidence"]["fresh"] == "red"
    assert not list((reuse.root() / "audit" / "pending").glob("*.json"))


# --- the structural check: no reuse but a registered path's ------------------------------------

API = {"lib/reuse.py", "lib/reuse_audit.py"}
# A call that reads a recorded verdict (lib/reuse.py's readers) or asks the audit, as code names it.
READS = re.compile(r"\breuse\.(?:lookup|lookup_gate|landed_identity|pushed)\(|lookup-gate")
ASKS = re.compile(r"\b(?:use|use_many|enqueue|compare)\(\s*\"([\w-]+)\"")


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
    if READS.search(code) and rel not in sites and rel not in API:
        out.append(f"{rel}: reads a recorded verdict but is no registered path's site")
    if re.search(r"\b(?:reuse\.)?register\(\s*\"", code) and rel != "lib/reuse.py":
        out.append(f"{rel}: registers a reuse path outside lib/reuse.py")
    return out


def _tracked() -> list[str]:
    argv = ["git", "-C", str(REPO_ROOT), "ls-files", "-co", "--exclude-standard", "--", *SCOPE,
            "tests/_land_verdicts.py"]  # fmt: skip
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
            if p.shadow:
                assert f'use("{p.name}"' in (REPO_ROOT / site).read_text(), (p.name, site)
    sampled = sorted(p.name for p in reuse.PATHS.values() if p.shadow)
    # slice 1 and GOH.VERDICTCARRY's `carry`; the rest say why (`unsampled`)
    assert sampled == ["carry", "estate", "landed"], sampled


def test_the_check_refuses_an_unregistered_reuse():
    assert refusals("lib/new_cache.py", "rec, _ = reuse.lookup(PATH, fields)\n")
    assert refusals("lib/x.py", 'reuse_audit.use("nope", f)\n')
    assert refusals("tools/x.py", 'reuse.register("mine", ("k",), 1, "d")\n')
    assert refusals("gates/x.sh", 'python3 "$GOH/lib/reuse.py" lookup-gate --sha "$s"\n')
    assert not refusals("lib/x.py", '"""reuse.lookup( in prose"""\nx = 1  # reuse.lookup(\n')
    assert not refusals("lib/ship_verdict.py", "found, why = reuse.lookup_gate(repo, sha)\n")
    assert not refusals("lib/x.py", 'reuse_audit.use("estate", f)\n')
