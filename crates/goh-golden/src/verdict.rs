//! The verdict: tolerances resolved from overrides, metrics checked
//! against them, and the reference's two renderings (JSON and one line).
//! Everything loops over the tables in `metrics`.

use crate::metrics::{Tolerances, METRICS, TOLERANCES};
use crate::{Image, PreconditionError};

/// What this implementation reports as its tier, where the reference
/// names Pillow/minimal and numpy/pure-python.
pub const DECODER_TIER: &str = "png-crate";
pub const COMPUTE_TIER: &str = "native";

/// The line's marks and states, as the reference prints them.
const MARK_OK: &str = "→";
const MARK_FAIL: &str = "✗";
const STATE_IDENTICAL: &str = "identical";
const STATE_WITHIN: &str = "within tolerance";
const FAILURE_SEPARATOR: &str = ", ";

/// Python's `repr(float)` switches to scientific notation below 1e-4 and
/// at 1e16 and above; the failure lines print tolerances through it.
const REPR_MIN_FIXED_EXPONENT: i32 = -4;
const REPR_MAX_FIXED_EXPONENT: i32 = 16;
/// Python pads the exponent to at least two digits (`1e-05`).
const REPR_EXPONENT_WIDTH: usize = 2;

/// Defaults overridden by `overrides`. Unknown keys, non-numbers and
/// non-finite numbers are precondition errors: every comparison against
/// NaN is false, so a NaN tolerance would forge a pass.
///
/// # Errors
///
/// The reason, in the reference's words.
pub fn resolve_tolerances(
    overrides: &serde_json::Map<String, serde_json::Value>,
) -> Result<Tolerances, PreconditionError> {
    let mut out = Tolerances::default();
    for (key, value) in overrides {
        let Some(spec) = TOLERANCES.iter().find(|s| s.key == key) else {
            let mut known: Vec<&str> = TOLERANCES.iter().map(|s| s.key).collect();
            known.sort_unstable();
            let listed = known
                .iter()
                .map(|k| format!("'{k}'"))
                .collect::<Vec<_>>()
                .join(FAILURE_SEPARATOR);
            return Err(PreconditionError {
                message: format!("unknown tolerance key: {key} (known: [{listed}])"),
            });
        };
        let Some(number) = value.as_f64() else {
            return Err(PreconditionError {
                message: format!(
                    "tolerance {key} must be a number, got {}",
                    python_value_repr(value)
                ),
            });
        };
        if !number.is_finite() {
            return Err(PreconditionError {
                message: format!("tolerance {key} must be finite, got {value} (NaN/Infinity cannot be compared against)"),
            });
        }
        (spec.set)(&mut out, number);
    }
    Ok(out)
}

/// One verdict: every metric's value, the failures, the pass flag.
#[derive(Debug, Clone, PartialEq)]
pub struct Verdict {
    pub identical: bool,
    pub size: (u32, u32),
    /// Values in `METRICS` order.
    pub values: Vec<f64>,
    pub tolerances: Tolerances,
    pub failures: Vec<String>,
    pub ok: bool,
}

/// Compare two frames under `tolerances`.
///
/// # Errors
///
/// A size mismatch is a precondition, not a verdict.
pub fn compare(
    a: &Image,
    b: &Image,
    tolerances: &Tolerances,
) -> Result<Verdict, PreconditionError> {
    if (a.width, a.height) != (b.width, b.height) {
        return Err(PreconditionError {
            message: format!(
                "size mismatch: {}x{} vs {}x{}",
                a.width, a.height, b.width, b.height
            ),
        });
    }
    let identical = a.pixels == b.pixels;
    let values: Vec<f64> = METRICS
        .iter()
        .map(|m| {
            if identical {
                m.when_identical
            } else {
                (m.compute)(a, b, tolerances)
            }
        })
        .collect();
    let failures: Vec<String> = METRICS
        .iter()
        .zip(&values)
        .filter(|(m, v)| m.bound.broken(**v, (m.limit)(tolerances)))
        .map(|(m, v)| {
            format!(
                "{} {:.prec$} {} {}",
                m.name,
                v,
                m.bound.symbol(),
                python_repr((m.limit)(tolerances)),
                prec = m.decimals
            )
        })
        .collect();
    Ok(Verdict {
        identical,
        size: (a.width, a.height),
        values,
        tolerances: tolerances.clone(),
        ok: failures.is_empty(),
        failures,
    })
}

