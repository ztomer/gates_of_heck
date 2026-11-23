* The main-red notice an enqueue prints now names the failing tests. It read the idle suite's log
  as `idle-suite-<full sha>.log` while the idle suite writes `idle-suite-<sha[:12]>.log`, so every
  notice since GOH.MAINREDNOTICE said "no failing test named in its log" and no node joined a
  pre-step. Both now use one path (`main_red.log_of`); ERROR nodes (a setup or collection error)
  are named beside FAILED ones, and a red log that names no test says why: the log is missing, or
  its last line.
