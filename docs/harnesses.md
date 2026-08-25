# The screen-presentation harness: two halves, one invariant

Nothing may render to the user's display from tests or unattended runs.
One invariant, enforced from two directions that do not subsume each other:

## Static half — `checks/check_no_screen_presentation.py`

Reads TEST TARGET SOURCES (paths or `--scope` glob) for per-language
presentation APIs (.swift / .m/.mm / .py pattern sets; see the module
docstring). It sees what a test ASKS for. Exit 1 names every
`file:line: API`. Escape hatch: a `screen-ok: <reason>` marker on the
offending line or above.

Blind spot, stated: it cannot see a change that makes an offscreen path
create a presentation surface anyway. That direction is the runtime half's.

## Runtime half — `lib/headless_env.sh`

Source it; it publishes the `GOH_HEADLESS=1` contract:

- `headless_env` — emits `GOH_HEADLESS=1` assignments for child launches:
  `env $(headless_env) ./bin/App --render-one`.
- `headless_enforced` — true when the policy is on (truthy values only;
  `"" 0 false no off` mean off).
- `headless_require_live <name>` — the LIVE-tier declaration. Refuses by
  EXITING 3 while the policy is enforced — distinct from 1 so a runner can
  tell "refused, wrong tier" from "ran and failed". Attended runs opt out
  with `env -u GOH_HEADLESS`.

GUI-facing programs should refuse under `GOH_HEADLESS=1` themselves; this
library is how harnesses forward the policy and declare their tier.

## Wiring

Both halves are opt-in until a third repo adopts them (layer-3 to layer-1
graduation). A repo opts in by adding to its gate script:

    goh_step "no screen presentation" \
        python3 "$CHECKS/check_no_screen_presentation.py" --scope 'tests/*'

and sourcing `$GOH_DIR/lib/headless_env.sh` in any live-tier harness.

# Pixel diff core — `lib/golden_core.py`

The arithmetic half of every golden harness, shared so ten repos cannot drift
into ten slightly-different diffs. It owns ONLY comparison: load two images,
compute metrics, verdict against explicit tolerances. Rendering, baselines and
blessing stay repo policy — there is deliberately NO `--update` here
(ZeroThunder blesses via `tests/e2e/golden.py --update`, ZoneTilerWM via
`ui_regression_sweep.py --record`; what to bless and when needs review is a
per-repo decision).

    python3 lib/golden_core.py A B [--tolerances '{"ssim_min": 0}'] [--json]

Exit codes: 0 within tolerance · 1 exceeded · 2 precondition (unreadable
image, size mismatch, malformed tolerances JSON). `--json` emits one object
(`metrics`, `failures`, `ok`, `tier`) for harness embedding; `tier` names which
decoder/compute path ran.

Metrics and tolerance defaults are inherited from the ancestors and stated in
the module docstring with their provenance (changed-pixel channel threshold 16
← ZoneTilerWM; SSIM floor 0.985 and diff tolerances ← ZeroThunder). Defaults
are STARTING POINTS: a repo calibrates on its own measured noise floor before
trusting a gate. Dependency posture degrades in two tiers — Pillow if
importable else a minimal built-in PNG decoder; numpy if importable else
pure-Python math with identical semantics — and the tests exercise both tiers
by monkeypatching, so no environment juggling.

# Offscreen render cookbook

The contract every offscreen/golden harness should honor, distilled from the
member repos that paid for these lessons. Flags first, then the recorded
findings — each with the repo that earned it.

## The shared flags contract

