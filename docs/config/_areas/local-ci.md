# Local CI (`gates/local_ci.sh`)
Order: 130
Level: 2

| Key | Default | Meaning |
|---|---|---|
<!-- rows -->

Every gate step also runs under `lib/orphan_canary.py`: the process table is sampled either side of
each step and anything that outlived it — attributable to this repo by path, or orphaned (`ppid 1`)
— is **reported and the step fails**. A leaked server is invisible from the inside: the step exits 0
and the orphan blocks every later run. Processes started elsewhere on the machine are counted and
named as such, never failed on.
