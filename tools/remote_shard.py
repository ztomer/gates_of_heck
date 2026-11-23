"""`goh remote-shard [opts] TEST...`: run a Linux-safe test shard in a worker container on another
host and fold its junit back in (GOH.REMOTEWORKERS, owner 2026-11-22).

THE WORKER. `tools/remote_worker/Dockerfile` is the image: goh's suite tools at the manifest's
exact pins (gates/required_tools.tsv). It is built ON the host, once per Dockerfile content (tag
`goh-remote-worker:<sha256[:12]>`), so the image and this runner cannot drift silently. It runs as
ONE long-lived container (GOH_REMOTE_CONTAINER, `--init --restart unless-stopped`, idle on `sleep
infinity`; owner 2026-11-22: no start-up per run), and each shard is a `docker exec` into it. The
probe starts it when absent and replaces it when it runs another image; any other container
carrying the label is reaped then. Its limits: cpuset 4-15 (the six physical cores the owner gave
it) at cpu-shares 128 with no `--cpus` cap, so the host's own processes win on those cpus too;
10 GiB memory with no extra swap (an OOM kills this container, never a media service); 4096 pids;
a non-root uid; no capabilities, no new privileges, no network; one mount. No disk throttle: the
work dir is its own SSD (and on this cgroup-v2 host `--blkio-weight 100` writes io.weight 910,
ABOVE the default 100). It is not a service of the host's compose project.

THE ONE MOUNT (GOH_REMOTE_DIR, default /mnt/scratch/goh-worker: the host's transcode-scratch SSD,
apart from the media pool) holds `tree/` (a git checkout, its cargo target/ inside), `cache/`
(HOME, so CARGO_HOME is $HOME/.cargo; one pytest cache per lane slot) and `out/` (each run's junit
and log). It is a CACHE: wiped, the next run rebuilds it with no manual step -- the probe says
"cold", the commits travel whole once, and the registry is fetched once per Cargo.lock by a short-
lived NETWORKED container with the same limits (`cargo fetch --locked`). The probe refuses (local
fallback) under 100 GB free on that disk (the transcodes' room) or over an 80 GB worker dir.

A RUN, in four ssh calls over one ControlMaster connection (ControlPersist, so no handshake):
  1. probe: load, free space, the container's image and its recorded tool versions;
  2. the tree: a git bundle of only the commits the worker lacks, then the uncommitted TRACKED
     edits as a tar (untracked files never travel; see `sync`);
  3. under `flock out/run.lock` (one tree, one run at a time): build bin/goh incrementally in the
     tree's own target/ (`git clean` spares ignored output), then pytest under
     `timeout`; the junit comes back on the same call.
COMPRESSION IS OFF, by measurement (2026-11-22, this LAN): a 10.2 MB full bundle took 0.36 s plain
and 0.46 s with `ssh -C` (packs are zlib already); a 31 KB junit shrinks to 6.4 KB under zstd -3,
a saving of well under a millisecond on this link.

IT NEVER FAILS A LAND FOR ITS OWN REASONS. An unreachable host, a load above GOH_REMOTE_MAX_LOAD,
a worker whose versions differ from the manifest or the Mac's Python, a remote run with no junit,
and a remote run whose SKIP count differs from the Mac's for the same tests (`--mac-junit`) each
print why and run the shard LOCALLY instead. A skip is how a missing tool hides; a remote green
that skipped what the Mac ran is not the same verdict.

Exit: 0 the shard passed (remote or local), 1 a test failed, 2 usage.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shlex
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / "lib"))
import gitrun

from tui.lib import info, ok, warn

ROOT = Path(__file__).resolve().parent.parent
LABEL = "goh.remote-shard=1"
RUN_TIMEOUT_S = 3600
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ControlMaster=auto",
            "-o", "ControlPath=~/.ssh/goh-remote-%C", "-o", "ControlPersist=600"]  # fmt: skip


class Fallback(Exception):
    """A reason the shard runs locally instead."""


def conf() -> dict[str, str]:
    """The worker's configuration: host, directory and limits, each overridable by env."""
    e = os.environ.get
    return {
        "host": e("GOH_REMOTE_HOST", "media-server"),
        "dir": e("GOH_REMOTE_DIR", "/mnt/scratch/goh-worker"),
        "cpuset": e("GOH_REMOTE_CPUSET", "4-15"),
        "shares": e("GOH_REMOTE_CPU_SHARES", "128"),
        "memory": e("GOH_REMOTE_MEMORY", "10g"),
        "max_load": e("GOH_REMOTE_MAX_LOAD", "6.0"),
        "loadavg": e("GOH_REMOTE_LOADAVG", "/proc/loadavg"),
        "uid": e("GOH_REMOTE_USER", "1000:1000"),
        "container": e("GOH_REMOTE_CONTAINER", "goh-remote-worker"),
        "floor_gb": e("GOH_REMOTE_FREE_FLOOR_GB", "100"),
        "ceiling_gb": e("GOH_REMOTE_CEILING_GB", "80"),
    }


