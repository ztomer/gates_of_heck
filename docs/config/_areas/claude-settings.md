# Managed Claude Code settings (`hooks/claude/registrations.json` `settings`, GOH.READCAP, GOH.CACHETTL)
Order: 210
Level: 2

`scripts/apply_claude_hooks.py` writes these into `~/.claude/settings.json` beside the hooks (`env`
merged var by var; no other key or env var is touched); `--check` and the weekly
`model_choice_watch.py` read a hand change as drift. Each value is a measurement, re-taken with
`python3 scripts/claude_cache_probe.py {reads|ttl} --cut <ISO time the value landed> [--days N]`
over `~/.claude/projects/**/subagents/**/*.jsonl`. Measured 2026-11-08 (Claude Code 2.1.311), cut
17:06:26Z (when the coordinator hand-placed both), before = the 7 days (reads) / 1 day (ttl) prior.

| Setting | Value | Why (measured) |
|---|---|---|
| `env.CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS` | `12000` | Claude Code's own Read cap (tools-reference.md#large-files; default 25,000): a whole file over it comes back as its first page with a `PARTIAL view` notice naming the next `offset`/`limit`, and a ranged read over it is refused with an error naming `offset`/`limit` or Grep. Probed live in a session started after the change: a 134 KB file (63,907 tokens) came back as lines 1-319 with `PARTIAL view ... (63907 tokens, cap 12000)`; `offset 1 limit 1500` (47,997 tokens) was refused; `offset 1990 limit 5` passed. So no hook. Distribution of the 26 whole-file text Reads before (tokens = chars / 2.34, the ratio measured from 24 turns that held one large result): 15 under 3k, then a gap from 8.3k to 13.5k, then 10 from 13.5k to 62k (the ROADMAP/CHANGELOG class). 12000 sits in that gap: it cuts 14 of 38 text reads (whole and ranged) and 41.6% of their tokens, where the 25k default cut 3 and 14%. After: 0 whole-file reads over 12k. The cap does not bound images (86 of 124 Reads before were screenshots, counted apart) and `allow_large: true` (used 9 times before, 0 after) opts a read out. |
| `subagentPromptCacheTtl` | `5m` | The 1h TTL was tried (hand-placed 1h at the cut) and is not kept: after the cut, subagent writes were 6,050,577 at 1h and 1,956 at 5m (so the setting does reach subagents), and the share of cache writes made right after a >5 min gap fell from 49.1% (105 gap turns of 9,733) to 8.6% (20 of 2,159). But the cost, in API price units (read 0.1, 5m write 1.25, 1h write 2, output 5), ROSE: on the after turns, 1h cost 49.88M units against 48.21M for 5m on the same turns, +3.5% (the 2x write rate on every write outweighs 20 avoided misses at a 5.9k shared prefix now that agents are lean). On the before turns (heavier agents, 36k shared prefix) 1h would have been 1.8% cheaper. Keep it only while the probe reads 1h cheaper; re-run it after any change in agent size or idle waits. |
| `env.BASH_DEFAULT_TIMEOUT_MS` | `600000` | The Bash tool's default timeout (Claude Code default 120,000; GOH.TOOLTIMEOUT, measured 2026-11-21 on Claude Code 2.1.330). In the 7 days before, 78 Bash calls in the agent transcripts (`~/.claude/projects/*/*.jsonl` and `*/subagents/*.jsonl`) hit the 120 s default and were moved to the background: 64 `git commit` calls (whose hooks run the commit gate) and 14 `git push` calls (the push gate). Of 2,011 foreground commits the p95 is 121 s, censored at the timeout itself (119 ran past it; p99 238 s); of the timing ledger's 641 push-gate runs (`lib/timing_ledger.py p95 --kind push --repo R` per repo; wall plus slot wait) p50 85 s, p90 550 s, p95 665 s, p99 943 s. p95 with headroom is past the tool's 600,000 ms maximum, so the value is that maximum: the binary reads `BASH_MAX_TIMEOUT_MS` as max(it or 600000, the default) (strings on `claude.app/Contents/MacOS/claude`), so a default above it would raise the maximum too and every hung call would sit longer. `BASH_MAX_TIMEOUT_MS` stays unset. The tail past 10 min is not met by a wider timeout: `goh land --enqueue --land` and `goh ship` background themselves unless `--wait`, and the push gate prints its own p95 and how to run it unwatched before it runs (a pre-push hook cannot background itself: git waits on its exit). A SUBAGENT is held to less: the same binary moves an async subagent's Bash call to the background after 180,000 ms whatever its timeout (`RMi=180000`, the "after running for 180s" notice), unless its prompt cache TTL is `1h` or off, so with `subagentPromptCacheTtl` `5m` (above) this setting lifts subagents from 120 s to 180 s and the main session to 600 s. Of the week's notices (transcript lines, a notice can repeat): subagents 1,349 at the 180 s cap and ~390 at a 110-178 s timeout, the main session 32 at 120 s. The cap is a cost trade the 1h TTL row prices (+3.5%); a subagent's long command goes to `goh land --enqueue` / `goh ship`, which background themselves. Re-count the timeouts after 20 lands (the item's metric: under 10 a week). |

**What an agent costs on this plan, and what images cost (GOH.IMGREAD, measured 2026-11-23,
Claude Code 2.1.330).** The plan's real cost metric is not the API price: on a Pro/Max/Ultra plan
what binds is the plan-limit utilisation, the 5-hour and 7-day `utilization` percent USED that
`/usage` shows (costs.md "Plan usage breakdown"; read from `GET /api/oauth/usage`, the endpoint
`/usage` reads, by the token-pool meter into `~/.cache/token-pool/meter.json`, and taken weekly by
`model_choice_watch.py`'s usage review as `claude_meter_left_pct`). The 1h-TTL A/B above priced
both arms in API price units, so its "+3.5%" is an API-price answer; a TTL decision on this plan
reads the meter's 7-day delta over matched work instead. Images are costed in tokens from their
pixels (width * height / 750, Anthropic's vision docs), never their base64 bytes:
`scripts/spend_watch.py` reports `image_results` by tool and `image_tokens_per_agent`. Over the
7 days to 2026-11-23 (389 subagents): 578 image results in 9 agents, 21.6 MB of base64 = 27% of
tool-result bytes but 245.8k tokens = 1.6% of tool-result tokens (632 per agent); browser
screenshots (543, already 440x293 to 800x540 at 326 tokens each) and Read of a PNG (31, 2,094
each). Claude Code's own lever is already in force (Read and MCP images are resized and recompressed
to the model's limits before sending: tools-reference.md Read "Images", mcp.md "Images in tool
results"; `MAX_MCP_OUTPUT_TOKENS` bounds MCP images); `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS`
does not touch them. Nothing further is built: at 1.6% no image lever pays for itself.

**Where READCAP's 42% baseline came from (GOH.READBASE).** Not from an instrument: the
coordinator's hand count of 2026-11-08 over that session's 118 general-purpose agents took whole-file
Read results' bytes with screenshots in them as their base64 (122 calls, 112 KB average: a
screenshot's size; 86 of the 124 Reads of the probe's before-window were images). The instrument,
`claude_cache_probe.py reads` over the same window (the 7 days to the 17:06:26Z cut), measured text
Reads at 4.3% of text tool-result bytes, the definition `spend_watch.py`'s `read_share` keeps
(images excluded; a test plants a 25% share beside screenshots and reads back 0.25). Those
transcripts are gone (Claude Code's cleanup: the oldest left is 2026-11-14), so 4.3% is the
re-derived baseline; on the week to 2026-11-23 the same reader gives 1.9%, and a screenshots-as-bytes
reader 10.8%. A queue item's `baseline_measured` (`value`, `instrument`, `window`) outranks the
number in its `predicted` text, and the spend check names which it used (`baseline_from`).
