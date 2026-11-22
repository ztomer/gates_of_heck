"""goh's own pre-commit, self-hosted: a clone of THIS repo, installed into and committed to, must
pass the hook goh installs everywhere else (split from tests/test_install.py, GOH.SELFHOSTTEST)."""

import os
import shutil
import site
import subprocess
import sys

import pytest
from conftest import REPO_ROOT, git, hermetic_env


def _hook_toolchain_env() -> dict[str, str]:
    """What the self-hosted hook needs of THIS machine beyond its reduced PATH: the toolchains the
    suite names (tests/suite_home.py), and the house interpreter the suite runs on. Run 42950563400:
    under `/usr/bin:/bin:/opt/homebrew/bin` the hook resolved Homebrew's python3, which held no ruff
    (the runner installs the suite's tools into its setup-python interpreter), and the python gate
    died "ruff is not available through '/opt/homebrew/bin/python3 -m'"."""
    env = {k: os.environ[k] for k in ("RUSTUP_HOME", "CARGO_HOME") if k in os.environ}
    env["GOH_PY"] = sys.executable
    # Its packages too: the commit runs goh's declared source guard under pytest
    # (gates/staged_guards.sh), and a user-site pytest is found through HOME (tmp_path here).
    env["PYTHONUSERBASE"] = site.getuserbase()
    return env


_HOOK_CONFIG = (".gatesrc", "tools/gate.sh")


def _file_kind(path: str) -> str:
    """A file's kind: its suffix, a dotfile's own name (`.gatesrc`), "" when it has neither."""
    name = path.rsplit("/", 1)[-1]
    if name.startswith(".") and name.count(".") == 1:
        return name
    return name.rpartition(".")[2] if "." in name else ""


def _self_host_sample(tracked: list[str]) -> list[str]:
    """What the self-hosted commit stages (GOH.SELFHOSTTEST): the hook's config (`.gatesrc`,
    `tools/gate.sh`, `.githooks/`) and one tracked file of every kind (suffix; a dotfile's name;
    extensionless), a fixture only when its kind has nothing else. Every staged layer the hook
    dispatches by kind then runs on goh's own files; the whole tree under every step is the push
    gate's (.gatesrc GOH_CI_STEPS `structural.sh --full`, `py_staged.sh --all`), run on every land.
    Staging all 1424 files made this one test a second whole gate: 247.2 s on 2026-11-18."""
    config = [t for t in tracked if t in _HOOK_CONFIG or t.startswith(".githooks/")]
    by_kind: dict[str, str] = {}
    for t in sorted(set(tracked) - set(config), key=lambda t: (t.startswith("tests/fixtures/"), t)):
        by_kind.setdefault(_file_kind(t), t)
    return sorted({*config, *by_kind.values()})


def test_the_self_hosted_hook_runs_on_the_suites_interpreter():
    assert _hook_toolchain_env()["GOH_PY"] == sys.executable


def test_the_self_host_sample_holds_the_hook_config_and_every_kind_of_tracked_file():
    tracked = ".gatesrc .githooks/pre-commit README.md docs/a.md gates/x.sh lib/a.py lib/b.py"
    tracked += " tests/fixtures/bad.py tests/fixtures/only.swift tools/gate.sh tools/run"
    # the config whatever its kind; a fixture only as its kind's last resort; extensionless a kind
    want = ".gatesrc .githooks/pre-commit README.md gates/x.sh lib/a.py tests/fixtures/only.swift"
    assert _self_host_sample(tracked.split()) == [*want.split(), "tools/gate.sh", "tools/run"]


