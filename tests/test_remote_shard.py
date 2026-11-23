"""`goh remote-shard` (`tools/remote_shard.py`, GOH.REMOTEWORKERS): the Linux-safe part of a test
run goes to a worker container on another host and its junit comes back. Driven through a FAKE
ssh (runs the remote command here) and a FAKE docker (writes the junit the test chose), so every
property is proven without a host: tracked files only, the container's CPU confinement, an image
tagged by its Dockerfile's content, and the two refusals -- a worker whose tool versions differ
from goh's manifest, and a remote run whose skip count differs from the Mac's for the same tests.
The image's own contract (every version a named release, every download checksummed, the pins
equal to the manifest) is read from `tools/remote_worker/Dockerfile`."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env
from timing_bounds import patience

sys.path.insert(0, str(REPO_ROOT / "lib"))
from source_closure import copy_closure

TOOL = REPO_ROOT / "tools" / "remote_shard.py"
DOCKERFILE = REPO_ROOT / "tools" / "remote_worker" / "Dockerfile"
TSV = REPO_ROOT / "gates" / "required_tools.tsv"
PY = sys.executable

FAKE_SSH = """#!/usr/bin/env bash
# ssh [-o X]... HOST CMD...: run CMD here, as the host's login shell would.
while [ "${1:-}" = -o ] || [ "${1:-}" = -T ]; do [ "$1" = -o ] && shift; shift; done
shift
echo "ssh $*" >> "$FAKE_LOG"
[ -z "${FAKE_SSH_DOWN:-}" ] || exit 255
exec bash -c "$*"
"""

FAKE_FLOCK = """#!/usr/bin/env bash
# flock FILE CMD...: the host's util-linux flock, minus the lock (one run at a time here anyway).
echo "flock $1" >> "$FAKE_LOG"
shift
exec "$@"
"""

FAKE_DOCKER = r"""#!/usr/bin/env python3
# docker, as far as goh remote-shard uses it: images and ONE named container, kept as files.
import os, re, sys
from pathlib import Path
a = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write("$ docker " + " ".join(a) + "\n")
state = Path(os.environ["FAKE_IMAGES"])
box = state / "container"
if a[:2] == ["image", "inspect"]:
    sys.exit(0 if (state / a[2].replace("/", "_")).exists() else 1)
if a[0] == "build":
    sys.stdin.read()
    (state / a[a.index("-t") + 1].replace("/", "_")).write_text("built")
    sys.exit(0)
if a[0] == "inspect":
    if not box.exists():
        sys.exit(1)
    print(box.read_text().split("\n")[0] + "|true")
    sys.exit(0)
if a[0] == "run" and "-d" in a:
    work = next(v.split(":")[0] for i, v in enumerate(a) if a[i - 1] == "-v")
    box.write_text(a[-3] + "\n" + work)
    sys.exit(0)
if a[0] == "exec":
    rest = a[1:]
    while rest[0].startswith("-"):
        rest = rest[2:] if rest[0] == "-e" else rest[1:]
    rest = rest[1:]  # the container's name
    tag, work = box.read_text().split("\n")
    if rest == ["cat", "/opt/goh-worker-versions.txt"]:
        sys.stdout.write(Path(os.environ["FAKE_VERSIONS"]).read_text())
        sys.exit(0)
    if "pytest" in rest[-1]:
        run_id = re.search(r"/work/out/([\w.-]+)\.xml", rest[-1]).group(1)
        Path(work, "out", run_id + ".xml").write_text(Path(os.environ["FAKE_JUNIT"]).read_text())
        sys.exit(int(os.environ.get("FAKE_RC", "0")))
    import subprocess
    sys.exit(subprocess.run([re.sub(r"/work(?![\w-])", work, x) for x in rest]).returncode)
if a[0] == "ps":
    sys.exit(0)
if a[0] == "rm":
    box.unlink(missing_ok=True)
    sys.exit(0)
