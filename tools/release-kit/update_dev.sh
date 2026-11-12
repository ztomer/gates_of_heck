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
#                     symlinks and metadata a plain cp can mangle), then READ IT
#                     BACK: the destination must equal the source (diff -r)
#   4. de-quarantine  /usr/bin/xattr BY ABSOLUTE PATH: Homebrew's python xattr
#                     shadows it and does not implement -r; bare `xattr -dr`
#                     printed usage and reported success with quarantine still
#                     set (found on this machine). Then VERIFY the flag is gone
#                     instead of assuming.
#   5. sign           ad-hoc codesign when available (keeps macOS from
#                     re-flagging the bundle after the copy)
#   6. launch         the app is put back as it was: a copy step 2 quit is relaunched
#                     (GOH.APPSTATE: Routines.app was left STOPPED by three kit installs on
#                     2026-11-07/08), and seen running again before it says so. --no-launch
#                     keeps it stopped; --launch / UPDATE_DEV_LAUNCH=1 launches one that was
#                     not running. A copy already running is never opened again: `open` on a
#                     running app is a reopen, which shows its window (RT.LAUNCHDASH). This step
#                     is the ONE launch path -- callers pass --launch, never `open` after it.
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
#   AFTER_INSTALL_CMD optional shell string run after the app is installed and BEFORE the launch
#                            step: a caller's other artifacts (routines' CLI) land before anything
#                            can fail at launch, so a failed launch never leaves the app new and
#                            the rest old (RT.INSTALLORDER, 2026-11-08: `open` -600 killed
#                            routines' install.sh between the app and its CLI). Failing, it is
#                            reported and nothing is launched.
#   LSREGISTER    optional   lsregister path (test seam); a failed `open` re-registers the bundle
#                            with LaunchServices and is retried ONCE, then fails as
#                            "relaunch failed" (exit 4) with every artifact already installed.
#
# Usage:
#   APP_NAME=Demo.app BUILD_CMD="make build" APP_PATH=build/Demo.app \
#     tools/release-kit/update_dev.sh [--launch | --no-launch]
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
. "$(dirname "${BASH_SOURCE[0]}")/../../lib/bash5.sh" && goh_bash5 "$@"
. "$(dirname "${BASH_SOURCE[0]}")/../../gates/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail

GOH="${GOH_DIR:-$HOME/Projects/gates_of_heck}"
# shellcheck disable=SC1091
source "$GOH/tui/lib.sh"

APP_NAME="${APP_NAME:-}"
BUILD_CMD="${BUILD_CMD:-}"
APP_PATH="${APP_PATH:-}"
DEST_DIR="${UPDATE_DEV_DEST:-${DEST_DIR:-/Applications}}"
LAUNCH=0
NO_LAUNCH=0
WAS_RUNNING=0

for arg in "$@"; do
  case "$arg" in
    --launch|-l) LAUNCH=1 ;;
    --no-launch) NO_LAUNCH=1 ;;
    *) die "unknown option: $arg (supported: --launch, --no-launch)" ;;
  esac
done
[ "${UPDATE_DEV_LAUNCH:-0}" = "1" ] && LAUNCH=1
[ "$LAUNCH" = 1 ] && [ "$NO_LAUNCH" = 1 ] && die "--launch and --no-launch contradict each other"

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
  WAS_RUNNING=1  # step 6 puts it back unless --no-launch
  info "quitting the running ${PROCESS_NAME} (relaunched after the install)"
  osascript -e "tell application \"${APP_BASE}\" to quit" >/dev/null 2>&1 || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    pgrep -x "$PROCESS_NAME" >/dev/null 2>&1 || break
    sleep 0.3
  done
  if pgrep -x "$PROCESS_NAME" >/dev/null 2>&1; then pkill -x "$PROCESS_NAME" || true; fi
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    pgrep -x "$PROCESS_NAME" >/dev/null 2>&1 || break
    sleep 0.3
  done
  # A copy still up is neither replaced (it keeps serving the old binary) nor opened again later
  # (an `open` of a running app is a reopen, step 6).
  if pgrep -x "$PROCESS_NAME" >/dev/null 2>&1; then
    die "${PROCESS_NAME} would not quit, gracefully or by signal -- nothing installed"
  fi
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
# Read the destination back (G16): `ditto` exiting 0 is not the copy being there. Compared BEFORE
# the xattr and signing steps change the installed bundle, symlinks as links (--no-dereference).
if ! { [ -d "$DEST" ] && diff -rq --no-dereference "$APP_PATH" "$DEST" >/dev/null 2>&1; }; then
  die "update_dev: $DEST differs from $APP_PATH after ditto -- nothing installed"
fi

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
    if codesign --force --deep --sign - "$DEST" >/dev/null 2>&1; then
      ok "signed"
    else
      warn "ad-hoc signing failed; the app should still launch"
    fi
  fi
fi

ok "installed $DEST"
if [ -n "${AFTER_INSTALL_CMD:-}" ]; then
  info "installing the companion artifacts: ${AFTER_INSTALL_CMD}"
  bash -c "$AFTER_INSTALL_CMD" || die "companion install failed (${AFTER_INSTALL_CMD}) -- nothing launched"
fi
if [ "$LAUNCH" = 1 ] || { [ "$WAS_RUNNING" = 1 ] && [ "$NO_LAUNCH" = 0 ]; }; then
  if pgrep -x "$PROCESS_NAME" >/dev/null 2>&1; then
    # Never `open` a running copy: macOS sends it a reopen event, and an app answers a reopen with
    # its window (RT.LAUNCHDASH: Routines.app showed the dashboard nobody had open). This step is
    # the ONE launch path; a caller that opens the app again after it reintroduces that reopen.
    ok "${PROCESS_NAME} is already running (not opened again: that would be a reopen)"
    exit
  fi
  if [ "$WAS_RUNNING" = 1 ]; then info "relaunching ${PROCESS_NAME} (it was running before the install)"; else info "launching"; fi
  if ! open "$DEST"; then
    # LaunchServices may not have registered the new bundle yet (`open` -600, 2026-11-08):
    # register it, then try once more. Every artifact is installed by now either way.
    LSREG="${LSREGISTER:-/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister}"
    warn "open $DEST failed -- registering it with LaunchServices and retrying once"
    "$LSREG" -f "$DEST" >/dev/null 2>&1 || warn "lsregister -f $DEST failed"
    if ! open "$DEST"; then
      err "relaunch failed: open $DEST failed twice -- ${PROCESS_NAME} is NOT running (every artifact is installed; open it by hand)"
      exit 4
    fi
  fi
  # Seen running before it is said (G15): `open` returning is not the app being up.
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    pgrep -x "$PROCESS_NAME" >/dev/null 2>&1 && break
    sleep 0.2
  done
  if pgrep -x "$PROCESS_NAME" >/dev/null 2>&1; then
    ok "${PROCESS_NAME} is running"
  else
    warn "opened $DEST, but no ${PROCESS_NAME} process showed up within 3 s"
  fi
elif [ "$WAS_RUNNING" = 1 ]; then
  warn "${PROCESS_NAME} was running and is now STOPPED (--no-launch): open $DEST"
else
  info "launch it with: open $DEST   (or re-run with --launch)"
fi
exit
} # parse-guard
