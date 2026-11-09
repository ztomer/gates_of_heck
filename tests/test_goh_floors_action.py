"""CI meets goh's floors ONE way, read from goh's own floor table (G7, 2026-10-26).

goh raised python3 to 3.15 (G2d) and shellcheck to 0.11.0 (G2c); GitHub's runners ship 3.12.3 and
0.9.0, and divoom-control's CI run 40291804362 went red on both. Every consumer workflow chose its
own Python and its own shellcheck, so every floor bump was a sweep. `.github/actions/goh-floors`
is the one way: its floors.sh reads gates/required_tools.tsv, installs a checksum-verified
shellcheck release when the runner's is below the floor, and asks goh's own judges, which fail
naming the floor. Both directions are pinned here on planted tools: a runner below a floor is red
naming it, one at or above it is green.
"""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ACTION = ROOT / ".github" / "actions" / "goh-floors"
FLOORS = ACTION / "floors.sh"
SUMS = ACTION / "shellcheck.sha256"
sys.path.insert(0, str(ROOT / "gates"))
import required_tools

FLOOR = required_tools.floors()


def _shellcheck(directory: Path, version: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "shellcheck"
    path.write_text(
        "#!/bin/sh\necho 'ShellCheck - shell script analysis tool'\n"
        f"echo 'version: {version}'\necho 'license: GNU General Public License, version 3'\n"
    )
    path.chmod(0o755)
    return path


def _python(directory: Path, version: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "python3"
    path.write_text(f"#!/bin/sh\necho {version}\n")
    path.chmod(0o755)
    return path


def _run(*args: str, path: str, **extra: str) -> subprocess.CompletedProcess:
    """floors.sh as a runner meets it: the newest Python there is BELOW the floor (GitHub's 3.12.3,
    planted), unless a test names one. This machine's /opt/homebrew 3.15 hid exactly that: the TUI
    library's trampoline refused the runner's Python, and floors.sh died before printing a floor
    (Adv360 run 40304098540)."""
    extra.setdefault("GOH_PY_SEARCH", str(_python(Path(_RUNNER_PY.name), "3.12.3").parent))
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("GOH_FLOORS_", "GOH_PY")) and k != "NO_COLOR"
    }
    env.update(PATH=path, NO_COLOR="1", **extra)
    return subprocess.run(
        ["bash", str(FLOORS), *args],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=120, check=False,
    )  # fmt: skip


def _release(tmp: Path, version: str, plat: str, reports: str) -> tuple[str, str]:
    """A release dir laid out like GitHub's: <base>/v<version>/shellcheck-v<version>.<plat>.tar.gz,
    whose binary reports `reports`. Returns (file:// base, sha256)."""
    script = (
        f"#!/bin/sh\necho 'ShellCheck - shell script analysis tool'\necho 'version: {reports}'\n"
    ).encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(f"shellcheck-v{version}/shellcheck")
        info.size, info.mode = len(script), 0o755
        tar.addfile(info, io.BytesIO(script))
    out = tmp / "rel" / f"v{version}"
    out.mkdir(parents=True)
    (out / f"shellcheck-v{version}.{plat}.tar.gz").write_bytes(buf.getvalue())
    return f"file://{tmp / 'rel'}", hashlib.sha256(buf.getvalue()).hexdigest()


def _sums(tmp: Path, rows: list[tuple[str, str, str]]) -> Path:
    path = tmp / "sums"
    path.write_text("".join(f"shellcheck\t{v}\t{p}\t{s}\n" for v, p, s in rows))
    return path


BASE_PATH = "/usr/bin:/bin"
_RUNNER_PY = tempfile.TemporaryDirectory(prefix="goh-floors-runner-py.")


def test_the_floors_come_from_the_table_not_a_copy() -> None:
    for tool in ("python3", "shellcheck"):
        done = _run("floor", tool, path=BASE_PATH)
        assert done.returncode == 0, done.stderr
        assert done.stdout.strip() == FLOOR[tool]
    action = (ACTION / "action.yml").read_text()
    assert FLOOR["python3"] not in action and FLOOR["shellcheck"] not in action


def test_a_raised_floor_is_followed(tmp_path: Path) -> None:
    tsv = tmp_path / "t.tsv"
    tsv.write_text("python\tpython3\tsystem\tx\tlib/house_python.sh\t3.99\n")
    done = _run("floor", "python3", path=BASE_PATH, GOH_FLOORS_TSV=str(tsv))
    assert done.stdout.strip() == "3.99"


