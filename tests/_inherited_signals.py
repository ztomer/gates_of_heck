"""INT and QUIT, inherited ignored, put back before a test starts a child (GOH.RUSTOVERLAP).

A non-interactive bash starts `cmd &` with both ignored, and the land's rust gate starts its
external suite so (gates/_rust_coverage.sh); a shell born with a signal ignored can neither trap
nor reset it, so every shell a test started there was deaf to INT. A Python handler is reset to the
default across exec, so INT gets Python's own (KeyboardInterrupt) and QUIT the default, and only
where the start left one ignored: tests/test_inherited_signals.py.
"""

import signal


def restore() -> None:
    if signal.getsignal(signal.SIGINT) is signal.SIG_IGN:
        signal.signal(signal.SIGINT, signal.default_int_handler)
    if signal.getsignal(signal.SIGQUIT) is signal.SIG_IGN:
        signal.signal(signal.SIGQUIT, signal.SIG_DFL)
