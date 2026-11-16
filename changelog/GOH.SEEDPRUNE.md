* Declaring a seeded floor retires its seed entry instead of turning it red (GOH.SEEDPRUNE). The
  private store held 139 `unreviewed: declare GOH_X_MIN_FILES=N` entries (GOH.FLOORREQ), and a repo
  that declared the floor met a stale-entry red at `GOH_GRACE=over` that only a hand edit of
  `~/.local/state/goh/seeds/floor_seed.json` cleared. A full run now deletes the store entry of
  every key `.gatesrc` declares, under the store's lock, and says `retired`; any declaration
  counts, `KEY=0` too. An entry in the repo's own tracked `floor_seed.json` is still stale and
  red: the commit that declares the floor deletes it.
