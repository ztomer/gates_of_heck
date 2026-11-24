"""tests/env_probe.py captures names and the asked-for values, never the rest (GOH.ENVDUMP)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from conftest import hermetic_env
from env_probe import REDACTED, print_safe, probe_sh, read_probe, redacted

SECRET = "not-a-real-secret-0b5e"  # a stand-in value; the assertions name it, never print it


def _run(script: str) -> None:
    env = {"PATH": "/usr/bin:/bin", "THE_TOKEN": SECRET, "KEPT": "kept value"}
    subprocess.run(["/bin/sh", "-c", script], env=env, check=True,
                   stdin=subprocess.DEVNULL, timeout=60)  # fmt: skip


def test_the_secret_is_in_the_probed_environment(tmp_path: Path) -> None:
    """The control: the stand-in secret IS in the environment, so its absence below is a finding."""
    out = tmp_path / "token"
    _run(f'printf %s "$THE_TOKEN" > {out}')
    assert SECRET in out.read_text()


def test_the_probe_writes_names_and_only_the_kept_values(tmp_path: Path) -> None:
    out = tmp_path / "seen.env"
    _run(probe_sh(out, keep=("KEPT", "ABSENT")))
    text = out.read_text()
    assert SECRET not in text, "the probe wrote a value nobody asked for"
    seen = read_probe(out)
    assert seen["KEPT"] == "kept value"
    assert seen["THE_TOKEN"] == REDACTED and seen["PATH"] == REDACTED
    assert "ABSENT" not in seen, "a kept name that is unset is absent, not empty"
    assert not (tmp_path / "seen.env.tmp").exists(), "written whole, then moved into place"


def test_redacted_keeps_names_and_the_asked_for_values() -> None:
    got = redacted({"THE_TOKEN": SECRET, "KEPT": "v"}, keep=("KEPT",))
    assert got == {"THE_TOKEN": REDACTED, "KEPT": "v"}


@pytest.mark.parametrize("bad", ["A B", "A;rm", "$X", ""])
def test_a_kept_name_that_is_not_a_name_is_refused(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        probe_sh(tmp_path / "x", keep=(bad,))


def test_an_inherited_name_that_is_not_a_shell_name_prints_redacted_not_raises() -> None:
    """A parent may export `SENTRY-TRACE` (a Claude Code agent's shell does): a test that passes
    its whole environment as overrides kept it, and print_safe raised, so every such test went red
    in that shell alone. It only prints, so the name shows with no value."""
    env = print_safe({"SENTRY-TRACE": SECRET, "KEPT": "v"}, keep=("SENTRY-TRACE", "KEPT"))
    assert env["SENTRY-TRACE"] == SECRET, "the child still gets it"
    assert SECRET not in repr(env) and "'KEPT': 'v'" in repr(env)


def test_redacted_takes_an_inherited_name_that_is_not_a_shell_name() -> None:
    """GOH.HYPHENENV, print_safe's sibling: `redacted` builds a dict and writes no shell line, so a
    kept `SENTRY-TRACE` is redacted like any unkept name, never raised on."""
    got = redacted({"SENTRY-TRACE": SECRET, "KEPT": "v"}, keep=("SENTRY-TRACE", "KEPT"))
    assert got == {"SENTRY-TRACE": REDACTED, "KEPT": "v"}


def test_hermetic_env_takes_a_whole_inherited_environment_with_a_hyphenated_name(
    monkeypatch,
) -> None:
    """GOH.HYPHENENV: an agent's shell exports SENTRY-TRACE, and tests that hand hermetic_env their
    whole environment as overrides (tests/test_land_prewait.py) went red at every pre-step enqueued
    from such a shell. The child still gets the variable (print_safe's test pins how it prints)."""
    monkeypatch.setenv("SENTRY-TRACE", SECRET)
    env = hermetic_env(**hermetic_env())  # the whole inherited environment, as overrides
    got = subprocess.run(["/usr/bin/printenv", "SENTRY-TRACE"], env=env, capture_output=True,
                         text=True, check=False, stdin=subprocess.DEVNULL)  # fmt: skip
    assert got.returncode == 0 and got.stdout.strip() == SECRET, "the child gets it"
