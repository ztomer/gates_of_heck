"""A checkout whose pre-push hook is goh's shim reads as push-gated, through the ONE reader every
asker uses (GOH.SHIMPUSHGATED).

The release kit's CI step, goh ship's verify and .loop's `loopctl done` each grepped the hook for
`push_gate.sh`. The stock hook became a shim that execs gates/hook.sh (GOH.CLASS.ROLLOUT, 7223a9e)
and names no gate, so every shimmed checkout read as ungated: goh v0.119.0's ship failed verify
with "this checkout's pre-push hook is not goh's push gate" while its CI was pending. The grep also
passed a hook that only MENTIONS the name. lib/push_gated.py judges the bytes; these tests drive the
kit and the ship through it (red on 14195ca) and pin what it must keep reading ungated.
"""

from __future__ import annotations

import base64
import hashlib
import subprocess
import sys
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import push_gated as gated
import ship_verify

sys.path.pop(0)

# The stamped 2468a52 pre-push (sha256 6a6f6a6b, in 21 repos on 2026-11-15), base64: its own
# marks are data, never this tree's TUI (the self-walk reads tests/fixtures).
LEGACY = REPO_ROOT / "tests" / "fixtures" / "push_gated" / "pre-push-2468a52.b64"
# A hook that is not goh's but names its push gate: what the three greps read as gated.
MENTIONS = '#!/bin/sh\n# exec "$GOH_DIR/gates/push_gate.sh" (a stand-in that passes)\nexit 0\n'
FOREIGN = "#!/bin/sh\nexec npx husky run pre-push\n"
# The first stock pre-push (73391f0) ran the repo's gate in the working tree, not push_gate.sh.
BEFORE_PUSH_GATE = {"87c60cd19cb13dc32c581cddca2cb305f91f972aba343ada2750bc2bfc2e761a"}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env=hermetic_env(drop_git=True), stdin=subprocess.DEVNULL)  # fmt: skip


def _checkout(tmp_path: Path, hook: bytes | None, hooks_path: str | None = ".githooks") -> Path:
    """A checkout on a GitHub-named remote whose resolved pre-push hook holds HOOK."""
    repo = tmp_path / "proj"
    fast_init(repo)
    _git(repo, "remote", "add", "origin", "https://github.com/o/n.git")
    if hooks_path:
        _git(repo, "config", "core.hooksPath", hooks_path)
    if hook is not None:
        path = repo / (hooks_path or ".git/hooks") / "pre-push"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(hook)
        path.chmod(0o755)
    return repo


def _kit_reads(repo: Path) -> str:
    """What the release kit's CI step reads: `ci_push_gated`, sourced as release.sh sources it."""
    script = '. "$GOH/tools/release-kit/ci_step.sh"; ci_push_gated'
    r = subprocess.run(["bash", "-c", script], cwd=repo, capture_output=True, text=True,
                       env=hermetic_env(drop_git=True, GOH=str(REPO_ROOT)), check=False,
                       stdin=subprocess.DEVNULL, timeout=patience(60))  # fmt: skip
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _ship_reads(repo: Path, tmp_path: Path) -> tuple[bool, str]:
    """What goh ship's verify decides over a PENDING hosted run (`loopctl ci` exits 3)."""
    fake = tmp_path / "loopctl.py"
    fake.write_text("import sys\nsys.exit(3)\n")
    return ship_verify.ci_read(repo, "origin", str(fake), sha="a" * 40)


@pytest.fixture
def shimmed(tmp_path: Path) -> Path:
    return _checkout(tmp_path, gated.shim())


def test_a_shimmed_checkout_reads_push_gated_in_the_kit(shimmed):
    assert _kit_reads(shimmed) == ".githooks/pre-push"


def test_a_shimmed_checkout_passes_the_ships_verify_advisory(shimmed, tmp_path):
    passes, why = _ship_reads(shimmed, tmp_path)
    assert passes, why
    assert "advisory" in why and ".githooks/pre-push" in why, why


