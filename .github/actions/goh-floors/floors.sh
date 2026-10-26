#!/usr/bin/env bash
# floors.sh -- what the goh-floors action runs: meet gates_of_heck's tool FLOORS on a CI runner.
#
#   floors.sh floor <tool>          the tool's floor from gates/required_tools.tsv, on stdout
#   floors.sh shellcheck <bindir>   a shellcheck at or above its floor: the one on PATH, or the
#                                   verified release (shellcheck.sha256) unpacked into <bindir>
#   floors.sh verify                goh's own judges, red naming the floor: the house Python
#                                   (lib/house_python.sh) and shellcheck (required_tools.py --check)
#
# WHY (G7, 2026-10-26). goh raised python3 to 3.15 and shellcheck to 0.11.0; GitHub's runners ship
# 3.12.3 and 0.9.0, and every consumer whose CI runs goh's gates went red (divoom-control run
# 40291804362: rust-core on the Python floor, no-emoji on the shellcheck one). Each workflow had
# its own idea of which Python and which shellcheck to install, so each floor bump was a sweep of
# every consumer. This is the ONE way: the action reads the floors from the tsv of the goh it
# serves, so a bump there is a bump in every CI that uses it, and nothing here restates a number.
#
# It runs BEFORE the runner meets the Python floor, so it is plain bash: never _from_head.sh, which
# refuses a Python below the floor -- the very state this script exists to leave.
#
# SEAMS (tests): GOH_FLOORS_OS / GOH_FLOORS_ARCH (the platform, default uname), GOH_FLOORS_URL (the
# release base, default GitHub's), GOH_FLOORS_TSV (the floor table), GOH_FLOORS_SUMS (the verified
# checksums). Platforms: Linux x86_64 and aarch64, macOS on Apple silicon. Anything else is
# refused -- never a fallback build.
{ # parse-guard -- bash reads this group whole before running it (tests/test_parse_guard.py)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GOH="$(cd "$HERE/../../.." && pwd)"
# GOH_TUI_BOOTSTRAP: this copy of the TUI, never the trampoline to HEAD's export -- that sources
# lib/house_python.sh, which refuses the below-floor Python this script runs on (tui/lib.sh).
# shellcheck source=tui/lib.sh
GOH_TUI_BOOTSTRAP=1 . "$GOH/tui/lib.sh"
TSV="${GOH_FLOORS_TSV:-$GOH/gates/required_tools.tsv}"
SUMS="${GOH_FLOORS_SUMS:-$HERE/shellcheck.sha256}"
URL="${GOH_FLOORS_URL:-https://github.com/koalaman/shellcheck/releases/download}"

# ge <a> <b>: a >= b, numerically, field by field (0.11.0 >= 0.9.0; 3.12.3 < 3.15).
ge() {
    local IFS=. i
    local -a a b
    read -r -a a <<<"$1"
    read -r -a b <<<"$2"
    for i in 0 1 2 3; do
        (( 10#${a[i]:-0} > 10#${b[i]:-0} )) && return 0
        (( 10#${a[i]:-0} < 10#${b[i]:-0} )) && return 1
    done
    return 0
}

floor() {
    local f
    f="$(awk -F'\t' -v t="$1" '!/^#/ && $2 == t && $6 != "-" { print $6; exit }' "$TSV")"
    [ -n "$f" ] || die "goh-floors: $TSV gives $1 no floor"
    printf '%s\n' "$f"
}

platform() {
    local os="${GOH_FLOORS_OS:-$(uname -s)}" arch="${GOH_FLOORS_ARCH:-$(uname -m)}"
    case "$os/$arch" in
        Linux/x86_64) echo linux.x86_64 ;;
        Linux/aarch64 | Linux/arm64) echo linux.aarch64 ;;
        Darwin/arm64 | Darwin/aarch64) echo darwin.aarch64 ;;
        *) die "goh-floors: $os/$arch is not a supported runner (Linux x86_64/aarch64, macOS on Apple silicon)" ;;
    esac
}

sc_version() {
    local out
    out="$("$1" --version 2>/dev/null)" || return 0
    awk '/^version:/ { print $2; exit }' <<<"$out"
}

sha256() {
    if type -P sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

shellcheck_at_floor() {
    local bindir="$1" want have plat version sum v=""
    want="$(floor shellcheck)"
    have="$(type -P shellcheck || true)"
    if [ -n "$have" ] && v="$(sc_version "$have")" && [ -n "$v" ] && ge "$v" "$want"; then
        ok "goh-floors: shellcheck $v at $have meets the floor $want"
        return 0
    fi
    [ -n "$have" ] && info "goh-floors: shellcheck ${v:-of no readable version} at $have is below the floor $want"
    plat="$(platform)"
    # The newest verified release at or above the floor, for this platform.
    read -r version sum < <(awk -F'\t' -v p="$plat" '!/^#/ && $1 == "shellcheck" && $3 == p { print $2, $4 }' "$SUMS" \
        | sort -t. -k1,1n -k2,2n -k3,3n | while read -r v s; do ge "$v" "$want" && echo "$v $s"; done | tail -1) || true
    [ -n "${version:-}" ] || die "goh-floors: no verified shellcheck release at or above the floor $want for $plat in $SUMS -- record its sha256 there in the commit that raises the floor"
    TMP="$(mktemp -d "${TMPDIR:-/tmp}/goh-floors.XXXXXX")"
    trap 'rm -rf "$TMP"' EXIT
    local tmp="$TMP"
    local asset="shellcheck-v$version.$plat.tar.gz"
    curl --proto '=https,file' --fail --silent --show-error --location --retry 5 -o "$tmp/$asset" "$URL/v$version/$asset" \
        || die "goh-floors: could not download $URL/v$version/$asset"
    [ "$(sha256 "$tmp/$asset")" = "$sum" ] || die "goh-floors: $asset does not match its recorded sha256 ($sum) -- refused"
    tar -xzf "$tmp/$asset" -C "$tmp"
    mkdir -p "$bindir"
    install -m 0755 "$tmp/shellcheck-v$version/shellcheck" "$bindir/shellcheck"
    v="$(sc_version "$bindir/shellcheck")"
    if [ -z "$v" ] || ! ge "$v" "$want"; then
        die "goh-floors: the installed shellcheck reports ${v:-no version}, below the floor $want"
    fi
    ok "goh-floors: shellcheck $v (verified release) in $bindir meets the floor $want"
}

verify() {
    # goh's OWN judges, so the verdict is the gates' verdict: the house Python and shellcheck.
    local py
    py="$(bash "$GOH/lib/house_python.sh")" || exit 1
    "$py" "$GOH/gates/required_tools.py" --check --tool shellcheck || exit 1
    ok "goh-floors: python3 $("$py" -c 'import platform; print(platform.python_version())') and shellcheck meet goh's floors"
}

case "${1:-}" in
    floor) floor "${2:?floor <tool>}" ;;
    shellcheck) shellcheck_at_floor "${2:?shellcheck <bindir>}" ;;
    verify) verify ;;
    *) die "usage: floors.sh floor <tool> | shellcheck <bindir> | verify" 2 ;;
esac
exit
} # parse-guard
