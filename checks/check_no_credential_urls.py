#!/usr/bin/env python3
"""Fail if a git remote URL, or a git URL rewrite, carries a credential.

    check_no_credential_urls.py            # every remote of the enclosing repo
    check_no_credential_urls.py --probe     # prove this gate can go red
    check_no_credential_urls.py --json      # findings as JSON, for a caller that wants to parse

# WHY THIS EXISTS, and it is a SCOPE no sibling can reach

`check_no_secrets.py` reads git-TRACKED FILES. `.git/config` is untracked by definition, so a live
token in a remote URL survives every sweep the estate runs. Measured, not asserted: a repo whose
`remote.origin.url` is `https://user:gho_<36>@github.com/o/r.git` — the exact prefix and tail
`check_no_secrets.py` refuses in a committed file — is reported `OK` by it, and
`tests/test_check_no_credential_urls.py::test_the_secret_checker_says_OK_over_the_same_repo` pins
that sibling verdict so this checker's reason to exist cannot quietly become false.

# THE MEASURED TABLE

Every row was measured on 2026-10-05 by planting the URL in a scratch repo and reading it back with
`git config --get-regexp`, then parsing it. `--probe` runs all of them, so narrowing this table
without a probe going red is the defect (SUPERSOTA R2).

| URL                                                            | stored? | parse            | verdict |
|----------------------------------------------------------------|---------|------------------|---------|
| `https://github.com/o/r.git`                                    | yes     | no userinfo      | silent  |
| `https://user:token@github.com/o/r.git`                         | yes     | user + password  | FINDING |
| `https://TOKEN@github.com/o/r.git`                              | yes     | user, no password| FINDING (credential-shaped) |
| `https://user@github.com/o/r.git`                               | yes     | user, no password| silent (a username) |
| `https://:token@github.com/o/r.git`                             | yes     | empty user + password | FINDING |
| `https://user:@github.com/o/r.git`                              | yes     | user + empty password | FINDING |
| `https://user:p@ss@github.com/o/r.git`                          | yes     | user + password  | FINDING (raw `@` splits wrong — see below) |
| `https://user%40x:pw@github.com/o/r.git`                        | yes     | user + password  | FINDING |
| `ssh://git@github.com/o/r.git`                                  | yes     | user, no password| silent  |
| `git@github.com:o/r.git`                                        | yes     | no scheme at all | silent  |
| `file:///Users/x/thing`                                         | yes     | no host          | silent  |
| `/Users/x/thing`, `../sibling`                                  | yes     | no scheme        | silent  |
| `git://github.com/o/r.git`, `http://…`                          | yes     | as written       | as the shape says |
| `https://[::1]:8080/o/r`                                        | yes     | IPv6 host        | silent  |
| `HTTPS://u:token@github.com/o/r.git`                            | yes, case kept | scheme uppercased | FINDING |
| `remote.<n>.pushurl`                                            | yes     | —                | FINDING |
| `url.<base>.insteadOf`                                          | yes, and git LOWERCASES the variable name to `insteadof` | — | FINDING |

# THREE THINGS THIS GETS WRONG IF IT IS WRITTEN THE OBVIOUS WAY

1. **`remote\\..*\\.url` does not match `remote.<n>.pushurl`.** Measured: the regex the obvious
   implementation uses returns nothing for a push URL, so the WRITE side of every remote is
   invisible. Asking for `remote\\..*url` catches both -- and also catches nothing else that git
   stores under `remote.`, so it is the form used here.
2. **git lowercases the variable name of a subsection key.** `url.https://…/.insteadOf` is STORED
   and READ BACK as `url.https://…/.insteadof`. A case-sensitive match on `insteadOf` finds nothing,
   which is the one shape a remote must look clean to carry a credential in. Match case-insensitively.
3. **`urlsplit` splits on the LAST `@` of the netloc, not the first.** `https://user:p@ss@host`
   parses as user `user`, password `p@ss`, host `host` — which is what RFC 3986 says and therefore
   right, but the naive `url.split("@")` takes `p@ss` as the host. Measured both.

# WHAT IS DELIBERATELY NOT SCANNED, and why

Scoped to **git config URLs**, and the boundary is stated rather than left for a reader to guess:

  * **`.netrc` / `_netrc`** — a real credential store, and out of scope because it is a file outside
    the repo that no gate in this estate reads from anywhere else either. Half a checker that reads
    `$HOME` is worse than none: it works on one machine, and the day it is wrong nobody can tell.
  * **CI configs** — a token in `.github/workflows` IS committed, so `check_no_secrets.py` already
    has it, and duplicating its patterns here would be the second copy a rule about duplicates should
    never have.
  * **shell history** — not a gate's subject. It is unbounded, private, and machine-local; a gate
    that read it would be reading the operator's whole session.
  * **`credential.helper` and `http.*.extraheader` ARE covered** (2026-10-05) by
    `checks/_credential_config.py`, with `check_no_secrets.py`'s own patterns. See its docstring.

What IS covered is every key git will resolve a URL FROM: `remote.<n>.url`, `remote.<n>.pushurl`,
`url.<base>.insteadOf`, and — measured — every value of a multi-valued key, plus anything reached
through `include.path`, because `--get-regexp` follows includes.

# THE CREDENTIAL IS NEVER PRINTED

A finding names the file, the remote, the host, and a FINGERPRINT: the first 12 hex of
`sha256(<the whole userinfo>)`. Never the value, never a prefix of it, not even the length. The
estate learned this with `gho_` and `ghp_`, and a gate that prints the secret puts it in a
scrollback, a CI log and the next paste.
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

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _credential_config import judge, label, read_values  # noqa: E402
from tui.lib import err, info, ok  # noqa: E402

# The git config keys that hold a URL git will actually resolve a connection FROM. Measured on
# 2026-10-05: `remote\..*\.url` does NOT match `pushurl`, and `remote\..*url` matches both and
# nothing else git stores under `remote.`. The `insteadof` spelling is git's own -- it lowercases
# the variable name of a subsection key on write, so a case-SENSITIVE `insteadOf` finds nothing.
KEYS = r"^remote\..*url$|^url\..*\.insteadof$"

# There is deliberately NO scheme allow-list. The first version had one -- `http`/`https` only --
# reasoning that `ssh://git@host` is a username and must stay silent, which is true and is handled
# by the SHAPE test below instead. Keeping both was redundant, and the scheme allow-list was the
# weaker of the two: it would have stayed silent about a `gho_` token sitting in an `ssh://` remote,
# which is a secret in a file whether or not the transport uses it. Measured 2026-10-05: git reads
# the userinfo of an `ssh://` URL as the SSH USERNAME and hands the rest to `ssh`, which ignores a
# password -- so it is not a live credential on the wire, and it is still a plaintext secret in
# `.git/config`. Both halves of that are why the row is a finding and the scheme is not consulted.
#
# `ssh://git@github.com` stays silent anyway, because `git` is neither a token prefix nor 20 opaque
# characters. That is the control, and it is in the table.

# What makes a userinfo a CREDENTIAL rather than a username. The first group is a known token
# prefix, which is the same confidence `check_no_secrets.py` uses and for the same reason: an
# unproven heuristic cries wolf and gets switched off. The second is a bare userinfo long and opaque
# enough to be one -- measured over this machine's 35 real remotes, none of which use ANY userinfo
# form, so this arm has never fired on the estate and is the arm most likely to need recalibrating.
TOKEN_PREFIX = re.compile(
    r"^(gh[pousr]_|github_pat_|sk-|sk-ant-|xox[bpas]-|AKIA|glpat-|npm_|dop_v1_|glsa-|ya29\.)"
)
OPAQUE = re.compile(r"^[A-Za-z0-9._~+/=-]{20,}$")


def fingerprint(userinfo: str) -> str:
    """A stable, non-reversible handle for a credential, for a report that must not carry it."""
    return "sha256:" + hashlib.sha256(userinfo.encode("utf-8")).hexdigest()[:12]


def looks_like_a_credential(userinfo: str) -> bool:
    """True when a userinfo is a credential rather than a username.

    `userinfo` is the WHOLE netloc prefix before the host, exactly as `urlsplit` hands it back.
    """
    if not userinfo:
        return False
    return bool(TOKEN_PREFIX.match(userinfo) or OPAQUE.match(userinfo))


def verdict(url: str):
    """(kind, host, userinfo, why) for a URL, or None when it carries no credential.

    Split on the LAST `@`, per RFC 3986 and measured: `https://user:p@ss@host` is user `user`,
    password `p@ss`, host `host`. `urlsplit` agrees, and this states it because the one-line version
    (`url.split("@")`) is the bug this row exists for.
    """
    parts = urlsplit(url)
    netloc = parts.netloc
    if "@" not in netloc:
        # No userinfo at all: the clean https remote, a `file://`, a local path, and the scp form
        # `git@github.com:o/r.git` (which is not a URL, so `urlsplit` leaves netloc empty).
        return None
    userinfo, _, _host = netloc.rpartition("@")
    host = parts.hostname or ""
    if parts.password is not None:
        # A `:` in the userinfo. The password may be EMPTY (`https://user:@host`) and that is still
        # a finding: a credential slot with nothing in it is a shape that gets filled in later.
        return ("userinfo-with-password", host, userinfo, "a password in a remote URL")
    if looks_like_a_credential(userinfo):
        return (
            "userinfo-credential",
            host,
            userinfo,
            "a credential-shaped value where a username belongs",
        )
    return None


def read_urls(root: str):
    """[(key, url)] for every URL-bearing key git holds in this repo, including includes.

    `git config --get-regexp` returns EVERY value of a multi-valued key (measured: a repo with two
    `remote.origin.url` prints both) and follows `include.path` (measured). Exit 1 means NO match,
    which is a normal answer and not an error -- distinguished from a real failure by `returncode`.
    """
    result = subprocess.run(
        ["git", "config", "--get-regexp", KEYS],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"`git config --get-regexp {KEYS}` exited {result.returncode}: "
            f"{result.stderr.strip() or 'no stderr'}"
        )
    out = []
    for line in result.stdout.splitlines():
        key, _, url = line.partition(" ")
        if not url:
            continue
        # An `insteadOf` rewrite carries its credential in the KEY, not the value: the key holds the
        # base URL git rewrites TO, and the value holds the PREFIX it rewrites away from. Reading
        # only the value would judge `https://github.com/` and miss the proxy in front of it.
        # Measured on a scratch repo, and the reason this row exists.
        if re.search(r"\.insteadof$", key, re.IGNORECASE):
            out.append((key, key.split("url.", 1)[-1][: -len(".insteadof")]))
        out.append((key, url))
    return out


def remote_of(key: str) -> str:
    """The remote's NAME, from `remote.<name>.url` / `remote.<name>.pushurl`.

    Sliced off the ENDS rather than by `split(".")[1]`, because git allows a dot in a remote name:
    measured, `remote.my.remote.url` is a legal key and a middle-index split reads it as `my`.
    """
    if not key.startswith("remote."):
        return key
    name = key[len("remote.") :]
    for suffix in (".pushurl", ".url"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def root_for(root=None) -> str:
    """The repo to judge, or a RuntimeError naming why there is not one."""
    probe = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=root or os.getcwd(),
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        raise RuntimeError("not a git repo -- .git/config is this gate's subject and there is none")
    return os.path.realpath(
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root or os.getcwd(),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )


def which_of(key: str) -> str:
    """`url`, `pushurl` or `insteadOf` — which slot of which key the credential was in."""
    lowered = key.lower()
    if lowered.endswith(".pushurl"):
        return "pushurl"
    if lowered.endswith(".insteadof"):
        return "insteadOf"
    return "url"


def label_of(key: str) -> str:
    """What the report prints where a URL would otherwise go — and never the URL itself.

    For a remote, `<name>.<which>`: a name, no credential. For a rewrite, the config KEY with its
    base URL reduced to its SCHEME, HOST and PORT. The key is where the credential lives, so printing
    the key verbatim would print the credential — which is the failure the fingerprint exists to
    avoid, and the test that caught it.
    """
    which = which_of(key)
    if which == "insteadOf":
        base = key.split("url.", 1)[-1][: -len(".insteadof")]
        parts = urlsplit(base)
        netloc = parts.netloc.rpartition("@")[2] or parts.netloc
        return f"{parts.scheme}://{netloc}/  [{which}]"
    return f"{remote_of(key)}.{which}"


def findings_for(root: str, urls=None):
    """[{file, key, label, remote, which, host, kind, why, fingerprint}] for every finding.

    `urls` is accepted rather than read so the caller can tell "no remote URLs at all" from "read
    them and found them all clean" -- a distinction a `for` loop over an empty list erases, and the
    empty-scope sweep is right to refuse a gate that cannot tell them apart.
    """
    out = []
    for key, url in read_urls(root) if urls is None else urls:
        got = verdict(url)
        if got is None:
            continue
        kind, host, userinfo, why = got
        out.append(
            {
                "file": os.path.join(root, ".git", "config"),
                "key": key,
                # `remote.origin.url` -> `origin`, and a pushurl says so, because the two are
                # different remotes for this purpose: one is read, one is written.
                "remote": remote_of(key),
                "which": which_of(key),
                "label": label_of(key),
                "host": host,
                "kind": kind,
                "why": why,
                # Over the WHOLE userinfo, so the two shapes of the same token share a handle and a
                # `user:` prefix cannot be used to tell two credentials apart by eye.
                "fingerprint": fingerprint(userinfo),
            }
        )
    return out


def config_findings(values):
    """Findings for `credential.*.helper` / `http.*.extraheader` values, in `findings_for`'s shape."""
    out = []
    for origin, key, value in values:
        got = judge(key, value)
        if got is None:
            continue
        kind, secret, why = got
        out.append(
            {
                "file": origin,
                "key": label(key),
                "remote": "",
                "which": key.rsplit(".", 1)[-1],
                "label": label(key),
                "host": "",
                "kind": kind,
                "why": why,
                "fingerprint": fingerprint(secret),
            }
        )
    return out


