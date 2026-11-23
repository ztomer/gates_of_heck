* A ship's bisect no longer fails a ship on a flake. The failing tests of a red suite are read at
  any indent (tools/pytest.sh prints pytest's `FAILED` summary indented, so the v0.150.0 ship's
  bisect saw none, ran two full suites and blamed the tip it never ran), ERROR nodes included.
  Before a culprit is named its failing tests run again at it and at its parent land (a floor red
  that names no test re-runs the full suite at a culprit no probe saw red); one green there is
  recorded as a flake (`bisect.flake`, no fault filed), and the ship runs its suite step once more.
