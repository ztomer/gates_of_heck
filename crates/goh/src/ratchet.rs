//! Shrink-only ratchet engine — Rust port of the `check_baseline_ratchet.py`
//! pieces the structural ceiling step runs (baseline parsing, the loc
//! measurement, comparison, reporting).
//!
//! NARROW SCOPE, stated plainly: the general ratchet surface (`--record`,
//! `--current-from-command` over arbitrary shell, `--allow-new-keys`)
//! stays in `check_baseline_ratchet.py` for direct consumer use; porting
//! the shell-spawning half would trade a tested `killtree` process-group
//! kill for a thinner one, which is a behavior gap, not a port.
//!
//! Baseline formats, same as the reference: JSON `{"key": number}` or line
//! `"<value><TAB><key>"` / `"<value> <key>"` with `#` whole-line comments.
//! Two accepted divergences, both unrepresentable in any real baseline:
//! non-ASCII digits (`float()` accepts Arabic-Indic digits, `f64::from_str`
//! does not) and `str::lines` vs `splitlines` breaks (vertical tab, form
//! feed).
//!
//! Line counting is the shared `gitutil::line_count` — the reference's
//! `loc_of_baseline_files.py` counts binary-split lines, which agrees with
//! it on every input (a trailing newline terminates, it does not begin
//! another; empty is 0).

use std::collections::BTreeMap;
use std::fmt::Write as _;

/// Strict ASCII number grammar for line-format values, mirroring
/// `_ASCII_NUMBER` (sign, int/float, optional exponent, anchored).
const ASCII_NUMBER: &str = r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?";

/// Significant digits of the reference's `{v:g}` formatting: the `g`
/// conversion defaults to 6 significant digits, fixed notation for magnitudes
/// in `[1e-4, 1e6)`, exponent otherwise. Every digit below derives from
/// these two — nothing is tuned.
const G_SIGNIFICANT_DIGITS: i32 = 6;
/// Fixed notation below this decimal exponent.
const G_FIXED_MIN_EXP: i32 = -4;
/// Fixed notation below this decimal exponent (6 == the digit count).
const G_FIXED_MAX_EXP: i32 = 6;

/// The compiled grammar, built once per parse (compiling it per value
/// would charge a regex build for every baseline row).
struct Grammar {
    /// Anchored ASCII-number matcher.
    rx: regex::Regex,
}

impl Grammar {
    /// Compile the built-in grammar.
    ///
    /// # Errors
    ///
    /// Returns a message only if the built-in pattern fails to compile,
    /// which is a programming error, never input-dependent.
    fn compile() -> Result<Self, String> {
        regex::Regex::new(&format!("^{ASCII_NUMBER}$"))
            .map(|rx| Self { rx })
            .map_err(|e| format!("built-in number grammar broken: {e}"))
    }
}

/// Parse one line-format value the way `_line_value` does: strict ASCII
/// grammar first, then a Python-`float()`-shaped fallback that only admits
/// the non-finite spellings (returned for the later rejection) and
/// underscore-grouped digits. Anything else is a named precondition error.
fn line_value(grammar: &Grammar, raw: &str, origin: &str, lineno: usize) -> Result<f64, String> {
    if grammar.rx.is_match(raw) {
        return raw
            .parse::<f64>()
            .map_err(|_| format!("{origin}:{lineno}: value exceeds float range: {raw:.40}"));
    }
    let lower = raw.to_ascii_lowercase();
    if [
        "inf",
        "infinity",
        "+inf",
        "+infinity",
        "-inf",
        "-infinity",
        "nan",
        "+nan",
        "-nan",
    ]
    .contains(&lower.as_str())
    {
        return Ok(lower.parse::<f64>().unwrap_or(f64::NAN));
    }
    // Python `float("1_0")` is 10.0; `f64::from_str` rejects the
    // underscore. Strip them the way `float` does and accept only a
    // finite result.
    if raw.contains('_') {
        let squashed: String = raw.chars().filter(|c| *c != '_').collect();
        if grammar.rx.is_match(&squashed) {
            if let Ok(v) = squashed.parse::<f64>() {
                if v.is_finite() {
                    return Ok(v);
                }
            }
        }
    }
    Err(format!(
        "{origin}:{lineno}: value is not a plain ASCII number: {raw:?}"
    ))
}

