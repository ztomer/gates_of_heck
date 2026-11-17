* `no_screen_presentation` (`goh screen`) reads code, not prose, in Python: comments (trailing ones
  included) and docstring/block-string statements are blanked by the shared lexer
  (`pylex::Lexed::code_only`, the Rust twin of `checks/_prose_mask.py`) before the patterns run, so
  a docstring or comment naming `pyautogui` passes while an import, call or argument string still
  fails. `tests/fixtures/prose_mask` is one fixture set both masks must judge alike
  (`tests/test_prose_mask_parity.py`) (GOH.SCREENDOCMASK).
