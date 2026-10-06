"""The self-proof for `checks/check_no_credential_urls.py`: the measured table, run.

Split out so neither file crowds the cap. It imports the checker it proves rather than copying its
rules, so a probe cannot pass on a stale copy.
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
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from check_no_credential_urls import KEYS, config_findings, findings_for, report, verdict  # noqa: E402
from _credential_config import read_values  # noqa: E402
from tui.lib import err, ok  # noqa: E402

# (key, value, expect_finding) -- the helper and header shapes, measured 2026-10-05.
CONFIG_TABLE = (
    ("credential.helper", "osxkeychain", False),
    ("credential.helper", "!f() { echo password=$GH_TOKEN; }; f", False),
    ("credential.helper", "!f() { echo password=s3cretvalue; }; f", True),
    ("http.extraheader", "X-Trace: on", False),
    ("http.extraheader", "AUTHORIZATION: basic dXNlcjpwYXNzd29yZA==", True),
    ("http.extraheader", "Authorization: Bearer ${TOKEN}", False),
)


# (label, url, expect_finding)
TABLE = (
    ("clean https", "https://github.com/o/r.git", False),
    ("user:token", "https://user:gho_" + "a" * 36 + "@github.com/o/r.git", True),
    ("token only", "https://gho_" + "a" * 36 + "@github.com/o/r.git", True),
    ("bare username", "https://someuser@github.com/o/r.git", False),
    ("empty user, password", "https://:tok@github.com/o/r.git", True),
    ("empty password", "https://user:@github.com/o/r.git", True),
    ("password containing @", "https://user:p@ss@github.com/o/r.git", True),
    ("percent-encoded @ in user", "https://user%40x:pw@github.com/o/r.git", True),
    ("ssh with a username", "ssh://git@github.com/o/r.git", False),
    ("ssh with a bare username", "ssh://buildbot@github.com/o/r.git", False),
    # ...and the row that killed the scheme allow-list the first version had. `ssh://` with a token
    # is not a LIVE credential (git hands the userinfo to ssh, which ignores a password -- measured
    # against a real `ls-remote`) and it is still a plaintext secret sitting in `.git/config`. A
    # scheme allow-list would have called this clean.
    ("ssh with a token", "ssh://gho_" + "a" * 36 + "@github.com/o/r.git", True),
    ("ssh with a password", "ssh://user:sekritpass@github.com/o/r.git", True),
    ("scp form", "git@github.com:o/r.git", False),
    ("file url", "file:///Users/x/thing", False),
    ("absolute local path", "/Users/x/thing", False),
    ("relative path", "../sibling", False),
    ("no path at all", "https://github.com", False),
    ("the unauthenticated git protocol", "git://github.com/o/r.git", False),
    ("ipv6 literal", "https://[::1]:8080/o/r", False),
    ("uppercase scheme", "HTTPS://u:gho_" + "a" * 36 + "@github.com/o/r.git", True),
    ("github pat, bare", "https://github_pat_" + "b" * 22 + "@github.com/o/r.git", True),
    ("long opaque userinfo", "https://xK39fLQ2pR7vT4wYz8Ab1Cd0Ef@github.com/o/r.git", True),
)


def probe() -> int:
    bad = 0
    for label, url, want in TABLE:
        got = verdict(url) is not None
        if got != want:
            err(f"probe: {label} — wanted finding={want}, got {got}")
            bad += 1
        else:
            ok(f"probe: {label} -> {'finding' if got else 'silent'}")

    # The two halves that are about the KEY rather than the URL, because the obvious regex gets both
    # of them wrong and a table of URLs cannot see either.
    import tempfile

    with tempfile.TemporaryDirectory() as td:

        def plant(*args):
            subprocess.run(["git", "-C", td, *args], capture_output=True, check=True)

        plant("init", "-q", "-b", "main")
        plant("config", "user.email", "p@e.invalid")
        plant("config", "user.name", "p")
        tok = "gho_" + "c" * 36

        git_keys = ("remote\\..*\\.url", "remote\\..*url$")
        plant("config", "remote.origin.pushurl", f"https://u:{tok}@github.com/o/r.git")
        got = subprocess.run(
            ["git", "-C", td, "config", "--get-regexp", KEYS], capture_output=True, text=True
        )
        if "pushurl" not in got.stdout:
            err(
                "probe: a credential in a pushurl was not read — `remote\\..*\\.url` cannot match it"
            )
            bad += 1
        else:
            ok("probe: `remote\\..*\\.url` misses pushurl and the form used here does not")
        # ...and the narrow form really does miss it, so the row is a measurement, not a claim.
        narrow = subprocess.run(
            ["git", "-C", td, "config", "--get-regexp", git_keys[0]], capture_output=True, text=True
        )
        if "pushurl" in narrow.stdout:
            err("probe: the narrow form matched pushurl after all — this table is stale")
            bad += 1
        else:
            ok("probe: the narrow form measured above genuinely returns nothing for a pushurl")

        plant("config", "url.https://t@proxy.invalid/.insteadOf", "https://github.com/")
        stored = subprocess.run(
            ["git", "-C", td, "config", "--get-regexp", KEYS], capture_output=True, text=True
        )
        if "insteadof" not in stored.stdout:
            err("probe: git's own lowercasing of `insteadOf` was not accounted for")
            bad += 1
        else:
            ok("probe: git lowercases `insteadOf`, and the form used here reads it anyway")

        # The credential must not appear in the report. A probe that prints the secret teaches the
        # next reader that printing it is fine.
        report_text = report(findings_for(os.path.realpath(td)))
        if tok in report_text:
            err("probe: the report printed the credential it exists to hide")
            bad += 1
        else:
            ok("probe: the report carries a fingerprint and not the credential")
        if "sha256:" not in report_text:
            err("probe: no fingerprint in the report — a finding with no handle is not a finding")
            bad += 1

        # The non-URL shapes (_credential_config.py), each through `git config` and back, judged
        # only on THIS scratch repo's values so the machine's own global helpers cannot answer.
        for key, value, want in CONFIG_TABLE:
            # A fresh slate per row: a previous row's value must not answer for this one.
            for k in {row[0] for row in CONFIG_TABLE}:
                subprocess.run(["git", "-C", td, "config", "--unset-all", k], capture_output=True)
            plant("config", key, value)
            mine = [v for v in read_values(td) if os.path.realpath(td) in os.path.realpath(v[0])]
            got = bool(config_findings(mine))
            if got != want:
                err(f"probe: {key}={value[:24]}… — wanted finding={want}, got {got}")
                bad += 1
            else:
                ok(f"probe: {key} {'finding' if got else 'silent'}")

    if bad:
        err(
            f"check_no_credential_urls --probe: {bad} of {len(TABLE) + len(CONFIG_TABLE) + 4} measured shapes disagree"
        )
        return 1
    ok(
        f"check_no_credential_urls --probe: {len(TABLE) + len(CONFIG_TABLE) + 4} shapes, measured against git's own "
        "config reader and CPython 3.14 urlsplit on 2026-10-05"
    )
    return 0


if __name__ == "__main__":
    sys.exit(probe())
