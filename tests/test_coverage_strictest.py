"""Strictest convergence: include filter, per-target floors, marker ceiling."""

import json
import sys
from pathlib import Path

import importlib.util

from conftest import REPO_ROOT, mk_fake_swift_toolchain

SWIFT_HELPER = REPO_ROOT / "gates" / "coverage_swift.py"
LCOV_MERGE = REPO_ROOT / "gates" / "lcov_merge.py"


def _load_swift():
    spec = importlib.util.spec_from_file_location("coverage_swift_strict", SWIFT_HELPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_xccov_include_filters_before_ignore():
    cov = _load_swift()
    report = (
        "Name                                    Coverage      \n"
        "---------------------------------- ------------- \n"
        "PkgTests                               50.00% (4/8)  \n"
        "    /tmp/p/Sources/cov/Domain/A.swift      20.00% (1/5)  \n"
        "    /tmp/p/Sources/cov/Data/B.swift        80.00% (4/5)  \n"
        "    /tmp/p/Sources/cov/Views/C.swift       0.00% (0/5)  \n"
    )
    files = cov.parse_xccov_report(report, ignore_re=None, include_re=r"/Domain|/Data/")
    assert "/tmp/p/Sources/cov/Domain/A.swift" in files
    assert "/tmp/p/Sources/cov/Data/B.swift" in files
    assert "/tmp/p/Sources/cov/Views/C.swift" not in files
    files2 = cov.parse_xccov_report(report, ignore_re=r"/Domain/", include_re=r"/Domain|/Data/")
    assert "/tmp/p/Sources/cov/Domain/A.swift" not in files2
    assert "/tmp/p/Sources/cov/Data/B.swift" in files2


def test_find_exclusions_include_positive_before_ignore(tmp_path):
    cov = _load_swift()
    proj = tmp_path / "proj"
    domain = proj / "Sources" / "Domain" / "A.swift"
    views = proj / "Sources" / "Views" / "B.swift"
    domain.parent.mkdir(parents=True)
    views.parent.mkdir(parents=True)
    domain.write_text("line // cov:ignore: reason\n")
    views.write_text("line // cov:ignore: reason\n")
    out, _ = cov.find_exclusions(str(proj), ignore_re="", include_re="")
    assert len(out) == 2
    out2, _ = cov.find_exclusions(str(proj), ignore_re="", include_re=r"/Domain/")
    assert len(out2) == 1
    assert any("Domain" in p for p in out2)


def test_load_floors_config_supports_both_shapes(tmp_path):
    cov = _load_swift()
    flat = tmp_path / "flat.json"
    flat.write_text(json.dumps({"ZTCore": 98.9, "ZTSystem": 58.9}))
    cfg = cov.load_floors_config(str(flat))
    assert cfg["targets"]["ZTCore"] == 98.9
    assert cfg["file_floor"] is None
    wrapper = tmp_path / "wrap.json"
    wrapper.write_text(json.dumps({
        "targets": {"ZTCore": 98.9},
        "file_floor": 90.0,
        "tolerance": 0.5,
        "exempt": {"ZTCore/Foo.swift": "defensive branch"}
    }))
    cfg2 = cov.load_floors_config(str(wrapper))
    assert cfg2["file_floor"] == 90.0
    assert cfg2["tolerance"] == 0.5
    assert "ZTCore/Foo.swift" in cfg2["exempt"]


def test_lcov_merge_include_filters_before_floor(tmp_path):
    spec = importlib.util.spec_from_file_location("lcov_merge_strict", LCOV_MERGE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    pd = tmp_path / "parts"
    pd.mkdir()

    def write_part(name, sf, covered):
        cnt = 1 if covered else 0
        rows = [f"SF:{sf}", "FN:1,_Za", "FNDA:1,_Za" if covered else "FNDA:0,_Za", f"DA:1,{cnt}", "end_of_record"]
        (pd / name).write_text("\n".join(rows) + "\n")

    write_part("part-a.info", "src/a.rs", True)
    write_part("part-b.info", "src/b.rs", False)
    parts = sorted(pd.glob("part-*.info"))
    rc = mod.merge([str(p) for p in parts], floor=90, include_re="")
    assert rc == 1
    rc2 = mod.merge([str(p) for p in parts], floor=90, include_re=r"a\.rs")
    assert rc2 == 0


def test_lcov_merge_per_file_floor_with_tolerance_and_exempt(tmp_path):
    spec = importlib.util.spec_from_file_location("lcov_merge_strict2", LCOV_MERGE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    pd = tmp_path / "parts"
    pd.mkdir()
    rows = ["SF:src/a.rs"] + [f"DA:{i},1" for i in range(1, 10)] + ["DA:10,0", "end_of_record"]
    (pd / "part-a.info").write_text("\n".join(rows) + "\n")
    floors = tmp_path / "floors.json"
    floors.write_text(json.dumps({"file_floor": 95.0, "tolerance": 0.0, "exempt": {}}))
    parts = [str(pd / "part-a.info")]
    rc = mod.merge(parts, floor=0, floors_json=str(floors))
    assert rc == 1
    floors2 = tmp_path / "floors2.json"
    floors2.write_text(json.dumps({"file_floor": 95.0, "tolerance": 0.0, "exempt": {"src/a.rs": "defensive"}}))
    rc2 = mod.merge(parts, floor=0, floors_json=str(floors2))
    assert rc2 == 0


def test_marker_ceiling_shrink_only(tmp_path):
    cov = _load_swift()
    ceiling = tmp_path / "ceil.json"
    ceiling.write_text(json.dumps({"max_forgiven_lines": 5}))
    cov.check_marker_ceiling(5, str(ceiling))
    cov.check_marker_ceiling(3, str(ceiling))
    try:
        cov.check_marker_ceiling(6, str(ceiling))
        assert False, "should have exited"
    except SystemExit as e:
        assert e.code == 1


def test_gates_bash_include_and_floors_json_flags_accepted(tmp_path):
    import subprocess
    gate = REPO_ROOT / "gates" / "coverage_gate.sh"
    proj = tmp_path / "proj"
    proj.mkdir()
    r = subprocess.run(["/bin/bash", str(gate), "--lang", "py", "--floor", "90", "--include", r"/Domain/", str(proj)],
                       capture_output=True, text=True)
    assert "unknown option" not in (r.stdout + r.stderr)
    r2 = subprocess.run(["/bin/bash", str(gate), "--lang", "py", "--floors-json", "/no/such/floors.json", str(proj)],
                        capture_output=True, text=True)
    assert r2.returncode == 2
    assert "floors file not found" in (r2.stdout + r2.stderr)


def test_coverage_swift_requires_one_floor_declaration(tmp_path):
    import subprocess
    helper = REPO_ROOT / "gates" / "coverage_swift.py"
    r = subprocess.run([sys.executable, str(helper), "--proj", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 2
    assert "no coverage floor" in (r.stderr + r.stdout).lower()
