# docs/config.md — every `GOH_*` key
Order: 0
Level: 1

Single schema. CLI flags beat env/.gatesrc where both exist. Unset means
"gate default or skip" per row — never a silent pass on a missing floor
(coverage gates exit 2 when no floor is stated anywhere).
