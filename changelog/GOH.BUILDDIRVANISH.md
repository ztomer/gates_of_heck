* The cache evictor no longer moves or deletes a cargo build dir a running build holds. cargo
  1.100 locks `<build-dir>/<profile>/.cargo-build-lock`, and the sweep only looked for
  `.cargo-lock`, so it found no lock and demoted a release's build dir mid-build (rustc:
  `extern location ... does not exist`). Both lock names are honoured now, and a removed lane
  worktree's build-dir reap renames each dir away under the same locks, keeping a held one.
