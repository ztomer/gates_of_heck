//! `json.dumps` as Python writes it, for the ported checkers' `--json` modes.
//!
//! A port's machine output is read by the same consumers as the reference's,
//! so it is the reference's text, not merely equal JSON: `ensure_ascii`
//! escapes, `", "` and `": "` separators, `indent=2` layout, and the key order
//! the document was built in (`serde_json`'s `preserve_order`).

use std::fmt::Write as _;

use serde_json::Value;

/// A JSON string literal, `ensure_ascii=True`.
#[must_use]
pub fn string(s: &str) -> String {
    let mut out = String::from("\"");
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if c.is_ascii() && c >= ' ' => out.push(c),
            c => {
                let mut buf = [0u16; 2];
                for unit in c.encode_utf16(&mut buf) {
                    let _ = write!(out, "\\u{unit:04x}");
                }
            }
        }
    }
    out.push('"');
    out
}

fn scalar(v: &Value) -> String {
    match v {
        Value::Null => "null".to_owned(),
        Value::Bool(b) => if *b { "true" } else { "false" }.to_owned(),
        Value::Number(n) => n.to_string(),
        Value::String(s) => string(s),
        Value::Array(_) | Value::Object(_) => String::new(),
    }
}

/// `json.dumps(v, indent=2)`.
#[must_use]
pub fn dumps_indent2(v: &Value) -> String {
    let mut out = String::new();
    write_indented(&mut out, v, 0);
    out
}

fn write_indented(out: &mut String, v: &Value, level: usize) {
    let pad = |n: usize| "  ".repeat(n);
    match v {
        Value::Array(items) if !items.is_empty() => {
            out.push_str("[\n");
            for (i, item) in items.iter().enumerate() {
                out.push_str(&pad(level + 1));
                write_indented(out, item, level + 1);
                out.push_str(if i + 1 < items.len() { ",\n" } else { "\n" });
            }
            out.push_str(&pad(level));
            out.push(']');
        }
        Value::Object(map) if !map.is_empty() => {
            out.push_str("{\n");
            for (i, (k, item)) in map.iter().enumerate() {
                out.push_str(&pad(level + 1));
                out.push_str(&string(k));
                out.push_str(": ");
                write_indented(out, item, level + 1);
                out.push_str(if i + 1 < map.len() { ",\n" } else { "\n" });
            }
            out.push_str(&pad(level));
            out.push('}');
        }
        Value::Array(_) => out.push_str("[]"),
        Value::Object(_) => out.push_str("{}"),
        _ => out.push_str(&scalar(v)),
    }
}

/// `json.dumps(v)`: one line, `", "` and `": "` separators.
#[must_use]
pub fn dumps(v: &Value) -> String {
    match v {
        Value::Array(items) => {
            format!(
                "[{}]",
                items.iter().map(dumps).collect::<Vec<_>>().join(", ")
            )
        }
        Value::Object(map) => format!(
            "{{{}}}",
            map.iter()
                .map(|(k, item)| format!("{}: {}", string(k), dumps(item)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        _ => scalar(v),
    }
}
