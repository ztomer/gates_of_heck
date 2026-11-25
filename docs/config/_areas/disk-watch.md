# Disk watch (standalone: `~/Projects/scripts/bin/disk_hygiene.sh`)
Order: 50
Level: 2

NOT a gate since v0.8.0 — a du stat-storm over host trees does not belong
in a commit gate. These keys configure the standalone watch (env or the
checker's flags, forwarded verbatim by the wrapper). When it fails, the
fix is `reclaim_build_space.sh` next to it.

| Key | Default | Meaning |
|---|---|---|
<!-- rows -->
