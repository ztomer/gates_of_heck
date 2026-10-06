"""`goh lock-version` — a committed Cargo.lock must agree with its manifest.

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

import pytest

from conftest import commit_all, git, write
from tier_kit import both_tiers, run_tiered  # noqa: F401  # both_tiers: a fixture
from _fast_git import fast_init  # noqa: E402

pytestmark = pytest.mark.usefixtures("both_tiers")

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
    """The checker over `repo`, on the current tier (Python, or `goh lock-version`)."""
    return run_tiered(repo, CHECKER, "lock-version", *args)


def audit(repo):
    """`(findings, examined, notes)` on the current tier: in process for Python, `--json` for the
    port -- the same three values, so every audit-level test judges both."""
    r = _run(repo, "--json")
    data = json.loads(r.stdout)
    return data["findings"], data["examined"], data["notes"]


# ── the incident, reproduced ──────────────────────────────────────────────────


def test_the_incident_is_red(tmp_path):
    """Manifest 1.36.0, lockfile 1.35.0. The whole defect."""
    repo = tmp_path / "app"
    repo.mkdir()
    _workspace(repo, "1.36.0", "1.35.0")
    fast_init(repo, "main")
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
    fast_init(repo, "main")
    commit_all(repo)
    assert _run(repo).returncode == 0, _run(repo).stderr


def test_every_member_is_named(tmp_path):
    """Four crates, one bumped manifest: all four are wrong, and a gate that
    names one of them leaves three to be found by hand."""
    repo = tmp_path / "app"
    repo.mkdir()
    _workspace(repo, "2.0.0", "1.0.0", members=("crates/a", "crates/b", "crates/c", "crates/d"))
    findings, examined, _ = audit(repo)
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
    findings, _, _ = audit(repo)
    assert len(findings) == 1, findings
    assert "no entry for app-cli" in findings[0], findings


def test_a_third_party_version_is_never_this_repos_claim(tmp_path):
    """`serde 1.0.219` in the lock is a fact about upstream, not a claim here.
    A gate that compared every `[[package]]` would fire on every pin bump."""
    repo = tmp_path / "app"
    repo.mkdir()
    _workspace(repo, "1.0.0", "1.0.0")
    findings, _, _ = audit(repo)
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
    findings, examined, _ = audit(repo)
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
    findings, _, _ = audit(repo)
    assert findings == [], findings


def test_inheritance_is_resolved_not_compared_literally(tmp_path):
    """`version.workspace = true` resolves to the workspace number. Compared as
    text, it is the string `true` against `1.36.0` -- a finding on every member
    of every modern workspace, and right about none of them."""
    repo = tmp_path / "app"
    repo.mkdir()
    _workspace(repo, "1.36.0", "1.36.0")
    findings, examined, _ = audit(repo)
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
    fast_init(repo, "main")
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
    findings, _, _ = audit(repo)
    assert any("no single answer" in f for f in findings), findings


def test_a_stale_lockfile_is_reported_once_not_twice(tmp_path):
    """Arm 1 and arm 2 both notice the incident. Reporting it four times reads
    as noise and gets the gate switched off, not the lockfile regenerated."""
    repo = tmp_path / "app"
    repo.mkdir()
    write(repo, "VERSION", "1.36.0\n")
    _workspace(repo, "1.36.0", "1.35.0")
    findings, _, _ = audit(repo)
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
    findings, examined, notes = audit(repo)
    assert examined == 0 and not findings and notes
    r = _run(repo)
    assert "OK —" not in r.stdout, r.stdout


def test_a_missing_directory_is_a_usage_error(tmp_path):
    r = _run(tmp_path, "--root", str(tmp_path / "nope"))
    assert r.returncode == 2, r.stdout + r.stderr


# ── parsing: the shapes that read as nothing ────────────────────────────────


# Each shape below once read as NOTHING to compare, so it is pinned by what the gate then says
# about a whole tree: the members it examined, and whether it invented a finding.


def _lock(repo, *pkgs):
    """A Cargo.lock holding exactly these `(name, version)` packages."""
    body = "version = 4\n" + "".join(
        f'\n[[package]]\nname = "{n}"\nversion = "{v}"\n' for n, v in pkgs
    )
    write(repo, "Cargo.lock", body)


def test_a_multiline_member_list_is_read(tmp_path):
    """`divoom-control` writes `members = [` across four lines. A per-line regex
    finds the opening bracket and never its partner, the member list comes back
    empty, and a three-crate workspace reports nothing to compare."""
    repo = tmp_path / "divoom"
    write(
        repo,
        "Cargo.toml",
        '[workspace]\nresolver = "2"\nmembers = [\n    "divoomd",\n    "nowplaying",\n]\n'
        '\n[workspace.package]\nversion = "2.0.0"\n',
    )
    for m in ("divoomd", "nowplaying"):
        write(repo, f"{m}/Cargo.toml", f'[package]\nname = "{m}"\nversion.workspace = true\n')
    _lock(repo, ("divoomd", "1.0.0"), ("nowplaying", "1.0.0"))
    findings, examined, _ = audit(repo)
    assert examined == 2 and len(findings) == 2, (examined, findings)


def test_an_exclude_is_not_mistaken_for_a_member(tmp_path):
    """`monitor` puts `exclude = ["fuzz"]` after its member list. A greedy
    DOTALL match runs to the LAST `]` in the section, so the excluded directory
    becomes a workspace member -- which is what this repo's first run reported
    against monitor, inventing a finding about a crate the workspace excludes."""
    repo = tmp_path / "mon"
    write(
        repo,
        "Cargo.toml",
        '[workspace]\nmembers = ["crates/agent"]\nexclude = ["fuzz"]\n'
        '\n[workspace.package]\nversion = "1.0.0"\n',
    )
    write(repo, "crates/agent/Cargo.toml", '[package]\nname = "agent"\nversion.workspace = true\n')
    write(repo, "fuzz/Cargo.toml", '[package]\nname = "fuzz"\nversion = "9.9.9"\n')
    _lock(repo, ("agent", "1.0.0"), ("fuzz", "0.0.1"))
    findings, examined, _ = audit(repo)
    assert (examined, findings) == (1, []), (examined, findings)


@pytest.mark.parametrize(
    "tail",
    [
        # `version = "1.0.219"` under [dependencies] belongs to serde: reading it as the
        # package's own is how a release number becomes a transitive dependency's.
        '\n[dependencies]\nserde = "1.0.219"\n',
        '\n[package.metadata.docs]\nversion = "9.9.9"\n',
        # The `[lib]` table follows `[package]`; a `version` after it belongs to nothing here.
        '\n[lib]\nversion = "7.7.7"\n',
    ],
)
def test_a_version_outside_the_package_table_is_not_a_declaration(tmp_path, tail):
    repo = tmp_path / "app"
    write(repo, "Cargo.toml", '[package]\nname = "app"\nversion = "1.0.0"\n' + tail)
    _lock(repo, ("app", "1.0.0"), ("serde", "1.0.219"))
    findings, examined, _ = audit(repo)
    assert (examined, findings) == (1, []), (examined, findings)


def test_a_lockfile_with_a_patch_table_does_not_confuse_the_parser(tmp_path):
    """`[[patch.unused]]` follows the `[[package]]` blocks and has its own
    `name`/`version`. A parser that keeps appending to the last block invents an
    entry for a patched registry crate -- or overwrites the real one."""
    repo = tmp_path / "app"
    repo.mkdir()
    _workspace(repo, "1.0.0", "1.0.0")
    write(
        repo,
        "Cargo.lock",
        LOCK.format(version="1.0.0") + '\n[[patch.unused]]\nname = "app-core"\nversion = "9.9.9"\n',
    )
    findings, examined, _ = audit(repo)
    assert (examined, findings) == (2, []), (examined, findings)


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


# The retired Python checker's `--probe`, and the two calibrations that blinded its source to
# prove the probe could go red, went with it (Phase N3). The forgotten-member case above is the
# same proof against the native: it is red on the tree arm two exists for.


def test_an_unknown_version_source_is_a_usage_error_not_a_traceback(tmp_path, monkeypatch):
    """`GOH_TAG_VERSION_SOURCES=bogus:x` raised ValueError out of `audit` and the checker died with
    a traceback, exit 1 -- read by a pipeline as FINDINGS. A config error is exit 2 (found porting
    this, Phase N1)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _workspace(repo, "1.0.0", "1.0.0")
    monkeypatch.setenv("GOH_TAG_VERSION_SOURCES", "bogus:x")
    r = _run(repo)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "unknown version source 'bogus:x'" in r.stderr and "Traceback" not in r.stderr, r.stderr
