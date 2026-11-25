"""What goh's suite refuses to start without, asked in one process (lib/suite_preflight.py).

tools/pytest.sh asked each question of an interpreter of its own, 1.9 s of a one-file run. Every
refusal it made is here, red when it is removed; the runner's half (the refusal stops the run, and
an import that KILLS the process is named) is driven through tools/pytest.sh itself.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from timing_bounds import patience

sys.path += [str(REPO_ROOT / "gates"), str(REPO_ROOT / "lib")]
import required_pins
import required_tools
import ruff_agree
import suite_preflight
import suite_width

ROWS = [r for r in required_tools.rows() if r["layer"] == "goh-suite"]


class Said(list):
    def __call__(self, key: str, value: str) -> None:
        self.append((key, value))

    def of(self, key: str) -> list[str]:
        return [v for k, v in self if k == key]


@pytest.fixture
def green(monkeypatch):
    """Every judge's answer but the one under test: this box's, whatever it is."""
    monkeypatch.setattr(suite_preflight, "pins", lambda: "")
    monkeypatch.setattr(suite_preflight, "rufs", lambda: "")
    monkeypatch.setattr(suite_preflight, "imports", lambda say: "")


# --- the modules ---------------------------------------------------------------------------------


def test_every_module_the_manifest_pins_for_the_suite_is_imported():
    pinned = {r["tool"] for r in ROWS if r["kind"] == "pip"}
    assert {dist for dist, _ in suite_preflight.MODULES} == pinned - {"ruff"}  # asked as `-m ruff`


def test_this_interpreter_passes_and_imports_every_one():
    said, loaded = Said(), []
    assert suite_preflight.imports(said, loaded.append) == ""
    assert loaded == [m for _, m in suite_preflight.MODULES]
    assert suite_preflight.imports(Said()) == "", "the real imports, on the box that runs the suite"


@pytest.mark.parametrize(("dist", "module"), suite_preflight.MODULES)
def test_a_missing_module_is_refused_by_its_distribution(dist, module, monkeypatch, green):
    def load(name: str) -> None:
        if name == module:
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)

    monkeypatch.undo()  # `green` replaced imports
    monkeypatch.setattr(suite_preflight, "pins", lambda: "")
    monkeypatch.setattr(suite_preflight, "rufs", lambda: "")
    real = suite_preflight.imports
    monkeypatch.setattr(suite_preflight, "imports", lambda say: real(say, load))
    said = Said()
    assert suite_preflight.main([], said) == 1
    [why] = said.of("refuse")
    assert why.startswith(f"1 {dist} is not installed for the house interpreter"), why
    assert why.endswith(f"-m pip install {dist}"), why
    assert said.of("at")[-1] == dist, "what a killed import is named by"


@pytest.mark.parametrize("raised", [ImportError("wrong ABI"), OSError("no dylib"), SystemExit(3)])
def test_an_import_that_fails_is_refused_though_the_distribution_is_there(raised):
    """What the import is for: metadata alone would pass a wheel built for another python."""

    def load(name: str) -> None:
        if name == "numpy":
            raise raised

    why = suite_preflight.imports(Said(), load)
    assert why.startswith("numpy is not installed") and type(raised).__name__ in why, why


def test_a_broken_leaf_is_named_not_what_imports_it():
    order = [m for _, m in suite_preflight.MODULES]
    assert order.index("pluggy") < order.index("pytest") < order.index("pytest_cov")
    assert order.index("execnet") < order.index("xdist")


# --- the pins, the plugins, the rufs, the table ---


@pytest.mark.parametrize("found", ["9.1.1", ""])
def test_a_module_off_its_pin_is_refused(monkeypatch, capsys, found):
    """GOH.PYTESTPIN: pytest 9.1.1 on the box, 9.2.0 in hosted CI."""
    real = required_pins.metadata.version

    def version(name: str) -> str:
        if name != "pytest":
            return real(name)
        if not found:
            raise required_pins.metadata.PackageNotFoundError(name)
        return found

    monkeypatch.setattr(required_pins.metadata, "version", version)
    monkeypatch.setattr(suite_preflight, "rufs", lambda: "")
    said = Said()
    assert suite_preflight.main([], said) == 1
    assert "are not the manifest's pins" in said.of("refuse")[0], said
    assert f"pytest is {found or 'not installed'}" in capsys.readouterr().err


def test_the_plugins_are_the_pins_own(green):
    said = Said()
    assert suite_preflight.main([], said) == 0
    want = ",".join(mod for mod, _ in required_pins.manifest_plugins(ROWS))
    assert said.of("plugins") == [want] and "xdist.plugin" in want, said


def test_plugins_that_cannot_be_read_stop_the_run(green, monkeypatch):
    monkeypatch.setattr(suite_preflight, "plugins", lambda: 1 / 0)
    said = Said()
    assert suite_preflight.main([], said) == 1
    assert said.of("refuse")[0].startswith("2 cannot read the goh-suite pins' pytest plugins")


def _rufs(monkeypatch, house: str | None, path: str | None) -> Said:
    pin = required_tools.pin("ruff")
    every = [("py -m ruff", None, house and house.replace("PIN", pin))]
    every += [("/x/ruff", Path("/x/ruff"), path.replace("PIN", pin))] if path else []
    monkeypatch.setattr(ruff_agree, "invocations", lambda py, where: every)
    monkeypatch.setattr(suite_preflight, "imports", lambda say: "")
    monkeypatch.setattr(suite_preflight, "pins", lambda: "")
    said = Said()
    assert suite_preflight.main([], said) == bool(said.of("refuse"))
    return said


def test_a_missing_ruff_is_refused_by_name(monkeypatch):
    [why] = _rufs(monkeypatch, None, "PIN").of("refuse")
    assert why.startswith("1 ruff is not installed for the house interpreter"), why


@pytest.mark.parametrize(("house", "path"), [("0.1", "PIN"), ("PIN", "0.1"), ("0.1", "0.1")])
def test_any_ruff_that_is_not_gohs_pin_is_refused(monkeypatch, house, path, capsys):
    [why] = _rufs(monkeypatch, house, path).of("refuse")
    assert "are not goh's pinned ruff" in why, why
    assert "ruff 0.1\n" in capsys.readouterr().err, "the one that is off is named"


def test_the_pinned_ruff_everywhere_is_no_refusal(monkeypatch):
    assert _rufs(monkeypatch, "PIN", "PIN").of("refuse") == []


def test_the_rufs_asked_are_the_machines(tmp_path, monkeypatch):
    """Not a list the test handed it: a stray ruff first on PATH is asked its version."""
    stray = tmp_path / "ruff"
    stray.write_text("#!/bin/sh\necho ruff 0.0.1\n")
    stray.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    assert "are not goh's pinned ruff" in suite_preflight.rufs()


def test_only_a_top_level_run_is_named_the_duration_table(green, monkeypatch, tmp_path):
    monkeypatch.setenv("GOH_TEST_DURATIONS", str(tmp_path / "t.db"))
    said = Said()
    assert suite_preflight.main(["--table"], said) == 0
    assert said.of("table") == [str(tmp_path / "t.db")]
    nested = Said()
    assert suite_preflight.main([], nested) == 0
    assert nested.of("table") == []


def test_a_table_that_cannot_be_named_stops_the_run(green, monkeypatch):
    from lib import fast_tests

    monkeypatch.setattr(fast_tests, "cache", lambda: 1 / 0)
    said = Said()
    assert suite_preflight.main(["--table"], said) == 1
    assert said.of("refuse")[0].startswith("2 cannot resolve the suite's duration table")


def test_a_width_that_cannot_be_read_is_the_ceiling_never_a_refusal(green, monkeypatch):
    monkeypatch.setattr(suite_width, "width", lambda args, root: 1 / 0)
    said = Said()
    assert suite_preflight.main(["--", "tests/test_x.py"], said) == 0
    assert said.of("width") == []


# --- the runner's half ---


def _runner(tmp_path: Path, preflight: str, *args: str, **env: str):
    """tools/pytest.sh over a stub interpreter whose preflight is `preflight` (a shell body); what
    pytest was started with is in `tmp_path/ran`."""
    stub = tmp_path / "python3"
    stub.write_text(
        "#!/bin/bash\n"
        f'case "$*" in *suite_preflight.py*) {preflight} ;;\n'
        f'  *worker_share.py*) exec "{sys.executable}" "$@" ;; esac\n'
        '[ "$1 $2" = "-m pytest" ] || exit 0\n'
        f'printf "%s\\n" "plugins=$PYTEST_PLUGINS" "autoload=$PYTEST_DISABLE_PLUGIN_AUTOLOAD" '
        f'"pool=${{GOH_SHARE_POOL:-}}" "$@" >"{tmp_path}/ran"\n'
    )
    stub.chmod(0o755)
    full = {k: v for k, v in os.environ.items() if not k.startswith("GOH_SHARE_")}
    full.update(GOH_PY=str(stub), GOH_PY_OK=str(stub), GATES_OF_HECK_SUITE_NESTED_OK="1",
                TMPDIR=str(tmp_path / "tmp"), GOH_GATE_CORES="16",
                GOH_GATE_SLOT_DIR=str(tmp_path / "slots"), **env)  # fmt: skip
    (tmp_path / "tmp").mkdir()
    done = subprocess.run(["bash", str(REPO_ROOT / "tools" / "pytest.sh"), *args], env=full,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
                          timeout=patience(60))  # fmt: skip
    ran = tmp_path / "ran"
    return done, ran.read_text().splitlines() if ran.exists() else None


@pytest.mark.parametrize("code", ["1", "2"])
def test_a_refusal_stops_the_run_before_pytest_and_leaves_no_temp_root(tmp_path, code):
    done, ran = _runner(tmp_path, f'printf "at\\tnumpy\\nrefuse\\t{code} numpy is off\\n"; exit 1')
    assert done.returncode == int(code) and ran is None, done.stdout + done.stderr
    assert "numpy is off" in done.stderr, done.stderr
    assert not list((tmp_path / "tmp").rglob("goh-pytest.*")), "a refused run kept a root"


@pytest.mark.parametrize(
    ("body", "named"),
    [('printf "at\\tnumpy\\n"; kill -ABRT $$', "importing numpy"), ("exit 3", "exit 3")],
)
def test_a_preflight_that_dies_is_never_a_pass(tmp_path, body, named):
    done, ran = _runner(tmp_path, body)
    assert done.returncode == 1 and ran is None, done.stdout + done.stderr
    assert "the suite's preflight died" in done.stderr and named in done.stderr, done.stderr


def test_an_import_that_takes_the_interpreter_down_is_named(tmp_path):
    """The real preflight, a real crash: the import's own output and death are both survived."""
    (tmp_path / "pluggy.py").write_text(
        "import os\nprint('refuse\\t1 not an answer', flush=True)\nos.abort()\n"
    )
    done, ran = _runner(tmp_path, f'exec "{sys.executable}" "$@"', PYTHONPATH=str(tmp_path))
    assert done.returncode == 1 and ran is None, done.stdout + done.stderr
    assert "importing pluggy" in done.stderr, done.stderr
    assert "✗ not an answer" not in done.stdout + done.stderr, "an import's print is no answer"


