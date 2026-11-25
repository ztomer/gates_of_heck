# GOH_BASH_CANDIDATES
Area: bash
Kind: setting
Order: 10
Default: macOS `/opt/homebrew/bin/bash`; Linux `/usr/bin/bash:/bin/bash`; then PATH's bash

Colon-separated bash paths the guard asks, in order, for one whose own `BASH_VERSINFO` is >= 5. A
test seam: `=/bin/bash` is "no bash 5 found".
