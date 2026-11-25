# GOH_PY_NOQA_PARITY
Area: python
Kind: setting
Order: 160
Default: unset (no parity check)

The linters the repo runs beside ruff, space- or comma-separated; `pyflakes` is the one it reads
(`gates/py_noqa_parity.py`, BACKLOG 4.5). A line carrying a `# noqa` that such a linter still
reports fails, at commit over the staged files and at push over every tracked one: pyflakes reads no
`noqa`, so the directive silenced one gate and not the other. Any other name exits 2 naming this
key. The fix is the code, or one vocabulary (ruff's F rules without the second linter).
