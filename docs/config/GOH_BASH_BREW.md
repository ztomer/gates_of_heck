# GOH_BASH_BREW
Area: bash
Kind: setting
Order: 30
Default: on a GitHub Actions runner, PATH's `brew`; elsewhere none

The brew the guard runs `brew install bash` with when an executed script finds no bash >= 5. Empty
never installs. A test seam; the default is what fixes the hosted macOS runner (GOH.INSTALLBASH5).
