# GOH_ESTATE_HOME
Area: runtime
Kind: internal
Default: unset

env, `checks/check_estate_corpus.py`: stands in for the home of the estate corpora's `~/` roots, and
has no private store. The empty-scope sweep names an empty one, so the gate is asked about an estate
with nothing in it (GOH.FSEVQUIET).
