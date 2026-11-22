"""The push export's tree is KEPT between pushes, so cargo sees unchanged files unchanged
(GOH.PUSHMTIME).

push_gate.sh made the export with a fresh `git worktree add` every push: every tracked file got a
new mtime, and cargo, which judges a path crate fresh by mtime, rebuilt every workspace crate in
every profile the gate uses, though the build dir keyed on the stable export path was warm. monitor
pre-push 2026-11-15: installed_freshness.py's no-op release build took 50 s (a fat-LTO relink),
bench_check.py 38 s. Now the run moves its tree aside (`git worktree move`, mtimes kept) and the
next run moves it back, `checkout --detach -f` of the new commit (git rewrites only the files that
differ) and `clean -ffdx` (nothing a gate left reaches the next run). Never by setting old mtimes,
which could make a changed file look fresh.
"""

from pathlib import Path

from test_push_gate import _git
from test_push_gate_runs import _cargo_repo, _push, _run_litter

# The gate builds; cargo says `Compiling x` only for a crate it judged stale.
CARGO_GATE = """#!/usr/bin/env bash
set -euo pipefail
printf 'cwd=%s\\n' "$PWD" >> "$GATE_REPORT"
n=$(cargo build --offline 2>&1 | grep -c 'Compiling x' || true)
printf 'compiled=%s\\n' "$n" >> "$GATE_REPORT"
printf left > left-by-the-gate.txt
exit 0
"""


def _compiled(tmp_path: Path) -> list[str]:
    report = (tmp_path / "report.txt").read_text().splitlines()
    return [ln.split("=", 1)[1] for ln in report if ln.startswith("compiled=")]


def _gated_repo(tmp_path: Path) -> Path:
    repo = _cargo_repo(tmp_path)
    (repo / "tools" / "gate.sh").write_text(CARGO_GATE)
    (repo / ".gitignore").write_text("local.cfg\ntarget/\n")
    _git(repo, "commit", "-qam", "a gate that builds and says what it compiled")
    return repo


def _push_ok(repo: Path, tmp_path: Path) -> None:
    got = _push(repo, tmp_path, tmp_path / "exports")
    assert got.returncode == 0, got.stdout + got.stderr


def test_an_unchanged_push_rebuilds_nothing_and_a_change_rebuilds_its_crate(tmp_path):
    repo = _gated_repo(tmp_path)
    _push_ok(repo, tmp_path)
    _push_ok(repo, tmp_path)
    assert _git(repo, "worktree", "list").count("\n") == 0, "the kept tree is still a worktree"
    (repo / "src" / "lib.rs").write_text("pub fn changed() {}\n")
    _git(repo, "commit", "-qam", "one file changed")
    _push_ok(repo, tmp_path)
    assert _compiled(tmp_path) == ["1", "0", "1"], "an unchanged push rebuilt, or a change did not"


def test_what_a_gate_left_in_its_tree_does_not_reach_the_next_push(tmp_path):
    repo = _gated_repo(tmp_path)
    (repo / "tools" / "gate.sh").write_text(
        CARGO_GATE.replace("exit 0\n", "[ -e seen.txt ] && exit 1\ntouch seen.txt\nexit 0\n")
    )
    _git(repo, "commit", "-qam", "a gate that is red on what the last run left")
    _push_ok(repo, tmp_path)
    _push_ok(repo, tmp_path)
    assert _run_litter(tmp_path / "exports") == [], "the run left more than its kept tree"


def test_a_kept_tree_whose_checkout_is_gone_is_removed_with_its_marker(tmp_path):
    repo = _gated_repo(tmp_path)
    _push_ok(repo, tmp_path)
    export_root = tmp_path / "exports"
    kept = [p for p in export_root.iterdir() if p.suffix == ".kept"]
    assert kept, f"no kept tree: {sorted(export_root.iterdir())}"
    marker = kept[0].with_suffix(".source")
    marker.write_text(f"{tmp_path / 'deleted-checkout'}\n")
    (tmp_path / "other").mkdir()
    other = _cargo_repo(tmp_path / "other")
    _push_ok(other, tmp_path)
    assert not kept[0].exists() and not marker.exists()
