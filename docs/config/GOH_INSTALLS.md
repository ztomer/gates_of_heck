# GOH_INSTALLS
Area: structural
Kind: setting
Order: 350
Default: unset (not judged)

What this repo installs on the Mac, read by the umbrella's installed-version check
(`~/Projects/.loop/installs.py`, LOOP.INSTALLED): space-separated artifacts, each an app bundle at
`/Applications/...X.app` or `~/Applications/...X.app` (read from `$HOME`), or a CLI by its bare name
(looked up in `~/.local/bin`, `/opt/homebrew/bin`), e.g. `GOH_INSTALLS='/Applications/Widget.app
widget'`. Every artifact must answer with the newest tag's version (an app's
`CFBundleShortVersionString`, a CLI's `--version`). `goh installs`, a structural step when the key
is present, refuses a form the reader cannot read: an empty value, an app anywhere else or with an
empty/`.`/`..` segment, a CLI given as a path, a name twice. Names with spaces are not representable
(the value splits on whitespace).
