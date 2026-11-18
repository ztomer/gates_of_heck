* An enqueue in its pre-step is visible to the lander (GOH.IDLEYIELDPRE, GOH.WAITUNFILED):
  `goh land --enqueue --land` marks `<landq>/enqueuing/<pid>-<item>.json` until it exits (a dead
  pid's marker counts as gone). The idle full suite stops for it within its poll period, as it
  does for a pending request, and `goh land --wait ID` started beside its own enqueue waits for
  the request instead of exiting 2; it still exits 2 when no enqueue of ID runs and none is filed.
