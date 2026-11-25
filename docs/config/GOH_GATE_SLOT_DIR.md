# GOH_GATE_SLOT_DIR
Area: pushes
Kind: setting
Order: 470
Default: `~/.cache/goh/slots`

The slot files: `slot-<i>`, each an exclusive flock(2) held by a `lib/gate_slot.py hold` process
that ends with the gate's shell (and is TERMed by its cleanup), so a SIGKILLed gate frees its slot
through the kernel and a daemon the gate started cannot inherit it. Never removed (a removed flock
file hands the next caller a fresh, unlocked inode). Machine-wide on purpose; set it only to isolate
a test.
