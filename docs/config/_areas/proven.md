# Proven steps (`gates/proven.sh` + `gates/_proven.sh`, used by `gates/local_ci.sh`)
Order: 140
Level: 2

A step that exited 0 on a clean tree (working tree == index) is recorded under
`<git-common-dir>/goh-proven/`; the same step string on the same tree, with the
same gates checkout, toolchains and keyed environment, is skipped within the
TTL. Rationale and the full key: the header of `gates/proven.sh`.

| Key | Default | Meaning |
|---|---|---|
<!-- rows -->
