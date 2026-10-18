"""lib/gate_slot.sh -- at most N gates run their heavy steps at once, machine-wide.

2026-10-18: several sessions pushed at once with no limit, and two pushes failed on the box's load
rather than on their changes (zinc killed mid-test, exit 144; games' Release rebuild out of its
900 s step limit at a 15-minute load average of 49.6). These tests run the real shell library and
the real push gate against a private slot directory (conftest's host_locks fixture), with fake
steps that wait on files, never on the clock:

  * N+1 gates with N slots: exactly one waits, and starts when a holder ends;
  * a SIGKILLed holder frees its slot (the kernel drops the flock with the holder's last fd);
  * the step limits count from the slot, not from the push;
  * a gate nested in a slot holder never waits on its parent's slot -- by GOH_GATE_SLOT, and by
    ancestry when a test environment stripped it;
  * the wait names the holders: repo, pid and since when.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from test_push_gate import PUSH_GATE, _git, _repo
from timing_bounds import GiveUp, assert_sooner

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "lib" / "gate_slot.sh"
HELPER = REPO_ROOT / "lib" / "gate_slot.py"
TUI = REPO_ROOT / "tui" / "lib.sh"

# A gate: takes a slot, says so, then holds it until its release file appears.
GATE = """
set -euo pipefail
GOH="{goh}"; . "$GOH/tui/lib.sh"; . "$GOH/lib/gate_slot.sh"
trap gate_slot_release EXIT
gate_slot_acquire "{name}"
echo "TAKEN pid=$$ slot=${{GOH_GATE_SLOT:-none}}"
{body}
echo DONE
"""
HOLD_UNTIL = 'while [ ! -f "{release}" ]; do sleep 0.05; done'


def _env(tmp_path: Path, slots: int | str | None, **extra: str) -> dict[str, str]:
    env = dict(os.environ, GOH_DIR=str(REPO_ROOT), NO_COLOR="1", **extra)
    env["GOH_GATE_SLOT_DIR"] = str(tmp_path / "slots")
    env.pop("GOH_GATE_SLOT", None)
    if slots is None:
        env.pop("GOH_GATE_SLOTS", None)
    else:
        env["GOH_GATE_SLOTS"] = str(slots)
    return env


class Gate:
    """A fake gate in its own process group, its output in a file the test polls."""

    def __init__(self, tmp_path: Path, name: str, env: dict[str, str], body: str = "") -> None:
        self.name = name
        self.release = tmp_path / f"release-{name}"
        self.log = tmp_path / f"{name}.out"
        script = GATE.format(
            goh=REPO_ROOT, name=name, body=body or HOLD_UNTIL.format(release=self.release)
        )
        with open(self.log, "w") as out:
            self.proc = subprocess.Popen(
                ["bash", "-c", script],
                stdout=out,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
            )

    def out(self) -> str:
        return self.log.read_text()

    def wait_for(self, needle: str, seconds: float = 20) -> str:
        give_up = GiveUp(seconds)
        while not give_up.passed():
            text = self.out()
            if needle in text:
                return text
            if self.proc.poll() is not None and needle not in self.out():
                raise AssertionError(f"{self.name} exited {self.proc.returncode}: {self.out()!r}")
            time.sleep(0.05)
        raise AssertionError(f"{self.name}: {needle!r} not seen; got {self.out()!r}")

    def finish(self) -> int:
        self.release.touch()
        return self.proc.wait(timeout=60)

    def kill(self) -> None:
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.proc.wait(timeout=30)


def test_n_plus_one_gates_on_n_slots_one_waits_until_a_holder_ends(tmp_path):
    env = _env(tmp_path, 2)
    gates = [Gate(tmp_path, n, env) for n in ("alpha", "beta")]
    try:
        for g in gates:
            g.wait_for("TAKEN")
        third = Gate(tmp_path, "gamma", env)
        gates.append(third)
        third.wait_for("all 2 gate slots are held")
        time.sleep(0.5)  # long enough to have taken a free slot, had there been one
        assert "TAKEN" not in third.out(), "a third gate ran beside two holders of two slots"
        assert gates[0].finish() == 0
        third.wait_for("TAKEN")
        assert "TAKEN" in gates[1].out() and gates[1].proc.poll() is None
        assert third.finish() == 0 and gates[1].finish() == 0
    finally:
        for g in gates:
            g.kill()


def test_the_wait_names_each_holder_and_its_end_says_how_long(tmp_path):
    env = _env(tmp_path, 1)
    holder = Gate(tmp_path, "zinc-like", env)
    try:
        holder.wait_for("TAKEN")
        waiter = Gate(tmp_path, "games-like", env, body="true")
        text = waiter.wait_for("all 1 gate slots are held")
        line = next(ln for ln in text.splitlines() if "slot-0" in ln)
        assert "zinc-like" in line, line  # the repo
        assert f"pid {holder.proc.pid}" in line, line  # the holder's shell
        assert "since " + time.strftime("%Y-%m-%d") in line, line  # since when
        time.sleep(1.2)
        holder.finish()
        done = waiter.wait_for("DONE")
        assert "taken after " in done and "s waiting" in done, done
        assert waiter.proc.wait(timeout=30) == 0
    finally:
        holder.kill()


def test_a_sigkilled_holder_frees_its_slot(tmp_path):
    """No trap runs on SIGKILL. The holder process dies with the group and the kernel drops its lock."""
    env = _env(tmp_path, 1)
    holder = Gate(tmp_path, "holder", env, body="sleep 600")
    try:
        holder.wait_for("TAKEN")
        waiter = Gate(tmp_path, "waiter", env, body="true")
        waiter.wait_for("all 1 gate slots are held")
        t0 = time.monotonic()
        holder.kill()
        waiter.wait_for("DONE", 60)
        assert_sooner(
            time.monotonic() - t0, 1, 600, "a slot freed by a SIGKILL, not the holder's end"
        )
    finally:
        holder.kill()


def test_a_holder_whose_shell_alone_is_killed_frees_its_slot(tmp_path):
    """The holder watches the gate's shell: killed without its group, the slot still comes back."""
    env = _env(tmp_path, 1)
    holder = Gate(tmp_path, "holder", env, body="sleep 600")
    try:
        holder.wait_for("TAKEN")
        waiter = Gate(tmp_path, "waiter", env, body="true")
        waiter.wait_for("all 1 gate slots are held")
        os.kill(holder.proc.pid, signal.SIGKILL)  # the shell only; its `sleep 600` lives on
        waiter.wait_for("DONE", 60)
    finally:
        holder.kill()


