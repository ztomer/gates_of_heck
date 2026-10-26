#!/usr/bin/env python3
"""actions_sweep.py -- keep GitHub Actions storage from growing without anyone remembering (G14).

For every NON-ARCHIVED repository of an owner:

  * artifacts older than --artifact-days (default 14) are deleted;
  * caches are deleted when their ref is not the default branch (a merged or closed branch, a tag,
    a pull request's merge ref) or when they were last used more than --cache-days (default 7)
    ago; a default-branch cache in use stays, because it is what keeps CI fast;
  * the repository's artifact-and-log retention is set to --artifact-days when it is HIGHER, so a
    repo created tomorrow is covered by the same run that covers today's (GitHub's default is 90
    days, and the sweep above only ever catches what already exists).

    python3 scripts/actions_sweep.py             # dry run: says what it would do, changes nothing
    python3 scripts/actions_sweep.py --apply     # does it (the launchd job runs this daily)

Bounded: every `gh` call has a timeout (--timeout, default 60 s). One repository's failure is
reported and does not stop the rest; the exit is 1 if any repository failed, 0 otherwise. Output is
only what was done -- a quiet run is one line.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tui.lib import err, info, ok, step  # noqa: E402

DEFAULT_OWNER = "ztomer"
ARTIFACT_DAYS = 14
CACHE_DAYS = 7
GH_TIMEOUT = 60.0
MB = 1_000_000


class GhError(RuntimeError):
    """A gh call that failed or timed out, with the command and gh's own words."""


def gh(*args: str, timeout: float) -> str:
    """Run `gh ARGS` and return stdout.

    Stdin is /dev/null (a gh that asks a question would hang an unattended job) and the call is
    bounded: a hung call is a GhError naming the command, never a stuck sweep.
    """
    cmd = ["gh", *args]
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise GhError(f"{' '.join(cmd)}: timed out after {timeout:g}s") from exc
    except OSError as exc:
        raise GhError(f"{' '.join(cmd)}: {exc}") from exc
    if out.returncode != 0:
        raise GhError(f"{' '.join(cmd)}: {out.stderr.strip() or 'exit ' + str(out.returncode)}")
    return out.stdout


def decode_pages(raw: str, key: str) -> list[dict]:
    """Every item under `key` across the pages of a `gh api --paginate` read.

    gh prints one JSON object per page, concatenated with nothing between them, which
    `json.loads` refuses; the decoder is walked object by object.
    """
    dec, i, items = json.JSONDecoder(), 0, []
    while True:
        while i < len(raw) and raw[i].isspace():
            i += 1
        if i >= len(raw):
            return items
        obj, i = dec.raw_decode(raw, i)
        items += obj.get(key, [])


def pages(path: str, key: str, timeout: float) -> list[dict]:
    sep = "&" if "?" in path else "?"
    return decode_pages(gh("api", "--paginate", f"{path}{sep}per_page=100", timeout=timeout), key)


