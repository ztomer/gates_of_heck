#!/usr/bin/env bash
# gates/_py.sh -- sourced by py_gate.sh and py_staged.sh, never run: the ONE resolution of the
# interpreter the Python gates drive their tools through, and the refusal when it cannot.
#
# The tools run as modules of that interpreter (`python3 -m ruff`), so they live in ITS
# site-packages. An interpreter upgrade leaves the new one without them, and the gate then ran
# `ruff check` and failed it with "No module named ruff" -- a finding about the code read off a
# missing tool, with no install line (2026-10-10, Homebrew python3 3.14 -> 3.15). Each tool is
# probed before any step runs, and a missing one is a named refusal (gates/required_tools.tsv).

# goh_py_runner: set RUN (a string, for messages) and RUN_ARR (argv) from GOH_PY_RUNNER, else a
# project .venv, else python3. Prefer the venv over PATH: a gate on a different interpreter than
# the project is measuring the wrong thing.
goh_py_runner() {
    RUN="${GOH_PY_RUNNER:-}"
    if [ -z "$RUN" ] && [ -x .venv/bin/python ]; then
        RUN=".venv/bin/python -m"
    elif [ -z "$RUN" ]; then
        RUN="python3 -m"
    fi
    read -r -a RUN_ARR <<<"$RUN"
}

# goh_py_require <module>: refuse unless the runner can run <module> (`<runner> <module> --version`).
goh_py_require() {
    "${RUN_ARR[@]}" "$1" --version >/dev/null 2>&1 \
        || die "$GOH_NAME: $1 is not available through '$RUN', and this gate does not pass without the step it runs -- python3 -m pip install $1 (into the interpreter '$RUN' names)"
}
