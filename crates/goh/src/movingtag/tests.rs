//! The check's specification as tables: every reference on a moving tag RED on the line named,
//! every digest, released tag, unreadable variable and reasoned exemption GREEN.
//!
//! The incident row is Adv360-Pro-ZMK's build image before its P0.2 (2026-10-17): the Dockerfile
//! and both CI jobs read `zmkfirmware/zmk-build-arm:stable`, so a CI run and a local build could
//! use different toolchains with nothing saying so; and its pin after, tag plus digest.

use super::reference::{is_moving_tag, substitute, verdict, Verdict};
use super::scan::{in_scope, scan, Problem};
use std::collections::BTreeMap;

const DIGEST: &str = "sha256:840526f03c19c286b614d5cb97b11242ae19e468fc2ef2f095f6cf0465fa1c5b";

#[test]
fn every_channel_is_moving_and_every_build_name_is_not() {
    for tag in [
        "latest",
        "LATEST",
        "stable",
        "main",
        "master",
        "edge",
        "nightly",
        "dev",
        "develop",
        "rolling",
        "canary",
        "alpine",
        "bookworm",
        "lts",
        "mainline",
        "3.5-branch",
        "v2-latest",
        "1.2-dev",
        "22-stable",
        "latest-alpine",
    ] {
        assert!(is_moving_tag(tag), "{tag} should be moving");
    }
    for tag in [
        "3.20.3",
        "18-alpine",
        "pg16",
        "1",
        "v3.40.0",
        "3.5-branch-20250930025731-3.5.0-0.16.3-f93290b3ab1a-18117245422",
        "nightly-2025-01-01",
        "nightly-20250101",
        "1.2.3-dev",
        "dev-a1b2c3d",
        "2.47.1",
        "sha-4f9e2c1",
    ] {
        assert!(!is_moving_tag(tag), "{tag} should name one build");
    }
}

#[test]
fn a_reference_is_judged_by_its_digest_then_its_last_component() {
    let rows: &[(&str, Verdict)] = &[
        ("ubuntu", Verdict::NoTag),
        ("ghcr.io/linuxserver/calibre", Verdict::NoTag),
        // A registry port is not a tag.
        ("localhost:5000/app", Verdict::NoTag),
        (
            "localhost:5000/app:latest",
            Verdict::Moving("latest".into()),
        ),
        ("localhost:5000/app:1.2.3", Verdict::Released),
        ("nginx:stable", Verdict::Moving("stable".into())),
        ("\"traefik:latest\"", Verdict::Moving("latest".into())),
        ("docker://alpine:edge", Verdict::Moving("edge".into())),
        ("docker://alpine", Verdict::NoTag),
        ("alpine:3.20.3", Verdict::Released),
        // A digest pins whatever the tag says.
        (
            &format!("zmkfirmware/zmk-build-arm:stable@{DIGEST}"),
            Verdict::Digest,
        ),
        (&format!("alpine@{DIGEST}"), Verdict::Digest),
        ("scratch", Verdict::NotAReference),
        ("", Verdict::NotAReference),
        ("${IMAGE}", Verdict::Unreadable),
        ("node:${TAG}", Verdict::Unreadable),
        ("${{ matrix.image }}", Verdict::Unreadable),
        ("{{ .Values.image }}", Verdict::Unreadable),
        // A variable in the registry leaves the tag readable: none here.
        ("${REGISTRY}/app", Verdict::NoTag),
        ("${REGISTRY}/app:latest", Verdict::Moving("latest".into())),
    ];
    for (raw, want) in rows {
        assert_eq!(verdict(raw), *want, "{raw}");
    }
}

#[test]
fn defaults_stand_in_for_unset_names() {
    let mut vars = BTreeMap::new();
    vars.insert("TAG".to_owned(), "1.2.3".to_owned());
    for (src, want) in [
        ("node:${TAG}", "node:1.2.3"),
        ("node:$TAG", "node:1.2.3"),
        (concat!("nginx:${NGINX", ":-stable}"), "nginx:stable"),
        ("nginx:${NGINX-1.27.2}", "nginx:1.27.2"),
        ("${IMG}", "${IMG}"),
        ("${{ matrix.x }}", "${{ matrix.x }}"),
        ("a$$b", "a$$b"),
    ] {
        assert_eq!(substitute(src, &vars), want, "{src}");
    }
}

/// Problem kinds without payloads, so a row says WHAT, not the wording.
#[derive(Debug, PartialEq, Eq)]
enum K {
    NoTag,
    Moving,
    Empty,
    Stale,
}

