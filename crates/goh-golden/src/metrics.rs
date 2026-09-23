//! The metrics and the tolerances they are held to, as TABLES.
//!
//! One row per metric and one per tolerance: the verdict, the JSON, the
//! human line and the precondition messages all loop over these, so a new
//! metric is a new row plus its function, never a new branch in four
//! places. Names and defaults are the reference's (`lib/golden_core.py`,
//! `TOLERANCE_DEFAULTS`), spelled once here.

use crate::numeric::{count, mean, total};
use crate::{Image, CHANNELS};

/// SSIM's stability constants (Wang et al., 2004) and the dynamic range of
/// an 8-bit channel, exactly as the reference spells them:
/// `c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2`.
const SSIM_K1: f64 = 0.01;
const SSIM_K2: f64 = 0.03;
const DYNAMIC_RANGE: f64 = 255.0;
/// The `2` in SSIM's `2 * mx * my` and `2 * cov`.
const SSIM_CROSS_WEIGHT: f64 = 2.0;

/// Which side of its tolerance a metric must stay on.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Bound {
    /// Fails when the metric exceeds the tolerance.
    AtMost,
    /// Fails when the metric falls below the tolerance.
    AtLeast,
}

impl Bound {
    /// Whether `value` breaks `limit`.
    #[must_use]
    pub fn broken(self, value: f64, limit: f64) -> bool {
        match self {
            Self::AtMost => value > limit,
            Self::AtLeast => value < limit,
        }
    }

    /// The comparison a failure line prints, as the reference does.
    #[must_use]
    pub const fn symbol(self) -> &'static str {
        match self {
            Self::AtMost => ">",
            Self::AtLeast => "<",
        }
    }
}

/// The tolerance keys, spelled once.
pub const MEAN_ABS_DIFF_MAX: &str = "mean_abs_diff_max";
pub const CHANGED_FRAC_MAX: &str = "changed_frac_max";
pub const CHANGED_PX_THRESHOLD: &str = "changed_px_threshold";
pub const SSIM_MIN: &str = "ssim_min";

/// One tolerance: its key, its documented starting value (to be
/// calibrated per repo on its own noise floor, never tuned here), and its
/// place in [`Tolerances`].
pub struct ToleranceSpec {
    pub key: &'static str,
    pub default: f64,
    pub get: fn(&Tolerances) -> f64,
    pub set: fn(&mut Tolerances, f64),
}

/// Every tolerance, in the reference's order.
pub const TOLERANCES: &[ToleranceSpec] = &[
    ToleranceSpec {
        key: MEAN_ABS_DIFF_MAX,
        default: 8.0,
        get: |t| t.mean_abs_diff_max,
        set: |t, v| t.mean_abs_diff_max = v,
    },
    ToleranceSpec {
        key: CHANGED_FRAC_MAX,
        default: 0.08,
        get: |t| t.changed_frac_max,
        set: |t, v| t.changed_frac_max = v,
    },
    ToleranceSpec {
        key: CHANGED_PX_THRESHOLD,
        default: 16.0,
        get: |t| t.changed_px_threshold,
        set: |t, v| t.changed_px_threshold = v,
    },
    ToleranceSpec {
        key: SSIM_MIN,
        default: 0.985,
        get: |t| t.ssim_min,
        set: |t, v| t.ssim_min = v,
    },
];

/// Resolved tolerances. Typed fields, so a metric reads its limit without a
/// string lookup that could miss; the table above is the only place the
/// fields are named as strings.
#[derive(Debug, Clone, PartialEq)]
pub struct Tolerances {
    pub mean_abs_diff_max: f64,
    pub changed_frac_max: f64,
    pub changed_px_threshold: f64,
    pub ssim_min: f64,
}

impl Default for Tolerances {
    fn default() -> Self {
        let mut out = Self {
            mean_abs_diff_max: 0.0,
            changed_frac_max: 0.0,
            changed_px_threshold: 0.0,
            ssim_min: 0.0,
        };
        for spec in TOLERANCES {
            (spec.set)(&mut out, spec.default);
        }
        out
    }
}

