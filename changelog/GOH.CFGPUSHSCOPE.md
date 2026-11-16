* A push whose diff since the remote's tip is scanner floors only (`.gatesrc` lines
  `GOH_<SCANNER>_MIN_<UNIT>=<digits>`, blanks and comments) runs `gates/structural.sh --full`, the one
  reader of a floor, instead of the repo's whole gate, when that tip has a green push verdict
  (GOH.CFGPUSHSCOPE). Every green push now records its commit in `goh-pushed` in the common git dir
  (`<sha> full|release|config`), so the first floor push after this release is still a full gate and
  the next ones chain. Any other file, any other key (one the gate reads: `GOH_CI_STEPS`,
  `GOH_MAX_LINES`), a value that is not a plain number, a tracked file that names the changed key, or
  a tip with no recorded verdict is the full gate, and the push says why. The floor list is
  `crate::subject`'s own, read from `crates/goh/src/subject.rs`.