def test_a_shellcheck_at_the_floor_is_green_and_downloads_nothing(tmp_path: Path) -> None:
    sc = _shellcheck(tmp_path / "have", FLOOR["shellcheck"])
    done = _run("shellcheck", str(tmp_path / "bin"), path=f"{sc.parent}:{BASE_PATH}",
                GOH_FLOORS_URL="file:///nonexistent")  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert "meets the floor" in done.stdout and not (tmp_path / "bin").exists()


def test_a_shellcheck_below_the_floor_is_replaced_by_the_verified_release(tmp_path: Path) -> None:
    sc = _shellcheck(tmp_path / "have", "0.9.0")
    base, sha = _release(tmp_path, "9.0.0", "linux.x86_64", "9.0.0")
    done = _run(
        "shellcheck", str(tmp_path / "bin"), path=f"{sc.parent}:{BASE_PATH}",
        GOH_FLOORS_URL=base, GOH_FLOORS_SUMS=str(_sums(tmp_path, [("9.0.0", "linux.x86_64", sha)])),
        GOH_FLOORS_OS="Linux", GOH_FLOORS_ARCH="x86_64",
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "bin" / "shellcheck").is_file()
    assert "below the floor" in done.stdout


def test_a_tampered_release_is_refused(tmp_path: Path) -> None:
    base, _sha = _release(tmp_path, "9.0.0", "linux.x86_64", "9.0.0")
    done = _run(
        "shellcheck", str(tmp_path / "bin"), path=BASE_PATH, GOH_FLOORS_URL=base,
        GOH_FLOORS_SUMS=str(_sums(tmp_path, [("9.0.0", "linux.x86_64", "0" * 64)])),
        GOH_FLOORS_OS="Linux", GOH_FLOORS_ARCH="x86_64",
    )  # fmt: skip
    assert done.returncode != 0 and "does not match its recorded sha256" in done.stderr
    assert not (tmp_path / "bin" / "shellcheck").exists()


def test_a_floor_above_every_verified_release_is_refused_naming_it(tmp_path: Path) -> None:
    tsv = tmp_path / "t.tsv"
    tsv.write_text("structural\tshellcheck\tbrew\tx\tcrates/goh/src/shell_lint.rs\t99.0.0\n")
    done = _run("shellcheck", str(tmp_path / "bin"), path=BASE_PATH, GOH_FLOORS_TSV=str(tsv),
                GOH_FLOORS_OS="Linux", GOH_FLOORS_ARCH="x86_64")  # fmt: skip
    assert done.returncode != 0
    assert "no verified shellcheck release at or above the floor 99.0.0" in done.stderr


@pytest.mark.parametrize(
    "os_, arch, ok",
    [
        ("Linux", "x86_64", True),
        ("Linux", "aarch64", True),
        ("Darwin", "arm64", True),
        ("Darwin", "x86_64", False),
        ("Linux", "i686", False),
        ("Linux", "armv7l", False),
        ("MINGW64_NT", "x86_64", False),
    ],
)
def test_the_platform_gate_is_closed_both_ways(tmp_path: Path, os_: str, arch: str, ok: bool):
    plat = {"Linux/x86_64": "linux.x86_64", "Linux/aarch64": "linux.aarch64"}.get(
        f"{os_}/{arch}", "darwin.aarch64"
    )
    base, sha = _release(tmp_path, "9.0.0", plat, "9.0.0")
    done = _run(
        "shellcheck", str(tmp_path / "bin"), path=BASE_PATH, GOH_FLOORS_URL=base,
        GOH_FLOORS_SUMS=str(_sums(tmp_path, [("9.0.0", plat, sha)])),
        GOH_FLOORS_OS=os_, GOH_FLOORS_ARCH=arch,
    )  # fmt: skip
    assert (done.returncode == 0) is ok, done.stdout + done.stderr
    if not ok:
        assert "is not a supported runner" in done.stderr


def test_every_platform_has_a_verified_release_at_the_current_floor() -> None:
    """The commit that raises the shellcheck floor must record the release that meets it."""
    have: dict[str, list[str]] = {}
    for line in SUMS.read_text().splitlines():
        if line and not line.startswith("#"):
            tool, version, plat, sha = line.split("\t")
            assert tool == "shellcheck" and len(sha) == 64, line
            have.setdefault(plat, []).append(version)
    for plat in ("linux.x86_64", "linux.aarch64", "darwin.aarch64"):
        assert any(required_tools.at_least(v, FLOOR["shellcheck"]) for v in have.get(plat, [])), (
            plat
        )


