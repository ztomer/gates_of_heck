"""check_lock_version.py — a committed Cargo.lock must agree with its manifest.

app_updates, 2026-10-01. The release commit bumped
`[workspace.package] version` to 1.36.0 and touched nothing else. `Cargo.lock`
shipped 1.35.0 for all four workspace crates.

Three reasons nothing noticed, and each one is structural rather than
accidental:

* **cargo silently repairs it.** Any `cargo build` rewrites the lockfile from
  the manifest, so the working tree self-heals on the next compile. The defect
  exists only in the COMMIT — which is what gets published and what `git clone`
  reproduces. A gate that runs cargo before looking has destroyed its evidence.
* **the tag check cannot see it.** `check_tag_version.py` compares a pushed tag
  against the version DECLARED at its commit; the declaration was right. It
  reads `Cargo.toml` and never opens `Cargo.lock`. Same class as the tag
  incident, one file over: a name checked against something other than the
  artifact that claims it.
* **`version.workspace = true` is an inheritance, not a version.** Reading the
  literal text compares `true` against `1.36.0` and manufactures a finding on
  every member of a modern workspace.
"""

import json
import sys

from conftest import REPO_ROOT, commit_all, git, write

sys.path.insert(0, str(REPO_ROOT / "checks"))
import check_lock_version as gate  # noqa: E402

CHECKER = "checks/check_lock_version.py"

LOCK = """\
version = 4

[[package]]
name = "app-core"
version = "{version}"

[[package]]
name = "app-cli"
version = "{version}"

[[package]]
name = "serde"
version = "1.0.219"
source = "registry+https://github.com/rust-lang/crates.io-index"
"""


def _workspace(repo, manifest_version, lock_version, members=("crates/core", "crates/cli")):
    """app_updates' exact shape: a workspace whose members all INHERIT."""
    lines = ", ".join(f'"{m}"' for m in members)
    write(
        repo,
        "Cargo.toml",
        f"[workspace]\nmembers = [{lines}]\n\n[workspace.package]\n"
        f'version = "{manifest_version}"\n',
    )
    write(repo, "Cargo.lock", LOCK.format(version=lock_version))
    for member in members:
        name = member.rsplit("/", 1)[-1]
        write(
            repo,
            f"{member}/Cargo.toml",
            f'[package]\nname = "app-{name}"\nversion.workspace = true\n',
        )


def _run(repo, *args):
    import subprocess

    return subprocess.run(
        [sys.executable, str(REPO_ROOT / CHECKER), *args], cwd=repo, capture_output=True, text=True
    )


# ── the incident, reproduced ──────────────────────────────────────────────────


