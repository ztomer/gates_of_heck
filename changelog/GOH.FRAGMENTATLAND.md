* `goh land --check` refuses a range whose commit subjects name an item id (`GOH.X: ...`) with no
  `changelog/<ID>.md` fragment at the tip, in a repository that keeps changelog/ fragments; a
  `Changelog: none` trailer exempts work no user sees. The omission is caught at the land, where
  the lane can add it, instead of at the release.
