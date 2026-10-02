"""One lookup against crates.io.

Split out of `check_dep_currency.py` for the file-length cap, and because
NETWORK ACCESS is a distinct concern from deciding what a finding is: this
module can fail, and what it does when it cannot reach is the checker's
decision rather than its own.

`None` means UNREACHED, never "no newer version". That distinction is the
whole reason this is a function rather than an inline expression: a currency
check that treats an unreachable index as a current version converts an
absence of evidence into a clean bill.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

USER_AGENT = "gates_of_heck-dep-currency"
NET_TIMEOUT = 10


def latest_stable(name: str) -> str | None:
    url = f"https://crates.io/api/v1/crates/{name}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=NET_TIMEOUT) as resp:
            doc = json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
    return (doc.get("crate") or {}).get("max_stable_version")


