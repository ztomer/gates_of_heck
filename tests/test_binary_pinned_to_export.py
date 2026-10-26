"""A gate runs the binary built from the commit it pinned, not whatever `bin/goh` is by then.

2026-10-17: goh v0.29.0 landed while divoom-control's push was mid-suite. The push had pinned its
gate source to the export of 8e44a2d (0.28.1, C4); the land moved the live checkout to 2432f95 and
the resolver rebuilt `bin/goh` for it (0.29.0, C3). A nested `gate.sh --full` in the suite then
judged the export's 0.28.1 manifest against the live 0.29.0 binary and refused: "the goh binary
serving this gate is BEHIND the source" -- about a binary that was AHEAD. Every consumer whose gate
spans a land goes red the same way, and monitor's did too.

The two halves disagreed because they answered to different commits: the source to the export's,
the binary to the live checkout's HEAD. Every build now also lands in a store keyed by its source
stamp (`bin/by-stamp/<sha1 of the stamp>/goh`), and a gate running from an export takes the
binary of the export's commit from there (building it, `build-goh.sh --rev`, when no gate has yet).
The answer a parent hands its children (GOH_RESOLVED_*) names the pin too, and is the store's
immutable copy, never the `bin/goh` path a land rewrites under it.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from test_binary_source_identity import _fake, _head_tree, _no_store, _version, private_store
from test_gate_environment import _clean_checkout_of_todays_gates

STORE = Path("bin") / "by-stamp"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout.strip()


def _stored(checkout: Path, stamp: str) -> Path:
    return checkout / STORE / hashlib.sha1(stamp.encode()).hexdigest() / "goh"


class Land:
    """The incident's state: an export pinned at A, the live checkout landed to B."""

    def __init__(self, tmp_path: Path) -> None:
        self.checkout = _clean_checkout_of_todays_gates(tmp_path)
        _no_store(self.checkout)
        self.a = _git(self.checkout, "rev-parse", "HEAD")
        self.a_version, self.a_stamp = _version(self.checkout), _head_tree(self.checkout)
        self.export = tmp_path / "head" / self.a
        self.export.mkdir(parents=True)
        archive = subprocess.run(
            ["git", "-C", str(self.checkout), "archive", self.a],
            capture_output=True,
            check=True,
            stdin=subprocess.DEVNULL,
        ).stdout
        subprocess.run(
            ["tar", "-x", "-C", str(self.export)],
            input=archive,
            check=True,
            capture_output=True,
        )
        (self.export / ".goh-head").write_text(self.a + "\n")
        (self.export / ".goh-used").write_text("")
        manifest = self.checkout / "Cargo.toml"
        manifest.write_text(
            manifest.read_text().replace(f'version = "{self.a_version}"', 'version = "99.0.0"', 1)
        )
        _git(self.checkout, "commit", "-q", "-am", "the land")
        self.b_stamp = _head_tree(self.checkout)
        assert self.b_stamp != self.a_stamp, "B must be another build, or this proves nothing"
        self.live_bin = _fake(self.checkout / "bin" / "goh", "99.0.0", self.b_stamp)
        self.live_bin.write_text(self.live_bin.read_text().replace("fake native", "B's binary"))
        self.calls = self.checkout.parent / "builder.calls"
        # The builder, for a `--rev` build: lands A's binary in the store, as build-goh.sh does.
        (self.checkout / "scripts" / "build-goh.sh").write_text(
            "#!/bin/bash\n"
            f'echo "$*" >> "{self.calls}"\n'
            '[ "$1" = --rev ] || exit 1\n'
            f'dest="{self.checkout}/{STORE}/$(printf %s "{self.a_stamp}" | shasum | cut -c1-40)"\n'
            'mkdir -p "$dest"\n'
            "cat > \"$dest/goh\" <<'EOF'\n"
            f'#!/bin/bash\n[ "$1" = --version ] && {{ echo "goh {self.a_version}"; exit 0; }}\n'
            f'[ "$1" = source-tree ] && {{ echo "{self.a_stamp}"; exit 0; }}\n'
            'echo "A\'s binary ran"\nexit 0\nEOF\n'
            'chmod +x "$dest/goh"\n'
        )

    def seed_a(self) -> None:
        """A's binary already in the store: what the gate's own first resolution left there."""
        a_bin = _fake(_stored(self.checkout, self.a_stamp), self.a_version, self.a_stamp)
        a_bin.write_text(a_bin.read_text().replace("fake native structural ran", "A's binary ran"))

    def gate(self, repo: Path, **extra: str) -> subprocess.CompletedProcess:
        """`structural.sh` from the export, as a consumer's nested gate runs it mid-push, in
        `repo` (the suite's template consumer) with one file staged."""
        (repo / "a.py").write_text("x = 1\n")
        _git(repo, "add", "a.py")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GOH_", "PYTEST_"))}
        env.pop("GATES_OF_HECK_SUITE", None)
        env.update(
            GOH_DIR=str(self.export),
            GOH_LIVE_ROOT=str(self.checkout),
            GOH_HEAD_CACHE=str(self.export.parent),
            GOH_BIN_STORE=str(private_store(self.checkout)),
            **extra,
        )
        return subprocess.run(
            ["bash", str(self.export / "gates" / "structural.sh"), "--staged"],
            cwd=repo,
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
            check=False,
            stdin=subprocess.DEVNULL,
        )


