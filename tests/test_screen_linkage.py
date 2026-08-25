"""checks/check_no_screen_linkage.sh — the DYNAMIC half, proven against REAL
nm output: tiny object files compiled on this machine (skipif no cc), one
referencing a forbidden symbol, one clean. A fake "binary" would prove
nothing — the gate's whole point is reading real link tables.
"""

import shutil
import subprocess

from conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "checks" / "check_no_screen_linkage.sh"

cc = shutil.which("cc")


def compile_obj(tmp_path, name, body):
    src = tmp_path / f"{name}.c"
    src.write_text(body)
    obj = tmp_path / name
    r = subprocess.run([cc, "-c", str(src), "-o", str(obj)], capture_output=True)
    assert r.returncode == 0, r.stderr.decode()
    return obj


def run(*args):
    return subprocess.run(
        ["/bin/bash", str(SCRIPT), *args], capture_output=True, text=True
    )


def test_clean_object_passes(tmp_path):
    obj = compile_obj(tmp_path, "clean", "int go(int x) { return x + 1; }\n")
    r = run(str(obj))
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout


def test_input_grabbing_symbol_fails(tmp_path):
    # nm -u lists UNDEFINED symbols: referencing CGEventPost is enough to put
    # it in the link table without linking a runnable binary.
    obj = compile_obj(
        tmp_path, "grabber",
        "extern void CGEventPost(unsigned long, void *);\n"
        "int go(void) { CGEventPost(0, 0); return 0; }\n",
    )
    r = run(str(obj))
    assert r.returncode == 1
    assert "CGEventPost" in r.stderr


def test_missing_binary_is_config_error_not_silent_pass(tmp_path):
    r = run(str(tmp_path / "nope.o"))
    assert r.returncode == 2


def test_no_arguments_is_usage_error():
    r = run()
    assert r.returncode == 2
    assert "usage" in r.stderr


def test_one_bad_binary_among_clean_ones_fails_the_batch(tmp_path):
    clean = compile_obj(tmp_path, "clean2", "int f(void) { return 7; }\n")
    bad = compile_obj(
        tmp_path, "bad2",
        "extern void CGDisplayHideCursor(void *);\n"
        "int g(void *d) { CGDisplayHideCursor(d); return 0; }\n",
    )
    r = run(str(clean), str(bad))
    assert r.returncode == 1
    assert "CGDisplayHideCursor" in r.stderr
