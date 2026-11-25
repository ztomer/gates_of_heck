# Pushes and rounds (`gates/push_gate.sh`, `gates/round.sh`)
Order: 40
Level: 2

| Key | Default | Meaning |
|---|---|---|
<!-- rows to 600 -->
| `GOH_SHARE_` | -- | The prefix of the live share's variables below (GOH.SHAREGROW, `lib/worker_share.py`): `tests/suite_home.py` packs every `GOH_SHARE_*` into `GATES_OF_HECK_SUITE_SHARE` so the suite's xdist workers find them past its `GOH_*` scrub. Not a setting. |
<!-- rows -->

`tools/quiet.sh --max-load` defaults to **8**: this box's floor, measured with `quiet.sh --floor`
(every gate drained for 180 s, 2026-10-08), is load 4.5 min / 6.2 median -- Spotlight,
WindowServer, Xcode's `xctest` and the desktop apps, which no goh lock holds -- so the old 4 was
under the idle floor and never met. The controls, not the load, say whether a run was quiet.