def _ran_a(got: subprocess.CompletedProcess) -> None:
    out = got.stdout + got.stderr
    assert "A's binary ran" in got.stdout, out
    assert "B's binary" not in out and "does NOT match" not in out and "BEHIND" not in out, out


def test_a_gate_pinned_at_a_runs_as_binary_after_the_land(tmp_path: Path, repo: Path) -> None:
    land = Land(tmp_path)
    land.seed_a()
    _ran_a(land.gate(repo))
    assert not land.calls.exists(), "A's binary was in the store; nothing should have been built"


def test_a_pin_no_gate_has_built_is_built_into_the_store(tmp_path: Path, repo: Path) -> None:
    land = Land(tmp_path)
    _ran_a(land.gate(repo))
    assert land.calls.read_text().split() == ["--rev", land.a], land.calls.read_text()


def test_a_parents_answer_for_another_pin_is_not_taken(tmp_path: Path, repo: Path) -> None:
    """The cache path of the incident: the push's parent resolved `bin/goh` and exported it; the
    land rewrote that path; the child took the answer while the path was still executable."""
    land = Land(tmp_path)
    land.seed_a()
    _ran_a(
        land.gate(
            repo,
            GOH_RESOLVED_FOR=f"{land.checkout.resolve()}|live:",
            GOH_RESOLVED_BIN=str(land.live_bin),
        )
    )


def test_the_live_head_gate_answers_with_the_stores_copy(tmp_path: Path) -> None:
    """Unpinned (the live checkout itself), a current `bin/goh` is adopted into the store and the
    STORE's path is the answer: a land rewriting `bin/goh` cannot change what a child then runs."""
    checkout = _clean_checkout_of_todays_gates(tmp_path)
    _no_store(checkout)
    stamp = _head_tree(checkout)
    _fake(checkout / "bin" / "goh", _version(checkout), stamp)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GOH_", "PYTEST_"))}
    env["GOH_LIVE"] = "1"
    env["GOH_BIN_STORE"] = str(private_store(checkout))
    got = subprocess.run(
        [
            "bash",
            "-c",
            f'. "{checkout}/gates/_goh_bin.sh"; goh_resolve_native; printf %s "$goh_native"',
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert Path(got.stdout).resolve() == _stored(checkout, stamp).resolve(), got.stdout + got.stderr
    assert _stored(checkout, stamp).read_bytes() == (checkout / "bin" / "goh").read_bytes()
    # The store is build output: a checkout that has one still reads clean (.gitignore).
    assert not _git(checkout, "status", "--porcelain"), _git(checkout, "status", "--porcelain")
