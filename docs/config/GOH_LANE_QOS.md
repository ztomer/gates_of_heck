# GOH_LANE_QOS
Area: pushes
Kind: setting
Order: 440
Default: the OS's (macOS: no clamp; Linux: nice + ionice)

The QoS clamp a lane- or work-class gate runs under (GOH.LANDSHARE, GOH.QOSCLAMP,
`lib/land_share.py`): `tools/load.py 0 -- CMD` execs CMD, and `tools/pytest.sh` its pytest. Unset,
Linux runs them under `nice -n 10` + `ionice -c 2 -n 7` and macOS at default QoS, where the land
path's `GOH_LAND_SHARE` of the cores protects it: a `nice` changes no thread's priority on macOS,
and `taskpolicy -c utility` ran one worker x2.7-6 slower, x9-25 with 43-62% of the machine idle
(2026-11-25).
`utility` asks for that clamp on macOS by name; `off` sets none on either. A missing tool degrades
to default QoS with its reason. The land path (`pre`, `land`, `ship`) and commit gates are never
clamped; a load experiment's command (N spinners) never either. The goh suite pins it `off` for its
fixtures (`tests/suite_home.py` QOS).
