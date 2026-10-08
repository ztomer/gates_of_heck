"""The structural pipeline's fixture cases and their verdicts (tests/test_goh_structural.py).

Split out at the 500-line cap, the cut the shape table took (tests/unreaped_spawn_table.py): this
file is data -- one fixture repo per case and its `(exit code, first failing step)` -- and the test
file is the driver. Adding a step is two cases and two verdicts here.
"""

GATESRC = b"GOH_MAX_LINES=10\n"
PYFMT_SRC = b"GOH_MAX_LINES=10\nGOH_PYTHON_FORMATTED=1\n"
# A `.gatesrc` that opts into the prose-claim gate. 500 rather than 10 because the
# claim document below is three lines and the cap has to be out of its way.
CLAIM_SRC = b"GOH_MAX_LINES=500\nGOH_CLAIM_DERIVATION=1\n"

RC_RULES = (
    b'[[rule]]\nname = "placement"\nwhy = "a relaunch can move windows"\nfiles = ["tools/*.py"]\n'
    b'calls = ["relaunch"]\nmust_call = ["window_placement.verify"]\n'
)

FULL_CASES: dict[str, dict[str, bytes]] = {
    "clean": {".gatesrc": GATESRC, "a.py": b"x = 1\n"},
    "emoji": {".gatesrc": GATESRC, "a.py": f"x = 1  # {chr(0x1F389)}\n".encode()},
    "marker": {".gatesrc": GATESRC, "a.py": b"x = 1\n<<<<<<< ours\n"},
    # The python-format step, red and green. Without a red case this step could
    # be absent from ONE tier and the parity test would still pass, because two
    # tiers that both skip a step also agree. That is exactly how the shell side
    # ran it in staged mode while the native side skipped it, and the only thing
    # that noticed was this suite.
    # The step is OPT-IN: until 2026-10-06 both cases declared only GATESRC, so the step was
    # skipped, both tiers agreed on (0, None), and the "red" case was green -- the miss this
    # comment describes, inside the fixture written to catch it. Expected verdicts (below)
    # instead of tier agreement are what exposed it.
    "pyfmt_green": {".gatesrc": PYFMT_SRC, "a.py": b'import os\n\nos.environ.get("X")\n'},
    "pyfmt_red": {".gatesrc": PYFMT_SRC, "a.py": b'import os\nos.environ.get("X")\n'},
    "over_cap": {".gatesrc": GATESRC, "a.py": b"x = 1\n" * 20},
    "no_gatesrc": {"a.py": b"x = 1\n"},
    "exclude_warn": {
        ".gatesrc": b"GOH_MAX_LINES=10\nGOH_LINE_EXCLUDE='a.py'\n",
        "a.py": b"x = 1\n",
    },
    "shell_fail": {".gatesrc": GATESRC, "bad.sh": b"if then\n"},
    # The opt-in home-paths step, both outcomes -- an opt-in step the table
    # never turns on is one the parity proof never sees.
    "home_path_red": {
        ".gatesrc": b"GOH_MAX_LINES=10\nGOH_NO_HOME_PATHS=1\n",
        "NOTES.md": b"run from ~/Projects/x\n",
    },
    "home_path_green": {
        ".gatesrc": b"GOH_MAX_LINES=10\nGOH_NO_HOME_PATHS=1\n",
        "NOTES.md": b"run from the repo root\n",
    },
    # The opt-in kill-by-name step, both outcomes. The command name is built from
    # parts because that gate reads this file too.
    #
    # CONCATENATED WITH `+`, NOT with adjacent literals, and that is the whole
    # point of this note. The original was two adjacent bytes literals
    # (`b'...["p' b'kill"...]'`), which the first `ruff format` run in this repo
    # MERGED into one -- putting the literal text `pkill` back into this file and
    # turning the gate red on a fixture that kills nothing. An evasion that
    # depends on the formatter leaving your source alone is not an evasion, and
    # the formatter is not going to be told to leave it alone.
    "kill_by_name_red": {
        ".gatesrc": b"GOH_MAX_LINES=10\nGOH_NO_KILL_BY_NAME=1\n",
        # The payload is written to a fixture repo and must itself be
        # ruff-formatted, or the python-format step -- which runs BEFORE this
        # one -- fails first and this case stops testing what it is for. The
        # blank line after the import is what `ruff format` wants, and the
        # command name is still built with `+` so this file never spells it.
        "run.py": b'import subprocess\n\nsubprocess.run(["p' + b'kill", "-f", "helper"])\n',
    },
    # early-exit-pipe runs in EVERY repo: ratchet closed (opt-in) an old racy pipe fails at full
    # scope and the fixed shape passes; ratchet open it is named, not failed (`_named`).
    "early_exit_pipe_red": {
        ".gatesrc": b"GOH_MAX_LINES=10\nGOH_NO_EARLY_EXIT_PIPE=1\n",
        "a.sh": b"#!/bin/bash\nset -euo pipefail\nls | grep -q x\n",
    },
    "early_exit_pipe_green": {
        ".gatesrc": b"GOH_MAX_LINES=10\nGOH_NO_EARLY_EXIT_PIPE=1\n",
        "a.sh": b'#!/bin/bash\nset -euo pipefail\nout="$(ls)"\ngrep -q x <<<"$out"\n',
    },
    "early_exit_pipe_named": {
        ".gatesrc": GATESRC,
        "a.sh": b"#!/bin/bash\nset -euo pipefail\nls | grep -q x\n",
    },
    # dead-after-exec, a hard gate in every repo: code after `exec CMD` fails; the parse-guard passes.
    "dead_exec_red": {".gatesrc": GATESRC, "a.sh": b"#!/bin/sh\nexec true\necho never\n"},
    "dead_exec_green": {".gatesrc": GATESRC, "a.sh": b"#!/bin/sh\n{\nexec true\nexit\n}\n"},
    # bare-hook-index, a hard gate in every repo: the carried index read outside _git_env.sh fails.
    "hook_index_red": {
        ".gatesrc": GATESRC,
        "a.sh": b'#!/bin/sh\nGIT_INDEX_FILE="${GOH_HOOK_INDEX_FILE:-x}" git diff --cached\n',
    },
    "hook_index_green": {".gatesrc": GATESRC, "a.sh": b"#!/bin/sh\nunset GOH_HOOK_INDEX_FILE\n"},
    # checkout-credentials, a hard gate in every repo: a default checkout fails; `false` passes.
    "checkout_token_red": {
        ".gatesrc": GATESRC,
        ".github/workflows/ci.yml": b"jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n",
    },
    "checkout_token_green": {
        ".gatesrc": GATESRC,
        ".github/workflows/ci.yml": (
            b"jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n"
            b"        with:\n          persist-credentials: false\n"
        ),
    },
    "kill_by_name_green": {
        ".gatesrc": b"GOH_MAX_LINES=10\nGOH_NO_KILL_BY_NAME=1\n",
        "run.py": b"import os\nos.killpg(os.getpgid(0), 15)\n",
    },
    # The ceiling steps, every branch -- an exempt-over-cap file with and
    # without its ceiling, growth past the ceiling, and a dangling baseline.
    "ceiling_green": {
        ".gatesrc": b"GOH_MAX_LINES=10\nGOH_LINE_EXCLUDE='a.py'\nGOH_LINE_BASELINE='base.txt'\n",
        "a.py": b"x = 1\n" * 15,
        "base.txt": b"15\ta.py\n",
    },
    "ceiling_no_ceiling": {
        ".gatesrc": b"GOH_MAX_LINES=10\nGOH_LINE_EXCLUDE='a.py'\nGOH_LINE_BASELINE='base.txt'\n",
        "a.py": b"x = 1\n" * 15,
        "base.txt": b"15\tother.py\n",
    },
    "ceiling_over": {
        ".gatesrc": b"GOH_MAX_LINES=10\nGOH_LINE_EXCLUDE='a.py'\nGOH_LINE_BASELINE='base.txt'\n",
        "a.py": b"x = 1\n" * 16,
        "base.txt": b"15\ta.py\n",
    },
    "ceiling_missing": {
        ".gatesrc": b"GOH_MAX_LINES=10\nGOH_LINE_EXCLUDE='a.py'\nGOH_LINE_BASELINE='missing.txt'\n",
        "a.py": b"x = 1\n" * 15,
    },
    # The two steps added with the 2026-10-01 audit, BOTH outcomes each.
    # structural.sh EXECs the native binary, so a step present in one pipeline
    # and not the other runs in exactly one of them -- and a case the parity
    # table never exercises is the only place that drift is visible.
    "md_link_red": {".gatesrc": GATESRC, "README.md": b"[x](gone.md)\n"},
    "md_link_green": {
        ".gatesrc": GATESRC,
        "README.md": b"[x](there.md)\n",
        "there.md": b"# here\n",
    },
    "lock_red": {
        ".gatesrc": GATESRC,
        "Cargo.toml": b'[package]\nname = "app"\nversion = "1.2.3"\n',
        "Cargo.lock": b'[[package]]\nname = "app"\nversion = "1.2.2"\n',
    },
    "lock_green": {
        ".gatesrc": GATESRC,
        "Cargo.toml": b'[package]\nname = "app"\nversion = "1.2.3"\n',
        "Cargo.lock": b'[[package]]\nname = "app"\nversion = "1.2.3"\n',
    },
    # The prose-claim step, BOTH outcomes. It is opt-in, and opt-in means the
    # fixture has to DECLARE it: a case the parity table never turns on is a step
    # both tiers skip, which is the one situation in which they agree perfectly.
    #
    # The `.gatesrc` here carries the key, and the document carries a claim -- and
    # they are the same fixture's two halves on purpose. That step REFUSES a tree
    # that declares the convention and has no marked claim in it, which is the
    # empty-scope rule, so a fixture with the key and no claim exercises the refusal
    # rather than the gate.
    "claim_red": {
        ".gatesrc": CLAIM_SRC,
        "doc.md": b"# t\n\nclaim: 9 lines in doc.md\n",
    },
    "claim_green": {
        ".gatesrc": CLAIM_SRC,
        "doc.md": b"# t\n\nclaim: 3 lines in doc.md\n",
    },
    # `goh requires-call`, opt-in by naming its rules file: a tool that relaunches must judge.
    "requires_call_red": {
        ".gatesrc": GATESRC + b"GOH_REQUIRES_CALL='rc.toml'\n",
        "rc.toml": RC_RULES,
        "tools/probe.py": b"def run():\n    relaunch()\n",
    },
    "requires_call_green": {
        ".gatesrc": GATESRC + b"GOH_REQUIRES_CALL='rc.toml'\n",
        "rc.toml": RC_RULES,
        "tools/probe.py": b"import window_placement as p\n\n\ndef run():\n    relaunch()\n    p.verify()\n",
    },
}