sys.exit(2)
"""


def _pins() -> dict[str, str]:
    """The manifest's exact pins (`=X.Y.Z`) for the pip modules the suite imports."""
    pins: dict[str, str] = {}
    for line in TSV.read_text().splitlines():
        f = line.split("\t")
        if len(f) == 6 and f[0] == "goh-suite" and f[2] == "pip" and f[5].startswith("="):
            pins[f[1]] = f[5][1:]
    return pins


def _versions(**override: str) -> str:
    pins = {**_pins(), **override}
    return "Python 3.15.2\n" + "".join(f"{k}=={v}\n" for k, v in pins.items())


def _junit(skipped: tuple[str, ...] = (), failed: tuple[str, ...] = ()) -> str:
    cases = ""
    for name in ("test_a", "test_b", "test_c", "test_d"):
        body = ""
        if name in skipped:
            body = '<skipped message="no tool"/>'
        elif name in failed:
            body = '<failure message="boom"/>'
        cases += f'<testcase classname="tests.test_x" name="{name}" time="0.1">{body}</testcase>'
    suite = f'<testsuite name="pytest">{cases}</testsuite>'
    return f'<?xml version="1.0"?><testsuites>{suite}</testsuites>'


@pytest.fixture
def rig(tmp_path: Path) -> dict[str, Path]:
    """A tracked tree (one untracked file beside it), the fakes on PATH, and a remote dir."""
    tree = tmp_path / "tree"
    (tree / "tests").mkdir(parents=True)
    (tree / "tests" / "test_x.py").write_text("def test_a(): pass\n")
    copy_closure(
        tree, "tools/remote_worker/Dockerfile", ".github/actions/goh-floors/shellcheck.sha256"
    )
    fast_init(tree)
    subprocess.run(["git", "-C", str(tree), "add", "-A"], check=True, stdin=subprocess.DEVNULL)
    subprocess.run(["git", "-C", str(tree), "-c", "user.name=t", "-c", "user.email=t@t", "commit",
                    "-qm", "one"], check=True, stdin=subprocess.DEVNULL)  # fmt: skip
    (tree / "untracked.txt").write_text("never sent")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("ssh", FAKE_SSH), ("docker", FAKE_DOCKER), ("flock", FAKE_FLOCK)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    images = tmp_path / "images"
    images.mkdir()
    remote = tmp_path / "remote"
    remote.mkdir()
    (tmp_path / "versions").write_text(_versions())
    (tmp_path / "junit.xml").write_text(_junit(skipped=("test_d",)))
    (tmp_path / "loadavg").write_text("0.50 0.40 0.30 1/500 1234\n")
    return {"tmp": tmp_path, "tree": tree, "bin": bin_dir, "images": images, "remote": remote}


def _env(rig: dict[str, Path], **env: str) -> dict[str, str]:
    tmp = rig["tmp"]
    return hermetic_env(
        PATH=f"{rig['bin']}{os.pathsep}{os.environ['PATH']}",
        FAKE_LOG=str(tmp / "log"),
        FAKE_IMAGES=str(rig["images"]),
        FAKE_VERSIONS=str(tmp / "versions"),
        FAKE_JUNIT=str(tmp / "junit.xml"),
        GOH_REMOTE_HOST="fakehost",
        GOH_REMOTE_DIR=str(rig["remote"]),
        GOH_REMOTE_LOADAVG=str(tmp / "loadavg"),
        **{"GOH_REMOTE_FREE_FLOOR_GB": "0", **env},
    )


def _run(rig: dict[str, Path], *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    argv = [
        PY,
        str(TOOL),
        "--tree",
        str(rig["tree"]),
        "--manifest",
        str(TSV),
        "--local-workers",
        "0",
    ]
    return subprocess.run([*argv, *args], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          env=_env(rig, **env), timeout=patience(120), check=False)  # fmt: skip


def _git(where: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(where), *args], capture_output=True, text=True,
                          check=False, stdin=subprocess.DEVNULL)  # fmt: skip


