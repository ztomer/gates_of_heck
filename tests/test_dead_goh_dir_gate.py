"""The starter tools/gate.sh every consumer carries runs under a dead export GOH_DIR
(EST.GOHDIRSHELL; the rule is lib/goh_root.sh, GOH.TRANSIENTDIR).

install.sh writes tools/gate.sh once, as a starter the repo then edits, and it read GOH_DIR bare:
a shell that inherited a pruned ~/.cache/goh/head/<sha> export ran `"$GOH/gates/structural.sh"`
from a directory that was gone. The template carries the rule verbatim (it can source nothing
from a dead goh), and a starter written before it is migrated in place by install.sh -- only the
one stock line it shipped, never the layers the repo added -- which `goh hooks --refresh` runs.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from conftest import REPO_ROOT, hermetic_env

OLD_LINE = 'GOH="${GOH_DIR:-${GOH:-$HOME/Projects/gates_of_heck}}"\n'
OLD_GATE = (
    "#!/usr/bin/env bash\nset -euo pipefail\n" + OLD_LINE + '"$GOH/gates/structural.sh" "$@"\n'
    '# this repo\'s own layer\n"$GOH/gates/py_gate.sh" .\n'
)


def _function(text: str) -> str:
    m = re.search(r"^goh_dir_repair\(\) \{\n.*?^\}\n", text, re.DOTALL | re.MULTILINE)
    assert m, "no goh_dir_repair() in it"
    return m.group(0)


def _fake_goh(root: Path) -> Path:
    (root / "gates").mkdir(parents=True)
    rec = 'echo "ran $0 GOH_DIR=${GOH_DIR:-}" >> "$(dirname "$0")/../ran"\n'
    for g in ("structural.sh", "py_gate.sh"):
        (root / "gates" / g).write_text("#!/usr/bin/env bash\n" + rec)
        (root / "gates" / g).chmod(0o755)
    return root


def _install(repo: Path) -> subprocess.CompletedProcess:
    env = hermetic_env(drop_git=True, GOH_SKIP_BUILD="1")
    return subprocess.run(
        ["bash", str(REPO_ROOT / "install.sh"), str(repo)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _gate(repo: Path, tmp_path: Path, live: Path) -> subprocess.CompletedProcess:
    dead = tmp_path / "cache" / "goh" / "head" / ("c06bc794" * 5)
    env = hermetic_env(drop_git=True, HOME=str(tmp_path / "home"), GOH_DIR=str(dead))
    env["GOH_LIVE_ROOT"] = str(live)
    return subprocess.run(
        ["bash", "tools/gate.sh", "--staged"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def test_the_starter_gate_runs_under_a_dead_export(tmp_path, repo):
    assert _install(repo).returncode == 0
    live = _fake_goh(tmp_path / "live")
    r = _gate(repo, tmp_path, live)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (live / "ran").read_text() == f"ran {live}/gates/structural.sh GOH_DIR={live}\n"
    assert "c06bc794" in r.stderr, "the fallback is silent: nobody learns the process is stale"


def test_the_starter_gate_carries_the_one_rule_verbatim(tmp_path, repo):
    assert _install(repo).returncode == 0
    body = (repo / "tools" / "gate.sh").read_text()
    assert _function(body) == _function((REPO_ROOT / "lib" / "goh_root.sh").read_text())
    assert body.index("goh_dir_repair\nGOH=") < body.index("/gates/structural.sh")


def test_install_migrates_an_old_starter_and_keeps_its_layers(tmp_path, repo):
    (repo / "tools").mkdir()
    (repo / "tools" / "gate.sh").write_text(OLD_GATE)
    assert _install(repo).returncode == 0
    body = (repo / "tools" / "gate.sh").read_text()
    assert body.replace(body[body.index("# A GOH_DIR") : body.index(OLD_LINE)], "") == OLD_GATE
    live = _fake_goh(tmp_path / "live")
    r = _gate(repo, tmp_path, live)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (live / "ran").read_text().count(f"GOH_DIR={live}\n") == 2
    # Idempotent: a second install leaves the migrated gate as it is.
    assert _install(repo).returncode == 0
    assert (repo / "tools" / "gate.sh").read_text() == body


def test_a_gate_without_the_stock_line_is_left_alone(repo):
    (repo / "tools").mkdir()
    own = '#!/usr/bin/env bash\nexec bash "$GOH_DIR/gates/structural.sh" "$@"\n'
    (repo / "tools" / "gate.sh").write_text(own)
    assert _install(repo).returncode == 0
    assert (repo / "tools" / "gate.sh").read_text() == own
