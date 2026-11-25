* GOH.ONEGATE: a goh land runs one gate, under the tip's code. A land that changed gate code ran two,
  main's and then its own; the steps are `./gates/...` run in the tip's tree, so main's never judged
  one of them (D-0012 said it did). Main's version of each step the lane changed now runs after the
  land, outside its lock, and reports: a would-be red, a step the lane broke, deleted or dropped from
  GOH_CI_STEPS, or `unavailable` by its reason. It refuses nothing. It runs only with `goh capacity`'s
  room and no land on its way (else `waiting`, after 24 h `unjudged: no room`), and stops after
  GOH_LAND_JUDGE_SAMPLE (20) judged lands until `land_judge.py continue`. `python3 lib/land_judge.py report`
  reads its false-red rate; `land_judge.py rule TIP STEP false|true WHY` rules a would-be red.
