# GOH_PY
Area: python
Kind: setting
Order: 20
Default: unset: the NEWEST `python3`/`python3.N` on `PATH` or in a release's install dirs (`/opt/homebrew/bin`, `/usr/local/bin`, `/home/linuxbrew/.linuxbrew/bin`, `~/.local/bin`), whatever the PATH order

The HOUSE interpreter goh runs its own helpers on (`lib/house_python.sh`, G2d), exported by every
gate. Set, it names one exactly, still judged against the python3 floor in
`gates/required_tools.tsv`; a machine whose newest is below the floor is refused before any step
runs, naming the floor, what was found and the fix. Never PATH's first `python3`: under a reduced
PATH that is macOS's 3.9.6.
