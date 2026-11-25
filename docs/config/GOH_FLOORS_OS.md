# GOH_FLOORS_OS, GOH_FLOORS_ARCH, GOH_FLOORS_URL, GOH_FLOORS_TSV, GOH_FLOORS_SUMS
Area: runtime
Kind: setting
Order: 480
Default: the host (`uname -s`/`-m`); GitHub's shellcheck releases; `gates/required_tools.tsv`; `.github/actions/goh-floors/shellcheck.sha256`

Test seams of `.github/actions/goh-floors/floors.sh` (G7), the one way CI meets goh's floors: the
runner platform (Linux x86_64/aarch64 and macOS on Apple silicon, anything else refused), the
release base a shellcheck is downloaded from, the floor table and the verified checksums. Not
consumer config.
