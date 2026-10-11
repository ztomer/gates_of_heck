//! Where a cache lives, from its knob: one rule for every cache the binary keeps.
//!
//! A knob that names a cache directory is read by people as a switch too. `GOH_ESTATE_CACHE=0`
//! meant "off" to its caller and "the directory `0`" to the reader, and a gate run in this repo
//! committed its verdicts under `0/`. So an off word is off, whatever the knob, and a relative
//! path is refused: it names whatever directory the gate happens to run in, a consumer's tree.

use std::ffi::OsStr;
use std::path::PathBuf;

/// The words a caller means "off" by.
pub const OFF_WORDS: [&str; 4] = ["0", "off", "false", "no"];

/// Whether `value` is an off word, ignoring case and surrounding space.
#[must_use]
pub fn is_off(value: &str) -> bool {
    OFF_WORDS.contains(&value.trim().to_ascii_lowercase().as_str())
}

/// The cache root `value` names, else `default` when it is unset or empty. `None` when it is an
/// off word or a relative path, or when the root it would use is relative.
#[must_use]
pub fn root_from(
    value: Option<&OsStr>,
    default: impl FnOnce() -> Option<PathBuf>,
) -> Option<PathBuf> {
    let chosen = match value.filter(|v| !v.is_empty()) {
        Some(v) if v.to_str().is_some_and(is_off) => return None,
        Some(v) => PathBuf::from(v),
        None => default()?,
    };
    chosen.is_absolute().then_some(chosen)
}

/// `root_from` over the environment variable `var`.
#[must_use]
pub fn root(var: &str, default: impl FnOnce() -> Option<PathBuf>) -> Option<PathBuf> {
    root_from(std::env::var_os(var).as_deref(), default)
}

/// `$HOME/.cache/<rest>`, the default every cache shares.
#[must_use]
pub fn home_cache(rest: &str) -> Option<PathBuf> {
    std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".cache").join(rest))
}

#[cfg(test)]
mod tests {
    use super::{is_off, root_from};
    use std::ffi::OsStr;
    use std::path::PathBuf;

    fn home() -> PathBuf {
        PathBuf::from("/home/u/.cache/goh")
    }

    #[test]
    fn every_off_word_is_off_in_any_case() {
        for word in ["0", "off", "OFF", " false ", "No"] {
            assert!(is_off(word), "{word}");
            assert_eq!(
                root_from(Some(OsStr::new(word)), || Some(home())),
                None,
                "{word}"
            );
        }
    }

    #[test]
    fn a_relative_root_is_refused_never_joined_to_the_cwd() {
        for rel in ["0/", "cache", "./c", "../c"] {
            assert_eq!(
                root_from(Some(OsStr::new(rel)), || Some(home())),
                None,
                "{rel}"
            );
        }
        assert_eq!(root_from(None, || Some(PathBuf::from("rel"))), None);
    }

    #[test]
    fn an_absolute_root_or_the_default_is_kept() {
        assert_eq!(
            root_from(Some(OsStr::new("/tmp/c")), || Some(home())),
            Some(PathBuf::from("/tmp/c"))
        );
        assert_eq!(root_from(None, || Some(home())), Some(home()));
        assert_eq!(
            root_from(Some(OsStr::new("")), || Some(home())),
            Some(home())
        );
    }
}
