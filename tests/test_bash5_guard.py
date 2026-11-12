"""goh's shell runs on bash >= 5: every bash file it tracks runs lib/bash5.sh first (GOH.BASH5).

Owner ruling 2026-11-12: require bash 5, enforced structurally. The macOS /bin/bash is 3.2 and is
what the hosted runner, a minimal-PATH git hook and launchd find through `#!/usr/bin/env bash`;
v0.106.1 was green on Homebrew bash 5 and red there. The guard (docs/contracts.md section 28)
re-execs an EXECUTED script under the first bash >= 5 it finds, or dies naming the fix; a SOURCED
file never re-execs: it returns, silently, and stays bash-3.2-safe.

THE GATE (test_every_tracked_bash_file_runs_the_guard_first): the guard is the first statement --
nothing but the shebang, comments, the parse-guard opener, `set` and plain assignments before it --
and every guarded file PARSES under the real bash 3.2 (on this Mac), so under 3.2 the guard is what
runs first. Calibrated both ways by `test_the_gate_sees_a_missing_or_late_guard` and, once by hand
on 2026-11-12, by deleting the guard line from tools/land.sh (red, naming the file).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
from _own_files import own_source_files
from conftest import REPO_ROOT, hermetic_env

GUARD = re.compile(
    r'\. "(?P<src>\$GOH|\$\(dirname "\$\{BASH_SOURCE\[0\]\}"\)(?:/\.\.)*)'
    r'/lib/bash5\.sh" && goh_bash5 "\$@"'
)
# Files that are not goh's shell, each with its reason; a stale entry fails (_scope()).
EXEMPT = {
    "lib/bash5.sh": "the guard itself",
    "tests/fixtures/rust_gate_legacy.sh": "a byte-frozen fixture of the pre-N3 rust gate",
}
HOOK = re.compile(r"^(?:\.githooks|hooks)/[^/]+$")
# A hook is COPIED into consumers and finds goh through GOH_DIR, which may predate lib/bash5.sh: a
# `.` of a missing file ends a non-interactive bash, and every commit was refused with no reason.
HOOK_FORM = (
    '[ ! -f "$GOH/lib/bash5.sh" ] || { . "$GOH/lib/bash5.sh" && goh_bash5 "$@"; }'
    "   # an older GOH has none"
)
OLD = Path("/bin/bash")


def _is_bash(f: str) -> bool:
    if f.endswith(".sh"):
        return True
    first = (REPO_ROOT / f).read_text(errors="replace").split("\n", 1)[0]
    return first.startswith("#!") and "bash" in first


def _scope() -> list[str]:
    files = [f for f in own_source_files() if _is_bash(f)]
    stale = sorted(set(EXEMPT) - set(files))
    assert not stale, f"EXEMPT names files that are gone: {stale}"
    return [f for f in files if f not in EXEMPT]


def tui_form(src: str) -> str:
    """tui/ is self-contained (its header): tests and vendored copies carry it without lib/."""
    lib = f"{src}/lib/bash5.sh"
    return f'[ ! -f "{lib}" ] || {{ . "{lib}" && goh_bash5 "$@"; }}   # a lone tui/ copy has none'


PRELUDE = re.compile(r"^(?:\{ # parse-guard.*|set -[a-z]+(?: [a-z]+)*|[A-Za-z_]\w*=[^`]*)$")


def findings(path: str, text: str) -> list[str]:
    """Why `text` (the file at repo-relative `path`) skips the guard or runs it late; [] if not."""
    lines = text.splitlines()
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = GUARD.search(s)
        if m and HOOK.match(path) and s != HOOK_FORM:
            return [f"{path}:{i}: a hook runs the guard only in its tolerant form: {HOOK_FORM}"]
        if m and path.startswith("tui/") and s != tui_form(m.group("src")):
            return [f"{path}:{i}: tui/ runs the guard only in its tolerant form"]
        if m:
            src = m.group("src")
            want = "$GOH" if HOOK.match(path) else '$(dirname "${BASH_SOURCE[0]}")'
            up = os.path.relpath(".", os.path.dirname(path) or ".")
            if not HOOK.match(path):
                want += "" if up == "." else "/" + up
            return [] if src == want else [f"{path}:{i}: sources {src}/lib/bash5.sh, not {want}"]
        if not PRELUDE.match(s) or "$(" in s:
            return [f"{path}:{i}: runs before the bash 5 guard: {s[:60]}"]
    return [f"{path}: never runs the bash 5 guard"]


def test_the_gate_sees_a_missing_or_late_guard():
    ok = (
        "#!/usr/bin/env bash\n# x\n{ # parse-guard\n"
        '. "$(dirname "${BASH_SOURCE[0]}")/../lib/bash5.sh" && goh_bash5 "$@"\nset -e\n'
    )
    assert findings("gates/x.sh", ok) == []
    assert findings("gates/x.sh", ok.replace('. "$(dirname', '# . "$(dirname')) == [
        "gates/x.sh: never runs the bash 5 guard"
    ]
    late = ok.replace("{ # parse-guard\n", "{ # parse-guard\nmapfile -t a < <(ls)\n")
    assert findings("gates/x.sh", late)[0].startswith("gates/x.sh:4: runs before")
    assert findings("tools/release-kit/x.sh", ok)[0].endswith(
        'not $(dirname "${BASH_SOURCE[0]}")/../..'
    )
    hook = (
        '#!/bin/bash\n{ # parse-guard\nset -euo pipefail\nGOH="${GOH_DIR:-x}"\n' + HOOK_FORM + "\n"
    )
    assert findings("hooks/pre-push", hook) == []
    bare = hook.replace(HOOK_FORM, '. "$GOH/lib/bash5.sh" && goh_bash5 "$@"')
    assert "tolerant form" in findings("hooks/pre-push", bare)[0]
    tui = "# x\n" + tui_form('$(dirname "${BASH_SOURCE[0]}")/..') + "\n"
    assert findings("tui/lib.sh", tui) == []
    assert "tolerant form" in findings("tui/lib.sh", ok)[0]
    assert findings("hooks/pre-push", hook.replace('GOH="${GOH_DIR:-x}"', 'GOH="$(pwd)"'))


def test_every_tracked_bash_file_runs_the_guard_first():
    files = _scope()
    assert len(files) >= 90, files  # 92 on 2026-11-12: a scope that stopped reading is red
    bad = [b for f in files for b in findings(f, (REPO_ROOT / f).read_text(errors="replace"))]
    assert not bad, "bash 5 guard missing or late (docs/contracts.md section 28):\n" + "\n".join(
        bad
    )


def _old_bash() -> str:
    if not OLD.exists():
        pytest.skip("no /bin/bash")
    v = subprocess.run([str(OLD), "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True,
                       text=True, check=True, stdin=subprocess.DEVNULL).stdout.strip()  # fmt: skip
    if int(v) >= 5:
        pytest.skip(
            f"/bin/bash is bash {v}: no old bash on this machine (GOH_BASH_MAJOR covers it)"
        )
    return str(OLD)


def test_every_guarded_file_parses_under_bash_3_2_so_the_guard_runs_first():
    old = _old_bash()
    bad = []
    for f in _scope():
        r = subprocess.run([old, "-n", str(REPO_ROOT / f)], capture_output=True, text=True,
                           check=False, stdin=subprocess.DEVNULL)  # fmt: skip
        if r.returncode:
            bad.append(f"{f}: {r.stderr.strip()[:120]}")
    assert not bad, "bash 3.2 cannot parse these, so their guard never runs:\n" + "\n".join(bad)


def _script(tmp_path: Path, body: str = "") -> Path:
    s = tmp_path / "t.sh"
    s.write_text(
        "#!/usr/bin/env bash\n{ # parse-guard\n"
        f'. "{REPO_ROOT}/lib/bash5.sh" && goh_bash5 "$@"\n'
        "set -euo pipefail\n" + body + "a=()\n"
        'printf "v=%s n=%s" "${BASH_VERSINFO[0]}" "$#"; printf " [%s]" "$@" "${a[@]}"; echo\n'
        'read -r line || true; echo "stdin=$line marker=${GOH_BASH_REEXEC-unset}"\nexit\n}\n'
    )
    return s


def _run(argv: list[str], **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False, input="hi\n",
                          env=hermetic_env(**env), timeout=60)  # fmt: skip


def test_old_bash_reexecs_under_bash_5_keeping_args_and_stdin(tmp_path):
    old = _old_bash()
    r = _run([old, str(_script(tmp_path)), "a b", "", "c"])
    assert r.returncode == 0, r.stderr
    # the bare empty array under set -u ran: bash 5 ran the body, not 3.2
    assert r.stdout.splitlines() == ["v=5 n=3 [a b] [] [c]", "stdin=hi marker=unset"], r.stdout


def test_old_bash_with_no_bash_5_dies_naming_the_fix(tmp_path):
    old = _old_bash()
    r = _run([old, str(_script(tmp_path))], GOH_BASH_CANDIDATES=old)
    assert r.returncode == 1 and r.stdout == ""
    assert "needs bash >= 5" in r.stderr and "brew install bash" in r.stderr, r.stderr
    assert "✗" in r.stderr


def test_the_old_bash_seam_reexecs_on_any_machine(tmp_path):
    """GOH_BASH_MAJOR: 'old bash' where no 3.2 exists (Linux), dropped before the re-exec."""
    r = _run([bash5(), str(_script(tmp_path)), "x"], GOH_BASH_MAJOR="3")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[0] == "v=5 n=1 [x]"
    r = _run([bash5(), str(_script(tmp_path))], GOH_BASH_MAJOR="3", GOH_BASH_CANDIDATES="/x")
    assert r.returncode == 1 and "none was found in /x" in r.stderr, r.stderr


def bash5() -> str:
    """The bash >= 5 the guard itself finds (lib/bash5.sh run directly), whatever PATH's is."""
    r = _run(["bash", str(REPO_ROOT / "lib/bash5.sh")])
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def test_on_bash_5_the_guard_is_a_no_op(tmp_path):
    r = _run([bash5(), str(_script(tmp_path)), "y"], GOH_BASH_CANDIDATES="/nonexistent")
    assert r.returncode == 0 and r.stdout.splitlines()[0] == "v=5 n=1 [y]", r.stderr
    assert r.stderr == ""


