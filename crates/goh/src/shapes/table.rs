//! THE table: one row per scanner in the registry (`super::registry`). See `super` for the rules.
//!
//! A declared row's shapes are re-proven by `super::tests` against their fixtures, and its probe is
//! run beside the scanner over the estate by `goh shape-diff --estate` (every land of a change to
//! the scanner's sources, `--since`). Escapes are assembled with `concat!` so no fixture here is a
//! finding of `goh status-markers` over this file.

use super::{hooks, Declared, Row, Shape, State, Token, Verdict};

/// The rows still `Unreviewed` on 2026-11-15, when the table landed (v0.118.0 included).
///
/// It may only SHRINK: a row leaves it when its shapes are declared, and a new scanner cannot join it (the walk refuses an
/// unreviewed row not listed here, and a listed name whose row is declared is stale).
pub const BURN_DOWN: &[&str] = &[
    "Cargo.lock matches its manifests",
    "a file that calls X calls Y",
    "cap-exempt files within their ceilings",
    "every .gatesrc key is set once",
    "every child process names its stdin",
    "every tag has its changelog heading",
    "every tracked JavaScript source parses",
    "goh fixed-sleep",
    "line-cap exemptions carry a ceiling",
    "markdown links resolve",
    "no doc keeps the installed version by hand",
    "no bare read of the hook's index",
    "no release step hides its output",
    "no BSD-only mktemp -t",
    "no bare open of an app",
    "no hedged plural in a string",
    "no bare python in a hook or gate",
    "no busy loop or busy-wait in a tracked file",
    "no checkout leaves its token in git config",
    "no direct write to a Homebrew tap",
    "no code after exec",
    "no committed secrets",
    "no conflict markers",
    "no credential in a git remote URL",
    "no disallowed emoji",
    "no early-exit pipe under pipefail",
    "no hard-coded home paths",
    "no moving runner label or action ref in a workflow",
    "no process kill by name",
    "no private repository named in a public tree",
    "no tracked build output",
    "no tracked file its .gitignore ignores",
    "no git stopped at a deadline holding a lock",
    "every language linted at commit is gated at push",
    "no changelog entry under Unreleased",
    "no unreaped spawns in tests",
    "no vendored copies of house files",
    "one roadmap, open entries only",
    "prose claims are derived",
    "python is ruff-formatted",
    "shell lint",
    "skills corpus",
    "version provenance",
];

/// The tokens only an opt-in scanner judges, each with the item that closes the hole (2026-11-15).
///
/// It may only SHRINK: an entry whose token a stock row now judges (or whose owner is
/// stock) is stale and red, and a new opt-in-alone token cannot join.
/// Empty since GOH.ESCAPEOWNER part 2: `goh tui-marks` judges the escape and the bracketed marker
/// opening a print as status-markers' fallback, where status-markers does not count the line.
pub const OPT_IN_ALONE: &[(&str, &str)] = &[];

const fn unreviewed(scanner: &'static str) -> Row {
    Row {
        scanner,
        state: State::Unreviewed,
    }
}

/// Prose names an image, a marker or an escape to describe it; nothing runs it.
pub(crate) const DOCS: &str = r"(?i)\.(md|markdown|txt|rst|adoc|html?)$";

/// A whole-line comment in the languages the estate carries.
pub(crate) const COMMENT: &str = r"^\s*(#|//|/\*|\*|<!--|;)";
/// Program source, not a build, deploy or shell file.
pub(crate) const PROGRAM: &str =
    r"(?i)\.(rs|py|swift|m|mm|c|cc|cpp|h|hpp|go|js|mjs|cjs|ts|tsx|jsx|java|kt|rb|css|scss)$";
