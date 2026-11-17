Shared fixture set for the Python prose mask (GOH.SCREENDOCMASK). A file named `*_prose.py`
names `pyautogui` only in prose; a file named `*_code.py` names it in code; a `.py.in` must not tokenize and is judged without `.in`. Both scanners
(`checks/_prose_mask.py` behind `check_display_seam.py`, and `goh screen`'s Rust mask in
`crates/goh/src/pylex`) must judge every file the same way: `tests/test_prose_mask_parity.py`.
