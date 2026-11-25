#!/usr/bin/env bash
# tools/release-kit/update_dev.sh — templated dev installer replacing the
# per-repo update_dev.sh copies (koffee_big, necrohand, a private Rust app), which agreed
# on the STEPS but drifted on every detail of them.
#
# Pipeline (the union of the ancestors' hard-won steps, in the order that
# matters):
#   1. build          run BUILD_CMD in the current directory
#   2. stage          ditto the bundle (preserves signature, symlinks and metadata a plain cp can
#                     mangle) into a hidden directory of this run beside the installed copy, then
#                     READ IT BACK: the staged copy must equal the source (diff -r). Nothing
#                     installed or running is touched before step 5 (GOH.DEVSWAP, 2026-11-25: the
#                     old order quit the app and removed $DEST first, so a failed ditto left no app,
#                     nothing running, and "nothing installed")
#   3. de-quarantine  /usr/bin/xattr BY ABSOLUTE PATH: Homebrew's python xattr
#                     shadows it and does not implement -r; bare `xattr -dr`
#                     printed usage and reported success with quarantine still
#                     set (found on this machine). Then VERIFY the flag is gone
#                     instead of assuming.
#   4. sign           ad-hoc codesign when available (keeps macOS from
#                     re-flagging the bundle after the copy)
#   5. quit running   a live copy cannot be replaced from under itself — the
#                     swap "succeeds" and keeps serving the old binary, so you
#                     test the build you just replaced. Quit gracefully
#                     (osascript), then pkill as fallback.
#   6. swap           two renames on one volume: the old copy aside, the staged one in. A failed
#                     second rename puts the old copy back. Nothing changes the bundle after it.
#   7. launch         the app is put back as it was: a copy step 5 quit is relaunched
#                     (GOH.APPSTATE: a private menu-bar app was left STOPPED by three kit installs on
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
#                            name differs from the bundle (e.g. a private Rust app ships
#                            a private menu-bar app whose executable is <app>-menubar;
#                            pgrep -x on the bundle name never matched, so the
#                            quit-before-replace step silently no-oped).
#   LAUNCH        optional   UPDATE_DEV_LAUNCH=1 same as --launch
#   AFTER_INSTALL_CMD optional shell string run after the app is installed and BEFORE the launch
#                            step: a caller's other artifacts (a private Rust app's CLI) land before anything
#                            can fail at launch, so a failed launch never leaves the app new and
#                            the rest old (RT.INSTALLORDER, 2026-11-08: `open` -600 killed
#                            a private Rust app's install.sh between the app and its CLI). Failing, it is
#                            reported and nothing is launched.
#   LSREGISTER    optional   lsregister path (test seam); a failed `open` re-registers the bundle
#                            with LaunchServices and is retried ONCE, then fails as
#                            "relaunch failed" (exit 4) with every artifact already installed.
#
# Usage:
#   APP_NAME=Demo.app BUILD_CMD="make build" APP_PATH=build/Demo.app \
#     tools/release-kit/update_dev.sh [--launch | --no-launch]
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../../lib/bash5.sh" || exit; goh_bash5 "$@"
. "$(dirname "${BASH_SOURCE[0]}")/../../gates/_from_head.sh"; goh_from_head "${BASH_SOURCE[0]}" "$@"   # run HEAD, not the tree (C4)
set -euo pipefail

GOH="${GOH_DIR:-$HOME/Projects/gates_of_heck}"
# shellcheck disable=SC1091
source "$GOH/tui/lib.sh"
# shellcheck source=lib/pid_owner.sh
. "$(dirname "${BASH_SOURCE[0]}")/../../lib/pid_owner.sh" || exit 1   # a saved pid is signalled by its stamp (GOH.STRAYSIGNAL)

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

# The staged copy: $STAGE/<base>.new, the one it replaces $STAGE/<base>.old. LaunchServices must see
# the app ONCE, and what keeps it away is the EXTENSION: `lsregister -R` registered an .app inside a
# hidden directory, skipped a .new, and refused one named to it (-10811; probed 2026-11-25). The
# base name stays, because codesign and dyld derive a plist-less bundle's identifier and executable
# from it. The directory names its owner, so a killed run's is told from a live one's.
STAGE="$DEST_DIR/.$APP_NAME.goh-devswap.$$"
STAGED="$STAGE/$APP_BASE.new"
DITTO_PID=""
DITTO_STAMP=""
SWAPPING=0

present() { [ -e "$1" ] || [ -L "$1" ]; }

# rename(2) itself: an existing target is an error, where mv(1) would move INTO it.
rename_to() {
  "$GOH_PY" -I -S -c 'import os, sys
try: os.rename(sys.argv[1], sys.argv[2])
except OSError as e: sys.exit(str(e))' "$1" "$2"
}

