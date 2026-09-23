//! Screen-presentation vocabularies — the pattern tables ported from
//! `checks/check_no_screen_presentation.py`.
//!
//! One table per language, in reference order (first match per line
//! wins). Each entry carries WHY it reaches the screen, so findings say
//! what is wrong rather than just what matched. No matcher logic lives
//! here — `screen.rs` compiles these; the tables are data, per the core
//! rule.

/// Suppression marker: `screen-ok:` on the line or the one above.
pub const ALLOW_MARKER: &str = "screen-ok:";

/// Headless-contract probe.
///
/// A Python file assigning this for its children declares its headless
/// contract; policing the children is the runtime half's job, not a
/// grep's. Mirrors `PY_GUARD` (both `GOH_HEADLESS = …` and
/// `env["GOH_HEADLESS"] = …`).
pub const PY_GUARD: &str = r"GOH_HEADLESS[^\n=]*=";

/// One presentation-API shape: its pattern, WHY it reaches the screen,
/// whether a bare mention needs an executor to count, and whether a
/// Swift type-position occurrence is exempt.
pub struct Shape {
    /// Match pattern.
    pub pattern: &'static str,
    /// Why this reaches the screen (the finding text).
    pub why: &'static str,
    /// Python only: prose naming a live command stays green unless
    /// something executes it (`subprocess`, `os.system`, …).
    pub needs_exec: bool,
    /// Swift only: a type-position occurrence annotates, not uses.
    pub type_exempt: bool,
}

/// Swift shapes, in reference order (first match per line wins).
pub const SWIFT_SHAPES: &[Shape] = &[
    Shape {
        pattern: r"\.(orderFront|orderFrontRegardless|makeKeyAndOrderFront|showWindow)\s*\(",
        why: "puts a window on the user's display",
        needs_exec: false,
        type_exempt: false,
    },
    Shape {
        pattern: r"\bNSApp\.activate\s*\(|\bNSApplication\.shared\.activate",
        why: "steals the user's focus",
        needs_exec: false,
        type_exempt: false,
    },
    Shape {
        pattern: r"\.setActivationPolicy\s*\(",
        why: "changes how the process presents to the window server",
        needs_exec: false,
        type_exempt: false,
    },
    Shape {
        pattern: r"\.runModal\s*\(",
        why: "runs a modal loop needing a live WindowServer",
        needs_exec: false,
        type_exempt: false,
    },
    Shape {
        pattern: r"\bNSScreen\b",
        why: "reads the real display's geometry",
        needs_exec: false,
        type_exempt: true,
    },
    Shape {
        pattern: r"\bCGDisplay\w*\s*\(|\bCGMainDisplayID\b",
        why: "talks to a real display",
        needs_exec: false,
        type_exempt: false,
    },
    Shape {
        pattern: r"\bscreencapture\b",
        why: "shells out to the screen capture tool",
        needs_exec: false,
        type_exempt: false,
    },
    Shape {
        pattern: r"\bAXIsProcessTrustedWithOptions\s*\(|\bCGRequestScreenCaptureAccess\s*\(",
        why: "triggers a system permission prompt that takes the user's keyboard",
        needs_exec: false,
        type_exempt: false,
    },
    Shape {
        pattern: r"\b[A-Z]\w*(?:Window|OverlayView)\s*\(",
        why: "constructs a window-server window or presentation view directly",
        needs_exec: false,
        type_exempt: false,
    },
    Shape {
        pattern: r"render:\s*\.presenting\b",
        why: "asks for the live presentation path instead of an offscreen render",
        needs_exec: false,
        type_exempt: false,
    },
    Shape {
        pattern: r"\bSCStream\b|\bSCShareableContent\b|\bSCScreenshotManager\b|\bSCContentSharing\b",
        why: "captures the real screen via ScreenCaptureKit (needs a TCC grant)",
        needs_exec: false,
        type_exempt: true,
    },
    Shape {
        pattern: r"\bCGWindowList\w*\b",
        why: "reads the real window list",
        needs_exec: false,
        type_exempt: true,
    },
    Shape {
        pattern: r"\bCAMetalLayer\b",
        why: "creates/acquires a window-server drawable surface",
        needs_exec: false,
        type_exempt: true,
    },
    Shape {
        pattern: r"\bCAMetalLayer\s*\(|\bnextDrawable\s*\(",
        why: "creates/acquires a window-server drawable surface",
        needs_exec: false,
        type_exempt: false,
    },
    Shape {
        pattern: r"\bCGEvent\w*\s*\(|\bCGWarpMouseCursorPosition\b",
        why: "posts real input to the whole machine",
        needs_exec: false,
        type_exempt: false,
    },
    Shape {
        pattern: r"\bNSCursor\b",
        why: "moves or hides the user's real cursor",
        needs_exec: false,
        type_exempt: true,
    },
    Shape {
        pattern: r"\bNSApplication\.shared\b|\bNSApp\b",
        why: "starts or queries the shared application object",
        needs_exec: false,
        type_exempt: false,
    },
];

