"""The remote worker's image, read statically (split out of tests/test_remote_shard.py for its line
cap): every version a named release with its checksum, the manifest's pins as build args, and every
tool the suite's gates refuse without in the image (GOH.REMOTEWORKERS, GOH.WORKERFAILS)."""

from __future__ import annotations

import re

from conftest import REPO_ROOT

DOCKERFILE = REPO_ROOT / "tools" / "remote_worker" / "Dockerfile"
TSV = REPO_ROOT / "gates" / "required_tools.tsv"


def _pins() -> dict[str, str]:
    """The manifest's exact pins (`=X.Y.Z`) for the pip modules the suite imports."""
    pins: dict[str, str] = {}
    for line in TSV.read_text().splitlines():
        f = line.split("\t")
        if len(f) == 6 and f[0] == "goh-suite" and f[2] == "pip" and f[5].startswith("="):
            pins[f[1]] = f[5][1:]
    return pins


def test_the_dockerfile_names_only_releases_and_checksums_every_download():
    text = DOCKERFILE.read_text()
    froms = re.findall(r"^FROM\s+(\S+)", text, re.MULTILINE)
    assert froms, "no FROM"
    for ref in froms:
        assert re.fullmatch(r"[\w./-]+:\d+\.\d+\.\d+[\w.-]*@sha256:[0-9a-f]{64}", ref), ref
    assert not re.search(r":(latest|main|edge|nightly|stable)\b", text)
    args = dict(re.findall(r"^ARG\s+(\w+)=(\S+)", text, re.MULTILINE))
    versions = {k: v for k, v in args.items() if k.endswith("_VERSION")}
    assert versions
    for k, v in versions.items():
        assert re.fullmatch(r"\d+\.\d+\.\d+", v), f"{k}={v} is not a named release"
    bare = set(re.findall(r"^ARG\s+(\w+)\s*$", text, re.MULTILINE))
    for url_var in set(re.findall(r"curl [^\n]*?\$\{(\w+)_VERSION\}", text)):
        sha = f"{url_var}_SHA256"  # a pinned default, or a build arg read from goh's own table
        assert sha in bare or re.fullmatch(r"[0-9a-f]{64}", args.get(sha, "")), url_var
        assert re.search(rf"\$\{{{sha}\}}\s+\S+\"\s*\|\s*sha256sum -c", text), url_var


def test_every_tool_the_manifest_versions_is_a_build_arg_without_a_default():
    text = DOCKERFILE.read_text()
    bare = set(re.findall(r"^ARG\s+(\w+)\s*$", text, re.MULTILINE))
    owned = {m.upper().replace("-", "_") + "_VERSION" for m in _pins()}
    owned |= {"SHELLCHECK_VERSION", "SHELLCHECK_SHA256", "CARGO_LLVM_COV_VERSION"}
    owned |= {"SWIFTLINT_VERSION"}
    assert owned <= bare, f"written in the Dockerfile, not read from the manifest: {owned - bare}"
    rows = (ln.split("\t") for ln in TSV.read_text().splitlines())
    floors = {f[1]: f[5] for f in rows if len(f) == 6}
    base = re.search(r"^FROM\s+python:(\d+\.\d+)\.\d+", text, re.MULTILINE)
    assert base and base.group(1) == floors["python3"]


def test_every_tool_the_suite_and_the_rust_gate_refuse_without_is_in_the_image():
    """A tool the manifest says a suite preflight or a gate the suite drives REFUSES without is in
    the image and in its version record (GOH.WORKERFAILS): without cargo-audit 37 rust-gate tests
    and without swiftlint 16 tools/pytest.sh tests failed on the worker, at their preflight."""
    text = DOCKERFILE.read_text()
    record = re.search(r"^RUN \{(.*)\} > /opt/goh-worker-versions\.txt", text, re.MULTILINE)
    assert record, "no version record"
    rows = (ln.split("\t") for ln in TSV.read_text().splitlines() if not ln.startswith("#"))
    layers = {"structural", "goh-suite", "rust"}
    tools = {f[1] for f in rows if len(f) == 6 and f[0] in layers and f[2] != "pip"}
    missing = sorted(
        t for t in tools if not re.search(rf"(?<![\w-]){t} (-V|--?version)", record[1])
    )
    assert not missing, f"refused-without tools the image does not record: {missing}"
