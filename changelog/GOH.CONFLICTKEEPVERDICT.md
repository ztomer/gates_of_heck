* A land that goes red only on a stack conflict keeps its green pre-step verdict for its item's
  next enqueue (`<queue>/pre-carry/<item>`, read once, reaped after a day). After a clean rebase
  the re-enqueue's pre-step asks `lib/pre_reuse.py` first, as the lander does: a base move no test
  reads runs nothing again, one a test reads runs only those readers, anything else the whole set.
  Before, the conflict red discarded the verdict and the re-enqueue ran the whole pre-step again
  (16-20 min each at 2026-11-22's load).
