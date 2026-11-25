# GOH_LAND_PRE_KEEP
Area: pushes
Kind: setting
Order: 120
Default: unset

Under `GOH_LAND_PRE_ONLY`, where `tools/land.sh` keeps a green pre-step's verdict: the judged sha in
the file, its record beside it as `.record`; a deadline (no verdict) keeps nothing. `goh land
--enqueue --land` names a file under `<landq>/pre/` and puts it on the request as `pre`
(GOH.PRELANDTWICE). Rebased in a slot (`GOH_LAND_PRE_ONTO`), the file's second line is the main the
judged tip sits on, and the tip is pinned as `refs/goh/pre/<file name>` until the request is
recorded (GOH.PREONTIP). Internal: the queue sets it.
