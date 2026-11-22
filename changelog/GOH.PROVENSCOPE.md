* A gate step in any language can key on its declared inputs (GOH.PROVENSCOPE): `proven.sh --scope
  P1,P2` or the step string's `[scope=P1+P2]` prefix (the spelling GOH_CI_STEPS carries, accepted by
  `local_ci.sh`) keys the proven verdict on the git objects of those paths alone, so a commit that
  leaves them unchanged reuses it. Before, only Rust steps could (`gates/_rust_proven.sh`).
