#!/usr/bin/env python3
"""Run each house checker against a corpus taken from the REAL estate, and require it to go red.

    check_estate_corpus.py              # the sweep
    check_estate_corpus.py --list       # name the corpora without building them
    check_estate_corpus.py --probe      # prove this gate can go red

WHY THIS EXISTS (SUPERSOTA R3). This is our single most repeated failure: a check written against
a fixture invented beside it, green across a dozen cases, and blind to the shape the real estate
actually has. Two, both in one session, both found against a real file rather than by reading:

  * `check_no_empty_assert.py`'s receiver pattern omitted `"`, so every emptiness assert whose
    receiver carried a string literal was skipped. Found by a real site in `storage-server` --
    `assert!(is_metadata(".DS_Store"))`.
  * a consumer's gluetun check read the LAST `image:` in a compose file, so it judged gluetun's
    pin by a different service's image. Found on the live host: 40-odd services per file.

Both fixtures had exactly one service and one image -- the shape the author had imagined. **A
fixture cannot disagree with the assumption that produced it.**

WHAT IS ACTUALLY CHECKABLE HERE, and this is the whole mechanism: for each declared checker, take
a REAL subtree from a REAL consumer repo, copy it (never run in place -- these are other people's
working trees), plant one violation inside it, and require the checker to go RED and NAME THE
PLANTED FILE. The plant goes into a real file of the real language, so the surrounding real code
is what the checker has to see through -- which is the entire difference from a fixture.

THE DIFFERENTIAL, which is why this is not just "plant and check". The same plant is ALSO run
against a MINIMAL one-file corpus. Three outcomes, all meaningful:

  real red, minimal red   the checker is right, and the fixture was not misleading anyone.
  real red, minimal GREEN the checker needed the real corpus to see it. Fine -- and reported,
                           because it means the fixture's proof was weaker than it looked.
  real GREEN              THE FAILURE. Either the shape is a blind spot, or -- the shape R3 is
                           about -- the author's fixture caught something the real estate does
                           not have, and the gate has been reading green on reality.

A corpus below `MIN_FILES` is REFUSED, never used: a "real corpus" that quietly degraded to one
file is the failure this gate exists to catch, reproduced inside the gate.

WHAT THIS IS NOT. It does not decide which checkers deserve an entry -- that judgement is a
person's, made once per checker and recorded in `ESTATE` with the reason. It does not prove a
checker CORRECT, only that it still refuses a violation in the shape the estate really has; that
is the claim R3 makes and the only one that is checkable without a live system. The cross-repo
survey that measures coverage (`games/game_asset_factory/tools/check_gate_calibration.py`) is a
different question and stays there.
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
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ordered_pool import in_order  # noqa: E402
import _estate_cache  # noqa: E402
from _gitutil import foreign_repo_env, listed_files, scratch_git  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tui.lib import err, info, ok, warn  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
# The gates checkout whose `goh.sh` runs the native checks: GOH_DIR when the caller says (a copy of
# this file in another tree, as the empty-scope sweep makes), else the checkout this file is in.
GOH = os.environ.get("GOH_DIR") or os.path.dirname(HERE)
REPO = os.path.dirname(HERE)

# A corpus below this is not an estate, it is a fixture wearing a real repo's name. Measured: the
# smallest subtree here is 7 files, so 5 is loose but not decorative -- the point is that a
# corpus which lost its bulk REFUSES instead of quietly proving less.
MIN_FILES = 5

# ...and the planted file must be a real file with this many lines ALREADY IN IT. A plant that
# lands in a two-line stub has not been judged by the estate; it has been judged by the estate's
# FILENAME. Reported on every pass, so "real corpus" is a measurement rather than a claim.
MIN_TARGET_LINES = 10

# Per entry: the checker, where its corpus really lives, the subtree to take, the file extension
# the plant goes into, the plant itself, extra argv, and WHY this corpus for this checker.
ESTATE = (
    {
        "checker": "empty-assert",
        "root": "~/Projects/servers/storage-server",
        "scope": ("pool-reaper-rs",),
        "ext": ".rs",
        # THE EXACT SHAPE of the 2026-10-02 miss: a string literal INSIDE the receiver. The
        # message form (`assert!(v.is_empty(), "msg")`) is the OTHER shape clippy is silent on and
        # is NOT this bug -- planting it tested something else and reported green over it.
        "plant": (
            "\nfn corpus_probe(dir: &Path) {\n"
            '    assert!(std::fs::read_to_string(dir.join("VERSION")).unwrap().is_empty());\n'
            "}\n"
        ),
        "args": (),
        "why": "THE R3 case, replayed in the shape it failed in: a receiver carrying a string "
        "literal, which the first pattern's character class omitted. Recorded in "
        "docs/SUPERSOTA.md R3 as found at a real site in this repo on 2026-10-02.",
    },
    {
        "checker": "unreaped-spawn",
        "root": "~/Projects/servers/media_server",
        "scope": ("crates",),
        "ext": ".rs",
        # THE INCIDENT, IN ITS OWN WORDS. Not "a spawn and a kill": the four lines that can panic
        # BETWEEN the spawn and the explicit `child.kill(); child.wait()`, because that ORDER is the
        # defect. The crate is clean under this gate today -- the fix is in it, a `ReapOnDrop` guard
        # -- so the plant's red is its own, and the file it lands in is the file the bug lived in.
        "plant": (
            "\n#[cfg(test)]\nmod corpus_probe {\n"
            "    use std::process::{Command, Stdio};\n"
            "    #[test]\n"
            "    fn reap_after_the_panic() {\n"
            '        let mut child = Command::new(env!("CARGO_BIN_EXE_archive_torznab"))\n'
            '            .args(["--bind", "127.0.0.1:0"])\n'
            "            .stderr(Stdio::piped())\n"
            "            .spawn()\n"
            '            .expect("the binary runs");\n'
            '        let banner = child.stderr.take().expect("piped stderr");\n'
            '        let port = banner.parse::<u16>().unwrap_or_else(|| panic!("no port: {banner:?}"));\n'
            '        assert_ne!(port, 0, "the kernel assigns a real port");\n'
            "        let _ = child.kill();\n"
            "        let _ = child.wait();\n"
            "        assert!(port > 0);\n"
            "    }\n"
            "}\n"
        ),
        "args": (),
        "why": "media_server's whole crates/ tree — the scope matcher reads the TOP directory, and "
        "this repo keeps its crates one level down, so naming the crate found 0 files and the floor "
        "refused the corpus rather than letting a fixture wear its name. Nine live "
        "orphans from that one test, each holding the cargo build lock, and the only observable was "
        "a `cargo test` printing nothing at all. The plant carries the ORDER, because the reap "
        "EXISTED in the real file — a plant that only added a missing kill would have tested a rule "
        "this checker does not have, and reported green over the defect it was written for.",
    },
    {
        "checker": "no-allow",
        "root": "~/Projects/routines",
        "scope": (".",),
        "ext": ".rs",
        "plant": "\n#[allow(dead_code)]\nfn corpus_probe() {}\n",
        "args": (),
        "why": "a real Rust workspace, clean under this gate today, so the plant's red is its own. "
        "The `#[cfg_attr(..., allow(...))]` form this tree also uses is NOT the plant -- one shape "
        "per entry, and a second one belongs in a second entry where its own red is measured.",
    },
    {
        "checker": "length",
        "root": "~/Projects/servers/server-template",
        "scope": (".",),
        "ext": ".sh",
        "plant": "\n" + "".join(f"# corpus line {i}\n" for i in range(340)),
        # 300 is above this estate's longest source file (117 lines) and far below the plant, so
        # the ceiling is crossed by the PLANT and by nothing else. Measured, not guessed: at
        # --max 10 the corpus is red before the plant lands, and the run refuses on that.
        "args": ("--max", "300"),
        "why": "real shell with the line-continuation and heredoc conventions this repo writes, "
        "so the ceiling is judged against text the estate produces.",
    },
    {
        "checker": "markers",
        "root": "~/Projects/monitor",
        "scope": (".",),
        "ext": ".py",
        "plant": "\n<<<<<<< HEAD\nlet value = 1\n=======\nlet value = 2\n>>>>>>> feature\n",
        "args": (),
        "why": "a repo with 395 tracked files, which is the shape the gluetun bug needed: one "
        "planted marker must be found without the scan giving up on the rest of the tree. Monitor "
        "is also the repo whose own emoji checker was retired in R6, so it is the estate's standing "
        "example of a retired local copy.",
    },
    {
        "checker": "md-links",
        "root": "~/Projects/app_updates",
        "scope": (".",),
        "ext": ".md",
        "plant": "\nSee [the corpus probe](./ZZCorpusProbeDoesNotExist.md).\n",
        "args": (),
        "why": "the WHOLE tree, because a subtree copy breaks every link that escapes it -- the "
        "first version of this entry scoped `docs/` and reported two 'findings' that were "
        "artefacts of the copy. Real Markdown with the link forms this repo uses, so a broken link "
        "is judged beside them.",
    },
    {
        "checker": "home-paths",
        "root": "~/Projects/mediaremote-adapter",
        "scope": (".",),
        "ext": ".m",
        "plant": "\ncd /Users/someone/Projects/thing && ./build\n",
        "args": (),
        "why": "the one repo in this estate clean under this gate today, which is exactly what an "
        "attributable corpus needs. Measured, not assumed: eight other candidate repos are red "
        "under it before any plant lands.",
    },
    {
        "checker": "claim-derivation",
        "root": "~/Projects/games/game_asset_factory",
        "scope": (".",),
        "ext": ".md",
        # THE ESTATE'S OWN SHAPE, in the repo that carries the inventory this gate
        # exists for: `README.md` said "86 Python files, 64 test files and 1059
        # tests" against 101 / 91 / 1732, and the file's own stated remedy ("names
        # the date it was counted") held for 26 days. The plant is a claim the
        # author MARKED, because that is the only form this checker reads -- an
        # unmarked number is prose by design, and planting one would test a rule
        # this checker deliberately does not have.
        "plant": ("\n<!-- corpus probe -->\n\nThe suite is `9` lines in `README.md`.\n"),
        "args": (),
        "why": "the whole tree, 294 files, and CLEAN under this checker today — measured, "
        "not assumed: six sibling repos were measured clean too, so this is a choice of "
        "where the defect was found rather than a claim that only one repo is clean. The "
        "plant lands in the first real markdown file git lists (a CHANGELOG), which is "
        "where a repo writes 'the file is now N lines' anyway, and names a real file so "
        "the finding is about a resolvable target rather than a missing path.",
    },
)


def corpus_files(root, scope):
    """Tracked files under any of `scope`, repo-relative. The estate, not a directory listing."""
    out = []
    for rel in listed_files(root, staged=False):
        top = rel.split("/")[0] if "/" in rel else ""
        if scope == (".",) or top in scope:
            out.append(rel)
    return sorted(out)


def plant_in(files, ext, text):
    """The first real file of this language, and its path. Appending puts the violation inside real,
    structurally non-trivial code instead of in a file invented for it."""
    for rel in files:
        if os.path.splitext(rel)[1] == ext:
            return rel, text
    return None, text


_IDENTITY = ("-c", "user.email=corpus@example.invalid", "-c", "user.name=corpus")


def materialise(source, files, dest):
    """Copy the real subtree into a scratch repo. Nothing ever runs in another working tree."""
    os.makedirs(dest)
    for rel in files:
        src = os.path.join(source, rel)
        dst = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(dst) or dest, exist_ok=True)
        shutil.copyfile(src, dst)
    scratch_git(dest)  # one empty .git copied, not init + config x2 (checks/_gitutil.py)
    for args in (("add", "-A"), (*_IDENTITY, "commit", "-qm", "corpus")):
        subprocess.run(
            ["git", "-C", dest, *args], capture_output=True, check=False, env=foreign_repo_env()
        )


def run_checker(checker, root, args):
    """(rc, output). The checker under test, exactly as a consumer runs it: a house check by its
    `goh.sh` name (the Python checkers it named are retired, Phase N3), a `.py` by path -- the
    probe's own planted checkers, which cannot be anything else."""
    argv = (
        [sys.executable, os.path.join(HERE, checker), *args]
        if checker.endswith(".py")
        else ["bash", os.path.join(GOH, "gates", "goh.sh"), checker, *args]
    )
    result = subprocess.run(
        argv,
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env=foreign_repo_env(),
    )
    return result.returncode, (result.stdout + result.stderr)


