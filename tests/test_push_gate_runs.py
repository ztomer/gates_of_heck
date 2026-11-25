"""push_gate.sh -- a run owns ONE directory, leaves nothing behind, and reaps what a killed run left.

Measured 2026-10-05 in `~/.cache/goh/push/`: one export worktree (`MA4NUA`, detached at v0.15.1,
three days old) and 230 `*.out` files going back two weeks. Two defects, one class -- *a gate run
leaves state outside anything that removes it*:

  * The worktree sat directly in the shared export root, so anything the gate wrote BESIDE its tree
    landed in the shared root and outlived the run. A private consumer's `tests/test_repos.sh` writes
    `"$HERE.out"` -- a sibling of the repo root -- which in an export is the shared root.
  * The header promised "a worktree left behind by a SIGKILL is pruned by the next run", and
    `git worktree prune` only forgets a worktree whose DIRECTORY is gone. A killed run's directory
    is not gone; that is the leak. Nothing removed it.

So a run now owns `<export root>/<run>/`, stamped with its owner (pid + start time). Cleanup removes
the whole run directory and NAMES anything the gate wrote outside its tree; the next run removes any
run directory whose owner is dead.
"""

import os
import subprocess
from pathlib import Path

import pytest
from _fast_git import fast_init
from _todays_gates import _clean_checkout_of_todays_gates
from conftest import native_goh_path
from test_push_gate import GATE, PUSH_GATE, REPO_ROOT, _git, _repo
from timing_bounds import patience

# It writes beside its tree, a `.cargo` too: the kit's own is only in a fallback run.
LITTER_GATE = GATE.replace(
    "exit 0\n", 'printf x > "../$(basename "$PWD").out"\nmkdir -p ../.cargo\nexit 0\n'
)


def _push(
    repo: Path, tmp_path: Path, export_root: Path, goh: Path = REPO_ROOT
) -> subprocess.CompletedProcess:
    sha = _git(repo, "rev-parse", "HEAD")
    env = dict(
        os.environ,
        GATE_REPORT=str(tmp_path / "report.txt"),
        GOH_DIR=str(goh),
        **({"GOH_BIN": str(native_goh_path())} if goh != REPO_ROOT else {}),
        GOH_PUSH_LOGS=str(tmp_path / "push-logs"),
        GOH_PUSH_WORKTREES=str(export_root),
    )
    return subprocess.run(
        ["bash", str(goh / PUSH_GATE.relative_to(REPO_ROOT))],
        cwd=repo,
        env=env,
        text=True,
        input=f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n",
        capture_output=True,
        timeout=patience(60),
        check=False,
    )


def _run_litter(export_root: Path) -> list[Path]:
    """What a run left in the export root, beyond the `<run dir>.source` marker that names a
    stable export's checkout and the `<run dir>.kept` tree the next run reuses (GOH.PUSHMTIME),
    both of which outlive every run by design."""
    return [p for p in export_root.iterdir() if p.suffix not in (".source", ".kept")]


def _stamp(pid: int) -> str:
    start = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    ).stdout
    return f"{pid} {' '.join(start.split())}\n"


def _dead_pid() -> int:
    proc = subprocess.Popen(["/usr/bin/true"], stdin=subprocess.DEVNULL)
    proc.wait()
    return proc.pid


def _abandoned_run(repo: Path, export_root: Path, name: str, owner: str) -> Path:
    """A run directory exactly as a SIGKILLed gate leaves it: stamped, with a registered worktree."""
    run = export_root / name
    run.mkdir(parents=True)
    (run / ".owner").write_text(owner)
    _git(repo, "worktree", "add", "--detach", "-q", str(run / "repo"), "HEAD")
    return run


def _cwds(tmp_path: Path) -> list[str]:
    report = (tmp_path / "report.txt").read_text()
    return [line.split("=", 1)[1] for line in report.splitlines() if line.startswith("cwd=")]