def test_verify_is_red_naming_the_python_floor(tmp_path: Path) -> None:
    py = _python(tmp_path / "py", "3.12.3")
    sc = _shellcheck(tmp_path / "sc", FLOOR["shellcheck"])
    done = _run("verify", path=f"{sc.parent}:{BASE_PATH}", GOH_PY_SEARCH=str(py.parent))
    assert done.returncode != 0
    assert f"the house Python is {FLOOR['python3']} or newer" in done.stderr
    assert "3.12.3" in done.stderr


def test_verify_is_red_naming_the_shellcheck_floor(tmp_path: Path) -> None:
    sc = _shellcheck(tmp_path / "sc", "0.9.0")
    done = _run("verify", path=f"{sc.parent}:{Path(sys.executable).parent}:{BASE_PATH}",
                GOH_PY=sys.executable)  # fmt: skip
    assert done.returncode != 0
    assert f"has 0.9.0, the floor is {FLOOR['shellcheck']}" in done.stderr


def test_verify_is_green_at_the_floors(tmp_path: Path) -> None:
    sc = _shellcheck(tmp_path / "sc", FLOOR["shellcheck"])
    done = _run("verify", path=f"{sc.parent}:{Path(sys.executable).parent}:{BASE_PATH}",
                GOH_PY=sys.executable)  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert "meet goh's floors" in done.stdout


def test_a_failed_floor_read_fails_its_step() -> None:
    """A `$(...)` inside an echo's argument is invisible to `set -e`: the step that read the floor
    went green with an empty one, and setup-python then installed nothing (Adv360 run 40304098540).
    Every command substitution in the action is captured into a variable first."""
    for n, line in enumerate((ACTION / "action.yml").read_text().splitlines(), 1):
        if line.strip().startswith("echo") and "$(" in line:
            raise AssertionError(f"action.yml:{n}: a $(...) inside an echo: {line.strip()}")


# GOH.FLOORSRETRY (2026-11-09): one transient GitHub outage turned five consumer runs red on
# "could not download .../shellcheck/releases/download/v0.11.0/..."; each passed on a rerun. The
# download is retried (bounded), the verified tarball is cached across runs, and an exhausted retry
# names the download, not the repository.


def _fetcher(tmp: Path, fails: int) -> tuple[Path, Path]:
    """A downloader that fails its first FAILS calls, then copies the file:// URL. Returns
    (the command, the call count file)."""
    calls = tmp / "fetch.calls"
    cmd = tmp / "fetch"
    cmd.write_text(
        "#!/bin/bash\n"
        f'n=$(( $(cat "{calls}" 2>/dev/null || echo 0) + 1 )); echo "$n" > "{calls}"\n'
        f'[ "$n" -gt {fails} ] || {{ echo "curl: (22) The requested URL returned error: 504" >&2; exit 22; }}\n'
        'cp "${1#file://}" "$2"\n'
    )
    cmd.chmod(0o755)
    return cmd, calls


def _below_floor(tmp: Path, fails: int, **extra: str) -> tuple[subprocess.CompletedProcess, Path]:
    base, sha = _release(tmp, "9.0.0", "linux.x86_64", "9.0.0")
    fetch, calls = _fetcher(tmp, fails)
    done = _run(
        "shellcheck", str(tmp / "bin"), path=BASE_PATH, GOH_FLOORS_URL=base,
        GOH_FLOORS_SUMS=str(_sums(tmp, [("9.0.0", "linux.x86_64", sha)])),
        GOH_FLOORS_OS="Linux", GOH_FLOORS_ARCH="x86_64", GOH_FLOORS_FETCH=str(fetch),
        GOH_FLOORS_BACKOFF="0", **extra,
    )  # fmt: skip
    return done, calls


def test_a_download_that_fails_twice_then_succeeds_installs(tmp_path: Path) -> None:
    done, calls = _below_floor(tmp_path, 2)
    assert done.returncode == 0, done.stderr
    assert calls.read_text().strip() == "3"
    assert (tmp_path / "bin" / "shellcheck").is_file()
    assert "attempt 1 of 4 failed" in done.stderr


def test_a_download_that_always_fails_names_the_download_not_the_repo(tmp_path: Path) -> None:
    done, calls = _below_floor(tmp_path, 99, GOH_FLOORS_ATTEMPTS="3")
    assert done.returncode != 0
    assert calls.read_text().strip() == "3", "the retry is bounded by GOH_FLOORS_ATTEMPTS"
    assert "INFRASTRUCTURE, not this repository" in done.stderr, done.stderr
    assert "shellcheck-v9.0.0.linux.x86_64.tar.gz failed 3 times" in done.stderr, done.stderr
    assert not (tmp_path / "bin" / "shellcheck").exists()