/// A recorded artifact: a patch, a log, a snapshot.
pub(crate) const DATA: &str = r"(?i)\.(patch|diff|log|snap|golden|csv|tsv|out|expected)$";
/// A shell script by name (`crate::shellsrc` also reads a shebang, which a path cannot show).
pub(crate) const SHELL: &str = r"(?i)\.(sh|bash|zsh)$|\.sh\.tmpl$";
/// A make recipe or a CI run block: shell a Makefile or YAML holds.
pub(crate) const RECIPE: &str = r"(?i)(^|/)(gnu)?makefile$|\.mk$|\.ya?ml$";
/// A test's own tree: what it holds is a planted fixture.
pub(crate) const TESTS: &str = r"(?i)(^|/)tests?/|(^|/)test_[^/]*$|_tests?\.(py|rs)$";

/// The shapes the differential found `goh tui-marks` does not read (to be filed as a member).
const TUI_GAP: &str = "GOH.TUIMARKSHAPES";
/// The shapes the differential found `goh status-markers` does not read (to be filed as a member).
const STATUS_GAP: &str = "GOH.STATUSMARKSHAPES";

const STATUS_MARKERS: Declared = Declared {
    probe: concat!(
        r"\[\s*(?:==>|-->)\s*\]|\[\s+(?:---|(?i:ok|pass|passed|fail|failed|err|error|wrn|warn|warning|info|skip|done))\s+\]",
        r"|\\(?:033|x1[bB]|e|u001[bB]|u\{0*1[bB]\})\["
    ),
    judged: hooks::status_markers,
    owns: &[
        Token {
            name: "bracketed status marker",
            sample: ("a.sh", concat!("echo \"[ ", "OK ] done\"\n")),
        },
        Token {
            name: "ANSI CSI escape",
            sample: ("b.sh", concat!("echo \"", "\\", "033[32m done\"\n")),
        },
    ],
    sources: &["crates/goh/src/statusmark/"],
    opt_in: Some("a repo's own gate step: `goh status-markers` in its GOH_CI_STEPS (BACKLOG 4.9)"),
    shapes: &[
        Shape {
            name: "a marker in a string in source",
            path: "",
            line: r"\[\s+",
            verdict: Verdict::Covered,
            fixture: ("tools/run.py", concat!("print(\"[ ", "Wrn ] slow\")\n")),
        },
        Shape {
            name: "an escape in source",
            path: "",
            line: r"\\",
            verdict: Verdict::Covered,
            fixture: (
                "src/log.rs",
                concat!("eprintln!(\"", "\\", "x1b[31mred\");\n"),
            ),
        },
        Shape {
            name: "a whole-line comment",
            path: "",
            line: r"^\s*(#[^!]|#$|//|/\*|\* |\*$)",
            verdict: Verdict::OutOfScope(
                "a comment quoting the forbidden style forbids it; it prints nothing",
            ),
            fixture: ("a.sh", concat!("# never print \"[ ", "OK ]\" by hand\n")),
        },
        Shape {
            name: "vendored third-party code",
            path: r"(^|/)(third_party|vendor|vendored)/",
            line: "",
            verdict: Verdict::OutOfScope("upstream's code; the house style does not reach it"),
            fixture: (
                "third_party/linenoise/linenoise.c",
                concat!("abAppend(ab,\"", "\\", "033[0m\",4);\n"),
            ),
        },
        Shape {
            name: "the house style's own definition",
            path: r"(^|/)tui/stylerc$",
            line: "",
            verdict: Verdict::OutOfScope(
                "tui/stylerc is where the house escapes are defined, once",
            ),
            fixture: ("tui/stylerc", concat!("C_RESET=\"", "\\", "033[0m\"\n")),
        },
        Shape {
            name: "a marker quoted in backticks: docstring prose",
            path: "",
            line: r"`[^`]*\[\s",
            verdict: Verdict::OutOfScope(
                "a docstring naming the style it forbids; it prints nothing",
            ),
            fixture: (
                "tests/test_style.py",
                concat!("\"\"\"Refuses\n(`[ ", "OK ]`) by hand.\"\"\"\n"),
            ),
        },
        Shape {
            name: "an installer template, a make recipe",
            path: r"(?i)\.tmpl$|(^|/)(gnu)?makefile$|\.mk$",
            line: "",
            verdict: Verdict::Gap(STATUS_GAP),
            fixture: (
                "release/install.sh.tmpl",
                concat!(
                    "ok() { printf '%s[ ",
                    "ok ]%s %s\\n' \"$C\" \"$R\" \"$*\"; }\n"
                ),
            ),
        },
        Shape {
            name: "not source: docs, data, config",
            path: r"(?i)\.(md|markdown|txt|rst|adoc|html?|json|ya?ml|toml|cfg|ini|csv|log|snap|golden|out|expected)$",
            line: "",
            verdict: Verdict::OutOfScope(
                "a doc, a fixture's data or a config describes the style; it is not printed from",
            ),
            fixture: ("docs/style.md", concat!("Never `[ ", "OK ]`.\n")),
        },
    ],
};