def judge(entry, bad):
    """One entry, both corpora. Returns 'verified' | 'unavailable' | 'blind'."""
    source = os.path.expanduser(entry["root"])
    if not os.path.isdir(source):
        warn(f"{entry['checker']}: {entry['root']} is not on this machine — NOT verified")
        return "unavailable"
    files = corpus_files(source, entry["scope"])
    if len(files) < MIN_FILES:
        where = "/".join(entry["scope"]) if entry["scope"] != (".",) else ""
        err(
            f"{entry['checker']}: the corpus at {entry['root']}/{where} has {len(files)} file(s), "
            f"below the floor of {MIN_FILES}. Refusing to count a fixture as an estate."
        )
        bad.append(entry["checker"])
        return "blind"
    target, _ = plant_in(files, entry["ext"], entry["plant"])
    if target is None:
        err(f"{entry['checker']}: no tracked {entry['ext']} file under that corpus to plant into")
        bad.append(entry["checker"])
        return "blind"
    existing = sum(
        1 for _ in open(os.path.join(source, target), encoding="utf-8", errors="replace")
    )
    if existing < MIN_TARGET_LINES:
        err(
            f"{entry['checker']}: the plant would land in {target}, which holds {existing} line(s) "
            f"of the estate -- below the floor of {MIN_TARGET_LINES}. A violation in a stub is "
            f"judged by the estate's FILENAME."
        )
        bad.append(entry["checker"])
        return "blind"

    ident = _estate_cache.current_identity(os.path.abspath(__file__), GOH)
    key = _estate_cache.entry_key(ident, entry, source, files)
    if seen := _estate_cache.lookup(key):  # nothing it depends on moved (checks/_estate_cache.py)
        ok(f"{seen['line']} -- verified {int(time.time() - seen['at'])}s ago, inputs unchanged")
        return "verified"
    with tempfile.TemporaryDirectory(prefix="goh-estate.") as td:
        real = os.path.join(td, "real")
        minimal = os.path.join(td, "minimal")
        materialise(source, files, real)
        materialise(source, [target], minimal)

        # BEFORE the plant: the corpus must be CLEAN under this checker. Without this the sweep
        # cannot tell "the plant was refused" from "something else in the estate was already
        # refused, and the output happened to mention the file" -- a red that is not attributable
        # to what caused it. Found by replaying the 2026-10-02 receiver bug: the sweep reported
        # 6/6 green while the checker was blind to the plant, because the corpus carried a finding
        # of its own and the output named the file either way.
        pristine_rc, pristine_out = run_checker(entry["checker"], real, entry["args"])

        for corpus in (real, minimal):
            with open(os.path.join(corpus, target), "a", encoding="utf-8") as handle:
                handle.write(entry["plant"])
        real_rc, real_out = run_checker(entry["checker"], real, entry["args"])
        min_rc, _ = run_checker(entry["checker"], minimal, entry["args"])

    named = target in real_out
    if pristine_rc != 0:
        err(
            f"{entry['checker']}: the corpus at {entry['root']} is NOT clean under this checker "
            f"(exit {pristine_rc}), so a plant's verdict there would not be attributable to the "
            f"plant. First finding:"
        )
        for line in [ln for ln in pristine_out.strip().splitlines() if ln.strip()][:3]:
            info(f"    {line}")
        bad.append(entry["checker"])
        return "blind"
    if real_rc != 1 or not named:
        err(
            f"{entry['checker']}: a violation planted in {len(files)} real file(s) from "
            f"{entry['root']} was NOT caught"
            + (f" (exit {real_rc})" if real_rc != 1 else " (exit 1, but it did not name the file)")
        )
        for line in real_out.strip().splitlines()[:6]:
            info(f"    {line}")
        bad.append(entry["checker"])
        return "blind"
    if min_rc != 1:
        # Not a failure. Worth saying out loud: the fixture would have been a WEAKER proof than
        # the corpus, which is the opposite of the R3 failure and just as invisible.
        info(
            f"{entry['checker']}: caught in the real corpus but NOT in a one-file fixture — the "
            f"estate was load-bearing"
        )
    line = (
        f"{entry['checker']}: red on a plant inside {target} ({existing} lines of real code, "
        f"{len(files)} real file(s) from {os.path.basename(entry['root'])})"
        + ("" if min_rc == 1 else " -- and GREEN on the bare one-file fixture")
    )
    ok(line)
    _estate_cache.record(key, line)
    return "verified"


