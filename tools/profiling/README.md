# CadGoose Profiling Tools

This folder contains scripts for profiling CadGoose performance.

## Usage

### CPU Profile (Time Profiler)
```bash
./profile_cpu.sh [PID] [DURATION_SECONDS]
```
Profiles CPU usage for specified duration (default: 30 seconds).
Uses `xctrace record` with Time Profiler template.

### Memory Profile (Allocations)
```bash
./profile_memory.sh [PID] [DURATION_SECONDS]
```
Profiles memory allocations for specified duration (default: 30 seconds).
Uses `xctrace record` with Allocations template.

### Soak Test with Profiling
```bash
./soak_profile.sh [DURATION_MINUTES]
```
Runs CadGoose for specified duration while continuously profiling.
Saves results to `soak_results_YYYYMMDD_HHMMSS.md`.

### Quick CPU Check
```bash
./quick_profile.sh [DURATION_SECONDS]
```
Simple 60-second CPU profile, saves to quick_profile.md

### One-Shot Hotspot Profile (build → launch → profile → report)
```bash
./hotspot_profile.sh [DURATION_SECONDS]
```
Fully automated: builds CadGoose (Release), launches it, waits for MCP socket,
records a `DURATION_SECONDS`-second Time Profiler trace, calls `analyze_trace.sh`
to print a ranked hotspot report, then kills the app.
Saves trace + report to `/tmp/hotspot_<timestamp>/`.
Requires `xctrace` (Xcode CLT). May need `sudo` on some macOS versions.

### Multi-Goose Stress Profile
```bash
./multi_goose_profile.sh [NUM_GEESE] [DURATION_SECONDS]
```
Same as `hotspot_profile.sh` but spawns N geese (default: 5) via the MCP socket
before profiling. Exposes O(N²) separation-force and per-goose render hotspots
that are invisible with a single goose.

### Analyze Existing Trace
```bash
./analyze_trace.sh <trace_file.trace> [output_report.txt]
```
Post-processes any existing `xctrace` `.trace` file into a ranked text hotspot
report (sorted by self-time). Uses `xctrace export` + Python3.
Saves report alongside the trace as `<trace>.hotspots.txt`.

### Trail Detection Test (Cyan-pixel)
```bash
./run_trail_test.sh
```
Full automated trail detection. Auto-re-launches in Ghostty (needs
Screen Recording permission), hides all other windows, builds,
launches CadGoose, runs v4 cyan-pixel test, restores desktop.
Exit: 0=clean, 10=trail, 11=item not visible, 97=build fail.
Output: `/tmp/trail_test_YYYYMMDD_HHMMSS/`

### Crash Debug (lldb)
```bash
./crash_debug.sh
```
Launches CadGoose under lldb, waits for crash, saves backtrace to
`/tmp/cadgoose_crash_*.txt`. Attach to running process:
```bash
./crash_debug.sh --attach <PID>
```

### Memory Watch (leaks + heap)
```bash
./mem_watch.sh
```
Launches CadGoose and monitors RSS/leaks/heap every 5 seconds.
Saves output to `/tmp/cadgoose_mem_*/`. Attach to running process:
```bash
./mem_watch.sh --attach <PID>
./mem_watch.sh --attach <PID> --quick  # single snapshot
```

## Requirements
- Xcode command line tools (`xctrace`, `leaks`, `heap`)
- CadGoose must be running (or let scripts launch it)
- `hotspot_profile.sh` / `multi_goose_profile.sh` may require `sudo` on recent macOS