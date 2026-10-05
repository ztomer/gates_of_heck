"""Run a function over items CONCURRENTLY, replaying each call's printed output in item order.

For a sweep whose items are independent subprocess-bound jobs that REPORT as they go (`judge` in
check_estate_corpus.py prints through tui.lib). Threads, because the work is waiting on children;
a per-thread stream, because redirect_stdout is process-global and would interleave the reports.
Measured 2026-10-05: the estate sweep went 9.4 s -> 5.2 s, bounded by its longest corpus.
"""

from __future__ import annotations

import io
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor


class _PerThread:
    """A stream that writes to the CURRENT thread's buffer when it has one, else through."""

    def __init__(self, real):
        self.real, self.local = real, threading.local()

    def write(self, text):
        buffer = getattr(self.local, "buffer", None)
        return (buffer if buffer is not None else self.real).write(text)

    def flush(self):
        self.real.flush()


def in_order(fn, items):
    """[fn(item) for item in items], run concurrently, with each call's stdout+stderr replayed to
    stdout in item order once all are done."""
    items = list(items)
    if not items:
        return []
    out, errs = _PerThread(sys.stdout), _PerThread(sys.stderr)
    saved = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, errs

    def one(item):
        buffer = io.StringIO()
        out.local.buffer = errs.local.buffer = buffer
        try:
            return fn(item), buffer.getvalue()
        finally:
            out.local.buffer = errs.local.buffer = None

    try:
        with ThreadPoolExecutor(max_workers=min(len(items), os.cpu_count() or 4)) as pool:
            results = list(pool.map(one, items))
    finally:
        sys.stdout, sys.stderr = saved
    for _value, text in results:
        sys.stdout.write(text)
    return [value for value, _ in results]
