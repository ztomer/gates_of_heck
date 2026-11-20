#!/usr/bin/env python3
"""Build a Bambu Studio project from a layout, slice every plate, and gate the OUTPUT (GOH.CAD).

    cad_bambu.py LAYOUT.json [--root DIR] [--out-dir DIR] [--bambu CLI]

The project is built by checks/cad_project.py; this slices it with the Bambu Studio CLI and fails
on:

  1. any slicing warning on any plate (result.json and the log: checks/cad_supports.py), so a
     "floating cantilever" the GUI would show stops the build;
  2. a plate whose part asks for supports (enable_support "1") with no `; FEATURE: Support` in its
     G-code: the gate counts the feature, never the setting;
  3. a part with support regions that got no support under one of them, or support more than
     `support_reach` mm (default 15) from all of them;
  4. a G-code header naming another nozzle or bed type than `expect`;
  5. more open-air travel hops than `air_travel.max_spans` on a plate holding one of
     `air_travel.objects` (checks/cad_travel.py: where PLA strings);
  6. a `filament_overrides` value the G-code header does not carry: the slicer did not use it
     (GOH.CADOVERRIDE; a dropped `filament_shrink` prints a long part 0.6 % short).

The report (plates: hours, grams, objects, warnings, open-air travel; the header keys) is written to
`report` under --out-dir. Layout keys:

  machine, process, filament   system profile names (<profiles>/<kind>/<name>.json)
  bambu, profiles              the CLI and its profiles (default: --bambu, GOH_CAD_BAMBU, else
                               the macOS app)
  expect                       {"printable_area": [...], "nozzle_diameter": [...],
                                "bed_type": "..."}
  bed, plate_gap, exclude      bed mm; plate grid gap as a fraction of it (0.2); exclusion
                               corner [x, y]
  project                      project_settings overrides; process_overrides names those of the
                               process profile (shown as changed in the GUI); filament_overrides
  presets, plates              named setting dicts; {"1": "plate name", ...}
  objects                      [{"name", "stl", "plate", "centre": [x, y], "preset", "settings",
                                 "shells": N (checks/cad_mesh.py), "support_regions": [{"why",
                                 "box": [x0, x1, y0, y1, z0, z1]}] or "support_regions_json"}]
  air_travel                   {"max_spans": N, "objects": [names]}
  support_reach, report_keys   see 3; extra G-code header keys to report
  out_3mf, report              outputs, relative to --out-dir (default the layout's directory)

Relative "stl" paths resolve against --root (default: the layout's directory). Exit codes: 0 every
plate sliced clean · 1 a gate failed (each named) · 2 the layout, a profile, an STL or the CLI is
missing or broken. Standard library only.
"""

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import contextlib as _contextlib
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    with _contextlib.suppress(ModuleNotFoundError):  # outside any checkout: nothing to re-run from
        __import__("_from_head").reexec(__file__)
    del _sys.path[0]

import argparse
import json
import os
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cad_project import LayoutError, bambu_path, build, run_cli
from cad_supports import gate_regions, slice_warnings, support_points
from cad_travel import air_travel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tui.lib import counted, err, info, ok

HEADER = ("printer_settings_id", "nozzle_diameter", "curr_bed_type", "enable_support")


def override_mismatches(gcode: str, overrides: dict) -> list[str]:
    """Each filament override the G-code header does not carry with the value the layout set."""
    fails = []
    for key, val in sorted(overrides.items()):
        want = ",".join(val) if isinstance(val, list) else str(val)
        m = re.search(rf"^; {re.escape(key)} = (.*)$", gcode, re.MULTILINE)
        if not m:
            fails.append(f"filament override {key} = {want} is not in the G-code header")
        elif (used := m.group(1).strip()) != want:
            fails.append(f"filament override {key} = {want} but the G-code used {used}")
    return fails


def _slice(lay: dict, out_3mf: str, tmp: str) -> tuple[zipfile.ZipFile, str, str]:
    sliced = os.path.join(tmp, "sliced")
    os.makedirs(sliced)
    log = os.path.join(tmp, "slice.log")
    cmd = [
        bambu_path(lay),
        "--debug",
        "2",
        "--slice",
        "0",
        "--outputdir",
        sliced,
        "--export-3mf",
        "sliced.3mf",
    ]
    rc = run_cli([*cmd, out_3mf], log)
    result = Path(sliced, "result.json")
    if rc != 0:  # say why, from the CLI's own result.json, before opening a 3MF that is not there
        why = (
            json.loads(result.read_text()).get("error_string")
            if result.exists()
            else "no result.json"
        )
        raise LayoutError(f"slice failed rc={rc}: {why} (log {log})")
    return (
        zipfile.ZipFile(os.path.join(sliced, "sliced.3mf")),
        result.read_text(),
        Path(log).read_text(errors="ignore"),
    )


