# GOH_LAND_WAIVE
Area: pushes
Kind: internal
Order: 310
Default: unset

Not a setting: the lander's waivable reds for one land.sh run (`lib/land_waive.py words`,
GOH.MAINREDWAIVE and GOH.RULEINFLIGHT), JSON: main's red tests per sha, each by the id of the goh.db
reading ON MAIN that saw it red (`lib/main_reading.py`: the idle full suite, or a probe of named
tests on a clean checkout of main), and the rule tests main gained after every item of the land
forked. The goh suite's plugin (`tests/_land_waive.py`) resolves it against the base its tree really
has (`git merge-base HEAD <main>`): a failing test red on main at THAT base is xfailed naming the
reading, a failing rule in flight is xfailed as a warning naming the rule (one land's grace: a
landed warning of the item spends it), and each is a `goh-waived:` line the land record keeps
(`waived`). A test green at the base has no reading and reds as before. It changes no cache key's
verdict (a Rust proof, an estate identity); taken out of the environment as the plugin loads.
