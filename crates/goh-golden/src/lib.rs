//! Pixel-comparison core for golden-image harnesses — the Rust port of
//! `lib/golden_core.py` (metrics and verdict only: no rendering, no
//! baselines, no blessing).
//!
//! Three metrics, one verdict against explicit tolerances, each a row of a
//! table (`metrics::METRICS`, `metrics::TOLERANCES`) so a new metric is a
//! new row:
//!
//! - `mean_abs_diff`: mean absolute per-channel difference, 0–255 scale.
//! - `changed_fraction`: fraction of pixels where ANY channel differs by
//!   more than `changed_px_threshold`.
//! - `ssim`: global structural similarity on luma.
//!
//! PARITY is with the NUMBERS the reference's Pillow/numpy tier produces,
//! because consumers calibrate tolerances against those numbers — and it is
//! bit-exact, not approximate: the integer metrics are exact in any order,
//! and the float reductions reproduce numpy's pairwise summation order
//! (`numeric`). `tests/test_goh_golden_parity.py` pins it.
//!
//! No library computes these: `dssim` and `image-compare` measure other
//! quantities (multi-scale SSIM in L*a*b*; windowed MSSIM per channel), so
//! adopting either would silently re-baseline every calibrated tolerance.
//! Decoding IS a library (`png`); only the definitions are ported.

mod decode;
pub mod metrics;
pub mod numeric;
mod verdict;

pub use decode::load_rgb;
pub use metrics::{Tolerances, METRICS, TOLERANCES};
pub use verdict::{
    compare, python_repr, resolve_tolerances, verdict_json, verdict_line, Verdict, COMPUTE_TIER,
    DECODER_TIER,
};

/// Channels per pixel once decoded: every input becomes RGB.
pub const CHANNELS: usize = 3;

/// An RGB image: row-major bytes, [`CHANNELS`] per pixel.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Image {
    pub width: u32,
    pub height: u32,
    pub pixels: Vec<u8>,
}

/// A comparison that cannot even be attempted (unreadable input, mismatched
/// sizes, malformed tolerances). Callers map it to the precondition exit.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PreconditionError {
    pub message: String,
}

impl std::fmt::Display for PreconditionError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for PreconditionError {}
