"""tui/lib.py — Kare TUI output primitives (vendored from ~/projects/scripts/_lib.py).

Import from a repo script (add the repo root or tui/ to sys.path first):
    from tui.lib import info, step, ok, err, warn, die, hr, section, report

Self-contained: reads tui/stylerc (repo source of truth), no machine dependency.
Icons: → · ✓ ✗ ⚠   Colors: restrained, NO_COLOR + non-tty aware (degrades to plain text).
"""

import os
import sys

_STYLERC_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stylerc")

_DEFAULTS = {"ICON_START": "→", "ICON_STEP": "·", "ICON_OK": "✓", "ICON_ERR": "✗", "ICON_WARN": "⚠"}


def _parse_stylerc(path: str) -> dict:
    """Parse key="value" lines from the stylerc, converting \\033 -> ESC."""
    cfg = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                cfg[key.strip()] = val.strip().strip("\"'").replace(r"\033", "\033")
    except OSError:
        pass
    return cfg


_CFG = _parse_stylerc(_STYLERC_PATH)

ICON_START = _CFG.get("ICON_START", _DEFAULTS["ICON_START"])
ICON_STEP = _CFG.get("ICON_STEP", _DEFAULTS["ICON_STEP"])
ICON_OK = _CFG.get("ICON_OK", _DEFAULTS["ICON_OK"])
ICON_ERR = _CFG.get("ICON_ERR", _DEFAULTS["ICON_ERR"])
ICON_WARN = _CFG.get("ICON_WARN", _DEFAULTS["ICON_WARN"])

# no-color.org spec: ANY non-empty NO_COLOR value disables color (matches
# tui/lib.sh; NO_COLOR=0 is still a set variable).
if sys.stdout.isatty() and not os.environ.get("NO_COLOR"):
    _C_RESET = _CFG.get("C_RESET", "\033[0m")
    _C_DIM = _CFG.get("C_DIM", "\033[2m")
    _C_BOLD = _CFG.get("C_BOLD", "\033[1m")
    _C_GREEN = _CFG.get("C_GREEN", "\033[0;32m")
    _C_RED = _CFG.get("C_RED", "\033[0;31m")
    _C_YELLOW = _CFG.get("C_YELLOW", "\033[0;33m")
    _C_GRAY = _CFG.get("C_GRAY", "\033[0;90m")
else:
    _C_RESET = _C_DIM = _C_BOLD = _C_GREEN = _C_RED = _C_YELLOW = _C_GRAY = ""


def info(message: str) -> None:
    print(f"{_C_GRAY}{ICON_START}{_C_RESET} {_C_DIM}{message}{_C_RESET}", flush=True)


def step(message: str) -> None:
    """A sub-item under an info line.

    Added 2026-10-03 to close a drift between the two halves of this library:
    `tui/lib.sh` has always had `step` (ICON_STEP, dim, no colour of its own) and
    `tui/lib.py` did not, so a Python tool had no way to render the per-item
    lines it wanted and a shell tool did. ICON_STEP was already parsed here and
    unused — the config key was read and thrown away.
    """
    print(f"{_C_DIM}{ICON_STEP} {message}{_C_RESET}", flush=True)


def _write_once(text: str) -> None:
    """Write `text` to stdout in ONE write(2), then flush.

    The text layer would split a multi-line report at every newline when stdout is a
    terminal (line buffering), so the bytes go to the byte layer: one write, one flush.
    The text layer is flushed FIRST, so anything it still holds is not reordered after
    the report -- a reader that saw the report would see the earlier lines first.
    """
    stream = sys.stdout
    buffer = getattr(stream, "buffer", None)
    if buffer is None:  # a text-only stream (io.StringIO): one write is all it takes
        stream.write(text)
        stream.flush()
        return
    stream.flush()
    buffer.write(text.encode("utf-8"))
    buffer.flush()


def report(header: str, *details: str) -> None:
    """A header and its details, in ONE write, so a reader cannot see half of it.

    E59 (2026-10-21). The gate-slot wait report was a header write and then a write per
    holder, so a reader that waited for the header could read the file BETWEEN the writes
    and treat a half-written report as final: the window was 56 us, and a reader polling
    every millisecond landed in it (1 of 20 trials against the real writer, 20 of 20
    against the instrumented one). The fix is not a rule for the READER -- the soundness
    of a wait depends on the writer's write ORDER, which a reader cannot know -- but a
    report that is written ONCE: the intermediate state does not exist, so there is
    nothing to read.

    The details are rendered as `step` lines (the house's sub-item shape) and the header
    as an `info` line, so the report looks exactly like the two writes it replaces.

    The write is atomic while the report fits in a pipe's buffer (PIPE_BUF is 512 on macOS
    and 4096 on Linux): keep reports short, or a reader can still see half of one.

    The shell half is `tui/lib.sh`'s `report`, and the two are pinned to render the same
    bytes by tests/test_tui_report.py.
    """
    lines = [f"{_C_GRAY}{ICON_START}{_C_RESET} {_C_DIM}{header}{_C_RESET}"]
    lines += [f"{_C_DIM}{ICON_STEP} {d}{_C_RESET}" for d in details]
    text = "\n".join(lines) + "\n"
    _write_once(text)


def ok(message: str) -> None:
    print(f"{_C_GREEN}{ICON_OK}{_C_RESET} {message}", flush=True)


def err(message: str) -> None:
    print(f"{_C_RED}{ICON_ERR}{_C_RESET} {message}", file=sys.stderr, flush=True)


def warn(message: str) -> None:
    # STDERR, matching tui/lib.sh's `_tui_warn`. This used to print to stdout,
    # so the two halves of the same house library disagreed: a shell tool
    # piping stdout got warnings on stderr while a Python tool piping stdout got
    # them mixed into its data. `err` already went to stderr; a warning is a
    # diagnostic for the same reason an error is.
    print(f"{_C_YELLOW}{ICON_WARN}{_C_RESET} {message}", file=sys.stderr, flush=True)


def die(message: str, code: int = 1) -> None:
    err(message)
    sys.exit(code)


def hr(width: int = 72) -> None:
    print(f"{_C_GRAY}{'─' * width}{_C_RESET}", flush=True)


def section(title: str) -> None:
    print()
    hr()
    print(f"{_C_BOLD}  {title}{_C_RESET}", flush=True)
    hr()
