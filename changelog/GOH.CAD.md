* **The CAD layer** (`gates/cad_gate.sh`, opt-in): a repo of FDM parts made by code is judged as the slicer and the
  printer see it, from one layout file (`GOH_CAD_LAYOUT`). Every STL is read as admesh reads it (holes, pinched and
  miswound edges, inside-out bodies, zero-area triangles, zero stored normals, which Bambu Studio refuses to load, and
  each object's body count); every snap the layout names is held to its bending strain; and the Bambu Studio project is
  built and every plate sliced, failing on any slicing warning (read from result.json and the log, where the warnings
  the GUI shows over a plate live), a support request the G-code does not honour, support missing under a named region
  or far from all of them, the wrong nozzle or bed, and open-air travel over its cap. Standard library only. Moved here
  from ZSUS's builder and the `3d-print-cad` skill, which each carried a copy and both missed ZSUS v21.2.0's floating
  cantilever.
