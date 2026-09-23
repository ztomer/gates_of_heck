//! `goh golden` as a process: the native image comparison's verdicts and
//! every precondition the command refuses on. The metrics themselves are
//! pinned by `crates/goh-golden`'s own tests; this proves the command
//! wires them (exit codes, both renderings, the refusals) and lets
//! `cargo llvm-cov` see `commands::run_golden`.

// Test-only crate: helpers `expect` fixture writes, and a fixture that
// cannot be built is a broken test, not a result.
#![cfg(test)]

use std::path::{Path, PathBuf};

use goh_testkit::{goh_at, Out};

fn goh(args: &[&str]) -> Out {
    goh_at(Path::new(env!("CARGO_BIN_EXE_goh")), None, args, &[]).expect("goh runs")
}

/// A solid `width`x`height` RGB8 PNG at `dir/name`.
fn png(dir: &Path, name: &str, width: u32, height: u32, rgb: [u8; 3]) -> String {
    let path: PathBuf = dir.join(name);
    let file = std::io::BufWriter::new(std::fs::File::create(&path).expect("create"));
    let mut encoder = png::Encoder::new(file, width, height);
    encoder.set_color(png::ColorType::Rgb);
    encoder.set_depth(png::BitDepth::Eight);
    let mut writer = encoder.write_header().expect("header");
    let pixels: Vec<u8> = std::iter::repeat_n(rgb, usize::try_from(width * height).expect("fits"))
        .flatten()
        .collect();
    writer.write_image_data(&pixels).expect("pixels");
    writer.finish().expect("finish");
    path.to_string_lossy().into_owned()
}

#[test]
fn identical_images_pass_in_both_renderings() {
    let dir = tempfile::tempdir().expect("tempdir");
    let a = png(dir.path(), "a.png", 8, 8, [10, 20, 30]);
    let b = png(dir.path(), "b.png", 8, 8, [10, 20, 30]);
    let out = goh(&["golden", &a, &b]);
    assert!(out.status.success(), "{}", out.text());
    assert!(out.stdout.contains("identical"), "{}", out.stdout);
    let out = goh(&["golden", &a, &b, "--json"]);
    assert!(out.status.success(), "{}", out.text());
    let doc: serde_json::Value = serde_json::from_str(&out.stdout).expect("--json is JSON");
    assert_eq!(doc["ok"], true, "{doc}");
    assert_eq!(doc["identical"], true, "{doc}");
    assert_eq!(doc["size"], serde_json::json!([8, 8]), "{doc}");
}

#[test]
fn a_different_image_exceeds_the_default_tolerances() {
    let dir = tempfile::tempdir().expect("tempdir");
    let a = png(dir.path(), "a.png", 8, 8, [0, 0, 0]);
    let b = png(dir.path(), "b.png", 8, 8, [255, 255, 255]);
    let out = goh(&["golden", &a, &b, "--json"]);
    assert_eq!(out.status.code(), Some(1), "{}", out.text());
    let doc: serde_json::Value = serde_json::from_str(&out.stdout).expect("--json is JSON");
    assert_eq!(doc["ok"], false, "{doc}");
    assert!(
        doc["failures"].as_array().is_some_and(|f| !f.is_empty()),
        "{doc}"
    );
}

#[test]
fn preconditions_exit_two_and_say_why() {
    let dir = tempfile::tempdir().expect("tempdir");
    let a = png(dir.path(), "a.png", 8, 8, [1, 2, 3]);
    let small = png(dir.path(), "small.png", 4, 8, [1, 2, 3]);
    let missing = dir
        .path()
        .join("missing.png")
        .to_string_lossy()
        .into_owned();
    for (args, why) in [
        (vec!["golden", a.as_str(), small.as_str()], "size mismatch"),
        (vec!["golden", a.as_str(), missing.as_str()], "cannot read"),
        (vec!["golden", missing.as_str(), a.as_str()], "cannot read"),
        (
            vec!["golden", a.as_str(), a.as_str(), "--tolerances", "{bad"],
            "not valid JSON",
        ),
        (
            vec!["golden", a.as_str(), a.as_str(), "--tolerances", "[1]"],
            "must be a JSON object",
        ),
        (
            vec![
                "golden",
                a.as_str(),
                a.as_str(),
                "--tolerances",
                "{\"nope\": 1}",
            ],
            "unknown tolerance key",
        ),
    ] {
        let out = goh(&args);
        assert_eq!(out.status.code(), Some(2), "{args:?}: {}", out.text());
        assert!(
            out.stderr.contains("precondition") && out.stderr.contains(why),
            "{args:?}: {}",
            out.stderr
        );
    }
}
