//! The check's specification as a table: every direct write to a Homebrew tap RED on the line
//! named, every read, every gated bump and every reasoned exemption GREEN.
//!
//! The first two rows are the incidents' shapes, recovered from git (2026-10-17): antiknob's
//! release wrote its cask with `gh api -X PUT repos/${TAP}/contents/...` (`4e6092b^:release.sh`),
//! and `CadGoose`'s release workflow cloned the tap with a PAT and pushed from the clone
//! (`84a40b4^:.github/workflows/build_and_release.yml`). Both skipped the tap's own gate.

use super::scan::{findings, in_scope, Problem, Scanner};

/// Problem kinds without payloads, so a row says WHAT, not the wording.
#[derive(Debug, PartialEq, Eq)]
enum K {
    Api,
    ClonePush(usize),
    Empty,
    Stale,
}

fn kinds(src: &str) -> Vec<(usize, K)> {
    findings(&Scanner::compile().unwrap(), src)
        .into_iter()
        .map(|f| {
            let k = match f.problem {
                Problem::ApiWrite => K::Api,
                Problem::PushFromClone { clone_line } => K::ClonePush(clone_line),
                Problem::EmptyReason => K::Empty,
                Problem::StaleMarker => K::Stale,
            };
            (f.line, k)
        })
        .collect()
}

/// `(source, the findings by line)`.
const RED: &[(&str, &[(usize, K)])] = &[
    // antiknob, 4e6092b^:release.sh -- the repo arrives through a variable.
    (
        "TAP=\"ztomer/homebrew-tap\"\nCASK_PATH=\"Casks/antiknob.rb\"\n\
         gh api -X PUT \"repos/${TAP}/contents/${CASK_PATH}\" \\\n  -f message=\"antiknob ${VER}\" \\\n  \
         -f sha=\"${CUR_SHA}\" >/dev/null\n",
        &[(3, K::Api)],
    ),
    // CadGoose, 84a40b4^:.github/workflows/build_and_release.yml -- clone with a token, push.
    (
        "jobs:\n  mac:\n    steps:\n      - name: Update Homebrew Cask in Tap\n        run: |\n          \
         git -c \"$TAP_HEADER\" clone https://github.com/ztomer/homebrew-tap.git /tmp/homebrew-tap\n          \
         cd /tmp/homebrew-tap\n          perl -pi -e \"s/version/v/\" Casks/cadgoose.rb\n          \
         git commit -m \"Update CadGoose cask\"\n          git -c \"$TAP_HEADER\" push origin main\n",
        &[(10, K::ClonePush(6))],
    ),
    // No -X, but fields: `gh api` sends a POST.
    ("gh api repos/o/homebrew-tap/contents/Formula/x.rb -f message=m -f content=c\n", &[(1, K::Api)]),
    ("gh api --method=PATCH repos/o/homebrew-tap/contents/Formula/x.rb --input body.json\n", &[(1, K::Api)]),
    ("gh api --method DELETE repos/o/homebrew-x/contents/Casks/y.rb\n", &[(1, K::Api)]),
    ("curl -X PUT https://api.github.com/repos/o/homebrew-tap/contents/Casks/y.rb -d @b.json\n", &[(1, K::Api)]),
    // `${VAR}` in braces, `$VAR` bare, and a default expansion that names the tap.
    ("T=${T:-o/homebrew-tap}\ngh api -X PUT repos/$T/contents/x\n", &[(2, K::Api)]),
    // Python: a list-form call, and an f-string path.
    (
        "TAP = \"o/homebrew-tap\"\nsubprocess.run([\"gh\", \"api\", \"-X\", \"PUT\", f\"repos/{TAP}/contents/x\"], check=True)\n",
        &[(2, K::Api)],
    ),
    ("gh repo clone o/homebrew-tap tap\ngit -C tap push\n", &[(2, K::ClonePush(1))]),
    // A marker without a reason exempts nothing and says so.
    ("# tap-write-ok:\ngh api -X PUT repos/o/homebrew-tap/contents/x\n", &[(2, K::Empty)]),
    // A marker with nothing under it is stale.
    ("# tap-write-ok: a reason\necho hello\n", &[(1, K::Stale)]),
];

const GREEN: &[&str] = &[
    // CadGoose's own verification read, before the migration: contents read, no write.
    "TAP_REPO=\"ztomer/homebrew-tap\"\nCASK_RAW=$(gh api \"repos/$TAP_REPO/contents/$TAP_FILE\" --jq '.content')\n",
    "gh api -X GET repos/o/homebrew-tap/contents/x\n",
    // A comment that SHOWS the old shape is not the old shape.
    "# was: gh api -X PUT repos/o/homebrew-tap/contents/x\n",
    // The gated route.
    "\"$GOH_DIR/tools/release-kit/tap_bump.sh\" --tap ztomer/homebrew-tap --cask antiknob --tag v1 --artifact d.dmg\n",
    // A write to some other repo's contents is not a tap write.
    "gh api -X PUT repos/o/website/contents/index.html -f content=c\n",
    // Cloning the tap to read it, and pushing an unrelated repo with no tap clone before it.
    "git clone https://github.com/o/homebrew-tap.git t\nbrew style t/Casks/x.rb\n",
    "git push origin main\n",
    // A reasoned exemption on the line above, and inline.
    "# tap-write-ok: the tap's own migration tool, gated by the tap\ngh api -X PUT repos/o/homebrew-tap/contents/x\n",
    "gh api -X PUT repos/o/homebrew-tap/contents/x  # tap-write-ok: one-off repair, reviewed\n",
    // A tap var that never reaches a write.
    "TAP=o/homebrew-tap\necho \"$TAP\"\n",
];

#[test]
fn every_direct_tap_write_is_red_on_its_line() {
    for (src, want) in RED {
        assert_eq!(kinds(src), *want, "source:\n{src}");
    }
}

#[test]
fn reads_gated_bumps_and_reasoned_exemptions_are_green() {
    for src in GREEN {
        assert_eq!(kinds(src), [], "source:\n{src}");
    }
}

#[test]
fn scope_is_scripts_and_workflows() {
    for rel in [
        "release.sh",
        "tools/bump.py",
        ".github/workflows/release.yml",
        "x/action.yaml",
        "Makefile",
        "rules.mk",
        "Justfile",
    ] {
        assert!(in_scope(rel, b""), "{rel} should be read");
    }
    assert!(
        in_scope("bin/release", b"#!/usr/bin/env bash\n"),
        "a shebang script is read"
    );
    for rel in ["README.md", "src/main.rs", "Casks/x.rb", "data.json"] {
        assert!(!in_scope(rel, b""), "{rel} should not be read");
    }
    assert!(
        !in_scope("bin/blob", b"\x7fELF"),
        "an extensionless binary is not read"
    );
}
