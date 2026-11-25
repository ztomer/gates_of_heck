# GOH_PYTHON_VERSION
Area: python
Kind: setting
Order: 130
Default: unset: a committed `.python-version`, else no pin (`python3`)

The Python VERSION this repo's gates and hooks run (`3.14`, or `3.14.8`), through `gates/python.sh`
and the Python gates' runner (BACKLOG 6.2). Found with `uv python find`, else `python<version>` on
`PATH`, and read back: an interpreter, or a `.venv`, that is not this version is refused by name,
and so is a pin nothing installed satisfies (`uv python install <version>`). A version, never a
path: a Homebrew keg path moves on every upgrade, which is the failure this exists for.