@pytest.mark.load_sensitive(
    reason="a real hook run over a sample of the tree, once a whole-tree one (GOH.COVDEDUPE2)"
)
def test_self_hosted_repo_satisfies_its_own_hook(tmp_path, goh):
    """gates_of_heck itself must pass its own pre-commit: a clone of THIS repo, its working tree's
    tracked files laid over it, installed into and committed to. The copy holds no `bin/goh`
    (untracked) and its PATH no cargo, so the binary is named: the Python tier that used to answer
    here is retired (Phase N3). The commit stages `_self_host_sample` over a base holding the rest
    of the tree, so every layer runs on goh's own files without this test being a second whole
    gate. A clone, never a fresh repository: a seed keyed by goh's root commit (GOH.SEEDPRIVATE)
    must find goh's root here, as it does in every real clone."""
    copy = tmp_path / "goh-copy"
    # not `main`: this tree's .gatesrc says GOH_LANDED_ONLY=main, and a commit on main in a primary
    # checkout is refused outside land.sh (GOH.LANDONLY, tests/test_landed_only_commit.py)
    subprocess.run(
        ["git", "clone", "-q", "--shared", "--no-checkout", str(REPO_ROOT), str(copy)],
        check=True,
        stdin=subprocess.DEVNULL,
    )
    git(copy, "checkout", "-q", "-B", "work")
    git(copy, "remote", "remove", "origin")
    # TRACKED files only, as the working tree holds them (a lane's uncommitted edit is judged too),
    # laid over the clone by git itself: `add -u` with this checkout as the work tree takes its
    # edits and deletions, then files only its index holds, then the clone's work tree is written
    # from that index. A copy by joined path declared a read of every file, VERSION included, and a
    # release commit then chose this test (tests/test_pre_reuse.py). `copytree(REPO_ROOT)` once
    # walked target/ (178 MB) and brought untracked files from anything else along too.
    here = f"--work-tree={REPO_ROOT}"
    git(copy, here, "add", "-u")
    gone = set(git(REPO_ROOT, "ls-files", "--deleted").splitlines())
    new = set(git(REPO_ROOT, "ls-files").splitlines()) - set(git(copy, "ls-files").splitlines())
    if new - gone:
        git(copy, here, "add", "--", *sorted(new - gone))
    git(copy, "checkout-index", "-a", "-f")
    for rel in git(copy, "diff", "--cached", "--name-only", "--diff-filter=D", "HEAD").splitlines():
        (copy / rel).unlink(missing_ok=True)  # deleted in the working tree, not yet committed
    present = git(copy, "ls-files").splitlines()
    sample = _self_host_sample(present)
    # The base: the rest of the tree, committed with no hook (hooksPath=/dev/null keeps a global one
    # out too); the sample is then the commit the installed hook judges.
    git(copy, "add", "-A")
    git(copy, "rm", "-q", "--cached", "--", *sample)
    base = "-c core.hooksPath=/dev/null -c user.name=t -c user.email=t commit -q --no-gpg-sign"
    git(copy, *base.split(), "--allow-empty", "-m", "base")
    # A prebuilt goh, never a build (GOH.INSTALLEXIT: install.sh compiled goh in the copy, 13-29 s),
    # and install.sh's exit is asserted: a failed install judged nothing.
    (copy / "bin").mkdir(exist_ok=True)
    shutil.copy2(goh, copy / "bin" / "goh")
    inst = subprocess.run(
        ["/bin/bash", str(copy / "install.sh"), str(copy)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
        env=hermetic_env(GOH_SKIP_BUILD="1"),
    )
    assert inst.returncode == 0, inst.stdout + inst.stderr
    git(copy, "add", "-A")
    staged = set(git(copy, "diff", "--cached", "--name-only").splitlines())
    assert set(sample) <= staged, f"the sample is not what the hook judges: {set(sample) - staged}"
    # The hook's formatters and linters, from wherever THIS machine keeps them, without the rest of
    # that directory: a hosted runner's rustfmt is a rustup proxy in ~/.cargo/bin (beside cargo,
    # which this PATH must not hold), and the hook said "rustfmt is not installed" (run
    # 42930175911). The proxy finds its toolchains through RUSTUP_HOME, which HOME (the test's
    # tmp_path) no longer gives.
    tools = tmp_path / "hook-tools"
    tools.mkdir()
    for name in ("rustfmt", "shellcheck", "ruff"):
        found = shutil.which(name)
        if found:
            (tools / name).symlink_to(found)
    toolchain_env = _hook_toolchain_env()
    # GOH_DIR points the delegation at the copy itself → true self-host.
    r = subprocess.run(
        [
            "git",
            "-C",
            str(copy),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t",
            "commit",
            "-m",
            "self-host",
        ],
        capture_output=True,
        text=True,
        env={
            "GOH_DIR": str(copy),
            "GOH_BIN": str(goh),
            "HOME": str(tmp_path),
            "PATH": f"/usr/bin:/bin:/opt/homebrew/bin:{tools}",
            **toolchain_env,
        },
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    for layer in ("structural", "python (staged)", "source guards (staged)"):
        assert f"all {layer} gates passed" in r.stdout + r.stderr, f"the {layer} layer never ran"
