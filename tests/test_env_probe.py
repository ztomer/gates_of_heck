"""tests/env_probe.py captures names and the asked-for values, never the rest (GOH.ENVDUMP)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from env_probe import REDACTED, probe_sh, read_probe, redacted

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
