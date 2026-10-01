"""check_tag_version.py — a pushed `refs/tags/v*` must name what its own commit declares.

media_server, 2026-10-01. A version was bumped, `git commit --amend --no-edit`
was rejected by pre-commit TWICE with `2>/dev/null` swallowing the refusal, and
`git tag -f v1.79.3` then named commit `a266067` — whose VERSION file and all 31
crate manifests still said 1.79.1. `git push --follow-tags` published it. The
repo's own `test_native_version_flag` DID catch the mismatch; nothing tied it to
the TAG.

Every case here is the class, not the instance: the commit, not the working tree,
is the thing read; the refs being PUSHED are the thing scanned; and a tag whose
version source is absent is unverifiable rather than fine.
"""

import subprocess
import sys

import pytest

from conftest import REPO_ROOT, git, run_check, write

sys.path.insert(0, str(REPO_ROOT / "checks"))
import check_tag_version as gate  # noqa: E402

CHECKER = "checks/check_tag_version.py"
ZERO = "0" * 40


def _sha(repo, rev):
    return git(repo, "rev-parse", rev).strip()


def _refs(*pairs):
    return "".join(f"{ref} {sha} {ref} {ZERO}\n" for ref, sha in pairs)


def _push(repo, tmp_path, *pairs, extra_env=None):
    """Drive the real pre-push gate with these refs, as git would."""
    import os

    env = dict(os.environ, GOH_DIR=str(REPO_ROOT),
               GOH_PUSH_LOGS=str(tmp_path / "push-logs"),
               GATE_REPORT=str(tmp_path / "report.txt"), **(extra_env or {}))
    return subprocess.run(["bash", str(REPO_ROOT / "gates" / "push_gate.sh"), "origin",
                           "unused-url"],
                          cwd=repo, env=env, text=True,
                          input=_refs(*pairs), capture_output=True)


def _media_shape(repo, version):
    """media_server's layout: a VERSION file AND a workspace Cargo.toml."""
    write(repo, "VERSION", f"{version}\n")
    write(repo, "Cargo.toml",
          f'[workspace]\nmembers = ["crates/healthcheck-rs"]\n\n'
          f'[workspace.package]\nversion = "{version}"\n')
    write(repo, "crates/healthcheck-rs/Cargo.toml",
          f'[package]\nname = "healthcheck"\nversion = "{version}"\n')


# ── the incident, reproduced ──────────────────────────────────────────────────

def test_the_incident_shape_is_red(tmp_path):
    """The exact defect: tag v1.79.3 on a commit declaring 1.79.1."""
    repo = tmp_path / "media"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    _media_shape(repo, "1.79.1")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    git(repo, "tag", "-a", "v1.79.3", "-m", "release")
    refs = tmp_path / "refs.txt"
    refs.write_text(_refs(("refs/tags/v1.79.3", _sha(repo, "v1.79.3"))))

    r = run_check(repo, CHECKER, "--refs-file", str(refs))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "1.79.3" in r.stderr and "1.79.1" in r.stderr, r.stderr
    assert "VERSION" in r.stderr, r.stderr


def test_the_commit_is_read_never_the_working_tree(tmp_path):
    """A dirty tree saying the right thing must not rescue the tag.

    This is the amend's own shape: the version was right on disk, and the commit
    that was about to be published was not.
    """
    repo = tmp_path / "media"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    _media_shape(repo, "1.79.1")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    git(repo, "tag", "v1.79.3")
    write(repo, "VERSION", "1.79.3\n")          # uncommitted, unstaged
    write(repo, "Cargo.toml",
          '[workspace]\nmembers = ["crates/healthcheck-rs"]\n\n'
          '[workspace.package]\nversion = "1.79.3"\n')
    refs = tmp_path / "refs.txt"
    refs.write_text(_refs(("refs/tags/v1.79.3", _sha(repo, "v1.79.3"))))

    r = run_check(repo, CHECKER, "--refs-file", str(refs))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "1.79.1" in r.stderr, "the working tree was read instead of the commit"