fn kinds(rel: &str, src: &str) -> Vec<(usize, K)> {
    scan(rel, src)
        .findings
        .into_iter()
        .map(|f| {
            let k = match f.problem {
                Problem::NoTag(_) => K::NoTag,
                Problem::Moving(..) => K::Moving,
                Problem::EmptyReason => K::Empty,
                Problem::StaleMarker => K::Stale,
            };
            (f.line, k)
        })
        .collect()
}

/// `(path, source, the findings by line)`.
type Row = (&'static str, &'static str, &'static [(usize, K)]);

const RED: &[Row] = &[
    // Adv360-Pro-ZMK before P0.2: the Dockerfile and a CI job on `:stable`.
    ("Dockerfile", "FROM zmkfirmware/zmk-build-arm:stable\nWORKDIR /app\n", &[(1, K::Moving)]),
    (
        ".github/workflows/build.yml",
        "jobs:\n  build:\n    runs-on: ubuntu-latest\n    container:\n      image: zmkfirmware/zmk-build-arm:stable\n",
        &[(5, K::Moving)],
    ),
    ("Dockerfile", "FROM ubuntu\n", &[(1, K::NoTag)]),
    ("x/Containerfile", "from docker.io/library/debian:bookworm\n", &[(1, K::Moving)]),
    (
        "build.Dockerfile",
        "FROM --platform=$BUILDPLATFORM golang:latest AS build\nFROM build\n",
        &[(1, K::Moving)],
    ),
    // An ARG default is what FROM pulls.
    ("Dockerfile", "ARG BASE=node:latest\nFROM $BASE\n", &[(2, K::Moving)]),
    ("Dockerfile", "FROM \\\n  ubuntu:latest\n", &[(1, K::Moving)]),
    ("Dockerfile", "FROM alpine:3.20.3 AS a\nCOPY --from=nginx:latest /a /b\n", &[(2, K::Moving)]),
    // compose, in a directory no filename rule would know.
    ("compose/media.yml", "services:\n  web:\n    image: nginx:latest\n", &[(3, K::Moving)]),
    ("docker-compose.yml", "services:\n  cal:\n    image: ghcr.io/linuxserver/calibre\n", &[(3, K::NoTag)]),
    ("compose.yaml", "services:\n  web:\n    image: nginx:${NGINX_TAG:-stable}\n", &[(3, K::Moving)]),
    ("compose.yaml", "services:\n  web:\n    image: \"traefik:latest\"\n", &[(3, K::Moving)]),
    (".github/workflows/ci.yml", "jobs:\n  a:\n    container: node:latest\n", &[(3, K::Moving)]),
    (
        ".github/workflows/ci.yml",
        "jobs:\n  a:\n    services:\n      pg:\n        image: postgres\n",
        &[(5, K::NoTag)],
    ),
    (
        ".github/workflows/ci.yml",
        "jobs:\n  a:\n    steps:\n      - uses: docker://alpine:edge\n",
        &[(4, K::Moving)],
    ),
    ("action.yml", "runs:\n  using: docker\n  image: docker://alpine\n", &[(3, K::NoTag)]),
    // Kubernetes, list form.
    ("k8s/pod.yaml", "spec:\n  containers:\n    - name: a\n      image: nginx\n", &[(4, K::NoTag)]),
    // An anchored default reached through an alias is one reference, at its anchor.
    (
        "compose.yml",
        "x-base: &base\n  image: redis:latest\nservices:\n  a: *base\n  b: *base\n",
        &[(2, K::Moving)],
    ),
    // A file the parser refuses (a Helm template) is still read for a literal reference.
    (
        "chart/templates/d.yaml",
        "{{- if .Values.on }}\nspec:\n  containers:\n    - image: nginx:latest\n{{- end }}\n",
        &[(4, K::Moving)],
    ),
    // A marker without a reason exempts nothing and says so; one with nothing under it is stale.
    ("Dockerfile", "# moving-tag-ok:\nFROM ubuntu:latest\n", &[(2, K::Empty)]),
    ("Dockerfile", "# moving-tag-ok: a reason\nFROM ubuntu:24.04\n", &[(1, K::Stale)]),
    (
        "compose.yml",
        "services:\n  a:\n    # moving-tag-ok: a reason\n    image: nginx@sha256:840526f03c19c286b614d5cb97b11242ae19e468fc2ef2f095f6cf0465fa1c5b\n",
        &[(3, K::Stale)],
    ),
];

const GREEN: &[(&str, &str)] = &[
    // Adv360-Pro-ZMK after P0.2: the dated release tag plus its digest.
    (
        "Dockerfile",
        "FROM zmkfirmware/zmk-build-arm:3.5-branch-20250930025731-3.5.0-0.16.3-f93290b3ab1a-18117245422@sha256:840526f03c19c286b614d5cb97b11242ae19e468fc2ef2f095f6cf0465fa1c5b\n",
    ),
    ("Dockerfile", "FROM scratch\nCOPY a /a\n"),
    ("Dockerfile", "FROM golang:1.23.4 AS build\nFROM build\nCOPY --from=build /a /a\nCOPY --from=0 /a /b\n"),
    ("Dockerfile", "ARG TAG=1.2.3\nFROM node:${TAG}\n"),
    // An ARG with no default: unreadable, not a guess.
    ("Dockerfile", "ARG TAG\nFROM node:${TAG}\n"),
    ("Dockerfile", "# FROM ubuntu:latest was the old base\nFROM ubuntu:24.04\n"),
    ("Dockerfile", "# moving-tag-ok: the upstream publishes no release tags\nFROM vendor/tool:latest\n"),
    // compose names the image it BUILDS; it does not pull it.
    ("docker-compose.yml", "services:\n  app:\n    build: .\n    image: me/app:latest\n"),
    ("compose.yml", "services:\n  web:\n    image: ${IMG:-nginx:1.27.2}\n"),
    ("compose.yml", "services:\n  web:\n    image: ${IMG}\n"),
    ("compose.yml", "services:\n  web:\n    image: nginx:latest  # moving-tag-ok: DECISIONS #9\n"),
    ("compose.yml", "services:\n  web:\n    # floats by ruling\n    # moving-tag-ok: DECISIONS #9\n    image: nginx:latest\n"),
    // Script text is not a key.
    (".github/workflows/ci.yml", "jobs:\n  a:\n    steps:\n      - run: |\n          echo 'image: nginx:latest'\n"),
    (".github/workflows/ci.yml", "jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n"),
    (".github/workflows/ci.yml", "jobs:\n  a:\n    container: ${{ matrix.image }}\n"),
    // `container:` means something only in a workflow.
    ("deploy/app.yml", "container: web\n"),
    // Not containers: pictures, paths, URLs, an action's own Dockerfile.
    ("_config.yml", "image: /assets/logo.png\nlogo:\n  image: assets/og.jpg\n"),
    ("mkdocs.yml", "image: https://example.org/x\n"),
    ("action.yml", "runs:\n  using: docker\n  image: Dockerfile\n"),
    ("action.yml", "runs:\n  using: docker\n  image: build/tool\n"),
    // A Helm values pair is joined by a template the check does not run.
    ("chart/values.yaml", "image:\n  repository: nginx\n  tag: latest\n"),
    ("chart/templates/d.yaml", "{{- if .Values.on }}\n    - image: \"{{ .Values.image }}\"\n{{- end }}\n"),
    ("k8s/job.yaml", "spec:\n  containers:\n    - image: alpine/git:2.47.1\n"),
];

#[test]
fn every_moving_reference_is_red_on_its_line() {
    for (rel, src, want) in RED {
        assert_eq!(kinds(rel, src), *want, "{rel}:\n{src}");
    }
}

#[test]
fn digests_releases_unreadables_and_reasoned_exemptions_are_green() {
    for (rel, src) in GREEN {
        assert_eq!(kinds(rel, src), [], "{rel}:\n{src}");
    }
}

#[test]
fn the_scan_counts_its_subject_and_says_what_it_could_not_read() {
    let s = scan(
        "compose.yml",
        "services:\n  a:\n    image: ${IMG}\n  b:\n    image: nginx@sha256:840526f03c19c286b614d5cb97b11242ae19e468fc2ef2f095f6cf0465fa1c5b\n  c:\n    image: n:latest # moving-tag-ok: r\n  d:\n    image: n:1.2.3\n",
    );
    assert_eq!(
        (s.refs, s.unreadable, s.digests, s.exempt, s.unparsed),
        (4, 1, 1, 1, false)
    );
    assert!(scan("t.yaml", "{{- x }}\nimage: a:1\n").unparsed);
}

#[test]
fn scope_is_dockerfiles_and_yaml() {
    for rel in [
        "Dockerfile",
        "app/Dockerfile.dev",
        "x/build.dockerfile",
        "Containerfile",
        "compose/media.yml",
        ".github/workflows/ci.yaml",
        "k8s/pod.YAML",
    ] {
        assert!(in_scope(rel), "{rel} should be read");
    }
    for rel in [
        "README.md",
        "Makefile",
        "build.sh",
        "dockerfile-lint.sh",
        "Cargo.toml",
    ] {
        assert!(!in_scope(rel), "{rel} should not be read");
    }
}
