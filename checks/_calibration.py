"""Read `checks/gate_calibration.json` and CHECK every claim it makes.

WHY THIS EXISTS. The registry holds 25 entries asserting that named gates have proven they can
fail, and for its whole life nothing in this repo read it: `check_probes_pass.py` swept the gates
it could DISCOVER and ignored the file, while the only reader lived in another estate
(`games/game_asset_factory/tools/check_gate_calibration.py`). A registry nothing reads is a rumour
(SUPERSOTA R7) -- and a rumour that says "proven" is worse than no file, because it reads as
evidence.

So the rule this module enforces is narrow and checkable: **every entry is verified against
reality, and an entry that cannot be is a finding.** Concretely, per entry:

  * the KEY must name a gate this repo actually has (`checks/check_<key>.py|.sh`, `gates/<key>.sh`);
  * a citation naming a file must RESOLVE -- repo-relative first, because three repos own a
    `tools/check_gate_canary.py` and reading the wrong one reports a sound citation as a lie;
  * a prover inside THIS repo must be COMMITTED at HEAD. A file on disk is not a claim, it is a
    rumour somebody can only check on one machine;
  * a prover inside this repo that is cited for a self-proof flag must still DISPATCH on that flag
    AND have had it run green in the sweep that called us. That is the half `check_probes_pass`
    structurally cannot see: it runs what it discovers, so a checker that stopped dispatching on
    `--probe` simply stops being run, and the registry entry keeps earning the word "proven";
  * a prover OUTSIDE this repo must be committed in ITS repo and must list the key among what it
    proves (`--proves`). When that estate is not on this machine the claim is reported, naming who
    verifies it -- reported, not passed in silence.

WHAT IT IS NOT. It is not a second survey of which gates are proven. That is the external
reader's job and duplicating it here would make two numbers that could disagree. This module reads
one file -- the house registry -- and holds it to the estate.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _gitutil import foreign_repo_env  # noqa: E402

# The registry, relative to the repo root.
REGISTRY = os.path.join("checks", "gate_calibration.json")

# Where a citation may point OUTSIDE this repo, and what verifies it there. A path is joined onto
# each root in order; the first that exists wins. Only used for citations naming another estate's
# prover, and the estate that verifies it is named in the output either way.
ESTATE_ROOTS = (
    os.path.expanduser("~/Projects/games"),
    os.path.expanduser("~/Projects"),
)
ESTATE_OWNERS = {
    os.path.expanduser("~/Projects/games"): "game_asset_factory/tools/check_gate_canary.py",
    os.path.expanduser("~/Projects"): "game_asset_factory/tools/check_gate_canary.py",
}

# A citation opens with the file that does the proving: "check_gate_canary.py -- ..." or
# "games/x/tools/check_gate_canary.py -- ...". Anything else (an "in-selftest:" claim, a
# description of the gate's own assertions) names no external prover and is not resolvable here.
CITED_FILE = re.compile(r"\s*([A-Za-z0-9_./~-]+\.(?:py|sh))")

# The self-proof flags a citation may claim, and what makes one a claim rather than prose.
SELF_PROOF_FLAG = re.compile(r"--(?:break-probe|probe|self-test|selftest)\b")


def gate_paths(root: str, key: str) -> list[str]:
    """Where a registry key could name a gate in this repo, most specific first.

    Both spellings are tried for a `checks/` entry, because the registry is not consistent about
    them and a reader that hard-fails on a spelling variant is brittle in the wrong direction: the
    claim it refuses is a real claim about a real gate. (`known_unproven` carried
    `check_swift_coverage` while `proven_by` carried `swift_coverage`.)
    """
    names = [key] if key.startswith("check_") else [f"check_{key}", key]
    out = []
    for name in names:
        out.append(os.path.join("checks", f"{name}.py"))
        out.append(os.path.join("checks", f"{name}.sh"))
    out.append(os.path.join("gates", f"{key}.sh"))
    out.append(os.path.join("lib", f"{key}.sh"))
    return out


def committed(repo_root: str, path: str) -> bool | None:
    """Is `path` inside `repo_root`'s HEAD tree? None when that cannot be answered.

    None (unknown) rather than False, because a file may legitimately live outside any git
    repository and "this directory is not a repository" must not read as "this proof is a lie".
    """
    try:
        inside = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=foreign_repo_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if inside.returncode != 0:
        return None
    top = inside.stdout.strip()
    try:
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(top))
    except ValueError:
        return None
    if rel.startswith(".." + os.sep):
        return None
    try:
        found = subprocess.run(
            ["git", "-C", top, "cat-file", "-e", f"HEAD:{rel}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=foreign_repo_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return found.returncode == 0


def resolves_to(root: str, cited: str) -> str | None:
    """Repo-relative first, then the two gate directories, then the other estates.

    Repo-relative first is not a detail: three repos own a `tools/check_gate_canary.py` and a bare
    name in one repo's registry means THAT repo's. Reading the wrong one is not a near-miss, it is
    a verifier that confidently calls a true citation false.
    """
    cited = os.path.expanduser(cited)
    candidates = [
        os.path.join(root, cited),
        os.path.join(root, "tools", os.path.basename(cited)),
        os.path.join(root, "checks", os.path.basename(cited)),
    ]
    for estate in ESTATE_ROOTS:
        candidates.append(os.path.join(estate, cited))
    return next((c for c in candidates if os.path.isfile(c)), None)


def owner_of(path: str) -> str:
    """The human-readable name of whoever verifies a proof outside this repo."""
    for estate, owner in ESTATE_OWNERS.items():
        if path.startswith(estate + os.sep):
            return owner
    return "the estate that owns it"


def claims(path: str) -> set[str] | None:
    """The registry keys a prover says it substantiates, or None when it does not say.

    Asked of the prover with `--proves` rather than inferred from its filename: a name-matcher
    cannot see that a gate is proven by a composed-gate assertion rather than a case over it, so it
    would report a sound citation as a defect.
    """
    try:
        result = subprocess.run(
            [sys.executable, path, "--proves"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=foreign_repo_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def load(root: str) -> dict | None:
    """The parsed registry, or None when this repo has none."""
    try:
        with open(os.path.join(root, REGISTRY), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def verify(root: str, data: dict, ran: set[str]) -> list[str]:
    """Every way the registry's claims fail to hold. `ran` is the set of repo-relative paths whose
    self-proof this sweep actually ran to green.

    An empty list means every claim checked out. A non-empty one names the entry, so a reader can
    see which claim died rather than that "the registry is stale".
    """
    bad: list[str] = []
    entries = data.get("proven_by")
    if not isinstance(entries, dict) or not entries:
        return ["proven_by is missing or empty -- a registry with no claims proves nothing"]
    unproven = data.get("known_unproven", [])
    whys = data.get("unproven_why", {})
    if not isinstance(unproven, list):
        return ["known_unproven is not a list"]
    if not isinstance(whys, dict):
        whys = {}

    for key in list(entries) + list(unproven):
        # (a) The key must name a gate that EXISTS. This half is estate-independent: it holds on
        # a machine that has never heard of the other estates, which is the point.
        if not any(os.path.isfile(os.path.join(root, rel)) for rel in gate_paths(root, key)):
            where = "proven_by" if key in entries else "known_unproven"
            bad.append(f"{where}: {key!r} names no gate here (tried {gate_paths(root, key)})")

    for key in unproven:
        if key in entries:
            bad.append(f"{key!r} is both proven and known_unproven -- one of the two is a lie")
        if not str(whys.get(key, "")).strip():
            bad.append(f"known_unproven: {key!r} has no entry in unproven_why")

    for key, why in sorted(entries.items()):
        if not isinstance(why, str):
            bad.append(f"proven_by: {key!r} is not a string")
            continue
        match = CITED_FILE.match(why)
        if not match:
            continue  # a self-proof claim; the sweep itself covers it
        cited = match.group(1)
        rest = why[match.end() :]
        path = resolves_to(root, cited)
        if path is None:
            bad.append(f"proven_by: {key!r} cites {cited}, which does not exist")
            continue
        in_head = committed(root, path)
        if in_head is False:
            bad.append(
                f"proven_by: {key!r} cites {cited}, which exists on disk but is in NO COMMIT -- "
                f"the proof runs on this machine and cannot be checked by anyone else"
            )
            continue
        rel = os.path.relpath(path, root)
        if rel.startswith(".." + os.sep):
            # Outside this repo. The prover is a different estate's gate, and `--proves` is the
            # only way to check it really claims this gate -- the citation's own filename cannot.
            claimed = claims(path)
            if claimed is not None and key not in claimed:
                bad.append(
                    f"proven_by: {key!r} cites {cited}, which does not list {key!r} among what "
                    f"it proves"
                )
            continue
        flag = SELF_PROOF_FLAG.search(rest)
        if flag and rel not in ran:
            # The load-bearing half. The sweep runs what it DISCOVERS, so a checker that stopped
            # dispatching on its flag stops being run at all -- silently, while the registry keeps
            # earning it the word "proven".
            bad.append(
                f"proven_by: {key!r} cites {rel} {flag.group(0)}, which this sweep did not run to "
                f"green: it declares no such self-proof, or the self-proof failed. A citation to a "
                f"proof that does not run is the unrun-probe failure one level of indirection out."
            )
    return bad


def external_note(root: str, data: dict) -> list[str]:
    """One line per OUTSIDE verifier: how many claims it carries, and what was checked about it.

    A claim that lives in another estate is not a failure -- it is a claim with an owner, and this
    gate checks the two things it CAN about that owner (its file is in a commit, and it lists the
    key among what it proves). What it must never be is a silent pass, so the owner is named
    rather than assumed.
    """
    by_owner: dict[str, list[str]] = {}
    for key, why in sorted(data.get("proven_by", {}).items()):
        if not isinstance(why, str):
            continue
        match = CITED_FILE.match(why)
        if not match:
            continue
        path = resolves_to(root, match.group(1))
        if path is None:
            continue  # already a finding
        rel = os.path.relpath(path, root)
        if not rel.startswith(".." + os.sep):
            continue  # inside this repo, verified above
        if committed(os.path.dirname(os.path.dirname(path)), path) is False:
            continue  # already a finding
        by_owner.setdefault(owner_of(path), []).append(key)
    return [
        f"{len(keys)} entr{'y' if len(keys) == 1 else 'ies'} cite {owner} -- committed there, and "
        f"it lists each of them among what it proves ({', '.join(keys[:4])}"
        f"{', ...' if len(keys) > 4 else ''})"
        for owner, keys in sorted(by_owner.items())
    ]