def test_a_matching_tag_is_green(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    _media_shape(repo, "1.79.3")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    git(repo, "tag", "-a", "v1.79.3", "-m", "release")   # annotated, peeled
    refs = tmp_path / "refs.txt"
    refs.write_text(_refs(("refs/tags/v1.79.3", _sha(repo, "v1.79.3"))))

    r = run_check(repo, CHECKER, "--refs-file", str(refs))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 pushed release tag" in r.stdout, r.stdout


def test_a_lightweight_tag_resolves_too(tmp_path):
    """`git tag` without -a: the ref's sha IS the commit, and ^{commit} is a no-op."""
    repo = tmp_path / "app"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    _media_shape(repo, "2.0.0")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    git(repo, "tag", "v2.0.0")
    refs = tmp_path / "refs.txt"
    refs.write_text(_refs(("refs/tags/v2.0.0", _sha(repo, "v2.0.0"))))
    assert run_check(repo, CHECKER, "--refs-file", str(refs)).returncode == 0


def test_a_prerelease_tag_is_compared_in_full(tmp_path):
    """`1.2.3-rc.1` is not `1.2.3`. Smoothing that is the defect, not the fix."""
    repo = tmp_path / "app"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    _media_shape(repo, "1.2.3-rc.1")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    git(repo, "tag", "v1.2.3-rc.1")
    ok_refs = tmp_path / "ok.txt"
    ok_refs.write_text(_refs(("refs/tags/v1.2.3-rc.1", _sha(repo, "v1.2.3-rc.1"))))
    assert run_check(repo, CHECKER, "--refs-file", str(ok_refs)).returncode == 0

    write(repo, "VERSION", "1.2.3\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "rc bump")
    git(repo, "tag", "-f", "v1.2.3-rc.2")
    bad_refs = tmp_path / "bad.txt"
    bad_refs.write_text(_refs(("refs/tags/v1.2.3-rc.2", _sha(repo, "v1.2.3-rc.2"))))
    r = run_check(repo, CHECKER, "--refs-file", str(bad_refs))
    assert r.returncode == 1, r.stdout + r.stderr


# ── which refs are in scope ───────────────────────────────────────────────────

def test_a_stale_unrelated_tag_does_not_block_an_unrelated_push(tmp_path):
    """THE scope rule. Scanning all tags makes one old tag veto every push
    until somebody deletes it — a gate that cries wolf gets --no-verify'd."""
    repo = tmp_path / "media"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    _media_shape(repo, "1.0.0")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    git(repo, "tag", "v9.9.9")                 # lies: the commit says 1.0.0
    _media_shape(repo, "2.0.0")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "bump")

    refs = tmp_path / "refs.txt"                # pushing ONLY the branch
    refs.write_text(_refs(("refs/heads/main", _sha(repo, "HEAD"))))
    r = run_check(repo, CHECKER, "--refs-file", str(refs))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "not applicable" in r.stdout, r.stdout


@pytest.mark.parametrize("name", ["nightly", "v1", "v1.2", "release-1.2.3", "V1.2.3"])
def test_a_ref_that_claims_no_semver_is_not_policed(tmp_path, name):
    repo = tmp_path / "app"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo, "README.md", "x\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    refs = tmp_path / "refs.txt"
    refs.write_text(_refs((f"refs/tags/{name}", _sha(repo, "HEAD"))))
    r = run_check(repo, CHECKER, "--refs-file", str(refs))
    assert r.returncode == 0 and "not applicable" in r.stdout, r.stdout


def test_a_tag_delete_is_not_policed(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo, "README.md", "x\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    refs = tmp_path / "refs.txt"
    refs.write_text(f"refs/tags/v1.2.3 {ZERO} refs/tags/v1.2.3 {ZERO}\n")
    r = run_check(repo, CHECKER, "--refs-file", str(refs))
    assert r.returncode == 0 and "not applicable" in r.stdout, r.stdout


def test_an_empty_push_is_a_named_non_run_not_a_pass(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo, "README.md", "x\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    refs = tmp_path / "refs.txt"
    refs.write_text("")
    r = run_check(repo, CHECKER, "--refs-file", str(refs))
    assert r.returncode == 0
    assert "not applicable" in r.stdout, (
        "zero refs examined must SAY so; a silent 0 is indistinguishable from "
        "having checked the tag and found it clean")


# ── absence is a finding ──────────────────────────────────────────────────────

def test_a_tag_with_no_version_source_is_a_finding(tmp_path):
    """`v1.0.0` on a tree declaring no version is unverifiable. Unverifiable
    read as fine is how the NEXT one ships."""
    repo = tmp_path / "bare"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo, "README.md", "x\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    git(repo, "tag", "v1.0.0")
    refs = tmp_path / "refs.txt"
    refs.write_text(_refs(("refs/tags/v1.0.0", _sha(repo, "v1.0.0"))))
    r = run_check(repo, CHECKER, "--refs-file", str(refs))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "NO version source" in r.stderr, r.stderr
    assert "VERSION" in r.stderr and "Cargo.toml" in r.stderr, (
        "the finding must name where it looked, or nobody knows what to add")


def test_a_present_but_empty_source_is_not_absence(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo, "VERSION", "\n# nothing here\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    git(repo, "tag", "v1.0.0")
    found, present = gate.declared_at(str(repo), _sha(repo, "HEAD"),
                                      gate.parse_sources("file:VERSION"))
    # Present-but-declaring-nothing must read differently from absent: the
    # finding says "VERSION exists and says nothing", which is a different fix
    # from "there is no VERSION file".
    assert not found
    assert "VERSION" in present, present


def test_a_glob_matching_nothing_is_reported_not_skipped(tmp_path):
    """A typo'd glob must not retire a strategy in silence."""
    repo = tmp_path / "app"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    _media_shape(repo, "1.0.0")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    found, present = gate.declared_at(str(repo), _sha(repo, "HEAD"),
                                      gate.parse_sources("cargo:crate/*/Cargo.toml"))
    assert not found
    assert any("matched no path" in p for p in present), present


# ── the strategies, both layouts ──────────────────────────────────────────────

def test_both_live_layouts_are_covered(tmp_path):
    """media_server's VERSION file and app_updates' workspace manifest, in one
    run — neither repo is special-cased."""
    repo = tmp_path / "both"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo, "VERSION", "4.5.6\n")
    write(repo, "Cargo.toml",
          '[workspace]\nmembers = ["crates/cli"]\n\n'
          '[workspace.package]\nversion = "4.5.6"\n')
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    git(repo, "tag", "v4.5.6")
    found, _ = gate.declared_at(str(repo), _sha(repo, "HEAD"),
                                gate.parse_sources(" ".join(gate.DEFAULT_SOURCES)))
    assert sorted(v for _, v in found) == ["4.5.6", "4.5.6"], found


def test_a_leading_v_in_the_version_file_is_presentation_not_identity():
    assert gate._norm("v1.2.3") == gate._norm("1.2.3") == "1.2.3"


def test_version_workspace_true_is_an_inheritance_not_a_declaration():
    """app_updates' members all say this. Reading it as a version would invent
    a finding about a number nobody wrote."""
    found = gate.from_cargo('[package]\nname = "app-updates-cli"\nversion.workspace = true\n')
    assert found == []


def test_a_dependency_version_is_not_this_repos_release_number():
    found = gate.from_cargo(
        '[package]\nname = "x"\n\n[dependencies]\nserde = { version = "1.0.219" }\n')
    assert found == []


def test_a_vendored_crate_is_reachable_only_through_an_explicit_glob(tmp_path):
    """app_updates vendors camoufox-rs at 0.1.0. The default sources must not
    sweep it; a scoped glob must be able to reach a repo's own crates."""
    repo = tmp_path / "app"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo, "Cargo.toml", '[workspace.package]\nversion = "1.35.1"\n')
    write(repo, "crates/cli/Cargo.toml", '[package]\nname = "cli"\nversion = "1.35.1"\n')
    write(repo, "vendor/camoufox-rs/Cargo.toml", '[package]\nname = "cf"\nversion = "0.1.0"\n')
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    head = _sha(repo, "HEAD")

    default, _ = gate.declared_at(str(repo), head,
                                  gate.parse_sources(" ".join(gate.DEFAULT_SOURCES)))
    assert [v for _, v in default] == ["1.35.1"], default
    scoped, _ = gate.declared_at(str(repo), head,
                                 gate.parse_sources("cargo:crates/*/Cargo.toml"))
    assert [v for _, v in scoped] == ["1.35.1"], scoped


# ── wiring: the real pre-push gate ────────────────────────────────────────────

def test_the_push_gate_refuses_a_lying_tag(tmp_path):
    """End to end through gates/push_gate.sh, which is what every repo runs."""
    repo = tmp_path / "media"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo, "tools/gate.sh", "#!/usr/bin/env bash\nexit 0\n")
    _media_shape(repo, "1.79.1")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    git(repo, "tag", "v1.79.3")

    r = _push(repo, tmp_path, ("refs/tags/v1.79.3", _sha(repo, "v1.79.3")))
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "1.79.1" in out and "1.79.3" in out, out
    assert "nothing pushed" in out, out
    # One line: the repo's own checkout. The throwaway export is gone — a
    # refusal that exits before the worktree is made must still leave none.
    assert len(git(repo, "worktree", "list").strip().splitlines()) == 1, \
        git(repo, "worktree", "list")


def test_the_push_gate_checks_a_tag_whose_commit_the_remote_already_has(tmp_path):
    """The retag case, and the reason the check runs before every skip below it.

    push_gate skips a commit the remote already has (ZoneWM: six never-pushed
    tags on days-old commits, each costing a cold gate). That skip is about
    CODE; a tag's version claim is not, and re-tagging an old commit to a new
    version is exactly how a lie gets published.
    """
    repo = tmp_path / "media"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo, "tools/gate.sh", "#!/usr/bin/env bash\nexit 0\n")
    _media_shape(repo, "1.79.1")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    old = _sha(repo, "HEAD")
    git(repo, "update-ref", "refs/remotes/origin/main", old)   # remote already has it
    git(repo, "tag", "v1.79.3", old)

    r = _push(repo, tmp_path, ("refs/tags/v1.79.3", _sha(repo, "v1.79.3")))
    out = r.stdout + r.stderr
    assert r.returncode != 0, out
    assert "1.79.1" in out, out