def _slot_tree(rig: dict[str, Path]) -> Path:
    """The worker tree of the rig's checkout: its default slot is keyed by the checkout's path."""
    slot = hashlib.sha256(str(rig["tree"].resolve()).encode()).hexdigest()[:10]
    return rig["remote"] / "trees" / slot


def _log(rig: dict[str, Path]) -> str:
    return (rig["tmp"] / "log").read_text()


def test_a_run_syncs_tracked_files_only_confines_the_container_and_returns_the_junit(rig):
    mac = rig["tmp"] / "mac.xml"
    mac.write_text(_junit(skipped=("test_d",)))
    out = rig["tmp"] / "out.xml"
    r = _run(
        rig, "--workers", "12", "--mac-junit", str(mac), "--junit", str(out), "tests/test_x.py"
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert (_slot_tree(rig) / "tests" / "test_x.py").is_file()
    assert not (_slot_tree(rig) / "untracked.txt").exists()
    assert (_slot_tree(rig) / ".git").is_dir()
    run = next(ln for ln in _log(rig).splitlines() if ln.startswith("$ docker run -d --name"))
    for flag in (
        "--init",
        "--restart unless-stopped",
        "--cpuset-cpus 4-15",
        "--cpu-shares 128",
        "--memory 10g",
        "--memory-swap 10g",
        "--pids-limit 4096",
        "--user 1000:1000",
        "--network none",
        "--security-opt no-new-privileges",
        "--cap-drop ALL",
        "--label goh.remote-shard=1",
    ):
        assert flag in run, flag
    assert "--cpus " not in run and "--oom-kill-disable" not in run and "--blkio-weight" not in run
    assert run.count(" -v ") == 1 and f"-v {rig['remote']}:/work" in run, run
    assert "-n 12" in next(
        ln for ln in _log(rig).splitlines() if ln.startswith("$ docker exec") and "pytest" in ln
    )
    assert out.read_text() == _junit(skipped=("test_d",))
    assert "3 passed" in r.stdout and "1 skipped" in r.stdout


def test_the_image_takes_its_pins_from_the_manifest_and_is_rebuilt_when_one_moves(rig):
    assert _run(rig, "tests/test_x.py").returncode == 0
    assert _run(rig, "tests/test_x.py").returncode == 0
    builds = [ln for ln in _log(rig).splitlines() if ln.startswith("$ docker build")]
    assert len(builds) == 1
    for module, pin in _pins().items():
        assert f"--build-arg {module.upper().replace('-', '_')}_VERSION={pin}" in builds[0]
    assert re.search(r"--build-arg SHELLCHECK_SHA256=[0-9a-f]{64}", builds[0])
    tag = re.search(r"-t (goh-remote-worker:[0-9a-f]{12})", builds[0]).group(1)
    raised = rig["tmp"] / "raised.tsv"
    rows = [ln.split("\t") for ln in TSV.read_text().splitlines()]
    raised.write_text("".join("\t".join([*f[:5], "=9" + f[5][1:]] if f[1:2] == ["pytest-xdist"]
                                        else f) + "\n" for f in rows))  # fmt: skip
    (rig["tmp"] / "versions").write_text(
        _versions(**{"pytest-xdist": "9" + _pins()["pytest-xdist"]})
    )
    argv = [
        PY,
        str(TOOL),
        "--tree",
        str(rig["tree"]),
        "--manifest",
        str(raised),
        "--local-workers",
        "0",
    ]
    r = subprocess.run([*argv, "tests/test_x.py"], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, env=_env(rig), timeout=patience(120),
                       check=False)  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    builds = [ln for ln in _log(rig).splitlines() if ln.startswith("$ docker build")]
    assert len(builds) == 2 and tag not in builds[1]


def test_a_remote_run_whose_skips_differ_from_the_macs_is_refused_naming_the_test(rig):
    mac = rig["tmp"] / "mac.xml"
    mac.write_text(_junit())
    out = rig["tmp"] / "out.xml"
    r = _run(rig, "--mac-junit", str(mac), "--junit", str(out), "tests/test_x.py")
    said = r.stdout + r.stderr
    assert r.returncode == 0, said
    assert "test_d" in said and "skip" in said.lower() and "refused" in said
    assert "locally" in said
    assert 'name="test_a"' in out.read_text() and "test_d" not in out.read_text()


def test_a_worker_off_the_manifests_pins_is_refused(rig):
    (rig["tmp"] / "versions").write_text(_versions(**{"pytest-xdist": "3.7.0"}))
    out = rig["tmp"] / "out.xml"
    r = _run(rig, "--junit", str(out), "tests/test_x.py")
    said = r.stdout + r.stderr
    assert r.returncode == 0, said
    assert "pytest-xdist" in said and "locally" in said
    assert not [
        ln for ln in _log(rig).splitlines() if ln.startswith("$ docker exec") and "pytest" in ln
    ]
    assert 'name="test_a"' in out.read_text()


@pytest.mark.parametrize(
    ("why", "env", "load"),
    [
        ("unreachable", {"FAKE_SSH_DOWN": "1"}, "0.5 0.4 0.3 1/500 1\n"),
        ("busy", {}, "9.75 6.00 3.00 1/500 1\n"),
    ],
)
def test_an_unreachable_or_busy_host_runs_the_shard_locally_and_says_why(rig, why, env, load):
    (rig["tmp"] / "loadavg").write_text(load)
    out = rig["tmp"] / "out.xml"
    r = _run(rig, "--junit", str(out), "tests/test_x.py", **env)
    said = r.stdout + r.stderr
    assert r.returncode == 0, said
    assert why in said and "locally" in said
    assert 'name="test_a"' in out.read_text()
    assert "docker run" not in (_log(rig) if (rig["tmp"] / "log").exists() else "")


def test_the_worker_stays_up_and_a_container_of_another_image_is_replaced(rig):
    assert _run(rig, "tests/test_x.py").returncode == 0
    assert _run(rig, "tests/test_x.py").returncode == 0
    lines = _log(rig).splitlines()
    assert sum(ln.startswith("$ docker run -d") for ln in lines) == 1  # the warm run only execs
    assert sum(ln.startswith("$ docker exec") and "pytest" in ln for ln in lines) == 2
    box = rig["images"] / "container"
    box.write_text("goh-remote-worker:000000000000\n" + box.read_text().split("\n")[1])
    (rig["tmp"] / "log").write_text("")
    assert _run(rig, "tests/test_x.py").returncode == 0
    log = _log(rig)
    assert "docker ps -aq --filter label=goh.remote-shard=1" in log and "flock" in log
    assert log.index("$ docker rm -f goh-remote-worker") < log.index("$ docker run -d --name")


def test_scratch_under_its_free_floor_runs_the_shard_locally(rig):
    out = rig["tmp"] / "out.xml"
    r = _run(rig, "--junit", str(out), "tests/test_x.py", GOH_REMOTE_FREE_FLOOR_GB="999999")
    said = r.stdout + r.stderr
    assert r.returncode == 0, said
    assert "floor" in said and "locally" in said


def test_a_wiped_worker_dir_is_a_cold_cache_rebuilt_with_no_manual_step(rig):
    assert _run(rig, "tests/test_x.py").returncode == 0
    import shutil

    shutil.rmtree(rig["remote"])
    (rig["images"] / "container").unlink()
    r = _run(rig, "tests/test_x.py")
    said = r.stdout + r.stderr
    assert r.returncode == 0, said
    assert "cold" in said and "locally" not in said
    assert sum(ln.startswith("$ docker build") for ln in _log(rig).splitlines()) == 1


def test_remote_failures_are_reported_as_failures_not_refusals(rig):
    (rig["tmp"] / "junit.xml").write_text(_junit(skipped=("test_d",), failed=("test_b",)))
    r = _run(rig, "tests/test_x.py", FAKE_RC="1")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "test_b" in r.stdout + r.stderr
    assert "locally" not in r.stdout + r.stderr


def test_the_dockerfile_names_only_releases_and_checksums_every_download():
    text = DOCKERFILE.read_text()
    froms = re.findall(r"^FROM\s+(\S+)", text, re.MULTILINE)
    assert froms, "no FROM"
    for ref in froms:
        assert re.fullmatch(r"[\w./-]+:\d+\.\d+\.\d+[\w.-]*@sha256:[0-9a-f]{64}", ref), ref
    assert not re.search(r":(latest|main|edge|nightly|stable)\b", text)
    args = dict(re.findall(r"^ARG\s+(\w+)=(\S+)", text, re.MULTILINE))
    versions = {k: v for k, v in args.items() if k.endswith("_VERSION")}
    assert versions
    for k, v in versions.items():
        assert re.fullmatch(r"\d+\.\d+\.\d+", v), f"{k}={v} is not a named release"
    bare = set(re.findall(r"^ARG\s+(\w+)\s*$", text, re.MULTILINE))
    for url_var in set(re.findall(r"curl [^\n]*?\$\{(\w+)_VERSION\}", text)):
        sha = f"{url_var}_SHA256"  # a pinned default, or a build arg read from goh's own table
        assert sha in bare or re.fullmatch(r"[0-9a-f]{64}", args.get(sha, "")), url_var
        assert re.search(rf"\$\{{{sha}\}}\s+\S+\"\s*\|\s*sha256sum -c", text), url_var


def test_every_tool_the_manifest_versions_is_a_build_arg_without_a_default():
    text = DOCKERFILE.read_text()
    bare = set(re.findall(r"^ARG\s+(\w+)\s*$", text, re.MULTILINE))
    owned = {m.upper().replace("-", "_") + "_VERSION" for m in _pins()}
    owned |= {"SHELLCHECK_VERSION", "SHELLCHECK_SHA256", "CARGO_LLVM_COV_VERSION"}
    assert owned <= bare, f"written in the Dockerfile, not read from the manifest: {owned - bare}"
    rows = (ln.split("\t") for ln in TSV.read_text().splitlines())
    floors = {f[1]: f[5] for f in rows if len(f) == 6}
    base = re.search(r"^FROM\s+python:(\d+\.\d+)\.\d+", text, re.MULTILINE)
    assert base and base.group(1) == floors["python3"]


def test_only_new_commits_and_uncommitted_tracked_edits_travel(rig):
    tree = rig["tree"]
    assert _run(rig, "tests/test_x.py").returncode == 0
    (tree / "tests" / "test_y.py").write_text("def test_y(): pass\n")
    subprocess.run(["git", "-C", str(tree), "add", "-A"], check=True, stdin=subprocess.DEVNULL)
    subprocess.run(["git", "-C", str(tree), "-c", "user.name=t", "-c", "user.email=t@t", "commit",
                    "-qm", "two"], check=True, stdin=subprocess.DEVNULL)  # fmt: skip
    (tree / "tests" / "test_x.py").write_text("def test_a(): assert True\n")
    (tree / "untracked2.txt").write_text("never sent")
    (rig["tmp"] / "log").write_text("")
    assert _run(rig, "tests/test_x.py").returncode == 0
    remote = _slot_tree(rig)
    assert (remote / "tests" / "test_y.py").is_file()
    assert (remote / "tests" / "test_x.py").read_text() == "def test_a(): assert True\n"
    assert not (remote / "untracked2.txt").exists()
    assert not list(remote.rglob("._*"))
    head1 = _git(tree, "rev-parse", "HEAD~1").stdout.strip()
    probe = _git(remote, "rev-parse", "HEAD").stdout.strip()
    assert probe != head1
    bundle = rig["remote"] / "out" / f"sync-{remote.name}.bundle"
    assert _git(tree, "bundle", "list-heads", str(bundle)).returncode == 0
    verify = _git(tree, "bundle", "verify", str(bundle))
    assert (
        "requires" in verify.stdout + verify.stderr and head1[:7] in verify.stdout + verify.stderr
    )
