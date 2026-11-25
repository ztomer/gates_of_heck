# GOH_PY_RUNNER
Area: python
Kind: setting
Order: 120
Default: `uv run --frozen` when the repo TRACKS a `uv.lock` (refused by name if `uv` is not on `PATH`), else `.venv/bin/python -m` if executable, else the pinned interpreter (`GOH_PYTHON_VERSION`), else `python3 -m`

Toolchain prefix, whitespace-split (`uv run` works; a runner path containing a space cannot be
expressed — use `.venv/bin/python`). The chosen interpreter is named with its version, and a `.venv`
older than `python3` is refused unless this key is set or a committed `.python-version` names the
venv's minor (BACKLOG 1.6). The default is decided by TRACKED state so the commit gate and the push
gate (a clean export with no gitignored `.venv`) take the same road: a locked repo runs `uv run
--frozen` in both, with the pin passed as `--python` and read back; a push export whose source
checkout judges with a `.venv` and has neither a lock nor this key is refused with that fix named,
instead of being judged by another interpreter (tests/test_py_runner_tracked.py).
