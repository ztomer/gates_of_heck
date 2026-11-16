* goh's self-walk (`tests/test_self_walk.py`) gives the same verdict on the owner's Mac and on a
  hosted runner (GOH.SELFWALKCI). It ran in the suite's home, which links the owner's `~/Projects`
  and `~/.claude/skills`, and the runner has neither: `skills corpus` (goh's `GOH_SKILLS_ROOT` is
  under HOME) ran on the Mac and not on CI, and `one roadmap, open entries only` judged its queue
  rule (the umbrella queue under `~/Projects`) on the Mac and said NOT JUDGED on CI. So v0.127.0's
  CI was red while every local gate was green. `walk_env` now runs the walk as the runner runs it,
  on any machine: a HOME of its own with none of the owner's state (`suite_home.furnish(home,
  reads=())`) and `GITHUB_ACTIONS=true`, so the estate sweep says NOT APPLICABLE here as there
  rather than failing on the Mac alone. The seed names both scanners' reasons (`not_run`,
  `unjudged`); a new test pins the walk's home and fails on the old `walk_env`.