def verify(
    lay: dict, out_3mf: str, tmp: str, plate_names: dict, placed: list
) -> tuple[dict, list[str]]:
    """Slice every plate; returns (report, failures)."""
    zs, result, log = _slice(lay, out_3mf, tmp)
    info_xml = zs.read("Metadata/slice_info.config").decode()
    version = re.search(r'X-BBL-Client-Version" value="([^"]+)"', info_xml)
    rep: dict = {
        "bambu_studio": version.group(1) if version else None,
        "exit_code": 0,
        "plates": [],
    }
    fails: list[str] = []
    warned = slice_warnings(result, log)
    gcode = {}
    for p in re.findall(r"<plate>.*?</plate>", info_xml, re.DOTALL):
        idx = int(re.search(r'key="index" value="(\d+)"', p).group(1))
        pred = re.search(r'key="prediction" value="(\d+)"', p)
        wt = re.search(r'key="weight" value="([\d.]+)"', p)
        warns = re.findall(r'<warning msg="([^"]+)"', p)
        warns += [w for w in warned.get(idx, []) if w not in warns]
        names = re.findall(r'<object identify_id="\d+" name="([^"]+)"', p)
        hours = int(pred.group(1)) / 3600 if pred else 0.0
        grams = float(wt.group(1)) if wt else 0.0
        gcode[idx] = zs.read(f"Metadata/plate_{idx}.gcode").decode(errors="ignore")
        row = {
            "plate": idx, "name": plate_names.get(idx, str(idx)), "hours": round(hours, 2),
            "grams": grams, "objects": names, "warnings": warns,
            "air_travel": air_travel(gcode[idx]),
        }  # fmt: skip
        rep["plates"].append(row)
        hops = row["air_travel"]["spans"]
        line = (
            f"plate {idx}: {hours:5.2f} h  {grams:6.1f} g  {', '.join(names)}  open-air hops {hops}"
        )
        (err if warns else ok)(line + (f"  WARNING {warns}" if warns else ""))
        fails += [f"plate {idx} slicer warning: {w}" for w in warns]
    if len(gcode) != len(plate_names):
        fails.append(f"{counted(len(gcode), 'plate')} sliced for {len(plate_names)} in the layout")
    for n in sorted({p["plate"] for p in placed if p["settings"].get("enable_support") == "1"}):
        k = len(re.findall(r"^; FEATURE: Support", gcode.get(n, ""), re.MULTILINE))
        rep.setdefault("support_features", {})[str(n)] = k
        if not k:
            fails.append(f"plate {n} asks for supports but its slice has none")
    reach = float(lay.get("support_reach", 15.0))
    for p in placed:
        if not p["regions"]:
            continue
        dx, dy = p["t"][0] - p["origin"][0], p["t"][1] - p["origin"][1]  # plate -> STL coordinates
        pts = [(x - dx, y - dy, z) for x, y, z in support_points(gcode.get(p["plate"], ""))]
        under, farthest, bad = gate_regions(pts, p["regions"], reach)
        rep.setdefault("support_regions", {})[p["name"]] = {
            "moves_under": under,
            "farthest_mm": farthest,
        }
        (err if bad else ok)(
            f"{p['name']}: support under each region {under}, "
            f"farthest {farthest} mm (max {reach:g})"
        )
        fails += [f"{p['name']}: {b}" for b in bad]
    gated = lay.get("air_travel", {})
    for row in rep["plates"]:
        hops = row["air_travel"]["spans"]
        if any(o in gated.get("objects", []) for o in row["objects"]) and hops > gated["max_spans"]:
            fails.append(f"plate {row['plate']}: {hops} open-air hops > {gated['max_spans']}")
    first = gcode[min(gcode)] if gcode else ""
    fails += override_mismatches(first, lay.get("filament_overrides", {}))
    for key in (*HEADER, *lay.get("report_keys", [])):
        m = re.search(rf"^; {key} = (.*)$", first, re.MULTILINE)
        rep[key] = m.group(1) if m else None
    expect = lay.get("expect", {})
    if "nozzle_diameter" in expect and rep["nozzle_diameter"] != ",".join(
        expect["nozzle_diameter"]
    ):
        fails.append(
            f"G-code nozzle {rep['nozzle_diameter']}, expected {expect['nozzle_diameter']}"
        )
    if "bed_type" in expect and rep["curr_bed_type"] != expect["bed_type"]:
        fails.append(f"G-code bed {rep['curr_bed_type']}, expected {expect['bed_type']}")
    return rep, fails


def run(lay: dict, root: str, out_dir: str) -> tuple[dict, list[str]]:
    """Build, slice and gate; writes the 3MF and the report under out_dir. Raises LayoutError."""
    out_3mf, tmp, plate_names, placed = build(lay, root, out_dir)
    info(f"wrote {out_3mf}")
    rep, fails = verify(lay, out_3mf, tmp, plate_names, placed)
    report = os.path.join(out_dir, lay.get("report", "slice_report.json"))
    os.makedirs(os.path.dirname(report) or ".", exist_ok=True)
    with open(report, "w") as fh:
        json.dump(rep, fh, indent=1)
    return rep, fails


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="cad_bambu.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("layout")
    ap.add_argument("--root")
    ap.add_argument("--out-dir")
    ap.add_argument("--bambu", help="the Bambu Studio CLI, when the layout names none")
    a = ap.parse_args(argv)
    here = os.path.dirname(os.path.abspath(a.layout))
    try:
        with open(a.layout) as fh:
            lay = json.load(fh)
        if a.bambu:
            lay.setdefault("bambu", a.bambu)
        if not os.path.exists(bambu_path(lay)):
            raise LayoutError(
                f"no Bambu Studio CLI at {bambu_path(lay)} (install it, or set GOH_CAD_BAMBU)"
            )
        rep, fails = run(lay, a.root or here, a.out_dir or here)
    except (OSError, ValueError, KeyError) as e:
        err(f"cad_bambu: {e}")
        return 2
    for f in fails:
        err(f)
    if fails:
        err(f"cad_bambu: {counted(len(fails), 'failure')} in the slice of {a.layout}")
        return 1
    ok(
        f"cad_bambu: {counted(len(rep['plates']), 'plate')} sliced: no warnings, "
        "supports where named, nozzle and bed as expected"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
