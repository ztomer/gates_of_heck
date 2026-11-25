# GOH_RELEASE_GATE
Area: release-kit
Kind: setting
Order: 10
Default: unset (required)

Gate command a release must pass: declared in `.gatesrc`, or exported (`--gate CMD` beats both, the
environment beats `.gatesrc`).
