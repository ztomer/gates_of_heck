#!/usr/bin/env bash
# Build script for goh (Rust port).
# Platform gate: 64-bit only; macOS is Apple silicon only.
# Linux keeps x86_64 (and aarch64). This repo MUST remain Linux compatible.
#
# OS/ARCH are overridable so the gate is testable on one machine
# (tests/test_goh_platform_gate.py drives it).
# GOH_BUILD_GATE_ONLY=1 stops after the gate, before cargo.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

OS="${GOH_BUILD_OS:-$(uname -s)}"
ARCH="${GOH_BUILD_ARCH:-$(uname -m)}"

if [[ "${OS}" == "Darwin" && ( "${ARCH}" == "x86_64" || "${ARCH}" == "amd64" ) ]]; then
  echo "✗ macOS Intel (x86_64) is not supported — Apple silicon (arm64) only." >&2
  echo "  Linux x86_64 remains supported; this gate is macOS-specific." >&2
  exit 1
fi

case "${ARCH}" in
  arm64|aarch64|x86_64|amd64) ;;
  *)
    echo "✗ Unsupported architecture: ${ARCH}. 64-bit only (arm64/aarch64 or x86_64)." >&2
    exit 1
    ;;
esac

case "${OS}" in
  Darwin|Linux) ;;
  *)
    echo "✗ Unsupported OS: ${OS}. Supported: Darwin (Apple silicon), Linux." >&2
    exit 1
    ;;
esac

if [[ -n "${GOH_BUILD_GATE_ONLY:-}" ]]; then
  echo "gate ok: ${OS}/${ARCH}"
  exit 0
fi

# PUBLISH GATE. bin/goh is not a dev artefact: it is the LIVE structural gate
# of every repo whose hooks delegate here, the moment it lands. On 2026-09-23 a
# port in progress was built into bin/goh from uncommitted sources, its staged
# ceiling step was mid-edit, and ZoneWM's commits were refused for an hour by a
# check that said a pattern matching 71 files matched none. Uncommitted goh
# sources therefore build nowhere live: commit them, or try the dev build in ONE
# repo through GOH_BIN. GOH_BUILD_DIRTY=1 publishes anyway, on purpose.
# A tree that is not a git checkout (a tarball install) has nothing to compare.
dirty="$(git -C "${PROJECT_ROOT}" status --porcelain -- crates Cargo.toml Cargo.lock 2>/dev/null || true)"
if [[ -n "${dirty}" && -z "${GOH_BUILD_DIRTY:-}" ]]; then
  echo "✗ refusing to publish bin/goh from uncommitted goh sources — every repo's hooks run it:" >&2
  echo "${dirty}" | sed 's/^/    /' >&2
  echo "  commit them, or build for one repo: cargo build --release -p goh, then GOH_BIN=<that binary>" >&2
  echo "  (GOH_BUILD_DIRTY=1 publishes anyway)" >&2
  exit 1
fi
if [[ -n "${GOH_BUILD_PUBLISH_CHECK_ONLY:-}" ]]; then
  echo "publish ok"
  exit 0
fi

echo "Building goh for ${OS}/${ARCH}…"
cargo build --release --quiet --manifest-path "${PROJECT_ROOT}/Cargo.toml" -p goh

# Place the binary where gates/structural.sh looks first (bin/goh, gitignored).
# Stage + mv: a hook executing the old binary right now keeps its inode; the
# new one lands atomically.
TARGET_DIR="$(cargo metadata --format-version 1 --no-deps --manifest-path "${PROJECT_ROOT}/Cargo.toml" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["target_directory"])')"
BIN="${TARGET_DIR}/release/goh"
if [[ ! -x "${BIN}" ]]; then
  echo "✗ release binary not found at ${BIN}" >&2
  exit 1
fi
mkdir -p "${PROJECT_ROOT}/bin"
cp "${BIN}" "${PROJECT_ROOT}/bin/.goh.$$"
mv "${PROJECT_ROOT}/bin/.goh.$$" "${PROJECT_ROOT}/bin/goh"

# The binary that landed is the one the manifest describes: a stale bin/goh
# (an interrupted build, a copy from another checkout) reports the wrong
# version and would otherwise pass as "ready".
WANT="$(grep -m1 '^version = ' "${PROJECT_ROOT}/Cargo.toml" | cut -d'"' -f2)"
GOT="$("${PROJECT_ROOT}/bin/goh" --version | awk '{print $NF}')"
if [[ "${GOT}" != "${WANT}" ]]; then
  echo "✗ bin/goh reports ${GOT}, Cargo.toml says ${WANT}" >&2
  exit 1
fi
echo "✓ bin/goh ready (${GOT})"