/// Objective-C shapes, in reference order.
pub const OBJC_SHAPES: &[Shape] = &[
    Shape {
        pattern: r"\borderFront:|\bmakeKeyAndOrderFront:|\borderFrontRegardless\b",
        why: "puts a window on the user's display",
        needs_exec: false,
        type_exempt: false,
    },
    Shape {
        pattern: r"\bactivateIgnoringOtherApps\b|\bNSApp\s+activate\b",
        why: "steals the user's focus",
        needs_exec: false,
        type_exempt: false,
    },
    Shape {
        pattern: r"\brunModal\b",
        why: "runs a modal loop needing a live WindowServer",
        needs_exec: false,
        type_exempt: false,
    },
    Shape {
        pattern: r"\bCGEventPost\b|\bCGWarpMouseCursorPosition\b",
        why: "drives the user's real mouse/keyboard",
        needs_exec: false,
        type_exempt: false,
    },
    Shape {
        pattern: r"\bscreencapture\b",
        why: "grabs the real display",
        needs_exec: false,
        type_exempt: false,
    },
];

/// A `.py` violation is `pyautogui` itself (the harness IS the driver) or
/// a live-screen command that something actually executes.
pub const PYTHON_SHAPES: &[Shape] = &[
    Shape {
        pattern: r"\bpyautogui\b",
        why: "pyautogui drives the real screen and real input",
        needs_exec: false,
        type_exempt: false,
    },
    Shape {
        pattern: r#"screencapture|cliclick|osascript|System Events|tell application|\bopen\s+[^\"']*\.(app|bundle)\b"#,
        why: "launches a live-screen command",
        needs_exec: true,
        type_exempt: false,
    },
];

/// Executor probe for `needs_exec` shapes. Mirrors `PY_EXECUTES`.
pub const PY_EXECUTES: &str = r"subprocess|os\.system|Popen|check_output|check_call";

/// Member-access probe, start-anchored (`\A`) for the missing
/// start-anchored match: an occurrence FOLLOWED by `.` is a live use,
/// never a type annotation.
pub const MEMBER_ACCESS: &str = r"\A\s*\.";

/// Type-shape probes over the match prefix, mirroring the three
/// `_SWIFT_*_BEFORE` patterns (`$` is end-of-prefix in both engines —
/// prefixes never contain a newline).
pub const SWIFT_CAST_BEFORE: &str = r"\b(?:is|as)[?!]?\s*(?:\[\s*)?$";
/// Return-type shape: `-> T` at the prefix end.
pub const SWIFT_RETURN_BEFORE: &str = r"->\s*(?:\[\s*)?$";
/// Annotation shape: `: T`, `: [T]`, `: [K: T]` at the prefix end.
pub const SWIFT_ANNOT_BEFORE: &str = r":\s*(?:\[\s*(?:\w+\s*:\s*)?\s*)?$";
