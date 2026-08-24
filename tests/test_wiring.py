"""The meta-gate: every script a gate or hook invokes must exist.

This is the test class that catches "gate references a checker that was never
shipped" (review findings #1 and #2) forever. Proven red against HEAD:
swift_gate.sh -> checks/check_swift_coverage.py (missing),
hooks/pre-commit -> tools/check_no_emoji.py (missing pre-self-host).
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
GUARD = re.compile(r"\[\s+-[def]\s+")          # [ -f x ] existence guard lines
OPTIONAL_STEP = "goh_optional_step"


def _referenced_scripts() -> list[tuple[str, int, str]]:
    """(file, lineno, normalized ref) for every guarded-or-not script ref."""
    refs = []
    for pattern in ("gates/*.sh", "hooks/pre-commit", "hooks/pre-push"):
        for f in sorted(REPO_ROOT.glob(pattern)):
            for i, line in enumerate(f.read_text().splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue  # comments document, they do not invoke
                if GUARD.search(line) or OPTIONAL_STEP in line:
                    continue  # existence-guarded: legal even if target-repo-local
                for m in REF.finditer(line):
                    ref = m.group(1)
                    for var, name in VAR_MAP.items():
                        if ref.startswith(var):
                            ref = f"{name}/{ref.split('/', 1)[1]}"
                            break
                    refs.append((str(f.relative_to(REPO_ROOT)), i, ref))
    return refs


EXPECTED_REFS = [
    ("gates/structural.sh", "checks/check_no_emoji.py"),
    ("gates/structural.sh", "checks/check_no_conflict_markers.py"),
    ("gates/structural.sh", "checks/check_file_length.py"),
    ("gates/structural.sh", "checks/check_disk_hygiene.py"),
]


def test_known_core_refs_present():
    """The parser finds the refs we think it finds (instrument calibration)."""
    got = {(f, r) for f, _, r in _referenced_scripts()}
    for f, r in EXPECTED_REFS:
        assert (f, r) in got, f"parser missed {r} in {f}"


def test_every_referenced_script_exists():
    missing = [
        (f, line, ref)
        for f, line, ref in _referenced_scripts()
        if not (REPO_ROOT / ref).exists()
        and not ref.startswith("tools/")  # target-repo contract, see below
    ]
    assert missing == [], (
        "gates/hooks reference scripts that do not exist in this repo:\n"
        + "\n".join(f"  {f}:{line} -> {ref}" for f, line, ref in missing)
    )


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
