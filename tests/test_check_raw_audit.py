"""No repo runs `cargo audit` outside goh's advisory-db wrapper (checks/check_raw_audit.py).

GOH.AUDITONCE, 2026-11-17: antiknob's tools/repo_gates.sh and house-lib's and sys_updater's
GOH_CI_STEPS ran a raw `cargo audit` beside rust_gate.sh's; it skipped goh's shared advisory
rulings and refused the v0.130.0 pin pushes on RUSTSEC-2026-0543. The shapes below are those
repos' own lines; the green ones are koffee_big's and webcam_tune's comment-only mentions and
routines' checker, which names the command in strings.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from _fast_git import fast_init
from conftest import REPO_ROOT, hermetic_env

CHECK = REPO_ROOT / "checks" / "check_raw_audit.py"
RAW_STEP = "GOH_CI_STEPS='./gates/rust_gate.sh:cargo audit --deny warnings --deny yanked'\n"
WRAPPED = (
    'GOH_CI_STEPS=\'./gates/rust_gate.sh:python3 "$GOH_DIR/lib/advisory_db.py" run --fetch --'
    " cargo audit'\n"
)


def _repo(root: Path, files: dict[str, str]) -> Path:
    fast_init(root)
    env = hermetic_env(drop_git=True)
    files = {"README.md": "a repo\n", **files}
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    subprocess.run(
        ["git", "-C", str(root), "add", "-A"], check=True, env=env, stdin=subprocess.DEVNULL
    )
    return root


def _run(*args: str, seed: Path | None = None) -> subprocess.CompletedProcess:
    env = hermetic_env(drop_git=True)
    if seed is not None:
        env["GOH_RAW_AUDIT_SEED"] = str(seed)
    return subprocess.run(
        [sys.executable, str(CHECK), *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        stdin=subprocess.DEVNULL,
    )


def test_a_raw_audit_step_is_red_and_the_wrapped_one_green(tmp_path: Path) -> None:
    red = _run(str(_repo(tmp_path / "raw", {".gatesrc": RAW_STEP})))
    assert red.returncode == 1 and "GOH_CI_STEPS step 2" in red.stderr, red.stderr
    green = _run(str(_repo(tmp_path / "ok", {".gatesrc": WRAPPED})))
    assert green.returncode == 0, green.stderr


def test_a_tools_script_is_judged_by_its_commands_not_its_words(tmp_path: Path) -> None:
    raw = 'info "dependency audit"\nif cargo audit; then\n    ok "dependency audit"\nfi\n'
    red = _run(str(_repo(tmp_path / "raw", {"tools/repo_gates.sh": raw})))
    assert red.returncode == 1 and "tools/repo_gates.sh" in red.stderr, red.stderr
    words = (
        "# cargo audit runs in rust_gate.sh\n"
        'echo "cargo audit failed"\n'
        "command -v cargo-audit\n"
        'python3 "$GOH_DIR/lib/advisory_db.py" run --no-fetch -- cargo audit --file x\n'
    )
    green = _run(str(_repo(tmp_path / "ok", {"tools/gate.sh": words})))
    assert green.returncode == 0, green.stderr


def test_a_python_argv_is_red_unless_it_goes_through_the_wrapper(tmp_path: Path) -> None:
    py = 'import subprocess\nsubprocess.run(["cargo", "audit", "--json"], check=False)\n'
    red = _run(str(_repo(tmp_path / "raw", {"tools/audit.py": py})))
    assert red.returncode == 1 and "tools/audit.py" in red.stderr, red.stderr
    mention = 'AUDIT = "cargo audit"  # the step this checker looks for\n'
    routed = "import advisory_db\n" + py.replace("subprocess.run(", "advisory_db.bounded(")
    green = _run(str(_repo(tmp_path / "ok", {"tools/a.py": mention, "tools/b.py": routed})))
    assert green.returncode == 0, green.stderr


def test_a_scan_of_nothing_is_refused_unless_the_gate_says_so(tmp_path: Path) -> None:
    shape_only = tmp_path / "shape"
    fast_init(shape_only)
    env = hermetic_env(drop_git=True)
    (shape_only / ".gatesrc").write_text(WRAPPED)
    subprocess.run(
        ["git", "-C", str(shape_only), "add", "-A"], check=True, env=env, stdin=subprocess.DEVNULL
    )
    assert _run(str(shape_only)).returncode == 2
    assert _run("--empty-ok", str(shape_only)).returncode == 0


def test_the_seed_exempts_its_entry_and_the_estate_refuses_a_stale_one(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    _repo(estate / "old", {".gatesrc": RAW_STEP})
    _repo(estate / "fixed", {".gatesrc": WRAPPED})
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps({"old": {".gatesrc": "fixture: seeded"}}))
    assert _run(str(estate / "old"), seed=seed).returncode == 0
    assert _run("--estate", str(estate), seed=seed).returncode == 0
    seed.write_text(json.dumps({"old": {".gatesrc": "r"}, "fixed": {".gatesrc": "stale"}}))
    stale = _run("--estate", str(estate), seed=seed)
    assert stale.returncode == 1 and "stale seed entry fixed/.gatesrc" in stale.stderr, stale.stderr


def test_the_probe_goes_red_and_the_rust_gate_runs_the_check() -> None:
    assert _run("--probe").returncode == 0
    gate = (REPO_ROOT / "gates" / "rust_gate.sh").read_text()
    assert 'checks/check_raw_audit.py" --empty-ok "$top"' in gate
    assert json.loads((REPO_ROOT / "checks" / "raw_audit_seed.json").read_text()).keys() == {"_doc"}