# unstage <dir> -- remove a staging directory; the old copy in it goes back first when nothing
# stands at $DEST, and the directory is kept when it cannot.
unstage() {
  if present "$1/$APP_BASE.old" && ! present "$DEST"; then
    rename_to "$1/$APP_BASE.old" "$DEST" \
      || { err "the previous copy could not be put back: it is at $1/$APP_BASE.old"; return 1; }
    warn "the previous copy is back at $DEST"
  fi
  rm -rf "$1" 2>/dev/null || { chmod -R u+w "$1" 2>/dev/null; rm -rf "$1"; } || warn "could not remove $1"
}

finish() {
  local rc=$? told="interrupted before the swap -- nothing installed"
  trap '' HUP INT TERM
  if [ -n "$DITTO_PID" ]; then
    goh_pid_kill TERM "$DITTO_STAMP" || true   # refused once the number is no longer that ditto
    wait "$DITTO_PID" 2>/dev/null || true
  fi
  if [ "$SWAPPING" = 1 ] && ! present "$STAGED"; then told="interrupted after the swap -- installed $DEST"; fi
  unstage "$STAGE" || true
  if [ "$rc" -ge 129 ]; then
    err "$told"
    [ "$WAS_RUNNING" = 0 ] || warn "${PROCESS_NAME} had been asked to quit and was not relaunched: open $DEST"
  fi
  exit "$rc"
}

# A killed run's leftovers, and the copy it had moved aside; a live owner's are its own. The number
# in the name is not the owner by itself: a process there that started after the directory was
# last written is a stranger handed the number since (goh_pid_alive_since, GOH.STRAYSIGNAL3).
for left in "$DEST_DIR/.$APP_NAME.goh-devswap."*; do
  owner="${left##*.}"
  case "$owner" in "" | *[!0-9]*) continue ;; esac
  [ "$owner" = "$$" ] || ! goh_pid_alive_since "$owner" "$left" || continue
  unstage "$left" || true
done

# ── 1. build ─────────────────────────────────────────────────────────────────
info "building: ${BUILD_CMD}"
bash -c "$BUILD_CMD" || die "build failed (${BUILD_CMD})"
[ -d "$APP_PATH" ] || die "build did not produce $APP_PATH"

# ── 2. stage beside the installed copy, and read it back ─────────────────────
trap finish EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
{ mkdir -p "$DEST_DIR" && mkdir "$STAGE"; } \
  || die "could not write to $DEST_DIR (try sudo, or set DEST_DIR) -- nothing installed"
step "staging the new copy in $STAGE"
# In the background: a signal reaches `wait` at once, where a foreground ditto would defer the trap.
ditto "$APP_PATH" "$STAGED" &
DITTO_PID=$!
DITTO_STAMP="$(goh_pid_stamp "$DITTO_PID")" || DITTO_STAMP=""   # already ended: nothing to stop
copied=0
wait "$DITTO_PID" || copied=$?
DITTO_PID=""
[ "$copied" = 0 ] || die "ditto could not copy $APP_PATH to $DEST_DIR (try sudo, or set DEST_DIR) -- nothing installed"
# Read the copy back (G16): `ditto` exiting 0 is not the copy being there. Compared BEFORE the
# xattr and signing steps change it, symlinks as links (--no-dereference).
if ! { [ -d "$STAGED" ] && diff -rq --no-dereference "$APP_PATH" "$STAGED" >/dev/null 2>&1; }; then
  die "update_dev: $STAGED differs from $APP_PATH after ditto -- nothing installed"
fi
# A directory its owner cannot write cannot be renamed into another one (EACCES): asked of both
# bundles here, before anything running is quit.
for bundle in "$STAGED" "$DEST"; do
  if [ -d "$bundle" ] && [ ! -L "$bundle" ] && [ ! -w "$bundle" ]; then
    chmod u+w "$bundle" 2>/dev/null || die "$bundle is not yours to replace (try sudo) -- nothing installed"
  fi
done

# ── 3. clear Gatekeeper xattrs — then PROVE it took ──────────────────────────
XATTR=/usr/bin/xattr
step "clearing quarantine/provenance attributes"
"$XATTR" -dr com.apple.quarantine "$STAGED" 2>/dev/null || true
find "$STAGED" -exec "$XATTR" -d com.apple.provenance {} + 2>/dev/null || true
if "$XATTR" -pr com.apple.quarantine "$STAGED" >/dev/null 2>&1; then
  warn "com.apple.quarantine is still set — the app may refuse to launch"
else
  ok "quarantine clear"
fi