def ts(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


# ---- the policy: pure, so every case is a one-line test ------------------------------------------


def stale_artifacts(artifacts: list[dict], now: dt.datetime, days: int) -> list[dict]:
    """Artifacts older than `days`. An EXPIRED one is already gone (GitHub keeps its row, not its
    bytes), so deleting it again frees nothing and is not counted."""
    cutoff = dt.timedelta(days=days)
    return [a for a in artifacts if not a.get("expired") and now - ts(a["created_at"]) > cutoff]


def stale_caches(caches: list[dict], now: dt.datetime, days: int, default_ref: str) -> list[dict]:
    """Caches off the default branch, or on it but unused for more than `days`."""
    cutoff = dt.timedelta(days=days)
    return [
        c for c in caches if c["ref"] != default_ref or now - ts(c["last_accessed_at"]) > cutoff
    ]


def retention_to_set(current_days: int | None, wanted: int) -> int | None:
    """The days to PUT, or None when retention is already at or below `wanted` (never raised)."""
    return wanted if current_days is not None and current_days > wanted else None


@dataclass
class Tally:
    artifacts: int = 0
    artifact_bytes: int = 0
    caches: int = 0
    cache_bytes: int = 0
    retention: int = 0
    failed: list[str] = field(default_factory=list)

    def acted(self) -> bool:
        return bool(self.artifacts or self.caches or self.retention)


def sweep_repo(
    name: str, default_ref: str, a: argparse.Namespace, now: dt.datetime, tally: Tally
) -> None:
    """One repository. Everything is read first, then acted on; the tally counts each delete as it
    happens (after its gh call returned), so a failure half-way leaves the tally true."""
    t = a.timeout
    arts = stale_artifacts(
        pages(f"repos/{name}/actions/artifacts", "artifacts", t), now, a.artifact_days
    )
    caches = stale_caches(
        pages(f"repos/{name}/actions/caches", "actions_caches", t), now, a.cache_days, default_ref
    )
    ret_path = f"repos/{name}/actions/permissions/artifact-and-log-retention"
    current = json.loads(gh("api", ret_path, timeout=t) or "{}").get("days")
    new_days = retention_to_set(current, a.artifact_days)

    parts: list[str] = []
    for x in arts:
        if a.apply:
            gh("api", "-X", "DELETE", f"repos/{name}/actions/artifacts/{x['id']}", timeout=t)
        tally.artifacts += 1
        tally.artifact_bytes += x["size_in_bytes"]
    if arts:
        parts.append(f"{len(arts)} artifacts ({sum(x['size_in_bytes'] for x in arts) / MB:.0f} MB)")
    for c in caches:
        if a.apply:
            gh("api", "-X", "DELETE", f"repos/{name}/actions/caches/{c['id']}", timeout=t)
        tally.caches += 1
        tally.cache_bytes += c["size_in_bytes"]
    if caches:
        parts.append(
            f"{len(caches)} caches ({sum(c['size_in_bytes'] for c in caches) / MB:.0f} MB)"
        )
    if new_days is not None:
        if a.apply:
            gh("api", "-X", "PUT", ret_path, "-F", f"days={new_days}", timeout=t)
        tally.retention += 1
        parts.append(f"retention {current} -> {new_days} days")
    if parts:
        step(f"{name}: {', '.join(parts)}")


def list_repos(owner: str, timeout: float) -> list[dict]:
    raw = gh(
        "repo",
        "list",
        owner,
        "--limit",
        "1000",
        "--json",
        "nameWithOwner,isArchived,defaultBranchRef",
        timeout=timeout,
    )
    return json.loads(raw)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--owner", default=os.environ.get("GOH_ACTIONS_SWEEP_OWNER", DEFAULT_OWNER))
    ap.add_argument("--artifact-days", type=int, default=ARTIFACT_DAYS)
    ap.add_argument("--cache-days", type=int, default=CACHE_DAYS)
    ap.add_argument("--timeout", type=float, default=GH_TIMEOUT, help="seconds per gh call")
    ap.add_argument("--apply", action="store_true", help="act; without it nothing is changed")
    a = ap.parse_args(argv)

    now = dt.datetime.now(dt.UTC)
    stamp = now.strftime("%Y-%m-%dT%H:%MZ")
    try:
        repos = list_repos(a.owner, a.timeout)
    except (GhError, json.JSONDecodeError) as exc:
        err(f"{stamp} actions-sweep: cannot list {a.owner}'s repositories: {exc}")
        return 1

    tally = Tally()
    swept = 0
    for r in repos:
        if r["isArchived"]:
            continue
        name = r["nameWithOwner"]
        default_ref = f"refs/heads/{(r.get('defaultBranchRef') or {}).get('name') or 'main'}"
        swept += 1
        try:
            sweep_repo(name, default_ref, a, now, tally)
        except (GhError, json.JSONDecodeError, KeyError, ValueError) as exc:
            tally.failed.append(name)
            err(f"{name}: {exc}")

    verb = "deleted" if a.apply else "would delete"
    rverb = "set" if a.apply else "would set"
    summary = (
        f"{stamp} actions-sweep: {swept} repos, {verb} {tally.artifacts} artifacts "
        f"({tally.artifact_bytes / MB:.0f} MB) and {tally.caches} caches ({tally.cache_bytes / MB:.0f} MB), "
        f"{rverb} retention on {tally.retention}"
    )
    if tally.failed:
        err(f"{summary}; {len(tally.failed)} failed: {', '.join(tally.failed)}")
        return 1
    if tally.acted():
        ok(summary)
    else:
        info(f"{stamp} actions-sweep: {swept} repos, nothing to do")
    return 0


if __name__ == "__main__":
    sys.exit(main())
