"""The two NON-URL places `.git/config` holds a credential: `credential.*.helper` and
`http.*.extraheader`. Read by `check_no_credential_urls.py`, whose subject is the same file.

Both were stated out of that checker's scope when it shipped, each measured to carry a token:
`credential.helper` may be multi-valued and any value may be a shell snippet
(`!f() { echo password=<token>; }; f`), and `http.extraheader` carries
`AUTHORIZATION: basic <base64 user:token>` -- the shape `actions/checkout` writes.

THE RULE IS check_no_secrets.py's, not a third variant of it: its PATTERNS (token prefixes, private
key headers, a credential-named key) judge every value. Two shapes those patterns cannot see are
added, because neither is a committed file's shape:

  * a helper's UNQUOTED `password=<value>` / `token=<value>` -- the credential protocol's own
    syntax. A `$VAR` is a reference, not a credential, and stays silent.
  * a header whose NAME carries a credential (`Authorization`, `Proxy-Authorization`, `*-Token`,
    `*Api-Key`, `Cookie`) and whose value is a literal: after an auth scheme (`basic`, `bearer`,
    `token`) the next word is the credential. base64 hides every token prefix, which is why the
    name, not the value, is the evidence.

The value is never printed: a finding carries the same `sha256:` fingerprint the URL rule uses.
"""

from __future__ import annotations

import os
import re
import subprocess

from check_no_secrets import PATTERNS

# git stores section and variable names LOWERCASED (measured: `extraHeader` reads back as
# `extraheader`), and keeps the subsection (the URL between them) as written.
KEYS = r"^credential\..*helper$|^http\..*extraheader$"

HELPER_ASSIGN = re.compile(r"\b(?:password|token)=([^\s;'\"`]+)", re.IGNORECASE)
CREDENTIAL_HEADER = re.compile(
    r"^\s*(?:authorization|proxy-authorization|[\w-]*token|[\w-]*api-?key|cookie)\s*:\s*(.*)$",
    re.IGNORECASE,
)
AUTH_SCHEME = re.compile(r"^(?:basic|bearer|token|digest|negotiate)\s+", re.IGNORECASE)


def _literal(value: str) -> bool:
    """A credential, rather than a reference to one or an empty slot."""
    return bool(value) and not value.startswith("$")


def judge(key: str, value: str):
    """(kind, secret, why) when `value` carries a credential, else None. `secret` is what the
    fingerprint is taken over; it is never printed."""
    for kind, pattern in PATTERNS:
        found = pattern.search(value)
        if found:
            return (kind, found.group(0), f"a {kind} in {key.rsplit('.', 1)[-1]}")
    if key.endswith(".helper"):
        for found in HELPER_ASSIGN.finditer(value):
            if _literal(found.group(1)):
                return (
                    "helper-literal",
                    found.group(1),
                    "a literal password in a credential helper",
                )
        return None
    header = CREDENTIAL_HEADER.match(value)
    if header:
        credential = AUTH_SCHEME.sub("", header.group(1).strip())
        if _literal(credential):
            return ("header-credential", credential, "a literal credential in an HTTP header")
    return None


def read_values(root: str):
    """[(origin, key, value)] for every helper and extra header git resolves in `root`, every value
    of a multi-valued key, includes followed. Exit 1 is git's "no match", not an error."""
    got = subprocess.run(
        ["git", "config", "--show-origin", "--get-regexp", KEYS],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if got.returncode not in (0, 1):
        raise RuntimeError(f"`git config --get-regexp {KEYS}` exited {got.returncode}")
    out = []
    for line in got.stdout.splitlines():
        origin, _, rest = line.partition("\t")
        key, _, value = rest.partition(" ")
        # git prints the repo's own config RELATIVE (`file:.git/config`); a report names a path
        # the reader can open, so it is resolved against the repo.
        path = os.path.realpath(os.path.join(root, origin.removeprefix("file:")))
        out.append((path, key, value))
    return out


def label(key: str) -> str:
    """The key with any URL subsection reduced to scheme://host -- a URL there can carry a
    credential too, and the report must not."""
    section, _, tail = key.partition(".")
    sub, _, var = tail.rpartition(".")
    if not sub:
        return key
    host = re.sub(r"^([a-z]+://)?(?:[^@/]*@)?([^/]*).*$", r"\1\2", sub, flags=re.IGNORECASE)
    return f"{section}.{host}.{var}"
