# GOH_TMP_LEAK_ALLOW
Area: local-ci
Kind: setting
Order: 150
Default: empty

Space-separated globs a `GOH_TMP_LEAKS=red` repo still leaks (matched against a top-level name, or
`goh.noindex/<name>`): named nowhere, failed never. A ratchet: in a run where every step ran green,
a glob that matched nothing fails (`stale entry`), so the list only shrinks. Read from `.gatesrc`
only.
