# GOH_CPU_OWNERS
Area: runtime
Kind: setting
Order: 403
Default: `idle suite=/idle-suite/;gate=/landq/;gate=/goh/ship;lane=/lanes/`

env, `lib/cpu_owners.py` (GOH.TIMEACCOUNT): the owners CPU is booked to, `owner=marker;...`, first match wins: a process is the owner whose marker is in its working directory, else its nearest ancestor's; none is `other`. A pair with no `=` is refused.