def report(findings) -> str:
    lines = [
        f"✗ {len(findings)} credential(s) in git config -- the value is NOT printed;",
        "  rotate it, then move it to a credential helper that reads a keychain, or an SSH remote:",
        "    git remote set-url origin <url-without-userinfo>",
    ]
    for f in findings:
        lines.append(
            # The KEY, never the URL: for a remote that is a name, and for an `insteadOf` the key IS a
            # URL that can carry the credential -- measured, and caught here by a test that plants one.
            f"  {f['file']} [{f['label']}] {f['host']}: {f['kind']} "
            f"[{f['fingerprint']}] -- {f['why']}"
        )
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=None, help="repo to judge (default: the enclosing one)")
    ap.add_argument("--json", action="store_true", help="findings as JSON on stdout")
    ap.add_argument("--probe", action="store_true", help="prove this gate can go red")
    args = ap.parse_args(argv)
    if args.probe:
        # Split out so neither file crowds the cap; it drives THIS module, never a copy.
        from _credential_urls_probe import probe

        return probe()
    try:
        repo = root_for(args.root)
        urls = read_urls(repo)
        values = read_values(repo)
        bad = findings_for(repo, urls) + config_findings(values)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        # Contract 7: a check that could not read its subject says so and exits 2. "OK" here would be
        # this gate reporting success over nothing.
        err(f"[no_credential_urls] {exc}")
        info("This gate's subject is .git/config. Without one there is nothing to judge.")
        return 2
    if bad:
        if args.json:
            print(json.dumps(bad, indent=2, sort_keys=True))
        else:
            print(report(bad))
        return 1
    common = os.path.realpath(
        subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        or os.path.join(repo, ".git")
    )
    # Applicable only when the REPO's own config holds something: global helpers are still judged
    # above, but they are not this repo's subject (the empty-tree sweep, 2026-10-05).
    if not urls and not any(origin.startswith(common + os.sep) for origin, _, _ in values):
        # A NAMED NON-RUN, not a pass over zero files. Measured, and the sweep is what found it:
        # `check_empty_scope.py` ran this gate over a skeleton repo and read
        # `✓ [no_credential_urls] OK — every git remote URL carries no userinfo` as a gate that
        # reported compliance having inspected nothing -- the exact failure
        # `checks/empty_scope_allow.json` exists to refuse. A repo with no remote at all has no
        # remote URL to carry a credential, and saying so is the honest answer; the excuse file is
        # for a gate that CANNOT answer, and this one can.
        if args.json:
            print("[]")
        else:
            ok(
                f"[no_credential_urls] not applicable — no remote URL here ({len(values)} global value(s) clean)"
            )
        return 0
    if args.json:
        print("[]")
    else:
        ok(
            f"[no_credential_urls] OK — {len(urls)} remote URL(s), {len(values)} helper/header value(s)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