def ssh(c: dict[str, str], script: str, stdin: bytes | None = None,
        timeout: float = 120) -> subprocess.CompletedProcess[bytes]:  # fmt: skip
    """`script` run by bash on the host (its login shell may be zsh), output captured."""
    try:
        return subprocess.run(["ssh", *SSH_OPTS, c["host"], "bash -c " + shlex.quote(script)],
                              input=stdin, capture_output=True, timeout=timeout,
                              stdin=None if stdin is not None else subprocess.DEVNULL,
                              check=False)  # fmt: skip
    except subprocess.TimeoutExpired as exc:
        raise Fallback(f"{c['host']} did not answer in {timeout:.0f} s") from exc


def manifest_pins(tsv: Path) -> dict[str, str]:
    """The suite's pip modules and their exact pins (`=X.Y.Z` rows of the goh-suite layer)."""
    pins: dict[str, str] = {}
    for line in tsv.read_text().splitlines():
        f = line.split("\t")
        if len(f) == 6 and f[0] == "goh-suite" and f[2] == "pip" and f[5].startswith("="):
            pins[f[1].lower()] = f[5][1:]
    return pins


def version_drift(text: str, pins: dict[str, str]) -> list[str]:
    """Each way the worker's recorded versions differ from the manifest and the Mac's Python."""
    have: dict[str, str] = {}
    python = ""
    for line in text.splitlines():
        if line.startswith("Python "):
            python = line.split()[1]
        elif "==" in line:
            name, ver = line.split("==", 1)
            have[name.strip().lower().replace("_", "-")] = ver.strip()
    mac = ".".join(map(str, sys.version_info[:3]))
    out = [] if python == mac else [f"python {python or 'absent'} (Mac {mac})"]
    out += [
        f"{m} {have.get(m, 'absent')} (manifest {v})" for m, v in pins.items() if have.get(m) != v
    ]
    return out


def build_args(tsv: Path, tree: Path) -> list[str]:
    """`KEY=VALUE` for every Dockerfile arg goh's manifest owns: each pinned pip module, shellcheck
    (with its linux.x86_64 checksum from the goh-floors table) and cargo-llvm-cov."""
    floors: dict[str, str] = {}
    for line in tsv.read_text().splitlines():
        f = line.split("\t")
        if len(f) == 6 and f[5] != "-":
            floors.setdefault(f[1].lower(), f[5].lstrip("="))
    args = {m.upper().replace("-", "_") + "_VERSION": v for m, v in manifest_pins(tsv).items()}
    for tool in ("shellcheck", "cargo-llvm-cov"):
        args[tool.upper().replace("-", "_") + "_VERSION"] = floors[tool]
    table = tree / ".github" / "actions" / "goh-floors" / "shellcheck.sha256"
    for line in table.read_text().splitlines() if table.exists() else []:
        f = line.split("\t")
        if len(f) == 4 and f[1] == floors["shellcheck"] and f[2] == "linux.x86_64":
            args["SHELLCHECK_SHA256"] = f[3]
    return [f"{k}={v}" for k, v in sorted(args.items())]


def image_tag(tree: Path, args: list[str]) -> tuple[str, Path]:
    """The image's tag: its Dockerfile's content AND the build args, so a raised pin is new."""
    dockerfile = tree / "tools" / "remote_worker" / "Dockerfile"
    digest = hashlib.sha256(dockerfile.read_bytes() + "\n".join(args).encode()).hexdigest()
    return f"goh-remote-worker:{digest[:12]}", dockerfile


