* A seed settle made on a lane branch no longer edits the machine-wide private seed store: the
  commit licenses it under `<store>/deferred/`, the lane's own full runs read the licensed entry
  at what they find, and the first full run on the store's branch (`origin/HEAD`, else `main`)
  that finds the change applies it. Main's checkout stays green while a lane that fixed its
  seeded sites is unmerged; an unlicensed stale entry is still red there. `goh seed-settle` takes
  `--checkout`, which the commit hook passes.
