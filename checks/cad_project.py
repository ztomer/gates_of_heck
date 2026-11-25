"""Build a multi-plate Bambu Studio project (.3mf) from a layout, for checks/cad_bambu.py (GOH.CAD).

Bambu Studio's own CLI imports the STLs (so project_settings and the object meshes are its own),
then this places each object on its plate, writes its per-object settings, plate names and project
settings, and adds the support enforcers a part names (checks/cad_supports.py). Traps closed, each
met for real on the ZSUS mount:

  * The CLI does not resolve a system profile's `inherits` chain: `--load-settings <machine>.json`
    loads that file's own keys and the rest fall back to built-in defaults WITHOUT a warning (a P1S
    came out with a 200 x 200 bed and a Cool Plate). The chain is flattened here, and the layout's
    `expect` (printable_area, nozzle_diameter) is asserted on the flattened machine before slicing.
  * The CLI drops result.json into its working directory: it runs from a temp dir.
  * A per-object `enable_support` overrides the GUI's global switch, so a part that needs support
    turns it on for itself, and only there (support enforcers + tree(manual) from the plate).

Standard library only.

Imported by `cad_bambu.py`: the Bambu project build (profiles flattened and asserted against
`expect`, CLI import from a temp dir, placement, settings, plates, enforcers). `LayoutError` is the
exit-2 class. Pinned by `test_cad_gate.py`.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
import zipfile

from _gitutil import foreign_repo_env
from _goh_tmp import goh_tmp  # the ONE temp parent (GOH.FSCHURN3)
from cad_supports import add_enforcers

BBS = "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio"
ENFORCED = {
    "enable_support": "1",
    "support_type": "tree(manual)",
    "support_on_build_plate_only": "1",
}
_VERTEX = re.compile(rb'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"')


class LayoutError(ValueError):
    """The layout, its profiles or its STLs cannot make a project (exit 2, with the reason)."""


def bambu_path(lay: dict) -> str:
    return lay.get("bambu") or os.environ.get("GOH_CAD_BAMBU") or BBS


def profiles_path(lay: dict) -> str:
    if lay.get("profiles"):
        return lay["profiles"]
    app = os.path.dirname(os.path.dirname(bambu_path(lay)))  # .../BambuStudio.app/Contents
    return os.path.join(app, "Resources", "profiles", "BBL")


def flatten(profiles: str, kind: str, name: str, out: str, overrides=None) -> dict:
    """A system profile with its `inherits` chain resolved into one file the CLI reads whole."""
    chain, n = [], name
    while n:
        path = os.path.join(profiles, kind, n + ".json")
        if not os.path.exists(path):
            raise LayoutError(f"no {kind} profile {n!r} in {profiles}")
        with open(path) as fh:
            d = json.load(fh)
        chain.append(d)
        n = d.get("inherits")
    flat: dict = {}
    for d in reversed(chain):
        flat.update(d)
    flat.pop("inherits", None)
    flat.update(name=name, **{"from": "system"})
    flat.update(overrides or {})
    with open(out, "w") as fh:
        json.dump(flat, fh, indent=1)
    return flat


def run_cli(cmd: list[str], log: str) -> int:
    """The Bambu CLI with its output in `log` and its cwd beside it (result.json lands there)."""
    with open(log, "w") as fh:
        return subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=fh,
            stderr=subprocess.STDOUT,
            cwd=os.path.dirname(log),
            env=foreign_repo_env(),  # the slicer runs in a scratch dir, not the gated repo
            check=False,
        ).returncode  # judged by the caller


def _bbox(xml: bytes):
    v = [tuple(float(c) for c in m) for m in _VERTEX.findall(xml)]
    return [min(p[i] for p in v) for i in range(3)], [max(p[i] for p in v) for i in range(3)]


def object_settings(lay: dict, o: dict) -> dict:
    s = dict(lay.get("presets", {}).get(o["preset"], {})) if o.get("preset") else {}
    s.update(o.get("settings", {}))
    s.setdefault(
        "enable_support", "0"
    )  # off unless the part asks: the GUI's switch does not reach it
    return {k: str(v) for k, v in s.items()}


def object_regions(root: str, o: dict) -> list[dict]:
    if "support_regions" in o:
        return o["support_regions"]
    if "support_regions_json" in o:
        with open(os.path.join(root, o["support_regions_json"])) as fh:
            return json.load(fh).get(o["name"], [])
    return []


def build(lay: dict, root: str, out_dir: str):
    """Write the project; returns (3mf path, temp dir, plate names, [placed object dicts])."""
    root, out_dir = (
        os.path.abspath(root),
        os.path.abspath(out_dir),
    )  # the CLI runs from its own temp dir
    bed = float(lay.get("bed", 256.0))
    stride = bed * (1.0 + float(lay.get("plate_gap", 0.2)))
    exclude = lay.get("exclude")
    plate_names = {int(k): v for k, v in lay["plates"].items()}
    objs = lay["objects"]
    expect = lay.get("expect", {})
    fil_over = lay.get("filament_overrides", {})
    profiles = profiles_path(lay)
    tmp = tempfile.mkdtemp(dir=goh_tmp(), prefix="goh-cad-")
    mach = flatten(profiles, "machine", lay["machine"], os.path.join(tmp, "machine.json"))
    flatten(profiles, "process", lay["process"], os.path.join(tmp, "process.json"))
    flatten(profiles, "filament", lay["filament"], os.path.join(tmp, "filament.json"), fil_over)
    for k in ("printable_area", "nozzle_diameter"):
        if k in expect and mach.get(k) != expect[k]:
            raise LayoutError(f"flattened machine {k} = {mach.get(k)}, expected {expect[k]}")

    # 1. Bambu Studio imports the STLs: a valid project_settings and the object meshes are its own
    stls = [os.path.join(root, o["stl"]) for o in objs]
    for s in stls:
        if not os.path.exists(s):
            raise LayoutError(f"no STL {s}")
    base = os.path.join(tmp, "base.3mf")
    cfg = f"{tmp}/machine.json;{tmp}/process.json"
    cmd = [
        bambu_path(lay),
        "--load-settings",
        cfg,
        "--load-filaments",
        f"{tmp}/filament.json",
        "--arrange",
        "0",
    ]
    rc = run_cli([*cmd, "--export-3mf", base, *stls], os.path.join(tmp, "import.log"))
    if rc != 0:
        raise LayoutError(f"Bambu Studio import failed rc={rc}, see {tmp}/import.log")
    with zipfile.ZipFile(base) as z:
        files = {
            n: z.read(n)
            for n in z.namelist()
            if not re.match(r"Metadata/(plate|top|pick|plate_no_light)_\d+.*\.png", n)
        }
    model = files["3D/3dmodel.model"].decode()

    # 2. objects in input order (Bambu keeps it), each centred where the layout puts it on its plate
    comps = re.findall(
        r'<object id="(\d+)"[^>]*>\s*<components>\s*'
        r'<component p:path="([^"]+)" objectid="\d+"[^>]*transform="([^"]+)"',
        model,
    )
    if len(comps) != len(objs):
        raise LayoutError(
            f"Bambu Studio imported {len(comps)} objects for {len(objs)} in the layout"
        )
    cols = math.ceil(math.sqrt(len(plate_names)))
    placed = []
    for (oid, path, ctf), o in zip(comps, objs, strict=True):
        name, plate, (cx, cy) = o["name"], int(o["plate"]), o["centre"]
        off = [float(v) for v in ctf.split()[9:12]]
        lo, hi = _bbox(files[path.lstrip("/")])
        lo, hi = (
            [a + b for a, b in zip(lo, off, strict=True)],
            [a + b for a, b in zip(hi, off, strict=True)],
        )
        size = [b - a for a, b in zip(lo, hi, strict=True)]
        origin = (((plate - 1) % cols) * stride, -((plate - 1) // cols) * stride)
        t = [origin[0] + cx - (lo[0] + hi[0]) / 2, origin[1] + cy - (lo[1] + hi[1]) / 2, -lo[2]]
        x0, y0, x1, y1 = cx - size[0] / 2, cy - size[1] / 2, cx + size[0] / 2, cy + size[1] / 2
        if not (x0 >= 0 and x1 <= bed and y0 >= 0 and y1 <= bed):
            raise LayoutError(f"{name} off plate {plate}: {x0:.1f}..{x1:.1f} x {y0:.1f}..{y1:.1f}")
        if exclude and not (x0 >= exclude[0] or y0 >= exclude[1]):
            raise LayoutError(f"{name} overlaps the bed exclusion corner")
        regions = object_regions(root, o)
        st = object_settings(lay, o)
        if regions:
            st.update(ENFORCED)
        placed.append({
            "oid": oid, "name": name, "plate": plate, "t": t, "origin": origin, "size": size,
            "settings": st, "regions": regions,
        })  # fmt: skip
        tf = "1 0 0 0 1 0 0 0 1 " + " ".join(f"{v:.5f}" for v in t)
        model = re.sub(
            rf'(<item objectid="{oid}"[^>]*transform=")[^"]+(")',
            lambda m, tf=tf: m.group(1) + tf + m.group(2),
            model,
        )
    files["3D/3dmodel.model"] = model.encode()

    # 3. model_settings: names, per-object settings, plates; then each part's support enforcers
    ms = files["Metadata/model_settings.config"].decode()
    for p in placed:

        def obj_block(m, p=p):
            block = re.sub(
                r'(<object id="\d+">\s*<metadata key="name" value=")[^"]+(")',
                lambda q: q.group(1) + p["name"] + q.group(2),
                m.group(0),
                count=1,
            )
            extra = "".join(
                f'\n    <metadata key="{k}" value="{v}"/>' for k, v in sorted(p["settings"].items())
            )
            return re.sub(
                r'(<metadata key="extruder" value="1"/>)',
                lambda q: q.group(1) + extra,
                block,
                count=1,
            )

        ms = re.sub(rf'<object id="{p["oid"]}">.*?</object>', obj_block, ms, flags=re.DOTALL)
    plates = []
    for n, pname in plate_names.items():
        inst = "".join(
            f'\n    <model_instance>\n      <metadata key="object_id" value="{p["oid"]}"/>'
            f'\n      <metadata key="instance_id" value="0"/>'
            f'\n      <metadata key="identify_id" value="{100 + int(p["oid"])}"/>'
            "\n    </model_instance>"
            for p in placed
            if p["plate"] == n
        )
        plates.append(
            f'  <plate>\n    <metadata key="plater_id" value="{n}"/>'
            f'\n    <metadata key="plater_name" value="{pname}"/>'
            '\n    <metadata key="locked" value="false"/>'
            '\n    <metadata key="filament_map_mode" value="Auto For Flush"/>'
            f'\n    <metadata key="gcode_file" value=""/>{inst}\n  </plate>\n'
        )
    ms = re.sub(r"  <plate>.*</plate>\n", "".join(plates), ms, flags=re.DOTALL)
    files["Metadata/model_settings.config"] = ms.encode()
    next_id = 1 + max(int(i) for f in files.values() for i in re.findall(rb'<object id="(\d+)"', f))
    for p in placed:
        if p["regions"]:
            next_id = add_enforcers(files, p["oid"], p["regions"], next_id)
    seq = {
        f"plate_{n}": {"nozzle_sequence": [], "optimal_assignment": [], "sequence": []}
        for n in plate_names
    }
    files["Metadata/filament_sequence.json"] = json.dumps(seq).encode()

    # 4. project settings; overrides named as changed from the system profile, as the GUI shows them
    ps = json.loads(files["Metadata/project_settings.config"])
    ps.update(lay.get("project", {}))
    ps.update(fil_over)
    diff = ps.get("different_settings_to_system", ["", "", ""])
    diff[0] = ";".join(sorted(lay.get("process_overrides", [])))
    diff[1] = ";".join(sorted(fil_over))
    ps["different_settings_to_system"] = diff
    files["Metadata/project_settings.config"] = json.dumps(ps, indent=4).encode()
    rels = files["_rels/.rels"].decode()
    files["_rels/.rels"] = re.sub(
        r'\s*<Relationship Target="/Metadata/plate_1[^"]*\.png"[^>]*/>', "", rels
    ).encode()

    out_3mf = os.path.join(out_dir, lay.get("out_3mf", "project.3mf"))
    os.makedirs(os.path.dirname(out_3mf) or ".", exist_ok=True)
    with zipfile.ZipFile(out_3mf, "w", zipfile.ZIP_DEFLATED) as out:
        first = ("[Content_Types].xml", "_rels/.rels")
        for n in [*first, *sorted(k for k in files if k not in first)]:
            out.writestr(n, files[n])
    return out_3mf, tmp, plate_names, placed
