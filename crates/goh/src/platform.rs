//! Platform gate — 64-bit only; macOS is Apple silicon only.
//!
//! `gates_of_heck` must remain Linux compatible: Linux `x86_64` (and
//! `aarch64`) stay supported. Only macOS loses its `x86_64` build.
//! Unsupported combinations are a hard failure, never a warn-and-build
//! fallback. `OS`/`ARCH` are overridable via env so both directions are
//! testable on one machine.

use std::env;

/// Supported platforms: (`os`, `arch`) pairs that pass the gate.
const SUPPORTED: &[(&str, &str)] = &[
    ("Darwin", "arm64"),
    ("Linux", "x86_64"),
    ("Linux", "amd64"),
    ("Linux", "aarch64"),
];

/// Check `os`/`arch`.
///
/// # Errors
///
/// Returns `Err` with a human-readable reason for unsupported `OS`/`ARCH`
/// combinations (macOS Intel, 32-bit, or unknown OS).
pub fn check(os: &str, arch: &str) -> Result<(), String> {
    if (os == "Darwin" || os == "macOS") && (arch == "x86_64" || arch == "amd64") {
        return Err(format!(
            "macOS Intel ({arch}) is not supported — Apple silicon (arm64) only. Linux x86_64 remains supported."
        ));
    }
    match arch {
        "arm64" | "aarch64" | "x86_64" | "amd64" => {}
        _ => {
            return Err(format!(
                "Unsupported architecture: {arch}. 64-bit only (arm64/aarch64 or x86_64)."
            ));
        }
    }
    match os {
        "Darwin" | "macOS" | "Linux" => {}
        _ => {
            return Err(format!(
                "Unsupported OS: {os}. Supported: Darwin (Apple silicon), Linux."
            ));
        }
    }
    if SUPPORTED.contains(&(os, arch))
        || (os == "macOS" && arch == "arm64")
        || (os == "Linux" && arch == "arm64")
        || (os == "Darwin" && arch == "aarch64")
    {
        Ok(())
    } else {
        Err(format!("Unsupported platform: {os}/{arch}."))
    }
}

/// Read `OS`/`ARCH` from the overridable env seam, defaulting to the host.
#[must_use]
pub fn current() -> (String, String) {
    let os = env::var("GOH_BUILD_OS").unwrap_or_else(|_| {
        if cfg!(target_os = "macos") {
            "Darwin".to_owned()
        } else if cfg!(target_os = "linux") {
            "Linux".to_owned()
        } else {
            "Unknown".to_owned()
        }
    });
    let arch = env::var("GOH_BUILD_ARCH").unwrap_or_else(|_| {
        if cfg!(target_arch = "aarch64") {
            "arm64".to_owned()
        } else if cfg!(target_arch = "x86_64") {
            "x86_64".to_owned()
        } else {
            "Unknown".to_owned()
        }
    });
    (os, arch)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn supported_platforms_pass() {
        for (os, arch) in [
            ("Darwin", "arm64"),
            ("Linux", "x86_64"),
            ("Linux", "amd64"),
            ("Linux", "aarch64"),
        ] {
            assert!(check(os, arch).is_ok(), "{os}/{arch} should pass");
        }
    }

    #[test]
    fn macos_intel_is_rejected() {
        for arch in ["x86_64", "amd64"] {
            let err = check("Darwin", arch).expect_err("macOS Intel must fail");
            assert!(err.contains("Intel"), "unexpected: {err}");
            assert!(
                err.contains("Linux x86_64 remains supported"),
                "unexpected: {err}"
            );
        }
    }

    #[test]
    fn thirty_two_bit_is_rejected_everywhere() {
        for arch in ["i686", "i386", "armv7l", "armhf"] {
            for os in ["Linux", "Darwin"] {
                assert!(check(os, arch).is_err(), "{os}/{arch} should fail");
            }
        }
    }

    #[test]
    fn unknown_os_is_rejected() {
        for os in ["FreeBSD", "OpenBSD", "SunOS", "Windows_NT"] {
            assert!(check(os, "x86_64").is_err(), "{os} should fail");
        }
    }
}
