//! The house TUI for Rust (BACKLOG 4.14): `tui/stylerc`'s Kare glyphs and Rams colours, the same
//! rendering as `tui/lib.sh` and `tui/lib.py`, `NO_COLOR` and non-tty aware.
//!
//! The glyphs are constants generated from the stylerc at build time (`build.rs`), so a format
//! string names them (`format!("{OK} [gate] clean")`) and a glyph changed in the stylerc reaches
//! every line on the next build. Colour is decided where a line is PRINTED, per stream, because a
//! report string is built long before anyone knows whether it lands on a terminal.
//!
//! Consumed from `$GOH_DIR` as the shell and Python libraries are: a path dependency on this
//! crate in the checkout, never a copy and never a git pin of one.
#![deny(unsafe_code)]

use std::io::IsTerminal;

include!(concat!(env!("OUT_DIR"), "/style.rs"));

/// What a status line says, which decides its glyph, its colour and its stream.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mark {
    /// A heading or a note: `ICON_START`, grey, its text dim.
    Info,
    /// A sub-item under an info line: `ICON_STEP`, all dim.
    Step,
    /// A pass: `ICON_OK`, green.
    Ok,
    /// A failure: `ICON_ERR`, red, on stderr.
    Err,
    /// A warning: `ICON_WARN`, yellow, on stderr.
    Warn,
}

impl Mark {
    /// Every mark, for a scan that has to try each.
    pub const ALL: [Self; 5] = [Self::Info, Self::Step, Self::Ok, Self::Err, Self::Warn];

    /// The stylerc glyph.
    #[must_use]
    pub const fn glyph(self) -> &'static str {
        match self {
            Self::Info => START,
            Self::Step => STEP,
            Self::Ok => OK,
            Self::Err => ERR,
            Self::Warn => WARN,
        }
    }

    const fn colour(self) -> &'static str {
        match self {
            Self::Info => C_GRAY,
            Self::Step => C_DIM,
            Self::Ok => C_GREEN,
            Self::Err => C_RED,
            Self::Warn => C_YELLOW,
        }
    }

    /// Whether the house prints this mark on stderr: an error and a warning are diagnostics.
    #[must_use]
    pub const fn to_stderr(self) -> bool {
        matches!(self, Self::Err | Self::Warn)
    }
}

/// Which stream a line is going to.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Stream {
    Out,
    Err,
}

/// Whether colour may be written: a terminal, and `NO_COLOR` unset or empty (no-color.org: ANY
/// non-empty value disables it, `NO_COLOR=0` included, as the shell and Python halves read it).
#[must_use]
pub fn colour_allowed(is_terminal: bool, no_color: Option<&std::ffi::OsStr>) -> bool {
    is_terminal && no_color.is_none_or(std::ffi::OsStr::is_empty)
}

fn colour_on(stream: Stream) -> bool {
    let tty = match stream {
        Stream::Out => std::io::stdout().is_terminal(),
        Stream::Err => std::io::stderr().is_terminal(),
    };
    colour_allowed(tty, std::env::var_os("NO_COLOR").as_deref())
}

/// One status line as the house renders it; plain when `colour` is false.
#[must_use]
pub fn render(mark: Mark, message: &str, colour: bool) -> String {
    let glyph = mark.glyph();
    if !colour {
        return format!("{glyph} {message}");
    }
    let c = mark.colour();
    match mark {
        Mark::Info => format!("{c}{glyph}{C_RESET} {C_DIM}{message}{C_RESET}"),
        Mark::Step => format!("{c}{glyph} {message}{C_RESET}"),
        Mark::Ok | Mark::Err | Mark::Warn => format!("{c}{glyph}{C_RESET} {message}"),
    }
}

/// The mark a built line opens with, and the rest of it: a report assembled with the glyph
/// constants is coloured at the print point by reading its glyph back.
#[must_use]
pub fn split_mark(line: &str) -> Option<(Mark, &str)> {
    Mark::ALL.into_iter().find_map(|m| {
        line.strip_prefix(m.glyph())
            .and_then(|rest| rest.strip_prefix(' '))
            .map(|rest| (m, rest))
    })
}

/// Every line of `text` rendered for `stream`: a line that opens with a glyph takes that mark's
/// colour, any other line passes through.
#[must_use]
pub fn render_text(text: &str, stream: Stream) -> String {
    let colour = colour_on(stream);
    if !colour {
        return text.to_owned();
    }
    let mut out = String::with_capacity(text.len());
    for piece in text.split_inclusive('\n') {
        let (line, end) = piece.strip_suffix('\n').map_or((piece, ""), |l| (l, "\n"));
        match split_mark(line) {
            Some((mark, rest)) => out.push_str(&render(mark, rest, true)),
            None => out.push_str(line),
        }
        out.push_str(end);
    }
    out
}

/// Print one status line on the stream the house uses for its mark.
pub fn say(mark: Mark, message: &str) {
    if mark.to_stderr() {
        eprintln!("{}", render(mark, message, colour_on(Stream::Err)));
    } else {
        println!("{}", render(mark, message, colour_on(Stream::Out)));
    }
}

/// Print already-built report text to stdout, each glyph-led line coloured.
pub fn out(text: &str) {
    print!("{}", render_text(text, Stream::Out));
}

/// Print already-built report text to stderr, each glyph-led line coloured.
pub fn eout(text: &str) {
    eprint!("{}", render_text(text, Stream::Err));
}

/// Print one built line and a newline to stdout, coloured by the glyph it opens with.
pub fn outln(line: &str) {
    println!("{}", render_text(line, Stream::Out));
}

/// Print one built line and a newline to stderr, coloured by the glyph it opens with.
pub fn eoutln(line: &str) {
    eprintln!("{}", render_text(line, Stream::Err));
}

/// `println!`'s shape through the house colouring: `goh_tui::outln!("{OK} [x] done")`. A call
/// site formats in place, as `println!` does, instead of building a `String` to hand over.
#[macro_export]
macro_rules! outln {
    ($($arg:tt)*) => { $crate::outln(&format!($($arg)*)) };
}

/// `eprintln!`'s shape through the house colouring.
#[macro_export]
macro_rules! eoutln {
    ($($arg:tt)*) => { $crate::eoutln(&format!($($arg)*)) };
}

/// `print!`'s shape through the house colouring.
#[macro_export]
macro_rules! out {
    ($($arg:tt)*) => { $crate::out(&format!($($arg)*)) };
}

/// `eprint!`'s shape through the house colouring.
#[macro_export]
macro_rules! eout {
    ($($arg:tt)*) => { $crate::eout(&format!($($arg)*)) };
}

pub fn info(message: &str) {
    say(Mark::Info, message);
}

pub fn step(message: &str) {
    say(Mark::Step, message);
}

pub fn ok(message: &str) {
    say(Mark::Ok, message);
}

pub fn err(message: &str) {
    say(Mark::Err, message);
}

pub fn warn(message: &str) {
    say(Mark::Warn, message);
}

#[cfg(test)]
mod tests;
