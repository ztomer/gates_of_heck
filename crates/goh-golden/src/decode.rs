//! PNG → RGB8, the way the reference's Pillow tier sees it.
//!
//! Pillow's `convert("RGB")` is the reference, measured 2026-09-23 on
//! hand-written 16-bit PNGs of every layout and a palette PNG:
//!
//! - palette and low-bit grayscale expand to their colours (the `png`
//!   crate's `EXPAND` does exactly this);
//! - 16-bit RGB, RGBA and gray+alpha keep the HIGH byte of each sample;
//! - 16-bit grayscale alone opens as `I;16` and CLAMPS to 255 — 0x0100 is
//!   255, not 1;
//! - alpha is dropped, never composited; gray triplicates.
//!
//! The first port refused palette PNGs (which Pillow opens) and took the
//! high byte of 16-bit gray (which Pillow clamps): two inputs on which
//! the tiers would have disagreed about the picture itself.

use crate::{Image, PreconditionError};

/// Bytes per 16-bit sample.
const WIDE: usize = 2;

/// Decode `path` to RGB8 as Pillow's `convert("RGB")` would.
///
/// # Errors
///
/// An unreadable file, an undecodable PNG, or a layout with no RGB
/// meaning — each a precondition, never a verdict.
pub fn load_rgb(path: &std::path::Path) -> Result<Image, PreconditionError> {
    let unreadable = |e: &dyn std::fmt::Display| PreconditionError {
        message: format!("cannot read {}: {e}", path.display()),
    };
    let undecodable = |e: &dyn std::fmt::Display| PreconditionError {
        message: format!("cannot decode {}: {e}", path.display()),
    };
    let file = std::fs::File::open(path).map_err(|e| unreadable(&e))?;
    let mut decoder = png::Decoder::new(std::io::BufReader::new(file));
    decoder.set_transformations(png::Transformations::EXPAND);
    let mut reader = decoder.read_info().map_err(|e| undecodable(&e))?;
    let mut buf = vec![0; reader.output_buffer_size().unwrap_or(0)];
    let info = reader.next_frame(&mut buf).map_err(|e| undecodable(&e))?;
    let pixels =
        to_rgb(info.color_type, info.bit_depth, &buf[..info.buffer_size()]).ok_or_else(|| {
            PreconditionError {
                message: format!(
                    "{}: {:?} at {:?} has no RGB meaning after expansion",
                    path.display(),
                    info.color_type,
                    info.bit_depth
                ),
            }
        })?;
    Ok(Image {
        width: info.width,
        height: info.height,
        pixels,
    })
}

/// One expanded frame as RGB8. After `EXPAND` only 8- and 16-bit gray,
/// gray+alpha, RGB and RGBA remain; anything else is `None` so a new
/// layout fails loudly instead of being misread.
fn to_rgb(color: png::ColorType, depth: png::BitDepth, buf: &[u8]) -> Option<Vec<u8>> {
    use png::{BitDepth, ColorType};
    let wide = depth == BitDepth::Sixteen;
    if !wide && depth != BitDepth::Eight {
        return None;
    }
    let sample = if wide { WIDE } else { 1 };
    let channels = color.samples();
    let pixels = buf.chunks_exact(channels * sample);
    // The one layout Pillow clamps rather than truncates (see module doc).
    let clamp_gray = wide && color == ColorType::Grayscale;
    let first = |px: &[u8], channel: usize| -> u8 {
        let at = channel * sample;
        if clamp_gray {
            u8::try_from(u16::from_be_bytes([px[at], px[at + 1]])).unwrap_or(u8::MAX)
        } else {
            px[at] // 8-bit sample, or the high byte of a big-endian 16-bit one
        }
    };
    match color {
        ColorType::Grayscale | ColorType::GrayscaleAlpha => Some(
            pixels
                .flat_map(|px| [first(px, 0); crate::CHANNELS])
                .collect(),
        ),
        ColorType::Rgb | ColorType::Rgba => Some(
            pixels
                .flat_map(|px| [first(px, 0), first(px, 1), first(px, 2)])
                .collect(),
        ),
        ColorType::Indexed => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use png::{BitDepth, ColorType};

    #[test]
    fn sixteen_bit_gray_clamps_and_every_other_wide_layout_keeps_the_high_byte() {
        let gray = [0x01, 0x00, 0x00, 0xFF];
        assert_eq!(
            to_rgb(ColorType::Grayscale, BitDepth::Sixteen, &gray),
            Some(vec![255, 255, 255, 255, 255, 255])
        );
        let rgb = [0x01, 0x00, 0x02, 0x00, 0x03, 0x00];
        assert_eq!(
            to_rgb(ColorType::Rgb, BitDepth::Sixteen, &rgb),
            Some(vec![1, 2, 3])
        );
        let gray_alpha = [0x01, 0x2C, 0xFF, 0xFF];
        assert_eq!(
            to_rgb(ColorType::GrayscaleAlpha, BitDepth::Sixteen, &gray_alpha),
            Some(vec![1, 1, 1])
        );
    }

    #[test]
    fn alpha_is_dropped_and_gray_triplicates() {
        assert_eq!(
            to_rgb(ColorType::Rgba, BitDepth::Eight, &[9, 8, 7, 0]),
            Some(vec![9, 8, 7])
        );
        assert_eq!(
            to_rgb(ColorType::Grayscale, BitDepth::Eight, &[42]),
            Some(vec![42, 42, 42])
        );
    }

    #[test]
    fn an_unexpanded_layout_is_refused() {
        assert_eq!(to_rgb(ColorType::Indexed, BitDepth::Eight, &[0]), None);
        assert_eq!(to_rgb(ColorType::Grayscale, BitDepth::Four, &[0]), None);
    }
}
