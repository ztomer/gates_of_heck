#!/usr/bin/env bash
# tools/release-kit/update_dev.sh — templated dev installer replacing the
# per-repo update_dev.sh copies (koffee_big, necrohand, routines), which agreed
# on the STEPS but drifted on every detail of them.
#
# Pipeline (the union of the ancestors' hard-won steps, in the order that
# matters):
#   1. build          run BUILD_CMD in the current directory
#   2. quit running   a live copy cannot be replaced from under itself — the
#                     swap "succeeds" and keeps serving the old binary, so you
#                     test the build you just replaced. Quit gracefully
#                     (osascript), then pkill as fallback.
#   3. install        replace $DEST_DIR/$APP_NAME (ditto — preserves signature,
#                     symlinks and metadata a plain cp can mangle)
#   4. de-quarantine  /usr/bin/xattr BY ABSOLUTE PATH: Homebrew's python xattr
#                     shadows it and does not implement -r; bare `xattr -dr`
#                     printed usage and reported success with quarantine still
#                     set (found on this machine). Then VERIFY the flag is gone
#                     instead of assuming.
#   5. sign           ad-hoc codesign when available (keeps macOS from
#                     re-flagging the bundle after the copy)
#   6. launch         only with --launch / UPDATE_DEV_LAUNCH=1
#
# Per-repo overrides — put these in a tiny wrapper script or .envrc:
#   APP_NAME    required   e.g. "Necrohand.app"
#   BUILD_CMD   required   shell string, run from repo root, e.g. "./build.sh"
#   APP_PATH    required   built bundle relative to repo root, e.g. "build/Necrohand.app"
#   DEST_DIR      optional   install target, default /Applications
#                            (UPDATE_DEV_DEST also works; tests point it at tmp)
#   PROCESS_NAME  optional   process name the running copy is detected by,
#                            default APP_NAME minus .app. Set when the binary's
#                            name differs from the bundle (e.g. routines ships
#                            Routines.app whose executable is routines-menubar;
#                            pgrep -x on the bundle name never matched, so the
#                            quit-before-replace step silently no-oped).
#   LAUNCH        optional   UPDATE_DEV_LAUNCH=1 same as --launch
#
# Usage:
#   APP_NAME=Demo.app BUILD_CMD="make build" APP_PATH=build/Demo.app \
#     tools/release-kit/update_dev.sh [--launch]
set -euo pipefail

GOH="${GOH_DIR:-$HOME/Projects/gates_of_heck}"
# shellcheck disable=SC1091
source "$GOH/tui/lib.sh"

APP_NAME="${APP_NAME:-}"
BUILD_CMD="${BUILD_CMD:-}"
APP_PATH="${APP_PATH:-}"
DEST_DIR="${UPDATE_DEV_DEST:-${DEST_DIR:-/Applications}}"
LAUNCH=0

for arg in "$@"; do
  case "$arg" in
    --launch|-l) LAUNCH=1 ;;
    *) die "unknown option: $arg (supported: --launch)" ;;
  esac
done
[ "${UPDATE_DEV_LAUNCH:-0}" = "1" ] && LAUNCH=1

[ -n "$APP_NAME" ] || die "APP_NAME is required (e.g. APP_NAME=Demo.app)"
[ -n "$BUILD_CMD" ] || die "BUILD_CMD is required (e.g. BUILD_CMD='./build.sh')"
[ -n "$APP_PATH" ] || die "APP_PATH is required (built bundle, relative to repo root)"

APP_BASE="${APP_NAME%.app}"
PROCESS_NAME="${PROCESS_NAME:-$APP_BASE}"
DEST="$DEST_DIR/$APP_NAME"

section "update_dev → $DEST"

# ── 1. build ─────────────────────────────────────────────────────────────────
info "building: ${BUILD_CMD}"
bash -c "$BUILD_CMD" || die "build failed (${BUILD_CMD})"
[ -d "$APP_PATH" ] || die "build did not produce $APP_PATH"

# ── 2. quit any running copy first ───────────────────────────────────────────
if pgrep -x "$PROCESS_NAME" >/dev/null 2>&1; then
  info "quitting the running ${PROCESS_NAME}"
  osascript -e "tell application \"${APP_BASE}\" to quit" >/dev/null 2>&1 || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    pgrep -x "$PROCESS_NAME" >/dev/null 2>&1 || break
    sleep 0.3
  done
  pgrep -x "$PROCESS_NAME" >/dev/null 2>&1 && pkill -x "$PROCESS_NAME" || true
fi

# ── 3. replace the installed copy ────────────────────────────────────────────
if [ -d "$DEST" ]; then
  step "replacing $DEST (config/state elsewhere is untouched)"
  rm -rf "$DEST"
else
  step "first install"
fi
mkdir -p "$DEST_DIR"
ditto "$APP_PATH" "$DEST" || die "could not write to $DEST_DIR (try sudo, or set DEST_DIR)"

# ── 4. clear Gatekeeper xattrs — then PROVE it took ──────────────────────────
XATTR=/usr/bin/xattr
step "clearing quarantine/provenance attributes"
"$XATTR" -dr com.apple.quarantine "$DEST" 2>/dev/null || true
find "$DEST" -exec "$XATTR" -d com.apple.provenance {} + 2>/dev/null || true
if "$XATTR" -pr com.apple.quarantine "$DEST" >/dev/null 2>&1; then
  warn "com.apple.quarantine is still set — the app may refuse to launch"
else
  ok "quarantine clear"
fi

# ── 5. sign ──────────────────────────────────────────────────────────────────
# A bundle that arrived already validly signed KEEPS its signature: an
# unconditional ad-hoc re-sign would clobber a real identity the build chose
# deliberately (routines' Makefile picks the first valid codesigning identity).
# Unsigned/invalid bundles still get the ad-hoc fallback.
if command -v codesign >/dev/null 2>&1; then
  if codesign -v "$DEST" >/dev/null 2>&1; then
    step "existing signature is valid — keeping it"
  else
    step "ad-hoc signing"
    codesign --force --deep --sign - "$DEST" >/dev/null 2>&1 \
      && ok "signed" || warn "ad-hoc signing failed; the app should still launch"
  fi
fi

ok "installed $DEST"
if [ "$LAUNCH" = 1 ]; then
  info "launching"
  open "$DEST"
  ok "launched"
else
  info "launch it with: open $DEST   (or re-run with --launch)"
fi
