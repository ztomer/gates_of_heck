"""A goh change that turns a consumer red is refused before it reaches one (GOH.CANARY).

v0.57.4 (GOH.SHSEV) made `goh shell-lint` judge warnings and seeded only the repos that round
touched: eight consumers refused every push for five days. `scripts/release_canary.py` replays
the native gates of the last release and of HEAD over every registered consumer and refuses while
one step goes from green to red, naming the repo and the seed it needs.

The replay here is v0.57.4's own change over a FIXTURE estate: the base side is the native binary
with a `shellcheck` that judges errors only -- v0.57.3's `--severity=error` -- and the head side
is the binary as it is. One consumer carries an unseeded SC2034 warning and one is clean.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "release_canary.py"
HOOK = REPO_ROOT / "hooks" / "claude" / "estate_advisories.py"
WARNING_SH = "#!/usr/bin/env bash\nunused=1\necho hi\n"  # SC2034: a warning, not an error
CLEAN_SH = "#!/usr/bin/env bash\necho hi\n"

pytestmark = pytest.mark.skipif(shutil.which("shellcheck") is None, reason="needs shellcheck")


def _git(d: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=d,
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    )


def _consumer(at: Path, script: str) -> Path:
    fast_init(at)
    (at / ".gatesrc").write_text("GOH_MAX_LINES=500\n")
    (at / "run.sh").write_text(script)
    (at / "run.sh").chmod(0o755)
    _git(at, "add", "-A")
    _git(at, "commit", "-qm", "first")
    return at


def _v0573(tmp: Path, goh: Path) -> Path:
    """The base side: the native binary over a shellcheck that judges errors only, which is what
    v0.57.3 ran (`--severity=error`) -- so a warning is green on this side, as it was then."""
    shim = tmp / "v0573-path"
    shim.mkdir()
    sc = shim / "shellcheck"
    sc.write_text(
        '#!/usr/bin/env bash\nargs=()\nfor a in "$@"; do case "$a" in\n'
        '  --severity=*) args+=(--severity=error) ;;\n  *) args+=("$a") ;;\nesac; done\n'
        f'exec {shutil.which("shellcheck")} "${{args[@]}}"\n'
    )
    sc.chmod(0o755)
    base = tmp / "v0573-goh"
    base.write_text(f'#!/usr/bin/env bash\nPATH="{shim}:$PATH" exec {goh} "$@"\n')
    base.chmod(0o755)
    return base


@pytest.fixture
def estate(tmp_path: Path, goh: Path) -> dict:
    root = tmp_path / "estate"
    root.mkdir()
    state = tmp_path / "state" / "estate_audit.json"
    state.parent.mkdir()
    unseeded = _consumer(tmp_path / "consumers" / "unseeded", WARNING_SH)
    clean = _consumer(tmp_path / "consumers" / "clean", CLEAN_SH)
    # gates_of_heck itself is registered on the real machine; it is the SUBJECT, never a consumer.
    (state.parent / "installed_repos").write_text(f"{unseeded}\n{clean}\n{REPO_ROOT}\n")
    return {
        "root": root,
        "state": state,
        "unseeded": unseeded,
        "clean": clean,
        "base": _v0573(tmp_path, goh),
        "head": goh,
        "goh": goh,
    }


def _canary(e: dict, *extra: str, head: Path | None = None) -> subprocess.CompletedProcess:
    argv = [sys.executable, str(SCRIPT), "--root", str(e["root"]), "--state", str(e["state"])]
    argv += ["--base-bin", str(e["base"]), "--head-bin", str(head or e["head"])]
    argv += ["--base-label", "v0.57.3", "--head-label", "v0.57.4", "--goh", str(REPO_ROOT)]
    return subprocess.run(
        [*argv, *extra],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
        env={**os.environ, "GOH_VERDICT_DIR": str(e["root"].parent / "verdicts")},
    )


def _record(e: dict) -> dict:
    return json.loads(e["state"].read_text())["release_canary"]


def _hook():
    spec = importlib.util.spec_from_file_location("estate_advisories_canary", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_shsev_change_is_refused_naming_the_unseeded_consumer(estate):
    r = _canary(estate)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "consumers/unseeded" in out and "shell lint" in out, out
    assert "goh shell-lint --seed" in out, out
    rec = _record(estate)
    assert rec["verdict"] == "refused"
    assert [k.endswith("unseeded") for k in rec["turned_red"]] == [True], rec
    assert rec["turned_red"][next(iter(rec["turned_red"]))][0]["step"] == "shell lint"
    assert rec["consumers"] == 2, rec  # unseeded and clean; gates_of_heck itself is not one
    # Read at session start, where the owner looks.
    lines = _hook().report(estate["state"], dt.datetime.now(dt.UTC))
    said = [ln for ln in lines if "release canary" in ln]
    assert said and "unseeded" in said[0] and "goh shell-lint --seed" in said[0], lines


def test_seeding_the_consumer_clears_the_release(estate, goh):
    assert _canary(estate).returncode == 1
    seeded = subprocess.run(
        [str(goh), "shell-lint", "--seed"],
        cwd=estate["unseeded"],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr
    _git(estate["unseeded"], "add", "shell_lint_seed.json")
    _git(estate["unseeded"], "commit", "-qm", "seed")
    r = _canary(estate)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _record(estate)["verdict"] == "clear"
    lines = _hook().report(estate["state"], dt.datetime.now(dt.UTC))
    assert not [ln for ln in lines if "release canary" in ln], lines


def test_an_uncommitted_seed_is_not_what_the_push_judges(estate, goh):
    # The push gates the COMMIT: a seed written and left uncommitted clears nothing.
    subprocess.run(
        [str(goh), "shell-lint", "--seed"],
        cwd=estate["unseeded"],
        capture_output=True,
        check=True,
        stdin=subprocess.DEVNULL,
    )
    assert _canary(estate).returncode == 1


def test_a_head_side_that_cannot_judge_refuses(estate, tmp_path):
    # Fail closed: a consumer the canary could not judge is never a clear release.
    broken = tmp_path / "broken-goh"
    broken.write_text("#!/usr/bin/env bash\necho 'not a gate'\nexit 3\n")
    broken.chmod(0o755)
    r = _canary(estate, head=broken)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "could not judge" in r.stdout + r.stderr
    assert _record(estate)["errors"]


def test_a_step_red_on_both_sides_is_named_but_not_this_release(estate):
    # Red under the last release too: the consumer's own debt, said, but not this release's doing.
    r = _canary(estate, "--base-bin", str(estate["head"]))
    assert r.returncode == 0, r.stdout + r.stderr
    rec = _record(estate)
    assert rec["verdict"] == "clear" and not rec["turned_red"]
    assert [k.endswith("unseeded") for k in rec["already_red"]] == [True], rec


def test_an_empty_estate_is_not_a_clear_release(estate):
    (estate["state"].parent / "installed_repos").write_text(f"{REPO_ROOT}\n")
    r = _canary(estate)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "no consumer" in r.stdout + r.stderr


def test_no_gate_input_changed_since_the_base_sweeps_nothing(estate):
    argv = [sys.executable, str(SCRIPT), "--root", str(estate["root"])]
    argv += ["--state", str(estate["state"]), "--goh", str(REPO_ROOT), "--base", "HEAD"]
    r = subprocess.run(argv, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _record(estate)["verdict"] == "no-gate-change"


def test_the_estate_audit_keeps_the_canary_record(estate, monkeypatch):
    # Both write the one state file the SessionStart hook reads; the daily audit must not erase it.
    _canary(estate)
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "estate_audit_canary", REPO_ROOT / "scripts" / "estate_audit.py"
    )
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    (estate["state"].parent / "installed_repos").write_text("")
    audit.run(estate["root"], estate["state"], db=None, fetch=False)
    data = json.loads(estate["state"].read_text())
    assert data["release_canary"]["verdict"] == "refused"
    assert "ran_at" in data and data["advisories"] == {}


def test_a_side_is_exported_as_a_consumer_gets_it(tmp_path):
    # A gate script run from an export with no `.goh-head` walked up for a checkout, found none,
    # and judged differently from the consumer's push (sys_updater's empty-scope probe, red here and
    # green there). The export names its commit, as gates/_from_head.sh's does.
    spec = importlib.util.spec_from_file_location("release_canary_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.export(REPO_ROOT, "HEAD", tmp_path / "side") is None
    head = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout.strip()
    assert (tmp_path / "side" / ".goh-head").read_text().strip() == head
    assert (tmp_path / "side" / "gates" / "structural.sh").is_file()


def test_an_export_survives_tar_closing_the_pipe_before_git_is_done(tmp_path, monkeypatch):
    """GOH.SUITEFLAKES: bsdtar exits at the end marker without draining its input, so a writer still
    padding the record takes SIGPIPE -- exit 141, no stderr, and the canary refused a release with
    "exporting v0.96.0: " and nothing after it. This git writes its archive, then more, late."""
    real = shutil.which("git")
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "git").write_text(
        "#!/bin/bash\n"
        'for a in "$@"; do\n'
        '  if [ "$a" = archive ]; then\n'
        f'    "{real}" "$@" || exit\n'
        "    sleep 0.5\n"
        "    head -c 65536 /dev/zero || exit\n"
        "    exit 0\n"
        "  fi\n"
        "done\n"
        f'exec "{real}" "$@"\n'
    )
    (shim / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim}{os.pathsep}{os.environ['PATH']}")
    spec = importlib.util.spec_from_file_location("release_canary_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.export(REPO_ROOT, "HEAD", tmp_path / "side") is None
    assert (tmp_path / "side" / "gates" / "structural.sh").is_file()


def test_a_failed_step_always_names_its_exit_and_stderr():
    spec = importlib.util.spec_from_file_location("release_canary_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    silent = subprocess.CompletedProcess(["git"], 141, b"", b"")
    assert mod._failed("git archive", silent) == "git archive exit 141: no stderr"
    killed = subprocess.CompletedProcess(["tar"], -13, b"", b"tar: x\ntar: broken\n")
    assert mod._failed("tar -x", killed) == "tar -x signal 13: tar: x | tar: broken"
