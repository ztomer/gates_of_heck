* The Rust coverage measurement is reused on goh's own lands at last, and a miss names what moved
  (GOH.RUSTCOVKEYSCOPE). After GOH.RUSTCOVREUSE three lands in a row measured in full (468-476 s
  each, the last a `lib/` and tests-only diff) and said "no measurement of this workspace is
  recorded yet" beside three records. Two defects: the miss read the store with a `grep | xargs ls`
  pipeline that met the store's directories, grep exited 2, and rust_gate's pipefail read that as
  no record; and the key named PYTHONPATH by path, where every land puts the tip's head export
  (`~/.cache/goh/head/<sha>`, new per commit). PYTHONPATH is now keyed as the measurement reads it,
  each gates root on it by its coverage scripts' content. The key gains what the number depends on
  and lacked: the runner the external command drives the binary through (goh's `tools/pytest.sh`
  and what it sources, imports or runs). Still not inputs, by the owner's rule: `tests/` and the
  scripts the suite exercises. Each measurement keeps its input lines beside its record, so the
  next miss says which moved (`the Rust build inputs (crates/goh/src/x.rs)`, `GOH_COV_FLOOR_RUST`,
  `the suite's runner (tools/pytest.sh)`, `PYTHONPATH`).
