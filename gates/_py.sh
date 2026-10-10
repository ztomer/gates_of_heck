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
# the project is measuring the wrong thing. But a venv is a snapshot of whichever python3 built it,
# and one that LAGS python3 judged ruff and pytest on the older interpreter with no word said
# (BACKLOG 1.6). So the choice is named with its version, and a venv older than python3 is refused
# unless the repo declares that pin: GOH_PY_RUNNER, or a COMMITTED .python-version naming the
# venv's minor. An untracked .python-version is one machine's state, not the repo's declaration.
goh_py_runner() {
    RUN="${GOH_PY_RUNNER:-}"
    if [ -z "$RUN" ] && [ -x .venv/bin/python ]; then
        RUN=".venv/bin/python -m"
        goh_py_venv_current
    elif [ -z "$RUN" ]; then
        RUN="python3 -m"
    fi
    read -r -a RUN_ARR <<<"$RUN"
    info "python: ${RUN% -m} ($(goh_py_minor "${RUN_ARR[0]}" || echo "version unreadable"))"
}

# goh_py_minor <python>: its major.minor, or nothing and 1 when it cannot say.
goh_py_minor() {
    "$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null </dev/null
}

# goh_py_venv_current: refuse a .venv whose minor is below python3's unless the repo pins it.
goh_py_venv_current() {
    local venv sys pin
    venv="$(goh_py_minor .venv/bin/python)" \
        || die "$GOH_NAME: .venv/bin/python cannot say its version -- rebuild the venv (uv venv) or set GOH_PY_RUNNER"
    sys="$(goh_py_minor python3)" || return 0   # no python3 to compare with: the venv is all there is
    if (( ${venv%%.*} > ${sys%%.*} || (${venv%%.*} == ${sys%%.*} && ${venv#*.} >= ${sys#*.}) )); then
        return 0
    fi
    if git ls-files --error-unmatch .python-version >/dev/null 2>&1; then
        pin="$(tr -d '[:space:]' <.python-version)"
        case "$pin" in "$venv" | "$venv".*) return 0 ;; esac
    fi
    die "$GOH_NAME: .venv is Python $venv and python3 is $sys -- the gate would judge on the older one unsaid. Rebuild it (uv venv --python $sys), or declare the pin: commit a .python-version reading $venv, or set GOH_PY_RUNNER"
}

# goh_py_require <module>: refuse unless the runner can run <module> (`<runner> <module> --version`).
goh_py_require() {
    "${RUN_ARR[@]}" "$1" --version >/dev/null 2>&1 \
        || die "$GOH_NAME: $1 is not available through '$RUN', and this gate does not pass without the step it runs -- python3 -m pip install $1 (into the interpreter '$RUN' names)"
}
