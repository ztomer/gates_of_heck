# GOH_NO_EARLY_EXIT_PIPE
Area: structural
Kind: setting
Order: 260
Default: unset (the seed holds the tree)

`goh early-exit-pipe` runs in EVERY repo as a structural step: an early-exit consumer reading a pipe
in any shell source -- `grep -q`/`--quiet`/`-l`, `grep -m`/`--max-count`, `head`, an awk program
that calls `exit` outside `END`, a sed script with `q`/`Q`. The consumer closes the pipe, the
producer's next write dies of SIGPIPE (141), and pipefail makes that the pipeline's status: a match
reads as a miss, depending on load (a private server workspace's deploy.sh read a RUNNING service as
absent, 2026-10-08). Unset, the step refuses a NEW finding at `--staged` (one the file's HEAD blob
does not carry, matched on the consumer's text, so a moved line is not new and an edited one is),
and at full scope judges each file against `early_exit_pipe_seed.json`, a per-file count that may
only fall: an unseeded finding fails, a file over its count fails, and a file under it fails until
the seed is lowered (BACKLOG 6.9; it used to name existing ones and pass). `goh early-exit-pipe
--seed` writes the seed a repo adopts with; a pipe that is genuinely safe (its producer cannot
outlive the reader) is said there. Set to `1` to ignore the seed: every finding fails, at both
scopes. Fix shape, printed per finding: `out="$(producer)"; grep -q PAT <<< "$out"` (or `head -n1
<<< "$out"`, or `[ -n "$out" ]`); only a status something READS is a finding, so a pipeline ending
`\|\| true`/`\|\| :` and a substitution used as an argument (not assigned) pass. Scope: every shell
source -- `*.sh`, `*.sh.tmpl` (the template an installer is regenerated from), `*.bash`, `*.zsh`,
and sh/bash/zsh/ksh/dash shebangs -- and NOT gated on the file's own pipefail line, because a
sourced library runs under its caller's (a private server workspace's install-lib.sh and
install.sh.tmpl both slipped a scan that read only `*.sh` files saying "pipefail"). Same
`GOH_EXCLUDE`.
