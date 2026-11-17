* `no_display_seam` reads code, not prose, in Python and shell: docstrings, block-string statements,
  comments and quoted-heredoc bodies are blanked (new `checks/_prose_mask.py`, line count kept 1:1)
  before the live-screen and `app_launch` patterns run, so ZeroThunder's `tools/publish_bundle.py`
  docstring "exec Contents/MacOS/ZeroThunder" passes while the same words in a `subprocess.run`
  argument, a shell line or an unquoted heredoc still go red. The shell guard must now appear in
  code too: a comment naming `GOH_HEADLESS` no longer exempts a script (GOH.DISPLAYSEAMDOC).
