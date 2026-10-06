"""goh_step's failure block names the step that FAILED, never a passing sibling's `✗` (BACKLOG C5).

ZoneWM H2, 2026-10-05: the pre-push summary of a red `make verify-clean` listed a calibration
plant's quoted `✗` row as a failure. The plant had exited 0; its `✗` was the point of it. The grep
read the whole log of the outer step, so every matching line any sub-step printed was quoted as the
reason, whether that sub-step passed or not.

Two shapes, two answers:
* NESTED house steps: an inner goh_step that failed has already printed its own failure block. That
  block is the attribution; the outer quotes it alone, and says how many other matching lines it
  left out (they came from sub-steps that exited 0).
* An OPAQUE command (one `make` running many recipes) frames nothing, so its lines cannot be
  attributed. The block says so instead of asserting, and names the failing sub-step when the
  command itself did (make's `*** [target] Error N`).
"""

import subprocess
import textwrap
from pathlib import Path

from conftest import REPO_ROOT

PLANT = "✗ PLANTED-ROW quoted by a calibration plant"
REAL = "✗ REAL-FAILURE-7781"


def _run(tmp_path: Path, body: str) -> subprocess.CompletedProcess:
    script = textwrap.dedent(f"""
        cd '{tmp_path}'
        . '{REPO_ROOT}/gates/_common.sh'
        goh_init outer
        {body}
    """)
    return subprocess.run(
        ["/bin/bash", "-c", script], capture_output=True, text=True, timeout=60, check=False
    )


def _block(stderr: str) -> str:
    """The OUTER step's failure block: the first one printed, up to its tail marker."""
    start = stderr.index("── failure lines")
    return stderr[start : stderr.index("── tail ──", start)]


def test_nested_failure_quotes_the_innermost_failing_step_only(tmp_path):
    inner = tmp_path / "inner.sh"
    inner.write_text(
        textwrap.dedent(f"""
            . '{REPO_ROOT}/gates/_common.sh'
            goh_init inner
            echo '{PLANT}'
            goh_step "plant check" /bin/bash -c "echo '{PLANT}'; exit 0"
            goh_step "real check" /bin/bash -c "echo '{REAL}'; exit 1"
        """)
    )
    r = _run(tmp_path, f'goh_step "make verify" /bin/bash {inner}')
    assert r.returncode != 0
    block = _block(r.stderr)
    assert "REAL-FAILURE-7781" in block, block
    assert "PLANTED-ROW" not in block, f"a passing sibling's ✗ was listed as the failure:\n{block}"
    assert "real check" in block, f"the innermost failing step is not named:\n{block}"
    assert "left out: 1 other matching line" in block, (
        f"the left-out lines are not flagged:\n{block}"
    )
    assert "PLANTED-ROW" in r.stderr, "the tail must still carry the whole log"


def test_opaque_command_flags_its_lines_and_names_makes_failing_target(tmp_path):
    (tmp_path / "Makefile").write_text(
        f"verify: plant real\nplant:\n\t@echo '{PLANT}'\nreal:\n\t@echo '{REAL}'; exit 1\n"
    )
    r = _run(tmp_path, 'goh_step "make verify" make verify')
    assert r.returncode != 0
    block = _block(r.stderr)
    assert "[real] Error 1" in block or "real] Error 1" in block, (
        f"make named its failing recipe and the block dropped it:\n{block}"
    )
    assert "exited 0" in block, f"an unframed log's matches are asserted, not flagged:\n{block}"
    assert "REAL-FAILURE-7781" in block


def test_a_plain_failing_step_keeps_the_plain_block(tmp_path):
    """No nesting, no make: the block is the grep it always was, with no flag noise."""
    r = _run(tmp_path, f"goh_step suite /bin/bash -c \"echo '{REAL}'; exit 1\"")
    assert r.returncode != 0
    block = _block(r.stderr)
    assert "REAL-FAILURE-7781" in block
    assert "exited 0" not in block and "other matching" not in block, block


def test_three_deep_names_the_deepest_and_still_counts_the_plant(tmp_path):
    """An enclosing step's dump carries the inner one in its tail, so the outer log holds two
    dumps; the LAST is the innermost. The plant printed between them is still a passing
    sibling's line at every level (red against a first-dump-to-last-die exclusion, which hid it)."""
    mid = tmp_path / "mid.sh"
    mid.write_text(
        textwrap.dedent(f"""
            . '{REPO_ROOT}/gates/_common.sh'
            goh_init mid
            echo '{PLANT}'
            goh_step deepest /bin/bash -c "echo '{REAL}'; exit 1"
        """)
    )
    top = tmp_path / "top.sh"
    top.write_text(
        f". '{REPO_ROOT}/gates/_common.sh'\ngoh_init top\ngoh_step middle /bin/bash {mid}\n"
    )
    r = _run(tmp_path, f"goh_step outer /bin/bash {top}")
    assert r.returncode != 0
    block = _block(r.stderr)
    assert "innermost step that failed: deepest" in block, block
    assert "PLANTED-ROW" not in block, block
    assert "left out: 1 other matching line" in block, block


def test_a_timeout_reason_travels_with_the_innermost_block(tmp_path):
    inner = tmp_path / "inner.sh"
    inner.write_text(
        f". '{REPO_ROOT}/gates/_common.sh'\ngoh_init inner\necho '{PLANT}'\n"
        f"GOH_STEP_TIMEOUT=1 goh_step stuck /bin/bash -c \"echo '{REAL}'; sleep 30\"\n"
    )
    r = _run(tmp_path, f"goh_step outer /bin/bash {inner}")
    assert r.returncode != 0
    block = _block(r.stderr)
    assert "TIMED OUT after 1s" in block, (
        f"the reason printed before the inner header was cut:\n{block}"
    )
    assert "innermost step that failed: stuck" in block and "PLANTED-ROW" not in block, block


def _lib():
    import importlib.util

    spec = importlib.util.spec_from_file_location("fail_lines", REPO_ROOT / "lib" / "fail_lines.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_block_unit_edges(tmp_path, capsys):
    fl = _lib()
    assert fl.block("all good\nExecuted 3 tests, with 0 failures\n") == ""
    colored = fl.block("\x1b[31m✗\x1b[0m boom\n")
    assert "1:✗ boom" in colored, "colour codes must not hide a match"
    capped = fl.block("".join(f"✗ {i}\n" for i in range(9)), cap=3)
    assert "3:✗ 2" in capped and "4:✗ 3" not in capped
    unnamed = fl.block("── failure lines (grep x) ──\n1:✗ inner\n")
    assert "a step that printed no name" in unnamed
    log = tmp_path / "log"
    log.write_text("✗ boom\n")
    assert fl.main(["fail_lines.py", str(log)]) == 0
    assert "1:✗ boom" in capsys.readouterr().out
    assert fl.main(["fail_lines.py"]) == 2
