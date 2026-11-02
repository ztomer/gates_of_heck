"""GOH.KEEP1: a green step that REWROTE a path GOH_EXPORT_KEEP keeps is named, on a ratchet.

divoom-control P1.14/P1.17 (2026-11-01): six pre-push steps rebuilt `target/debug/divoomd`, a kept
file the proven key hashes, so each moved its own key and was never recorded as proven -- and the
only trace was an info line. Now the runner names repo, step and path; a rewrite the repo's
kept_rewrite_allow.json carries is a warning, one it does not is red, and an entry that went
stale (its step ran green without the rewrite, or its path is no longer kept) is red too.
"""

import json
from pathlib import Path

import pytest
import test_proven as _tp
from conftest import REPO_ROOT
from test_proven import _git, local_ci, proven, records

goh, repo, env, hooked = _tp.goh, _tp.repo, _tp.env, _tp.hooked

REWRITE = 'echo ran >> "$COUNT"; echo rebuilt > build/out.bin'
CLEAN = 'echo ran >> "$COUNT"'


def _allow(repo: Path, *entries: dict) -> None:
    (repo / "kept_rewrite_allow.json").write_text(json.dumps({"entries": list(entries)}))


def _seed(repo: Path, *entries: dict) -> None:
    (repo / "build").mkdir(exist_ok=True)
    (repo / "build" / "out.bin").write_text("before\n")
    if entries:
        _allow(repo, *entries)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "seed", "--no-verify")


def _keep(env: dict) -> dict:
    return dict(env, GOH_EXPORT_KEEP="build/out.bin")


# ── the unit: lib/kept_rewrite.py's verdict, pure ──


@pytest.fixture(scope="module")
def kr():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "kept_rewrite", REPO_ROOT / "lib" / "kept_rewrite.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_unit_an_unlisted_rewrite_is_red_and_named(kr):
    lines, red = kr.verdict([], "r", "s", ".venv", [".venv"])
    assert red
    assert any(k == "err" and "r: step 's' rewrote kept path '.venv'" in t for k, t in lines)


def test_unit_a_listed_rewrite_is_a_warning_with_its_reason(kr):
    e = {"step": "s", "path": ".venv", "reason": "uv sync"}
    lines, red = kr.verdict([e], "r", "s", ".venv/", [".venv"])
    assert not red
    want = (
        "r: step 's' rewrote kept path '.venv' -- never recorded as proven"
        " (on the kept_rewrite_allow.json ratchet: uv sync)"
    )
    assert lines == [("warn", want)]


def test_unit_nothing_moved_and_nothing_listed_is_silent(kr):
    assert kr.verdict([], "r", "s", ".venv", []) == ([], False)


def test_unit_a_listed_step_that_did_not_rewrite_is_stale(kr):
    e = {"step": "s", "path": ".venv", "reason": "x"}
    lines, red = kr.verdict([e], "r", "s", ".venv", [])
    assert red and "stale entry" in lines[0][1]
    # ...and another step's green run says nothing about this entry.
    assert kr.verdict([e], "r", "other", ".venv", []) == ([], False)


def test_unit_an_entry_for_a_path_no_longer_kept_is_stale(kr):
    e = {"step": "s", "path": "jniLibs", "reason": "x"}
    lines, red = kr.verdict([e], "r", "other", ".venv", [])
    assert red and "does not keep it" in lines[0][1]


def test_unit_an_entry_without_a_reason_is_refused(kr, tmp_path):
    f = tmp_path / "a.json"
    f.write_text(json.dumps({"entries": [{"step": "s", "path": "p", "reason": " "}]}))
    with pytest.raises(TypeError, match="reason"):
        kr.load(f)
    assert kr.main(["--allow", str(f), "--repo", "r", "--step", "s"]) == 2


# ── proven.sh: both directions, end to end ──


def test_a_planted_rewrite_of_a_kept_path_is_red_and_named(goh, repo, env):
    _seed(repo)
    r = proven(goh, repo, _keep(env), REWRITE)
    assert r.returncode == 1, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert f"kept-rewrite: repo: step '{REWRITE}' rewrote kept path 'build/out.bin'" in out, out
    assert records(repo) == []


def test_an_untouched_kept_path_is_not_named_and_records(goh, repo, env):
    _seed(repo)
    r = proven(goh, repo, _keep(env), CLEAN)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "kept-rewrite" not in r.stdout + r.stderr
    assert len(records(repo)) == 1


def test_an_allowlisted_rewrite_is_a_warning_and_still_not_recorded(goh, repo, env):
    _seed(repo, {"step": REWRITE, "path": "build/out.bin", "reason": "the fixture's rebuild"})
    r = proven(goh, repo, _keep(env), REWRITE)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "kept-rewrite: repo: step" in out and "the fixture's rebuild" in out, out
    assert records(repo) == []


def test_a_stale_allowlist_entry_is_red_and_records_nothing(goh, repo, env):
    _seed(repo, {"step": CLEAN, "path": "build/out.bin", "reason": "it once rebuilt"})
    r = proven(goh, repo, _keep(env), CLEAN)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "a stale entry, delete it" in r.stdout + r.stderr
    assert records(repo) == []


def test_an_entry_naming_a_path_not_kept_is_red(goh, repo, env):
    _seed(repo, {"step": "other", "path": "gone", "reason": "it was kept once"})
    r = proven(goh, repo, _keep(env), CLEAN)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "GOH_EXPORT_KEEP does not keep it" in r.stdout + r.stderr


# ── local_ci.sh: the push gate's runner goes red the same way ──


def test_local_ci_is_red_on_an_unlisted_kept_rewrite(goh, hooked, env):
    (hooked / ".gatesrc").write_text(
        f"GOH_EXPORT_KEEP='build/out.bin'\nGOH_CI_STEPS='{REWRITE}:{CLEAN}'\n"
    )
    _seed(hooked)
    r = local_ci(goh, hooked, _keep(env))
    assert r.returncode == 1, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "rewrote kept path 'build/out.bin'" in out and "(kept-rewrite)" in out, out
    # The clean step beside it is green and recorded; only the rewriter is named.
    assert out.count("rewrote kept path") == 1, out


def test_local_ci_is_green_when_the_ratchet_carries_it(goh, hooked, env):
    (hooked / ".gatesrc").write_text(f"GOH_EXPORT_KEEP='build/out.bin'\nGOH_CI_STEPS='{REWRITE}'\n")
    _seed(hooked, {"step": REWRITE, "path": "build/out.bin", "reason": "the fixture's rebuild"})
    r = local_ci(goh, hooked, _keep(env))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "the fixture's rebuild" in r.stdout + r.stderr
