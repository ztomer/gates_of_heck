# GOH_ESTATE_CACHE
Area: runtime
Kind: setting
Order: 400
Default: `~/.cache/goh/estate-corpus`

Where `check_estate_corpus.py` remembers a VERIFIED entry, keyed on every input its verdict depends
on (`checks/_estate_cache.py`); `off`, `0`, `false` or `no` disables it, as `GOH_PROVEN=0` does, and
so does a relative path, which would name the tree the gate runs in.
