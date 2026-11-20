- **CAD layer: a filament override the slice did not use fails the build** (GOH.CADOVERRIDE). `checks/cad_bambu.py`
  reads every `filament_overrides` key back from the G-code header and names the value the slicer used instead. ZSUS
  compensates PLA shrinkage with `filament_shrink` 99.4 %; dropped, its 356 mm bezel prints 2 mm short.
