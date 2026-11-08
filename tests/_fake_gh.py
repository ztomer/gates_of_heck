"""The stateful gh stub the release kit's tests put on PATH (split out of conftest.py, the line cap)."""

FAKE_GH = """\
#!/usr/bin/env bash
STATE="${GH_STATE:?}"; LOG="${GH_LOG:?}"
printf 'gh %s\\n' "$*" >> "$LOG"
cmd="$1"; shift
case "$cmd" in
  auth) exit 0 ;;
  api)
    # `api repos/OWNER/NAME/actions/permissions --jq .enabled`: what a test planted in
    # $STATE/actions_enabled ("true"/"false"); nothing planted, nothing printed (an unreadable API).
    case "$1" in
      */actions/permissions) [ -f "$STATE/actions_enabled" ] && cat "$STATE/actions_enabled" ;;
    esac
    exit 0 ;;
  run)
    # `run list`: the runs a test planted in $STATE/runs.tsv, already in the release kit's
    # `--jq` shape (status, conclusion, name, url, tab-separated); none planted, none listed.
    [ "$1" = list ] || exit 0
    # GitHub runs CI only on a commit it HAS: with $STATE/origin_git planted (a bare repo's path),
    # a `--commit SHA` that origin lacks lists nothing, as the real service does.
    if [ -f "$STATE/origin_git" ]; then
      sha=""; prev=""
      for a in "$@"; do [ "$prev" = --commit ] && sha="$a"; prev="$a"; done
      /usr/bin/git --git-dir="$(cat "$STATE/origin_git")" cat-file -e "${sha}^{commit}" 2>/dev/null \
        || exit 0
    fi
    # A SEQUENCE of answers (pending, then green): $STATE/runs.d/<n> in sort order, each look
    # consuming the first; the last one stays, so the final state holds however often it is read.
    if [ -d "$STATE/runs.d" ]; then
      set -- "$STATE"/runs.d/*
      [ -e "$1" ] || exit 0
      cat "$1"
      [ $# -gt 1 ] && rm -f "$1"
      exit 0
    fi
    [ -f "$STATE/runs.tsv" ] && cat "$STATE/runs.tsv"
    exit 0 ;;
  release)
    sub="$1"; shift
    case "$sub" in
      view)
        tag=""
        while [ $# -gt 0 ]; do
          case "$1" in
            --repo) shift 2 ;;
            --*) shift ;;
            *) [ -z "$tag" ] && tag="$1"; shift ;;
          esac
        done
        [ -n "$tag" ] && [ -f "$STATE/rel-$tag" ] ;;
      list)
        # One tag name per release, as `--json tagName --jq '.[].tagName'` prints.
        for f in "$STATE"/rel-*; do [ -e "$f" ] && printf '%s\\n' "${f##*/rel-}"; done
        exit 0 ;;
      create)
        tag=""; notes=""
        while [ $# -gt 0 ]; do
          case "$1" in
            --notes-file) notes="$2"; shift 2 ;;
            --title|--repo) shift 2 ;;
            *) [ -z "$tag" ] && tag="$1"; shift ;;
          esac
        done
        : > "$STATE/rel-$tag"
        [ -n "$notes" ] && cp "$notes" "$STATE/notes-$tag"
        exit 0 ;;
      *) exit 0 ;;
    esac ;;
  *) exit 0 ;;
esac
"""
