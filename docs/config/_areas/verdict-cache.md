# Per-file verdict cache (BACKLOG P4)
Order: 160
Level: 2

A ported checker whose verdict is a pure function of one file and its tools records it per file
(`crates/goh/src/verdict_cache.rs`), keyed on EVERY input: today `goh shell-lint`, keyed on both
tools' identities (path, size, mtime), the flags, `SHELLCHECK_OPTS`, every `.shellcheckrc`
shellcheck would read, the display path and the file's sha256. An unreadable or malformed record
is a miss; a run whose shellcheck output cannot be attributed to one file records nothing.
Measured on a private server workspace: `structural --full` 1.9 s -> 0.30 s warm.

| Key | Default | Meaning |
|---|---|---|
<!-- rows -->