| Flag / pin            | Contract                                                        |
|-----------------------|-----------------------------------------------------------------|
| `--golden-out <path>` | Deterministic offscreen render mode; writing this flag also     |
|                       | declares the run offscreen-for-presentation (ZeroThunder's      |
|                       | `DisplayPolicy.offscreenRenderFlag`).                           |
| `--seed N`            | All subsystem RNG seeded from one declared seed; a render is    |
|                       | pixel-deterministic across runs and processes (ZeroThunder      |
|                       | goldens measure ~0 noise after the GlobalRNG migration).        |
| Fixed clock           | Pin the wall clock (`--now <epoch>`); unpinned, goldens are     |
|                       | only valid for the minute they were written (Koffee             |
|                       | `RenderMode` Clock.withPinnedTime; cross-minute runs flaked).   |
| Appearance pinned,    | Pin BOTH light and dark, every render: scheme environment AND   |
| both modes            | `NSApp.appearance`/`NSAppearance.currentDrawingAppearance`. An  |
|                       | unpinned system appearance diverged a committed dark golden by  |
|                       | 86% with zero code change (Koffee, measured 2026-08-22).        |
| Warm host reuse       | One offscreen host window + hosting view, created once, reused; |
|                       | fresh-per-render hosts leak auxiliary AppKit windows into       |
|                       | `NSApp.windows` forever (Koffee measured 8 → ~700 in a suite).  |
| Blank-frame detector  | A frame must PROVE it is not blank (distinct-colour count on a  |
| as assertion          | coarse grid) before comparison; two identical blanks "converge" |
|                       | perfectly. Never a warning — an unsettled render fails the test |
|                       | outright instead of feeding a plausible-looking artifact to the |
|                       | diff (Koffee `isSettled` + `lastRenderDidNotSettle`).           |
| Headless env          | Live-tier harnesses source `$GOH_DIR/lib/headless_env.sh` and   |
|                       | forward `GOH_HEADLESS=1` to children (`env $(headless_env)`),   |
|                       | declare their tier with `headless_require_live` (exit 3 =       |
|                       | refused, wrong tier). The app-side twin of ZT_NO_SCREEN.        |

## Hard-won lessons (cite the repo when you rely on one)

1. **ImageRenderer blanks AppKit-backed views** (Koffee,
   `App/RenderMode+Capture.swift`, measured 2026-08-23). SwiftUI's
   `ImageRenderer` walks its own display list and cannot draw views backed by
   AppKit; it substitutes the unsupported-view placeholder — a yellow box with
   a red prohibition sign. Every settings golden ever committed carried one
   where each `DatePicker`/`Stepper`/`Picker` should be, green throughout,
   defending nothing: differential blindness in its purest form. Render through
   a REAL window (`cacheDisplay(in:to:)`) so AppKit itself draws.
2. **Goldens must render at real scale on BOTH light and dark backdrops**
   (house user-POV rule; Koffee supplies the measured mechanism). Real scale =
   pinned backing scale (Koffee pins 2.0 — inheriting the monitor's scale makes
   a baseline depend on the hardware that wrote it). Both appearances because
   appearance leaks through at least three independent inputs (scheme
   environment, current drawing appearance, app appearance) and each was
   caught by a different failure; a gate over one mode misses what only the
   other mode shows.
3. **Key-ness and emphasis are questions the window answers, not grants**
   (Koffee `OffscreenRenderWindow`). Ordering a window front to get emphasized
   controls composited real surfaces hundreds of times per suite and coupled
   renders to display load; overriding `isKeyWindow`/`canBecomeKey` gets the
   drawing state with nothing on screen. Also refuse `order(_:)` in the window
   itself — a gate beats a promise.
4. **Converge, don't count** (Koffee `renderPinned`). Discarding a FIXED number
   of early passes holds only while settling takes exactly that many; render
   until bytes stop changing AND the blank-frame check passes, then fail loudly
   if attempts run out.
5. **Change detector vs golden gate is a deliberate choice** (ZoneTilerWM
   `tools/ui_regression_sweep.py`). Surfaces that legitimately differ per run
   must NOT be hard-gated — the gate fails constantly and gets switched off,
   which is worse than no gate. Print magnitudes for a human where a tolerance
   cannot be honestly set.
6. **Content-relative metrics for content-sparse frames** (ZeroThunder
   `golden.py`). A mostly-background frame makes whole-frame fractions
   insensitive; diff over the union of non-background pixels and check the
   content-pixel COUNT is stable too. Tolerances come from the measured noise
   floor × margin, calibrated per scene — and a golden must demonstrably SEE
   what it pins (ZT proved its window-timeline scene by rendering it with the
   timeline stripped: 11,831 px moved).
7. **Two-tier screen policy** (ZeroThunder `DisplayPolicy.swift`). Offscreen-
   FOR-PRESENTATION (never a pixel on the user's display) is distinct from
   determinism mode; env-var forces the policy down a process tree, a flag lets
   attended runs opt back out, and every suppression is counted with its reason
   so tests can assert on the runtime evidence. gates_of_heck's halves:
   `check_no_screen_presentation.py` (static) + `GOH_HEADLESS` (runtime).
