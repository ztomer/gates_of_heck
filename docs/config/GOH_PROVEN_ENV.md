# GOH_PROVEN_ENV
Area: proven
Kind: setting
Order: 30
Default: unset

Space-separated environment variable NAMES (in `.gatesrc`) whose values join the key, beside the
built-in `CI RUSTFLAGS RUSTDOCFLAGS CARGO_BUILD_TARGET`. PYTHONPATH is keyed apart, by content: each
gates root on it by the gates' files, any other entry by its path (`lib/gate_identity.py`
`pythonpath`); named here it is keyed by its path as well. Name a variable here when a step's
verdict depends on it.
