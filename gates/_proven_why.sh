# shellcheck shell=bash
# _proven_why.sh — sourced by gates/_proven.sh: why a proven key missed (GOH.PROOFKEY).

# ── why a key missed (GOH.PROOFKEY) ──
# A record is a bare sha256: when a pass in a lane worktree was not reused by the push from the
# main checkout, nothing said which of the key's inputs had moved (a private consumer, 2026-11-16: three full
# re-runs of a 10-26 min gate, each because goh landed while the lane's gate ran). Every record
# now keeps, beside it under .id/, the identity lines it was keyed under, and the goh manifest
# (lib/gate_identity.py `manifest`) once per goh content; a miss on a tree and step that passed
# before names the lines that differ and, when only goh moved, the goh files that changed. It
# still RE-RUNS: a goh file the step may run is a new verdict until shown otherwise.

# Sourced from zsh: refuse in words and return, never exit the caller (GOH.ZSHSOURCE).
[ -n "${BASH_VERSION-}" ] || { printf '%s\n' "gates/_proven_why.sh is bash: source it from bash, not ${ZSH_NAME:-this shell}" >&2; return 1; }
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"

# _proven_manifest — the keyed goh files by content, one `<sha> <kind> <path>` per line.
_proven_manifest() {
    local d roots=()
    while IFS= read -r d; do
        [ -n "$d" ] && roots+=(--root "$d")
    done < <(_proven_goh_roots)
    [ "${#roots[@]}" -gt 0 ] || return 0
    "$GOH_PY" -S "$_PROVEN_IDENTITY_PY" manifest "${roots[@]}" </dev/null 2>/dev/null
}

# _proven_goh_mkey <identity> — the name of the manifest an identity's goh lines stand for.
_proven_goh_mkey() {
    printf 'm-%s\n' "$(grep '^goh ' <<<"$1" | hash_hex /dev/stdin)"
}

# _proven_noted <step> — the identity lines a record keeps: the step's goh closure line (as `goh`,
# so a miss names the goh files that moved, GOH.PROVENCLOSURE), then proven_identity's.
_proven_noted() {
    ! _proven_closure_on || printf 'goh %s\n' "$(_proven_closure_line "$1")"
    proven_identity
}

# proven_note_identity <key> <step> — beside the record: the identity lines it was keyed under, and
# the goh manifest for them unless one is kept already; then sidecars of pruned records go.
proven_note_identity() (
    local key="$1" dir rdir ident m f name keep
    goh_bind_hook_index
    rdir="$(proven_dir)" || return 1
    dir="$rdir/.id"
    mkdir -p "$dir" || return 1
    ident="$(_proven_noted "${2:-}")"
    if ! { printf '%s\n' "$ident" >"$dir/.tmp.$BASHPID" && mv -f "$dir/.tmp.$BASHPID" "$dir/$key"; }; then
        rm -f "$dir/.tmp.$BASHPID"
        return 1
    fi
    m="$(_proven_goh_mkey "$ident")"
    if [ ! -s "$dir/$m" ] && ! { _proven_manifest >"$dir/.tmp.$BASHPID" && mv -f "$dir/.tmp.$BASHPID" "$dir/$m"; }; then
        rm -f "$dir/.tmp.$BASHPID"
    fi
    keep=" "
    for f in "$dir"/*; do
        [ -f "$f" ] || continue
        name="${f##*/}"
        case "$name" in m-*) continue ;; esac
        if [ -f "$rdir/$name" ]; then
            keep="$keep$(_proven_goh_mkey "$(cat "$f")") "
        else
            rm -f "$f"
        fi
    done
    for f in "$dir"/m-*; do
        [ -f "$f" ] || continue
        case "$keep" in *" ${f##*/} "*) ;; *) rm -f "$f" ;; esac
    done
    return 0
)

# proven_miss_reason <key> <tree> <step> — on a miss, the newest live record for this tree and step
# that kept its identity: one phrase naming what moved since it ("goh changed since in a, b" or
# "its key moved in: tool rustc, env CI"), after "proven on this tree <age> ago by <label>".
# Prints nothing when there is no such record. Never fails the caller.
proven_miss_reason() (
    local key="$1" tree="$2" step="$3" rdir dir f best="" best_epoch=0 epoch rtree rstep by=""
    local line now old new kinds paths m n
    goh_bind_hook_index
    rdir="$(proven_dir)" || return 0
    dir="$rdir/.id"
    [ -d "$dir" ] || return 0
    for f in "$rdir"/*; do
        [ -f "$f" ] && [ "${f##*/}" != "$key" ] && [ -f "$dir/${f##*/}" ] || continue
        epoch="" rtree="" rstep=""
        while IFS= read -r line; do
            case "$line" in
                epoch=*) epoch="${line#epoch=}" ;;
                tree=*) rtree="${line#tree=}" ;;
                step=*) rstep="${line#step=}" ;;
            esac
        done <"$f"
        case "$epoch" in ""|*[!0-9]*) continue ;; esac
        [ "$rtree" = "$tree" ] && [ "$rstep" = "$step" ] && [ "$epoch" -gt "$best_epoch" ] || continue
        best="${f##*/}" best_epoch="$epoch"
        by="$(sed -n 's/^label=//p' "$f")"
    done
    [ -n "$best" ] || return 0
    now="$(proven_now)"
    [ $((now - best_epoch)) -lt "${PROVEN_TTL:-86400}" ] || return 0
    old="$(cat "$dir/$best")"
    new="$(_proven_noted "$step")"
    [ "$old" != "$new" ] || return 0
    kinds="$(diff <(printf '%s\n' "$old") <(printf '%s\n' "$new") \
        | awk '$1 == "<" || $1 == ">" {
                   k = $2; if (k == "tool" || k == "env" || k == "keep") { n = $3; sub(/=.*/, "", n); k = k " " n }
                   print k }' | env LC_ALL=C sort -u | paste -sd, - | sed 's/,/, /g')"
    printf 'proven on this tree %s ago by %s, ' "$(proven_age $((now - best_epoch)))" "${by:-unknown}"
    case ", $kinds, " in
        ", goh, "|", goh, pythonpath, "|", pythonpath, ")
            m="$dir/$(_proven_goh_mkey "$old")"
            if [ -s "$m" ]; then
                paths="$(diff <(cat "$m") <(_proven_manifest) | awk '$1 == "<" || $1 == ">" { print $4 }' \
                    | env LC_ALL=C sort -u)"
                n="$(grep -c . <<<"$paths" || true)"
                paths="$(head -n 6 <<<"$paths" | paste -sd, - | sed 's/,/, /g')"
                [ "$n" -le 6 ] || paths="$paths and $((n - 6)) more"
                printf 'under other gates — goh changed since in %s\n' "${paths:-no keyed file}"
            else
                printf 'under other gates — goh changed since\n'
            fi ;;
        *) printf 'but its key moved in: %s\n' "$kinds" ;;
    esac
)
