* **`gates/cad_gate.sh` is executable:** it landed 100644, so a consumer's `gate.sh`, which runs it as a command,
  refused the push ("Permission denied") while every goh test, running gates through `bash`, passed.
  `tests/test_gate_entry_executable.py` holds every gate the `install.sh` template runs to 100755 in git.
