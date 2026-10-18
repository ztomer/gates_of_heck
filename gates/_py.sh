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
        goh_py_interpreter >/dev/null   # and a pinned repo's venv is the pin's version
    elif [ -z "$RUN" ]; then
        RUN="$(goh_py_interpreter) -m"
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

# THE PIN (BACKLOG 6.2). Homebrew moved `python3` to 3.15 on 2026-10-10 and every gate that ran a
# bare `python3` moved with it: necrohand lost spacy, scripts' coverage hook moved, ZeroThunder's
# hook lost `tui`. A repo pins a VERSION -- GOH_PYTHON_VERSION, else a COMMITTED .python-version --
# and the interpreter is found for that version and its version read back; a path to a Homebrew
# keg would pin nothing, because the keg moves. No pin is python3, as before, said by name.

# goh_py_pin: the repo's pinned version and where it came from ("3.14<TAB>.python-version"), or
# nothing when the repo pins none.
goh_py_pin() {
    if [ -n "${GOH_PYTHON_VERSION:-}" ]; then
        printf '%s\tGOH_PYTHON_VERSION\n' "$GOH_PYTHON_VERSION"
    elif git ls-files --error-unmatch .python-version >/dev/null 2>&1; then
        printf '%s\t.python-version\n' "$(tr -d '[:space:]' <.python-version)"
    fi
}

# goh_py_full <python>: its major.minor.micro, or nothing and 1 when it cannot say.
goh_py_full() {
    "$1" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null </dev/null
}

# goh_py_interpreter: the interpreter for this repo, on stdout -- a .venv's when there is one, else
# the pinned version's, else python3 -- refusing a venv or an interpreter that is not the pin.
goh_py_interpreter() {
    local pin src py full
    # Captured whole, then read: a producer still writing after `read` took its line wrote into a
    # closed pipe and printed `write error: Broken pipe` under a SIGPIPE-ignoring ancestor.
    IFS=$'\t' read -r pin src <<<"$(goh_py_pin)"
    if [ -x .venv/bin/python ]; then
        py=.venv/bin/python
    elif [ -n "$pin" ]; then
        py="$(uv python find --no-project "$pin" 2>/dev/null || command -v "python$pin" || true)"
        [ -n "$py" ] || die "$GOH_NAME: $src pins Python $pin and no interpreter of that version is installed -- uv python install $pin"
    else
        py="$(command -v python3)" || die "$GOH_NAME: no python3 on PATH and no pin to find one by (GOH_PYTHON_VERSION or a committed .python-version)"
    fi
    if [ -n "$pin" ]; then
        full="$(goh_py_full "$py")" || die "$GOH_NAME: $py cannot say its version"
        case "$full" in
            "$pin" | "$pin".*) ;;
            *) die "$GOH_NAME: $src pins Python $pin and $py is $full -- rebuild the venv (uv venv --python $pin) or move the pin" ;;
        esac
    fi
    printf '%s\n' "$py"
}
