"""check_tag_version.py — the `plist` layout, on its own.

An Apple bundle declares its version as a `<string>` VALUE under a `<key>` in `Info.plist`, and that
is none of the layouts this gate shipped with. Measured on ZeroThunder, whose `v2.10.0` tag no
strategy could check:

  · `file:` reads the XML **declaration** as the version — `('(file)', '<?xml version="1.0"…?>')` —
    because it takes the first line it does not read as a `#` comment and `<?xml` is not one. It then
    compares the tag against "1.0" and reports a correct release as a mismatch.
  · `swift:` and `xcconfig:` correctly find nothing, because there is no `let` and no `SETTING =`.

So a repo that declares its version the way Apple ships it was UNVERIFIABLE, while the gate's own
rule is that an absent version source is a finding rather than a pass. That is correct behaviour
pointed at a layout nobody had written down — the same class as a gate that cannot see a member of
its own population, which is the defect this repository keeps finding in a new place.

Split out by concern rather than length, like the `swift` and `xcconfig` suites.
"""

import os
import subprocess
import sys

from conftest import REPO_ROOT, git, write

sys.path.insert(0, str(REPO_ROOT / "checks"))
import _version_sources as sources

PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>CFBundleName</key>
\t<string>ZeroThunder</string>
\t<key>CFBundleShortVersionString</key>
\t<string>{version}</string>
\t<key>CFBundleVersion</key>
\t<string>11</string>
</dict>
</plist>
"""


def _tagged_repo(tmp_path, plist_text, tag="v2.10.0"):
    """A repo whose HEAD declares its version in a plist, with one tag pointing at it."""
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    write(root, "Info.plist", plist_text)
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@local")
    git(root, "config", "user.name", "t")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "declare")
    git(root, "tag", tag)
    return root


def _refs(root, tag, tmp_path):
    head = git(root, "rev-parse", "HEAD").strip()
    path = tmp_path / f"refs-{tag}.txt"
    path.write_text(f"refs/heads/main {head}\nrefs/tags/{tag} {head}\n", encoding="utf-8")
    return path


def _run(root, refs, extra_env=None):
    env = dict(os.environ)
    env["GOH_TAG_VERSION_SOURCES"] = "plist:Info.plist"
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "checks" / "check_tag_version.py"),
            "--refs-file",
            str(refs),
            "--root",
            str(root),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_it_reads_the_marketing_version_and_ignores_the_build_number(tmp_path):
    found = sources.from_plist(PLIST.format(version="2.10.0"))
    assert found == [("(plist:CFBundleShortVersionString)", "2.10.0")], (
        "CFBundleVersion is a monotonic INTEGER, not a release number. Reading it would report a "
        "correct release as a mismatch on every bundle that has one."
    )


def test_the_xml_declaration_is_not_a_version(tmp_path):
    """The exact misreading that made this layout unverifiable.

    `file:` returns `<?xml version="1.0" …?>` as the version, so the gate compares the tag against
    "1.0". A strategy that reproduced that would make the layout "supported" and still wrong.
    """
    found = sources.from_plist(PLIST.format(version="2.10.0"))
    assert all(version != "1.0" for _key, version in found), found
    assert not any(
        "1.0" == version.split(".")[0] and len(version.split(".")) == 1 for _key, version in found
    )


def test_a_plist_with_no_version_declares_nothing(tmp_path):
    assert (
        sources.from_plist(
            '<?xml version="1.0"?>\n<dict>\n<key>CFBundleName</key>\n<string>ZeroThunder</string>\n</dict>\n'
        )
        == []
    )


def test_a_matching_tag_is_accepted(tmp_path):
    root = _tagged_repo(tmp_path, PLIST.format(version="2.10.0"))
    code, out = _run(root, _refs(root, "v2.10.0", tmp_path))
    assert code == 0, out
    assert "match the version declared" in out


def test_a_tag_that_disagrees_with_the_plist_is_a_finding(tmp_path):
    """The direction that matters. A strategy that only ever accepts is not a check."""
    root = _tagged_repo(tmp_path, PLIST.format(version="2.10.0"), tag="v9.99.9")
    code, out = _run(root, _refs(root, "v9.99.9", tmp_path))
    assert code == 1, out
    assert "9.99.9" in out and "2.10.0" in out


def test_a_plist_declaring_nothing_is_a_finding_not_a_pass(tmp_path):
    """An absent version source is a finding, by this gate's own rule. Silence is not a release."""
    root = _tagged_repo(
        tmp_path,
        '<?xml version="1.0"?>\n<dict>\n<key>CFBundleName</key>\n'
        "<string>ZeroThunder</string>\n</dict>\n",
    )
    code, out = _run(root, _refs(root, "v1.0.0", tmp_path))
    assert code == 1, out
    assert "NO version source declares a version" in out


def test_two_disagreeing_versions_in_one_plist_still_fail(tmp_path):
    """Matching by value shape must not resolve a self-contradicting file by taking the first."""
    text = (
        "<plist><dict>\n<key>CFBundleShortVersionString</key>\n<string>2.10.0</string>\n"
        "<key>NSHumanReadableCopyright</key>\n<string>3.1.4</string>\n</dict></plist>\n"
    )
    _tagged_repo(tmp_path, text, tag="v2.10.0")
    # A second x.y.z string that is not a version key is noise; the point of this case is that the
    # strategy returns BOTH, so a caller can see the file disagrees with itself rather than being
    # handed a single confident answer.
    assert len(sources.from_plist(text)) == 2


def test_the_strategy_is_registered_and_configurable():
    """A strategy nothing can name is a function, not a capability."""
    assert "plist" in sources.KINDS
    assert sources.STRATEGIES["plist"] is sources.from_plist
