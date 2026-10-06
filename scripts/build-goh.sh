#!/usr/bin/env bash
# Build script for goh (Rust port).
# Platform gate: 64-bit only; macOS is Apple silicon only.
# Linux keeps x86_64 (and aarch64). This repo MUST remain Linux compatible.
#
# OS/ARCH are overridable so the gate is testable on one machine
# (tests/test_goh_platform_gate.py drives it).
# GOH_BUILD_GATE_ONLY=1 stops after the gate, before cargo.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
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

# Named up front: since Phase N3 the binary is the only structural tier, so a missing cargo is a
# repo with no layer-1 gate at all -- not "cargo: command not found" halfway down a build.
if ! command -v cargo >/dev/null 2>&1; then
  echo "✗ build-goh: cargo is not on PATH, and the structural gate is this binary." >&2
  echo "  Install a Rust toolchain: rustup (https://rustup.rs)" >&2
  exit 1
fi

# BUILT FROM HEAD, NEVER FROM THE WORKING TREE (BACKLOG C3). bin/goh is the live structural gate
# of every repo whose hooks delegate here; 2026-09-23 a port in progress was built into it from
# uncommitted sources and refused another repo's commits for an hour. This used to REFUSE a dirty
# tree (an override let it publish anyway); now the cause is gone: the build reads an export of
# HEAD's committed tree, so an uncommitted edit cannot reach the binary by construction, and the
# binary is stamped with the git trees it was built from (`goh source-tree`, crates/goh/build.rs).
# gates/_goh_bin.sh compares that stamp with HEAD's and calls this with --if-stale to rebuild.
# Uncommitted checks/ / gates/ / lib/ / tui/ are not this script's to judge any more: the binary
# carries none of them, and structural.sh + push_gate.sh name them at the moment they run.
#   --if-stale   build only when bin/goh's stamp differs from HEAD's (the resolver's call)
INPUTS=(crates Cargo.toml Cargo.lock rust-toolchain.toml)
IF_STALE=""
[[ "${1:-}" == "--if-stale" ]] && IF_STALE=1
# shellcheck disable=SC2046  # word-splitting git's variable list is the point
git_here() { (unset $(git rev-parse --local-env-vars 2>/dev/null); git -C "${PROJECT_ROOT}" "$@"); }
# Captured first: git ECHOES a revision it cannot resolve (no HEAD yet), which must read as empty.
head_stamp() { local out; out="$(git_here rev-parse "${INPUTS[@]/#/HEAD:}" 2>/dev/null)" || return 0
  printf '%s' "${out}" | tr '\n' ' ' | sed 's/ $//'; }

STAMP="$(head_stamp)"
if [[ -n "${GOH_BUILD_PUBLISH_CHECK_ONLY:-}" ]]; then
  echo "publish ok${STAMP:+ (the committed source at HEAD)}"
  exit 0
fi

current() { [[ -n "${STAMP}" && -x "${PROJECT_ROOT}/bin/goh" \
  && "$("${PROJECT_ROOT}/bin/goh" source-tree 2>/dev/null)" == "${STAMP}" ]]; }

# ONE BUILDER AT A TIME. Every repo's hook can find the binary stale in the same minute; they wait
# for one build instead of racing thirty. The owner is a pid, so a killed builder's lock is reaped.
LOCK="${PROJECT_ROOT}/bin/.build.lock"
mkdir -p "${PROJECT_ROOT}/bin"
for _ in $(seq 1 600); do
  if mkdir "${LOCK}" 2>/dev/null; then
    echo $$ > "${LOCK}/pid"
    break
  fi
  owner="$(cat "${LOCK}/pid" 2>/dev/null || true)"
  if [[ -n "${owner}" ]] && ! kill -0 "${owner}" 2>/dev/null; then
    rm -rf "${LOCK}"
    continue
  fi
  sleep 1
done
[[ -d "${LOCK}" && "$(cat "${LOCK}/pid" 2>/dev/null)" == "$$" ]] \
  || { echo "✗ another goh build held ${LOCK} for 10 minutes" >&2; exit 1; }
trap 'rm -rf "${LOCK}"' EXIT

if [[ -n "${IF_STALE}" ]] && current; then
  exit 0   # another hook rebuilt it while this one waited
fi

if [[ -n "${STAMP}" ]]; then
  # A STABLE export path, updated in place: `read-tree -u` rewrites only files that changed, so
  # cargo's fingerprints stay warm and a one-file change rebuilds one crate, not the workspace.
  cache="${XDG_CACHE_HOME:-${HOME}/.cache}/goh/build/$(printf %s "${PROJECT_ROOT}" | cksum | cut -d' ' -f1)"
  mkdir -p "${cache}/src"
  # shellcheck disable=SC2046  # word-splitting git's variable list is the point
  (unset $(git rev-parse --local-env-vars 2>/dev/null)
   GIT_INDEX_FILE="${cache}/index" git -C "${PROJECT_ROOT}" --work-tree="${cache}/src" read-tree --reset -u HEAD)
  SRC="${cache}/src"
  dirty="$(git_here status --porcelain --untracked-files=normal -- "${INPUTS[@]}" || true)"
  [[ -z "${dirty}" ]] || echo "· building HEAD; these uncommitted edits are NOT in it (try them: cargo build --release -p goh, then GOH_BIN=<that binary>):" >&2
  [[ -z "${dirty}" ]] || echo "${dirty}" | sed 's/^/    /' >&2
else
  SRC="${PROJECT_ROOT}"   # not a git checkout (a tarball): build what is here, stamped `unknown`
fi

echo "Building goh for ${OS}/${ARCH} from ${STAMP:+HEAD }source…"
GOH_SOURCE_STAMP="${STAMP:-unknown}" cargo build --release --quiet --locked \
  --manifest-path "${SRC}/Cargo.toml" -p goh

# Place the binary where gates/structural.sh looks first (bin/goh, gitignored).
# Stage + mv: a hook executing the old binary right now keeps its inode; the
# new one lands atomically.
TARGET_DIR="$(cargo metadata --format-version 1 --no-deps --manifest-path "${SRC}/Cargo.toml" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["target_directory"])')"
BIN="${TARGET_DIR}/release/goh"
if [[ ! -x "${BIN}" ]]; then
  echo "✗ release binary not found at ${BIN}" >&2
  exit 1
fi
cp "${BIN}" "${PROJECT_ROOT}/bin/.goh.$$"
mv "${PROJECT_ROOT}/bin/.goh.$$" "${PROJECT_ROOT}/bin/goh"

# The binary that landed is the one HEAD describes: its version and its source stamp.
WANT="$(grep -m1 '^version = ' "${SRC}/Cargo.toml" | cut -d'"' -f2)"
GOT="$("${PROJECT_ROOT}/bin/goh" --version | awk '{print $NF}')"
if [[ "${GOT}" != "${WANT}" ]]; then
  echo "✗ bin/goh reports ${GOT}, Cargo.toml says ${WANT}" >&2
  exit 1
fi
if [[ -n "${STAMP}" ]] && ! current; then
  echo "✗ bin/goh's source stamp is not HEAD's: $("${PROJECT_ROOT}/bin/goh" source-tree)" >&2
  exit 1
fi
echo "✓ bin/goh ready (${GOT})"
exit
} # parse-guard
