"""The Python coverage floor over EVERY file the repo holds, not only the ones a test imported.

    py_cov_floor.py <floor>     # run in the package dir, under the repo's own interpreter,
                                # after `pytest --cov=<pkg_dir>` has written the data file

WHY THIS EXISTS. coverage discovers unexecuted files only inside PACKAGES (directories with an
`__init__.py`), so a script in a plain directory -- ZeroThunder's `asset_factory/creatures/`,
any `tools/` -- did not exist for the floor until a test first imported it. Then that test
LOWERED the total: ZeroThunder read 67.03% against a 68 floor with no test failing, because its
first test of `grade_shrub_native.py` pulled 347 never-measured statements into the denominator
(2026-10-18). Adding a test turned the gate red, and an untested module stayed invisible.

`[report] include_namespace_packages` finds those scripts, and also every `.py` in `.venv/`,
`build/` and `node_modules/`, so the denominator here is the repo's own files instead: tracked,
plus new files git would not ignore (so a module is counted before its first `git add`). Each one
no test touched is recorded in the data as measured with no lines run -- then coverage's own
report, the repo's own omit lists and coverage's own fail-under rule decide, so nothing about
what "covered" means is re-implemented here.
"""

if __name__ == "__main__":  # C4: run HEAD's copy, not the shared working tree (gates/_from_head.py)
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "gates"))
    try:
        __import__("_from_head").reexec(__file__)
    except ModuleNotFoundError:  # a copy outside any checkout: nothing to re-run from
        pass
    del _sys.path[0]

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "checks"))
import coverage
from _gitutil import foreign_repo_env
from coverage.files import GlobMatcher, prep_patterns
from coverage.results import should_fail_under

try:
    from tui.lib import err, info, warn
except ImportError:  # the repo's interpreter may not see goh; plain lines keep the gate honest

    def err(message):
        print(f"✗ {message}", file=sys.stderr)

    def info(message):
        print(f"→ {message}")

    def warn(message):
        print(f"⚠ {message}", file=sys.stderr)


def repo_files(root):
    """Every .py under `root` that is the repo's: tracked, or new and not ignored."""
    proc = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.py"],
        cwd=root,
        env=foreign_repo_env(),  # the tree on disk, never a hook's GIT_DIR (a push export's differs)
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"py_cov_floor: {root} is not a git work tree ({proc.stderr.strip()}); the floor's "
            "denominator is the repo's own files, and without git there is no list of them"
        )
    return sorted(
        os.path.realpath(os.path.join(root, rel))
        for rel in proc.stdout.split("\0")
        if rel and os.path.isfile(os.path.join(root, rel))
    )


def unseen(cov, root):
    """The repo's files no test touched, minus everything the repo omits."""
    measured = {os.path.realpath(path) for path in cov.get_data().measured_files()}
    patterns = (cov.config.run_omit or []) + (cov.config.report_omit or [])
    omitted = GlobMatcher(prep_patterns(patterns), "omit")
    # A repo that narrows what it measures with `include` is narrowed here the same way.
    narrowed = (cov.config.run_include or []) + (cov.config.report_include or [])
    included = GlobMatcher(prep_patterns(narrowed), "include") if narrowed else None
    return [
        path
        for path in repo_files(root)
        if path not in measured
        and not omitted.match(path)
        and (included is None or included.match(path))
    ]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        raise SystemExit("usage: py_cov_floor.py <floor>")
    floor = float(argv[0])
    cov = coverage.Coverage(config_file=True)
    cov.load()
    missing = unseen(cov, os.getcwd())
    if missing:
        data = cov.get_data()
        data.touch_files(missing)
        data.write()
        warn(f"{len(missing)} file(s) no test imports, counted at 0%:")
        for path in missing:
            print(f"    {os.path.relpath(path)}", file=sys.stderr)
    total = cov.report(show_missing=True, file=sys.stdout)
    precision = cov.config.precision
    if should_fail_under(total, floor, precision):
        err(
            f"coverage {total:.{precision}f}% is below the floor {floor:g}% (every repo file counted)"
        )
        return 1
    info(f"coverage {total:.{precision}f}% >= {floor:g}% over every repo file")
    return 0


if __name__ == "__main__":
    sys.exit(main())