def test_a_legacy_stamped_hook_still_reads_push_gated(tmp_path):
    """Consumers keep the stamped 2468a52 pre-push until their hooks are refreshed."""
    data = base64.b64decode(LEGACY.read_text())
    assert hashlib.sha256(data).hexdigest() in gated.retired_push_gates()
    repo = _checkout(tmp_path, data)
    assert gated.push_gated(repo) == ".githooks/pre-push"
    assert _kit_reads(repo) == ".githooks/pre-push"


def test_the_common_hooks_dir_is_read_when_no_hooks_path_is_set(tmp_path):
    repo = _checkout(tmp_path, gated.shim(), hooks_path=None)
    assert gated.push_gated(repo) == ".git/hooks/pre-push"


@pytest.mark.parametrize(
    "hook", [MENTIONS.encode(), FOREIGN.encode(), b""], ids=["mentions", "foreign", "empty"]
)
def test_a_hook_that_is_not_gohs_reads_ungated_everywhere(tmp_path, hook):
    repo = _checkout(tmp_path, hook)
    assert gated.push_gated(repo) == ""
    assert _kit_reads(repo) == ""
    passes, why = _ship_reads(repo, tmp_path)
    assert not passes and "not goh's push gate" in why, why


def test_no_hook_reads_ungated(tmp_path):
    assert gated.push_gated(_checkout(tmp_path, None)) == ""


def test_an_edited_shim_reads_ungated(tmp_path):
    """Nothing says what an edited hook runs, even one that still execs hook.sh."""
    repo = _checkout(tmp_path, gated.shim() + b"echo extra\n")
    assert gated.push_gated(repo) == ""


def test_every_retired_pre_push_is_classified():
    """A retired stock pre-push either ran the push gate (tagged `push-gate`) or is named here: a
    shim ABI move that forgets the tag would turn every un-refreshed consumer ungated."""
    lines = (REPO_ROOT / "retired_hooks.sha256").read_text(encoding="utf-8").splitlines()
    pre_push = {p[0] for ln in lines if not ln.startswith("#") and len(p := ln.split()) >= 2
                and p[1] == "pre-push"}  # fmt: skip
    assert pre_push - gated.retired_push_gates() == BEFORE_PUSH_GATE
    assert gated.retired_push_gates() <= pre_push


def _tracked_code() -> list[str]:
    out = subprocess.run(["git", "-C", str(REPO_ROOT), "ls-files"], capture_output=True, text=True,
                         check=True, env=hermetic_env(drop_git=True),
                         stdin=subprocess.DEVNULL).stdout.split()  # fmt: skip
    return [f for f in out if not f.startswith("tests/")]


def test_no_reader_greps_a_hook_for_a_gate_scripts_name():
    """The class: whether a hook runs goh's gate is lib/push_gated.py's to say, from the bytes."""
    assert gated.name_greps(REPO_ROOT, _tracked_code()) == []


def test_the_name_grep_check_sees_the_three_readers_it_replaced(tmp_path):
    """Calibration: the shapes ci_step.sh, ship_verify.py and loopadvisory.py had at 14195ca."""
    shapes = {
        "ci_step.sh": '[ -f "$hook" ] && grep -q \'push_gate\\.sh\' "$hook"\n',
        "ship_verify.py": 'return rel if hook and PUSH_GATE in hook.read_text() else ""\n',
        "loopadvisory.py": 'return rel if "push_gate.sh" in hook.read_text() else ""\n',
        "fine.sh": '# grep -q push_gate.sh, as it was\nexec bash "$GOH/gates/push_gate.sh"\n',
        "excused.py": 'ok = "push_gate.sh" in out  # hook-bytes-ok: a test of gate output\n',
        "notes.md": "grep for push_gate.sh in the hook\n",
    }
    for name, text in shapes.items():
        (tmp_path / name).write_text(text)
    found = gated.name_greps(tmp_path, sorted(shapes))
    assert [f.split(":", 1)[0] for f in found] == [
        "ci_step.sh",
        "loopadvisory.py",
        "ship_verify.py",
    ]
