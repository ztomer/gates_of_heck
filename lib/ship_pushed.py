"""goh ship's release-effect check (GOH.RELEASEUNPUSHED): is the kit's release on the remote?"""

from __future__ import annotations

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # a copy outside any checkout: nothing to rerun
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gitrun

SEMVER = re.compile(r"^v\d+\.\d+\.\d+$")


def _git(repo: Path, *args: str) -> str:
    done = gitrun.run_reader(args, repo=repo, timeout=60)
    return done.stdout.strip() if done.returncode == 0 else ""


def release_unpushed(repo: Path, remote: str, branch: str) -> str:
    """Why the release kit's effect is not on the remote, or "" when it is: HEAD on the tracked
    branch and every version tag at HEAD present remotely. A kit's exit 0 is not its effect
    (GOH.RELEASEUNPUSHED: necrohand's kit committed and tagged, never pushed, and the ship failed
    one step later at verify, far from the cause)."""
    head = _git(repo, "rev-parse", "HEAD")
    tags = [t for t in _git(repo, "tag", "--points-at", "HEAD").splitlines() if SEMVER.match(t)]
    refs = [f"refs/heads/{branch}", *(f"refs/tags/{t}" for t in tags)]
    url = _git(repo, "remote", "get-url", "--push", remote) or remote  # where the kit's push lands
    proc = subprocess.run(["git", "ls-remote", url, *refs], cwd=repo,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          check=False)  # fmt: skip
    if proc.returncode != 0:
        return f"cannot read {remote} (git ls-remote exit {proc.returncode}): {proc.stderr.strip()}"
    have = {ln.split()[1]: ln.split()[0] for ln in proc.stdout.splitlines() if len(ln.split()) == 2}
    gaps = []
    if have.get(refs[0]) != head:
        gaps.append(f"{remote}/{branch} {have.get(refs[0], 'absent')[:8]} != HEAD {head[:8]} "
                    "(release commit unpushed)")  # fmt: skip
    gaps += [f"tag {t} unpushed" for t in tags if f"refs/tags/{t}" not in have]
    if not gaps:
        return ""
    return (
        "the release kit exited 0 but its release is not on the remote: "
        + "; ".join(gaps)
        + f". Fix: make the kit push (append `&& git push {remote} HEAD:refs/heads/{branch} "
        "v{version}` to .gatesrc GOH_SHIP_RELEASE, or set the kit itself to push)"
    )


def after(kit: str, res: dict) -> str:
    """The release step's command: KIT, then this check on the ship's remote and branch."""
    me = [sys.executable, str(Path(__file__).resolve()), res["remote"], res["branch"]]
    return f"( {kit} ) && {shlex.join(me)}"


def main(argv: list[str]) -> int:
    """`ship_pushed.py REMOTE BRANCH`, run in the repo after its release kit: 1 naming the gap."""
    gap = release_unpushed(Path.cwd(), argv[0], argv[1])
    if gap:
        print(f"✗ {gap}", file=sys.stderr)
    return 1 if gap else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
