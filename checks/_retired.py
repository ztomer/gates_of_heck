"""Forward a retired Python checker's entry point to its native check (Phase N3).

A consumer that calls `python3 $GOH_DIR/checks/check_no_emoji.py` by path keeps working: the file
is now three lines that exec `gates/goh.sh <check>` with the same arguments, so it gets the one
binary resolution and the one refusal when no binary exists. Only the entry points a consumer was
MEASURED calling by path are kept (an estate sweep, 2026-10-06); the rest were deleted, and their
behaviour is the frozen spec in `tests/reference_kit.py`.

claim: 6 checks in checks/_retired.py:SHIMS
"""

from __future__ import annotations

import os
import sys
from typing import NoReturn

# Every retired checker -> the `goh` check that replaced it, spelled once. Read by
# `_calibration.py` (a registry key names a gate that still exists) and by the test suite's
# `run_check`. Pinned by tests/test_retired_shims.py: every value is a check `goh.sh` dispatches.
NATIVE = {
    "check_no_emoji": "emoji",
    "check_no_conflict_markers": "markers",
    "check_file_length": "length",
    "check_no_secrets": "secrets",
    "check_no_home_paths": "home-paths",
    "check_no_allow": "no-allow",
    "check_no_empty_assert": "empty-assert",
    "check_no_screen_presentation": "screen",
    "check_lints_optin": "lints",
    "check_skills_corpus": "skills",
    "check_dep_currency": "deps",
    "check_no_unreaped_spawn": "unreaped-spawn",
    "check_version_provenance": "version-provenance",
    "check_no_kill_by_name": "kill-by-name",
    "check_claim_derivation": "claim-derivation",
    "check_md_links": "md-links",
    "check_lock_version": "lock-version",
    "check_tag_version": "tag-version",
    "check_no_credential_urls": "credential-urls",
    "check_python_formatted": "python-formatted",
    "check_shell_lint": "shell-lint",
    "check_exclusion_has_ceiling": "ceiling",
}

# The entry points still on disk as forwarders, because a consumer calls them BY PATH. Pinned both
# ways by tests/test_retired_shims.py: each forwards to `NATIVE[stem]`, and no other file does.
SHIMS = [
    "check_no_emoji",
    "check_file_length",
    "check_python_formatted",
    "check_version_provenance",
    "check_tag_version",
    "check_no_allow",
]


def forward(check: str, module: str) -> NoReturn:
    """Run as a script: exec `bash gates/goh.sh <check> <argv...>`; never returns.

    IMPORTED, it raises instead: an exec at import time would replace the importing process
    (a pytest worker died silently that way), and there is no Python left to import -- a caller
    that wanted the module wanted the retired implementation, so it is told where it went."""
    if module != "__main__":
        raise ImportError(
            f"{module} is retired (Phase N3): run `goh {check}`; its Python behaviour is the "
            "frozen spec in tests/reference_kit.py"
        )
    goh = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gates", "goh.sh"
    )
    sys.stdout.flush()
    os.execvp("bash", ["bash", goh, check, *sys.argv[1:]])
