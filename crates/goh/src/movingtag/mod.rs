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
//! SHELL AND MAKE (GOH.MOVINGTAGSH, judged from `crate::grace::OVER_FROM`, 2026-11-23). A shell
//! script and a Makefile's recipes are read too, narrowly (`shell.rs`): an image variable's
//! assignment at the start of a command, and the image argument of `docker`/`podman` `run`,
//! `create` and `pull` read through the CLI's own flag grammar. Before then they are read, so the
//! subject floor counts them, and not judged (GOH.GRACEFWD, `scan::scan_at`). Before
//! GOH.MOVINGTAGSH they were not read at all, and a private server workspace's `linux_test.sh`
//! ran `IMAGE=rust:1` unseen (SRV.RUSTTAG). A name the script builds (`docker build --tag "$IMAGE"`) pulls nothing, and an option the reader
//! does not know leaves the reference unreadable rather than guessed. Not read: compose YAML a
//! script writes in a heredoc or a string (every estate instance on 2026-11-15 was a test's
//! fixture), and an image a program's source holds (`crate::shapes::table` names both).
//!
//! RELEASE LINES (GOH.MOVINGTAGCOARSE, judged from `crate::grace::OVER_FROM`, 2026-11-23; owner
//! ruling 2026-11-15, "Yes, flag them"). A version coarser than a full `x.y.z` -- `rust:1`,
//! `node:22`, `alpine:3`, `ubuntu:24.04` -- is re-pointed at every release of its line, so it is a
//! moving tag unless a date or a commit id beside it names one build (`reference.rs`, D-0007).
//!
//! WIRING: a structural step in EVERY repo, a hard gate at both scopes. No `--exclude`, like
//! `goh tap-writes`: what a build pulls is not a style a path can be exempt from, vendored or
//! not; the one escape is the reasoned per-reference `# moving-tag-ok: <reason>` (the home-server
//! fleet floats its images by owner ruling, servers DECISIONS #9, and says so on each image).
//!
//! THE TWO PINS NOTHING CAN MOVE (2026-10-21). A digest and a commit id. The house names them
//! apart, because a gate that cannot EXPRESS a pin cannot accept one: Adv360-Pro-ZMK's base-pin
//! row (P1.3) is blocked on its own gate being able to say "this is pinned to a commit", and the
//! branch that said it was unreachable -- the check went red before it got there. So
//! `reference.rs` judges a commit id as its own verdict, and the report names it. What the house
//! does NOT do is rule which pin a repository must use: a commit pin cannot move, so a build is
//! reproducible, but it does not follow its upstream either -- every upstream fix waits for
//! someone to move the pin, and a branch follows its upstream and can move under the build. That
//! is the owner's ruling, not the gate's.
//
// `goh moving-tags`: A container image pinned to a tag its maintainer re-points, or to no tag: a
// Dockerfile/Containerfile `FROM` or `COPY --from=` (`ARG` defaults substituted; a stage name, a
// stage index and `scratch` are not pulls), and in every `*.yml`/`*.yaml` (read through `saphyr`,
// so script text in a `run: |` block is not a key) an `image:` -- compose, workflow
// `container.image` and `services.*.image`, Kubernetes -- plus a workflow's `container:` and any
// `uses: docker://`. Compose's `${VAR:-default}` is judged by its default. Passes: a digest
// (`@sha256:...`, whatever the tag says), a tag that names one build, or a COMMIT ID
// (`:f93290b3ab1a`: the other pin nothing can move -- nobody can re-point a build's own commit; the
// house names the two apart because a gate that cannot EXPRESS a pin cannot accept one, which is
// what blocked Adv360-Pro-ZMK's base-pin row, P1.3. A commit pin cannot move, so a build is
// reproducible, but it does not follow its upstream either: every upstream fix waits for someone to
// move the pin. The house does NOT rule which pin a repository must use -- the owner does). Moving:
// a tag with no digit (`latest`, `stable`, `alpine`, `bookworm`: a channel, never a release), or a
// digit-bearing tag spelling a channel word as a part (`3.5-branch`, `v2-latest`, `1.2-dev`) with
// no date, full `x.y.z` or commit id beside it. From 2026-11-23 (GOH.MOVINGTAGCOARSE, owner ruling
// 2026-11-15, D-0007) also a RELEASE LINE: any digit-bearing tag with no date, full `x.y.z` or
// commit id (`rust:1`, `node:22`, `alpine:3`, `redis:7.4`, `postgres:18-alpine`), distro versions
// included (`ubuntu:24.04`, `debian:13.1`: pin the dated tag or a digest); the report names it as a
// release line. Not a reference: an `image:` beside `build:` (compose names what it builds), a
// path, URL, picture or an action's local `Dockerfile`. Adv360-Pro-ZMK's `:stable` build image
// (P0.2) is the incident row. One escape, **`# moving-tag-ok: <reason>`** on the line or the
// comment lines directly above; an empty reason and a marker with no moving image under it are
// findings. Every repo, both scopes, a hard gate; no path exemption. Floor
// `GOH_MOVING_TAGS_MIN_REFS` (references read). Shell scripts (by name or shebang) and Makefiles
// are judged narrowly from 2026-11-23 (`movingtag/shell.rs`, GOH.MOVINGTAGSH), and read before it
// so `GOH_MOVING_TAGS_MIN_REFS` counts them on both sides; a reasoned marker over an image that
// moves only from that day (a script's, or a release line) is accepted before it, never stale
// (GOH.GRACEFWD): an image variable's assignment at a command's start and the image argument of
// `docker`/`podman` `run`/`create`/`pull` past the CLI's flag grammar (an unknown short flag is
// unreadable, never guessed); a name the script builds pulls nothing. Lies: compose YAML a script
// writes in a heredoc is not read; a full `x.y.z` tag its maintainer rebuilds in place passes (only
// a digest is immune); a Helm values `repository:`/`tag:` pair is not joined; a reference in an
// unresolved variable is counted as unreadable, not judged. Pinned by `test_check_moving_tags.py`,
// the tables in `movingtag/tests.rs`.

