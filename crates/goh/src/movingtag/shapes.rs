//! `goh moving-tags`' row in the shape table (`crate::shapes::table::ROWS`, GOH.CLASS.SHAPE): the
//! probe, the judged hook, and every input shape with its fixture.

use crate::shapes::table::{COMMENT, DATA, DOCS, PROGRAM, SHELL};
use crate::shapes::{hooks, Declared, Shape, Token, Verdict};

/// This scanner's row.
pub const SHAPES: Declared = Declared {
    probe: r#"^\s*FROM\s+\S|--from=\S|^\s*(?:-\s+)?image:\s*["']?[a-z0-9$]|\b[A-Z_]*IMAGE[A-Z_]*=["']?[a-z0-9$]|\b(?:docker|podman)\s+(?:run|pull|create)\b|docker://|^\s*container:\s*["']?[a-z0-9$]"#,
    judged: hooks::moving_tags,
    owns: &[Token {
        name: "container image reference",
        sample: ("Dockerfile", "FROM rust:1\n"),
    }],
    sources: &["crates/goh/src/movingtag/"],
    opt_in: None,
    shapes: &[
        Shape {
            name: "Dockerfile FROM",
            path: r"(?i)(^|/)(Docker|Container)file[^/]*$",
            line: r"^\s*FROM\s",
            verdict: Verdict::Covered,
            fixture: ("Dockerfile", "FROM rust:1.99.1\nRUN true\n"),
        },
        Shape {
            name: "Dockerfile COPY --from",
            path: r"(?i)(^|/)(Docker|Container)file[^/]*$",
            line: r"--from=",
            verdict: Verdict::Covered,
            fixture: (
                "build/Containerfile",
                "FROM alpine:3.20.3\nCOPY --from=busybox:1.36.1 /bin/sh /sh\n",
            ),
        },
        Shape {
            name: "YAML image:",
            path: r"(?i)\.ya?ml$",
            line: r"image:",
            verdict: Verdict::Covered,
            fixture: ("compose/db.yml", "services:\n  db:\n    image: postgres:16.4\n"),
        },
        Shape {
            name: "workflow container:",
            path: r"(?i)\.ya?ml$",
            line: r"container:",
            verdict: Verdict::Covered,
            fixture: (
                ".github/workflows/ci.yml",
                "on: push\njobs:\n  t:\n    runs-on: ubuntu-24.04\n    container: rust:1.99.1\n    steps:\n      - run: true\n",
            ),
        },
        Shape {
            name: "workflow uses: docker://",
            path: r"(?i)\.ya?ml$",
            line: r"docker://",
            verdict: Verdict::Covered,
            fixture: (
                ".github/workflows/lint.yml",
                "on: push\njobs:\n  l:\n    runs-on: ubuntu-24.04\n    steps:\n      - uses: docker://alpine:3.20.3\n",
            ),
        },
        Shape {
            name: "a comment",
            path: "",
            line: COMMENT,
            verdict: Verdict::OutOfScope("a comment names an image, and a commented-out image is not built; nothing pulls it"),
            fixture: ("Dockerfile", "FROM a:1.2.3\n# COPY --from=busybox:latest /x /x\n"),
        },
        Shape {
            name: "prose",
            path: DOCS,
            line: "",
            verdict: Verdict::OutOfScope("a doc names an image to describe it; it pulls nothing"),
            fixture: ("docs/build.md", "The build runs `FROM rust:1.99.1`.\nFROM rust:1\n"),
        },
        Shape {
            name: "SQL",
            path: r"\.sql$",
            line: "",
            verdict: Verdict::OutOfScope("a query's FROM names a table; nothing pulls it"),
            fixture: ("db/0001_view.sql", "SELECT a\nFROM readings\nGROUP BY a;\n"),
        },
        Shape {
            name: "program source",
            path: PROGRAM,
            line: "",
            verdict: Verdict::OutOfScope(
                "an image in a program is a string a test or a message holds, or an argument vector the \
                 language builds; this check reads build and deploy files, not programs",
            ),
            fixture: ("tests/test_pins.py", "FIXTURE = \"\"\"\nimage: foo:latest\n\"\"\"\n"),
        },
        Shape {
            name: "a SQL query's FROM",
            path: r"\.sql$",
            line: r"^\s*FROM\s",
            verdict: Verdict::OutOfScope("a FROM clause names a table, not an image; nothing builds a .sql file"),
            fixture: ("db/0001_views.sql", "CREATE VIEW v AS SELECT a\nFROM readings\nWHERE a > 0;\n"),
        },
        Shape {
            name: "data: a patch, a log, a recorded output",
            path: DATA,
            line: "",
            verdict: Verdict::OutOfScope("a recorded patch, log or output quotes an image; it is not a build file"),
            fixture: ("audits/fix.patch", "+COPY --from=busybox:latest /x /x\n"),
        },
        Shape {
            name: "an image variable a shell script assigns",
            path: SHELL,
            line: r"\b[A-Z_]*IMAGE[A-Z_]*=",
            verdict: Verdict::Covered,
            fixture: (
                "scripts/dev/linux_test.sh",
                "#!/usr/bin/env bash\nIMAGE=rust:1\ndocker_run() { :; }\n",
            ),
        },
        Shape {
            name: "a docker/podman run, pull or create in a shell script",
            path: SHELL,
            line: r"(?i)\b(docker|podman)\s+(run|pull|create)\b",
            verdict: Verdict::Covered,
            fixture: (
                "scripts/test.sh",
                "#!/bin/sh\ndocker run --rm \\\n  -v \"$PWD:/w\" rust:1.99.1 cargo test\n",
            ),
        },
        Shape {
            name: "a script known by its shebang alone",
            path: r"(^|/)[^./]+$",
            line: r"(?i)\b(docker|podman)\s+(run|pull|create)\b|\b[A-Z_]*IMAGE[A-Z_]*=",
            verdict: Verdict::Covered,
            fixture: ("bin/ci", "#!/bin/bash\nif ! docker pull alpine:3.20.3; then exit 1; fi\n"),
        },
        Shape {
            name: "a Makefile's recipe or assignment",
            path: r"(?i)(^|/)(gnu)?makefile$|\.mk$",
            line: "",
            verdict: Verdict::Covered,
            fixture: ("Makefile", "IMAGE=rust:1.99.1\ntest:\n\tdocker run --rm rust:1 cargo test\n"),
        },
        Shape {
            name: "a command or assignment quoted as text (a grep pattern, an echo)",
            path: "",
            line: r#"['"]\^?\s*((docker|podman)\s+(run|pull|create)\b|[A-Z_]*IMAGE[A-Z_]*=)"#,
            verdict: Verdict::OutOfScope(
                "a quoted string is one word a test greps for or a message prints; nothing runs it",
            ),
            fixture: (
                "tests/test_linux.sh",
                "#!/bin/sh\ngrep -q '^docker run$' calls\ngrep -Eq '^IMAGE=rust:[0-9]+$' f\n",
            ),
        },
        Shape {
            name: "compose YAML a shell heredoc or string writes",
            path: SHELL,
            line: r"^\s*(-\s+)?image:",
            verdict: Verdict::OutOfScope(
                "every estate instance (13, 2026-11-15) is a test's fixture a parser reads; a compose \
                 file a host runs is tracked YAML, read as YAML",
            ),
            fixture: (
                "tests/fixtures/gate_host.sh",
                "#!/bin/sh\ncat > compose.yml <<'EOF'\nservices:\n  vpn:\n    image: qmcgaw/gluetun:latest\nEOF\n",
            ),
        },
        Shape {
            name: "a JSON record or config",
            path: r"(?i)\.(json|jsonl|ndjson)$",
            line: "",
            verdict: Verdict::OutOfScope(
                "a recorded call or a permission list quotes a command; it is data, not a script",
            ),
            fixture: (
                ".claude/settings.local.json",
                "{\"allow\": [\"Bash(docker run --rm owntone/owntone-server:latest cat x)\"]}\n",
            ),
        },
    ],
};