/// Reject non-finite values wherever they appear, mirroring
/// `_reject_nonfinite`: NaN comparisons are always false (a NaN current
/// would silently pass), so they are a precondition failure, never data.
fn reject_nonfinite(values: &BTreeMap<String, f64>) -> Result<(), String> {
    let shown: Vec<&str> = values
        .iter()
        .filter(|(_, v)| !v.is_finite())
        .map(|(k, _)| k.as_str())
        .take(5)
        .collect();
    if shown.is_empty() {
        Ok(())
    } else {
        Err(format!(
            "non-finite values (NaN/Infinity) are not measurable — keys {shown:?}"
        ))
    }
}

/// Parse either baseline format into `{key: number}`, mirroring `parse()`.
/// JSON values must be numbers (booleans rejected, like the reference);
/// unbounded JSON ints beyond float range are named.
///
/// # Errors
///
/// Returns the precondition message when the text is empty, unparseable,
/// mistyped, or non-finite.
pub fn parse_baseline(text: &str, origin: &str) -> Result<BTreeMap<String, f64>, String> {
    let grammar = Grammar::compile()?;
    let stripped = text.trim();
    if stripped.is_empty() {
        return Err(format!("{origin}: empty — nothing to verify"));
    }
    if stripped.starts_with('{') {
        let data: serde_json::Value = serde_json::from_str(stripped)
            .map_err(|e| format!("{origin}: looks like JSON but does not parse ({e})"))?;
        let obj = data
            .as_object()
            .ok_or_else(|| format!("{origin}: JSON must be an object mapping key to number"))?;
        let bad: Vec<&String> = obj
            .keys()
            .filter(|k| !obj[*k].is_number())
            .take(5)
            .collect();
        if !bad.is_empty() {
            return Err(format!("{origin}: non-numeric values for keys {bad:?}"));
        }
        let mut entries = BTreeMap::new();
        let mut huge = Vec::new();
        for (k, v) in obj {
            if let Some(n) = v.as_f64() {
                entries.insert(k.clone(), n);
            } else {
                huge.push(k.clone());
            }
        }
        if !huge.is_empty() {
            huge.truncate(5);
            return Err(format!(
                "{origin}: value(s) too large to measure (float range exceeded) for keys {huge:?}"
            ));
        }
        reject_nonfinite(&entries)?;
        return Ok(entries);
    }
    let mut out = BTreeMap::new();
    for (idx, line) in stripped.lines().enumerate() {
        let lineno = idx + 1;
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let raw: Vec<&str> = if line.contains('\t') {
            line.splitn(2, '\t').collect()
        } else {
            line.splitn(2, char::is_whitespace).collect()
        };
        if raw.len() != 2 {
            return Err(format!(
                "{origin}:{lineno}: expected '<value><TAB><key>' or '<value> <key>', got: {line:?}"
            ));
        }
        out.insert(
            raw[1].trim().to_owned(),
            line_value(&grammar, raw[0].trim(), origin, lineno)?,
        );
    }
    reject_nonfinite(&out)?;
    Ok(out)
}

/// Today's line counts for every key in `baseline`.
///
/// Natively replaces the `loc_of_baseline_files.py` subprocess: a listed
/// path that is not a file measures 0 (vanished files are under any
/// ceiling; the exclusion check is what notices stale entries).
#[must_use]
pub fn measure_loc(
    root: &std::path::Path,
    baseline: &BTreeMap<String, f64>,
) -> BTreeMap<String, f64> {
    baseline
        .keys()
        .map(|key| {
            let path = root.join(key);
            let count = if path.is_file() {
                std::fs::read(&path)
                    .ok()
                    .map_or(0.0, |blob| count_f64(crate::gitutil::line_count(&blob)))
            } else {
                0.0
            };
            (key.clone(), count)
        })
        .collect()
}

/// A line count as an f64 ceiling value. Counts never approach 2^53, so
/// the conversion is exact; the fallback names the impossible so the
/// `Option` is discharged honestly instead of unwrapped.
fn count_f64(lines: usize) -> f64 {
    use num_traits::ToPrimitive as _;
    lines.to_f64().unwrap_or_else(|| f64::from(u32::MAX))
}

/// The four ratchet populations, mirroring the reference names.
#[derive(Debug, Default, PartialEq)]
pub struct Comparison {
    /// In both, current above ceiling.
    pub rose: Vec<String>,
    /// In current, absent from baseline.
    pub grew: Vec<String>,
    /// In both, current below baseline: key → (baseline, current).
    pub shrank: Vec<(String, f64, f64)>,
    /// In baseline, absent from current.
    pub vanished: Vec<String>,
}