def test_the_incident_is_red(tmp_path):
    """Manifest 1.36.0, lockfile 1.35.0. The whole defect."""
    repo = tmp_path / "app"
    repo.mkdir()
    _workspace(repo, "1.36.0", "1.35.0")
    git(repo, "init", "-q", "-b", "main")
    commit_all(repo)

    r = _run(repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "1.36.0" in r.stderr and "1.35.0" in r.stderr, r.stderr
    assert "Cargo.lock" in r.stderr, r.stderr


def test_regenerating_the_lockfile_is_green(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    _workspace(repo, "1.36.0", "1.35.0")
    write(repo, "Cargo.lock", LOCK.format(version="1.36.0"))
    git(repo, "init", "-q", "-b", "main")
    commit_all(repo)
    assert _run(repo).returncode == 0, _run(repo).stderr


def test_every_member_is_named(tmp_path):
    """Four crates, one bumped manifest: all four are wrong, and a gate that
    names one of them leaves three to be found by hand."""
    repo = tmp_path / "app"
    repo.mkdir()
    _workspace(repo, "2.0.0", "1.0.0", members=("crates/a", "crates/b", "crates/c", "crates/d"))
    findings, examined, _ = gate.audit(repo, " ".join(gate.DEFAULT_SOURCES))
    assert examined == 4, examined
    assert len(findings) == 4, findings
    for member in ("app-a", "app-b", "app-c", "app-d"):
        assert any(member in f for f in findings), (member, findings)


def test_a_lockfile_missing_a_member_is_red(tmp_path):
    """The other direction: the lockfile does not know about a crate at all."""
    repo = tmp_path / "app"
    repo.mkdir()
    _workspace(repo, "1.0.0", "1.0.0")
    write(repo, "Cargo.lock", '[[package]]\nname = "app-core"\nversion = "1.0.0"\n')
    findings, _, _ = gate.audit(repo, " ".join(gate.DEFAULT_SOURCES))
    assert len(findings) == 1, findings
    assert "no entry for app-cli" in findings[0], findings


def test_a_third_party_version_is_never_this_repos_claim(tmp_path):
    """`serde 1.0.219` in the lock is a fact about upstream, not a claim here.
    A gate that compared every `[[package]]` would fire on every pin bump."""
    repo = tmp_path / "app"
    repo.mkdir()
    _workspace(repo, "1.0.0", "1.0.0")
    findings, _, _ = gate.audit(repo, " ".join(gate.DEFAULT_SOURCES))
    assert findings == [], findings


# ── the layouts that must NOT produce findings ───────────────────────────────


def test_per_crate_versions_are_a_layout_not_a_defect(tmp_path):
    """Five crates at five numbers, no [workspace.package]. Real, and five
    findings for it is how a gate gets `--no-verify`'d."""
    repo = tmp_path / "multi"
    repo.mkdir()
    write(repo, "Cargo.toml", '[workspace]\nmembers = ["crates/a", "crates/b"]\n')
    for name, version in (("a", "1.2.3"), ("b", "4.5.6")):
        write(
            repo,
            f"crates/{name}/Cargo.toml",
            f'[package]\nname = "{name}"\nversion = "{version}"\n',
        )
    write(
        repo,
        "Cargo.lock",
        '[[package]]\nname = "a"\nversion = "1.2.3"\n\n'
        '[[package]]\nname = "b"\nversion = "4.5.6"\n',
    )
    findings, examined, _ = gate.audit(repo, " ".join(gate.DEFAULT_SOURCES))
    assert findings == [], findings
    assert examined == 2, examined


def test_a_member_at_its_own_version_under_a_workspace_version_is_a_layout(tmp_path):
    """`monitor` ships `multitop-vault` at 0.21.0 under
    `[workspace.package] version = "0.51.0"`. A `cargo:` source yields the
    workspace's OWN number, so comparing crates against it would be the repo
    disagreeing with itself -- which is what this repo's first run reported,
    before the arm learned to read only a source outside the manifests."""
    repo = tmp_path / "mon"
    repo.mkdir()
    write(
        repo,
        "Cargo.toml",
        '[workspace]\nmembers = ["crates/vault"]\n\n[workspace.package]\nversion = "0.51.0"\n',
    )
    write(
        repo, "crates/vault/Cargo.toml", '[package]\nname = "multitop-vault"\nversion = "0.21.0"\n'
    )
    write(repo, "Cargo.lock", '[[package]]\nname = "multitop-vault"\nversion = "0.21.0"\n')
    findings, _, _ = gate.audit(repo, " ".join(gate.DEFAULT_SOURCES))
    assert findings == [], findings


def test_inheritance_is_resolved_not_compared_literally(tmp_path):
    """`version.workspace = true` resolves to the workspace number. Compared as
    text, it is the string `true` against `1.36.0` -- a finding on every member
    of every modern workspace, and right about none of them."""
    repo = tmp_path / "app"
    repo.mkdir()
    _workspace(repo, "1.36.0", "1.36.0")
    findings, examined, _ = gate.audit(repo, " ".join(gate.DEFAULT_SOURCES))
    assert findings == [] and examined == 2, (findings, examined)


# ── arm 2: the release number declared OUTSIDE the manifests ─────────────────


def _forgotten_member(repo):
    """The release bumped in every place EXCEPT one member manifest.

    `VERSION` and `[workspace.package]` both say 1.79.3; `healthcheck` still
    declares 1.79.1 and its lockfile entry agrees. So the manifest and the
    lockfile are consistent with each other, arm 1 has nothing to say, and the
    release claim has no gate standing in front of it.
    """
    write(repo, "VERSION", "1.79.3\n")
    write(
        repo,
        "Cargo.toml",
        '[workspace]\nmembers = ["crates/hc"]\n\n[workspace.package]\nversion = "1.79.3"\n',
    )
    write(repo, "crates/hc/Cargo.toml", '[package]\nname = "healthcheck"\nversion = "1.79.1"\n')
    write(repo, "Cargo.lock", '[[package]]\nname = "healthcheck"\nversion = "1.79.1"\n')


def test_a_release_number_bumped_in_one_place_only_is_red(tmp_path):
    """media_server's shape: a `VERSION` file AND crate manifests. Bump VERSION
    and the workspace, forget a member, and the manifest and the lockfile AGREE
    with each other on the old number -- so arm 1 is silent and nothing else is
    standing between the release claim and what shipped."""
    repo = tmp_path / "media"
    repo.mkdir()
    _forgotten_member(repo)
    git(repo, "init", "-q", "-b", "main")
    commit_all(repo)

    r = _run(repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "1.79.3" in r.stderr and "1.79.1" in r.stderr, r.stderr
    assert "VERSION" in r.stderr, r.stderr


def test_a_consistent_release_declaration_is_green(tmp_path):
    repo = tmp_path / "media"
    repo.mkdir()
    write(repo, "VERSION", "1.79.3\n")
    write(
        repo,
        "Cargo.toml",
        '[workspace]\nmembers = ["crates/hc"]\n\n[workspace.package]\nversion = "1.79.3"\n',
    )
    write(repo, "crates/hc/Cargo.toml", '[package]\nname = "healthcheck"\nversion = "1.79.3"\n')
    write(repo, "Cargo.lock", '[[package]]\nname = "healthcheck"\nversion = "1.79.3"\n')
    assert _run(repo).returncode == 0, _run(repo).stderr


def test_two_release_numbers_at_once_is_red(tmp_path):
    """A release number with no single answer. The tag checker would call this
    unverifiable; here it is a finding about the same claim."""
    repo = tmp_path / "media"
    repo.mkdir()
    write(repo, "VERSION", "1.79.3\n")
    write(
        repo,
        "Cargo.toml",
        '[workspace]\nmembers = ["crates/hc"]\n\n[workspace.package]\nversion = "1.79.1"\n',
    )
    write(repo, "crates/hc/Cargo.toml", '[package]\nname = "healthcheck"\nversion = "1.79.1"\n')
    write(repo, "Cargo.lock", '[[package]]\nname = "healthcheck"\nversion = "1.79.1"\n')
    findings, _, _ = gate.audit(repo, " ".join(gate.DEFAULT_SOURCES))
    assert any("no single answer" in f for f in findings), findings


def test_a_stale_lockfile_is_reported_once_not_twice(tmp_path):
    """Arm 1 and arm 2 both notice the incident. Reporting it four times reads
    as noise and gets the gate switched off, not the lockfile regenerated."""
    repo = tmp_path / "app"
    repo.mkdir()
    write(repo, "VERSION", "1.36.0\n")
    _workspace(repo, "1.36.0", "1.35.0")
    findings, _, _ = gate.audit(repo, " ".join(gate.DEFAULT_SOURCES))
    assert len(findings) == 2, findings
    assert sum("1.35.0" in f for f in findings) == 2, findings


# ── named non-runs ───────────────────────────────────────────────────────────


def test_no_lockfile_is_a_named_non_run(tmp_path):
    repo = tmp_path / "plain"
    repo.mkdir()
    write(repo, "Cargo.toml", "[workspace]\nmembers = []\n")
    r = _run(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no Cargo.lock" in r.stdout and "not applicable" in r.stdout, r.stdout


def test_a_manifest_with_no_crates_is_a_named_non_run(tmp_path):
    repo = tmp_path / "plain"
    repo.mkdir()
    write(repo, "Cargo.toml", '[workspace]\nresolver = "2"\n')
    write(repo, "Cargo.lock", "version = 4\n")
    r = _run(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "not applicable" in r.stdout, r.stdout


def test_zero_compared_is_never_reported_as_agreement(tmp_path):
    """The empty-scope contract: a gate that examined nothing must not print the
    same sentence as one that examined everything and agreed."""
    repo = tmp_path / "plain"
    repo.mkdir()
    write(repo, "Cargo.toml", "[workspace]\nmembers = []\n")
    write(repo, "Cargo.lock", "version = 4\n")
    findings, examined, notes = gate.audit(repo, " ".join(gate.DEFAULT_SOURCES))
    assert examined == 0 and not findings and notes
    r = _run(repo)
    assert "OK —" not in r.stdout, r.stdout


def test_a_missing_directory_is_a_usage_error(tmp_path):
    r = _run(tmp_path, "--root", str(tmp_path / "nope"))
    assert r.returncode == 2, r.stdout + r.stderr


# ── parsing: the shapes that read as nothing ────────────────────────────────


def test_a_multiline_member_list_is_read(tmp_path):
    """`divoom-control` writes `members = [` across four lines. A per-line regex
    finds the opening bracket and never its partner, the member list comes back
    empty, and a three-crate workspace reports nothing to compare."""
    repo = tmp_path / "divoom"
    repo.mkdir()
    write(
        repo,
        "Cargo.toml",
        '[workspace]\nresolver = "2"\nmembers = [\n    "divoomd",\n    "nowplaying",\n]\n',
    )
    version, members = gate.parse_workspace((repo / "Cargo.toml").read_text())
    assert members == ["divoomd", "nowplaying"], members


def test_an_exclude_is_not_mistaken_for_a_member(tmp_path):
    """`monitor` puts `exclude = ["fuzz"]` after its member list. A greedy
    DOTALL match runs to the LAST `]` in the section, so the excluded directory
    becomes a workspace member -- which is what this repo's first run reported
    against monitor, inventing a finding about a crate the workspace excludes."""
    repo = tmp_path / "mon"
    repo.mkdir()
    write(repo, "Cargo.toml", '[workspace]\nmembers = ["crates/agent"]\nexclude = ["fuzz"]\n')
    _, members = gate.parse_workspace((repo / "Cargo.toml").read_text())
    assert members == ["crates/agent"], members


def test_a_dependency_version_is_not_a_declaration(tmp_path):
    """`version = "1.0.219"` under [dependencies] belongs to serde. Reading it
    as the package's own version is how a repo's release number becomes a
    transitive dependency's."""
    name, version = gate.parse_package(
        '[package]\nname = "app"\nversion = "1.0.0"\n\n[dependencies]\nserde = "1.0.219"\n'
    )
    assert (name, version) == ("app", "1.0.0"), (name, version)


def test_a_metadata_version_is_not_a_declaration(tmp_path):
    name, version = gate.parse_package(
        '[package]\nname = "app"\nversion = "1.0.0"\n\n[package.metadata.docs]\nversion = "9.9.9"\n'
    )
    assert version == "1.0.0", version


def test_a_version_after_the_package_table_ends_the_package(tmp_path):
    """The `[lib]` table follows `[package]`; a `version` key after it belongs
    to nothing this checker should read."""
    name, version = gate.parse_package(
        '[package]\nname = "app"\nversion = "1.0.0"\n\n[lib]\nversion = "7.7.7"\n'
    )
    assert (name, version) == ("app", "1.0.0"), (name, version)


def test_a_lockfile_with_a_patch_table_does_not_confuse_the_parser(tmp_path):
    """`[[patch.unused]]` follows the `[[package]]` blocks and has its own
    `name`/`version`. A parser that keeps appending to the last block invents an
    entry for a patched registry crate."""
    repo = tmp_path / "app"
    repo.mkdir()
    _workspace(repo, "1.0.0", "1.0.0")
    write(
        repo,
        "Cargo.lock",
        LOCK.format(version="1.0.0") + '\n[[patch.unused]]\nname = "serde"\nversion = "9.9.9"\n',
    )
    entries = gate.parse_lock((repo / "Cargo.lock").read_text())
    assert entries["serde"]["version"] == "1.0.219", entries["serde"]


# ── wiring and output ────────────────────────────────────────────────────────


def test_json_output_carries_findings_and_population(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    _workspace(repo, "1.36.0", "1.35.0")
    r = _run(repo, "--json")
    assert r.returncode == 1
    payload = json.loads(r.stdout)
    assert payload["examined"] == 2, payload
    assert len(payload["findings"]) == 2, payload


def test_the_probe_runs_and_is_green():
    """check_probes_pass discovers this by source, then RUNS it."""
    import subprocess

    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / CHECKER), "--probe"], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "a stale lockfile goes red" in r.stdout, r.stdout


# ── calibration: these cases can go RED ─────────────────────────────────────


def test_calibration_the_suite_is_red_without_arm_two(tmp_path):
    """Proof arm 2 is load-bearing. Dropping it must turn the
    release-number-bumped-in-one-place case red -- and it must be red in the
    PROBE too, since check_probes_pass runs that."""
    import os
    import subprocess

    source = (REPO_ROOT / CHECKER).read_text(encoding="utf-8")
    assert "elif numbers:" in source
    blind = tmp_path / "blind.py"
    blind.write_text(source.replace("elif numbers:", "elif False:"), encoding="utf-8")

    repo = tmp_path / "media"
    repo.mkdir()
    _forgotten_member(repo)

    r = subprocess.run(
        [sys.executable, str(blind)],
        cwd=repo,
        capture_output=True,
        text=True,
        env=dict(os.environ, PYTHONPATH=str(REPO_ROOT / "checks")),
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1.79.3" not in r.stdout, "arm 2 still fired -- this calibration proves nothing"
    # ...and the real checker, on the same tree, is red.
    assert _run(repo).returncode == 1, "the real checker no longer runs arm 2"


def test_calibration_the_probe_goes_red_when_arm_two_stops(tmp_path):
    """The same break, seen by the gate that RUNS the probe. A probe that has
    silently stopped covering half its checker is the failure check_probes_pass
    exists for, and it can only be demonstrated by breaking the checker."""
    import os
    import subprocess

    source = (REPO_ROOT / CHECKER).read_text(encoding="utf-8")
    blind = tmp_path / "blind.py"
    blind.write_text(source.replace("elif numbers:", "elif False:"), encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(blind), "--probe"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=dict(os.environ, PYTHONPATH=str(REPO_ROOT / "checks")),
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "per-crate versions" not in r.stdout.split("probe: ")[-1], r.stdout
