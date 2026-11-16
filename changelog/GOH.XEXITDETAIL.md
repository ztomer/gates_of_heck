* A step that exits 0 after printing a `✗` line (GOH.XEXIT) is now refused with the detail that line
  introduced (GOH.XEXITDETAIL). The refusal showed only the bare `✗` lines, so a private Swift
  app's push read `✗ compiler warnings in our sources (dependency warnings are exempt):` with its list cut away, which
  hid that the file it named was a selftest's planted fixture and cost a lane a cycle. `lib/xexit.py`
  now prints each `✗` line with the lines indented under it (at most 20 per `✗`, the rest counted)
  and ends with `full log: PATH`; `goh_step`, whose log is deleted at exit, passes `--keep` so the
  path names a kept copy. The verdict (`failure_lines`, and its Rust twin) is unchanged.
