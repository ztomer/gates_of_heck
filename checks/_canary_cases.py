#!/usr/bin/env python3
"""The FIXTURES proving each shared gate can fail -- one case per gate in checks/, or a reason.

They live BESIDE the gates (moved from game_asset_factory/tools/canary_cases.py, 2026-10-19).
There the table demanded a case for every gate here, so each new gate landed, ran in every repo,
and turned GAF's next unrelated push red until someone there wrote its case -- at least eight
times from 2026-10-01 on, the last check_ci_local_parity.py (v0.33.0). A registry in another repo
from the set it enumerates can only agree with it after the fact. Here, tests/test_canary.py
refuses a gate without its case in the commit that adds it, and runs every case. GAF's
tools/check_gate_canary.py still runs them from a consumer's vantage, because that is the prover
the calibration registry cites.

Not named check_*: check_empty_scope requires every check_* to FAIL over an empty tree, and a
table of fixtures has no main() to fail with. Comments below saying "here" or "this table" were
written while it lived in GAF; the history they record is unchanged by the move.

A case is (label, gate script, argv, violating tree, clean tree) and is worthless unless BOTH
directions hold: the violating tree must fail, or the gate does not bite; the clean tree must
pass, or the gate is noisy. One direction alone is the failure mode that lets a broken gate look
proven -- see {root} in _canary.build_repo for why a fixture may need its own root spelled out.
"""

EMOJI = chr(0x1F680)
CONFLICT = "<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n"
# An AWS access key ID SHAPE -- the gate matches `AKIA` + 16 upper/digits, and an
# `aws_secret_access_key = ...` line alone matches nothing, which is how the first version of
# this fixture certified the gate as broken when the fixture was.
# secret-ok: a documented AWS example value, not a credential; it exists to be detected here.
SECRET = "AKIA" + "IOSFODNN7EXAMPLE\n"