def limits(c: dict[str, str]) -> list[str]:
    """The confinement every worker container carries (module doc), its one mount included."""
    w = "/work"
    return ["--label", LABEL, "--cpuset-cpus", c["cpuset"], "--cpu-shares", c["shares"],
            "--memory", c["memory"], "--memory-swap", c["memory"], "--pids-limit", "4096",
            "--user", c["uid"], "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
            "-v", f"{c['dir']}:{w}", "-e", f"HOME={w}/cache/home",
            "-e", f"GOH_DIR={w}/tree", "-e", "PYTHONDONTWRITEBYTECODE=1"]  # fmt: skip


def probe(c: dict[str, str], tree: Path, tsv: Path, times: dict[str, float]) -> str:
    """The image tag, with its long-lived container running (built and started when absent or
    different); Fallback if the host is down, busy, short of scratch space or off-pin."""
    pins, args = manifest_pins(tsv), build_args(tsv, tree)
    tag, dockerfile = image_tag(tree, args)
    q, d, name = shlex.quote, shlex.quote(c["dir"]), c["container"]
    versions = f"docker exec {name} cat /opt/goh-worker-versions.txt"
    r = ssh(c, f"cat {q(c['loadavg'])}; mkdir -p {d}; echo $(df -Pk {d} | awk 'NR==2{{print $4}}') "
               f"$(du -sk {d} | cut -f1); have=$(docker inspect -f '{{{{.Config.Image}}}}|"
               f"{{{{.State.Running}}}}' {name} 2>/dev/null); "
               f"if ! docker image inspect {tag} >/dev/null 2>&1; then echo GOH-NO-IMAGE; "
               f"elif [ \"$have\" != '{tag}|true' ]; then echo GOH-NO-CONTAINER; "
               f"else {versions}; fi")  # fmt: skip
    if r.returncode != 0:
        raise Fallback(f"{c['host']} unreachable (ssh exit {r.returncode})")
    lines = [*r.stdout.decode(errors="replace").splitlines(), "", ""]
    load = float(lines[0].split()[0]) if lines[0].split() else 99.0
    if load > float(c["max_load"]):
        raise Fallback(f"{c['host']} busy: load {load:.2f} > {c['max_load']}")
    free_kb, used_kb = (int(x) for x in [*lines[1].split(), "0", "0"][:2])
    if free_kb < int(c["floor_gb"]) * 1024 * 1024:
        raise Fallback(f"{c['dir']}: {free_kb / 1048576:.0f} GB free, under the {c['floor_gb']} GB "
                       "floor the host's transcodes keep")  # fmt: skip
    if used_kb > int(c["ceiling_gb"]) * 1024 * 1024:
        raise Fallback(
            f"{c['dir']}: {used_kb / 1048576:.0f} GB, over its {c['ceiling_gb']} GB ceiling"
        )
    text = "\n".join(lines[2:])
    if "GOH-NO-IMAGE" in text or "GOH-NO-CONTAINER" in text:
        t0 = time.monotonic()
        build = "GOH-NO-IMAGE" in text
        info(
            f"building {tag} on {c['host']} (once per Dockerfile)"
            if build
            else f"cold: starting the worker container {name} on {c['host']}"
        )
        start = ["docker", "run", "-d", "--name", name, "--init", "--restart", "unless-stopped",
                 *limits(c), "--network", "none", tag, "sleep", "infinity"]  # fmt: skip
        # A container of this name that is not this image, or not running, is replaced; others
        # carrying the label (an older name, a crashed run) are reaped too.
        flags = " ".join(f"--build-arg {shlex.quote(a)}" for a in args)
        b = ssh(c, (f"docker build -q -t {tag} {flags} - >/dev/null && " if build else "")
                + f"mkdir -p {d}/out {d}/cache/home && "
                f"ids=$(docker ps -aq --filter label={LABEL}); "
                "[ -z \"$ids\" ] || docker rm -f $ids >/dev/null; "
                f"docker rm -f {name} >/dev/null 2>&1; "
                f"{shlex.join(start)} >/dev/null && {versions}",
                stdin=dockerfile.read_bytes() if build else None, timeout=3600)  # fmt: skip
        if b.returncode != 0:
            raise Fallback(f"the worker did not start: {b.stderr.decode()[-300:]}")
        text = b.stdout.decode(errors="replace")
        times["build" if build else "start"] = time.monotonic() - t0
    drift = version_drift(text, pins)
    if drift:
        raise Fallback("refused: the worker is off goh's pins: " + "; ".join(drift))
    return tag


