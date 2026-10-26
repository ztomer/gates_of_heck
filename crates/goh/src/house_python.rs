//! The house interpreter, as `lib/house_python.sh` finds it (G2d, 2026-10-25).
//!
//! A bare `python3` is the FIRST one on PATH: under a reduced PATH (a GUI client's hook, a launchd
//! job, a test handing its gate `PATH=/usr/bin:/bin`) that is macOS's 3.9.6. This binary asks the
//! one resolver instead -- never a second copy of its rules -- and trusts the verdict a gate that
//! started it already exported (`GOH_PY` equal to `GOH_PY_OK`).

use std::process::{Command, Stdio};
use std::sync::OnceLock;

static PY: OnceLock<Result<String, String>> = OnceLock::new();

/// The house interpreter's path, or the resolver's refusal (it names the floor and the fix).
///
/// # Errors
/// The resolver's own message when no python3 at or above the floor is installed.
pub fn path() -> Result<String, String> {
    PY.get_or_init(resolve).clone()
}

fn resolve() -> Result<String, String> {
    if let (Ok(py), Ok(ok)) = (std::env::var("GOH_PY"), std::env::var("GOH_PY_OK")) {
        if !py.is_empty() && py == ok {
            return Ok(py);
        }
    }
    let script = crate::goh_root().join("lib").join("house_python.sh");
    let out = Command::new("bash")
        .arg(&script)
        .stdin(Stdio::null())
        .output()
        .map_err(|e| format!("cannot run {}: {e}", script.display()))?;
    let found = String::from_utf8_lossy(&out.stdout).trim().to_owned();
    if out.status.success() && !found.is_empty() {
        Ok(found)
    } else {
        Err(String::from_utf8_lossy(&out.stderr).into_owned())
    }
}