def test_what_it_answers_reaches_pytest(tmp_path):
    body = 'printf "at\\t\\nplugins\\ta.b,c\\nwidth\\t3\\n"; exit 0'
    done, ran = _runner(tmp_path, body, "tests/test_plural_hedge.py")
    assert done.returncode == 0, done.stdout + done.stderr
    assert ran[:2] == ["plugins=a.b,c", "autoload=1"], ran
    assert ran[ran.index("-n") + 1] == "3" and ran[2] != "pool=", ran


def test_a_serial_run_starts_no_worker_and_no_pool(tmp_path):
    done, ran = _runner(tmp_path, 'printf "width\\t0\\n"; exit 0', "tests/test_plural_hedge.py")
    assert done.returncode == 0, done.stdout + done.stderr
    assert ran[ran.index("-n") + 1] == "0" and ran[2] == "pool=", ran


def test_the_ceiling_and_a_pinned_count_outrank_the_width(tmp_path):
    for name, width, env, want in (
        ("a", "40", {}, "12"), ("b", "0", {"GOH_SUITE_CORES": "5"}, "5"),
        ("c", "0", {}, "0"),
    ):  # fmt: skip
        (tmp_path / name).mkdir()
        _, ran = _runner(tmp_path / name, f'printf "width\\t{width}\\n"; exit 0', **env)
        assert ran[ran.index("-n") + 1] == want, (name, ran)