# Every case's verdict, `(exit code, first failing step)`. A red case that is not red here is a
# step its fixture never turned on.
EXPECTED: dict[str, tuple[int, str | None]] = {
    "ceiling_green": (0, None),
    "ceiling_missing": (1, "line-cap exemptions carry a ceiling"),
    "ceiling_no_ceiling": (1, "line-cap exemptions carry a ceiling"),
    "ceiling_over": (1, "cap-exempt files within their ceilings"),
    "claim_green": (0, None),
    "claim_red": (1, "prose claims are derived"),
    "requires_call_green": (0, None),
    "requires_call_red": (1, "a file that calls X calls Y"),
    "checkout_token_green": (0, None),
    "checkout_token_red": (1, "no checkout leaves its token in git config"),
    "clean": (0, None),
    "dead_exec_green": (0, None),
    "dead_exec_red": (1, "no code after exec"),
    "early_exit_pipe_green": (0, None),
    "early_exit_pipe_named": (0, None),
    "early_exit_pipe_red": (1, "no early-exit pipe under pipefail"),
    "emoji": (1, "no disallowed emoji"),
    "exclude_warn": (0, None),
    "hook_index_green": (0, None),
    "hook_index_red": (1, "no bare read of the hook's index"),
    "home_path_green": (0, None),
    "home_path_red": (1, "no hard-coded home paths"),
    "kill_by_name_green": (0, None),
    "kill_by_name_red": (1, "no process kill by name"),
    "lock_green": (0, None),
    "lock_red": (1, "Cargo.lock matches its manifests"),
    "marker": (1, "no conflict markers"),
    "md_link_green": (0, None),
    "md_link_red": (1, "markdown links resolve"),
    "no_gatesrc": (0, None),
    "over_cap": (1, "file length <= 10"),
    "pyfmt_green": (0, None),
    "pyfmt_red": (1, "python is ruff-formatted"),
    "shell_fail": (1, "shell lint"),
}
