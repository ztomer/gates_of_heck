"""_cov_run_tests.py -- run the test binaries of a `cargo test --no-run` build CONCURRENTLY.

usage: _cov_run_tests.py <cargo-json>     (run in the project, in the `cargo llvm-cov show-env` env)

`cargo test --workspace` runs its test binaries one after another, each parallel only inside itself.
monitor's coverage step (2026-11-15, load 87 -> 40): 82 binaries, most under a second, five of them
12-31 s that serialise their own tests behind a mutex, so the cores idled through 197 s of serially
run binaries. The binaries are already built (the JSON the build step wrote names every one: a
`compiler-artifact` whose `profile.test` is true), and every process writes its own profile
(`LLVM_PROFILE_FILE` carries `%p-%m`), so the run needs no cargo: this starts up to JOBS of them at
once, each as `cargo test` would -- cwd the package's root, the CARGO_PKG_* / CARGO_MANIFEST_DIR
variables of its manifest, CARGO_BIN_EXE_<name> for its package's binaries, the library path of
its dependencies, the `[env]` table of cargo's config files (`_cargo_env_table`), in the gate's own
session (a process group of its own, never a session: a session leader that opens a tty takes it as
its controlling terminal, which cargo test never lets a test do) -- and reports like it: each
binary's output whole, the failures LAST (so the gate's tail and its `test X ... FAILED` /
`test result: FAILED` readers see them), exit 101 when any binary failed, never fail-fast (a red
binary does not hide a second).

JOBS: GOH_COV_RUST_JOBS, else 6; 1 is the old serial run. The biggest binaries start
first (a long one started last is the whole step's tail). A build that names NO test executable is
refused: a run of nothing is not a pass. The line at the end says how many ran, at what width,
and the wall time against the sum of the binaries' own (the speedup, on the log).
"""

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(
        ModuleNotFoundError
    ):  # a copy outside any checkout: nothing to re-run
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]


import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import tomllib
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

FAILED = 101  # cargo test's own exit code for a red test binary
DEFAULT_JOBS = 6  # explicit, never sized to the machine (tests/test_suite_drift.py)


def _artifacts(json_path: Path) -> tuple[list[dict], dict[str, dict[str, str]]]:
    """(test executables, package id -> {bin target name: its executable})."""
    tests: dict[str, dict] = {}
    bins: dict[str, dict[str, str]] = {}
    for line in json_path.read_text().splitlines():
        if not line.startswith("{"):
            continue
        msg = json.loads(line)
        exe = msg.get("executable")
        if msg.get("reason") != "compiler-artifact" or not exe:
            continue
        if msg.get("profile", {}).get("test"):
            tests[exe] = msg
        elif "bin" in msg.get("target", {}).get("kind", []):
            bins.setdefault(msg.get("package_id", ""), {})[msg["target"]["name"]] = exe
    return list(tests.values()), bins


def _package_env(pkg: dict) -> dict[str, str]:
    """The variables cargo sets for a test run of this package (cargo's `fill_env`)."""
    version = pkg.get("version", "")
    core, _, pre = version.partition("-")
    parts = [*core.split("+")[0].split("."), "", "", ""][:3]
    manifest = pkg["manifest_path"]
    return {
        "CARGO_MANIFEST_DIR": str(Path(manifest).parent),
        "CARGO_MANIFEST_PATH": manifest,
        "CARGO_PKG_NAME": pkg["name"],
        "CARGO_PKG_VERSION": version,
        "CARGO_PKG_VERSION_MAJOR": parts[0],
        "CARGO_PKG_VERSION_MINOR": parts[1],
        "CARGO_PKG_VERSION_PATCH": parts[2],
        "CARGO_PKG_VERSION_PRE": pre.split("+")[0],
        "CARGO_PKG_AUTHORS": ":".join(pkg.get("authors") or []),
        "CARGO_PKG_DESCRIPTION": pkg.get("description") or "",
        "CARGO_PKG_HOMEPAGE": pkg.get("homepage") or "",
        "CARGO_PKG_REPOSITORY": pkg.get("repository") or "",
        "CARGO_PKG_LICENSE": pkg.get("license") or "",
        "CARGO_PKG_LICENSE_FILE": pkg.get("license_file") or "",
        "CARGO_PKG_RUST_VERSION": pkg.get("rust_version") or "",
        "CARGO_PKG_README": pkg.get("readme") or "",
    }


