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

THE SHARDED SHAPE (GOH.CIWALL). gate.yml runs the suite as a matrix of coverage parts, each
`./gates/rust_gate.sh` with GOH_COV_PART=${{ matrix.part }} (a shard K/N, tests/_shard.py),
while the gate step itself makes the workspace's part (GOH_COV_PART=workspace) and a last job merges
them. So the SUITE's step is the one whose GOH_COV_PART is a matrix expression -- the overlay reads
ITS job and step, and the partition variable is no environment property: a land runs the suite
whole, every shard at once (`partition` names the shards, tests/test_suite_shard.py proves they
cover every test once). A workflow with no such step is the one-job shape: the gate's step is the
suite's.

THE SETUP IS ONE ACTION (GOH.CIWALL2). Every job's setup -- GOH_DIR, the floors, the toolchain, the
tools -- is the composite action `.github/actions/goh-setup`, so a step `uses: ./.github/...` is
followed into its `action.yml` and ITS steps' GITHUB_ENV writes are carried as the workflow's own
(nested local actions too). It fails closed the same way: a local action this reading cannot open,
or one that is not composite (whose writes no reading of YAML can see), raises ShapeError. `reads`
names every file the overlay reads, which lib/test_closure.py's ENTRY_READS must hold.

What it does not model, named: the runner's own image variables (ImageOS, RUNNER_*), the PATH the
steps' actions prepend (setup-python, the floors action's shellcheck), and its 3 cores.
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(".github") / "workflows" / "gate.yml"
# The command whose step is the gate (gate.yml's "The gate" step): its env is the suite's when the
# workflow has no shards.
GATE_RUN = "./tools/gate.sh --full"
# The variable that makes a step one coverage part (gates/_coverage_parts.sh); a matrix expression
# in it makes the step the suite's shards.
PART_VAR = "GOH_COV_PART"
_MATRIX = re.compile(r"\$\{\{\s*matrix\.([A-Za-z_][A-Za-z0-9_-]*)\s*\}\}")
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


def _value(name: str, value: object, workspace: str, shell: bool = True) -> str:
    """`value` as the runner gives it. Only a GITHUB_ENV write (`shell`) is expanded by a shell:
    an `env:` block's `$GITHUB_WORKSPACE` stays those characters on the runner (GOH.CIWALL), so
    there it is refused like any other variable, never read as the checkout."""
    text = "" if value is None else str(value)
    if "${{" in text:
        raise ShapeError(f"{name}={text}: a workflow expression the suite cannot evaluate")
    if shell:
        text = text.replace("${GITHUB_WORKSPACE}", workspace).replace(
            "$GITHUB_WORKSPACE", workspace
        )
    if "$" in text:
        where = "the suite cannot evaluate" if shell else "an env: block never expands"
        raise ShapeError(f"{name}={text}: a shell variable {where}")
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
        out[name] = _value(name, value, workspace, shell=False)
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


def _root(workflow: Path) -> Path:
    """The checkout a workflow is in: three levels above `.github/workflows/X.yml`; a planted
    workflow elsewhere (a test's) reads its local actions from its own directory."""
    here = workflow.resolve().parent
    if here.name == "workflows" and here.parent.name == ".github":
        return here.parent.parent
    return here


def _local_steps(uses: str, root: Path, where: str, seen: list[Path]) -> list[dict]:
    """The steps of the local composite action `uses` (`./path`), its file appended to `seen`."""
    path = root / uses[2:].rstrip("/") / "action.yml"
    if not path.is_file():
        raise ShapeError(f"{where}: uses {uses}, which has no action.yml this reading can open")
    doc = parse(path.read_text())
    runs = doc.get("runs") if isinstance(doc, dict) else None
    if not isinstance(runs, dict) or runs.get("using") != "composite":
        raise ShapeError(f"{where}: uses {uses}, not a composite action: its writes are unseen")
    seen.append(path)
    return runs.get("steps") or []


def _step_writes(step: dict, root: Path, where: str, workspace: str, seen: list[Path]):
    """What one step leaves in GITHUB_ENV: its own `run`'s writes, or a local action's steps'."""
    out = _writes(str(step.get("run") or ""), where, workspace)
    uses = str(step.get("uses") or "")
    if uses.startswith("./"):
        for n, inner in enumerate(_local_steps(uses, root, where, seen)):
            out.update(_step_writes(inner, root, f"{uses} step {n}", workspace, seen))
    return out


