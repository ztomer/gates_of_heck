"""`check_estate_corpus.py`'s ESTATE table (split out by concern, GOH.CAPHEADROOM): per checker,
the real corpus it is judged on and the plant that must turn it red. Pinned by
tests/test_estate_corpus.py.

`check_estate_corpus.py`'s ESTATE table, split out by concern (GOH.CAPHEADROOM): per checker, the
real corpus it is judged on, the plant that must turn it red, and why that corpus. Re-exported as
`check_estate_corpus.ESTATE`. Pinned by `test_estate_corpus.py`.
"""

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
        "root": "store:server-crates",
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
            "        let port = banner.parse::<u16>()"
            '.unwrap_or_else(|| panic!("no port: {banner:?}"));\n'
            '        assert_ne!(port, 0, "the kernel assigns a real port");\n'
            "        let _ = child.kill();\n"
            "        let _ = child.wait();\n"
            "        assert!(port > 0);\n"
            "    }\n"
            "}\n"
        ),
        "args": (),
        "why": "a private server workspace's whole crates/ tree — the scope matcher reads the "
        "TOP directory, and this repo keeps its crates one level down, so naming the crate found 0 "
        "files and the floor refused the corpus rather than let a fixture wear its name. Nine live "
        "orphans from that one test, each holding the cargo build lock, and the only observable "
        "was a `cargo test` printing nothing at all. The plant carries the ORDER, because the reap "
        "EXISTED in the real file — a plant that only added a missing kill would have tested a "
        "rule this checker does not have, and reported green over the defect it was written for.",
    },
    {
        "checker": "no-allow",
        "root": "store:rust-app",
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
        "is also the repo whose own emoji checker was retired in R6, so it is the estate's "
        "standing example of a retired local copy.",
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
        "ext": ".swift",
        "plant": "\ncd /Users/someone/Projects/thing && ./build\n",
        "args": (),
        "why": "the one repo in this estate clean under this gate today, which is exactly what an "
        "attributable corpus needs. Measured, not assumed: eight other candidate repos are red "
        "under it before any plant lands. The plant lands in Swift since 2026-10-24: the repo "
        "became a Swift package over a VENDORED Objective-C core, every tracked .m is under "
        "`Vendor/`, which its GOH_EXCLUDE exempts, and the corpus went blind ('no tracked .m "
        "file') on the extension rather than on the gate.",
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