def _cargo_env_table(cwd: Path, environ: Mapping[str, str], cargo_home: Path) -> dict[str, str]:
    """The variables cargo's `[env]` table sets for a process it runs from `cwd` (GOH.SWEEPREDS130).

    Every `.cargo/config.toml` (else the legacy `.cargo/config`) from `cwd` up to the root, then
    `cargo_home/config.toml`; a deeper file wins a key. An entry is a string or a table
    `{value, force, relative}`: it yields to a variable already in `environ` unless `force`, and a
    `relative` value is joined to the directory holding that `.cargo`. A file that is no TOML is
    refused naming it (ValueError), as cargo refuses it.
    """
    files = []
    for d in (cwd.resolve(), *cwd.resolve().parents):
        found = [
            d / ".cargo" / n for n in ("config.toml", "config") if (d / ".cargo" / n).is_file()
        ]
        files.extend(found[:1])
    home = cargo_home / "config.toml"
    if home.is_file() and home.resolve() not in {f.resolve() for f in files}:
        files.append(home)
    table: dict[str, tuple[object, Path]] = {}
    for f in reversed(files):  # the lowest first: a deeper file overwrites
        try:
            env = tomllib.loads(f.read_text()).get("env", {})
        except (tomllib.TOMLDecodeError, OSError) as e:
            raise ValueError(f"{f}: {e}") from e
        for key, entry in env.items():
            table[key] = (entry, f.parent.parent)
    out: dict[str, str] = {}
    for key, (entry, base) in table.items():
        spec = entry if isinstance(entry, dict) else {"value": entry}
        if key in environ and not spec.get("force", False):
            continue
        value = str(spec.get("value", ""))
        out[key] = str(base / value) if spec.get("relative", False) else value
    return out


def _libpath_var() -> str:
    return "DYLD_FALLBACK_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"


def _sysroot_lib() -> str:
    try:
        out = subprocess.run(
            ["rustc", "--print", "sysroot"], capture_output=True, text=True,
            stdin=subprocess.DEVNULL, check=False,
        ).stdout.strip()  # fmt: skip
    except OSError:  # no rustc on this PATH: the binaries need no toolchain library of their own
        return ""
    return str(Path(out) / "lib") if out else ""


class _Run:
    def __init__(self, label: str, argv: list[str], cwd: str, env: dict[str, str]):
        self.label, self.argv, self.cwd, self.env = label, argv, cwd, env
        self.rc = 0
        self.out = ""
        self.secs = 0.0


