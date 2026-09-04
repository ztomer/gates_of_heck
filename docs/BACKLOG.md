# BACKLOG — forward-looking work (rule #13)

One file; prune landed items to git history. Seeded from EVAL-2026-09-04.md
(delete that file once this list is accepted as the transfer).

## Open

- Secrets v2: entropy heuristics for unknown key shapes. Blocked on FP
  tuning first — v1 (prefixes + key headers) ships instead. Measure FP
  rate on all consumer trees before enforcing.
- shfmt enforcement: dropped from the shell-lint gate (tree uses aligned
  continuations shfmt won't reproduce). Revisit only as reformat-everything
  (noisy) or an explicit shfmt flag set proven clean on this tree.
- Swift coverage: parity-pinned, not merged. A merge needs a real-swift
  oracle proving identical verdicts across Xcode versions; without that
  toolchain, merging violates prove-before-claim. Revisit when it exists.
- Periodic disk watch: `scripts/bin/disk_hygiene.sh --warn-only` wants a
  launchd schedule (the prune-logs plist is the pattern). Manual runs only
  until then.
- `secret-ok` scope: line + line-above only. If fixture pressure appears,
  consider (not promise) path-scoped allow rules — never bare markers.

## Done (prune to history)

- v0.8.0 arc: disk watch out of CI, pooled du, parallel suite, shell lint,
  rust coverage floor, secrets v1, swift parity, doctor, timeouts, timing,
  mcp split, xdist grouping, stub dedup, doc refresh.