@pytest.mark.load_sensitive
def test_the_next_run_reaps_what_is_dead_and_leaves_what_is_live(tmp_path):
    """One push over an export root holding each thing a past run leaves: a killed run (owner dead),
    a run whose pid came back as someone else (a pid alone is a rumour: the start time is what makes
    the stamp an identity), a LIVE run (two pushes at once must not delete each other's tree), the
    marker of a checkout that is gone (one per checkout ever pushed would grow forever), and one of
    a checkout that is here."""
    repo = _repo(tmp_path)
    export_root = tmp_path / "exports"
    dead = _abandoned_run(repo, export_root, "KILLED", _stamp(_dead_pid()))
    reused = _abandoned_run(
        repo, export_root, "REUSED", f"{os.getpid()} Thu Jan  1 00:00:00 1970\n"
    )
    live = _abandoned_run(repo, export_root, "LIVE", _stamp(os.getpid()))
    (export_root / "gone-000000000000.source").write_text(f"{tmp_path / 'deleted-worktree'}\n")
    (export_root / "kept-111111111111.source").write_text(f"{repo}\n")
    got = _push(repo, tmp_path, export_root)
    assert got.returncode == 0, got.stdout + got.stderr
    assert not dead.exists(), "a killed run's directory survived the next run"
    assert not reused.exists(), "a reused pid was taken for the owner"
    assert (live / "repo").is_dir(), "a live run's tree was deleted under it"
    trees = _git(repo, "worktree", "list")
    assert trees.count("\n") == 1 and str(live) in trees, trees  # the checkout's and the live run's
    names = {p.name for p in export_root.iterdir()}
    assert "gone-000000000000.source" not in names and "kept-111111111111.source" in names, names


# ── a STABLE export path per repo ─────────────────────────────────────────────────────────────
# ~/.cargo/config.toml keys the build directory by `{workspace-path-hash}`, and the export used to
# land at a random path: every push built every dependency cold into a brand-new build-dir and left
# it behind. Measured 2026-10-05: 24 such dirs, 30 GB, in three days.


def test_the_same_repo_exports_to_one_path_its_marker_names_and_litter_is_removed(tmp_path):
    """The export is deleted after every push and recreated at the same path for the next, so the
    build dir keyed on that path is warm on purpose. A build-dir reclaimer that evicts a workspace
    whose sources are gone (scripts' cargo-orphan-build-dirs) cannot tell this export from a dead
    worktree by its path, and evicted it: a cold push every time (2026-10-17). `<run dir>.source`
    names the checkout; while it exists, so does the workspace, between pushes. The second push's
    gate writes beside its tree: removed, and named."""
    repo = _repo(tmp_path)
    export_root = tmp_path / "exports"
    got = _push(repo, tmp_path, export_root)
    assert got.returncode == 0, got.stdout + got.stderr
    assert "outside its tree" not in got.stdout + got.stderr, got.stdout + got.stderr
    run_dir = Path(_cwds(tmp_path)[0]).parent
    assert not run_dir.exists(), "the export outlived its push"
    marker = Path(f"{run_dir}.source")
    assert marker.is_file(), (
        f"no {marker.name} beside the export root: {sorted(export_root.iterdir())}"
    )
    assert Path(marker.read_text().strip()).resolve() == repo.resolve()
    (repo / "tools" / "gate.sh").write_text(LITTER_GATE)
    _git(repo, "commit", "-qam", "a gate that writes beside its tree")
    got = _push(repo, tmp_path, export_root)
    said = got.stdout + got.stderr
    assert got.returncode == 0, said
    first, second = _cwds(tmp_path)
    assert first == second, f"two pushes of one repo exported to two paths: {first} vs {second}"
    assert _run_litter(export_root) == [], f"the run left: {list(export_root.rglob('*'))}"
    (named,) = [line for line in said.splitlines() if "outside its tree" in line]
    assert "repo.out" in named and ".cargo" in named, named


# What the gate's cargo would ACTUALLY use, asked of cargo: the property is cargo's config
# precedence, which a report of one environment variable cannot see.
BUILD_DIR_GATE = GATE.replace(
    "exit 0\n",
    'printf "build=%s\\n" "$(cargo metadata -q --format-version 1 --no-deps'
    ' | sed -n \'s/.*"build_directory":"\\([^"]*\\)".*/\\1/p\')" >> "$out"\n'
    "exit 0\n",
)


def _names_build_dir(repo: Path, own: str) -> None:
    (repo / ".cargo").mkdir()
    (repo / ".cargo" / "config.toml").write_text(f'[build]\nbuild-dir = "{own}"\n')


def _cargo_repo(tmp_path: Path, own_build_dir: str | None = None) -> Path:
    repo = _repo(tmp_path)
    (repo / "tools" / "gate.sh").write_text(BUILD_DIR_GATE)
    (repo / "Cargo.toml").write_text('[package]\nname = "x"\nversion = "1.0.0"\nedition = "2021"\n')
    (repo / "src").mkdir()
    (repo / "src" / "lib.rs").write_text("")
    if own_build_dir is not None:
        _names_build_dir(repo, own_build_dir)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "a cargo package whose gate reports its build dir")
    return repo