def _doc(workflow: Path) -> dict:
    doc = parse(workflow.read_text())
    if not isinstance(doc, dict) or not isinstance(doc.get("jobs"), dict):
        raise ShapeError(f"{workflow}: no jobs")
    return doc


def _steps(doc: dict, match) -> list[tuple[str, dict, list, int]]:
    found = []
    for job_name, job in doc["jobs"].items():
        steps = (job or {}).get("steps") or []
        for n, step in enumerate(steps):
            if match(step):
                found.append((job_name, job, steps, n))
    return found


def _axis(step: dict) -> str | None:
    """The matrix axis a step's GOH_COV_PART names, or None."""
    env = step.get("env")
    m = _MATRIX.fullmatch(str(env.get(PART_VAR, "")).strip()) if isinstance(env, dict) else None
    return m.group(1) if m else None


def _suite_step(workflow: Path, doc: dict) -> tuple[str, dict, list, int, str | None]:
    """The step the suite runs in: the sharded one, else the gate's. Exactly one either way."""
    sharded = _steps(doc, lambda s: _axis(s) is not None)
    if len(sharded) > 1:
        raise ShapeError(f"{workflow}: {len(sharded)} steps shard the suite ({PART_VAR}), not one")
    if sharded:
        job_name, job, steps, n = sharded[0]
        return job_name, job, steps, n, _axis(steps[n])
    found = _steps(doc, lambda s: str(s.get("run", "")).strip() == GATE_RUN)
    if len(found) != 1:
        raise ShapeError(f"{workflow}: {len(found)} steps run `{GATE_RUN}`, not one")
    return (*found[0], None)


def _flow_list(text: object, where: str) -> list[str]:
    raw = str(text or "").strip()
    if not (raw.startswith("[") and raw.endswith("]")):
        raise ShapeError(f"{where}: a matrix axis this reading cannot evaluate: {raw!r}")
    return [_scalar(v) for v in raw[1:-1].split(",") if v.strip()]


def partition(workflow: Path) -> list[str]:
    """The suite's shards: the values of the matrix axis its step's GOH_COV_PART names, in order;
    [] for the one-job shape."""
    doc = _doc(workflow)
    job_name, job, *_, axis = _suite_step(workflow, doc)
    if axis is None:
        return []
    matrix = ((job.get("strategy") or {}).get("matrix")) or {}
    if not isinstance(matrix, dict) or axis not in matrix:
        raise ShapeError(
            f"{workflow}: job {job_name} shards on matrix.{axis}, which it does not set"
        )
    return _flow_list(matrix[axis], f"job {job_name} matrix.{axis}")


def gate_part(workflow: Path) -> str:
    """The coverage part the gate step itself makes ("" when it measures whole)."""
    found = _steps(_doc(workflow), lambda s: str(s.get("run", "")).strip() == GATE_RUN)
    if len(found) != 1:
        raise ShapeError(f"{workflow}: {len(found)} steps run `{GATE_RUN}`, not one")
    _job, _j, steps, n = found[0]
    env = steps[n].get("env")
    return str(env.get(PART_VAR, "")) if isinstance(env, dict) else ""


def _overlay(workflow: Path, workspace: str, seen: list[Path]) -> dict[str, str]:
    doc = _doc(workflow)
    job_name, job, steps, at, axis = _suite_step(workflow, doc)
    out = dict(RUNNER_DEFAULTS)
    out.update(_env(doc.get("env"), "workflow", workspace))
    out.update(_env(job.get("env"), f"job {job_name}", workspace))
    root = _root(workflow)
    for n, step in enumerate(steps[:at]):
        out.update(_step_writes(step, root, f"job {job_name} step {n}", workspace, seen))
    env = steps[at].get("env")
    if axis is not None:
        env = {k: v for k, v in env.items() if k != PART_VAR}
    out.update(_env(env, f"job {job_name} {'suite' if axis else 'gate'} step", workspace))
    return out


def overlay(workflow: Path, workspace: str) -> dict[str, str]:
    """The variables the suite's step of `workflow` runs with, `workspace` its checkout: the
    sharded step's (its partition variable left out: a land runs every shard), else the gate's."""
    return _overlay(workflow, workspace, [])


def reads(workflow: Path) -> list[str]:
    """Every file `overlay` reads, relative to the checkout: the workflow, then each local action
    it follows, once each."""
    seen: list[Path] = []
    _overlay(workflow, "/ws", seen)
    root = _root(workflow)
    files = [workflow.resolve(), *(p.resolve() for p in seen)]
    return list(dict.fromkeys(p.relative_to(root).as_posix() for p in files))
