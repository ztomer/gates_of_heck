#!/usr/bin/env python3
"""check_ci_local_parity.py -- every repo script CI runs, the local pre-push gate runs too.

    check_ci_local_parity.py              # judge the repo at the current directory
    check_ci_local_parity.py --list       # the scripts CI runs, one per line, judging nothing
    check_ci_local_parity.py --probe      # prove THIS gate can go red

WHY. CadGoose's G0.13 push passed all 12 local steps and went red in CI's Coverage Gate: the gate
was in CI and not in GOH_CI_STEPS, and the one runner that ran it did so only under a flag the
pre-push hook never passed. A local gate weaker than CI is a gate that lets you push red
(engineering rule #17), and "remember to add the CI step to the local list" is exactly the
disciplinary fix rule #3 rejects.

WHAT. It reads every `run:` command in `.github/workflows/*.y*ml` and in composite actions
(`.github/actions/**/action.y*ml`), and every step of `.gatesrc`'s GOH_CI_STEPS (read by sourcing
`.gatesrc` as the runner does, then split on ':' as `local_ci.sh` splits it), and collects the
repo scripts each one invokes: `scripts/...` or `tools/...` ending `.sh` or `.py`, matched after
any `../`. A script CI runs that no local step names is red.

Keys in `.gatesrc` (documented in docs/config.md):

    GOH_CI_PARITY_EXEMPT     one `path: reason` per line. A script CI runs that genuinely cannot
                             run before a push (packaging, a deploy). A RATCHET, red both ways: an
                             entry CI no longer runs, or that the local gate now runs, is stale.
    GOH_CI_PARITY_MIN_SCRIPTS  the scan's floor (default 1). A scan that finds fewer CI scripts
                             than this has lost its subject, and a scan of nothing reads as clean.
    GOH_CI_PARITY_DIRS       the directories scripts live in (default `scripts tools`).

The match is by NAME in the step string. A local step that runs a CI script through a wrapper
(`./tools/all.sh` calling `scripts/x.sh`) does not name it: exempt it with that reason, or list it.

Exit 0 every CI script runs locally, 1 a script does not (or an exemption is stale or the scan is
under its floor), 2 a precondition (no workflows, no GOH_CI_STEPS, an exemption with no reason).
"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    try:
        __import__("_from_head").reexec(__file__)
    except ModuleNotFoundError:  # a copy outside any checkout: nothing to re-run from
        pass
    del _sys.path[0]

import os
import pathlib
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import foreign_repo_env, repo_root, tree_files

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import err, ok

NAME = "ci_local_parity"
DEFAULT_DIRS = "scripts tools"
RUN_INLINE = re.compile(r"^(\s*)-?\s*run:\s*(\S.*)$")
RUN_BLOCK = re.compile(r"^(\s*)-?\s*run:\s*[|>][-+]?\s*$")
KEYS = ("GOH_CI_STEPS", "GOH_CI_PARITY_EXEMPT", "GOH_CI_PARITY_MIN_SCRIPTS", "GOH_CI_PARITY_DIRS")


class ConfigError(Exception):
    """A precondition the check cannot judge without: exit 2, never a verdict."""


def ci_commands(text: str) -> list[str]:
    """The text of every `run:` in one workflow, inline or block scalar."""
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        block = RUN_BLOCK.match(line)
        inline = RUN_INLINE.match(line)
        if block:
            indent = len(block.group(1))
            body = []
            i += 1
            while i < len(lines) and (
                not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > indent
            ):
                body.append(lines[i])
                i += 1
            out.append("\n".join(body))
            continue
        if inline:
            out.append(inline.group(2))
        i += 1
    return out


def script_pattern(dirs: str) -> re.Pattern[str]:
    names = "|".join(re.escape(d.strip("/")) for d in dirs.split())
    return re.compile(rf"(?:\.\./)*((?:{names})/[\w./-]+\.(?:sh|py))\b")


def scripts_in(commands: list[str], pattern: re.Pattern[str]) -> set[str]:
    found: set[str] = set()
    for cmd in commands:
        found.update(m.group(1) for m in pattern.finditer(cmd))
    return found


def read_config(root: pathlib.Path) -> dict[str, str]:
    """The parity keys as `.gatesrc` declares them: sourced by bash, as every gate reads it, with
    the environment's own copies dropped (the environment is not configuration)."""
    config = dict.fromkeys(KEYS, "")
    gatesrc = root / ".gatesrc"
    if not gatesrc.is_file():
        return config
    program = ". ./.gatesrc >/dev/null 2>&1; " + "; ".join(
        f'printf "%s\\0" "${{{k}:-}}"' for k in KEYS
    )
    env = {k: v for k, v in foreign_repo_env().items() if not k.startswith("GOH_")}
    run = subprocess.run(
        ["bash", "-c", program],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    config.update(zip(KEYS, run.stdout.split("\0"), strict=False))
    return config


def parse_exempt(value: str) -> dict[str, str]:
    exempt: dict[str, str] = {}
    for raw in value.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        path, _, reason = line.partition(":")
        if not path.strip() or not reason.strip():
            raise ConfigError(
                f"GOH_CI_PARITY_EXEMPT entry {line!r} has no reason: write `path: reason`, the "
                "reason a CI-only script cannot run before a push"
            )
        exempt[path.strip()] = reason.strip()
    return exempt


CI_FILE = re.compile(r"^\.github/(?:workflows/[^/]+|actions/(?:.+/)?action)\.ya?ml$")


def workflow_files(root: pathlib.Path) -> list[str]:
    """Root-relative workflows and composite actions: the tree git names, never a disk walk."""
    return sorted(f for f in tree_files(str(root)) if CI_FILE.match(f))


def ci_scripts(root: pathlib.Path, pattern: re.Pattern[str]) -> set[str]:
    files = workflow_files(root)
    if not any(f.startswith(".github/workflows/") for f in files):
        raise ConfigError("no .github/workflows/*.yml: there is no CI to hold the local gate to")
    found: set[str] = set()
    for wf in files:
        text = (root / wf).read_text(encoding="utf-8")
        found |= scripts_in(ci_commands(text), pattern)
    return found


def judge(root: pathlib.Path) -> tuple[int, list[str], str]:
    """(exit code, failure lines, ok line) for the repo at `root`."""
    config = read_config(root)
    dirs = config["GOH_CI_PARITY_DIRS"] or DEFAULT_DIRS
    pattern = script_pattern(dirs)
    steps = config["GOH_CI_STEPS"]
    if not steps:
        raise ConfigError("no GOH_CI_STEPS in .gatesrc: the local pre-push gate declares no steps")
    floor_text = config["GOH_CI_PARITY_MIN_SCRIPTS"] or "1"
    if not floor_text.isdigit():
        raise ConfigError(f"GOH_CI_PARITY_MIN_SCRIPTS must be a whole number (got {floor_text!r})")
    exempt = parse_exempt(config["GOH_CI_PARITY_EXEMPT"])
    ci = ci_scripts(root, pattern)
    local = scripts_in(steps.split(":"), pattern)

    floor = int(floor_text)
    if len(ci) < floor:
        thin = (
            f"found {len(ci)} scripts in CI's run: steps, floor {floor}: the scan lost its subject"
        )
        return 1, [thin], ""
    bad = [
        f"CI runs {script} and the local pre-push gate does not: add it to GOH_CI_STEPS in "
        "`.gatesrc`, or a push can pass here and fail there"
        for script in sorted(ci - local - set(exempt))
    ]
    for script in sorted(exempt):
        if script not in ci:
            bad.append(f"stale exemption {script}: CI no longer runs it")
        elif script in local:
            bad.append(f"stale exemption {script}: the local gate runs it now")
    done = (
        f"{len(ci)} scripts in CI, every gate among them runs locally ({len(exempt)} exempt "
        "with a reason)"
    )
    return (1 if bad else 0), bad, done


def report(root: pathlib.Path) -> int:
    try:
        code, bad, done = judge(root)
    except ConfigError as exc:
        err(f"[{NAME}] {exc}")
        return 2
    for line in bad:
        err(f"[{NAME}] {line}")
    if code == 0:
        ok(f"[{NAME}] {done}")
    return code


def probe() -> int:
    """Run the gate over fixtures built to be wrong in each way it polices, and fail if one reads
    clean: a gate that cannot go red is indistinguishable from a gate that passes."""
    workflow = "jobs:\n  a:\n    steps:\n      - run: scripts/a.sh\n      - run: tools/b.py\n"
    cases = [
        ("a CI-only script is red", "./scripts/a.sh", "", 1),
        ("every CI script run locally is green", "./scripts/a.sh:python3 tools/b.py", "", 0),
        (
            "an exemption CI no longer runs is red",
            "./scripts/a.sh:python3 tools/b.py",
            "GOH_CI_PARITY_EXEMPT='tools/gone.py: x'",
            1,
        ),
        (
            "an exemption the local gate now runs is red",
            "./scripts/a.sh:python3 tools/b.py",
            "GOH_CI_PARITY_EXEMPT='tools/b.py: x'",
            1,
        ),
        (
            "an exemption covers a CI-only script",
            "./scripts/a.sh",
            "GOH_CI_PARITY_EXEMPT='tools/b.py: x'",
            0,
        ),
        (
            "a scan under its floor is red",
            "./scripts/a.sh:python3 tools/b.py",
            "GOH_CI_PARITY_MIN_SCRIPTS=3",
            1,
        ),
    ]
    failed = 0
    for label, steps, extra, want in cases:
        with tempfile.TemporaryDirectory(prefix="goh-ci-parity.") as tmp:
            root = pathlib.Path(tmp)
            (root / ".github" / "workflows").mkdir(parents=True)
            (root / ".github" / "workflows" / "ci.yml").write_text(workflow, encoding="utf-8")
            (root / ".gatesrc").write_text(f"GOH_CI_STEPS='{steps}'\n{extra}\n", encoding="utf-8")
            got = judge(root)[0]
        if got != want:
            failed += 1
            err(f"[{NAME} probe] {label}: exit {got}, wanted {want}")
    with tempfile.TemporaryDirectory(prefix="goh-ci-parity.") as tmp:
        (pathlib.Path(tmp) / ".gatesrc").write_text("GOH_CI_STEPS='x'\n", encoding="utf-8")
        try:
            judge(pathlib.Path(tmp))
            failed += 1
            err(f"[{NAME} probe] no workflows read as a verdict, not a precondition")
        except ConfigError:
            pass
    if failed:
        return 1
    ok(f"[{NAME} probe] red on a CI-only script, a stale exemption, a thin scan and no CI")
    return 0


def main(argv: list[str]) -> int:
    if "--probe" in argv:
        return probe()
    root = pathlib.Path(repo_root() or os.getcwd())
    if "--list" in argv:
        try:
            pattern = script_pattern(read_config(root)["GOH_CI_PARITY_DIRS"] or DEFAULT_DIRS)
            print("\n".join(sorted(ci_scripts(root, pattern))))
        except ConfigError as exc:
            err(f"[{NAME}] {exc}")
            return 2
        return 0
    return report(root)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
