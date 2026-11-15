//! The shell reader's specification: what it reads as an image, on which lines, and what it
//! leaves alone. The incident row is `media_server`'s `scripts/dev/linux_test.sh` before 09cdcd89
//! (SRV.RUSTTAG), and its pin after.

use super::refs;
use crate::movingtag::scan::{scan_at, Problem};

const DIGEST: &str = "sha256:8a4e90c7946b74626488b1446f712c0fe0ed1a596cbd3d871684bc9c5433ae07";

/// `(start, end, raw)` of every reference.
fn read(text: &str, make: bool) -> Vec<(usize, usize, String)> {
    refs(text, make)
        .into_iter()
        .map(|r| (r.start, r.end, r.raw))
        .collect()
}

fn moving(rel: &str, text: &str) -> Vec<usize> {
    scan_at(rel, text, true)
        .findings
        .into_iter()
        .filter(|f| matches!(f.problem, Problem::Moving(..) | Problem::NoTag(_)))
        .map(|f| f.line)
        .collect()
}

#[test]
fn a_planted_moving_tag_in_a_script_is_red_and_its_pin_is_green() {
    let planted = "#!/usr/bin/env bash\nset -euo pipefail\ndocker run --rm -v \"$v:/v\" rust:latest cargo test\n";
    assert_eq!(moving("scripts/dev/linux_test.sh", planted), [3]);
    let pinned = format!(
        "#!/usr/bin/env bash\ndocker run --rm -v \"$v:/v\" rust:1.99.1@{DIGEST} cargo test\n"
    );
    assert_eq!(
        moving("scripts/dev/linux_test.sh", &pinned),
        Vec::<usize>::new()
    );
    // By shebang alone, with no extension.
    assert_eq!(moving("bin/ci", "#!/bin/sh\ndocker pull alpine\n"), [2]);
    // Not a shell script: nothing read.
    assert_eq!(
        moving("bin/ci", "#!/usr/bin/env python3\ndocker pull alpine\n"),
        Vec::<usize>::new()
    );
}

#[test]
fn the_incident_script_is_read_at_its_assignment_and_its_runs() {
    let before = "#!/usr/bin/env bash\nIMAGE=rust:latest\nvol=t\ndocker run --rm -v \"$vol:/v\" \"$IMAGE\" chown \"$uid\" /v\ndocker run --rm \\\n    -v \"$vol:/v\" -w /w \\\n    \"$IMAGE\" cargo test\n";
    // The assignment is judged; each run of the variable is the same image, judged once, there.
    assert_eq!(
        read(before, false),
        [
            (2, 2, "rust:latest".to_owned()),
            (4, 4, String::new()),
            (5, 7, String::new()),
        ]
    );
    assert_eq!(moving("linux_test.sh", before), [2]);
    let after = before.replace("rust:latest", &format!("rust:1.99.1@{DIGEST}"));
    assert_eq!(moving("linux_test.sh", &after), Vec::<usize>::new());
}

#[test]
fn defaults_and_earlier_assignments_resolve() {
    let src = "BASE_TAG=\"${LINT_BASE_TAG:-1.100.0-trixie}\"\nBASE_IMAGE=\"${LINT_BASE_IMAGE:-rust:$BASE_TAG}\"\nexport AGENT_IMAGE=node:${NODE:-latest}\nlocal X_IMAGE=$(pick_image)\n";
    assert_eq!(
        read(src, false),
        [
            (2, 2, "rust:1.100.0-trixie".to_owned()),
            (3, 3, "node:latest".to_owned()),
            (4, 4, String::new()),
        ]
    );
}