# (label, script, argv, {path: violating content}, {path: clean content})
# Paths matter as much as contents -- see the scope warning above.
CASES = [
    (
        "workspace members inherit lints",
        "check_lints_optin.py",
        [],
        {
            "Cargo.toml": '[workspace]\nmembers=["a"]\n[workspace.lints.rust]\nunsafe_code="forbid"\n',
            "a/Cargo.toml": '[package]\nname="a"\n',
        },
        {
            "Cargo.toml": '[workspace]\nmembers=["a"]\n[workspace.lints.rust]\nunsafe_code="forbid"\n',
            "a/Cargo.toml": '[package]\nname="a"\n[lints]\nworkspace=true\n',
        },
    ),
    (
        "line exclusions retain a ceiling",
        "check_exclusion_has_ceiling.py",
        ["--max", "10", "--line-exclude", "big.py", "--baseline", "base.json"],
        {"big.py": "x=1\n" * 12, "base.json": "{}"},
        {"big.py": "x=1\n" * 12, "base.json": '{"big.py":12}'},
    ),
    (
        "skill corpus has valid frontmatter",
        "check_skills_corpus.py",
        ["--root", ".", "--min-skills", "1"],
        {"sample/SKILL.md": "# Sample\nMissing frontmatter.\n"},
        {"sample/SKILL.md": "---\nname: sample\ndescription: Example skill\n---\n# Sample\n"},
    ),
    (
        "no disallowed emoji",
        "check_no_emoji.py",
        [],
        {"doc.md": f"decorative {EMOJI}\n"},
        {"doc.md": "plain text, an arrow is fine: -> ok\n"},
    ),
    (
        "no conflict markers",
        "check_no_conflict_markers.py",
        [],
        {"src.txt": CONFLICT},
        {"src.txt": "resolved content\n"},
    ),
    (
        # The shape clippy is SILENT on: an emptiness assert that carries a message. Measured
        # against clippy 1.99.0 by the gate itself, `assert!(v.is_empty(), "msg")` is the form
        # people write on purpose and clippy reports nothing on it, which is the whole reason the
        # checker exists. It landed in the shared layer with no case here, so `uncanaried()`
        # reported it as "declared and nothing reaches it" -- its own docstring's reachability
        # class, pointed at itself. Fixturable in two files, so a case rather than an exemption.
        "emptiness asserts that carry a message",
        "check_no_empty_assert.py",
        [],
        {
            "Cargo.toml": '[package]\nname = "a"\n',
            "src/lib.rs": 'pub fn drained(v: &Vec<u8>) {\n    assert!(v.is_empty(), "buffer drained");\n}\n',
        },
        {
            "Cargo.toml": '[package]\nname = "a"\n',
            "src/lib.rs": "pub fn drained(v: &Vec<u8>) -> bool {\n    v.is_empty()\n}\n",
        },
    ),
    (
        "file length cap",
        "check_file_length.py",
        ["--max", "10"],
        {"big.py": "x = 1\n" * 40},
        {"big.py": "x = 1\n" * 3},
    ),
    (
        "no committed secrets",
        "check_no_secrets.py",
        [],
        {"conf.txt": SECRET},
        {"conf.txt": "nothing sensitive here\n"},
    ),
    # Landed in the shared layer 2026-09-19 and was red in the calibration ratchet the same day:
    # the CASE is what makes a new gate arrive proven rather than merely declared.
    (
        "no hard-coded home paths",
        "check_no_home_paths.py",
        [],
        {"run.sh": "cd /Users/someone/Projects/thing && ./build\n"},
        {"run.sh": 'cd "${THING_DIR:-$(dirname "$0")}" && ./build\n'},
    ),
    # Landed 2026-09-23. The command name is split because that gate reads this file too.
    (
        "no process kill by name",
        "check_no_kill_by_name.py",
        [],
        {"clean.sh": f'{"p" + "kill"} -9 -f "camoufox -no-remote" || true\n'},
        {"clean.sh": f'{"p" + "kill"} -TERM -P "$$" || true\n'},
    ),
    # src/, not the root: the gate scopes to compiled non-test code.
    (
        "no Rust #[allow]",
        "check_no_allow.py",
        [],
        {"src/lib.rs": "fn main(){}\n#[allow(dead_code)]\nfn x(){}\n"},
        {"src/lib.rs": "fn main(){}\n"},
    ),
    # test*, not t_*: the gate only counts files named like tests.
    (
        "every test is registered",
        "check_tests_registered.py",
        ["--buildsystem", "cmake", "--tests-dir", "tests", "--makefile", "CMakeLists.txt"],
        {
            "CMakeLists.txt": "add_executable(T tests/test_a.cpp)\n",
            "tests/test_a.cpp": "int main(){return 0;}\n",
            "tests/test_b.cpp": "int main(){return 0;}\n",
        },
        {
            "CMakeLists.txt": "add_executable(T tests/test_a.cpp)\n",
            "tests/test_a.cpp": "int main(){return 0;}\n",
        },
    ),
    (
        "tests never take the screen",
        "check_no_screen_presentation.py",
        ["--scope", "tests/*"],
        {"tests/test_ui.swift": "func f() { NSApplication.shared.run() }\n"},
        {"tests/test_ui.swift": "func f() { let x = 1 }\n"},
    ),
    (
        "a ratchet may not grow",
        "check_baseline_ratchet.py",
        ["--baseline", "base.json", "--current", "cur.json"],
        {"base.json": '{"a": 5}', "cur.json": '{"a": 9}'},
        {"base.json": '{"a": 5}', "cur.json": '{"a": 3}'},
    ),
    # The generator writes into a sandbox dir handed to it as $1 (the out-dir contract).
    (
        "generated artifacts are fresh",
        "check_generated_fresh.py",
        ["--generator", "sh gen.sh", "out.txt"],
        {"gen.sh": 'mkdir -p "$1"\necho generated > "$1/out.txt"\n', "out.txt": "stale\n"},
        {"gen.sh": 'mkdir -p "$1"\necho generated > "$1/out.txt"\n', "out.txt": "generated\n"},
    ),
    # ⚠ THE "scratch disk ceiling" CASE WAS DELETED, NOT FIXED, and the reason is a MOVE.
    # `gates_of_heck` commit eed6ffa -- "move the disk watch out of this repo, not just out of the
    # gate" -- removed checks/check_disk_hygiene.py, and this table kept driving it. The canary
    # did not go red: the checker is missing, so the gate warned "not present" and carried on,
    # which is the fixture-blindness case this file's own docstring is about -- an arm that cannot
    # run is not a passing arm. Delete the case when the gate leaves; do not leave it driving a
    # path that is gone.
    # The two below were "declared and nothing reaches it" until 2026-10-01. Both are genuinely
    # expressible as a map of files, so neither was written off: an UNCOVERED entry for a gate a
    # file map CAN drive is the excuse this table is supposed to prevent.
    (
        "markdown links resolve",
        "check_md_links.py",
        [],
        {
            "ROADMAP.md": "# Real Heading\n",
            # The link is well-formed and the FILE exists; only the ANCHOR is invented.
            "doc.md": "[ok](ROADMAP.md#real-heading)\n[bad](ROADMAP.md#no-such-heading)\n",
        },
        {"ROADMAP.md": "# Real Heading\n", "doc.md": "[ok](ROADMAP.md#real-heading)\n"},
    ),
    # The two below were "declared and nothing reaches it" until 2026-10-10: gates_of_heck grew
    # them after this table was last read against it.
    (
        "every child process names its stdin",
        "check_subprocess_stdin.py",
        ["--exclude", ""],
        {"a.py": "import subprocess\nsubprocess.run(['ls'])\n"},
        {"a.py": "import subprocess\nsubprocess.run(['ls'], stdin=subprocess.DEVNULL)\n"},
    ),
    (
        "prose claims are derived",
        "check_claim_derivation.py",
        [],
        # README.md is three lines long; the bad arm claims five.
        {"README.md": "# r\n\nclaim: 5 lines in README.md\n"},
        {"README.md": "# r\n\nclaim: 3 lines in README.md\n"},
    ),
    (
        "python is formatted",
        "check_python_formatted.py",
        [],
        # Two spaces before the assignment: ruff format removes it, so the gate must go red.
        {"m.py": "x = 1\ny  =  2\n"},
        {"m.py": "x = 1\ny = 2\n"},
    ),
    (
        "shell lint",
        "check_shell_lint.sh",
        [],
        # An unterminated `if` -- `bash -n` alone catches it, so the case holds whether or not
        # shellcheck is installed on the machine running the canary.
        {"s.sh": "#!/usr/bin/env bash\nif true; then\n  echo hi\n"},
        {"s.sh": "#!/usr/bin/env bash\nif true; then\n  echo hi\nfi\n"},
    ),
    (
        "gate self-proofs still pass",
        "check_probes_pass.py",
        [],
        {
            "tools/check_rotted.py": (
                "import sys\n"
                "if '--probe' in sys.argv:\n"
                "    raise SystemExit('the fixture this probe drove was renamed')\n"
                "sys.exit(0)\n"
            )
        },
        {
            "tools/check_sound.py": (
                "import sys\nif '--probe' in sys.argv:\n    sys.exit(0)\nsys.exit(0)\n"
            )
        },
    ),
    (
        "every gate refuses to pass over an empty tree",
        "check_empty_scope.py",
        [],
        # A gate that counts files under Sources and reports success regardless -- the class.
        {
            "tools/check_blind.py": (
                "import os, sys\n"
                "n = sum(len(f) for _, _, f in os.walk('Sources'))\n"
                "print(f'ok {n} files clean')\n"
                "sys.exit(0)\n"
            )
        },
        # The same scan, with the guard that makes an empty population a failure.
        {
            "tools/check_guarded.py": (
                "import os, sys\n"
                "n = sum(len(f) for _, _, f in os.walk('Sources'))\n"
                "sys.exit(1 if not n else 0)\n"
            )
        },
    ),
    (
        # A version string that says which BUILD it is: a commit and a date, set by a build
        # script. Added 2026-09-30 with the gate itself, which arrived UNPROVEN -- the calibration
        # table's ceiling of one unproven gate was already spent by check_swift_coverage, so a
        # second unproven gate turned this estate's push red. It is fixturable, unlike that one:
        # this gate is STATIC, so a Cargo.toml and a src/bin file are the whole fixture, and
        # proving it is cheaper than an exemption and is the only answer that leaves the ceiling
        # where it was.
        "a binary's version string carries its build",
        "check_version_provenance.py",
        # No argv: the root is a POSITIONAL argument defaulting to ".", and the canary runs the
        # gate with the fixture repo as cwd. `--root .` -- which is how every other gate in this
        # table spells it -- is an unrecognised argument here, and the gate answers a usage error
        # with exit 2, which the canary read as a false positive rather than as a bad fixture.
        [],
        {
            "Cargo.toml": '[package]\nname="app"\nversion="0.1.0"\n',
            "src/bin/app.rs": ('#[command(version = "0.1.0")]\nfn main() {}\n'),
        },
        {
            "Cargo.toml": '[package]\nname="app"\nversion="0.1.0"\n',
            "src/bin/app.rs": (
                '#[command(version = concat!("0.1.0+", env!("GIT_HASH"), " ", '
                'env!("BUILD_DATE")))]\n'
                "fn main() {}\n"
            ),
            "build.rs": (
                "fn main() {\n"
                '    println!("cargo:rustc-env=GIT_HASH=deadbeefcafe");\n'
                '    println!("cargo:rustc-env=BUILD_DATE=2026-09-30");\n'
                "}\n"
            ),
        },
    ),
    (
        # Every compiler warning in the repo's OWN sources is fatal, judged from a saved build log
        # so the gate never needs a toolchain. Canaried 2026-09-30: this gate shipped UNPROVEN,
        # which is the reachability class pointed at itself -- declared, and nothing reached it.
        # The fixture log names {root} because the gate matches absolute diagnostic paths and then
        # asks whether they are ours; a dependency's warning is exempt, which is the second half
        # of the rule and is why the clean tree keeps one.
        "a warning in our sources fails; a dependency's does not",
        "check_swift_warnings.py",
        ["--log", "build.log"],
        {
            "build.log": (
                "Building for debugging...\n"
                "{root}/Sources/App.swift:9:3: warning: variable 'x' was never used\n"
            ),
        },
        {
            "build.log": (
                "Building for debugging...\n"
                "/some/other/checkout/Sources/Lib.swift:4:9: warning: unused result\n"
            ),
        },
    ),
    (
        # The gate landed in the shared layer 2026-10-03 and the canary found it DECLARED AND
        # UNREACHED -- which is this table's own reachability class pointed at itself, and a red
        # this repo had been carrying. It needs `--policy`, and gates_of_heck's own
        # empty_scope_allow.json says why a sweep cannot supply one: "requires --policy and says
        # so: it has no opinion about a consumer's display, and a default policy would be a wrong
        # opinion rather than a convenient one." That is true of the SWEEP, which builds a bare
        # tree; a case is not a sweep, and a case can carry the policy as a file. It is fixturable
        # after all, which is the answer this table exists to reach.
        "presentation routes through the declared seam",
        "check_display_seam.py",
        ["--policy", "policy.json"],
        {
            # The policy names the seam, and the seam has to EXIST or the gate answers a config
            # error (exit 2) rather than a verdict -- which `run_case` reads as a false positive.
            "policy.json": '{"note": "canary fixture", "scan": ["Core"], '
            '"seam": ["Core/seam.swift"], "markers": ["screen-ok:"], '
            '"live_helper": "require_live_screen"}\n',
            "Core/seam.swift": "import AppKit\npublic func present() { NSApplication.shared.present() }\n",
            "Core/caller.swift": "import AppKit\n\nfunc go() { seam.present() }\n",
            # Outside the seam, with no marker. The scan root has to contain at least one file
            # that is NOT the seam: with only the seam present the gate reports "examined ZERO
            # files -- refusing to report a pass for nothing", which is correct of it and useless
            # as a clean arm.
            "Core/bad.swift": "import AppKit\n\nfunc go() { NSApplication.shared.activate(ignoringOtherApps: true) }\n",
        },
        {
            "policy.json": '{"note": "canary fixture", "scan": ["Core"], '
            '"seam": ["Core/seam.swift"], "markers": ["screen-ok:"], '
            '"live_helper": "require_live_screen"}\n',
            "Core/seam.swift": "import AppKit\npublic func present() { NSApplication.shared.present() }\n",
            "Core/caller.swift": "import AppKit\n\nfunc go() { seam.present() }\n",
            # The marker shape is in the clean tree on purpose: a gate whose clean arm is only
            # "nothing to find" would go red the day a marked call site stopped being accepted,
            # and nobody would notice for a release.
            "Core/marked.swift": "import AppKit\n\nfunc go() {\n"
            "    // screen-ok: a deliberate one-off during a manual inspection\n"
            "    NSApplication.shared.activate(ignoringOtherApps: true)\n}\n",
        },
    ),
    (
        # The gate that cost media_server a day: a test spawns a child that loops forever and the
        # reap sits below an assertion that can panic, so every ordinary failure leaks it, each
        # orphan holds the cargo build lock, and the only observable is a test run that prints
        # nothing. Static, so a source file is the whole fixture -- no toolchain needed, unlike the
        # cases above it that had to be written off for exactly that reason.
        "a test cannot leave an unreaped child behind",
        "check_no_unreaped_spawn.py",
        [],
        {
            "Cargo.toml": '[package]\nname = "a"\n',
            # The incident shape verbatim: spawn, then an assertion that can panic, and no reap.
            "tests/test_leak.rs": "use std::process::Command;\n\n#[test]\nfn spawns_and_forgets() {\n"
            '    let _child = Command::new("sleep").arg("300").spawn().unwrap();\n'
            "    assert!(true);\n}\n",
        },
        {
            "Cargo.toml": '[package]\nname = "a"\n',
            # kill() AND wait(), both above the assertion. A bare kill() leaves a zombie, which
            # cannot hold a lock and cannot outlive the run -- the gate measures that as clean, and
            # says so in its own table.
            "tests/test_leak.rs": "use std::process::Command;\n\n#[test]\nfn spawns_and_reaps() {\n"
            '    let mut child = Command::new("sleep").arg("300").spawn().unwrap();\n'
            "    child.kill().unwrap();\n    child.wait().unwrap();\n    assert!(true);\n}\n",
        },
    ),
    # Landed in goh 2026-10-13 (85d0b51, BACKLOG 6.1) and found here DECLARED AND UNREACHED on
    # 2026-10-14. Fixturable in two files: the census config and one tool that spawns git raw.
    (
        "every git spawn goes through the seam or says why",
        "check_git_env.py",
        [],
        {
            "tools/git_env.json": '{"sources": ["tools"]}\n',
            "tools/a.py": 'import subprocess\nsubprocess.run(["git", "status"], check=False)\n',
        },
        {
            "tools/git_env.json": '{"sources": ["tools"]}\n',
            "tools/a.py": "print('no git here')\n",
        },
    ),
    # Landed in goh 2026-10-18 (126955e, v0.33.0) and found here DECLARED AND UNREACHED on
    # 2026-10-19, when it turned this repo's push red. Fixturable in two files: a workflow whose
    # run: step calls a repo script, and a .gatesrc whose local steps do or do not name it.
    (
        "every script CI runs, the local gate runs too",
        "check_ci_local_parity.py",
        [],
        {
            ".github/workflows/ci.yml": "jobs:\n  a:\n    steps:\n      - run: scripts/a.sh\n",
            ".gatesrc": "GOH_CI_STEPS='bash scripts/other.sh'\n",
        },
        {
            ".github/workflows/ci.yml": "jobs:\n  a:\n    steps:\n      - run: scripts/a.sh\n",
            ".gatesrc": "GOH_CI_STEPS='bash scripts/a.sh'\n",
        },
    ),
]

