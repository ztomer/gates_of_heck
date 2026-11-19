* A land's suite runs as hosted CI runs it (GOH.CIPARITY): a suite carrying a land key
  (GOH_LAND_VERDICTS_RECORD, the pre-step's; GOH_LAND_VERDICTS_REUSE, the gate's) enters in the CI
  shape -- a home linking none of the owner's state (no ~/Projects, so no live loop queue; no
  ~/.claude/skills), every XDG directory inside it, and the variables `.github/workflows/gate.yml`
  gives its gate step, read from the workflow by tests/ci_shape.py (workflow, job and gate-step
  `env`, `GITHUB_ENV` writes, GitHub's `CI` and `GITHUB_ACTIONS`), failing closed on a form it
  cannot evaluate. The suite still runs once per land. `GATES_OF_HECK_SUITE_SHAPE=ci` asks for it
  by hand; tests/test_ci_shape.py is red when gate.yml sets a variable the leg omits.