# ── 4. sign ──────────────────────────────────────────────────────────────────
# A bundle that arrived already validly signed KEEPS its signature: an
# unconditional ad-hoc re-sign would clobber a real identity the build chose
# deliberately (a private Rust app's Makefile picks the first valid codesigning identity).
# Unsigned/invalid bundles still get the ad-hoc fallback.
if command -v codesign >/dev/null 2>&1; then
  if codesign -v "$STAGED" >/dev/null 2>&1; then
    step "existing signature is valid — keeping it"
  else
    step "ad-hoc signing"
    if codesign --force --deep --sign - "$STAGED" >/dev/null 2>&1; then
      ok "signed"
    else
      warn "ad-hoc signing failed; the app should still launch"
    fi
  fi
fi

# ── 5. quit any running copy ─────────────────────────────────────────────────
if pgrep -x "$PROCESS_NAME" >/dev/null 2>&1; then
  WAS_RUNNING=1  # step 7 puts it back unless --no-launch
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
  # (an `open` of a running app is a reopen, step 7).
  if pgrep -x "$PROCESS_NAME" >/dev/null 2>&1; then
    die "${PROCESS_NAME} would not quit, gracefully or by signal -- nothing installed"
  fi
fi

# ── 6. swap ──────────────────────────────────────────────────────────────────
RC=0
AT_HAND="every artifact is installed"
if present "$DEST"; then
  step "replacing $DEST (config/state elsewhere is untouched)"
  rename_to "$DEST" "$STAGE/$APP_BASE.old" || RC=1
else
  step "first install"
fi
SWAPPING=1
[ "$RC" != 0 ] || rename_to "$STAGED" "$DEST" || RC=1
unstage "$STAGE" || true   # the old copy goes, or comes back when the new one is not in
trap - EXIT HUP INT TERM
if [ "$RC" != 0 ]; then
  err "update_dev: could not swap the new copy into $DEST -- nothing installed"
  [ -d "$DEST" ] || exit 1
  # The old copy is what step 7 puts back; --launch asked for the new one.
  AT_HAND="the new copy is NOT installed"
  LAUNCH=0
else
  ok "installed $DEST"
fi

if [ "$RC" = 0 ] && [ -n "${AFTER_INSTALL_CMD:-}" ]; then
  info "installing the companion artifacts: ${AFTER_INSTALL_CMD}"
  bash -c "$AFTER_INSTALL_CMD" || die "companion install failed (${AFTER_INSTALL_CMD}) -- nothing launched"
fi
if [ "$LAUNCH" = 1 ] || { [ "$WAS_RUNNING" = 1 ] && [ "$NO_LAUNCH" = 0 ]; }; then
  if pgrep -x "$PROCESS_NAME" >/dev/null 2>&1; then
    # Never `open` a running copy: macOS sends it a reopen event, and an app answers a reopen with
    # its window (RT.LAUNCHDASH: a private menu-bar app showed the dashboard nobody had open). This
    # step is the ONE launch path; a caller that opens the app again after it reintroduces that
    # reopen.
    ok "${PROCESS_NAME} is already running (not opened again: that would be a reopen)"
    exit "$RC"
  fi
  if [ "$WAS_RUNNING" = 1 ]; then info "relaunching ${PROCESS_NAME} (it was running before the install)"; else info "launching"; fi
  # The app gets launchd's LOGIN environment, never this script's (GOH.TRANSIENTDIR): `open` hands
  # the app its own environment, and under a ship that held GOH_DIR at a transient HEAD export.
  # A private menu-bar app kept it, and every job it ran exited 2 once the export was pruned. `env
  # -i` on the opener is `goh launch-app`'s mechanism (crates/goh/src/launchapp.rs, its probe); the
  # opener is resolved first, so a test's fake on PATH still answers.
  OPENER="$(command -v open)" || die "no \`open\` on PATH -- cannot launch ${DEST}"
  if ! env -i "$OPENER" "$DEST"; then
    # LaunchServices may not have registered the new bundle yet (`open` -600, 2026-11-08):
    # register it, then try once more. Every artifact is installed by now either way.
    LSREG="${LSREGISTER:-/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister}"
    warn "open $DEST failed -- registering it with LaunchServices and retrying once"
    "$LSREG" -f "$DEST" >/dev/null 2>&1 || warn "lsregister -f $DEST failed"
    if ! env -i "$OPENER" "$DEST"; then
      err "relaunch failed: open $DEST failed twice -- ${PROCESS_NAME} is NOT running (${AT_HAND}; open it by hand)"
      [ "$RC" != 0 ] || RC=4
      exit "$RC"
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
elif [ "$RC" = 0 ]; then
  info "launch it with: open $DEST   (or re-run with --launch)"
fi
[ "$RC" = 0 ] || exit "$RC"
# `command exit`: the trap-only `finish` stays seen by shellcheck (tests/test_parse_guard.py)
command exit
} # parse-guard
