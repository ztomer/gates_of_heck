# GOH_LOAD_DIR
Area: runtime
Kind: setting
Order: 20
Default: `~/.cache/goh/load`

Where the load registry lives: `tools/load.py` writes it there, and the launchd backstop
(`com.ztomer.goh-load-reap`, every 60 s) reads it and kills the spinners whose owner is gone. It
also holds the release pipes a spinner waits on and `.registry.lock`, the lock every mutation of the
registry takes (the tool and the backstop are two writers), so the directory is the load
experiment's whole state. Point it elsewhere only to isolate a test.
