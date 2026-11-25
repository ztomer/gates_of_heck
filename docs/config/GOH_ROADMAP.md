# GOH_ROADMAP
Area: structural
Kind: setting
Order: 330
Default: REQUIRED: unset is red, naming the plan files it sees (GOH.ROADMAPREQ, 2026-11-12; the staged turn-on list went once every repo on it declared). No seam excuses a repo or a fixture (GOH.ROADMAPHOOK): a fixture writes its `.gatesrc` through `tests/_gatesrc_kit.py` or `goh_testkit::gatesrc`, which declare `none`

The ONE roadmap file, repo-relative (`ROADMAP.md`, `docs/BACKLOG.md`, `koffee_big`'s
`FEATURE_PLAN.md`: the choice is written, not guessed), or `none` for a repo that keeps no plan.
Read by `goh roadmap`, a structural step in every repo (GOH.ROADMAPGATE; owner 2026-11-08: *"rework,
rephase, harden the roadmaps"*). Four rules: (1) exactly one plan file -- any other tracked
`ROADMAP`/`BACKLOG`/`TODO`/`PLAN(S)` `.md` (whole name any case, or an UPPER-CASE name with that
word between `_`/`-`, or any `.md` under `plans/`/`roadmap(s)/`; `GOH_EXCLUDE` honoured) is a
finding, and a declared file that is not tracked is red outright, never seedable; (2) no ENTRY
marked completed: a heading, list item or table row that carries an id with `DONE`, `shipped`,
`closed`, `✓`, `~~` or a ticked `[x]` on it, or one whose status position OPENS with such a mark
(`\| ... \| DONE (abc123) \|`) -- prose that says "what shipped is in CHANGELOG.md" is not an entry;
the one escape is `<!-- roadmap-ok: <reason> -->` on the line; (3) every open entry under a `Phase`
heading (a list item, or a deeper heading opening with an id) opens with an id and carries `Done
when:`, and a `Done when:` that opens a line outside every entry is a finding (the anchor that keeps
the entry grammar from going blind); more than five phases is a finding; (4) `(queue: <ID>)` whose
umbrella item is `done` is a finding. Today's findings are DEBT in `roadmap_seed.json` (`goh roadmap
--seed`), a count per `<file>#<rule>` that may only fall: over its count red, under it red until
lowered, a stale key red, a corrupt seed exit 2, and the seed may not RISE against HEAD's (nor may
`--seed` raise it). At `--staged` the step judges only a commit that brings the roadmap, the seed,
`.gatesrc` or a plan file (the queue moves under a repo, and an unrelated commit is not where to
bill it); the push judges every rule.