/// The reference's `--json` object.
#[must_use]
pub fn verdict_json(verdict: &Verdict) -> serde_json::Value {
    let metrics: serde_json::Map<String, serde_json::Value> = METRICS
        .iter()
        .zip(&verdict.values)
        .map(|(m, v)| (m.name.to_owned(), (*v).into()))
        .collect();
    let tolerances: serde_json::Map<String, serde_json::Value> = TOLERANCES
        .iter()
        .map(|s| (s.key.to_owned(), (s.get)(&verdict.tolerances).into()))
        .collect();
    serde_json::json!({
        "identical": verdict.identical,
        "size": [verdict.size.0, verdict.size.1],
        "metrics": metrics,
        "tolerances": tolerances,
        "failures": verdict.failures,
        "ok": verdict.ok,
        "tier": {"decoder": DECODER_TIER, "compute": COMPUTE_TIER},
    })
}

/// The reference's human line.
#[must_use]
pub fn verdict_line(verdict: &Verdict) -> String {
    let state = if verdict.identical {
        STATE_IDENTICAL.to_owned()
    } else if verdict.failures.is_empty() {
        STATE_WITHIN.to_owned()
    } else {
        verdict.failures.join(FAILURE_SEPARATOR)
    };
    let mark = if verdict.ok { MARK_OK } else { MARK_FAIL };
    let metrics: Vec<String> = METRICS
        .iter()
        .zip(&verdict.values)
        .map(|(m, v)| format!("{}={:.prec$}", m.line_label, v, prec = m.decimals))
        .collect();
    format!(
        "{mark} {} [{DECODER_TIER}/{COMPUTE_TIER}] {state}",
        metrics.join(" ")
    )
}

/// A JSON value as Python's `repr` prints the object `json.loads` made of
/// it: `'high'`, `True`, `None`, `[1, 2]`. Only what a precondition
/// message can show needs to be exact; containers fall back to JSON.
fn python_value_repr(value: &serde_json::Value) -> String {
    match value {
        serde_json::Value::String(s) => format!("'{s}'"),
        serde_json::Value::Bool(true) => "True".to_owned(),
        serde_json::Value::Bool(false) => "False".to_owned(),
        serde_json::Value::Null => "None".to_owned(),
        other => other.to_string(),
    }
}

/// `x` as Python's `repr(float)` spells it.
///
/// The shortest round-trip digits, fixed notation for exponents in
/// [-4, 16), scientific with a signed two-digit exponent outside it, and
/// always a decimal point (`8.0`).
#[must_use]
pub fn python_repr(x: f64) -> String {
    // `{:e}` is Rust's shortest round-trip form: "8e0", "1.5e-5".
    let sci = format!("{x:e}");
    let (mantissa, exponent) = sci.split_once('e').unwrap_or((sci.as_str(), "0"));
    let exponent: i32 = exponent.parse().unwrap_or(0);
    let negative = mantissa.starts_with('-');
    let digits: String = mantissa.chars().filter(char::is_ascii_digit).collect();
    let sign = if negative { "-" } else { "" };
    if (REPR_MIN_FIXED_EXPONENT..REPR_MAX_FIXED_EXPONENT).contains(&exponent) {
        let point = exponent + 1; // digits before the decimal point
        let fixed = if point <= 0 {
            let zeros = "0".repeat(usize::try_from(-point).unwrap_or(0));
            format!("0.{zeros}{digits}")
        } else {
            let point = usize::try_from(point).unwrap_or(0);
            if digits.len() <= point {
                format!("{digits}{}.0", "0".repeat(point - digits.len()))
            } else {
                format!("{}.{}", &digits[..point], &digits[point..])
            }
        };
        return format!("{sign}{fixed}");
    }
    let (head, tail) = digits.split_at(1);
    let fraction = if tail.is_empty() {
        String::new()
    } else {
        format!(".{tail}")
    };
    let exp_sign = if exponent < 0 { '-' } else { '+' };
    format!(
        "{sign}{head}{fraction}e{exp_sign}{:0width$}",
        exponent.unsigned_abs(),
        width = REPR_EXPONENT_WIDTH
    )
}

#[cfg(test)]
mod tests {
    use super::python_repr;

    /// Pinned against `CPython`'s `repr`, including both notation switches.
    #[test]
    fn python_repr_matches_cpython() {
        for (value, expected) in [
            (8.0, "8.0"),
            (0.985, "0.985"),
            (0.08, "0.08"),
            (100.0, "100.0"),
            (0.0001, "0.0001"),
            (0.00001, "1e-05"),
            (1.5e-7, "1.5e-07"),
            (1e16, "1e+16"),
            (1_234_567_890_123_456.0, "1234567890123456.0"),
            (-2.5, "-2.5"),
            (0.0, "0.0"),
        ] {
            assert_eq!(python_repr(value), expected, "{value}");
        }
    }
}