const TUI_MARKS: Declared = Declared {
    // The Kare marks, then status-markers' MARKER and ESCAPE (`crate::statusmark::scan`), which
    // this scanner judges as their fallback (GOH.ESCAPEOWNER; a test holds the copy to them).
    probe: concat!(
        r#"(?:\b(?:echo|printf)\s|\bprint\()[^#]*?["']\s*(?:→|·|✓|✗|⚠"#,
        r"|\[\s*(?:==>|-->)\s*\]|\[\s+(?:---|(?i:ok|pass|passed|fail|failed|err|error|wrn|warn|warning|info|skip|done))\s+\]",
        r"|\\(?:033|x1[bB]|e|u001[bB]|u\{0*1[bB]\})\[)"
    ),
    judged: hooks::tui_marks,
    owns: &[Token {
        name: "Kare mark opening a print",
        sample: ("c.sh", "echo \"✓ done\"\n"),
    }],
    sources: &["crates/goh/src/tuimarks/"],
    opt_in: None,
    shapes: &[
        Shape {
            name: "shell echo/printf",
            path: r"(?i)\.(sh|bash|zsh)$|\.sh\.tmpl$",
            line: r"\b(echo|printf)\s",
            verdict: Verdict::Covered,
            fixture: ("tools/setup.sh", "printf '  ✓ %s\\n' \"$x\"\n"),
        },
        Shape {
            name: "Python print",
            path: r"(?i)\.py$",
            line: r"\bprint\(",
            verdict: Verdict::Covered,
            fixture: ("tools/run.py", "print(f\"  ✗ {name}\")\n"),
        },
        Shape {
            name: "status-markers' escape or marker opening a print, where it does not run",
            path: r"(?i)\.(sh|bash|zsh|py)$",
            line: r"\\|\[\s",
            verdict: Verdict::Covered,
            fixture: (
                "tools/banner.sh",
                concat!("printf '", "\\", "033[1m%s\\n' \"$x\"\necho \"[ ", "OK ] built\"\n"),
            ),
        },
        Shape {
            name: "a whole-line comment",
            path: "",
            line: COMMENT,
            verdict: Verdict::OutOfScope("a comment quoting the forbidden shape forbids it; it prints nothing"),
            fixture: ("tools/a.sh", "# never echo \"✓ done\" by hand\n"),
        },
        Shape {
            name: "a print quoted in backticks: docstring prose",
            path: "",
            line: r"`(echo|printf|print\()",
            verdict: Verdict::OutOfScope("a docstring naming the shape it forbids; it prints nothing"),
            fixture: (
                "tools/check.py",
                "\"\"\"Refuses marks\n(`echo \"✗ ...\"`, `print(\"✓ x\")`) by hand.\"\"\"\n",
            ),
        },
        Shape {
            name: "status-markers' escape or marker printed from Swift, Rust, C or JS",
            path: r"(?i)\.(swift|rs|m|mm|c|cc|cpp|h|js|mjs|ts|tsx)$",
            line: r"\\|\[\s",
            verdict: Verdict::Gap(TUI_GAP),
            fixture: (
                "Sources/probe/Inspect.swift",
                concat!("print(\"", "\\", "u{1B}[31mfailed\")\n"),
            ),
        },
        Shape {
            name: "a language with no house TUI lib (Swift, Rust, C, JS)",
            path: r"(?i)\.(swift|rs|m|mm|c|cc|cpp|h|js|mjs|ts|tsx)$",
            line: "",
            verdict: Verdict::OutOfScope(
                "tui/lib.sh and tui.lib serve shell and Python; a language with none prints tui/stylerc's \
                 glyphs itself (the status-markers fix text), and goh's own Rust prints through goh-tui",
            ),
            fixture: ("Sources/probe/Inspect.swift", "print(\"  ✓ every key is read\")\n"),
        },
        Shape {
            name: "a test's planted fixture",
            path: TESTS,
            line: "",
            verdict: Verdict::OutOfScope("the print line is data a test feeds a gate; it runs only in the test"),
            fixture: ("tests/test_profile.py", "STEP = \"echo '✓ [1/2] ./step_one.sh'\"\n"),
        },
        Shape {
            name: "a mark the line compares, not prints (`startswith(\"✗\")`)",
            path: "",
            line: r#"\b(startswith|endswith|in)\s*\(?\s*["'](→|·|✓|✗|⚠)"#,
            verdict: Verdict::OutOfScope("the print routes a line by its mark; the mark printed is the line's own"),
            fixture: ("checks/cov.py", "print(line, file=sys.stderr if line.startswith(\"✗\") else sys.stdout)\n"),
        },
        Shape {
            name: "data: a patch, a log, a recorded output",
            path: DATA,
            line: "",
            verdict: Verdict::OutOfScope("a recorded patch, log or output quotes a print; it is not a script"),
            fixture: ("audits/fix.patch", "+print(\"✓ done\")\n"),
        },
        Shape {
            name: "prose",
            path: DOCS,
            line: "",
            verdict: Verdict::OutOfScope("a doc shows the shape; it prints nothing"),
            fixture: ("README.md", "Do not `echo \"✓ done\"`.\n"),
        },
        Shape {
            name: "a shell print held in a Python string (a subprocess line, a checker's sample)",
            path: r"(?i)\.py$",
            line: r"\b(echo|printf)\s",
            verdict: Verdict::Gap(TUI_GAP),
            fixture: ("tools/check_style.py", "SAMPLE = 'echo \"✓ step\"\\n'\n"),
        },
        Shape {
            name: "a mark in printf's argument, after a `%s` format",
            path: "",
            line: r#"\bprintf\s+(--\s+)?['"][^'"]*%s"#,
            verdict: Verdict::Gap(TUI_GAP),
            fixture: ("lib/bash5.sh", "printf '%s\\n' \"✗ $*\" >&2\n"),
        },
        Shape {
            name: "a mark an expression chooses (`{'✓' if ok else '✗'}`)",
            path: r"(?i)\.py$",
            line: r#"\bprint\(\s*(\(|f?["'][^"']*\{)"#,
            verdict: Verdict::Gap(TUI_GAP),
            fixture: ("tools/stamp.py", "print(f\"{'✓' if ok else '✗'} selftest {name}\")\n"),
        },
        Shape {
            name: "Python a shell script runs from a heredoc",
            path: SHELL,
            line: r"\bprint\(",
            verdict: Verdict::Gap(TUI_GAP),
            fixture: ("tools/cut.sh", "#!/bin/sh\npython3 - <<'PY'\nprint(\"  ✓ all read back\")\nPY\n"),
        },
        Shape {
            name: "a make recipe or a CI run block",
            path: RECIPE,
            line: "",
            verdict: Verdict::Gap(TUI_GAP),
            fixture: ("Makefile", "app:\n\t@echo \"✓ built\"\n"),
        },
        Shape {
            name: "a Python script with no .py (shebang only)",
            path: r"(^|/)[^./]+$",
            line: r"\bprint\(",
            verdict: Verdict::Gap(TUI_GAP),
            fixture: ("bin/ab_test", "#!/usr/bin/env python3\nprint(f\"  ✓ all agree\")\n"),
        },
    ],
};