def test_a_nested_gate_does_not_wait_on_its_parents_slot(tmp_path):
    env = _env(tmp_path, 1)
    inner = GATE.format(goh=REPO_ROOT, name="inner", body="true")
    (tmp_path / "inner.sh").write_text(inner)
    # By the variable the parent exports, and by ancestry once it is stripped (hermetic test envs).
    body = f'bash "{tmp_path}/inner.sh"; env -u GOH_GATE_SLOT bash "{tmp_path}/inner.sh"'
    outer = Gate(tmp_path, "outer", env, body=body)
    try:
        assert outer.proc.wait(timeout=60) == 0, outer.out()
        text = outer.out()
        assert text.count("inside a gate that holds slot-0") == 2, text
        assert "GOH_GATE_SLOT)" in text and "recorded holder above this gate" in text, text
    finally:
        outer.kill()


def test_a_slot_variable_naming_a_non_ancestor_bypasses_nothing(tmp_path):
    env = _env(tmp_path, 1)
    holder = Gate(tmp_path, "holder", env)
    try:
        taken = holder.wait_for("TAKEN")
        slot = taken.split("slot=", 1)[1].splitlines()[0]
        assert slot.endswith(f" {holder.proc.pid}"), slot
        waiter = Gate(tmp_path, "stranger", dict(env, GOH_GATE_SLOT=slot), body="true")
        waiter.wait_for("all 1 gate slots are held")
        assert "inside a gate" not in waiter.out()
        holder.finish()
        waiter.wait_for("DONE")
    finally:
        holder.kill()


