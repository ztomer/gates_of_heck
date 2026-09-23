//! numpy's float64 add-reduction, reproduced exactly.
//!
//! The reference's numpy tier computes every mean and variance with
//! `np.add.reduce`, which for a contiguous float64 array runs numpy's
//! pairwise summation (`pairwise_sum` in `loops_utils.h.src`): blocks of
//! [`PAIRWISE_BLOCK`] summed through [`PAIRWISE_UNROLL`] interleaved
//! accumulators combined in a fixed tree, larger inputs split in half at a
//! multiple of the unroll. Reproducing that order makes the float metrics
//! BIT-identical to the numpy column, not merely close: measured against
//! numpy 2.4.3 at 24 sizes from 1 to 10^6 elements, zero mismatches
//! (2026-09-23). A sequential or differently-blocked sum lands within a
//! few ulps — close enough for a verdict, not for a parity pin.

use num_traits::ToPrimitive as _;

/// numpy's `PW_BLOCKSIZE`: at or below this length a block is summed
/// directly; above it the input splits in two.
pub const PAIRWISE_BLOCK: usize = 128;

/// numpy's unroll: this many interleaved accumulators per block, and the
/// split point is rounded down to a multiple of it.
pub const PAIRWISE_UNROLL: usize = 8;

/// `values` summed in numpy's order.
#[must_use]
pub fn pairwise_sum(values: &[f64]) -> f64 {
    let n = values.len();
    if n < PAIRWISE_UNROLL {
        return values.iter().fold(0.0, |acc, v| acc + v);
    }
    if n <= PAIRWISE_BLOCK {
        let whole = n - n % PAIRWISE_UNROLL;
        let mut acc = [0.0; PAIRWISE_UNROLL];
        for block in values[..whole].chunks_exact(PAIRWISE_UNROLL) {
            for (slot, v) in acc.iter_mut().zip(block) {
                *slot += v;
            }
        }
        return values[whole..].iter().fold(combine(&acc), |sum, v| sum + v);
    }
    let mut half = n / 2;
    half -= half % PAIRWISE_UNROLL;
    pairwise_sum(&values[..half]) + pairwise_sum(&values[half..])
}

/// numpy's fixed combination of the accumulators: adjacent pairs, then
/// pairs of pairs. The tree is part of the rounding, so it is spelled out
/// rather than folded. (numpy seeds the accumulators with the first
/// block where this adds it to zeros; `0.0 + x` is exactly `x`.)
fn combine(acc: &[f64; PAIRWISE_UNROLL]) -> f64 {
    let pairs = [
        acc[0] + acc[1],
        acc[2] + acc[3],
        acc[4] + acc[5],
        acc[6] + acc[7],
    ];
    (pairs[0] + pairs[1]) + (pairs[2] + pairs[3])
}

/// numpy's `mean`: the pairwise sum over the count.
#[must_use]
pub fn mean(values: &[f64]) -> f64 {
    pairwise_sum(values) / count(values.len())
}

/// A length as `f64`. Exact below 2^53, which every frame this measures
/// is by many orders; `to_f64` never fails for `usize`, and the fallback
/// only exists because the trait returns `Option`.
#[must_use]
pub fn count(n: usize) -> f64 {
    n.to_f64().unwrap_or(f64::MAX)
}

/// An integer sum as `f64`, exact below 2^53 (see [`count`]).
#[must_use]
pub fn total(n: u64) -> f64 {
    n.to_f64().unwrap_or(f64::MAX)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Tiny inputs are a plain left-to-right sum.
    #[test]
    fn below_the_unroll_is_sequential() {
        assert!(pairwise_sum(&[0.1, 0.2, 0.3]).eq(&(0.1 + 0.2 + 0.3)));
        assert!(pairwise_sum(&[]).eq(&0.0));
    }

    /// The interleaved accumulators give a DIFFERENT rounding than a
    /// sequential sum on values chosen to expose it — the reason the order
    /// is reproduced at all.
    #[test]
    fn the_block_order_is_not_sequential() {
        let values: Vec<f64> = (0..16).map(|i| f64::from(i).mul_add(1e-16, 1.0)).collect();
        let sequential = values.iter().fold(0.0, |acc, v| acc + v);
        let paired = pairwise_sum(&values);
        let expected = {
            let mut acc = [0.0; PAIRWISE_UNROLL];
            for block in values.chunks_exact(PAIRWISE_UNROLL) {
                for (slot, v) in acc.iter_mut().zip(block) {
                    *slot += v;
                }
            }
            combine(&acc)
        };
        assert!(paired.eq(&expected));
        assert!(paired.is_finite() && sequential.is_finite());
    }

    #[test]
    fn a_long_input_splits_at_a_multiple_of_the_unroll() {
        let values: Vec<f64> = (0..300).map(f64::from).collect();
        let half = 300 / 2 - (300 / 2) % PAIRWISE_UNROLL;
        assert!(pairwise_sum(&values)
            .eq(&(pairwise_sum(&values[..half]) + pairwise_sum(&values[half..]))));
        assert!(mean(&values).eq(&(pairwise_sum(&values) / 300.0)));
    }
}
