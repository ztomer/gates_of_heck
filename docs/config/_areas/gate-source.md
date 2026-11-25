# Which gate source runs (C4)
Order: 230
Level: 2

Consumers never run the shared checkout's working tree: every entry point re-runs itself from an
immutable export of its HEAD (`gates/_from_head.sh`).

| Key | Default | Meaning |
|---|---|---|
<!-- rows -->