def test_the_push_gate_allows_a_truthful_tag_and_still_runs_the_code_gate(tmp_path):
    """Not a veto on everything: a matching tag passes, and the worktree gate
    for its commit still happens."""
    repo = tmp_path / "media"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo, "tools/gate.sh",
          '#!/usr/bin/env bash\nset -euo pipefail\necho "ran=$PWD" >> "$GATE_REPORT"\n')
    _media_shape(repo, "1.79.3")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    git(repo, "tag", "v1.79.3")

    r = _push(repo, tmp_path, ("refs/tags/v1.79.3", _sha(repo, "v1.79.3")))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ran=" in (tmp_path / "report.txt").read_text(), "the code gate was skipped"


def test_a_branch_only_push_is_not_a_named_non_run_of_the_tag_check(tmp_path):
    repo = tmp_path / "media"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo, "tools/gate.sh", "#!/usr/bin/env bash\nexit 0\n")
    _media_shape(repo, "1.0.0")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    git(repo, "tag", "v9.9.9")                      # a stale liar in the repo

    r = _push(repo, tmp_path, ("refs/heads/main", _sha(repo, "HEAD")))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "not applicable" in r.stdout, r.stdout


def test_the_push_gate_leaves_no_refs_file_behind(tmp_path):
    """The refs capture is a file in TMPDIR, so it can be leaked by every push.

    A private TMPDIR is the seam that makes this measurable: globbing the SHARED
    one measures the other xdist workers too, and a green run would be a
    coincidence. This test passed alone and failed in the full suite for exactly
    that reason, which is what it is here to catch about itself.
    """
    repo = tmp_path / "media"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo, "tools/gate.sh", "#!/usr/bin/env bash\nexit 0\n")
    _media_shape(repo, "1.0.0")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    for green, refs in ((True, [("refs/heads/main", _sha(repo, "HEAD"))]),
                        (False, [("refs/tags/v1.79.3", _sha(repo, "HEAD"))])):
        r = _push(repo, tmp_path, *refs, extra_env={"TMPDIR": str(scratch)})
        assert (r.returncode == 0) is green, r.stdout + r.stderr
        assert not list(scratch.glob("goh-push-refs.*")), \
            f"{'a green' if green else 'a refused'} push leaked its refs file"


