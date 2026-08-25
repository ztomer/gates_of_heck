#!/usr/bin/env python3
"""Fail when a committed generated artifact disagrees with its generator.

One check for the freshness contract that export_rust_fixtures /
check_fixtures_fresh pioneered: generated files are COMMITTED so diffs are
reviewable, which means they can silently go stale the moment someone edits
the generator without re-running it. This check closes that hole generically:
it runs the generator into a throwaway SANDBOX — never in place, so a stale
or broken generator cannot dirty your tree while you investigate — hashes
what it produced against the committed artifacts, and names every mismatch.

Generator contract (the out-dir convention): the command is run through the
shell with ONE extra argument — the directory to generate into. It must
produce every listed artifact at the SAME relative path under that directory
(parent directories are pre-created for you):

    check_generated_fresh.py --generator 'python3 tools/gen_sprites.py' \
        assets/sprites.json assets/icons/lock.png

    # inside tools/gen_sprites.py, argv[1] is where the outputs go:
    #     out_dir = sys.argv[1]
    #     write(out_dir / "assets/sprites.json", ...)

Exit codes: 0 fresh · 1 stale/mismatched (artifacts named) · 2 precondition
missing with reason (no committed artifact, generator failed or timed out).

--write blesses: copies the freshly generated outputs over the committed
artifacts. Commit that separately from any logic change — a blessing diff
should contain nothing else, because it is unreadable by design.

    check_generated_fresh.py --generator '...' <artifacts...>   # verify
    check_generated_fresh.py --generator '...' <artifacts...> --write
"""

import argparse
import hashlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def generate(generator: str, sandbox: Path, timeout: int):
    """Run the generator with the sandbox as its out-dir argument."""
    cmd = f"{generator} {shlex.quote(str(sandbox))}"
    proc = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return proc


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Artifact-freshness gate: regenerate into a sandbox "
                    "and hash-compare against the committed artifacts.")
    ap.add_argument("--generator", required=True,
                    help="shell command; receives the OUT-DIR as $1")
    ap.add_argument("artifacts", nargs="+",
                    help="committed generated paths, relative to cwd")
    ap.add_argument("--write", action="store_true",
                    help="bless: copy the generated outputs over the "
                         "committed artifacts")
    ap.add_argument("--timeout", type=int, default=120,
                    help="seconds allowed for the generator (default 120)")
    args = ap.parse_args()

    missing = [a for a in args.artifacts if not Path(a).is_file()]
    if missing:
        print("✗ [fresh] precondition missing: committed artifact(s) not "
              "found:", file=sys.stderr)
        for m in missing:
            print(f"    {m}", file=sys.stderr)
        return 2

    sandbox = Path(tempfile.mkdtemp(prefix="goh-fresh-"))
    # Pre-create each artifact's parent directories inside the sandbox: the
    # generator writes to <sandbox>/<artifact-path> and must not have to
    # guess what tree that is.
    for art in args.artifacts:
        (sandbox / art).parent.mkdir(parents=True, exist_ok=True)
    try:
        try:
            proc = generate(args.generator, sandbox, args.timeout)
        except subprocess.TimeoutExpired:
            print(f"✗ [fresh] precondition missing: generator timed out "
                  f"after {args.timeout}s and was killed:\n"
                  f"    {args.generator}", file=sys.stderr)
            return 2
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-600:]
            print(f"✗ [fresh] precondition missing: generator exited "
                  f"{proc.returncode}:\n{tail}", file=sys.stderr)
            return 2

        stale: list[tuple[str, str]] = []
        for art in args.artifacts:
            committed = Path(art)
            produced = sandbox / committed
            if not produced.is_file():
                stale.append((art, "NOT PRODUCED by the generator"))
                continue
            if _sha256(committed) == _sha256(produced):
                continue
            stale.append((
                art,
                f"stale — committed {_sha256(committed)[:12]} vs generated "
                f"{_sha256(produced)[:12]}"))

        if not stale:
            print(f"→ [fresh] OK — {len(args.artifacts)} artifact(s) match "
                  f"a fresh run of the generator")
            return 0

        if args.write:
            print(f"→ [fresh] writing {len(stale)} regenerated artifact(s):")
            for art, why in stale:
                if why.startswith("stale"):
                    shutil.copy2(sandbox / art, art)
                    print(f"    {art}")
                else:
                    print(f"    {art}: {why} — nothing to write")
            return 0

        print(f"✗ [fresh] {len(stale)} artifact(s) disagree with the "
              f"generator:", file=sys.stderr)
        for art, why in stale:
            print(f"    {art}: {why}", file=sys.stderr)
        print("\n  Re-run the generator and commit its output — or, if this\n"
              "  IS the generating commit, pass --write and commit the\n"
              "  blessing on its own.", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