def test_a_bash_that_lies_about_its_version_is_refused_not_looped(tmp_path):
    old = _old_bash()
    liar = tmp_path / "liar"
    liar.write_text(f'#!/bin/sh\ncase "$1" in -c) printf 5 ;; *) exec {old} "$@" ;; esac\n')
    liar.chmod(0o755)
    r = _run([old, str(_script(tmp_path))], GOH_BASH_CANDIDATES=str(liar))
    assert r.returncode == 1 and "refused rather than re-run again" in r.stderr, r.stderr


def test_a_sourced_library_never_reexecs_and_says_nothing(tmp_path):
    old = _old_bash()
    s = tmp_path / "s.sh"
    s.write_text(f'. "{REPO_ROOT}/tui/lib.sh"\n. "{REPO_ROOT}/lib/exit_reason.sh"\n'
                 'echo "v=${BASH_VERSINFO[0]}"\n')  # fmt: skip
    r = _run([old, str(s)])
    assert r.returncode == 0 and r.stdout == "v=3\n" and r.stderr == "", (r.stdout, r.stderr)
    r = _run([bash5(), str(s)])
    assert r.returncode == 0 and r.stdout == "v=5\n" and r.stderr == "", r.stderr


def test_run_directly_it_names_the_bash_it_would_use():
    r = _run(["bash", str(REPO_ROOT / "lib/bash5.sh")])
    assert r.returncode == 0 and Path(r.stdout.strip()).is_file(), r.stderr
    r = _run(["bash", str(REPO_ROOT / "lib/bash5.sh")], GOH_BASH_CANDIDATES="/nonexistent")
    assert r.returncode == 1 and "no bash >= 5 found" in r.stderr, r.stderr