def _judged(entry, bad):
    mine = []
    state = judge(entry, mine)
    bad.extend(mine)  # list.extend is atomic under the GIL; order is fixed by the replay below
    return state


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="name the corpora without building them")
    ap.add_argument("--probe", action="store_true", help="prove this gate can go red")
    args = ap.parse_args(argv)
    if args.probe:
        return probe()

    if args.list:
        for entry in ESTATE:
            info(f"{entry['checker']:32} {entry['root']}")
            info(f"{'':32} {entry['why']}")
        return 0

    bad = []
    states = in_order(lambda entry: _judged(entry, bad), ESTATE)
    verified = states.count("verified")
    unavailable = states.count("unavailable")
    if bad:
        err(f"{len(bad)} checker(s) did not refuse a violation planted in the real estate.")
        info("A checker that only works on the shape its author imagined is not a gate, and the")
        info("fixture beside it is the reason: a fixture cannot disagree with its own assumption.")
        return 1
    ok(
        f"{verified}/{len(ESTATE)} checker(s) went red on a plant inside real estate corpora"
        + (f"; {unavailable} not on this machine, named above" if unavailable else "")
    )
    return 0


def probe():
    """Three cases: a real estate that is green, a checker that CANNOT fail, and a corpus that
    degraded to a fixture. The second is the whole point of this file."""

    saved, home, blind_dir = ESTATE, HERE, ""
    bad = 0
    try:
        # A checker that always passes: a sweep blind to it reports coverage it never measured.
        blind_dir = tempfile.mkdtemp(prefix="goh-estate-blind.")
        blind = os.path.join(blind_dir, "check_cannot_fail.py")
        with open(blind, "w", encoding="utf-8") as handle:
            handle.write('"""Green whatever the tree says."""\nprint("clean")\n')
        globals()["HERE"] = os.path.dirname(blind)
        entry = {
            "checker": "check_cannot_fail.py",
            "root": saved[0]["root"],
            "scope": saved[0]["scope"],
            "ext": saved[0]["ext"],
            "plant": saved[0]["plant"],
            "args": (),
            "why": "probe",
        }
        findings = []
        state = judge(entry, findings)
        if state == "blind" and "check_cannot_fail.py" in findings:
            ok("probe: a checker that cannot fail is caught")
        else:
            err(
                f"probe: a checker that always exits 0 was reported as {state} — the sweep is blind"
            )
            bad += 1

        # A corpus below the floor must be REFUSED, not used: a real repo's name on one file is
        # the failure this gate exists to catch, reproduced inside the gate.
        with tempfile.TemporaryDirectory(prefix="goh-estate.") as td:
            thin = os.path.join(td, "thin")
            os.makedirs(os.path.join(thin, "src"))
            with open(os.path.join(thin, "src", "lib.rs"), "w", encoding="utf-8") as handle:
                handle.write("fn main() {}\n")
            for args in (
                ("init", "-q"),
                ("config", "user.email", "c@e.invalid"),
                ("config", "user.name", "c"),
                ("add", "-A"),
            ):
                subprocess.run(
                    ["git", "-C", thin, *args], capture_output=True, env=foreign_repo_env()
                )
            findings = []
            judge(
                {**entry, "checker": "check_thin_corpus.py", "root": thin, "scope": ("src",)},
                findings,
            )
            if findings:
                ok("probe: a corpus below the floor is refused, not used")
            else:
                err("probe: a one-file 'estate' was accepted — the gate can prove a fixture")
                bad += 1

        globals()["HERE"] = home
        if judge(saved[0], []) != "verified":
            err("probe: the first real estate entry did not verify — the sweep is broken")
            bad += 1
        else:
            ok("probe: a real estate entry verifies end to end")
    finally:
        globals().update(ESTATE=saved, HERE=home)
        shutil.rmtree(blind_dir, ignore_errors=True)  # it leaked once per probe run (2026-10-06)

    if bad:
        err(f"check_estate_corpus --probe: {bad} case(s) wrong")
        return 1
    ok("check_estate_corpus --probe: a checker that cannot fail, and a corpus that is a fixture")
    return 0


if __name__ == "__main__":
    sys.exit(main())
