//! `goh goh-pins`' row in the shape table (`crate::shapes::table::ROWS`, GOH.CLASS.SHAPE): the
//! probe, the judged hook, and every input shape with its fixture.

use crate::shapes::table::{COMMENT, DOCS};
use crate::shapes::{hooks, Declared, Shape, Token, Verdict};

/// Paths outside `.github/` (no lookaround in the regex crate: the first five bytes differ).
const NOT_GITHUB: &str = r"^(?:[^.]|\.[^g]|\.g[^i]|\.gi[^t]|\.git[^h])";

/// This scanner's row.
pub const SHAPES: Declared = Declared {
    probe: r#"repository:\s*["']?[\w.-]+/gates_of_heck\b|\bGOH_TAG:|\bgit\s+clone\b.*gates_of_heck|uses:\s*["']?[\w.-]+/gates_of_heck/"#,
    judged: hooks::goh_pins,
    owns: &[Token {
        name: "goh release pin",
        sample: (
            ".github/workflows/ci.yml",
            "on: push\njobs:\n  g:\n    runs-on: ubuntu-24.04\n    steps:\n      - uses: actions/checkout@v7.0.1\n        with:\n          repository: o/gates_of_heck\n          ref: v0.121.0\n",
        ),
    }],
    sources: &["crates/goh/src/gohpin/"],
    opt_in: None,
    shapes: &[
        Shape {
            name: "a checkout of goh",
            path: r"^\.github/",
            line: "repository:",
            verdict: Verdict::Covered,
            fixture: (
                ".github/workflows/ci.yml",
                "on: push\njobs:\n  g:\n    runs-on: ubuntu-24.04\n    steps:\n      - uses: actions/checkout@v7.0.1\n        with:\n          repository: o/gates_of_heck\n          path: goh\n",
            ),
        },
        Shape {
            name: "GOH_TAG under env:",
            path: r"^\.github/",
            line: "GOH_TAG:",
            verdict: Verdict::Covered,
            fixture: (
                ".github/workflows/ci.yml",
                "on: push\nenv:\n  GOH_TAG: v0.95.0\njobs:\n  g:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: true\n",
            ),
        },
        Shape {
            name: "a git clone of goh in a run:",
            path: r"^\.github/",
            line: r"git\s+clone",
            verdict: Verdict::Covered,
            fixture: (
                ".github/workflows/ci.yml",
                "on: push\njobs:\n  g:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: |\n          git clone https://github.com/o/gates_of_heck \"$GOH_DIR\"\n",
            ),
        },
        Shape {
            name: "a goh action by commit",
            path: r"^\.github/",
            line: r"uses:\s*\S+/gates_of_heck/",
            verdict: Verdict::Gap("GOH.WFPINACTION"),
            fixture: (
                ".github/workflows/ci.yml",
                "on: push\njobs:\n  g:\n    runs-on: ubuntu-24.04\n    steps:\n      - uses: o/gates_of_heck/.github/actions/goh-floors@274ce50e3d4f820de845cedfe85ac69aa6910a1e # v0.54.1\n",
            ),
        },
        Shape {
            name: "a comment",
            path: "",
            line: COMMENT,
            verdict: Verdict::OutOfScope("a commented-out clone or checkout runs nothing"),
            fixture: (
                ".github/workflows/ci.yml",
                "on: push\njobs:\n  g:\n    runs-on: ubuntu-24.04\n    steps:\n      # git clone https://github.com/o/gates_of_heck\n      - run: true\n",
            ),
        },
        Shape {
            name: "prose",
            path: DOCS,
            line: "",
            verdict: Verdict::OutOfScope("a doc names a clone to describe it; no hosted gate runs it"),
            fixture: ("README.md", "Run `git clone https://github.com/o/gates_of_heck`.\n"),
        },
        Shape {
            name: "outside .github",
            path: NOT_GITHUB,
            line: "",
            verdict: Verdict::OutOfScope(
                "a script, test or tool clones goh on the machine (an install, a canary, a fixture); \
                 the goh a hosted gate runs is named in its workflow, which this reads",
            ),
            fixture: ("tools/bootstrap.sh", "git clone https://github.com/o/gates_of_heck \"$d\"\n"),
        },
    ],
};
