"""The meta-gate: every script a gate or hook invokes must exist.

This is the test class that catches "gate references a checker that was never
shipped" (review findings #1 and #2) forever. Proven red against HEAD:
swift_gate.sh -> checks/check_swift_coverage.py (missing),
hooks/pre-commit -> tools/check_no_emoji.py (missing pre-self-host).

BLIND SPAT FIX (2026-08-26): lines carrying an existence guard ([ -f ... ])
or goh_optional_step used to be SKIPPED entirely — so a typo'd checker
reference inside a guarded line shipped forever, invisible to this suite.
Guarded refs are now parsed like any other: only target-repo-local tools/
references may be absent (the consumer repo's contract); anything pointing at
THIS repo's checks/, gates/ or hooks/ must resolve here.
"""

import re
from pathlib import Path

import pytest

from conftest import REPO_ROOT

REF = re.compile(
    r"((?:checks|tools|hooks|gates)/[A-Za-z0-9_.-]+\.(?:py|sh)|"
    r"\$(?:CHECKS|\{CHECKS\})/[A-Za-z0-9_.-]+\.(?:py|sh))"
)
VAR_MAP = {"$CHECKS": "checks", "${CHECKS}": "checks"}


def _referenced_scripts(files=None) -> list[tuple[str, int, str]]:
    """(file, lineno, normalized ref) for every script ref — GUARDED OR NOT."""
    if files is None:
        sources = [(str(f.relative_to(REPO_ROOT)), f)
                   for pattern in ("gates/*.sh", "hooks/pre-commit", "hooks/pre-push")
                   for f in sorted(REPO_ROOT.glob(pattern))]
    else:
        sources = files
    refs = []
    for fname, f in sources:
        text = f.read_text() if isinstance(f, Path) else f
        for i, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue  # comments document, they do not invoke
            for m in REF.finditer(line):
                ref = m.group(1)
                for var, name in VAR_MAP.items():
                    if ref.startswith(var):
                        ref = f"{name}/{ref.split('/', 1)[1]}"
                        break
                refs.append((fname, i, ref))
    return refs


EXPECTED_REFS = [
    ("gates/structural.sh", "checks/check_no_emoji.py"),
    ("gates/structural.sh", "checks/check_no_conflict_markers.py"),
    ("gates/structural.sh", "checks/check_file_length.py"),
    # NOTE: checks/check_disk_hygiene.py is deliberately NOT referenced by
    # any gate (2026-09-04: disk watch moved out of CI to
    # ~/Projects/scripts/bin/disk_hygiene.sh). If a gate references it
    # again, add the ref here AND justify why a du stat-storm belongs in
    # a commit gate.
]


def test_known_core_refs_present():
    """The parser finds the refs we think it finds (instrument calibration)."""
    got = {(f, r) for f, _, r in _referenced_scripts()}
    for f, r in EXPECTED_REFS:
        assert (f, r) in got, f"parser missed {r} in {f}"


def _missing(refs):
    return [
        (f, line, ref)
        for f, line, ref in refs
        if not (REPO_ROOT / ref).exists()
        and not ref.startswith("tools/")  # target-repo contract, see below
    ]


def test_every_referenced_script_exists():
    missing = _missing(_referenced_scripts())
    assert missing == [], (
        "gates/hooks reference scripts that do not exist in this repo:\n"
        + "\n".join(f"  {f}:{line} -> {ref}" for f, line, ref in missing)
    )


def test_no_gate_runs_disk_hygiene():
    """The disk watch stays OUT of CI. Red-proof: re-adding a
    check_disk_hygiene reference to any gate fails here naming the line —
    move the watch, don't re-wire it (see scripts/bin/disk_hygiene.sh)."""
    refs = _referenced_scripts()
    disk = [(f, line, ref) for f, line, ref in refs if "disk_hygiene" in ref]
    assert disk == [], (
        "a gate references the disk watch again:\n"
        + "\n".join(f"  {f}:{line} -> {ref}" for f, line, ref in disk)
    )


def test_guarded_refs_must_still_resolve_when_they_name_this_repo():
    """THE regression: a typo'd reference inside a [ -f ] guard or an
    optional step used to be skipped by the wiring scan entirely. Red-proof:
    the synthetic gate below names checks/does_not_exist.py behind a guard —
    pre-fix wiring tests passed it; now it is red."""
    synthetic = (
        "synthetic-gate.sh",
        "\n".join([
            "#!/usr/bin/env bash",
            # Same-line forms ONLY: the old scanner skipped any line carrying
            # goh_optional_step / an existence guard, so these were invisible.
            'goh_optional_step "opt" "$CHECKS/does_not_exist.py" python3 "$CHECKS/does_not_exist.py"',
            '[ -f "$CHECKS/guarded_missing.py" ] && python3 "$CHECKS/guarded_missing.py"',
            "# tools/ stays exempt: target-repo-local",
            'goh_optional_step "local" tools/local_check.py true',
        ]) + "\n",
    )
    missing = _missing(_referenced_scripts(files=[synthetic]))
    named = {ref for _, _, ref in missing}
    assert "checks/does_not_exist.py" in named, (
        f"a typo'd OPTIONAL-step reference shipped invisibly; got {named}")
    assert "checks/guarded_missing.py" in named, (
        f"a typo'd GUARDED reference shipped invisibly; got {named}")
    assert not any(r.startswith("tools/") for r in named)


def test_hook_checker_resolution_is_self_hostable():
    """hooks/pre-commit must resolve its checker through a chain that ends at
    a real file: repo-local tools/ copy (installed repos) OR this repo's
    checks/ (delegation). A hook whose only reference cannot exist here is
    broken for every consumer including this one."""
    hook = REPO_ROOT / "hooks" / "pre-commit"
    text = hook.read_text()
    m = re.search(r"/(tools|checks)/(check_no_emoji\.py)", text)
    assert m, "pre-commit does not reference the emoji checker at all"
    kind, name = m.groups()
    if kind == "tools":
        # Legal only if the hook falls back to the shared checkout when the
        # repo-local copy is absent AND this repo can satisfy one branch.
        assert "GOH" in text, (
            "hook references tools/ with no fallback to the shared checkout"
        )
    assert (REPO_ROOT / "checks" / name).exists()


def test_install_sh_exists_and_wires_hooks():
    inst = REPO_ROOT / "install.sh"
    assert inst.exists(), "README promises install.sh; hooks depend on it"
    body = inst.read_text()
    assert "core.hooksPath" in body, "installer must set core.hooksPath"
    for needed in ("pre-commit", "pre-push"):
        assert needed in body, f"installer must wire {needed}"
