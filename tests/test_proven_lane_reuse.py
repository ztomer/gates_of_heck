"""A lane's pass is the push's pass, and a miss says what moved (GOH.PROOFKEY).

A private consumer, 2026-11-16: its pre-push `make ci` (10-35 min) passed in a lane worktree,
main fast-forwarded to that tree, and the push from the main checkout ran it again in full, three
times in a row. Every record was reconstructed byte for byte: the pre-push hook runs proven.sh from
goh's head export
(GOH_DIR=~/.cache/goh/head/<sha>, PYTHONPATH=<that export>:<the checkout>), and the two keys of
each pair differed in exactly two lines -- `goh content` (goh landed while the lane's gate ran)
and `env PYTHONPATH=<a new export path>` (GOH.PROVENPYPATH, fixed before this). What is pinned:

  * a pass recorded in one worktree, under one export path, is reused in another worktree of the
    same repo under another export path of the same goh -- the hook's environment;
  * a goh change touching only TEST sources (a `#[cfg(test)] mod x;` file, a crate's tests/) keeps
    the key: no release build compiles them, so the binary the gates run is the same program;
  * a change to a gate the step runs, or to a `tests.rs` that is built, still re-runs it;
  * a miss on a tree and step that passed before names what moved -- under goh, the files.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT
from test_proven import _git, env, goh, repo, runs  # noqa: F401  (fixtures)
from test_proven_goh_content import _export, _prove, _with_crates, _write
from timing_bounds import patience

LIB = REPO_ROOT / "lib" / "gate_identity.py"
CRATE = "crates/goh"
TESTED = {
    f"{CRATE}/src/lib.rs": "pub fn f() {}\n#[cfg(test)]\nmod tests;\n",
    f"{CRATE}/src/tests.rs": "#[test]\nfn t() {}\n",
    f"{CRATE}/tests/it.rs": "#[test]\nfn it() {}\n",
}


def _hook(e: dict, export: Path, checkout: Path) -> dict:
    """The pre-push hook's environment: goh's head export first on PYTHONPATH, then the checkout
    ~/.zshrc puts there (gates/_from_head.sh `goh_path_front`)."""
    return dict(e, PYTHONPATH=f"{export}:{checkout}")


def _tested(goh: Path) -> Path:  # noqa: F811
    checkout = _with_crates(goh)
    _write(checkout, TESTED)
    _git(checkout, "add", "-A")
    _git(checkout, "commit", "-q", "-m", "a crate with a test module and integration tests")
    return checkout


def test_a_lane_pass_is_reused_by_the_push_from_the_main_checkout(goh, repo, env, tmp_path):  # noqa: F811
    checkout = _with_crates(goh)
    lane_export = _export(checkout, tmp_path / "lane-cache")
    push_export = _export(checkout, tmp_path / "push-cache")
    lane = tmp_path / "lane"
    _git(repo, "worktree", "add", "-q", "--detach", str(lane), "HEAD")
    first = _prove(lane_export, lane, _hook(env, lane_export, checkout))
    assert first.returncode == 0 and runs(env) == 1, first.stdout + first.stderr
    push = _prove(push_export, repo, _hook(env, push_export, checkout))
    assert push.returncode == 0, push.stdout + push.stderr
    assert runs(env) == 1, f"the push re-ran what the lane proved on the same tree: {push.stdout}"
    assert "proven on this tree" in push.stdout, push.stdout


def test_a_goh_change_to_test_sources_only_keeps_the_key(goh, repo, env, tmp_path):  # noqa: F811
    export = _export(_tested(goh), tmp_path)
    assert _prove(export, repo, env).returncode == 0 and runs(env) == 1
    (export / CRATE / "src" / "tests.rs").write_text("#[test]\nfn t() { assert!(true); }\n")
    (export / CRATE / "tests" / "it.rs").write_text("#[test]\nfn it() { assert!(true); }\n")
    again = _prove(export, repo, env)
    assert again.returncode == 0, again.stdout + again.stderr
    assert runs(env) == 1, f"a test-only goh change re-ran a proven step: {again.stdout}"
    # Calibration: the same crate's built source is still keyed.
    built = "pub fn f() -> u8 { 1 }\n#[cfg(test)]\nmod tests;\n"
    (export / CRATE / "src" / "lib.rs").write_text(built)
    assert _prove(export, repo, env).returncode == 0 and runs(env) == 2


def test_a_tests_rs_the_build_compiles_is_keyed(goh, repo, env, tmp_path):  # noqa: F811
    """By proof from the declaring source, never by name: `mod tests;` without the cfg is built."""
    checkout = _with_crates(goh)
    _write(checkout, {f"{CRATE}/src/lib.rs": "pub fn f() {}\nmod tests;\n",
                      f"{CRATE}/src/tests.rs": "pub fn g() {}\n"})  # fmt: skip
    _git(checkout, "add", "-A")
    _git(checkout, "commit", "-q", "-m", "a built module named tests")
    export = _export(checkout, tmp_path)
    assert _prove(export, repo, env).returncode == 0 and runs(env) == 1
    (export / CRATE / "src" / "tests.rs").write_text("pub fn g() -> u8 { 2 }\n")
    assert _prove(export, repo, env).returncode == 0 and runs(env) == 2


def test_a_miss_after_a_goh_change_names_the_changed_gate_and_reruns(goh, repo, env, tmp_path):  # noqa: F811
    export = _export(_with_crates(goh), tmp_path)
    assert _prove(export, repo, env).returncode == 0 and runs(env) == 1
    with open(export / "gates" / "local_ci.sh", "a") as f:
        f.write("# one byte more\n")
    again = _prove(export, repo, env)
    assert again.returncode == 0 and runs(env) == 2, again.stdout + again.stderr
    assert "goh changed since in gates/local_ci.sh -- re-run" in again.stdout, again.stdout


def test_a_miss_after_an_environment_change_names_the_line(goh, repo, env, tmp_path):  # noqa: F811
    export = _export(_with_crates(goh), tmp_path)
    assert _prove(export, repo, dict(env, CI="")).returncode == 0 and runs(env) == 1
    again = _prove(export, repo, dict(env, CI="true"))
    assert again.returncode == 0 and runs(env) == 2, again.stdout + again.stderr
    assert "its key moved in: env CI -- re-run" in again.stdout, again.stdout


def _explain(export, cwd, base_env, **ci):
    e = {k: v for k, v in base_env.items() if k != "CI"} | ci  # CI is pinned here, never inherited
    e |= {"GOH_DIR": str(export), "GOH_HEAD_CACHE": str(export.parent)}
    return subprocess.run(["bash", str(export / "gates" / "proven.sh"), "--explain", "--", "true"],
                          cwd=cwd, env=e, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          timeout=patience(120), check=False)  # fmt: skip


def test_explain_prints_the_identity_lines_and_runs_nothing(goh, repo, env, tmp_path):  # noqa: F811
    """CI is pinned, not inherited: a hosted runner has CI=true and prints `env CI=true`, a laptop
    has none and prints `env CI unset` (v0.129.0 was red on hosted CI asserting only the second)."""
    export = _export(_with_crates(goh), tmp_path)
    for ci, want in (({}, "env CI unset"), ({"CI": "true"}, "env CI=true")):
        r = _explain(export, repo, dict(env, GOH_PROVEN_CLOSURE="on"), **ci)  # its closure line
        assert r.returncode == 0, r.stdout + r.stderr
        lines = r.stdout.splitlines()
        assert (
            lines[0].startswith("key ") and lines[1].startswith("tree ") and lines[2] == "step true"
        )
        assert any(x.startswith("closure ") for x in lines), r.stdout
        assert want in lines, (want, r.stdout)
        assert runs(env) == 0


def test_test_only_follows_path_attributes_and_module_trees(tmp_path: Path) -> None:
    sys.path.insert(0, str(LIB.parent))
    import gate_identity as gi

    root = tmp_path / "g"
    _write(root, {
        "Cargo.toml": "[workspace]\n",
        f"{CRATE}/src/main.rs": "mod a;\nmod b;\n"
                                "#[cfg(test)]\n#[path = \"main_tests.rs\"]\nmod tests;\n",
        f"{CRATE}/src/main_tests.rs": "",
        f"{CRATE}/src/a.rs": "#[cfg(test)]\nmod deep;\n#[cfg(any(test, feature = \"x\"))]\n"
                             "mod both;\n",
        f"{CRATE}/src/a/deep/mod.rs": "mod more;\n",
        f"{CRATE}/src/a/deep/more.rs": "",
        f"{CRATE}/src/a/both.rs": "",
        f"{CRATE}/src/b.rs": "include!(\"b_tests.rs\");\n#[cfg(test)]\nmod b_tests;\n",
        f"{CRATE}/src/b/b_tests.rs": "",
    })  # fmt: skip
    got = gi.test_only(root)
    assert got == {f"{CRATE}/src/main_tests.rs", f"{CRATE}/src/a/deep/mod.rs",
                   f"{CRATE}/src/a/deep/more.rs"}, sorted(got)  # fmt: skip
