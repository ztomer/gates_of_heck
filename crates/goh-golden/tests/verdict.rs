//! Tolerances resolved from JSON overrides, the verdict over hand-made
//! pairs that trip each metric alone, and both renderings of it.

// Test-only crate: helpers `expect` well-formed JSON literals, and a
// malformed one is a broken test, not a result.
#![cfg(test)]

use goh_golden::{
    compare, resolve_tolerances, verdict_json, verdict_line, Image, Tolerances, Verdict,
};
use serde_json::{json, Map, Value};

/// A `width`x1 gray image, each value triplicated.
fn gray(values: &[u8]) -> Image {
    Image {
        width: u32::try_from(values.len()).expect("small"),
        height: 1,
        pixels: values.iter().flat_map(|v| [*v; 3]).collect(),
    }
}

fn overrides(value: &Value) -> Map<String, Value> {
    value.as_object().expect("an object literal").clone()
}

fn resolve_err(value: &Value) -> String {
    resolve_tolerances(&overrides(value))
        .expect_err("refused")
        .message
}

fn verdict(a: &Image, b: &Image) -> Verdict {
    compare(a, b, &Tolerances::default()).expect("same size")
}

/// A textured strip of 100 pixels, 0..=245 in steps of 5 twice: enough
/// variance that a few local changes barely move SSIM, and headroom for
/// every shift below.
fn strip() -> Vec<u8> {
    (0..100_u8).map(|i| (i % 50) * 5).collect()
}

#[test]
fn no_overrides_resolves_to_the_defaults() {
    assert_eq!(
        resolve_tolerances(&Map::new()).expect("empty"),
        Tolerances::default()
    );
}

#[test]
fn every_key_overrides_its_own_tolerance() {
    let resolved = resolve_tolerances(&overrides(&json!({
        "mean_abs_diff_max": 1.5,
        "changed_frac_max": 0.5,
        "changed_px_threshold": 20,
        "ssim_min": 0.9,
    })))
    .expect("valid overrides");
    assert_eq!(
        resolved,
        Tolerances {
            mean_abs_diff_max: 1.5,
            changed_frac_max: 0.5,
            changed_px_threshold: 20.0,
            ssim_min: 0.9,
        }
    );
    let one = resolve_tolerances(&overrides(&json!({"ssim_min": 0.5}))).expect("valid");
    assert_eq!(
        one,
        Tolerances {
            ssim_min: 0.5,
            ..Tolerances::default()
        }
    );
}

#[test]
fn an_unknown_key_lists_the_known_ones_sorted_as_python_would() {
    assert_eq!(
        resolve_err(&json!({"bogus": 1})),
        "unknown tolerance key: bogus (known: ['changed_frac_max', \
         'changed_px_threshold', 'mean_abs_diff_max', 'ssim_min'])"
    );
}

#[test]
fn a_non_number_is_refused_and_shown_as_python_repr() {
    for (value, shown) in [
        (json!("high"), "'high'"),
        (json!(true), "True"),
        (json!(false), "False"),
        (Value::Null, "None"),
        (json!([1, 2]), "[1,2]"),
    ] {
        assert_eq!(
            resolve_err(&json!({ "ssim_min": value })),
            format!("tolerance ssim_min must be a number, got {shown}")
        );
    }
}

/// The non-finite guard cannot be reached through `serde_json` without
/// `arbitrary_precision`: a NaN or infinity never becomes a JSON number,
/// so it arrives as `null` and is refused as a non-number instead. Pin
/// that, so turning the feature on (which would make the guard live)
/// shows up here.
#[test]
fn a_non_finite_number_cannot_enter_as_a_number() {
    assert!(serde_json::Number::from_f64(f64::NAN).is_none());
    assert!(serde_json::Number::from_f64(f64::INFINITY).is_none());
    assert_eq!(Value::from(f64::NAN), Value::Null);
    assert!(serde_json::from_str::<Value>("NaN").is_err());
    assert!(serde_json::from_str::<Value>("1e400").is_err());
    assert_eq!(
        resolve_err(&json!({ "mean_abs_diff_max": f64::INFINITY })),
        "tolerance mean_abs_diff_max must be a number, got None"
    );
}

#[test]
fn a_size_mismatch_is_a_precondition_not_a_verdict() {
    let a = Image {
        width: 2,
        height: 1,
        pixels: vec![0; 6],
    };
    let b = Image {
        width: 1,
        height: 2,
        pixels: vec![0; 6],
    };
    let err = compare(&a, &b, &Tolerances::default()).expect_err("mismatch");
    assert_eq!(err.message, "size mismatch: 2x1 vs 1x2");
}

#[test]
fn identical_frames_pass_with_the_identical_values() {
    let a = gray(&strip());
    let v = verdict(&a, &a.clone());
    assert!(v.identical && v.ok);
    assert_eq!(v.size, (100, 1));
    assert_eq!(v.values, vec![0.0, 0.0, 1.0]);
    assert!(v.failures.is_empty());
    assert_eq!(
        verdict_line(&v),
        "→ mean_abs_diff=0.0000 changed_frac=0.000000 ssim=1.0000 [png-crate/native] identical"
    );
}

