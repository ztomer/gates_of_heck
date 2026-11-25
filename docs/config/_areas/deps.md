# Dependency currency (`goh deps`, run as `goh.sh deps`)
Order: 90
Level: 3

A step in `rust_gate.sh`. Two severities on purpose, because conflating them is
how a currency check becomes a gate nobody reads.

| Key | Default | Meaning |
|---|---|---|
<!-- rows -->

FATAL with no key set at all, and needing no network: a direct dependency
pinned BELOW a version the graph already resolves. Our pin is then why two
majors of one crate are in the tree — and that makes the same type not the same
type, which the compiler reports by NAME and never by pointing at the pin.
