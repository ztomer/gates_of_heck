# Every `GOH_*` key

Rendered, never written here (GOH.CONFIGMD): `goh config` prints the whole reference, `goh config
KEY` one key's declaration, the code that reads it and the tests that name it (`lib/config_ref.py`).
A key is one file, `docs/config/<KEY>.md`; an area's prose is `docs/config/_areas/<area>.md`. A new
key is a new file, refused until it is there (`goh config --check`, `tests/test_config_schema.py`):

    # GOH_X
    Area: structural
    Kind: setting
    Default: unset

    What it means, in one paragraph wrapped at 100. Kind is `internal` for the gates' own.
