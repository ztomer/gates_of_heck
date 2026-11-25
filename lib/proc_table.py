"""proc_table.py -- the machine's process table, read without privilege, for lib/load_sources.py.

One call, `snapshot()`, returns every process the kernel lists with what the load sampler needs and
nothing it must not keep: pid, parent, cumulative own CPU, the CPU of its REAPED children (which is
how a process born and dead between two samples is still counted), resident memory, the executable
(its path is held in memory to classify; the ledger keeps its basename), the working directory, and
for an interpreter the basename of the script it runs. An interpreter's argument vector is read to
find that script and then dropped: a command line can hold a secret, so none reaches the caller.

macOS: libproc (`proc_pid_rusage`, `proc_pidinfo`, `proc_pidpath`) for the caller's own processes,
and `ps -A`, which is setuid and reads every process's CPU time, for the ones libproc refuses (other
users': root's daemons, `_windowserver`, `_mds_stores`); those carry no reaped-children time and no
working directory. The machine's busy CPU is `host_statistics(HOST_CPU_LOAD_INFO)`'s ticks.
Linux: `/proc`. Pinned through lib/load_sources.py's tests (tests/test_load_sources.py).

The process table for `load_sources.py`, without privilege: libproc (`proc_pid_rusage` v2 whole,
`proc_pidinfo` cwd, `proc_pidpath`, `KERN_PROCARGS2` for an interpreter's script basename, then
dropped) for the caller's processes and `ps -A` for other users'; `host_statistics` ticks for the
machine's busy CPU; `/proc` on Linux.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from dataclasses import dataclass

INTERPRETERS = frozenset(
    {"bash", "sh", "zsh", "dash", "python", "python3", "Python", "node", "ruby", "perl", "uv"}
)


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    exe: str  # basename of the executable
    path: str  # the executable's full path: classification only, never written
    cpu_s: float  # own user+system CPU since birth
    child_s: float | None  # CPU of reaped children (None: unreadable)
    rss_kb: int
    cwd: str | None
    script: str | None = None  # an interpreter's script basename (`pytest` for `-m pytest`)
    start: float | None = None  # birth, any monotonic unit: identity across samples

    @property
    def key(self) -> tuple[int, object]:
        return (self.pid, self.start if self.start is not None else self.exe)


@dataclass(frozen=True)
class Snapshot:
    t: float  # wall clock, epoch seconds
    busy_s: float | None  # the machine's cumulative busy CPU seconds (all cores)
    load1: float | None
    cores: int
    procs: dict[int, Proc]


def script_of(argv: list[str]) -> str | None:
    """The script an interpreter's argv runs, as a basename; `-m mod` names mod; `-c` names none."""
    args = argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "-m" and i + 1 < len(args):
            return args[i + 1].split(".")[0]
        if a in ("-c", "-e", "--eval"):
            return None
        if a.startswith("-"):
            i += 1
            continue
        return os.path.basename(a) or None
    return None


def parse_cputime(text: str) -> float:
    """`ps -o time` is [[dd-]hh:]mm:ss[.cc]; seconds as a float."""
    days = 0
    if "-" in text:
        d, text = text.split("-", 1)
        days = int(d)
    secs = 0.0
    for part in text.split(":"):
        secs = secs * 60 + float(part.replace(",", "."))
    return days * 86400 + secs


def parse_ps(out: str) -> dict[int, Proc]:
    """`ps -A -o pid=,ppid=,time=,rss=,comm=` into Procs (no children's time, no cwd)."""
    procs: dict[int, Proc] = {}
    for line in out.splitlines():
        f = line.split(None, 4)
        if len(f) < 5 or not f[0].isdigit():
            continue
        try:
            pid, ppid, cpu, rss = int(f[0]), int(f[1]), parse_cputime(f[2]), int(f[3])
        except ValueError:
            continue
        path = f[4].strip()
        procs[pid] = Proc(pid, ppid, os.path.basename(path), path, cpu, None, rss, None)
    return procs


def _ps() -> str:
    return subprocess.run(
        ["ps", "-A", "-o", "pid=,ppid=,time=,rss=,comm="],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
        timeout=20,
    ).stdout


class _RusageInfoV2(ctypes.Structure):
    _fields_ = [("ri_uuid", ctypes.c_uint8 * 16)] + [
        (n, ctypes.c_uint64)
        for n in (
            "user",
            "system",
            "pkg_idle",
            "intr",
            "pageins",
            "wired",
            "resident",
            "footprint",
            "start",
            "exit",
            "child_user",
            "child_system",
            "child_pkg_idle",
            "child_intr",
            "child_pageins",
            "child_elapsed",
            "diskio_read",
            "diskio_written",
        )  # the whole rusage_info_v2: the kernel writes all of it
    ]


class _HostCpuLoad(ctypes.Structure):
    _fields_ = [("ticks", ctypes.c_uint32 * 4)]  # user, system, idle, nice


