- The 23 red goh lands of 2026-11-15..17 (23 of 58 attempts on 11-17), classified from their landq logs; `goh land --report` now fills `cause` from the same reading (`lib/land_cause.py`), with every cause on a line under the row (GOH.PREPARITY):

  | cause | reds | lands |
  |---|---|---|
  | repo-wide ratchet test failed (self_walk, suite_drift, verdict_free_keys, source_closure, no_env_spread, bash5_guard, check_lock_order, git_deadline_class, gate_temp_leaks) | 9 | LANDERSTALECODE, SCREENDOCMASK, SWEEPSLOTS, CANARYFLAKE 3, GATEBUDGET 1, COVPARBINS 1, REUSEAUDIT 1 (with a docs/map.md row), ADAPTLANES 1 and 2 |
  | structural gate (the 500-line cap) | 1 | AFFECTEDBINARY |
  | rebase conflict (GOH.LANDUNION's) | 3 | LANDREPORT 1 and 2, BUILDINCLUDES 1 |
  | load-flaky test | 4 | VERDICTCARRY (test_rust_cov_reuse), CANARYFLAKE 1 (test_ps_table_unbounded), GATEBUDGET 2 (test_land_identity at load 124), PRESTEPDEADLINE (test_gate_slot_consumer, a 1785 s suite) |
  | a test the pre-step did not choose | 2 | BUILDINCLUDES 2 (test_rust_gate, 14 reds), GRACESHAPES (test_check_moving_tags, a binary runner before GOH.AFFECTEDBINARY) |
  | the pre-step deadline | 1 | REUSEAUDIT 2 (600 s, red before GOH.PRESTEPDEADLINE) |
  | the tip moved after the enqueue | 2 | CANARYFLAKE 2, COVPARBINS 2 |
  | a real defect elsewhere (release canary: divoom-control's Cargo.lock) | 1 | TAGVERSIONRC |

  ADAPTLANES' two ratchet reds came after GOH.ENQUEUEPRE: its enqueue pre-step reached the 600 s deadline at load 175 with no verdict, so the ratchets it had chosen never judged before the queue.
- The pre-step's test choice follows what a reached shell script RUNS by a variable-rooted path (`"$GOH_PY" -S "$PROVEN_GOH_ROOT/lib/gate_identity.py"`), a file pytest loads before every test included: a `lib/gate_identity.py` change now chooses `tests/test_rust_gate.py` (BUILDINCLUDES 73ba690b went red on 14 of its tests in the land gate after a green pre-step). One use per chain (a used script's sources come with it, its own runs do not; followed, `land.sh -> gate_profile.sh -> push_gate.sh -> install.sh -> ...` added ~100 files a land), and none for the binary's runners, whose subcommand reading it would blur: on main's last 12 commits the set grew by 0-64 files (mean ~19). The graph moved to `lib/test_closure.py`, `tools/affected_tests.py` being at its 500-line cap.