/// Split baseline vs current into the four populations.
#[must_use]
pub fn compare(baseline: &BTreeMap<String, f64>, current: &BTreeMap<String, f64>) -> Comparison {
    let mut out = Comparison::default();
    for key in current.keys() {
        if !baseline.contains_key(key) {
            out.grew.push(key.clone());
        }
    }
    for (key, base) in baseline {
        match current.get(key) {
            None => out.vanished.push(key.clone()),
            Some(cur) if cur > base => out.rose.push(key.clone()),
            Some(cur) if cur < base => out.shrank.push((key.clone(), *base, *cur)),
            Some(_) => {}
        }
    }
    out
}

/// Format a float the way the reference's `{v:g}` does: 6 significant
/// digits, fixed notation for magnitudes in `[1e-4, 1e6)`, exponent
/// otherwise, no trailing zeros. Both formatters round the exact binary
/// value, so digit strings agree.
fn format_g(value: f64) -> String {
    if value == 0.0 {
        return if value.is_sign_negative() {
            "-0".to_owned()
        } else {
            "0".to_owned()
        };
    }
    let sign = if value.is_sign_negative() { "-" } else { "" };
    let mag = value.abs();
    // Exact decimal exponent from Rust's scientific rendering (one digit
    // before the point): no `log10` float error at powers of ten.
    let sci = format!("{mag:e}");
    let exp: i32 = sci.split('e').nth(1).unwrap_or("0").parse().unwrap_or(0);
    if (G_FIXED_MIN_EXP..G_FIXED_MAX_EXP).contains(&exp) {
        // `exp` in [-4, 6): `after` in [0, 9], so the `try_from` cannot
        // fail — the fallback is unreachable-but-required, like every
        // discharged `Option` in this file.
        let after = usize::try_from(G_SIGNIFICANT_DIGITS - 1 - exp).unwrap_or(0);
        let mut text = format!("{mag:.after$}");
        if text.contains('.') {
            text = text.trim_end_matches('0').trim_end_matches('.').to_owned();
        }
        return format!("{sign}{text}");
    }
    // One fewer than the significant count; non-negative by construction,
    // so the fallback is unreachable-but-required.
    let mantissa_digits = usize::try_from(G_SIGNIFICANT_DIGITS - 1).unwrap_or(0);
    let mut mantissa = format!("{mag:.mantissa_digits$e}");
    let exp_part = mantissa.split_off(mantissa.find('e').unwrap_or(mantissa.len()));
    let exp_val: i32 = exp_part[1..].parse().unwrap_or(0);
    let frac = mantissa.split('.').nth(1).unwrap_or("");
    let int = mantissa.split('.').next().unwrap_or("0");
    let frac = frac.trim_end_matches('0');
    let mantissa = if frac.is_empty() {
        int.to_owned()
    } else {
        format!("{int}.{frac}")
    };
    format!("{sign}{mantissa}e{exp_val:+03}")
}

