# GOH_RED_RUN
Area: commit-messages
Kind: setting
Order: 20
Default: unset (rule off)

Set to `1` to run `goh red-run` from the stock `commit-msg` hook and again over the pushed range: a
commit whose diff ADDS a test declaration (`def test`, `#[test]`, `func test`, `@Test`,
`it(`/`test(`, `@test`) must carry `Red-run:` in its trailer block, naming the mutation that turned
the new test red. Hollow values (`none`, `n/a`, `tbd`, `-`, `yes`, `done`) are refused. Presence and
substance are checked, truth is a review rule (BACKLOG 4.11). Over a pushed range, each commit is
judged only when its parent's `.gatesrc` enables `GOH_RED_RUN`, so turning the rule on never refuses
older unpushed commits (queue GOH.RR1).