def test_a_cached_tarball_is_verified_and_downloads_nothing(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    first, _ = _below_floor(tmp_path / "a", 0, GOH_FLOORS_CACHE=str(cache))
    assert first.returncode == 0, first.stderr
    assert (cache / "shellcheck-v9.0.0.linux.x86_64.tar.gz").is_file(), "the download was not kept"
    (tmp_path / "b").mkdir()
    sha = hashlib.sha256((cache / "shellcheck-v9.0.0.linux.x86_64.tar.gz").read_bytes()).hexdigest()
    fetch, calls = _fetcher(tmp_path / "b", 99)
    done = _run(
        "shellcheck", str(tmp_path / "b" / "bin"), path=BASE_PATH, GOH_FLOORS_URL="file:///nonexistent",
        GOH_FLOORS_SUMS=str(_sums(tmp_path / "b", [("9.0.0", "linux.x86_64", sha)])),
        GOH_FLOORS_OS="Linux", GOH_FLOORS_ARCH="x86_64", GOH_FLOORS_FETCH=str(fetch),
        GOH_FLOORS_BACKOFF="0", GOH_FLOORS_CACHE=str(cache),
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert not calls.exists(), "a verified cache hit still downloaded"
    assert "from the cache" in done.stdout + done.stderr


def test_a_tampered_cache_is_dropped_and_fetched_again(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "shellcheck-v9.0.0.linux.x86_64.tar.gz").write_bytes(b"not the release")
    done, calls = _below_floor(tmp_path, 0, GOH_FLOORS_CACHE=str(cache))
    assert done.returncode == 0, done.stderr
    assert calls.read_text().strip() == "1"
    assert "dropped, fetched again" in done.stderr


def test_the_cache_is_keyed_by_version_and_sha256(tmp_path: Path) -> None:
    sums = _sums(tmp_path, [("9.0.0", "linux.x86_64", "ab" * 32)])
    tsv = tmp_path / "t.tsv"
    tsv.write_text("structural\tshellcheck\tbrew\tx\tcrates/goh/src/shell_lint.rs\t9.0.0\n")
    done = _run("cache-key", path=BASE_PATH, GOH_FLOORS_SUMS=str(sums), GOH_FLOORS_TSV=str(tsv),
                GOH_FLOORS_OS="Linux", GOH_FLOORS_ARCH="x86_64")  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == f"shellcheck-9.0.0-linux.x86_64-{'ab' * 32}"
    action = (ACTION / "action.yml").read_text()
    assert "uses: actions/cache@" in action
    assert "key: goh-floors-${{ steps.key.outputs.shellcheck }}" in action
    assert 'GOH_FLOORS_CACHE="$RUNNER_TEMP/goh-floors/cache"' in action


# GOH.GATEHONEST (4): on a runner whose shellcheck already meets the floor nothing is downloaded,
# so the cache dir is never made, and actions/cache's post step warned "Path Validation Error" on
# every run. floors.sh says whether a release is needed, and the cache step runs only then.
def test_a_runner_at_the_floor_needs_no_release(tmp_path: Path) -> None:
    sc = _shellcheck(tmp_path / "have", FLOOR["shellcheck"])
    done = _run("needs-release", path=f"{sc.parent}:{BASE_PATH}")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "false"


def test_a_runner_below_the_floor_or_without_one_needs_the_release(tmp_path: Path) -> None:
    sc = _shellcheck(tmp_path / "have", "0.9.0")
    below = _run("needs-release", path=f"{sc.parent}:{BASE_PATH}")
    assert below.returncode == 0 and below.stdout.strip() == "true", below.stderr
    none = _run("needs-release", path=BASE_PATH)
    assert none.returncode == 0 and none.stdout.strip() == "true", none.stderr


def test_the_cache_step_runs_only_when_a_release_is_needed() -> None:
    action = (ACTION / "action.yml").read_text()
    assert 'floors.sh" needs-release)' in action
    assert 'echo "needed=$needed" >> "$GITHUB_OUTPUT"' in action
    cache = action[action.index("uses: actions/cache@") :]
    head = action[: action.index("uses: actions/cache@")].rsplit("- ", 1)[1]
    assert "if: steps.key.outputs.needed == 'true'" in head + cache.split("\n    - ", 1)[0], (
        "the cache step runs on a runner that downloads nothing"
    )