/// One metric: how it is named in JSON and on the human line, how it is
/// computed, what it reads for byte-identical inputs, and which tolerance
/// bounds it on which side.
pub struct MetricSpec {
    pub name: &'static str,
    pub line_label: &'static str,
    pub decimals: usize,
    pub when_identical: f64,
    pub bound: Bound,
    pub limit: fn(&Tolerances) -> f64,
    pub compute: fn(&Image, &Image, &Tolerances) -> f64,
}

/// Every metric, in the reference's order (JSON, failures and the line).
pub const METRICS: &[MetricSpec] = &[
    MetricSpec {
        name: "mean_abs_diff",
        line_label: "mean_abs_diff",
        decimals: 4,
        when_identical: 0.0,
        bound: Bound::AtMost,
        limit: |t| t.mean_abs_diff_max,
        compute: |a, b, _| mean_abs_diff(a, b),
    },
    MetricSpec {
        name: "changed_fraction",
        line_label: "changed_frac",
        decimals: 6,
        when_identical: 0.0,
        bound: Bound::AtMost,
        limit: |t| t.changed_frac_max,
        compute: |a, b, t| changed_fraction(a, b, t.changed_px_threshold),
    },
    MetricSpec {
        name: "ssim",
        line_label: "ssim",
        decimals: 4,
        when_identical: 1.0,
        bound: Bound::AtLeast,
        limit: |t| t.ssim_min,
        compute: |a, b, _| ssim(a, b),
    },
];

/// Mean absolute per-channel difference, 0–255. An integer sum below 2^53,
/// so exact in any order — numpy's `abs(a - b).mean()` to the bit.
#[must_use]
pub fn mean_abs_diff(a: &Image, b: &Image) -> f64 {
    let sum: u64 = a
        .pixels
        .iter()
        .zip(&b.pixels)
        .map(|(x, y)| u64::from(x.abs_diff(*y)))
        .sum();
    total(sum) / count(a.pixels.len())
}

/// Fraction of pixels where ANY channel differs by more than `threshold`.
#[must_use]
pub fn changed_fraction(a: &Image, b: &Image, threshold: f64) -> f64 {
    let changed = a
        .pixels
        .chunks_exact(CHANNELS)
        .zip(b.pixels.chunks_exact(CHANNELS))
        .filter(|(p, q)| {
            p.iter()
                .zip(q.iter())
                .any(|(x, y)| f64::from(x.abs_diff(*y)) > threshold)
        })
        .count();
    count(changed) / count(a.pixels.len() / CHANNELS)
}

/// Per-pixel channel mean. Exact: the numerator is an integer ≤ 765, and
/// numpy's `mean(axis=1)` divides the same exact sum by the same count.
fn luma(pixels: &[u8]) -> Vec<f64> {
    let channels = count(CHANNELS);
    pixels
        .chunks_exact(CHANNELS)
        .map(|px| px.iter().map(|c| f64::from(*c)).fold(0.0, |acc, v| acc + v) / channels)
        .collect()
}

/// Global SSIM on luma, in the reference's arithmetic order.
///
/// Every product is bound before it is added: a fused multiply-add rounds
/// once where numpy and Python round twice, and the parity pin is
/// bit-exact.
#[must_use]
pub fn ssim(a: &Image, b: &Image) -> f64 {
    let x = luma(&a.pixels);
    let y = luma(&b.pixels);
    let mx = mean(&x);
    let my = mean(&y);
    let vx = mean(&x.iter().map(|v| (v - mx) * (v - mx)).collect::<Vec<_>>());
    let vy = mean(&y.iter().map(|v| (v - my) * (v - my)).collect::<Vec<_>>());
    let cov = mean(
        &x.iter()
            .zip(&y)
            .map(|(xi, yi)| (xi - mx) * (yi - my))
            .collect::<Vec<_>>(),
    );
    let k1_range = SSIM_K1 * DYNAMIC_RANGE;
    let k2_range = SSIM_K2 * DYNAMIC_RANGE;
    let c1 = k1_range * k1_range;
    let c2 = k2_range * k2_range;
    let cross_means = SSIM_CROSS_WEIGHT * mx * my;
    let cross_cov = SSIM_CROSS_WEIGHT * cov;
    let squared_means = [mx * mx, my * my];
    let numerator = (cross_means + c1) * (cross_cov + c2);
    let denominator = (squared_means[0] + squared_means[1] + c1) * (vx + vy + c2);
    numerator / denominator
}