# Gates this cannot fixture, with the reason. Each is a claim someone can challenge, which is the
# point of writing it down rather than quietly covering ten and reporting eleven.
UNCOVERED = {
    # Added 2026-10-01 after the canary found it DECLARED AND UNREACHED -- the state its own
    # failure message calls "running in five repos with nothing showing it can fail". This gate
    # came out of a real published defect (media_server: an amend was refused twice with
    # `2>/dev/null`, a force-tag then named a commit whose VERSION said something else, and anyone
    # checking out the tag got a build reporting the wrong version). It is worth keeping; it cannot
    # be fixtured by THIS harness, because a case is a map of files and this gate needs a git
    # repository with a commit, a tag and an amended VERSION file -- three things a file map cannot
    # express. Recorded rather than left to look covered.
    # Added 2026-10-10: gates_of_heck grew it after this table was last read against it.
    "check_no_credential_urls.py": (
        "its subject is a credential in a git REMOTE URL, which lives in .git/config -- an "
        "untracked file of the repository, not a file of its tree, and a case is a map of tracked "
        "files; gates_of_heck's tests/test_credential_urls_native_parity.py drives it red"
    ),
    "check_tag_version.py": (
        "needs a git repository with a commit, a tag, and a commit whose declared VERSION does "
        "not match the tag -- a case is a map of files, and a tag is not a file"
    ),
    "check_dep_currency.py": (
        "asks crates.io what the LATEST version of a dependency is, so its answer depends on the "
        "network and on the registry's current state; a fixture that froze a version would assert "
        "something about the day it was written, not about the gate"
    ),
    "check_lock_version.py": (
        "needs a real cargo invocation -- a manifest, a generated Cargo.lock, and a second edit to "
        "the manifest -- and a case is a map of files, not a toolchain run"
    ),
    "check_no_screen_linkage.sh": (
        "it inspects the LINK TABLE of a built test binary, so a fixture would have to be a "
        "compiled Mach-O; a text file cannot exercise it. Its static half, "
        "check_no_screen_presentation.py, is canaried above"
    ),
    # Added 2026-10-14: goh grew it (72e8445, BACKLOG 6.12) after this table was last read.
    "check_audit_ignores.py": (
        "its stale-entry half runs `cargo audit` against a real advisory database and asks which "
        "ids a real Cargo.lock still triggers -- a database fetch and a toolchain run, not a map "
        "of files; gates_of_heck's tests/test_rust_audit.py drives every direction red and green"
    ),
    "check_swift_coverage.py": (
        "needs a built Swift package with real coverage data; a fixture would be a Swift "
        "toolchain invocation, not a file"
    ),
    # Added 2026-10-04 when this canary found it DECLARED AND UNREACHED. It is the one of the three
    # that gates_of_heck's own empty_scope_allow.json already describes correctly, and the
    # description is the reason: "its subject is the CONSUMER ESTATE, not the tree it is invoked
    # in: it copies real subtrees out of other repos into a temp directory and never reads this
    # repo at all". A case is a map of files planted in a throwaway git repo; this gate plants its
    # violation in a COPY OF A REAL CONSUMER'S FILE and requires the checker to name it. There is
    # no file map that expresses "the estate", so this is not an excuse -- it is a different kind
    # of instrument, and its own `--probe` and `--list` are how it proves itself. Recorded rather
    # than left to look covered.
    "check_estate_corpus.py": (
        "its subject is OTHER REPOS: it copies a real consumer's subtree into a temp tree, plants "
        "a violation in a real file of the real language, and requires the checker to go red AND "
        "name that file. A case is a map of files planted in a throwaway repo, which is the one "
        "thing this cannot express -- a fixture cannot disagree with the assumption that produced "
        "it, and manufacturing a corpus would be exactly that. It carries its own --probe and --list"
    ),
}
