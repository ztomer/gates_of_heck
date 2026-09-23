//! `load_rgb` over real PNG files of every layout the encoder can write,
//! each decoded to the RGB8 Pillow's `convert("RGB")` would produce.
//! Fixtures are written here, byte by byte, so the expected pixels are
//! the ones the test chose rather than whatever a checked-in file holds.

// Test-only crate: the fixture helpers `expect` because a fixture that
// cannot be written is a broken test, not a result.
#![cfg(test)]

use goh_golden::{load_rgb, Image};
use png::{BitDepth, ColorType};

/// One PNG to write: its layout, raw scanline data and optional chunks.
struct Fixture<'a> {
    width: u32,
    height: u32,
    color: ColorType,
    depth: BitDepth,
    data: &'a [u8],
    palette: Option<&'a [u8]>,
    trns: Option<&'a [u8]>,
}

impl<'a> Fixture<'a> {
    const fn new(width: u32, color: ColorType, depth: BitDepth, data: &'a [u8]) -> Self {
        Self {
            width,
            height: 1,
            color,
            depth,
            data,
            palette: None,
            trns: None,
        }
    }

    /// Write the fixture into `dir` and decode it back.
    fn load(&self, dir: &tempfile::TempDir) -> Image {
        let path = dir.path().join("fixture.png");
        let file = std::fs::File::create(&path).expect("create fixture");
        let mut encoder = png::Encoder::new(file, self.width, self.height);
        encoder.set_color(self.color);
        encoder.set_depth(self.depth);
        if let Some(palette) = self.palette {
            encoder.set_palette(palette.to_vec());
        }
        if let Some(trns) = self.trns {
            encoder.set_trns(trns.to_vec());
        }
        let mut writer = encoder.write_header().expect("png header");
        writer.write_image_data(self.data).expect("png data");
        writer.finish().expect("png finish");
        load_rgb(&path).expect("fixture decodes")
    }
}

fn dir() -> tempfile::TempDir {
    tempfile::tempdir().expect("tempdir")
}

#[test]
fn eight_bit_rgb_is_read_as_is() {
    let img = Fixture::new(
        2,
        ColorType::Rgb,
        BitDepth::Eight,
        &[10, 20, 30, 40, 50, 60],
    )
    .load(&dir());
    assert_eq!(
        img,
        Image {
            width: 2,
            height: 1,
            pixels: vec![10, 20, 30, 40, 50, 60],
        }
    );
}

#[test]
fn eight_bit_rgba_drops_alpha_without_compositing() {
    let img = Fixture::new(
        2,
        ColorType::Rgba,
        BitDepth::Eight,
        &[1, 2, 3, 4, 5, 6, 7, 0],
    )
    .load(&dir());
    assert_eq!(img.pixels, vec![1, 2, 3, 5, 6, 7]);
}

#[test]
fn eight_bit_gray_triplicates() {
    let img = Fixture::new(2, ColorType::Grayscale, BitDepth::Eight, &[7, 200]).load(&dir());
    assert_eq!(img.pixels, vec![7, 7, 7, 200, 200, 200]);
}

#[test]
fn eight_bit_gray_alpha_triplicates_and_drops_alpha() {
    let img = Fixture::new(
        2,
        ColorType::GrayscaleAlpha,
        BitDepth::Eight,
        &[7, 0, 9, 255],
    )
    .load(&dir());
    assert_eq!(img.pixels, vec![7, 7, 7, 9, 9, 9]);
}

#[test]
fn a_palette_png_expands_to_its_colours() {
    let palette = [255, 0, 0, 0, 255, 0, 0, 0, 255];
    let mut fixture = Fixture::new(3, ColorType::Indexed, BitDepth::Eight, &[2, 0, 1]);
    fixture.palette = Some(&palette);
    let img = fixture.load(&dir());
    assert_eq!((img.width, img.height), (3, 1));
    assert_eq!(img.pixels, vec![0, 0, 255, 255, 0, 0, 0, 255, 0]);
}

