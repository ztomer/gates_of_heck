# Shared advisory rulings

Some advisories reach every consumer through ONE upstream that has no release without the advised
crate. Every repo hand-adding the same `.cargo/audit.toml` ignore gives nine copies of one ruling,
and nobody re-checks them together. goh keeps ONE copy, `gates/shared_advisories.toml`
(GOH.SHAREDADVISORY):

```toml
[[advisory]]
id = "RUSTSEC-2026-0543"
crate = "chrono"      # the advised crate
blocker = "rmcp"      # the upstream whose newest release still requires it
reason = "..."
reopen = "rmcp no longer requires chrono"
```

* **The gate.** Every goh `cargo audit` goes through `lib/advisory_db.py run -- cargo audit`, which
  adds `--ignore ID` for the lock it reads only where the blocker still reaches the advised crate
  through the lock's dependency graph (`lib/shared_advisories.py ignores LOCK` gives the same
  answer). That covers `gates/rust_gate.sh` and a consumer's own GOH_CI_STEPS audit alike
  (GOH.AUDITONCE); a raw `cargo audit` there is refused by `checks/check_raw_audit.py`. A lock that carries the crate some other way is not
  covered. In a lock that carries both crates where the blocker no longer reaches the advised one,
  the re-open condition is met, and the gate goes red before the audit runs
  (`every shared advisory ruling still holds in LOCK`).
* **The ratchet.** `python3 checks/check_audit_ignores.py --shared [--estate ROOT]` treats an entry
  as stale when it fires in no tracked lock of the estate (default `~/Projects` unioned with the
  estate registry). It also reports every lock where the re-open condition is met. A repo's own
  `.cargo/audit.toml` keeps its per-repo ratchet, which is unchanged.
* **The estate audit.** `scripts/estate_audit.py` drops the same findings, so it reports what a
  push would.

## A new direct use stays red: the ban recipe

The shared ignore covers the crate wherever the blocker brings it in. To keep a NEW direct
dependency on the advised crate red, a consumer that runs cargo-deny lists the crate under
`[bans]` and names the crates allowed to bring it in as wrappers:

```toml
# deny.toml
[bans]
deny = [{ crate = "chrono", wrappers = ["rmcp", "schemars"] }]
```

`cargo deny check bans` passes when only `rmcp` or `schemars` depend on chrono. It fails as soon
as any other crate does, including the consumer itself. `tests/test_shared_advisories.py` tests
both cases.

## Standing rulings

| Advisory | Crate | Blocker | Since | Re-open when |
|---|---|---|---|---|
| RUSTSEC-2026-0543 | chrono (unmaintained, every version, no patch) | rmcp 3.10.0 needs chrono non-optionally and enables schemars' `chrono04` | 2026-11-17 | rmcp no longer requires chrono. The gate says so in each lock where that holds, and the estate ratchet calls the entry stale once no estate lock fires it. |
