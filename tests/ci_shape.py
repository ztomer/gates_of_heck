"""The suite's CI SHAPE: the environment hosted CI gives goh's suite, read from the workflow itself.

THE CLASS (GOH.CIPARITY): box green, CI red. 5 of 12 goh releases before this were green on the
owner's machine and red in hosted CI, each for a property of the box the runner lacks: the
pytest release (v0.139.6), the auto-loaded plugins (GOH.PLUGINPARITY), and a suite home linking
the owner's ~/Projects, whose live loop queue a test read (v0.139.10, GOH.CIRED01310). Each was
fixed after its release, one at a time. So a LAND's suite (tests/suite_home.py `shape`) runs as
the runner runs it: a home holding none of the owner's state (no ~/Projects, no ~/.claude/skills),
every XDG directory inside it, and the variables `.github/workflows/gate.yml` gives the step that
runs the gate -- read from that file here, never restated, so the two cannot drift.

`overlay(workflow, workspace)` is that reading: the workflow's `env`, the gate job's `env`, the
gate step's own `env`, every `echo "NAME=VALUE" >> "$GITHUB_ENV"` a step before it runs, and the
two variables GitHub sets in every job (RUNNER_DEFAULTS). It FAILS CLOSED: a value it cannot
evaluate here (a `${{ }}` expression, a shell variable other than $GITHUB_WORKSPACE) or a
GITHUB_ENV write of another form raises ShapeError, so a variable gate.yml gains is carried or is a
red suite, never silently dropped (tests/test_ci_shape.py plants each).

What it does not model, named: the runner's own image variables (ImageOS, RUNNER_*), the PATH the
steps' actions prepend (setup-python, the floors action's shellcheck), and its 3 cores.
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(".github") / "workflows" / "gate.yml"
# The command whose step is the gate (gate.yml's "The gate" step): its env is the suite's.
GATE_RUN = "./tools/gate.sh --full"
# What GitHub sets in every job's environment (docs: "Default environment variables").
RUNNER_DEFAULTS = {"CI": "true", "GITHUB_ACTIONS": "true"}
# The names the leg applied, as JSON, for every child to read (tests/test_ci_shape.py's drift test):
# the caller's GOH_* drop removes some of them again, as it does on the runner.
APPLIED = "GATES_OF_HECK_SUITE_CI_ENV"

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ENV_WRITE = re.compile(r'echo\s+"([A-Za-z_][A-Za-z0-9_]*)=([^"]*)"\s*>>\s*"\$GITHUB_ENV"')


class ShapeError(ValueError):
    """gate.yml sets the gate's environment in a way this reading cannot evaluate."""


def _lines(text: str) -> list[tuple[int, str]]:
    out = []
    for raw in text.splitlines():
        body = raw.lstrip(" ")
        if body and not body.startswith("#"):
            out.append((len(raw) - len(body), body.rstrip()))
    return out


def _scalar(text: str) -> str:
    text = text.strip()
    if text[:1] in "\"'":
        quote = text[0]
        end = text.find(quote, 1)
        if end < 0:
            raise ShapeError(f"unterminated quote: {text}")
        return text[1:end]
    return re.sub(r"\s+#.*$", "", text)


def parse(text: str):
    """The YAML subset gate.yml is written in: block mappings, block sequences, scalars, flow
    lists kept as their text, and `|` block scalars. Enough for the workflow, not a YAML parser."""
    lines = _lines(text)
    raw = text.splitlines()

    def block(i: int, indent: int):
        if lines[i][1].startswith("- "):
            return seq(i, indent)
        return mapping(i, indent)

    def mapping(i: int, indent: int):
        out: dict = {}
        while i < len(lines) and lines[i][0] == indent and not lines[i][1].startswith("- "):
            key, _, rest = lines[i][1].partition(":")
            rest = rest.strip()
            i += 1
            if rest in ("|", "|-", ">", ">-"):
                out[key], i = scalar_block(i, indent)
            elif rest:
                out[key] = _scalar(rest)
            elif i < len(lines) and lines[i][0] > indent:
                out[key], i = block(i, lines[i][0])
            elif i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
                out[key], i = seq(i, indent)
            else:
                out[key] = None
        return out, i

    def seq(i: int, indent: int):
        out: list = []
        while i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
            # The item's body is a mapping at the indent of the text after "- ".
            inner = indent + 2
            lines[i] = (inner, lines[i][1][2:])
            item, i = mapping(i, inner)
            out.append(item)
        return out, i

    # A block scalar's text is the raw lines below its key, read from the source by position.
    starts = [n for n, r in enumerate(raw) if r.strip() and not r.lstrip(" ").startswith("#")]

    def scalar_block(i: int, indent: int):
        if i >= len(lines) or lines[i][0] <= indent:
            return "", i
        first = starts[i]
        while i < len(lines) and lines[i][0] > indent:
            i += 1
        last = starts[i] if i < len(lines) else len(raw)
        return "\n".join(raw[first:last]), i

    if not lines:
        return {}
    got, _ = block(0, lines[0][0])
    return got


def _value(name: str, value: object, workspace: str) -> str:
    text = "" if value is None else str(value)
    if "${{" in text:
        raise ShapeError(f"{name}={text}: a workflow expression the suite cannot evaluate")
    text = text.replace("${GITHUB_WORKSPACE}", workspace).replace("$GITHUB_WORKSPACE", workspace)
    if "$" in text:
        raise ShapeError(f"{name}={text}: a shell variable the suite cannot evaluate")
    return text


def _env(block: object, where: str, workspace: str) -> dict[str, str]:
    if block is None:
        return {}
    if not isinstance(block, dict):
        raise ShapeError(f"{where}: env is not a mapping")
    out = {}
    for name, value in block.items():
        if not _NAME.fullmatch(name):
            raise ShapeError(f"{where}: {name!r} is not a variable name")
        out[name] = _value(name, value, workspace)
    return out


def _writes(run: str, where: str, workspace: str) -> dict[str, str]:
    out = {}
    for line in run.splitlines():
        if "GITHUB_ENV" not in line:
            continue
        m = _ENV_WRITE.search(line)
        if not m:
            raise ShapeError(
                f"{where}: a GITHUB_ENV write this reading cannot evaluate: {line.strip()}"
            )
        out[m.group(1)] = _value(m.group(1), m.group(2), workspace)
    return out


def overlay(workflow: Path, workspace: str) -> dict[str, str]:
    """The variables the gate step of `workflow` runs with, `workspace` its checkout."""
    doc = parse(workflow.read_text())
    if not isinstance(doc, dict) or not isinstance(doc.get("jobs"), dict):
        raise ShapeError(f"{workflow}: no jobs")
    found = []
    for job_name, job in doc["jobs"].items():
        steps = (job or {}).get("steps") or []
        for n, step in enumerate(steps):
            if str(step.get("run", "")).strip() == GATE_RUN:
                found.append((job_name, job, steps, n))
    if len(found) != 1:
        raise ShapeError(f"{workflow}: {len(found)} steps run `{GATE_RUN}`, not one")
    job_name, job, steps, gate = found[0]
    out = dict(RUNNER_DEFAULTS)
    out.update(_env(doc.get("env"), "workflow", workspace))
    out.update(_env(job.get("env"), f"job {job_name}", workspace))
    for n, step in enumerate(steps[:gate]):
        out.update(_writes(str(step.get("run") or ""), f"job {job_name} step {n}", workspace))
    out.update(_env(steps[gate].get("env"), f"job {job_name} gate step", workspace))
    return out
