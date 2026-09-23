//! The metric and tolerance TABLES row by row, and each metric function on
//! hand-made pairs whose value is known in closed form.

// Test-only crate: helpers `expect` a table row that must exist, and a
// missing row is a broken test, not a result.
#![cfg(test)]

use goh_golden::metrics::{
    changed_fraction, mean_abs_diff, ssim, Bound, CHANGED_FRAC_MAX, CHANGED_PX_THRESHOLD,
    MEAN_ABS_DIFF_MAX, SSIM_MIN,
};
use goh_golden::numeric::total;
use goh_golden::{Image, PreconditionError, Tolerances, METRICS, TOLERANCES};

/// A `width`x1 gray image, each value triplicated.
fn gray(values: &[u8]) -> Image {
    Image {
        width: u32::try_from(values.len()).expect("small"),
        height: 1,
        pixels: values.iter().flat_map(|v| [*v; 3]).collect(),
    }
}

/// The SSIM stability constants, recomputed independently of the crate.
const C1: f64 = (0.01 * 255.0) * (0.01 * 255.0);
const C2: f64 = (0.03 * 255.0) * (0.03 * 255.0);

#[test]
fn bound_at_most_breaks_only_strictly_above_the_limit() {
    assert!(Bound::AtMost.broken(8.5, 8.0));
    assert!(!Bound::AtMost.broken(8.0, 8.0));
    assert!(!Bound::AtMost.broken(7.0, 8.0));
    assert_eq!(Bound::AtMost.symbol(), ">");
}

#[test]
fn bound_at_least_breaks_only_strictly_below_the_limit() {
    assert!(Bound::AtLeast.broken(0.9, 0.985));
    assert!(!Bound::AtLeast.broken(0.985, 0.985));
    assert!(!Bound::AtLeast.broken(1.0, 0.985));
    assert_eq!(Bound::AtLeast.symbol(), "<");
}

#[test]
fn default_tolerances_are_the_documented_ones() {
    assert_eq!(
        Tolerances::default(),
        Tolerances {
            mean_abs_diff_max: 8.0,
            changed_frac_max: 0.08,
            changed_px_threshold: 16.0,
            ssim_min: 0.985,
        }
    );
    let keys: Vec<&str> = TOLERANCES.iter().map(|s| s.key).collect();
    assert_eq!(
        keys,
        [
            MEAN_ABS_DIFF_MAX,
            CHANGED_FRAC_MAX,
            CHANGED_PX_THRESHOLD,
            SSIM_MIN
        ]
    );
}

/// Each row's `set` writes exactly its own field and `get` reads it back.
#[test]
fn every_tolerance_row_gets_and_sets_its_own_field() {
    let field = |t: &Tolerances, key: &str| match key {
        MEAN_ABS_DIFF_MAX => t.mean_abs_diff_max,
        CHANGED_FRAC_MAX => t.changed_frac_max,
        CHANGED_PX_THRESHOLD => t.changed_px_threshold,
        SSIM_MIN => t.ssim_min,
        other => panic!("unknown key {other}"),
    };
    for (i, spec) in TOLERANCES.iter().enumerate() {
        let base = Tolerances::default();
        assert!((spec.get)(&base).eq(&spec.default), "{}", spec.key);
        let mut t = base.clone();
        let value = 100.0 + f64::from(u32::try_from(i).expect("small"));
        (spec.set)(&mut t, value);
        assert!((spec.get)(&t).eq(&value), "{}", spec.key);
        assert!(field(&t, spec.key).eq(&value), "{}", spec.key);
        for other in TOLERANCES.iter().filter(|o| o.key != spec.key) {
            assert!(
                field(&t, other.key).eq(&other.default),
                "{} leaked into {}",
                spec.key,
                other.key
            );
        }
    }
}

/// Every METRICS row computes what its function computes, reads its own
/// tolerance, and is bounded on the documented side.
#[test]
fn every_metric_row_computes_its_function_and_reads_its_limit() {
    let a = gray(&[100, 110, 120, 130]);
    let b = gray(&[100, 110, 140, 160]);
    let t = Tolerances {
        mean_abs_diff_max: 1.0,
        changed_frac_max: 2.0,
        changed_px_threshold: 25.0,
        ssim_min: 3.0,
    };
    let row = |name: &str| {
        METRICS
            .iter()
            .find(|m| m.name == name)
            .expect("metric row exists")
    };
    let names: Vec<&str> = METRICS.iter().map(|m| m.name).collect();
    assert_eq!(names, ["mean_abs_diff", "changed_fraction", "ssim"]);

    let mad = row("mean_abs_diff");
    assert!((mad.compute)(&a, &b, &t).eq(&mean_abs_diff(&a, &b)));
    assert!((mad.compute)(&a, &b, &t).eq(&12.5));
    assert!((mad.limit)(&t).eq(&1.0));
    assert_eq!(mad.bound, Bound::AtMost);
    assert_eq!((mad.line_label, mad.decimals), ("mean_abs_diff", 4));
    assert!(mad.when_identical.eq(&0.0));

    // Threshold 25 comes from the tolerances: only the 30-step pixel counts.
    let changed = row("changed_fraction");
    assert!((changed.compute)(&a, &b, &t).eq(&0.25));
    assert!((changed.limit)(&t).eq(&2.0));
    assert_eq!(changed.bound, Bound::AtMost);
    assert_eq!((changed.line_label, changed.decimals), ("changed_frac", 6));
    assert!(changed.when_identical.eq(&0.0));

    let s = row("ssim");
    assert!((s.compute)(&a, &b, &t).eq(&ssim(&a, &b)));
    assert!((s.limit)(&t).eq(&3.0));
    assert_eq!(s.bound, Bound::AtLeast);
    assert_eq!((s.line_label, s.decimals), ("ssim", 4));
    assert!(s.when_identical.eq(&1.0));
}

