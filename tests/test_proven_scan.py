"""A gate reads the proven records' identity sidecars once, in one process (GOH.PROVENSCAN).

`proven_note_identity` (gates/_proven_why.sh) read every sidecar after EVERY recorded step, a `cat`,
`grep`, `shasum` and `cut` each: with 420 sidecars 6.9 s a record, 66-84 s of a ten-step gate after
its last step ended, under the land lock. What is pinned:

  * the spawns of a note do not grow with the sidecars kept;
  * two notes of one gate scan once, and a second gate scans again;
  * the prune stayed: a sidecar with no record goes, and a manifest no kept sidecar stands for;
  * a record's own sidecar and the manifest the bash names for it stay (one formula, two readers);
  * a manifest is listed before the sidecars, so another gate's note in between keeps both.

No repository: `proven_dir` and the identity are functions here, the store a few small files.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
import proven_prune

IDENTITY = "goh content {0}\npythonpath goh {0}\ngoh tree {0}\nenv CI unset\n"
# Each external the note runs is a function that says so on fd 7, then runs the real one.
PROBE = """
for c in cat grep shasum sha256sum cut mkdir mv rm; do
    eval "$c() { printf '%s\\\\n' $c >&7; command $c \\"\\$@\\"; }"
done
py() { printf 'py\\n' >&7; command "$REAL_PY" "$@"; }
GOH_PY=py
goh_bind_hook_index() { :; }
proven_dir() { printf '%s\\n' "$STORE"; }
proven_identity() { printf 'goh content %s\\nenv CI unset\\n' "$WHAT"; }
_proven_manifest() { printf 'sha file gates/x.sh\\n'; }
_proven_closure_on() { return 1; }
"""


def _note(store: Path, calls: str) -> list[str]:
    """The externals `calls` ran, sorted (a pipeline starts its commands in no order)."""
    r, w = os.pipe()
    env = hermetic_env(drop_git=True, STORE=str(store), WHAT="now", REAL_PY=sys.executable)
    head = f'. "{REPO_ROOT}/gates/_hash.sh"; . "{REPO_ROOT}/gates/_proven.sh"\n'
    with os.fdopen(r, "rb") as pipe:
        try:
            p = subprocess.run(
                ["bash", "-c", f"exec 7>&{w}\n{head}{PROBE}{calls}"], env=env, pass_fds=(w,),
                capture_output=True, text=True, stdin=subprocess.DEVNULL,
                timeout=patience(60), check=False,
            )  # fmt: skip
        finally:
            os.close(w)
        assert p.returncode == 0 and not p.stderr, p.stdout + p.stderr
        return sorted(pipe.read().decode().split())


def _store(tmp_path: Path, live: int) -> Path:
    """A store of `live` records with their sidecars, all of one goh content."""
    store = tmp_path / f"s{live}"
    (store / ".id").mkdir(parents=True)
    for i in range(live):
        (store / f"k{i}").write_text("epoch=1\n")
        (store / ".id" / f"k{i}").write_text(IDENTITY.format("one"))
    (store / "new").write_text("epoch=1\n")
    return store


def _mkey(*lines: str) -> str:
    return "m-" + hashlib.sha256("".join(x + "\n" for x in lines).encode()).hexdigest()


def test_a_notes_spawns_do_not_grow_with_the_sidecars(tmp_path: Path) -> None:
    few = _note(_store(tmp_path, 1), "proven_note_identity new step")
    many = _note(_store(tmp_path, 6), "proven_note_identity new step")
    assert many == few, (few, many)
    assert few.count("py") == 1, few


def test_a_gate_scans_once_and_the_next_gate_again(tmp_path: Path) -> None:
    store = _store(tmp_path, 2)
    three = "for i in 1 2 3; do proven_note_identity new step; done"
    assert _note(store, three).count("py") == 1
    # A subshell of the gate is another process: no record of the parent's scan is trusted there.
    twice = "proven_note_identity new step; ( proven_note_identity new step )"
    assert _note(store, twice).count("py") == 2
    assert _note(store, "proven_note_identity new step").count("py") == 1


def test_a_note_still_prunes_what_no_record_needs(tmp_path: Path) -> None:
    store = _store(tmp_path, 1)
    ids = store / ".id"
    (ids / "dead").write_text(IDENTITY.format("two"))
    (ids / _mkey("goh content two", "goh tree two")).write_text("x")
    (ids / ".tmp.9").write_text("in flight")
    other = _mkey("goh content one", "goh tree one")  # a live record's
    (ids / other).write_text("x")
    _note(store, "proven_note_identity new step")
    own = _mkey("goh content now")  # named by the bash, kept by the Python: one formula
    assert {p.name for p in ids.iterdir()} == {"k0", "new", own, other, ".tmp.9"}
    assert (ids / "new").read_text() == "goh content now\nenv CI unset\n"
    assert (ids / own).read_text() == "sha file gates/x.sh\n"


def test_a_failed_prune_is_said_and_the_note_stands(tmp_path: Path) -> None:
    store = _store(tmp_path, 1)
    said = 'out=$(proven_note_identity new step 2>&1) && [[ "$out" == "$W"*"could not prune"* ]]'
    _note(store, f"unset -f warn; py() {{ return 1; }}; W=proven; {said}")
    _note(store, f'warn() {{ echo "warned $*"; }}; py() {{ return 1; }}; W=warned; {said}')
    assert (store / ".id" / "new").exists()


def test_a_sidecar_gone_since_it_was_listed_dooms_nothing_else() -> None:
    texts = {"a": b"goh x\n", "b": None}
    mine = proven_prune.manifest_key(b"goh x\n")
    got = proven_prune.doomed([mine, "m-0"], ["a", "b", "c"], lambda n: n != "c", texts.get)
    assert got == ["c", "m-0"]


def test_an_identity_with_no_goh_line_has_the_bash_name() -> None:
    assert proven_prune.manifest_key(b"env CI unset\n") == _mkey()


def test_another_gates_note_between_the_two_listings_keeps_its_manifest(monkeypatch) -> None:
    """Its sidecar is written before its manifest, so the manifests are listed first."""
    store = {"a": b"goh a\n", proven_prune.manifest_key(b"goh a\n"): b""}
    gone: list[str] = []

    def listed(_ids: str, *, manifests: bool) -> list[str]:
        names = [n for n in store if n.startswith("m-") is manifests]
        store.update({"b": b"goh b\n", proven_prune.manifest_key(b"goh b\n"): b""})
        return names

    monkeypatch.setattr(proven_prune, "_listed", listed)
    monkeypatch.setattr(proven_prune.os.path, "isfile", lambda _p: True)
    monkeypatch.setattr(proven_prune.os, "unlink", lambda p: gone.append(os.path.basename(p)))
    monkeypatch.setattr(proven_prune, "_read", lambda _ids, n: store[n])
    proven_prune.prune("unused")
    assert gone == []


def test_the_command_says_why_it_could_not_prune(capsys, tmp_path: Path) -> None:
    assert proven_prune.main([str(tmp_path / "none")]) == 1
    assert "proven_prune: " in capsys.readouterr().err
