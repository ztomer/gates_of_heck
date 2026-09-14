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

echo "Building goh for ${OS}/${ARCH}…"
cargo build --release --manifest-path "${PROJECT_ROOT}/Cargo.toml"
