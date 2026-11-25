# GOH_LANE_QOS
Area: pushes
Kind: setting
Order: 440
Default: on

`off` turns off the QoS clamp a lane- or work-class gate runs under (GOH.LANDSHARE,
`lib/land_share.py`): `tools/load.py 0 -- CMD` execs CMD, and `tools/pytest.sh` its pytest, under
`taskpolicy -c utility` on macOS (probed 2026-11-21 at load 48-50: 6-7x slower than default under
contention, where `nice 19` changed nothing) and `nice -n 10` + `ionice -c 2 -n 7` on Linux; a
missing tool degrades to default QoS with its reason. The land path (`pre`, `land`, `ship`) and
commit gates are never clamped; a load experiment's command (N spinners) never either. The goh suite
pins it `off` for its fixtures (`tests/suite_home.py` QOS).
