"""Process-group CPU time (including reaped descendants) and runnable-thread probe.

Process-group CPU time (including reaped descendants) via
`proc_listpids`/`proc_pid_rusage`/`proc_pidinfo` (macOS) or `/proc` (Linux), runnable-thread probe,
and `--cpu-timeout`/`--stall` wait loop + CLI helpers for `bounded_run.py` and `orphan_canary.py`.
Pinned by `test_bounded_run.py`, `test_step_wrapper_cli.py`.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time

_PROC_PGRP_ONLY = 2
_PROC_PIDTASKINFO = 4
_RUSAGE_INFO_V2 = 2
_POLL_S = 0.05
_TIMEOUT_EXIT = 124

if sys.platform == "darwin":

    class _MachTimebaseInfo(ctypes.Structure):
        _fields_ = [("numer", ctypes.c_uint32), ("denom", ctypes.c_uint32)]

    class _ProcTaskInfo(ctypes.Structure):
        _fields_ = [
            ("pti_virtual_size", ctypes.c_uint64),
            ("pti_resident_size", ctypes.c_uint64),
            ("pti_total_user", ctypes.c_uint64),
            ("pti_total_system", ctypes.c_uint64),
            ("pti_threads_user", ctypes.c_uint64),
            ("pti_threads_system", ctypes.c_uint64),
            ("pti_policy", ctypes.c_int32),
            ("pti_faults", ctypes.c_int32),
            ("pti_pageins", ctypes.c_int32),
            ("pti_cow_faults", ctypes.c_int32),
            ("pti_messages_sent", ctypes.c_int32),
            ("pti_messages_received", ctypes.c_int32),
            ("pti_syscalls_mach", ctypes.c_int32),
            ("pti_syscalls_unix", ctypes.c_int32),
            ("pti_csw", ctypes.c_int32),
            ("pti_threadnum", ctypes.c_int32),
            ("pti_numrunning", ctypes.c_int32),
            ("pti_priority", ctypes.c_int32),
        ]

    class _RusageInfoV2(ctypes.Structure):
        _fields_ = [
            ("ri_uuid", ctypes.c_uint8 * 16),
            ("ri_user_time", ctypes.c_uint64),
            ("ri_system_time", ctypes.c_uint64),
            ("ri_pkg_idle_wkups", ctypes.c_uint64),
            ("ri_interrupt_wkups", ctypes.c_uint64),
            ("ri_pageins", ctypes.c_uint64),
            ("ri_wired_size", ctypes.c_uint64),
            ("ri_resident_size", ctypes.c_uint64),
            ("ri_phys_footprint", ctypes.c_uint64),
            ("ri_proc_start_abstime", ctypes.c_uint64),
            ("ri_proc_exit_abstime", ctypes.c_uint64),
            ("ri_child_user_time", ctypes.c_uint64),
            ("ri_child_system_time", ctypes.c_uint64),
            ("ri_child_pkg_idle_wkups", ctypes.c_uint64),
            ("ri_child_interrupt_wkups", ctypes.c_uint64),
            ("ri_child_pageins", ctypes.c_uint64),
            ("ri_child_elapsed_abstime", ctypes.c_uint64),
            ("ri_diskio_bytesread", ctypes.c_uint64),
            ("ri_diskio_byteswritten", ctypes.c_uint64),
        ]

    _LIBC = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    _TIMEBASE = _MachTimebaseInfo()
    _LIBC.mach_timebase_info(ctypes.byref(_TIMEBASE))
    _TICK_SECS = (_TIMEBASE.numer / _TIMEBASE.denom / 1_000_000_000.0) if _TIMEBASE.denom else 0.0


def _darwin_sample(pgid: int) -> tuple[float, int] | None:
    if pgid <= 0 or _TICK_SECS <= 0.0:
        return None
    cap = 512
    while True:
        pids = (ctypes.c_int32 * cap)()
        nbytes = _LIBC.proc_listpids(_PROC_PGRP_ONLY, pgid, pids, ctypes.sizeof(pids))
        if nbytes < 0:
            return None
        count = nbytes // ctypes.sizeof(ctypes.c_int32)
        if count < cap or cap >= 8192:
            break
        cap *= 2
    ticks = 0
    runnable = 0
    ti_size = ctypes.sizeof(_ProcTaskInfo)
    for pid in pids[: min(count, cap)]:
        if pid <= 0:
            continue
        ru = _RusageInfoV2()
        if _LIBC.proc_pid_rusage(pid, _RUSAGE_INFO_V2, ctypes.byref(ru)) == 0:
            ticks += (
                ru.ri_user_time
                + ru.ri_system_time
                + ru.ri_child_user_time
                + ru.ri_child_system_time
            )
        ti = _ProcTaskInfo()
        if _LIBC.proc_pidinfo(pid, _PROC_PIDTASKINFO, 0, ctypes.byref(ti), ti_size) == ti_size:
            runnable += max(0, ti.pti_numrunning)
    return ticks * _TICK_SECS, runnable


def _linux_sample(pgid: int) -> tuple[float, int] | None:
    try:
        hz = os.sysconf("SC_CLK_TCK")
    except (ValueError, OSError):
        return None
    if hz <= 0:
        return None
    jiffies = 0
    runnable = 0
    try:
        entries = os.listdir("/proc")
    except OSError:
        return None
    for name in entries:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", encoding="utf-8", errors="replace") as fh:
                stat = fh.read()
        except OSError:
            continue
        rparen = stat.rfind(")")
        if rparen < 0:
            continue
        rest = stat[rparen + 1 :].split()
        if len(rest) <= 14:
            continue
        try:
            if int(rest[2]) != pgid:
                continue
            jiffies += int(rest[11]) + int(rest[12]) + int(rest[13]) + int(rest[14])
        except ValueError:
            continue
        if rest[0] == "R":
            runnable += 1
    return jiffies / hz, runnable


def pgid_cpu_and_runnable(pgid: int) -> tuple[float, int] | None:
    """Return `(cpu_seconds, runnable_threads)` across `pgid` (including reaped descendants)."""
    if sys.platform == "darwin":
        return _darwin_sample(pgid)
    if sys.platform.startswith("linux"):
        return _linux_sample(pgid)
    return None


def add_bound_args(ap) -> None:
    """Register optional `--cpu-timeout` and `--stall` flags on an ArgumentParser."""
    ap.add_argument("--cpu-timeout", type=int, default=None, help="CPU-seconds ceiling")
    ap.add_argument("--stall", type=int, default=None, help="stalled wall-seconds ceiling")


def validate_bound_args(args, who: str) -> int | None:
    """Return exit code 2 if `--cpu-timeout` or `--stall` is non-positive, else None."""
    for attr, flag in (("cpu_timeout", "--cpu-timeout"), ("stall", "--stall")):
        val = getattr(args, attr, None)
        if val is not None and val <= 0:
            print(f"{who}: {flag} must be positive (got {val})", file=sys.stderr)
            return 2
    return None


def fast_args(
    argv: list[str], default_timeout: int, default_grace: int
) -> tuple[int, int, str, int | None, int | None, list[str]] | None:
    """Fast-path CLI parser for gate runs; returns None to fall back to argparse."""
    seen: dict[str, str] = {}
    i = 0
    flags = ("--timeout", "--grace", "--label", "--cpu-timeout", "--stall")
    while i < len(argv) and argv[i] != "--":
        flag = argv[i]
        if flag not in flags or flag in seen or i + 1 >= len(argv):
            return None
        seen[flag] = argv[i + 1]
        i += 2
    rest = argv[i + 1 :]
    if i >= len(argv) or not rest:
        return None
    try:
        timeout = int(seen.get("--timeout", str(default_timeout)))
        grace = int(seen.get("--grace", str(default_grace)))
        cpu_raw = seen.get("--cpu-timeout")
        stall_raw = seen.get("--stall")
        cpu_timeout = int(cpu_raw) if cpu_raw is not None else None
        stall = int(stall_raw) if stall_raw is not None else None
    except ValueError:
        return None
    nums = [seen[f] for f in ("--timeout", "--grace", "--cpu-timeout", "--stall") if f in seen]
    if (
        timeout <= 0
        or (cpu_timeout is not None and cpu_timeout <= 0)
        or (stall is not None and stall <= 0)
        or not all(v.isdigit() for v in nums)
    ):
        return None
    return timeout, grace, seen.get("--label", ""), cpu_timeout, stall, rest


def parse_step_cli(
    argv: list[str] | None,
    default_timeout: int,
    default_grace: int,
    doc: str | None,
) -> tuple[tuple[int, int, str, int | None, int | None, list[str]] | None, int]:
    """Parse bounded_run CLI flags via argparse; return `(parsed_tuple, exit_code)`."""
    import argparse

    ap = argparse.ArgumentParser(
        description=doc, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--timeout", type=int, default=default_timeout, help="ceiling in seconds")
    ap.add_argument("--grace", type=int, default=default_grace, help="TERM->KILL grace, seconds")
    ap.add_argument("--label", default="", help="what to call the step in the message")
    add_bound_args(ap)
    args, rest = ap.parse_known_args(argv)
    if rest and rest[0] == "--":
        rest = rest[1:]
    if not rest:
        ap.error("no command: bounded_run.py -- CMD [ARG...]")
    if args.timeout <= 0:
        print(f"bounded_run: --timeout must be positive (got {args.timeout})", file=sys.stderr)
        return None, 2
    if (rc := validate_bound_args(args, "bounded_run")) is not None:
        return None, rc
    return (args.timeout, args.grace, args.label, args.cpu_timeout, args.stall, rest), 0


def wait_for_exit(
    proc: subprocess.Popen[bytes] | subprocess.Popen[str],
    pgid: int,
    timeout: int,
    label: str,
    *,
    cpu_timeout: int | None,
    stall: int | None,
) -> str | None:
    """Wait until `proc` exits or hits `cpu_timeout`/`stall`/wall `timeout`; return reason."""
    start = time.monotonic()
    last_progress = start
    peak_cpu = 0.0
    while True:
        try:
            proc.wait(timeout=_POLL_S)
            return None
        except subprocess.TimeoutExpired:
            now = time.monotonic()
            sample = pgid_cpu_and_runnable(pgid)
            if sample is not None:
                cpu_s, runnable = sample
                if cpu_s > peak_cpu or runnable > 0:
                    peak_cpu = max(peak_cpu, cpu_s)
                    last_progress = now
            else:
                last_progress = now
            if cpu_timeout is not None and peak_cpu >= cpu_timeout:
                return f"TIMED OUT after {cpu_timeout}s: {label} (CPU budget)"
            if stall is not None and (now - last_progress) >= stall:
                return f"TIMED OUT after {stall}s: {label} (stalled)"
            if (now - start) >= timeout:
                return f"TIMED OUT after {timeout}s: {label}"
