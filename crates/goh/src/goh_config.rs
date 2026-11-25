//! The roots goh's state, the checkouts and the loop's queue live at, and where they come from.
//!
//! GOH.REPOKEY (the data plan, section 6): `$XDG_CONFIG_HOME/goh/config.toml` (`~/.config`) with
//! the keys `state_root`, `repos_root` and `loop_queue`, each absolute or `~/..`. The environment
//! wins: `GOH_STATE_ROOT`, `GOH_REPOS_ROOT`, `GOH_LOOP_QUEUE`. `lib/state_paths.py` is the Python
//! face; `tests/test_state_paths.py` asks it the cases this module's tests ask. A file or a value that says
//! nothing usable is an error, never a silent default.

use std::path::{Path, PathBuf};

/// Where a variable is read from: the process's environment, or a test's.
pub type Env<'a> = &'a dyn Fn(&str) -> Option<String>;

/// The process's own environment (a value that is not UTF-8 names no root).
#[must_use]
pub fn process(key: &str) -> Option<String> {
    std::env::var(key).ok()
}

fn set(env: Env, key: &str) -> Option<String> {
    env(key).filter(|v| !v.is_empty())
}

fn home(env: Env) -> Result<PathBuf, String> {
    set(env, "HOME")
        .map(PathBuf::from)
        .ok_or_else(|| "HOME is not set".to_owned())
}

fn absolute(env: Env, key: &str) -> Option<PathBuf> {
    set(env, key).map(PathBuf::from).filter(|p| p.is_absolute())
}

/// `raw` as a path; `Err` when it is neither absolute nor `~/..`.
fn rooted(env: Env, said: &str, raw: &str) -> Result<PathBuf, String> {
    if let Some(rest) = raw.strip_prefix("~/") {
        return Ok(home(env)?.join(rest));
    }
    if raw.starts_with('/') {
        return Ok(PathBuf::from(raw));
    }
    Err(format!("{said} is {raw:?}, not an absolute path"))
}

/// `key` in config.toml, `None` without the file, the key or a home to look under.
fn configured(env: Env, key: &str) -> Result<Option<PathBuf>, String> {
    let Some(base) =
        absolute(env, "XDG_CONFIG_HOME").or_else(|| home(env).ok().map(|h| h.join(".config")))
    else {
        return Ok(None);
    };
    let at = base.join("goh").join("config.toml");
    let text = match std::fs::read_to_string(&at) {
        Ok(text) => text,
        Err(e)
            if matches!(
                e.kind(),
                std::io::ErrorKind::NotFound | std::io::ErrorKind::NotADirectory
            ) =>
        {
            return Ok(None)
        }
        Err(e) => return Err(format!("{}: {e}", at.display())),
    };
    let table: toml::Table = text.parse().map_err(|e| format!("{}: {e}", at.display()))?;
    match table.get(key) {
        None => Ok(None),
        Some(toml::Value::String(raw)) => {
            rooted(env, &format!("{}: {key}", at.display()), raw).map(Some)
        }
        Some(other) => Err(format!(
            "{}: {key} is {other}, not an absolute path",
            at.display()
        )),
    }
}

fn named(env: Env, var: &str, key: &str) -> Result<Option<PathBuf>, String> {
    set(env, var).map_or_else(
        || configured(env, key),
        |raw| rooted(env, var, &raw).map(Some),
    )
}

/// goh's state directory: `GOH_STATE_ROOT`, the config, `$XDG_STATE_HOME/goh`, `~/.local/state/goh`.
///
/// # Errors
///
/// The config or the variable is unusable, or nothing names a home.
pub fn state_root(env: Env) -> Result<PathBuf, String> {
    if let Some(at) = named(env, "GOH_STATE_ROOT", "state_root")? {
        return Ok(at);
    }
    match absolute(env, "XDG_STATE_HOME") {
        Some(xdg) => Ok(xdg.join("goh")),
        None => Ok(home(env)?.join(".local/state/goh")),
    }
}

/// Where the checkouts are: `GOH_REPOS_ROOT`, the config, `~/Projects`.
///
/// # Errors
///
/// As `state_root`.
pub fn repos_root(env: Env) -> Result<PathBuf, String> {
    match named(env, "GOH_REPOS_ROOT", "repos_root")? {
        Some(at) => Ok(at),
        None => Ok(home(env)?.join("Projects")),
    }
}