#[test]
fn a_palette_png_with_transparency_still_reads_its_colours() {
    let palette = [10, 20, 30, 40, 50, 60];
    let trns = [0, 128];
    let mut fixture = Fixture::new(2, ColorType::Indexed, BitDepth::Eight, &[1, 0]);
    fixture.palette = Some(&palette);
    fixture.trns = Some(&trns);
    assert_eq!(fixture.load(&dir()).pixels, vec![40, 50, 60, 10, 20, 30]);
}

#[test]
fn one_bit_gray_expands_to_black_and_white() {
    // Three pixels packed MSB-first: 1, 0, 1.
    let img = Fixture::new(3, ColorType::Grayscale, BitDepth::One, &[0b1010_0000]).load(&dir());
    assert_eq!(img.pixels, vec![255, 255, 255, 0, 0, 0, 255, 255, 255]);
}

#[test]
fn sixteen_bit_rgb_keeps_the_high_byte() {
    let img = Fixture::new(
        1,
        ColorType::Rgb,
        BitDepth::Sixteen,
        &[0x12, 0x34, 0xAB, 0xCD, 0x00, 0xFF],
    )
    .load(&dir());
    assert_eq!(img.pixels, vec![0x12, 0xAB, 0x00]);
}

/// A tRNS chunk makes the decoder's expansion hand back gray+ALPHA, but the
/// file is still 16-bit gray, and Pillow still clamps it (measured: Pillow
/// 10.4 reads 0x0080, 0x0100, 0xFFFF with a tRNS as 128, 255, 255). The clamp
/// follows the FILE's layout, not the expanded one.
#[test]
fn sixteen_bit_gray_with_transparency_still_clamps() {
    let trns = [0x00, 0x80];
    let mut fixture = Fixture::new(
        3,
        ColorType::Grayscale,
        BitDepth::Sixteen,
        &[0x00, 0x80, 0x01, 0x00, 0xFF, 0xFF],
    );
    fixture.trns = Some(&trns);
    let img = fixture.load(&dir());
    assert_eq!(
        img.pixels,
        vec![128, 128, 128, 255, 255, 255, 255, 255, 255]
    );
}

#[test]
fn sixteen_bit_gray_clamps_rather_than_truncating() {
    let img = Fixture::new(
        3,
        ColorType::Grayscale,
        BitDepth::Sixteen,
        &[0x00, 0x80, 0x01, 0x00, 0xFF, 0xFF],
    )
    .load(&dir());
    // 0x0080 fits; 0x0100 is 255 (not the high byte, 1); 0xFFFF is 255.
    assert_eq!(
        img.pixels,
        vec![128, 128, 128, 255, 255, 255, 255, 255, 255]
    );
}

#[test]
fn an_unreadable_path_is_a_precondition() {
    let dir = dir();
    let missing = dir.path().join("absent.png");
    let err = load_rgb(&missing).expect_err("missing file");
    assert!(err.message.starts_with("cannot read "), "{err}");
    assert!(err.message.contains("absent.png"), "{err}");
}

#[test]
fn a_file_that_is_not_a_png_is_a_precondition() {
    let dir = dir();
    let path = dir.path().join("text.png");
    std::fs::write(&path, b"not a png at all").expect("write");
    let err = load_rgb(&path).expect_err("not a png");
    assert!(err.message.starts_with("cannot decode "), "{err}");
    assert!(err.message.contains("text.png"), "{err}");
}

#[test]
fn a_truncated_png_fails_while_reading_the_frame() {
    let dir = dir();
    let whole = Fixture::new(4, ColorType::Rgb, BitDepth::Eight, &[9; 12]);
    let img = whole.load(&dir);
    assert_eq!(img.pixels, vec![9; 12]);
    // Keep the header chunks, cut the image data short.
    let path = dir.path().join("fixture.png");
    let bytes = std::fs::read(&path).expect("read fixture");
    let idat = bytes
        .windows(4)
        .position(|w| w == b"IDAT")
        .expect("has IDAT");
    std::fs::write(&path, &bytes[..idat + 6]).expect("truncate");
    let err = load_rgb(&path).expect_err("truncated");
    assert!(err.message.starts_with("cannot decode "), "{err}");
}
