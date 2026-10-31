"""check_tracked_build_output.py — a tracked file that is build output.

koffee_oss asked for this on 2026-10-19 (BACKLOG.md, "no tracked build output"): six files under
`DerivedDataTests/` came in with the history flatten, and the `.gitignore` check cannot see them --
it cannot see a file that is ALREADY tracked, which is the only case that matters.

The gate reads the TRACKED files, so these tests are about the difference between a file that is
tracked and one that is merely on disk: the second is `.gitignore`'s business, and the house's
tracked-ignored step already covers a file that is tracked AND ignored.
"""

from __future__ import annotations

from conftest import run_check, stage, write

GATE = "checks/check_tracked_build_output.py"


def run(repo, *args):
    return run_check(repo, GATE, *args)


def test_a_clean_tree_is_green(repo):
    write(repo, "src/main.rs", "fn main() {}\n")
    stage(repo, "src/main.rs")
    r = run(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "looks like build output" in r.stdout, r.stdout


def test_a_tracked_build_output_is_red_and_named(repo):
    write(repo, "target/debug/app", "binary\n")
    stage(repo, "target/debug/app")
    r = run(repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "target/debug/app" in r.stderr, r.stderr
    assert "build directory" in r.stderr, r.stderr


def test_a_repository_that_tracks_nothing_is_refused_not_passed(tmp_path):
    """A scan that found no subject has judged nothing: the empty sweep's class, refused."""
    from _fast_git import fast_init

    empty = tmp_path / "empty"
    empty.mkdir()
    fast_init(empty, "main")
    r = run_check(empty, GATE)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "tracks no files" in r.stderr, r.stderr


def test_a_build_output_that_is_only_on_disk_is_not_a_finding(repo):
    """Untracked is `.gitignore`'s business, and this gate is about the other case."""
    write(repo, "src/main.rs", "fn main() {}\n")
    stage(repo, "src/main.rs")
    write(repo, "target/debug/app", "binary\n")
    r = run(repo)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_file_whose_NAME_is_a_shape_word_is_not_a_finding(repo):
    """`target.md` is a document; `target/` is a build directory. The shape is a PATH SEGMENT."""
    write(repo, "docs/target.md", "# target\n")
    stage(repo, "docs/target.md")
    r = run(repo)
    assert r.returncode == 0, r.stdout + r.stderr


def test_every_shape_is_caught(repo):
    """One case per shape: a gate that reads only one of them is a gate that misses the rest."""
    cases = {
        "target/debug/app": "build directory",
        "DerivedData/App/Build/x": "build directory",
        "pkg/__pycache__/m.cpython-311.pyc": "build directory",
        "web/node_modules/left-pad/index.js": "build directory",
        ".build/checkouts/x": "build directory",
        ".venv/lib/python3.11/x.py": "build directory",
        "build/libthing.a": "compiled file",
        "obj/main.o": "compiled file",
        "lib/libthing.so": "compiled file",
        "lib/libthing.dylib": "compiled file",
        "pkg/m.pyc": "compiled file",
        "pkg/m.class": "compiled file",
        "Logs/build.xcactivitylog": "compiled file",
        "build/App.dSYM/Contents/x": "build bundle",
        "dist/App.app/Contents/x": "build bundle",
        "dist/App.ipa": "build bundle",
    }
    for path, why in cases.items():
        write(repo, path, "x\n")
        stage(repo, path)
        r = run(repo)
        assert r.returncode == 1, (path, r.stdout + r.stderr)
        assert why in r.stderr, (path, r.stderr)
        # One case at a time: the next case must not inherit this one's finding.
        (repo / path).unlink()
        stage(repo, path)


# ── An extension that names BOTH a binary build product and a text format (OW.29) ──────────────
#
# `.obj` is a compiler's object file (COFF) AND a Wavefront mesh (plain text). ZeroThunder tracks 244
# `assets/meshes/*.obj` meshes -- source assets -- and the name-only rule refused every one of them.
# For such an extension the CONTENT decides: a binary object has a magic number or NUL bytes, a text
# file has neither. Both directions are pinned: the mesh passes AND the object file is still red.

WAVEFRONT = (
    "# a mesh\nmtllib x.mtl\nv 0.0 0.0 0.0\nv 1.0 0.0 0.0\nvn 0.0 0.0 1.0\nf 1//1 2//1 3//1\n"
)
COFF_X64 = b"\x64\x86\x03\x00\x00\x00\x00\x00\x40\x01\x00\x00\x0a\x00\x00\x00\x00\x00\x04\x00.text\x00\x00\x00"
ELF_OBJ = b"\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x01\x00\x3e\x00\x01\x00\x00\x00"
# A Mach-O header whose first bytes hold no NUL: the magic number alone must be enough.
MACHO_64 = b"\xcf\xfa\xed\xfe\x07\x01\x01\x01\x01\x01\x01\x01"


def write_bytes(repo, rel, data):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_a_text_wavefront_obj_is_a_mesh_not_a_compiled_file(repo):
    write(repo, "assets/meshes/ship.obj", WAVEFRONT)
    stage(repo, "assets/meshes/ship.obj")
    r = run(repo)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ship.obj" not in r.stderr, r.stderr


def test_a_binary_obj_is_still_a_compiled_file(repo):
    """The other direction: letting the mesh through must not let an object file through."""
    for name, data in {"coff": COFF_X64, "elf": ELF_OBJ, "macho": MACHO_64}.items():
        path = f"build/{name}.obj"
        write_bytes(repo, path, data)
        stage(repo, path)
        r = run(repo)
        assert r.returncode == 1, (name, r.stdout + r.stderr)
        assert path in r.stderr and "compiled file" in r.stderr, (name, r.stderr)
        (repo / path).unlink()
        stage(repo, path)


def test_the_mesh_passes_beside_an_object_file_that_does_not(repo):
    """One run, both kinds: only the object file is named."""
    write(repo, "assets/meshes/ship.obj", WAVEFRONT)
    write_bytes(repo, "build/main.obj", COFF_X64)
    stage(repo, "assets/meshes/ship.obj", "build/main.obj")
    r = run(repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "build/main.obj" in r.stderr and "ship.obj" not in r.stderr, r.stderr


def test_an_extension_with_no_text_form_is_still_refused_by_name_alone(repo):
    """`.o` has no text format: a file called main.o is refused whatever it holds, as before."""
    write(repo, "obj/main.o", WAVEFRONT)
    stage(repo, "obj/main.o")
    r = run(repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "obj/main.o" in r.stderr, r.stderr


def test_a_text_obj_inside_a_build_directory_is_still_in_a_build_directory(repo):
    """The directory shape does not read content: a mesh under `target/` is still build output."""
    write(repo, "target/ship.obj", WAVEFRONT)
    stage(repo, "target/ship.obj")
    r = run(repo)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "build directory" in r.stderr, r.stderr


# ── --staged: the commit hook's scope (OW.29: this is now a structural step) ───────────────────


def test_staged_judges_only_what_the_commit_brings(repo):
    """A pre-commit red on an already-committed file would block every unrelated commit."""
    from conftest import commit_all

    write(repo, "target/old.bin", "x\n")
    commit_all(repo)
    write(repo, "src/main.rs", "fn main() {}\n")
    stage(repo, "src/main.rs")
    assert run(repo).returncode == 1  # the whole tree names the old file
    r = run(repo, "--staged")
    assert r.returncode == 0, r.stdout + r.stderr
    write(repo, "target/new.bin", "y\n")
    stage(repo, "target/new.bin")
    r = run(repo, "--staged")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "target/new.bin" in r.stderr and "target/old.bin" not in r.stderr, r.stderr


def test_staged_reads_the_index_blob_not_the_worktree(repo):
    """Stage a mesh, dirty the worktree copy into an object file: the commit holds the mesh."""
    write(repo, "m/ship.obj", WAVEFRONT)
    stage(repo, "m/ship.obj")
    write_bytes(repo, "m/ship.obj", COFF_X64)
    r = run(repo, "--staged")
    assert r.returncode == 0, r.stdout + r.stderr
    # ...and the reverse: stage the object file, tidy the worktree copy into a mesh.
    write_bytes(repo, "m/real.obj", COFF_X64)
    stage(repo, "m/real.obj")
    write(repo, "m/real.obj", WAVEFRONT)
    r = run(repo, "--staged")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "m/real.obj" in r.stderr, r.stderr


def test_a_commit_that_stages_nothing_is_not_refused(repo):
    """A scope with no subject is a commit with nothing to judge, not a repository tracking nothing."""
    r = run(repo, "--staged")
    assert r.returncode == 0, r.stdout + r.stderr