/// The loop's queue: `GOH_LOOP_QUEUE` as given, the config, `<repos root>/.loop/queue.json`.
///
/// # Errors
///
/// As `state_root`.
pub fn loop_queue(env: Env) -> Result<PathBuf, String> {
    if let Some(raw) = set(env, "GOH_LOOP_QUEUE") {
        return Ok(PathBuf::from(raw));
    }
    match configured(env, "loop_queue")? {
        Some(at) => Ok(at),
        None => Ok(repos_root(env)?.join(Path::new(".loop").join("queue.json"))),
    }
}

/// `root`, or `None`; an unusable config is said on stderr, and the caller then has no root, never
/// the default one: that would be another state than the rest of goh reads.
#[must_use]
pub fn or_said(root: Result<PathBuf, String>) -> Option<PathBuf> {
    match root {
        Ok(at) => Some(at),
        Err(why) => {
            eprintln!("{} goh config: {why}", goh_tui::WARN);
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{loop_queue, or_said, repos_root, state_root};
    use std::path::{Path, PathBuf};

    type Root = fn(super::Env) -> Result<PathBuf, String>;

    fn at(root: Root, vars: &[(&str, &str)]) -> Result<PathBuf, String> {
        root(&|k| {
            vars.iter()
                .find(|(n, _)| *n == k)
                .map(|(_, v)| (*v).to_owned())
        })
    }

    fn conf(text: &str) -> (tempfile::TempDir, String) {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir(dir.path().join("goh")).unwrap();
        std::fs::write(dir.path().join("goh/config.toml"), text).unwrap();
        let path = dir.path().to_string_lossy().into_owned();
        (dir, path)
    }

    #[test]
    fn the_defaults_are_the_homes_and_xdg_wins_when_absolute() {
        let home = ("HOME", "/h");
        let is = |root: Root, vars: &[(&str, &str)], want: &str| {
            assert_eq!(at(root, vars).as_deref(), Ok(Path::new(want)), "{vars:?}");
        };
        is(state_root, &[home], "/h/.local/state/goh");
        is(state_root, &[home, ("XDG_STATE_HOME", "/x")], "/x/goh");
        is(
            state_root,
            &[home, ("XDG_STATE_HOME", "x")],
            "/h/.local/state/goh",
        );
        is(repos_root, &[home], "/h/Projects");
        is(loop_queue, &[home], "/h/Projects/.loop/queue.json");
        is(loop_queue, &[("GOH_LOOP_QUEUE", "q.json")], "q.json");
        assert_eq!(at(state_root, &[]), Err("HOME is not set".to_owned()));
    }

    #[test]
    fn the_config_names_the_roots_and_the_environment_wins() {
        let (_dir, xdg) =
            conf("state_root = \"/s\"\nrepos_root = \"~/src\"\nloop_queue = \"/q.json\"\n");
        let vars = [
            ("HOME", "/h"),
            ("XDG_CONFIG_HOME", xdg.as_str()),
            ("XDG_STATE_HOME", "/x"),
        ];
        assert_eq!(at(state_root, &vars), Ok(PathBuf::from("/s")));
        assert_eq!(at(repos_root, &vars), Ok(PathBuf::from("/h/src")));
        assert_eq!(at(loop_queue, &vars), Ok(PathBuf::from("/q.json")));
        let env = [
            &vars[..],
            &[("GOH_STATE_ROOT", "/e"), ("GOH_REPOS_ROOT", "~/r")],
        ]
        .concat();
        assert_eq!(at(state_root, &env), Ok(PathBuf::from("/e")));
        assert_eq!(at(repos_root, &env), Ok(PathBuf::from("/h/r")));
    }

    #[test]
    fn an_unusable_config_or_variable_is_named_never_a_default() {
        for (text, said) in [
            ("state_root = 5\n", "state_root is 5"),
            ("state_root = \"rel\"\n", "not an absolute path"),
            ("state_root = \n", "config.toml"),
        ] {
            let (_dir, xdg) = conf(text);
            let got = at(state_root, &[("HOME", "/h"), ("XDG_CONFIG_HOME", &xdg)]);
            assert!(got.as_ref().is_err_and(|e| e.contains(said)), "{got:?}");
        }
        let got = at(state_root, &[("HOME", "/h"), ("GOH_STATE_ROOT", "rel")]);
        assert!(got.is_err_and(|e| e.contains("GOH_STATE_ROOT")));
        let (dir, xdg) = conf("");
        std::fs::remove_file(dir.path().join("goh/config.toml")).unwrap();
        std::fs::create_dir(dir.path().join("goh/config.toml")).unwrap();
        assert!(at(repos_root, &[("HOME", "/h"), ("XDG_CONFIG_HOME", &xdg)]).is_err());
        assert_eq!(or_said(Ok(PathBuf::from("/s"))), Some(PathBuf::from("/s")));
        assert_eq!(or_said(Err("no".to_owned())), None);
    }
}