def _built(tmp_path: Path) -> str:
    """The build dir the last push's gate ran with."""
    return (tmp_path / "report.txt").read_text().splitlines()[-1].split("=", 1)[1]


def test_a_concurrent_push_of_the_same_repo_takes_a_private_path_and_build_dir(tmp_path):
    """Three pushes: one claims the stable path, two run while a LIVE run holds it.

    The stable one names its build dirs as cargo does. Between pushes the export does not exist, and
    cargo cannot say which build dir a missing path keys -- it canonicalises the manifest. So a
    reclaimer could not tell the export's warm build dir from an orphan, and its ceiling evicted
    every one an hour after its push (2026-10-17). The gate asks cargo while the export exists and
    records it in the marker.

    The fallback's own cargo config is not named as litter (ztools' release, 2026-11-08: 'the gate
    wrote outside its tree (removed): .cargo'). The gate wrote nothing -- the KIT writes `<run
    dir>/.cargo/config.toml` in the private fallback, records it (`.kit-wrote`), and the report
    skips exactly the recorded names.

    And it redirects only the build-dir the USER's ~/.cargo/config.toml keys by workspace path: a
    repo that names its own in .cargo/config.toml keeps it (an exported CARGO_BUILD_BUILD_DIR
    outranks every config file, so it silently replaced the repo's choice)."""
    repo, export_root = _cargo_repo(tmp_path), tmp_path / "exports"
    first = _push(repo, tmp_path, export_root)
    assert first.returncode == 0, first.stdout + first.stderr
    stable, used = Path(_cwds(tmp_path)[0]).parent, _built(tmp_path)
    lines = Path(f"{stable}.source").read_text().splitlines()
    assert f"build-dir {used}" in lines[1:], (used, lines)

    _abandoned_run(repo, export_root, stable.name, _stamp(os.getpid()))
    got = _push(repo, tmp_path, export_root)
    assert got.returncode == 0, got.stdout + got.stderr
    fallback, build = Path(_cwds(tmp_path)[-1]).parent, _built(tmp_path)
    assert fallback != stable, "a live run's export path was taken"
    assert build.startswith(str(fallback)), f"the fallback build-dir outlives its run: {build}"
    assert "outside its tree" not in got.stdout + got.stderr, got.stdout + got.stderr

    own = str(tmp_path / "the-repos-own-build-dir")
    _names_build_dir(repo, own)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "its own build dir")
    got = _push(repo, tmp_path, export_root)
    assert got.returncode == 0, got.stdout + got.stderr
    assert Path(_cwds(tmp_path)[-1]).parent != stable, "a live run's export path was taken"
    build = _built(tmp_path)
    assert build == own, f"the fallback run overrode the repo's own build-dir: {build}"


SLEEPING_GATE = GATE.replace(
    "[ -f fail.flag ] && exit 1\n", 'echo 200 > "$GOH_SLEEPTIME_FILE"\nexit 1\n'
)


def _red_push(tmp_path: Path, gate: str, sleeptime: Path) -> subprocess.CompletedProcess:
    repo = _repo(tmp_path)
    (repo / "tools" / "gate.sh").write_text(gate)
    _git(repo, "commit", "-qam", "red")
    sleeptime.write_text("100\n")
    os.environ["GOH_SLEEPTIME_FILE"] = str(sleeptime)
    try:
        return _push(repo, tmp_path, tmp_path / "exports")
    finally:
        del os.environ["GOH_SLEEPTIME_FILE"]


def test_a_red_run_the_machine_slept_through_names_the_sleep(tmp_path):
    """A private Swift app 2026-10-11: two Swift rows red at the second of a dark wake, green on the
    awake re-run. A failure the sleep caused reads exactly like a defect unless the gate says the
    machine slept."""
    got = _red_push(tmp_path, SLEEPING_GATE, tmp_path / "sleeptime")
    assert got.returncode == 1, got.stdout + got.stderr
    assert "SLEPT during this run" in got.stderr