def test_sources_come_from_gatesrc(tmp_path):
    """A repo whose version lives elsewhere joins with config, not a code change."""
    repo = tmp_path / "swift"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo, "MarketingVersion.txt", "7.0.1\n")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    git(repo, "tag", "v7.0.1")
    refs = tmp_path / "refs.txt"
    refs.write_text(_refs(("refs/tags/v7.0.1", _sha(repo, "v7.0.1"))))

    plain = run_check(repo, CHECKER, "--refs-file", str(refs))
    assert plain.returncode == 1, "no default source exists here"

    import os

    configured = subprocess.run(
        ["python3", str(REPO_ROOT / CHECKER), "--refs-file", str(refs)],
        cwd=repo, capture_output=True, text=True,
        env=dict(os.environ, GOH_TAG_VERSION_SOURCES="file:MarketingVersion.txt"))
    assert configured.returncode == 0, configured.stdout + configured.stderr


def test_an_unknown_source_kind_is_a_usage_error(tmp_path):
    r = run_check(tmp_path, CHECKER, "--refs-file", "/dev/null")
    assert r.returncode == 2, r.stdout + r.stderr


def test_the_probe_runs_and_is_green():
    """check_probes_pass discovers this by source, then RUNS it."""
    r = subprocess.run([sys.executable, str(REPO_ROOT / CHECKER), "--probe"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "a lying tag goes red" in r.stdout, r.stdout
