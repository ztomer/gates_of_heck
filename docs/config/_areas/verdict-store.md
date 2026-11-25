# The one verdict store (lib/reuse.py, GOH.ONEVERDICT)
Order: 150
Level: 3

Every reuse path records and looks up its verdicts through `lib/reuse.py`, under one root, each by
the key fields it registers (`reuse.py paths`). The map of every store onto it: `docs/reuse.md`.

| Key | Default | Meaning |
|---|---|---|
<!-- rows to 40 -->
| (the reuse audit) | no setting | `lib/reuse_audit.py` (GOH.REUSEAUDIT): each registered path's sample share (`every`, and `every_unbacked` for a repo tracking no GitHub workflow) and window minimum (`min_samples`) live in `lib/reuse.py`'s one table, and a path's on/off state in the store (`<root>/audit/<path>.json`), never in an environment variable. A disagreement files its fault through `GOH_LOOPCTL` (into `GOH_LOOP_QUEUE` when set). |
<!-- rows -->

`rust_gate.sh` proves its three groups separately (`gates/_rust_proven.sh`): the crate group and coverage on the crate's own inputs (`goh rust-scope`), the repo-wide scans on the whole tree. The same `GOH_PROVEN` / `GOH_PROVEN_TTL_S` apply; every `GOH_*` in the environment is part of each group's key.
