# GOH_SHELLCHECK_SLOTS
Area: pushes
Kind: setting
Order: 480
Default: one per 1 GiB of memory at the default severity (dataflow off), one per 8 GiB with dataflow on (`style`/`info`), four times the largest child measured in each mode, between 1 and the core count (**16** on a 64 GiB 16-core Mac, **3** on the 7 GiB 3-core hosted runner, GOH.CIRED01392)

How many shellcheck children run at once, MACHINE-wide, whichever `goh shell-lint` started them
(`crates/goh/src/shellcheck_slots.rs`): each child holds one token, a flock on `token-<i>` in
`GOH_SHELLCHECK_SLOT_DIR`, and a lint run starts no more threads than tokens. One thread per core
per run, about eight runs at once, was 128 shellchecks and 63.7 GiB on 2026-11-16 (GOH.MEMADMIT). A
pool of its own, not the gate slots: a token counts a child, a slot a gate, and a lint inside a gate
takes no slot (`docs/decisions/D-0010`). `0` turns the bound off; non-numeric is a refusal naming
the key.
