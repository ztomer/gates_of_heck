#!/usr/bin/env bash
# Build script for goh (Rust port).
# Platform gate: 64-bit only; macOS is Apple silicon only.
# Linux keeps x86_64 (and aarch64). This repo MUST remain Linux compatible.
#
# OS/ARCH are overridable so the gate is testable on one machine
# (tests/test_goh_platform_gate.py drives it).
# GOH_BUILD_GATE_ONLY=1 stops after the gate, before cargo.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=tui/lib.sh
. "${PROJECT_ROOT}/tui/lib.sh"

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
#   --rev SHA    build that commit into the store only, never bin/goh: the resolver's call for a
#                gate pinned to a commit the live checkout has moved past (gates/_goh_bin.sh)
#   --rev SHA --as-release vX.Y.Z
#                the build that commit WILL be once tagged vX.Y.Z, into the store only: stamped and
#                versioned as the tag, so the first gate to ask after the tag finds it built.
#                tools/release_goh.sh starts it beside its land and push (GOH.TAGPUSHGATE): the tag
#                push's check found the tagged stamp unbuilt and rebuilt goh, 98.6 s of v0.125.0's
#                ship at load 190. A commit tagged with another release is refused; a prebuild for
#                a tag never made is a store entry no stamp asks for.
# Every build also lands in the store, `bin/by-stamp/<sha1 of its stamp>/goh`, which is what the
# resolver answers with: a gate gets the build of the commit it pinned, whatever bin/goh is by then.
INPUTS=(crates Cargo.toml Cargo.lock rust-toolchain.toml)
IF_STALE=""
REV="HEAD"
AS_RELEASE=""
case "${1:-}" in
  --if-stale) IF_STALE=1 ;;
  --rev)
    REV="${2:-}"
    [[ -n "${REV}" ]] || { echo "✗ build-goh: --rev needs a commit" >&2; exit 2; }
    if [[ "${3:-}" == "--as-release" ]]; then
      AS_RELEASE="${4:-}"
      [[ "${AS_RELEASE}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] \
        || die "build-goh: --as-release needs vX.Y.Z (got '${AS_RELEASE}')" 2
    fi
    ;;
esac
# shellcheck disable=SC2046  # word-splitting git's variable list is the point
git_here() { (unset ${GOH_GIT_LOCAL_VARS:-$(git rev-parse --local-env-vars 2>/dev/null)}; git -C "${PROJECT_ROOT}" "$@"); }
# Captured first: git ECHOES a revision it cannot resolve (no HEAD yet), which must read as empty.
# The release field (` vX.Y.Z` on a tagged commit) is lib/goh_version.sh's, as gates/_goh_bin.sh's
# goh_stamp_at and build.rs append it (GOH.SHIPRELREUSE).
# shellcheck source=lib/goh_version.sh
. "${PROJECT_ROOT}/lib/goh_version.sh"
head_stamp() { local out; out="$(git_here rev-parse "${INPUTS[@]/#/${REV}:}" 2>/dev/null)" || return 0
  out="${out} ${AS_RELEASE:-$(goh_release_at "${PROJECT_ROOT}" "${REV}")}"
  printf '%s' "${out}" | tr '\n' ' ' | sed 's/ *$//'; }

if [[ -n "${AS_RELEASE}" ]]; then
  # A commit, never the name HEAD: every `REV != HEAD` below means "the store only", and a build
  # as a tag HEAD does not carry must never reach bin/goh (a literal `--rev HEAD` placed it there).
  REV="$(git_here rev-parse -q --verify "${REV}^{commit}" 2>/dev/null)" \
    || die "build-goh: --as-release names no commit of this checkout" 2
  carried="$(goh_release_at "${PROJECT_ROOT}" "${REV}")"
  if [[ -n "${carried}" && "${carried}" != "${AS_RELEASE}" ]]; then
    die "build-goh: ${REV} is tagged ${carried}; it is never built as ${AS_RELEASE}" 2
  fi
fi
STAMP="$(head_stamp)"
if [[ -n "${GOH_BUILD_PUBLISH_CHECK_ONLY:-}" ]]; then
  echo "publish ok${STAMP:+ (the committed source at HEAD)}"
  exit 0
fi
# A --rev of the commit HEAD already is: an ordinary build (bin/goh too) -- unless it is built as
# a release HEAD does not carry yet, which never reaches bin/goh (bin/goh is HEAD's, C3).
if [[ -z "${AS_RELEASE}" && "${REV}" != "HEAD" && "$(git_here rev-parse -q --verify "${REV}^{commit}" 2>/dev/null)" \
      == "$(git_here rev-parse -q --verify HEAD 2>/dev/null)" ]]; then
  REV="HEAD"
fi
if [[ "${REV}" != "HEAD" && -z "${STAMP}" ]]; then
  echo "✗ build-goh: ${REV} is not a commit of this checkout with goh's build inputs" >&2
  exit 2
fi
# The store needs a stamp to key on: a tree that is not a checkout (a tarball) has none, builds
# bin/goh alone, and never reads the resolver (which such a tree need not even carry).
STORED=""
if [[ -n "${STAMP}" ]]; then
  # shellcheck source=gates/_goh_bin.sh
  . "${PROJECT_ROOT}/gates/_goh_bin.sh"
  STORED="$(goh_store_path "${PROJECT_ROOT}" "${STAMP}")"
fi

current() { [[ -n "${STAMP}" && -x "${PROJECT_ROOT}/bin/goh" \
  && "$("${PROJECT_ROOT}/bin/goh" source-tree 2>/dev/null)" == "${STAMP}" ]]; }

# ONE BUILDER AT A TIME. Every repo's hook can find the binary stale in the same minute; they wait
# for one build instead of racing thirty. The owner is a pid, so a killed builder's lock is reaped.
# The lock is this checkout's, under the run root (lib/run_path.sh), never in its work tree: as
# `bin/.build.lock` it read "1 uncommitted" whenever a build was killed or merely running.
# shellcheck source=lib/run_path.sh
. "${PROJECT_ROOT}/lib/run_path.sh"
RUN_DIR="$(goh_run_dir "${PROJECT_ROOT}")" || { echo "✗ build-goh: no run directory for its lock" >&2; exit 2; }
LOCK="${RUN_DIR}/build.lock"
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
if [[ "${REV}" != "HEAD" && "$(goh_stamp_of "${STORED}")" == "${STAMP}" ]]; then
  exit 0   # another gate pinned to the same commit built it while this one waited
fi

if [[ -n "${STAMP}" ]]; then
  # A STABLE export path, updated in place: `read-tree -u` rewrites only files that changed, so
  # cargo's fingerprints stay warm and a one-file change rebuilds one crate, not the workspace.
  # A --rev build has its own export, so it never churns HEAD's warm one.
  cache="${XDG_CACHE_HOME:-${HOME}/.cache}/goh/build/$(printf %s "${PROJECT_ROOT}" | cksum | cut -d' ' -f1)"
  [[ "${REV}" == "HEAD" ]] || cache="${cache}-rev"
  mkdir -p "${cache}/src"
  # One export per root ever built from -- every HEAD export among them -- so prune them like the
  # HEAD exports (gates/_head_cache.py): a week unused, or past the newest 20 and idle an hour.
  : >"${cache}/.goh-used"
  # The house interpreter (lib/house_python.sh, G2d), never PATH's first python3. Best-effort here.
  # shellcheck source=lib/house_python.sh
  { . "${PROJECT_ROOT}/lib/house_python.sh" \
      && "${GOH_PY}" -S -c 'import sys; sys.path.insert(0, sys.argv[1]); import _head_cache; _head_cache.prune(sys.argv[2], sys.argv[3])' \
        "${PROJECT_ROOT}/gates" "$(dirname "${cache}")" "$(basename "${cache}")"; } 2>/dev/null || true
  # shellcheck disable=SC2046  # word-splitting git's variable list is the point
  (unset ${GOH_GIT_LOCAL_VARS:-$(git rev-parse --local-env-vars 2>/dev/null)}
   GIT_INDEX_FILE="${cache}/index" git -C "${PROJECT_ROOT}" --work-tree="${cache}/src" read-tree --reset -u "${REV}")
  SRC="${cache}/src"
  if [[ "${REV}" == "HEAD" ]]; then
    dirty="$(git_here status --porcelain --untracked-files=normal -- "${INPUTS[@]}" || true)"
    [[ -z "${dirty}" ]] || echo "· building HEAD; these uncommitted edits are NOT in it (try them: cargo build --release -p goh, then GOH_BIN=<that binary>):" >&2
    [[ -z "${dirty}" ]] || echo "${dirty}" | sed 's/^/    /' >&2
  fi
else
  SRC="${PROJECT_ROOT}"   # not a git checkout (a tarball): build what is here, stamped `unknown`
fi

echo "Building goh for ${OS}/${ARCH} from ${STAMP:+${REV} }source…"
# The export has no .git, so the version is read here, by the one rule (lib/goh_version.sh): the
# tag on a release commit, `X.Y.Z-N-g<sha>` past one, never a number from Cargo.toml.
VERSION="$(goh_version_at "${PROJECT_ROOT}" "${REV}")"
[[ -z "${AS_RELEASE}" ]] || VERSION="${AS_RELEASE#v}"
GOH_VERSION="${VERSION}" GOH_SOURCE_STAMP="${STAMP:-unknown}" cargo build --release --quiet --locked \
  --manifest-path "${SRC}/Cargo.toml" -p goh

# Place the binary where gates/structural.sh looks first (bin/goh, gitignored).
# Stage + mv: a hook executing the old binary right now keeps its inode; the
# new one lands atomically.
# shellcheck source=lib/house_python.sh
[[ -n "${GOH_PY:-}" && "${GOH_PY}" == "${GOH_PY_OK:-}" ]] || . "${PROJECT_ROOT}/lib/house_python.sh"
TARGET_DIR="$(cargo metadata --format-version 1 --no-deps --manifest-path "${SRC}/Cargo.toml" \
    | "${GOH_PY}" -c 'import json,sys; print(json.load(sys.stdin)["target_directory"])')"
BIN="${TARGET_DIR}/release/goh"
if [[ ! -x "${BIN}" ]]; then
  echo "✗ release binary not found at ${BIN}" >&2
  exit 1
fi
WANT="${VERSION}"
if [[ "${REV}" != "HEAD" ]]; then
  goh_store_adopt "${BIN}" "${STORED}"
  if [[ "$(goh_stamp_of "${STORED}")" != "${STAMP}" ]]; then
    echo "✗ the store's build of ${REV} is not stamped with its source: $(goh_stamp_of "${STORED}")" >&2
    exit 1
  fi
  echo "✓ goh for ${REV:0:7} ready in the store ($("${STORED}" --version | awk '{print $NF}'))"
  exit 0
fi
# shellcheck source=lib/place.sh
. "${PROJECT_ROOT}/lib/place.sh"   # staged and renamed; a killed copy's stage is reaped (GOH.STAGINGSTRAY)
goh_place "${BIN}" "${PROJECT_ROOT}/bin/goh"

# The binary that landed is the one HEAD describes: its version and its source stamp.
GOT="$("${PROJECT_ROOT}/bin/goh" --version | awk '{print $NF}')"
if [[ "${GOT}" != "${WANT}" ]]; then
  echo "✗ bin/goh reports ${GOT}, the tag says ${WANT} (lib/goh_version.sh)" >&2
  exit 1
fi
if [[ -n "${STAMP}" ]] && ! current; then
  echo "✗ bin/goh's source stamp is not HEAD's: $("${PROJECT_ROOT}/bin/goh" source-tree)" >&2
  exit 1
fi
[[ -z "${STAMP}" ]] || goh_store_adopt "${PROJECT_ROOT}/bin/goh" "${STORED}"
echo "✓ bin/goh ready (${GOT})"
exit
} # parse-guard