def split_share(jobs: int, runs: list[_Run], share: str) -> int:
    """The binaries run at once SPLIT the gate's share (GOH_GATE_WORKERS, GOH.POOLSHARE): at most
    `share` of them, each on share // jobs test threads -- `jobs` binaries each on the whole share
    were `jobs` gates' worth of threads. A repo's own lower RUST_TEST_THREADS (tests that cannot
    overlap) stays. No share (a run outside any gate): `jobs` and the binaries' defaults."""
    if not (share.strip().isdigit() and int(share) > 0):
        return jobs
    jobs = min(jobs, int(share))
    threads = max(1, int(share) // jobs)
    for run in runs:
        have = run.env.get("RUST_TEST_THREADS", "")
        keep = have.isdigit() and 0 < int(have) < threads
        run.env["RUST_TEST_THREADS"] = have if keep else str(threads)
    return jobs


def _go(run: _Run, live: set, lock: threading.Lock) -> _Run:
    t0 = time.monotonic()
    proc = subprocess.Popen(
        run.argv, cwd=run.cwd, env=run.env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
        process_group=0,  # a group for `stop` to kill; a SESSION would make it take a tty it opens
    )  # fmt: skip
    with lock:
        live.add(proc)
    run.out = proc.communicate()[0]
    run.rc = proc.returncode
    with lock:
        live.discard(proc)
    run.secs = time.monotonic() - t0
    return run


def main(argv: list[str]) -> int:
    json_path = Path(argv[1])
    tests, bins = _artifacts(json_path)
    if not tests:
        print(f"_cov_run_tests: {json_path} names no test executable -- nothing run is not a pass")
        return 1
    meta = json.loads(subprocess.run(
        ["cargo", "metadata", "--no-deps", "--format-version", "1"],
        check=True, capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout)  # fmt: skip
    by_manifest = {os.path.realpath(p["manifest_path"]): p for p in meta["packages"]}
    sysroot = _sysroot_lib()
    cargo_home = Path(os.environ.get("CARGO_HOME") or Path.home() / ".cargo")
    try:
        config_env = _cargo_env_table(Path.cwd(), os.environ, cargo_home)
    except ValueError as e:
        print(f"_cov_run_tests: a cargo config is unreadable: {e}")
        return 1
    runs: list[_Run] = []
    for msg in tests:
        exe = msg["executable"]
        pkg = by_manifest.get(os.path.realpath(msg.get("manifest_path", "")))
        if pkg is None:
            print(f"_cov_run_tests: no workspace package owns {msg.get('manifest_path')!r} ({exe})")
            return 1
        env = dict(os.environ)
        env.update(config_env)
        env.update(_package_env(pkg))
        env["CARGO_CRATE_NAME"] = msg["target"]["name"].replace("-", "_")
        for name, path in bins.get(msg.get("package_id", ""), {}).items():
            env[f"CARGO_BIN_EXE_{name}"] = path
        var = _libpath_var()
        dirs = [str(Path(exe).parent), sysroot, env.get(var, "")]
        env[var] = ":".join(d for d in dirs if d)
        kind = "unit tests" if "test" not in msg["target"]["kind"] else "integration test"
        label = f"{msg['target']['name']} ({kind}, {pkg['name']})"
        runs.append(_Run(label, [exe], str(Path(pkg["manifest_path"]).parent), env))
    runs.sort(key=lambda r: -os.path.getsize(r.argv[0]))  # the big ones first: the step's tail
    jobs = int(os.environ.get("GOH_COV_RUST_JOBS") or DEFAULT_JOBS)
    jobs = max(1, min(jobs, len(runs)))
    jobs = split_share(jobs, runs, os.environ.get("GOH_GATE_WORKERS", ""))

    live: set = set()
    lock = threading.Lock()

    def stop(signum, _frame):  # a stop signal takes every running binary with it
        with lock:
            for p in list(live):
                with contextlib.suppress(OSError):
                    os.killpg(p.pid, signal.SIGTERM)
        sys.exit(128 + signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGHUP, stop)

    print(f"running {len(runs)} test binaries, {jobs} at a time", flush=True)
    t0 = time.monotonic()
    red: list[_Run] = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for run in pool.map(lambda r: _go(r, live, lock), runs):
            if run.rc != 0:
                red.append(run)
                continue
            print(f"     Running {run.label}  [{run.secs:.1f}s]\n{run.out}", end="", flush=True)
    wall = time.monotonic() - t0
    for run in red:  # the failures LAST: the gate's tail and its FAILED readers see them
        print(f"     Running {run.label}  [{run.secs:.1f}s]\n{run.out}", end="", flush=True)
    serial = sum(r.secs for r in runs)
    print(
        f"ran {len(runs)} test binaries, {jobs} at a time: {wall:.1f} s wall, "
        f"{serial:.1f} s of binaries run one after another",
        flush=True,
    )
    if red:
        print("error: test failed in: " + ", ".join(r.label for r in red), flush=True)
        return FAILED
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
