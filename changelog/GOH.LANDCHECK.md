* `goh land --check WORKTREE` runs the land's structural guards over the committed tip in seconds:
  the `.gatesrc` GOH_LAND_CHECK_STEPS (each verbatim one of GOH_CI_STEPS; goh's are the structural
  gate, status markers, shape diff, lint as the gate runs it, the lock order and the saved-pid
  probes) beside the guard tests (`# goh-reads-staged:` files, `# goh-land-guard:` functions, and
  the tests past land records name as their refusing guard, each measured at most 1 s). Every
  `goh land --enqueue --land` runs it before the pre-step: a refusal is recorded red at enqueue
  naming its guard, nothing queued. Each red land's record now carries `guard`/`guards` read from
  its log, and `goh land-profile --guards` counts red lands by guard (GOH.LANDCHECK: 59 of 257
  lands since 2026-11-01 went red, GOH.STRAYSIGNAL3 refused three times by guards found only
  after a 200-900 s pre-step).
