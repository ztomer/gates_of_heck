# GOH_SUBPROCESS_STDIN_MIN_CALLS
Area: structural
Kind: setting
Order: 280
Default: unset (no floor)

**Full scope only.** `goh subprocess-stdin` runs as a structural step unless
`GOH_SUBPROCESS_STDIN=off`: every tracked `.py`'s process calls must name `stdin=` or `input=`, read
from the AST (`ruff_python_parser`), because a child that inherits a stdin it did not ask for HANGS
rather than fails -- `ZeroThunder`'s bare `md5sum` probe hung the games metarepo's claims gate on
2026-10-05. The rule is uniform rather than a list of tools that read stdin, because that list rots;
`stdin=None` is legal and means "inherit, on purpose", so inheriting is a decision someone wrote
down. The declarable calls are `subprocess.{run,Popen,call,check_call,check_output}` and
`asyncio.create_subprocess_{exec,shell}`; `subprocess.{getoutput,getstatusoutput}` and
`os.{system,popen}` are REFUSED outright (no keyword exists), and a call through `**kwargs` is a
finding -- a static read cannot see what the mapping holds. One level of attribute only: `a.b.run()`
is deliberately not followed, because reaching through an attribute never resolved is how a port
invents findings nobody has to fix. **This key is the floor that catches the fourth vacuous pass**:
a scan that stopped seeing calls. Below it the step is red and says the scan lost its subject; a
value that is not a whole number is exit 2 NAMING THIS KEY, never a floor of zero. It is
full-scope-only because at `--staged` the listing is the files this commit brings, so a commit
touching one helper would be red against its own scope -- while the stage that matters (did this
commit ADD an undeclared call) is exact at both scopes. The floor is per-repo because a hardcoded
one is wrong everywhere else. Survivors go in `subprocess_stdin_allow.json` at the repo root, or
`subprocess_stdin_allow.d/*.json` beside it when that file would go over `GOH_MAX_LINES`: one entry,
ONE call site, with a `path`, `call`, `line`, `reason` and `status` (`legitimate` or `unreviewed`);
**a stale entry fails** (the finding is gone, the file is gone, or the call's text changed), and a
malformed allowlist is a PROBLEM, never an empty list -- a gate cannot disable itself by corrupting
its own exemptions. `line` is the call itself (whitespace and trailing commas ignored), so `ruff
format` reflowing it across lines cannot revoke a judgement, and changing or deleting the call does;
an older entry holding the call's first line still matches the call that contains it. A missing
`status` counts as `unreviewed` debt and is printed on the passing line.
`GOH_SUBPROCESS_STDIN_MIN_CALLS` unset means no floor; the step's other three vacuous-pass modes (no
Python at all, every `.py` excluded, a `.py` it cannot parse) each have their own verdict. Same
`GOH_EXCLUDE`.
