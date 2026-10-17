//! A container image is pinned to one build, never to a moving tag (`goh moving-tags`).
//!
//! The house rule (2026-10-06): "latest" means the newest RELEASE, never a moving tag. A tag its
//! maintainer re-points -- `:latest`, `:stable`, `:main`, `:nightly`, or no tag at all -- makes the
//! same file build or run different software on different days with nothing in the diff saying
//! so. Adv360-Pro-ZMK found it the hard way: its Dockerfile and both CI jobs read
//! `zmkfirmware/zmk-build-arm:stable`, so a CI run and a local build could use different
//! toolchains (P0.2, pinned to a dated tag plus digest on 2026-10-17). This check refuses the
//! class in every repo: a Dockerfile/Containerfile `FROM` or `COPY --from=`, and every YAML
//! `image:` (compose, workflow containers and services, Kubernetes), a workflow's
//! `container:` and `uses: docker://`. `reference.rs` says what moves; `scan.rs` reads a file.
//!
//! NOT READ: shell scripts and Makefiles. There an image is an argument whose position depends on
//! the CLI's flag grammar (`docker run -v "$d:/v" -p 80:80 img cmd`), a volume or port spec has
//! exactly the `a:b` shape of `name:tag`, most references arrive through a variable, and a name a
//! script BUILDS (`apes/ticket-service:dev`) looks like one it pulls. A line read would invent
//! findings or miss most references, so this check does not claim them.
//!
//! WIRING: a structural step in EVERY repo, a hard gate at both scopes. No `--exclude`, like
//! `goh tap-writes`: what a build pulls is not a style a path can be exempt from, vendored or
//! not; the one escape is the reasoned per-reference `# moving-tag-ok: <reason>` (the home-server
//! fleet floats its images by owner ruling, servers DECISIONS #9, and says so on each image).

pub mod dockerfile;
pub mod reference;
pub mod scan;
pub mod yaml;

use goh_tui::{ERR, OK};
use std::fmt::Write as _;
use std::path::Path;

use scan::Problem;

const TAG: &str = "[moving_tags]";

fn describe(p: &Problem) -> String {
    match p {
        Problem::NoTag(r) => {
            format!("`{r}` has no tag -- it pulls whatever `latest` points at on the day it runs")
        }
        Problem::Moving(r, tag) => format!(
            "`{r}` is on the moving tag `{tag}` -- its maintainer re-points it, so the same file \
             pulls different software on different days"
        ),
        Problem::EmptyReason => format!("`# {}` with no reason", scan::MARKER),
        Problem::StaleMarker => format!(
            "`# {}` with no moving image under it -- it exempts nothing; delete it",
            scan::MARKER
        ),
    }
}

#[derive(Default)]
struct Tally {
    refs: usize,
    digests: usize,
    exempt: usize,
    unreadable: usize,
    unparsed: Vec<String>,
}

impl Tally {
    fn summary(&self) -> String {
        let mut s = format!(
            "{} image reference(s): {} pinned by digest, {} exempted with a reason, {} carried by a \
             variable it cannot resolve",
            self.refs, self.digests, self.exempt, self.unreadable
        );
        if let Some(first) = self.unparsed.first() {
            let more = self.unparsed.len() - 1;
            let _ = write!(
                s,
                "; {} file(s) not YAML (templates) read line by line for `image:`, {first}",
                self.unparsed.len()
            );
            if more > 0 {
                let _ = write!(s, " and {more} more");
            }
        }
        s
    }
}

/// The verdict over `files`: `(exit code, stdout, stderr)`.
fn judge(root: &Path, files: &[String], staged: bool) -> (i32, String, String) {
    let scope = if staged { "staged" } else { "tracked" };
    let mut listing = String::new();
    let mut n = 0usize;
    let mut tally = Tally::default();
    for rel in files.iter().filter(|r| scan::in_scope(r)) {
        let Some(blob) = crate::gitutil::content_bytes(root, rel, staged) else {
            continue;
        };
        // Not UTF-8 is not a Dockerfile or YAML anything reads; there is nothing to judge.
        let Ok(text) = String::from_utf8(blob) else {
            continue;
        };
        let s = scan::scan(rel, &text);
        tally.refs += s.refs;
        tally.digests += s.digests;
        tally.exempt += s.exempt;
        tally.unreadable += s.unreadable;
        if s.unparsed {
            tally.unparsed.push(rel.clone());
        }
        for f in &s.findings {
            n += 1;
            let _ = writeln!(listing, "    {rel}:{}: {}", f.line, describe(&f.problem));
        }
    }
    crate::subject::record(&crate::subject::MOVING_TAGS, tally.refs);
    if n == 0 {
        let ok = format!("{OK} {TAG} OK — {scope}: {}\n", tally.summary());
        return (0, ok, String::new());
    }
    let mut err = format!("{ERR} {TAG} {n} container image(s) on a moving tag ({scope}):\n");
    err.push_str(&listing);
    err.push_str(
        "    Pin each to ONE build: the release tag it is on today plus its digest, read from the\n    \
         registry (`docker buildx imagetools inspect IMAGE:TAG` prints the digest):\n          \
         FROM ghcr.io/owner/tool:1.4.2@sha256:<64 hex>\n    \
         A digest pins whatever the tag says; a tag alone must name a release, not a channel.\n    \
         An image that must float says why: `# moving-tag-ok: <reason>` on the line or in the\n    \
         comment lines directly above it.\n",
    );
    (1, String::new(), err)
}

/// `goh moving-tags [--staged]`.
#[must_use]
pub fn run_command(staged: bool) -> i32 {
    let Some(root) = crate::gitutil::repo_root().map(std::path::PathBuf::from) else {
        goh_tui::eoutln!("{ERR} {TAG} not a git repository");
        return 2;
    };
    let files = match crate::gitutil::listed_files(&root, staged) {
        Ok(f) => f,
        Err(m) => {
            goh_tui::eoutln!("{ERR} {TAG} {m}");
            return 2;
        }
    };
    let (code, out, err) = judge(&root, &files, staged);
    goh_tui::out(&out);
    goh_tui::eout(&err);
    code
}

/// The structural step, in every repo.
#[must_use]
pub fn step(repo: &Path, files: &[String], staged: bool) -> Option<i32> {
    let label = if staged {
        "no container image on a moving tag (staged)"
    } else {
        "no container image on a moving tag"
    };
    let start = crate::step_report::begin(label);
    match judge(repo, files, staged) {
        (0, _, _) => {
            crate::step_report::ok(label, start);
            None
        }
        (code, _, err) => {
            let _ = crate::step_report::fail(
                label,
                &crate::step_report::ported("crates/goh/src/movingtag/mod.rs"),
                &err,
                start,
            );
            Some(code)
        }
    }
}

#[cfg(test)]
mod tests;