pub mod dockerfile;
pub mod reference;
pub mod scan;
pub mod shapes;
pub mod shell;
pub mod shlex;
pub mod yaml;

use goh_tui::{ERR, OK};
use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::path::Path;

use scan::Problem;

const TAG: &str = "[moving_tags]";

fn describe(p: &Problem) -> String {
    match p {
        Problem::NoTag(r) => {
            format!("`{r}` has no tag -- it pulls whatever `latest` points at on the day it runs")
        }
        Problem::Moving(r, tag) if !reference::is_channel(tag) => format!(
            "`{r}` is on `{tag}`, a release line coarser than `x.y.z` -- it is re-pointed at every \
             release of the line, so the same file pulls different software on different days"
        ),
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
    commits: usize,
    exempt: usize,
    unreadable: usize,
    unparsed: Vec<String>,
}

impl Tally {
    fn summary(&self) -> String {
        let mut s = format!(
            "{} image {n_reference}: {} pinned by digest, {} pinned by commit, {} exempted with a \
             reason, {} carried by a variable it cannot resolve",
            self.refs,
            self.digests,
            self.commits,
            self.exempt,
            self.unreadable,
            n_reference = goh_tui::noun(self.refs, "reference")
        );
        if let Some(first) = self.unparsed.first() {
            let more = self.unparsed.len() - 1;
            let _ = write!(
                s,
                "; {} {n_file} not YAML (templates) read line by line for `image:`, {first}",
                self.unparsed.len(),
                n_file = goh_tui::noun(self.unparsed.len(), "file")
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
    judge_at(root, files, staged, crate::grace::over())
}

/// [`judge`] with the grace decided: before it, a shell script or Makefile is read (the subject
/// floor counts it) but not judged, and a release line (`rust:1`) passes (`scan::scan_at`).
fn judge_at(root: &Path, files: &[String], staged: bool, over: bool) -> (i32, String, String) {
    let scope = if staged { "staged" } else { "tracked" };
    let mut found = crate::estate_seed::Sites::new();
    let mut day = crate::estate_seed::Sites::new();
    let mut tally = Tally::default();
    for rel in files.iter().filter(|r| scan::maybe_in_scope(r)) {
        let Some(blob) = crate::gitutil::content_bytes(root, rel, staged) else {
            continue;
        };
        // Not UTF-8 is not a Dockerfile, YAML or script anything reads; there is nothing to judge.
        let Ok(text) = String::from_utf8(blob) else {
            continue;
        };
        if scan::kind(rel, &text).is_none() {
            continue;
        }
        let s = scan::scan_at(rel, &text, over);
        tally.refs += s.refs;
        tally.digests += s.digests;
        tally.commits += s.commits;
        tally.exempt += s.exempt;
        tally.unreadable += s.unreadable;
        if s.unparsed {
            tally.unparsed.push(rel.clone());
        }
        let row = |f: &scan::Finding| format!("    {rel}:{}: {}", f.line, describe(&f.problem));
        if !s.findings.is_empty() {
            found.insert(rel.clone(), s.findings.iter().map(row).collect());
        }
        if !over {
            let ahead = scan::scan_at(rel, &text, true).findings;
            if !ahead.is_empty() {
                day.insert(rel.clone(), ahead.iter().map(row).collect());
            }
        }
    }
    crate::subject::record(&crate::subject::MOVING_TAGS, tally.refs);
    // A release line and a script's image are judged from the grace's end: shown now. No seed
    // here (`# moving-tag-ok:` is the exemption), so every one the day finds is named.
    let warn = if over {
        String::new()
    } else {
        crate::grace::ahead::shown(TAG, found.clone(), &day, &BTreeMap::new()).warn
    };
    let n: usize = found.values().map(Vec::len).sum();
    let listing = found.values().flatten().fold(String::new(), |mut acc, l| {
        acc.push_str(l);
        acc.push('\n');
        acc
    });
    if n == 0 {
        let ok = format!("{OK} {TAG} OK — {scope}: {}\n", tally.summary());
        return (0, ok, warn);
    }
    let mut err = format!(
        "{ERR} {TAG} {n} container {n_image} on a moving tag ({scope}):\n",
        n_image = goh_tui::noun(n, "image")
    );
    err.push_str(&listing);
    err.push_str(
        "    Pin each to ONE build: the release tag it is on today plus its digest, read from the\n    \
         registry (`docker buildx imagetools inspect IMAGE:TAG` prints the digest):\n          \
         FROM ghcr.io/owner/tool:1.4.2@sha256:<64 hex>\n    \
         A digest pins whatever the tag says; a tag alone must name ONE build -- a full `x.y.z`\n    \
         release, a date or a commit id -- not a channel or a release line (`rust:1`, `node:22`,\n    \
         `ubuntu:24.04`: docs/decisions/D-0007).\n    \
         An image that must float says why: `# moving-tag-ok: <reason>` on the line or in the\n    \
         comment lines directly above it.\n",
    );
    err.push_str(
        "    A COMMIT PIN is the other pin nothing can move: `:f93290b3ab1a` names one build, and\n    \
         nobody can re-point it. What it does not do is follow its upstream: every upstream fix\n    \
         waits for someone to move the pin, and a build that floats waits for nobody. The house\n    \
         names the two apart and does NOT rule which one a repository must use -- the owner\n    \
         does.\n",
    );
    (1, String::new(), format!("{warn}{err}"))
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