def test_a_red_run_with_no_sleep_says_nothing_of_one_and_leaves_no_run_directory(tmp_path):
    got = _red_push(
        tmp_path, GATE.replace("[ -f fail.flag ] && exit 1\n", "exit 1\n"), tmp_path / "s"
    )
    assert got.returncode == 1, got.stdout + got.stderr
    assert "SLEPT" not in got.stdout + got.stderr
    export_root = tmp_path / "exports"
    assert _run_litter(export_root) == [], list(export_root.rglob("*"))


# ── the gates_of_heck sibling ─────────────────────────────────────────────────────────────────
# antiknob, 2026-10-24: `goh-tui = { path = "../gates_of_heck/crates/goh-tui" }` resolves beside the
# checkout, and the export (<export root>/<repo>-<hash>/<repo>) has nothing beside its tree, so the
# push died on "no matching package named `goh-tui`". The consumer's workaround -- a link made in its
# own tools/gate.sh -- was then named here as "wrote outside its tree".

SIBLING_GATE = GATE.replace(
    "exit 0\n",
    'printf "sibling=%s\\n" '
    '"$(git -C ../gates_of_heck rev-parse HEAD 2>/dev/null || echo NONE)" >> "$out"\n'
    'printf "tui=%s\\n" "$([ -d ../gates_of_heck/crates/goh-tui ] && echo found || echo MISSING)" >> "$out"\n'
    "exit 0\n",
)
TUI_DEP = '[dependencies]\ngoh-tui = { path = "../gates_of_heck/crates/goh-tui" }\n'


def _sibling_repo(tmp_path: Path, cargo: str | None, gate: str = SIBLING_GATE) -> Path:
    repo = _repo(tmp_path)
    (repo / "tools" / "gate.sh").write_text(gate)
    if cargo is not None:
        (repo / "Cargo.toml").write_text('[package]\nname = "c"\nversion = "0.1.0"\n' + cargo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "sibling fixture")
    return repo


def _gates(tmp_path: Path) -> Path:
    """The goh checkout an export carries: today's gates, a git checkout of their own, with no Rust
    source -- a tenth of what each export of the whole checkout wrote and removed."""
    return _clean_checkout_of_todays_gates(tmp_path / "goh", rust=False)


def _report(tmp_path: Path) -> dict[str, str]:
    lines = (tmp_path / "report.txt").read_text().splitlines()
    return dict(line.split("=", 1) for line in lines)


def test_a_consumer_that_declares_the_goh_sibling_gets_it_in_the_export(tmp_path):
    """Its own link is not litter. The exemption is for that link only: any other thing a gate
    writes beside its tree is still named, so the workaround stays visible if a consumer keeps it
    for another name."""
    gate = SIBLING_GATE.replace("exit 0\n", 'ln -s /tmp "../other_sibling"\nexit 0\n')
    repo, gates = _sibling_repo(tmp_path, TUI_DEP, gate), _gates(tmp_path)
    got = _push(repo, tmp_path, tmp_path / "exports", gates)
    assert got.returncode == 0, got.stdout + got.stderr
    seen = _report(tmp_path)
    assert seen["sibling"] == _git(gates, "rev-parse", "HEAD") and seen["tui"] == "found", seen
    (named,) = [x for x in (got.stdout + got.stderr).splitlines() if "outside its tree" in x]
    assert "outside its tree (removed): other_sibling" in named, named
    assert "gates_of_heck" not in named.split("(removed):")[1], named
    assert _run_litter(tmp_path / "exports") == []


# ── a sibling is gated at a named commit, never its live working tree (GOH.PUSHSIBLINGS) ──────
# ZeroThunder, 2026-11-11: its push failed on game_asset_factory's UNCOMMITTED edit, because the
# export linked the sibling's working tree; a private Swift app D-0361 moved SpaceBarKit consumers
# to a pinned release for the same reason. The export carries the sibling's HEAD, and the log names
# the commit.

HEAD_GATE = GATE.replace(
    "exit 0\n",
    'printf "file=%s\\n" "$(cat ../Kit/Packages/K/f.txt 2>/dev/null || echo NONE)" >> "$out"\n'
    'printf "untracked=%s\\n" '
    '"$([ -e ../Kit/Packages/K/new.txt ] && echo SEEN || echo no)" >> "$out"\n'
    "exit 0\n",
)


def _kit_checkout(tmp_path: Path) -> tuple[Path, str]:
    kit = tmp_path / "Kit"
    (kit / "Packages" / "K").mkdir(parents=True)
    (kit / "Packages" / "K" / "f.txt").write_text("committed\n")
    fast_init(kit, "main")
    _git(kit, "add", "-A")
    _git(kit, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "kit")
    return kit, _git(kit, "rev-parse", "HEAD")


