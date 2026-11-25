# GOH_FLOORS_FETCH, GOH_FLOORS_ATTEMPTS, GOH_FLOORS_BACKOFF, GOH_FLOORS_CACHE
Area: runtime
Kind: setting
Order: 500
Default: curl; `4`; `5`; unset (the action sets `$RUNNER_TEMP/goh-floors/cache`)

The shellcheck download, made survivable (GOH.FLOORSRETRY): one attempt's command (`<cmd> <url>
<out>`, a test seam), the bounded number of attempts, the first pause in seconds (doubling: 5 + 10 +
20 s), and the tarball cache, which `actions/cache` keeps across runs under `floors.sh cache-key`
(version, platform, sha256) and floors.sh re-verifies on every use. An exhausted retry fails naming
the download as INFRASTRUCTURE, not the repository.
