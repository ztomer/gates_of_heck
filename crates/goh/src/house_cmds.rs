//! The subcommands that run one house script, read from `crates/goh/house_scripts.tsv`.
//!
//! GOH.LANDUNION: adding one is one line of that table, never a shared line of `cli.rs` or
//! `main.rs`, so two lanes adding a subcommand each no longer conflict (the table merges by row,
//! `lib/row_merge.py`). Each takes every argument, `--help` included (GOH.SHIPADOPT).

use clap::{Arg, ArgAction, ArgMatches, Command};

/// The table, embedded: one `NAME[|ALIAS...] TAB SCRIPT TAB ABOUT` line per subcommand.
pub const TABLE: &str = include_str!("../house_scripts.tsv");

/// One house-script subcommand.
#[derive(Debug, PartialEq, Eq)]
pub struct Entry<'a> {
    /// Its name, then its aliases.
    pub names: Vec<&'a str>,
    /// The script, relative to the goh root.
    pub script: &'a str,
    /// The line `goh --help` prints.
    pub about: &'a str,
}

/// The table's entries, in order; a line that is not three tab-separated fields is an error.
///
/// # Errors
/// The first malformed line, numbered from 1.
pub fn parse(table: &str) -> Result<Vec<Entry<'_>>, String> {
    let mut out = Vec::new();
    for (n, line) in table.lines().enumerate() {
        if line.trim().is_empty() || line.trim_start().starts_with('#') {
            continue;
        }
        let fields: Vec<&str> = line.split('\t').collect();
        match fields.as_slice() {
            [names, script, about] if !names.is_empty() && !script.is_empty() => out.push(Entry {
                names: names.split('|').collect(),
                script,
                about,
            }),
            _ => {
                return Err(format!(
                    "house_scripts.tsv:{}: not NAME TAB SCRIPT TAB ABOUT",
                    n + 1
                ))
            }
        }
    }
    Ok(out)
}

/// The embedded table's entries (its shape is pinned by this module's tests).
#[must_use]
pub fn entries() -> Vec<Entry<'static>> {
    parse(TABLE).unwrap_or_default()
}

/// `base` with every table entry added as a subcommand that passes its arguments through.
#[must_use]
pub fn command(base: Command) -> Command {
    entries().into_iter().fold(base, |cmd, e| {
        cmd.subcommand(
            Command::new(e.names[0])
                .about(e.about)
                .aliases(e.names[1..].iter().copied())
                .disable_help_flag(true)
                .arg(
                    Arg::new("args")
                        .num_args(0..)
                        .trailing_var_arg(true)
                        .allow_hyphen_values(true)
                        .action(ArgAction::Append),
                ),
        )
    })
}

/// The (name, script, arguments) of a parsed house-script subcommand, or None for any other.
#[must_use]
pub fn chosen(matches: &ArgMatches) -> Option<(&'static str, &'static str, Vec<String>)> {
    let (name, sub) = matches.subcommand()?;
    let entry = entries().into_iter().find(|e| e.names[0] == name)?;
    let args = sub
        .get_many::<String>("args")
        .map(|v| v.cloned().collect())
        .unwrap_or_default();
    Some((entry.names[0], entry.script, args))
}

#[cfg(test)]
#[path = "house_cmds_tests.rs"]
mod tests;
