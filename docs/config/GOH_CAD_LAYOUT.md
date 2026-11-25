# GOH_CAD_LAYOUT
Area: cad
Kind: setting
Order: 10
Default: unset (the gate refuses, exit 2)

The repo's layout JSON: its plates, objects (STL, plate, centre, settings, `shells`, support
regions), the slicer profiles and what the slice must show. Every key is listed in
`checks/cad_bambu.py`'s docstring. The mesh, strain and slicer steps all read it.