#[test]
fn a_small_difference_is_within_tolerance() {
    let a = gray(&strip());
    let mut shifted = strip();
    shifted[0] += 3;
    let v = verdict(&a, &gray(&shifted));
    assert!(!v.identical && v.ok, "{v:?}");
    assert!(v.values[0].eq(&0.03));
    assert!(v.values[1].eq(&0.0));
    assert!(v.values[2] < 1.0 && v.values[2] > 0.985);
    let line = verdict_line(&v);
    assert!(line.starts_with("→ mean_abs_diff=0.0300 changed_frac=0.000000 ssim="));
    assert!(
        line.ends_with(" [png-crate/native] within tolerance"),
        "{line}"
    );
}

#[test]
fn a_uniform_shift_trips_mean_abs_diff_alone() {
    // Every channel moves by 10: over the 8.0 mean, under the 16 threshold,
    // and SSIM only loses its mean term (~0.997).
    let a = gray(&strip());
    let b = gray(&strip().iter().map(|v| v + 10).collect::<Vec<_>>());
    let v = verdict(&a, &b);
    assert_eq!(v.failures, vec!["mean_abs_diff 10.0000 > 8.0"]);
    assert!(!v.ok);
    assert!(v.values[1].eq(&0.0));
    assert!(v.values[2] > 0.985);
}

#[test]
fn a_few_hard_changes_trip_changed_fraction_alone() {
    // 9 of 100 pixels move by 17 (> 16): 9% changed, a tiny mean, and
    // structure barely touched.
    let a = gray(&strip());
    let mut moved = strip();
    for px in moved.iter_mut().step_by(11).take(9) {
        *px += 17;
    }
    let v = verdict(&a, &gray(&moved));
    assert_eq!(v.failures, vec!["changed_fraction 0.090000 > 0.08"]);
    assert!(v.values[0] < 8.0);
    assert!(v.values[2] > 0.985, "{v:?}");
}

#[test]
fn an_inverted_low_contrast_pattern_trips_ssim_alone() {
    // Means equal, every step 4 (under the threshold, mean 4.0), but the
    // structure is anti-correlated: SSIM (c2 - 8) / (c2 + 8) ~ 0.7595.
    let a = gray(&[126, 130, 126, 130]);
    let b = gray(&[130, 126, 130, 126]);
    let v = verdict(&a, &b);
    assert_eq!(v.failures, vec!["ssim 0.7595 < 0.985"]);
    assert!(v.values[0].eq(&4.0));
    assert!(v.values[1].eq(&0.0));
    assert!(!v.ok);
}

#[test]
fn several_failures_join_on_the_line_under_the_fail_mark() {
    let a = gray(&[0, 0, 0, 0]);
    let b = gray(&[255, 0, 255, 0]);
    let v = verdict(&a, &b);
    assert_eq!(v.failures.len(), 3, "{v:?}");
    assert_eq!(
        verdict_line(&v),
        "✗ mean_abs_diff=127.5000 changed_frac=0.500000 ssim=0.0000 [png-crate/native] \
         mean_abs_diff 127.5000 > 8.0, changed_fraction 0.500000 > 0.08, ssim 0.0000 < 0.985"
    );
}

#[test]
fn verdict_json_carries_every_metric_tolerance_and_the_tier() {
    let a = gray(&[126, 130, 126, 130]);
    let b = gray(&[130, 126, 130, 126]);
    let v = verdict(&a, &b);
    let out = verdict_json(&v);
    let keys: Vec<&str> = out
        .as_object()
        .expect("object")
        .keys()
        .map(String::as_str)
        .collect();
    let mut expected_keys = [
        "failures",
        "identical",
        "metrics",
        "ok",
        "size",
        "tier",
        "tolerances",
    ];
    expected_keys.sort_unstable();
    let mut sorted = keys.clone();
    sorted.sort_unstable();
    assert_eq!(sorted, expected_keys);
    assert_eq!(out["identical"], json!(false));
    assert_eq!(out["ok"], json!(false));
    assert_eq!(out["size"], json!([4, 1]));
    assert_eq!(out["metrics"]["mean_abs_diff"], json!(4.0));
    assert_eq!(out["metrics"]["changed_fraction"], json!(0.0));
    assert_eq!(out["metrics"]["ssim"], json!(v.values[2]));
    assert_eq!(
        out["tolerances"],
        json!({
            "mean_abs_diff_max": 8.0,
            "changed_frac_max": 0.08,
            "changed_px_threshold": 16.0,
            "ssim_min": 0.985,
        })
    );
    assert_eq!(out["failures"], json!(["ssim 0.7595 < 0.985"]));
    assert_eq!(
        out["tier"],
        json!({"decoder": "png-crate", "compute": "native"})
    );
}

#[test]
fn a_custom_tolerance_is_what_the_failure_prints() {
    let t = resolve_tolerances(&overrides(&json!({"mean_abs_diff_max": 0.00001}))).expect("valid");
    let mut nudged = strip();
    nudged[1] += 1;
    let v = compare(&gray(&strip()), &gray(&nudged), &t).expect("same size");
    assert_eq!(v.failures, vec!["mean_abs_diff 0.0100 > 1e-05"]);
    assert_eq!(
        verdict_json(&v)["tolerances"]["mean_abs_diff_max"],
        json!(0.00001)
    );
}