#[test]
fn mean_abs_diff_is_the_mean_over_every_channel() {
    let a = Image {
        width: 2,
        height: 1,
        pixels: vec![0, 10, 20, 255, 0, 5],
    };
    let b = Image {
        width: 2,
        height: 1,
        pixels: vec![3, 10, 14, 0, 1, 5],
    };
    // |0-3| + 0 + |20-14| + |255-0| + |0-1| + 0 = 265, over 6 channels.
    assert!(mean_abs_diff(&a, &b).eq(&(265.0 / 6.0)));
    assert!(mean_abs_diff(&a, &a).eq(&0.0));
}

#[test]
fn changed_fraction_counts_only_differences_strictly_above_the_threshold() {
    let base = gray(&[100, 100, 100, 100]);
    // Differences of 15, 16 (the threshold: not counted), 17, 0.
    let other = gray(&[115, 84, 117, 100]);
    assert!(changed_fraction(&base, &other, 16.0).eq(&0.25));
    // At threshold 15 the 16 and 17 steps count; the 15 step still does not.
    assert!(changed_fraction(&base, &other, 15.0).eq(&0.5));
}

#[test]
fn changed_fraction_counts_a_pixel_once_when_any_channel_moves() {
    let a = Image {
        width: 2,
        height: 1,
        pixels: vec![0, 0, 0, 0, 0, 0],
    };
    let b = Image {
        width: 2,
        height: 1,
        pixels: vec![0, 0, 50, 50, 50, 50],
    };
    assert!(changed_fraction(&a, &b, 16.0).eq(&1.0));
}

#[test]
fn ssim_of_equal_images_is_one_without_the_identical_short_circuit() {
    let a = gray(&[10, 60, 200, 30, 90]);
    assert!(ssim(&a, &a.clone()).eq(&1.0));
    // A flat image has no variance: both factors reduce to their constants.
    let flat = gray(&[77; 4]);
    assert!(ssim(&flat, &flat).eq(&1.0));
}

#[test]
fn ssim_of_an_inverted_pattern_matches_the_closed_form() {
    // Means 128 on both sides, variances 4, covariance -4.
    let a = gray(&[126, 130, 126, 130]);
    let b = gray(&[130, 126, 130, 126]);
    // The mean term is exactly 1; the structure term is (2cov + c2) / (vx + vy + c2).
    let expected = (C2 - 8.0) / (C2 + 8.0);
    assert!((ssim(&a, &b) - expected).abs() < 1e-12, "{}", ssim(&a, &b));
    // Luma is the channel mean: the same picture through unequal channels.
    let rgb = Image {
        width: 1,
        height: 1,
        pixels: vec![0, 30, 60],
    };
    let same_luma = gray(&[30]);
    assert!(ssim(&rgb, &same_luma).eq(&1.0));
}

#[test]
fn ssim_of_a_uniform_shift_is_the_mean_term_alone() {
    let a = gray(&[100, 120]);
    let b = gray(&[110, 130]);
    // Means 110 and 120: 2*mx*my = 26400, mx^2 + my^2 = 26500; the
    // structure term is exactly 1 (equal variances, covariance = variance).
    let expected = (26_400.0 + C1) / (26_500.0 + C1);
    assert!((ssim(&a, &b) - expected).abs() < 1e-12, "{}", ssim(&a, &b));
}

#[test]
fn total_converts_integer_sums_exactly_below_two_to_the_fifty_third() {
    assert!(total(0).eq(&0.0));
    assert!(total(265).eq(&265.0));
    let big = 1_u64 << 53;
    assert!(total(big).eq(&9_007_199_254_740_992.0));
    // Above 2^53 it rounds to the nearest double rather than failing.
    assert!(total(u64::MAX).eq(&18_446_744_073_709_551_616.0));
}

#[test]
fn a_precondition_displays_its_message() {
    let err = PreconditionError {
        message: "size mismatch: 1x1 vs 2x2".to_owned(),
    };
    assert_eq!(err.to_string(), "size mismatch: 1x1 vs 2x2");
    let boxed: Box<dyn std::error::Error> = Box::new(err);
    assert_eq!(boxed.to_string(), "size mismatch: 1x1 vs 2x2");
}