def test_a_siblings_uncommitted_edit_does_not_reach_the_push_gate(tmp_path):
    repo = _swift_sibling_repo(tmp_path)
    (repo / "tools" / "gate.sh").write_text(HEAD_GATE)
    _git(repo, "commit", "-qam", "head gate")
    kit, sha = _kit_checkout(tmp_path)
    (kit / "Packages" / "K" / "f.txt").write_text("UNCOMMITTED EDIT\n")
    (kit / "Packages" / "K" / "new.txt").write_text("untracked\n")
    got = _push(repo, tmp_path, tmp_path / "exports")
    assert got.returncode == 0, got.stdout + got.stderr
    seen = _report(tmp_path)
    assert seen["file"] == "committed" and seen["untracked"] == "no", seen
    assert f"sibling Kit at {sha[:12]}" in got.stdout + got.stderr, got.stdout + got.stderr
    assert "uncommitted" in got.stdout + got.stderr, got.stdout + got.stderr
    assert (kit / "Packages" / "K" / "f.txt").read_text() == "UNCOMMITTED EDIT\n"
    assert _git(kit, "worktree", "list").count("\n") == 0  # the sibling's .git is untouched
    assert _run_litter(tmp_path / "exports") == []


# ── any sibling a tracked manifest names ─────────────────────────────────────────────────────────
# token-pool, 2026-11-07: widget/Package.swift depends on `.package(path: "../../app/Packages/
# SpaceBarKit")`, the house widget kit in the app's checkout beside it, and the export has nothing
# beside its tree, so the push died on "the package at .../app/Packages/SpaceBarKit cannot be
# accessed". The gates_of_heck link above was ONE instance of the class: a path dependency from
# any tracked manifest (Cargo.toml or Package.swift, at any depth) on a sibling checkout.

KIT_GATE = GATE.replace(
    "exit 0\n",
    'printf "sibling=%s\\n" "$(readlink ../Kit || echo NONE)" >> "$out"\n'
    'printf "kit=%s\\n" "$([ -d ../Kit/Packages/K ] && echo found || echo MISSING)" >> "$out"\n'
    "exit 0\n",
)
SWIFT_DEP = 'import PackageDescription\nlet package = Package(name: "W", dependencies: [\n    .package(path: "../../Kit/Packages/K")\n])\n'


def _swift_sibling_repo(tmp_path: Path) -> Path:
    repo = _repo(tmp_path)
    (repo / "tools" / "gate.sh").write_text(KIT_GATE)
    (repo / "widget").mkdir()
    (repo / "widget" / "Package.swift").write_text(SWIFT_DEP)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "swift sibling fixture")
    return repo


def test_a_sibling_checkout_a_nested_package_swift_names_is_in_the_export(tmp_path):
    repo = _swift_sibling_repo(tmp_path)
    kit = tmp_path / "Kit"
    (kit / "Packages" / "K").mkdir(parents=True)
    got = _push(repo, tmp_path, tmp_path / "exports")
    assert got.returncode == 0, got.stdout + got.stderr
    seen = _report(tmp_path)
    assert seen["sibling"] == str(kit) and seen["kit"] == "found", seen
    assert "outside its tree" not in got.stdout + got.stderr, got.stdout + got.stderr
    assert _run_litter(tmp_path / "exports") == []


def test_a_sibling_not_declared_or_not_checked_out_gets_no_link(tmp_path):
    """A manifest naming a Kit that is not here, and one that does not name gates_of_heck."""
    repo = _swift_sibling_repo(tmp_path)
    (repo / "Cargo.toml").write_text(
        '[package]\nname = "c"\nversion = "0.1.0"\n[dependencies]\nserde = "1"\n'
    )
    both = KIT_GATE.replace(
        "exit 0\n",
        'printf "goh=%s\\n" "$([ -e ../gates_of_heck ] && echo LINK)" >> "$out"\nexit 0\n',
    )
    (repo / "tools" / "gate.sh").write_text(both)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "neither sibling")
    got = _push(repo, tmp_path, tmp_path / "exports")
    assert got.returncode == 0, got.stdout + got.stderr
    assert _report(tmp_path)["sibling"] == "NONE", _report(tmp_path)
    assert _report(tmp_path)["goh"] == "", _report(tmp_path)
