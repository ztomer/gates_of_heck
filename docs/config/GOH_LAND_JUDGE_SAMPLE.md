# GOH_LAND_JUDGE_SAMPLE
Area: ship
Kind: setting
Order: 165
Default: 20

How many goh lands main's code judges (GOH.ONEGATE, `lib/land_judge.py drain`) before it stops: a
later land reads `unjudged: the sample is full`, and is not run, until the report has been read and
the next sample asked for (`python3 lib/land_judge.py continue`; `report` says when the sample is
full). `0` judges no land. A value that is no whole number is the default, said on stderr.
