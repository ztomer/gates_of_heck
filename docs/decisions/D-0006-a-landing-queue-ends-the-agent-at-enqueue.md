# D-0006 — an agent ends at "committed and enqueued"; one lander runs the push gates

- **Question:** Agents run their own push gates, and the machine-wide gate slot and load queue
  each push behind other repos' gates; the agent sits idle until its push exits. Where should the
  wait be spent, who runs the gate, and how does a queued landing survive a reboot?
- **Evidence:** (measured 2026-11-14 from one coordinator session's 98 subagent transcripts, the
  wall time from a push's tool call to its completion notice) pushes of 1197 s and 1196 s
  (game_asset_factory, a private server workspace; 12 s of it a slot wait), 1298 s (a private consumer: its `make ci` hook
  alone 1276 s), 1523 s (koffee_big's gate); one push waited 1308 s for a gate slot before its
  gate began. The cost is the gate's own wall time under three other gates' load plus the slot
  queue, and every second of it was an agent's idle lifetime (owner 2026-11-11: KB.OILCUP 9 h,
  KB.CLICKTOGGLE 4 h, CG.2 2 h).
- **Options priced:**
  1. More gate slots, or faster gates. Shortens the wait; the agent's lifetime still depends on
     how busy the machine is, which is the defect. Rejected as the fix (gate speed is its own work).
  2. A launchd agent that drains the queue. Survives a reboot with no one asking, but adds an
     install step to every machine and a daemon to keep alive, for a queue that only fills when an
     agent or the coordinator is running -- and either can start the lander. Rejected.
  3. `goh land --enqueue` writes a request file and starts a detached lander (own session) when no
     lander holds `lander.lock`; the lander drains requests in name (enqueue) order, one at a time,
     each push through the repo's own pre-push gate (which takes the gate slot itself), and records
     the outcome on the item with `loopctl set ID landing ...`. A request is a file, so a reboot
     loses nothing: the next lander (the next enqueue, or the coordinator's tick running
     `goh land --drain`) moves what a dead lander left in `running/` back to the front. TAKEN.
- **Ruling:** (GOH.LANDQUEUE, 2026-11-14) option 3, `tools/landq.py`. A push lands exactly the
  enqueued sha (`SHA:refs/heads/BRANCH`); `--ship` runs `goh ship --wait` and refuses a checkout
  whose HEAD moved past the sha (a ship releases HEAD). A red gate pushes nothing (git refuses a
  push its pre-push hook fails) and records the log path. (GOH.LANDQLAND, 2026-11-16; owner
  2026-11-15: "move the lanes onto the landing queue") `--land WORKTREE` runs that worktree's
  `tools/land.sh` bare in the lander -- the one land lock still serialises lands -- refuses a tip
  that moved after enqueue unrun, and `goh land --wait ID` is the lane's one wait on the outcome.
- **Falsifier:** `tests/test_landq.py`: two requests' gates overlap in time, or run out of enqueue
  order; a red gate's sha reaches the remote or its outcome lacks the log; a request left in
  `running/` is never run. Each was seen red against a broken lander (concurrent runs, an
  outcome read without the exit code).
- **Re-open when:** requests sit in `pending/` with no lander running for longer than one tick
  (the on-demand start missed a case), or the estate wants landings while no agent and no
  coordinator runs (then option 2).
- **Lives in:** `tools/landq.py`, `crates/goh/src/cli.rs` (`goh land`), `tests/test_landq.py`,
  `docs/config.md` (GOH_LANDQ_DIR, GOH_LAND_TIMEOUT).