#[test]
fn a_name_the_script_builds_pulls_nothing() {
    // Adv360-Pro-ZMK's tools/check_keymap_build.sh and divoom-control's docker commit.
    let src = "IMAGE_TAG=\"adv360-zmk-gate\"\n\"$docker\" build --quiet --tag \"$IMAGE_TAG\" .\ndocker run --rm \"$IMAGE_TAG\" true\nIMAGE=lint:1\ndocker commit \"$CTR\" \"$IMAGE\"\ndocker build -t app .\ndocker run --rm app make\n";
    assert_eq!(moving("t.sh", src), Vec::<usize>::new());
    // A path is a file an image variable names, not a registry reference (old_phones'
    // update-binary).
    let paths = "FLASH_IMAGE=$(which flash_image)\n[ -e \"$FLASH_IMAGE\" ] || FLASH_IMAGE=/sbin/flash_image\nIMG_IMAGE=./out/boot.img\n";
    assert_eq!(moving("update-binary.sh", paths), Vec::<usize>::new());
    // `"$docker"` runs docker: a variable named for it is the tool.
    let r = read(src, false);
    assert!(r.iter().all(|(_, _, raw)| raw.is_empty()), "{r:?}");
    assert_eq!(r.len(), 4, "two assignments and two runs, each read: {r:?}");
}

#[test]
fn quoted_text_comments_and_heredocs_are_not_commands() {
    let src = "grep -q '^docker run$' \"$DIR/calls\"\ngrep -Eq '^IMAGE=rust:[0-9]+$' f\necho \"docker pull nginx\"\n# docker run nginx:latest\ncat > c.yml <<'YAML'\nservices:\n  a:\n    image: nginx:latest\nIMAGE=nginx:latest\nYAML\nx=1 # IMAGE=y\n";
    assert_eq!(read(src, false), []);
}

#[test]
fn the_flag_grammar_is_the_clis_and_an_unknown_flag_is_unreadable() {
    let src = "docker run -it --name=a --network host -e A=1 -v/x:/y -p 80:80 alpine:3.20.3 sh\npodman container run --rm -d nginx:latest\ndocker run -Z odd image:latest cmd\nsudo docker pull --platform linux/amd64 -q busybox\nX=$(docker create redis:latest)\ndocker run --rm img:1 2>&1 | tee log\n";
    let r = read(src, false);
    assert_eq!(r[0].2, "alpine:3.20.3");
    assert_eq!(r[1].2, "nginx:latest");
    assert!(
        r[2].2.contains("{{"),
        "an unknown short flag is unreadable: {r:?}"
    );
    assert_eq!(r[3].2, "busybox");
    assert_eq!(r[4].2, "redis:latest");
    assert_eq!(r[5].2, "img:1");
    // `img:1` is a release line (GOH.MOVINGTAGCOARSE).
    assert_eq!(moving("t.sh", src), [2, 4, 5, 6]);
}

#[test]
fn the_escape_reads_in_a_script() {
    let src = "# moving-tag-ok: the fixture plants it on purpose\ndocker run nginx:latest\ndocker run redis:latest # moving-tag-ok: DECISIONS #9\n";
    let s = scan_at("t.sh", src, true);
    assert_eq!((s.findings.len(), s.exempt), (0, 2), "{:?}", s.findings);
}

#[test]
fn a_makefile_is_its_recipes_and_its_assignments() {
    let src = "IMAGE ?= rust:latest\nTAG := 1.2.3\ntest:\n\t@docker run --rm $(IMAGE) cargo test\n\tdocker pull redis:$(TAG)\n\t(cd x && docker run --rm $$(cat img) true)\nnotes: ## docker run nginx:latest\n";
    let r = read(src, true);
    assert_eq!(r[0], (1, 1, "rust:latest".to_owned()));
    assert_eq!(
        r[1],
        (4, 4, String::new()),
        "the variable is judged where assigned"
    );
    assert_eq!(r[2], (5, 5, "redis:1.2.3".to_owned()));
    assert_eq!(r.len(), 3, "a command substitution is no image: {r:?}");
    assert_eq!(moving("Makefile", src), [1]);
}

#[test]
fn the_grace_decides_whether_a_script_is_judged() {
    let repo = goh_testkit::repo_with(&[("ci.sh", "#!/bin/sh\ndocker pull alpine:latest\n")])
        .expect("fixture");
    let files = vec!["ci.sh".to_owned()];
    let (code, out, _) = crate::movingtag::judge_at(repo.path(), &files, false, false);
    assert_eq!(code, 0, "within the grace a script is not read: {out}");
    let (code, _, err) = crate::movingtag::judge_at(repo.path(), &files, false, true);
    assert_eq!(code, 1, "{err}");
    assert!(err.contains("ci.sh:2:"), "{err}");
}