/// The ratchet verdict block.
///
/// Passages mirror the reference's text; `ok` is true on pass. The
/// blind-gate rule is here too: a non-empty baseline against an EMPTY
/// current measurement is a blind gate (exit 1), never total success.
#[must_use]
pub fn format_ratchet(
    baseline: &BTreeMap<String, f64>,
    current: &BTreeMap<String, f64>,
    cmp: &Comparison,
) -> (bool, String) {
    if !cmp.rose.is_empty() || !cmp.grew.is_empty() {
        let mut text = format!(
            "✗ [ratchet] {} ceiling(s) exceeded, {} new key(s) — shrink-only:\n",
            cmp.rose.len(),
            cmp.grew.len()
        );
        for key in &cmp.rose {
            let (base, cur) = (baseline[key], current[key]);
            let _ = writeln!(
                text,
                "    {key}: {} -> {}  (+{})",
                format_g(base),
                format_g(cur),
                format_g(cur - base)
            );
        }
        for key in &cmp.grew {
            let _ = writeln!(
                text,
                "    {key}: NEW at {} (use --allow-new-keys to permit)",
                format_g(current[key])
            );
        }
        text.push_str(
            "\n  Fix the regression, or — only if the new size is genuinely\n  intended — re-record the ceiling in the same commit, so the\n  growth is reviewed rather than absorbed.\n",
        );
        return (false, text);
    }
    if !baseline.is_empty() && current.is_empty() {
        let n = baseline.len();
        let entries = if n == 1 { "entry" } else { "entries" };
        return (
            false,
            format!(
                "✗ [ratchet] the baseline names {n} {entries} and the current measurement found NONE. That is a blind gate, not a clean one -- every ceiling is trivially met when there is nothing left to measure.\n  Find what stopped producing the measurement. If the entries are genuinely gone,\n  re-record the baseline in the same commit so the deletion is reviewed.\n",
            ),
        );
    }
    let mut parts = Vec::new();
    if !cmp.shrank.is_empty() {
        let mut items = cmp.shrank.clone();
        items.sort_by(|a, b| a.0.cmp(&b.0));
        parts.push(
            items
                .iter()
                .map(|(k, b, c)| format!("{k} {}-> {}", format_g(*b), format_g(*c)))
                .collect::<Vec<_>>()
                .join(", "),
        );
    }
    if !cmp.vanished.is_empty() {
        parts.push(format!("vanished: {}", cmp.vanished.join(", ")));
    }
    let detail = if parts.is_empty() {
        String::new()
    } else {
        format!(" ({})", parts.join("; "))
    };
    let n = current.len();
    let entries = if n == 1 { "entry" } else { "entries" };
    (
        true,
        format!("→ [ratchet] OK — {n} {entries} within ceilings{detail}\n"),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn map(pairs: &[(&str, f64)]) -> BTreeMap<String, f64> {
        pairs.iter().map(|(k, v)| ((*k).to_owned(), *v)).collect()
    }

    #[test]
    fn line_format_parses_tab_and_space() {
        let got = parse_baseline("600\tsrc/big.rs\n450 src/small.rs\n", "base").expect("parses");
        // Exactness is the intent: whole numbers parse bit-exact, asserted
        // through the display path (float `assert_eq` trips `float_cmp`,
        // `partial_cmp` trips `manual_assert_eq` — the string is the value
        // the gate reports, so it is the honest assertion).
        assert_eq!(format_g(got["src/big.rs"]), "600");
        assert_eq!(format_g(got["src/small.rs"]), "450");
    }

    #[test]
    fn json_format_parses_and_rejects_bools() {
        let got = parse_baseline("{\"a\": 3, \"b\": 4.5}", "base").expect("parses");
        assert_eq!(format_g(got["a"]), "3");
        assert!(parse_baseline("{\"a\": true}", "base").is_err());
        assert!(parse_baseline("[1, 2]", "base").is_err());
        assert!(parse_baseline("", "base").is_err());
        assert!(parse_baseline("{\"a\": NaN}", "base").is_err());
    }

    #[test]
    fn line_values_reject_non_ascii_numbers() {
        assert!(parse_baseline("1_0\tk", "base").is_ok());
        assert!(parse_baseline("abc\tk", "base").is_err());
        assert!(parse_baseline("inf\tk", "base").is_err());
        assert!(parse_baseline("10", "base").is_err());
    }

    #[test]
    fn compare_splits_four_populations() {
        let base = map(&[("rose", 5.0), ("same", 5.0), ("shrank", 9.0), ("gone", 1.0)]);
        let cur = map(&[("rose", 6.0), ("same", 5.0), ("shrank", 7.0), ("new", 2.0)]);
        let cmp = compare(&base, &cur);
        assert_eq!(cmp.rose, ["rose"]);
        assert_eq!(cmp.grew, ["new"]);
        assert_eq!(cmp.vanished, ["gone"]);
        assert_eq!(cmp.shrank.len(), 1);
        let (ok, report) = format_ratchet(&base, &cur, &cmp);
        assert!(!ok);
        assert!(report.contains("1 ceiling(s) exceeded, 1 new key(s)"));
    }

    #[test]
    fn empty_current_against_baseline_is_blind_not_clean() {
        let base = map(&[("a", 1.0)]);
        let (ok, report) =
            format_ratchet(&base, &BTreeMap::new(), &compare(&base, &BTreeMap::new()));
        assert!(!ok);
        assert!(report.contains("blind gate"));
        let (ok, _) = format_ratchet(&BTreeMap::new(), &BTreeMap::new(), &Comparison::default());
        assert!(ok);
    }

    #[test]
    fn g_format_matches_python_at_every_magnitude() {
        // Spot-checked against `format(v, ".6g")` in CPython.
        let cases = [
            (600.0, "600"),
            (4.5, "4.5"),
            (100.0, "100"),
            (123_456.0, "123456"),
            (1_234_567.0, "1.23457e+06"),
            (0.0001, "0.0001"),
            (0.000_01, "1e-05"),
            (1.5e-7, "1.5e-07"),
            (-0.0, "-0"),
            (0.0, "0"),
            (1.0 / 3.0, "0.333333"),
            (2.0 / 3.0, "0.666667"),
        ];
        for (value, want) in cases {
            assert_eq!(format_g(value), want, "{value}");
        }
    }
}