def test_zero_slots_is_off_and_garbage_is_refused(tmp_path):
    off = subprocess.run(
        ["bash", "-c", GATE.format(goh=REPO_ROOT, name="x", body="true")],
        env=_env(tmp_path, 0),
        check=False,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    assert off.returncode == 0 and "gate slots: off" in off.stdout, off.stdout + off.stderr
    bad = subprocess.run(
        ["bash", "-c", GATE.format(goh=REPO_ROOT, name="x", body="true")],
        env=_env(tmp_path, "four"),
        check=False,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    assert bad.returncode == 2 and "GOH_GATE_SLOTS must be" in bad.stderr, bad.stderr


def test_the_default_is_one_slot_per_four_cores():
    got = subprocess.run(
        ["python3", str(HELPER), "default"],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout.strip()
    assert int(got) == max(1, (os.cpu_count() or 1) // 4)


# --- through the push gate ----------------------------------------------------------------------


def _push_proc(repo: Path, tmp_path: Path, env: dict[str, str], log: Path) -> subprocess.Popen:
    sha = _git(repo, "rev-parse", "HEAD")
    ref = f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n"
    (tmp_path / f"{log.stem}.refs").write_text(ref)
    with open(log, "w") as out, open(tmp_path / f"{log.stem}.refs") as refs:
        return subprocess.Popen(
            ["bash", str(PUSH_GATE)],
            cwd=repo,
            env=dict(
                env,
                GATE_REPORT=str(tmp_path / "report.txt"),
                GOH_PUSH_LOGS=str(tmp_path / "push-logs"),
                GOH_PUSH_WORKTREES=str(tmp_path / "exports"),
            ),
            stdin=refs,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def test_a_push_waits_for_a_slot_and_its_step_limit_counts_from_the_slot(tmp_path):
    """local_ci's step ceiling (GOH_LCI_TIMEOUT=2) is shorter than the wait (>2 s) plus the step
    (0.5 s): the push is green only if the ceiling starts when the slot is taken."""
    env = _env(tmp_path, 1)
    holder = Gate(tmp_path, "holder", env)
    repo = _repo(tmp_path)
    (repo / "tools" / "gate.sh").write_text(
        '#!/usr/bin/env bash\nexec "$GOH_DIR/gates/local_ci.sh" --steps-only --step "sleep 0.5" .\n'
    )
    env["GOH_LCI_TIMEOUT"] = "2"
    _git(repo, "commit", "-qam", "a bounded step")
    try:
        holder.wait_for("TAKEN")
        log = tmp_path / "push.log"
        push = _push_proc(repo, tmp_path, env, log)
        give_up = GiveUp(30)
        while "all 1 gate slots are held" not in log.read_text():
            assert push.poll() is None and not give_up.passed(), log.read_text()
            time.sleep(0.05)
        time.sleep(2.5)  # the wait alone outlasts the step's ceiling
        holder.finish()
        assert push.wait(timeout=120) == 0, log.read_text()
        text = log.read_text()
        assert "TIMED OUT" not in text, text
        waited = int(text.split("taken after ", 1)[1].split("s waiting", 1)[0])
        assert waited >= 2, text
    finally:
        holder.kill()


def test_a_push_gate_nested_in_a_push_gate_does_not_deadlock(tmp_path):
    """A gate that runs another repo's push gate (a metarepo, land.sh -> push gate), on ONE slot."""
    env = _env(tmp_path, 1)
    inner_root = tmp_path / "inner"
    inner_root.mkdir()
    inner = _repo(inner_root)
    outer_root = tmp_path / "outer"
    outer_root.mkdir()
    outer = _repo(outer_root)
    sha = _git(inner, "rev-parse", "HEAD")
    (outer / "tools" / "gate.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        f"cd '{inner}'\n"
        f"printf 'refs/heads/main {sha} refs/heads/main {'0' * 40}\\n' | "
        f"GOH_PUSH_WORKTREES='{tmp_path}/inner-exports' bash '{PUSH_GATE}'\n"
    )
    _git(outer, "commit", "-qam", "runs the inner push gate")
    log = tmp_path / "outer.log"
    push = _push_proc(outer, tmp_path, env, log)
    try:
        assert push.wait(timeout=120) == 0, log.read_text()
        assert "inside a gate that holds slot-0" in log.read_text(), log.read_text()
    finally:
        try:
            os.killpg(push.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_the_push_gate_takes_a_slot_before_the_export():
    gate = (REPO_ROOT / "gates" / "push_gate.sh").read_text()
    take = gate.index('gate_slot_acquire "$(basename "$root")"')
    assert take < gate.index('git -C "$root" worktree add --detach'), (
        "the export ran before the slot"
    )
    cleanup = gate[gate.index("cleanup() {") : gate.index("trap cleanup EXIT")]
    assert "gate_slot_release" in cleanup, "a slot not released on every exit path"


def test_a_hermetic_child_keeps_the_private_machine_locks():
    """hermetic_env drops GOH_* opt-ins; a lock DIRECTORY is not one. Dropped, every hermetic child
    (land.sh's tests, a fixture push) reached the real bench lock and the real gate slots."""
    from conftest import hermetic_env
    from host_locks import HOST_LOCKS

    env = hermetic_env(drop_git=True)
    for name in HOST_LOCKS:
        assert env.get(name) == os.environ[name], name