/// Every scanner's row, in registry order (the walk checks the set, not the order).
pub const ROWS: &[Row] = &[
    Row {
        scanner: "no container image on a moving tag",
        state: State::Declared(crate::movingtag::shapes::SHAPES),
    },
    Row {
        scanner: "a workflow runs a current goh on a clone with its tags",
        state: State::Declared(crate::gohpin::shapes::SHAPES),
    },
    Row {
        scanner: "goh status-markers",
        state: State::Declared(STATUS_MARKERS),
    },
    Row {
        scanner: "no hand-rolled TUI mark",
        state: State::Declared(TUI_MARKS),
    },
    Row {
        scanner: "no temp, lock or stage path in a work tree",
        state: State::Declared(crate::runpaths::SHAPES),
    },
    Row {
        scanner: "every scanner read its declared subject",
        state: State::NotAScanner("it judges the other scanners' read counts; it reads no file"),
    },
    Row {
        scanner: "gate self-proofs still pass",
        state: State::NotAScanner("it runs each gate's own self-proof; the gates read the files"),
    },
    Row {
        scanner: "gates refuse to pass over an empty tree",
        state: State::NotAScanner(
            "it runs each gate over a skeleton tree; the gates read the files",
        ),
    },
    unreviewed("Cargo.lock matches its manifests"),
    unreviewed("a file that calls X calls Y"),
    unreviewed("cap-exempt files within their ceilings"),
    unreviewed("every .gatesrc key is set once"),
    unreviewed("every child process names its stdin"),
    unreviewed("every tag has its changelog heading"),
    unreviewed("every tracked JavaScript source parses"),
    Row {
        scanner: "goh empty-assert",
        state: State::Declared(crate::emptyassert::shapes::SHAPES),
    },
    Row {
        scanner: "file length",
        state: State::Declared(crate::length::shapes::SHAPES),
    },
    unreviewed("goh fixed-sleep"),
    unreviewed("line-cap exemptions carry a ceiling"),
    unreviewed("markdown links resolve"),
    unreviewed("no doc keeps the installed version by hand"),
    unreviewed("no bare read of the hook's index"),
    unreviewed("no release step hides its output"),
    unreviewed("no BSD-only mktemp -t"),
    unreviewed("no bare open of an app"),
    unreviewed("no hedged plural in a string"),
    unreviewed("no bare python in a hook or gate"),
    unreviewed("no busy loop or busy-wait in a tracked file"),
    unreviewed("no checkout leaves its token in git config"),
    unreviewed("no direct write to a Homebrew tap"),
    unreviewed("no code after exec"),
    unreviewed("no committed secrets"),
    unreviewed("no conflict markers"),
    unreviewed("no credential in a git remote URL"),
    unreviewed("no disallowed emoji"),
    unreviewed("no early-exit pipe under pipefail"),
    unreviewed("no hard-coded home paths"),
    unreviewed("no moving runner label or action ref in a workflow"),
    unreviewed("no process kill by name"),
    unreviewed("no private repository named in a public tree"),
    unreviewed("no tracked build output"),
    unreviewed("no tracked file its .gitignore ignores"),
    // On main (v0.118.0, GOH.GITKILLNATIVE) before this table landed: listed, not exempt.
    unreviewed("no git stopped at a deadline holding a lock"),
    // On main (GOH.LAYERPARITY, GOH.CLASS.CHECKOUT) before this table landed.
    unreviewed("every language linted at commit is gated at push"),
    unreviewed("no changelog entry under Unreleased"),
    unreviewed("no unreaped spawns in tests"),
    unreviewed("no vendored copies of house files"),
    unreviewed("one roadmap, open entries only"),
    unreviewed("prose claims are derived"),
    unreviewed("python is ruff-formatted"),
    unreviewed("shell lint"),
    unreviewed("skills corpus"),
    unreviewed("version provenance"),
];
