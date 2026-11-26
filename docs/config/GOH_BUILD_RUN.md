# GOH_BUILD_RUN
Area: runtime
Kind: setting
Default: unset (a lone pytest run is its own run)

The directory of the run whose builds are once (`lib/build_once.py`, GOH.BUILDONCE): `goh land
--check` and `tools/affected_tests.py --run` make it and hand it to every pytest run they start, so
the goh test binary (`tests/conftest.py` `native_goh`) is built by the first and taken by the rest;
a second build of one (kind, tree) in it is refused, naming both callers. Set by the owner, never
by hand; an inherited one is reused and left for its owner to remove.
