# shellcheck shell=bash
# Sourced from zsh: refuse in words and return, never exit the caller (GOH.ZSHSOURCE).
[ -n "${BASH_VERSION-}" ] || { printf '%s\n' "gates/_proven_audit.sh is bash: source it from bash, not ${ZSH_NAME:-this shell}" >&2; return 1; }
_goh_d=${BASH_SOURCE[0]%/*}; [ "$_goh_d" != "${BASH_SOURCE[0]}" ] || _goh_d=.; . "$_goh_d/../lib/bash5.sh" || exit; goh_bash5 "$@"
# _proven_audit.sh — the reuse audit of the proven-step cache (GOH.REUSEAUDIT2), sourced by
# gates/_proven.sh (its callers provide proven_dir, proven_lookup, GOH_PY, PROVEN_GOH_ROOT).
#
# A proven hit is a claim that the step would pass again on these inputs, and the closure keying
# (GOH.PROVENCLOSURE) keys a step on less than the whole goh content: a closure that misses a file
# the step reads makes the skip a lie. So every hit a step runner is about to skip asks the audit
# first (lib/reuse_audit.py, the `step` path of lib/reuse.py): "reuse" skips; "shadow" runs the step
# anyway and `proven_audit_judge` compares its verdict with the reused green; "off" (a repeated
# disagreement turned the path off) runs it. A red shadow is run once more and the record looked up
# again before the audit hears it: only a disagreement both runs repeat turns the path off. A repo
# no hosted CI gates has nothing behind its skips, so its hits are counted apart and sampled at the
# path's `every_unbacked`. An audit that cannot answer is no licence to skip: the step runs.

# proven_backed — 0 when hosted CI gates this repo too: it tracks a GitHub workflow. Read once.
proven_backed() {
    if [ -z "${PROVEN_BACKED:-}" ]; then
        PROVEN_BACKED=0
        [ -z "$(git ls-files -- '.github/workflows/*.yml' '.github/workflows/*.yaml' 2>/dev/null)" ] \
            || PROVEN_BACKED=1
    fi
    [ "$PROVEN_BACKED" = 1 ]
}

# _proven_audit_fields <key> <tree> <step> — the `step` path's key fields as K=V words, one per
# line. The proven key hashes the tree, the step, its closure line, the identity and the keyed env
# together, so it stands for the identity; closure and env name the switch and the repo's names.
_proven_audit_fields() {
    printf '%s\n' "repo=$(proven_dir)" "tree=$2" "step=$3" "closure=${GOH_PROVEN_CLOSURE:-off}" \
        "identity=$1" "env=${GOH_PROVEN_ENV:-}"
}

# proven_audit_use <key> <tree> <step> — the audit's decision on a hit about to be skipped:
# reuse, shadow, off, or unasked (no answer: the step runs).
proven_audit_use() {
    local out fields=() args=()
    mapfile -t fields < <(_proven_audit_fields "$@" </dev/null)
    proven_backed </dev/null || args+=(--unbacked)
    out="$("$GOH_PY" -S "$PROVEN_GOH_ROOT/lib/reuse_audit.py" use step ${args[@]+"${args[@]}"} \
        "${fields[@]}" </dev/null 2>/dev/null)" || out=""
    case "$out" in reuse|shadow|off) printf '%s\n' "$out" ;; *) printf 'unasked\n' ;; esac
}

# proven_audit_why <decision> — why a hit is run anyway, as the step's note says it.
proven_audit_why() {
    case "$1" in
        shadow) printf 'the reuse audit sampled this hit (lib/reuse_audit.py)\n' ;;
        off) printf "the reuse audit turned the step path off (reuse_audit.py report)\n" ;;
        *) printf 'the reuse audit did not answer\n' ;;
    esac
}

# proven_audit_judge <key> <tree> <step> <rc> <again...> — a shadowed hit's fresh run (exit rc)
# against the green it would have reused. A red one runs `again...` and looks the record up again
# first, so the audit can tell a repeated disagreement from a flake. Returns 0 when they agreed.
# PROVEN_AUDIT_LOGS="FIRST AGAIN" names the two runs' logs, which a pass on retry's flake record
# keeps (lib/flakes.py, GOH.FLAKEREGISTER).
proven_audit_judge() {
    local key="$1" tree="$2" step="$3" rc="$4" fresh=green fields=() again=() r2=green f2=green logs=()
    shift 4
    [ "$rc" -eq 0 ] || fresh=red
    if [ "$fresh" = red ]; then
        proven_lookup "$key" "$step" >/dev/null </dev/null || r2=absent
        "$@" || f2=red
        again=(--again "$r2" "$f2")
        read -r -a logs <<<"${PROVEN_AUDIT_LOGS:-}"
        [ "${#logs[@]}" -ne 2 ] || again+=(--logs "${logs[0]}" "${logs[1]}")
    fi
    mapfile -t fields < <(_proven_audit_fields "$key" "$tree" "$step" </dev/null)
    "$GOH_PY" -S "$PROVEN_GOH_ROOT/lib/reuse_audit.py" compare step --reused green \
        --fresh "$fresh" ${again[@]+"${again[@]}"} "${fields[@]}" </dev/null >/dev/null 2>&1
}