class _Darwin:
    PIDVNODEPATHINFO, VNODE_INFO, MAXPATH = 9, 152, 1024

    def __init__(self) -> None:
        self.libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        tb = (ctypes.c_uint32 * 2)()
        self.libc.mach_timebase_info(tb)
        self.tick = tb[0] / tb[1] / 1e9 if tb[1] else 1e-9
        self.libc.mach_host_self.restype = ctypes.c_uint32
        self.vbuf = ctypes.create_string_buffer(2 * (self.VNODE_INFO + self.MAXPATH))
        self.pbuf = ctypes.create_string_buffer(4096)
        self.argv_cache: dict[tuple[int, int], str | None] = {}

    def busy_s(self) -> float | None:
        h, n = _HostCpuLoad(), ctypes.c_uint32(4)
        if self.libc.host_statistics(
            self.libc.mach_host_self(), 3, ctypes.byref(h), ctypes.byref(n)
        ):
            return None
        hz = os.sysconf("SC_CLK_TCK") or 100
        return (h.ticks[0] + h.ticks[1] + h.ticks[3]) / hz

    def cwd(self, pid: int) -> str | None:
        size = len(self.vbuf)
        if self.libc.proc_pidinfo(pid, self.PIDVNODEPATHINFO, 0, self.vbuf, size) != size:
            return None
        raw = self.vbuf.raw[self.VNODE_INFO : self.VNODE_INFO + self.MAXPATH]
        return raw.split(b"\0", 1)[0].decode(errors="replace") or None

    def argv(self, pid: int) -> list[str]:
        mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2
        size = ctypes.c_size_t(0)
        if self.libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) or not size.value:
            return []
        buf = ctypes.create_string_buffer(size.value)
        if self.libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0):
            return []
        raw = buf.raw[: size.value]
        argc = int.from_bytes(raw[:4], sys.byteorder)
        rest = raw[4:].split(b"\0")
        rest = rest[1:]  # the exec path
        while rest and rest[0] == b"":
            rest = rest[1:]
        return [a.decode(errors="replace") for a in rest[:argc]]

    def snapshot(self, ps=_ps) -> dict[int, Proc]:
        procs = parse_ps(ps())
        out: dict[int, Proc] = {}
        for pid, p in procs.items():
            ru = _RusageInfoV2()
            if pid <= 0 or self.libc.proc_pid_rusage(pid, 2, ctypes.byref(ru)):
                out[pid] = p  # another user's: ps's figures stand
                continue
            path = p.path
            if self.libc.proc_pidpath(pid, self.pbuf, len(self.pbuf)) > 0:
                path = self.pbuf.value.decode(errors="replace")
            exe = os.path.basename(path)
            script = None
            if exe in INTERPRETERS or exe.startswith("python"):
                ck = (pid, ru.start)
                if ck not in self.argv_cache:
                    self.argv_cache[ck] = script_of(self.argv(pid))
                script = self.argv_cache[ck]
            out[pid] = Proc(
                pid,
                p.ppid,
                exe,
                path,
                (ru.user + ru.system) * self.tick,
                (ru.child_user + ru.child_system) * self.tick,
                ru.resident // 1024,
                self.cwd(pid),
                script,
                float(ru.start),
            )
        live = {(pid, p.start) for pid, p in out.items()}
        self.argv_cache = {k: v for k, v in self.argv_cache.items() if k in live}
        return out


class _Linux:
    def __init__(self) -> None:
        self.hz = os.sysconf("SC_CLK_TCK") or 100
        self.page_kb = os.sysconf("SC_PAGE_SIZE") // 1024

    def busy_s(self) -> float | None:
        try:
            with open("/proc/stat", encoding="ascii") as fh:
                f = fh.readline().split()
        except OSError:
            return None
        v = [int(x) for x in f[1:9]]  # user nice system idle iowait irq softirq steal
        return (v[0] + v[1] + v[2] + v[5] + v[6] + v[7]) / self.hz

    def snapshot(self) -> dict[int, Proc]:
        out: dict[int, Proc] = {}
        for name in os.listdir("/proc"):
            if name.isdigit():
                p = self._one(int(name))
                if p is not None:
                    out[p.pid] = p
        return out

    def _one(self, pid: int) -> Proc | None:
        base = f"/proc/{pid}"
        try:
            with open(f"{base}/stat", encoding="utf-8", errors="replace") as fh:
                stat = fh.read()
        except OSError:
            return None
        r = stat.rfind(")")
        comm, rest = stat[stat.find("(") + 1 : r], stat[r + 2 :].split()
        try:
            path = os.readlink(f"{base}/exe")
        except OSError:
            path = comm
        try:
            cwd: str | None = os.readlink(f"{base}/cwd")
        except OSError:
            cwd = None
        exe = os.path.basename(path)
        script = None
        if exe in INTERPRETERS or exe.startswith("python"):
            try:
                with open(f"{base}/cmdline", "rb") as fh:
                    script = script_of([a.decode(errors="replace") for a in fh.read().split(b"\0")])
            except OSError:
                pass
        u, s, cu, cs = (int(x) for x in rest[11:15])
        return Proc(
            pid,
            int(rest[1]),
            exe,
            path,
            (u + s) / self.hz,
            (cu + cs) / self.hz,
            int(rest[21]) * self.page_kb,
            cwd,
            script,
            float(rest[19]),
        )


def reader():
    """The reader for this OS (macOS, Linux); anything else is refused by the caller."""
    if sys.platform == "darwin":
        return _Darwin()
    if sys.platform.startswith("linux"):
        return _Linux()
    raise SystemExit(f"proc_table: {sys.platform} is not a supported OS (macOS, Linux)")
