# GOH_PYTHON_FORMATTED
Area: structural
Kind: setting
Order: 200
Default: unset (check skipped)

Set to `1` to run `check_python_formatted.py`: every tracked `.py` must match what `ruff format`
produces, using the rule set the repo declares in `pyproject.toml` / `ruff.toml`. Opt-in per repo,
both scopes: `--staged` judges the INDEX blobs of the staged `.py` files (`ruff format --check
--force-exclude --stdin-filename`) and reformats nothing, so a commit is refused for its own bytes
only; full scope judges the tree. (It was full-only until v0.21.0, and two unformatted files passed
pre-commit and refused the v0.20.0 push.) The checker reads ruff's settings from the tree it runs
in, so with no configuration it answers from ruff's defaults and from the directory it was launched
from — which is why the flag exists rather than a default-on step. It refuses to skip when `ruff` is
absent: an uninstalled formatter is a missing gate, not a pass. WHICH ruff is the Python gates'
runner rule (`gates/_py.sh`): `GOH_PY_RUNNER`, else a TRACKED `uv.lock` runs `uv run --frozen ruff`
(`GOH_PYTHON_VERSION` adds `--python`), else a `.venv` runs `.venv/bin/python -m ruff`, else `ruff`
on `PATH` -- so the format verdict is the repo's pin, never the launching shell's. A tracked lock
with no `uv` on `PATH` is a named refusal, not a fall back to `PATH`'s ruff.
