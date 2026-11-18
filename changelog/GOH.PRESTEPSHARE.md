* A land's pre-step deadline follows the worker share it was admitted with (GOH.PRESTEPSHARE):
  `min(GOH_LAND_PRE_CEIL 25m, max(GOH_LAND_PRE_MAX 10m, 1.5 x WORK / workers))`, WORK the
  worker-seconds of the costliest of the newest 30 verdict-reaching pre-steps in the timing ledger
  (land rows now carry `pre_run_s` and `pre_workers`), seeded at 347 s x 12. The 368-file set that
  took 650 s at a 5-worker share gets 1250 s there and 600 s at 12; lib/pre_deadline.py reads the
  share after admission and kills the set whole at its deadline (no verdict, exit 124).