def git(tree: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """A READ of the Mac's checkout (rev-parse, merge-base, bundle, diff): no optional locks."""
    argv = ["git", "--no-optional-locks", "-C", str(tree), *args]
    return subprocess.run(argv, capture_output=True, check=False, stdin=subprocess.DEVNULL,
                          timeout=120)  # fmt: skip


def sync(c: dict[str, str], tree: Path) -> None:
    """Make the worker's `tree/` a checkout of the Mac's HEAD plus its uncommitted TRACKED edits:
    only the commits the worker lacks travel (a git bundle against the commit it has; packs are
    already compressed), then the edited tracked files as a tar, deleted ones removed. Untracked
    files never travel; the worker's own untracked leftovers are cleaned (ignored build output,
    bin/goh and caches, stays)."""
    head = git(tree, "rev-parse", "HEAD").stdout.decode().strip()
    if not head:
        raise Fallback(f"{tree} has no HEAD to send")
    q, name = shlex.quote, c["container"]
    x = f"docker exec -i {name} git -C /work/tree"
    r = ssh(c, f"docker exec {name} sh -c 'git -C /work/tree rev-parse -q --verify HEAD "
               "2>/dev/null || true'")  # fmt: skip
    have = r.stdout.decode().strip()
    steps = [f"[ -d {q(c['dir'])}/tree/.git ] || docker exec {name} git init -q /work/tree"]
    bundle = b""
    if have != head:
        basis = (["--not", have] if have and git(tree, "merge-base", "--is-ancestor", have,
                                                  "HEAD").returncode == 0 else [])  # fmt: skip
        made = git(tree, "bundle", "create", "-", "HEAD", *basis)
        if made.returncode != 0:
            raise Fallback(f"git bundle failed: {made.stderr.decode()[-200:]}")
        bundle = made.stdout
        steps += [
            f"cat > {q(c['dir'])}/out/sync.bundle",
            f"{x} fetch -q /work/out/sync.bundle HEAD",
            f"{x} checkout -q -f --detach FETCH_HEAD",
        ]
    else:
        steps.append(f"{x} checkout -q -f --detach HEAD")
    steps.append(f"{x} clean -fdq")
    r = ssh(c, " && ".join(steps), stdin=bundle, timeout=600)
    if r.returncode != 0:
        raise Fallback(f"the tree did not sync: {r.stderr.decode()[-300:]}")
    changed = git(tree, "diff", "--name-only", "-z", "HEAD").stdout.decode().split("\0")
    changed = [f for f in changed if f]
    if not changed:
        return
    present = [f for f in changed if (tree / f).exists()]
    gone = [f for f in changed if not (tree / f).exists()]
    # macOS tar adds AppleDouble files and xattr headers unless told not to.
    tar = subprocess.run(["tar", "--no-mac-metadata", "--no-xattrs", "-cf", "-", "-C", str(tree),
                          "--", *present], capture_output=True, check=True,
                         stdin=subprocess.DEVNULL,
                         env={**os.environ, "COPYFILE_DISABLE": "1"},
                         ).stdout if present else b""  # fmt: skip
    cmd = f"docker exec -i {name} tar -xf - -C /work/tree" if present else "true"
    if gone:
        cmd += f" && docker exec {name} rm -f -- " + " ".join(q("/work/tree/" + f) for f in gone)
    r = ssh(c, cmd, stdin=tar, timeout=600)
    if r.returncode != 0:
        raise Fallback(f"the uncommitted edits did not sync: {r.stderr.decode()[-300:]}")


def run_remote(c: dict[str, str], tag: str, tests: list[str], workers: int, slot: str,
               times: dict[str, float]) -> tuple[int, str]:  # fmt: skip
    """The shard as one `docker exec` into the running worker; (pytest's exit code, the junit)."""
    rid = f"{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    q, d, w = shlex.quote, c["dir"], "/work"
    # goh's binary first: the tree is no checkout (no stamp), so build-goh.sh builds bin/goh from
    # the synced source, incrementally in the slot's warm target dir, offline.
    pytest = (f"cd {w}/tree && t0=$(date +%s%N) && if [ -f scripts/build-goh.sh ]; then "
              "bash scripts/build-goh.sh >&2 || exit 97; fi && "
              "echo GOH-BUILD-MS=$(( ($(date +%s%N) - t0) / 1000000 )) && "
              f"exec timeout {RUN_TIMEOUT_S} python3 -m pytest -q "
              f"-o cache_dir={w}/cache/pytest-{slot} -n {workers} --junitxml={w}/out/{rid}.xml "
              f"@{w}/out/{rid}.args")  # fmt: skip
    run = ["docker", "exec", c["container"],
           "bash", "-c", pytest]  # fmt: skip
    # The registry needs the network the worker has not: once per Cargo.lock, a networked
    # short-lived container with the same limits fills $HOME/.cargo (`cargo fetch --locked`).
    fetch = ["docker", "run", "--rm", "--name", f"{c['container']}-fetch", *limits(c), tag,
             "bash", "-c", f"cd {w}/tree && cargo fetch --locked -q"]  # fmt: skip
    used = f"{w}/tree/target/.goh-used {w}/cache/home/.cargo/.goh-used"
    evict = ["docker", "exec", c["container"], "bash", "-c",
             (f"mkdir -p {w}/tree/target {w}/cache/home/.cargo && touch {used} && "
              f"exec python3 {w}/tree/lib/worker_evict.py run --root {w} "
              f"--ceiling-gib {c['ceiling_gb']} --floor-gib {c['floor_gb']}")]  # fmt: skip
    inner = "\n".join([
        (f"if [ -f {q(d)}/tree/Cargo.lock ]; then "
         f"stamp={q(d)}/cache/home/.cargo/goh-fetched.$(sha256sum "
         f"{q(d)}/tree/Cargo.lock | cut -c1-16); if [ ! -f \"$stamp\" ]; then "
         "echo 'cold cache: fetching the cargo registry once' >&2; "
         f"{shlex.join(fetch)} >&2 && touch \"$stamp\"; fi; fi"),
        f"cat > {q(d)}/out/{rid}.args <<'SHARD_ARGS_EOF'", *tests, "SHARD_ARGS_EOF",
        f"{shlex.join(run)} > {q(d)}/out/{rid}.log 2>&1; rc=$?",
        # The worker's housekeeping (GOH.REMOTEEVICT), never the run's verdict: stamp what this
        # run used, then goh's evictor under the worker's rules; a week's run files go.
        f"{shlex.join(evict)} >&2 || echo 'worker evictor failed (the run stands)' >&2",
        f"find {q(d)}/out -maxdepth 1 -type f -mtime +7 -name '*.*' ! -name run.lock -delete",
        f"grep -h '^GOH-BUILD-MS=' {q(d)}/out/{rid}.log >&2; tail -3 {q(d)}/out/{rid}.log >&2",
        f"echo \"GOH-RC=$rc\"; cat {q(d)}/out/{rid}.xml 2>/dev/null",
    ])  # fmt: skip
    r = ssh(c, f"exec flock {q(d)}/out/run.lock bash -c {q(inner)}",
            timeout=RUN_TIMEOUT_S + 120)  # fmt: skip
    err = r.stderr.decode(errors="replace")
    sys.stderr.write(err)
    build_ms = [ln.split("=", 1)[1] for ln in err.splitlines() if ln.startswith("GOH-BUILD-MS=")]
    times["goh build"] = int(build_ms[0]) / 1000 if build_ms and build_ms[0].isdigit() else 0.0
    head, _, junit = r.stdout.decode(errors="replace").partition("\n")
    if r.returncode != 0 or not head.startswith("GOH-RC="):
        raise Fallback(f"the remote run did not finish (ssh exit {r.returncode})")
    return int(head.removeprefix("GOH-RC=")), junit


def cases(junit: str) -> dict[str, str]:
    """test id -> passed|skipped|failed, from a junit document."""
    out: dict[str, str] = {}
    for tc in ET.fromstring(junit).iter("testcase"):
        kind = "passed"
        for child in tc:
            if child.tag == "skipped":
                kind = "skipped"
            elif child.tag in ("failure", "error"):
                kind = "failed"
        out[f"{tc.get('classname')}::{tc.get('name')}"] = kind
    return out


def skip_drift(remote: dict[str, str], mac: dict[str, str]) -> list[str]:
    """The tests both ran whose skipped-ness differs, when the skip COUNTS differ."""
    both = remote.keys() & mac.keys()
    r = {t for t in both if remote[t] == "skipped"}
    m = {t for t in both if mac[t] == "skipped"}
    return [] if len(r) == len(m) else sorted(r ^ m)


def summary(kinds: dict[str, str]) -> str:
    n = {k: list(kinds.values()).count(k) for k in ("passed", "skipped", "failed")}
    return f"{n['passed']} passed, {n['skipped']} skipped, {n['failed']} failed"


def run_local(tree: Path, tests: list[str], workers: str, junit: Path) -> int:
    argv = [sys.executable, "-m", "pytest", "-q", f"--junitxml={junit}", *tests]
    if workers != "0":
        argv[3:3] = ["-n", workers]
    # In the checkout, with git's repository bindings dropped: a hook's GIT_DIR names another repo.
    env = gitrun.repo_env()
    return subprocess.run(argv, cwd=tree, env=env, stdin=subprocess.DEVNULL, check=False).returncode


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="goh remote-shard", description=__doc__.split("\n\n")[0])
    ap.add_argument("tests", nargs="+", help="test files or node ids, relative to the tree")
    ap.add_argument("--tree", type=Path, default=Path.cwd(), help="the checkout (default: cwd)")
    ap.add_argument("--workers", type=int, default=10, help="xdist workers in the container")
    ap.add_argument(
        "--local-workers",
        default="auto",
        help="xdist workers for a local fallback: a count, auto, or 0 for none",
    )
    ap.add_argument("--junit", type=Path, help="write the shard's junit here")
    ap.add_argument(
        "--mac-junit", type=Path, help="the Mac's junit for the same tests: skip parity"
    )
    ap.add_argument(
        "--slot", default="0", help="the lane slot: one cargo target and pytest cache each"
    )
    ap.add_argument("--manifest", type=Path, default=ROOT / "gates" / "required_tools.tsv")
    a = ap.parse_args(argv)
    c, tree = conf(), a.tree.resolve()
    junit = a.junit or tree / ".goh-remote-shard.xml"
    times: dict[str, float] = {}
    runs: dict[str, float] = {}
    t0 = time.monotonic()
    try:
        tag = probe(c, tree, a.manifest, times)
        t1 = time.monotonic()
        sync(c, tree)
        t2 = time.monotonic()
        rc, text = run_remote(c, tag, a.tests, a.workers, a.slot, runs)
        t3 = time.monotonic()
        if not text.strip():
            raise Fallback(f"the remote run wrote no junit (pytest exit {rc})")
        remote = cases(text)
        if a.mac_junit:
            drift = skip_drift(remote, cases(a.mac_junit.read_text()))
            if drift:
                raise Fallback("refused: the remote skip count differs from the Mac's: "
                               + ", ".join(drift))  # fmt: skip
        if rc not in (0, 1):
            raise Fallback(f"remote pytest exited {rc}")
    except Fallback as why:
        warn(f"{why} -- running the shard locally")
        rc = run_local(tree, a.tests, a.local_workers, junit)
        info(f"local: {summary(cases(junit.read_text())) if junit.exists() else 'no junit'}")
        return 0 if rc == 0 else 1
    junit.write_text(text)
    failed = [t for t, k in remote.items() if k == "failed"]
    suite = ET.fromstring(text).find(".//testsuite")
    tests_s = float(suite.get("time", "0")) if suite is not None else 0.0
    parts = {
        "probe": t1 - t0 - sum(times.values()),
        **times,
        "sync": t2 - t1,
        **runs,
        "exec overhead": t3 - t2 - tests_s - sum(runs.values()),
        "tests": tests_s,
    }
    line = (
        f"remote {c['host']}: {summary(remote)} ("
        + ", ".join(f"{k} {v:.1f} s" for k, v in parts.items())
        + ")"
    )
    if failed:
        warn(line + ": " + ", ".join(failed))
        return 1
    ok(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
