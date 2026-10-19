# Profiling: the trace ranker

The generic half of a profiling harness: rank any repo's Time Profiler trace. App-specific
harnesses (launch, soak, trail and crash scripts) live in their own repo; the one app harness that
sat here moved back home in v0.40.0, and `tests/test_profiling_generic.py` keeps this directory to
the generic files.

## Analyze an existing trace
```bash
./analyze_trace.sh <trace_file.trace> [output_report.txt] [--process NAME] [--under FRAME --tree N]
```
Exports the trace's `time-profile` table (the one today's `xctrace` writes) and ranks it with
`xctrace_profile.py`: by thread, by the leaf's binary, self and inclusive time; `--process` keeps
the processes whose name contains NAME (an `--all-processes` recording holds every one), `--under`
the stacks through one frame, with the tree `--tree` deep below it. Any repo's trace works.
Saves the report alongside the trace as `<trace>.hotspots.txt`. Pinned by `test_xctrace_profile.py`.

Record an app that a driver relaunches (a census) with `--all-processes`, never `--attach`: the
attached recording ends when its target exits.

## Requirements
- Xcode command line tools (`xctrace`)
