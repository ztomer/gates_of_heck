# GOH_EMOJI_MIN_FILES, GOH_CONFLICT_MARKERS_MIN_FILES, GOH_FILE_LENGTH_MIN_FILES, GOH_SECRETS_MIN_FILES, GOH_SHELL_LINT_MIN_FILES, GOH_EARLY_EXIT_PIPE_MIN_FILES, GOH_DEAD_AFTER_EXEC_MIN_FILES, GOH_BARE_HOOK_INDEX_MIN_FILES, GOH_RELEASE_QUIET_MIN_FILES, GOH_MKTEMP_PORTABLE_MIN_FILES, GOH_LOCALE_PREFIX_MIN_FILES, GOH_BARE_OPEN_MIN_FILES, GOH_RUN_PATHS_MIN_FILES, GOH_GIT_DEADLINE_MIN_FILES, GOH_DEAD_PORT_MIN_FILES, GOH_PLURAL_HEDGE_MIN_FILES, GOH_BARE_PYTHON_MIN_FILES, GOH_CHECKOUT_CREDENTIALS_MIN_FILES, GOH_TAP_WRITES_MIN_FILES, GOH_MOVING_TAGS_MIN_REFS, GOH_WORKFLOW_PINS_MIN_REFS, GOH_MD_LINKS_MIN_LINKS, GOH_UNREAPED_SPAWN_MIN_FILES, GOH_FIXED_SLEEP_MIN_FILES, GOH_STATUS_MARKERS_MIN_FILES, GOH_JS_SYNTAX_MIN_FILES, GOH_BUSY_LOOP_MIN_FILES, GOH_INSTALLED_VERSION_MIN_FILES
Area: structural
Kind: setting
Order: 380
Default: REQUIRED for every scanner that read something: an undeclared floor is red from 2026-11-23 (`GOH.FLOORREQ`; the advisory line until then, `GOH_GRACE`, a week of grace for the consumers whose CI cannot read the private seed store), unless the repo's `floor_seed.json` names the key (a warning); a seeded key that is now declared retires its private-store entry in the run that sees it (GOH.SEEDPRUNE, said, green), and is a stale entry (red) only in the repo's own tracked file, which the declaring commit edits

**Every scanner declares its subject** (BACKLOG 4.3, `crates/goh/src/subject.rs`), in the shape
`GOH_SUBPROCESS_STDIN_MIN_CALLS` had alone: `GOH_<SCANNER>_MIN_<UNIT>` is how much of its subject a
FULL-SCOPE scan must read before a clean run is trusted. The class: a scanner whose own selection
broke reads less than the repo holds, and a scan of nothing finds nothing, so it reads as clean --
found independently in four repos (an `__all__` omitting a name, a gate regex dropping gates
declared with a third tuple element, a regex `ruff format` broke that silently vanished three
gates). What each counts: text files read (emoji, secrets), files read (conflict markers), files
measured against the cap (file length, only when `GOH_MAX_LINES` is set), shell files handed to
shellcheck (shell lint), shell sources read (early-exit pipe, code after exec, bare hook index,
BSD-only mktemp, bash setting a locale itself (bash sources only: the shell sources less zsh), bare
open (which also reads tracked `.py` files, for a subprocess that runs `open`; GOH.BAREOPEN2) --
five scanners, five keys, one selection each could break alone), release-named shell scripts read
(release quiet), hook and gate scripts (bare python), workflow and composite-action files (checkout
credentials), script and workflow files (tap writes), image references found (moving tags: every
`FROM`, `COPY --from=`, YAML `image:`, workflow `container:` and `uses: docker://` it read, whether
pinned, released, exempted or carried by a variable it cannot resolve -- a parser or file filter
that broke stops seeing references, not files), every link the markdown grammar found, judged or
skipped, and every `src`, `href` and `srcset` candidate of an HTML tag (md-links: a grammar that
stopped matching reads every file and finds no link; HTML paths since GOH.MDHTMLREFS, 2026-11-18),
test files read (unreaped spawn), test sources read (dead port: test-path files and Rust files with
a `#[cfg(test)]` region), and Python or source files read by the two seeded commands `goh
fixed-sleep` and `goh status-markers`, which judge their own floor, and the tracked JavaScript
sources handed to the parser (`goh js-syntax`: the files it read, so a scope that shrank -- a
renamed directory, a new vendor rule -- is red rather than clean), and the tracked shell sources the
busy-loop check read (`goh busy-loop`: the shell files it judged, so a selection that broke -- a
suffix rule, a shebang -- is red rather than clean), and the Markdown files the installed-version
check read (`goh installed-version`: every tracked `.md` that is not dated history, so a history
rule that grew to swallow the docs is red rather than clean). The structural pipeline's last step,
`every scanner read its declared subject`, judges the rest: under a floor is red, naming the key,
the floor and the count read (`read NONE` at zero -- what an exclusion covering the whole subject
looks like); a value that is not a whole number refuses the whole `.gatesrc` NAMING THE KEY (exit
2), never a floor of zero; `0` declares "no floor here" (a repo with no shell); and a scanner that
read something with no floor is red, naming each unset key with what this run read, `KEY=N`, ready
to paste (until 2026-11-14 it was one advisory line, which six consumers printed and none acted on).
The estate's keyless scanners on the day the requirement landed are seeded in `floor_seed.json`
(`crates/goh/src/subject_required.rs`, `crate::estate_seed`: the repo's own tracked file or the
private store, keyed by root commit, the key as the path): a seeded key warns; declaring it retires
a private-store entry with no hand edit (the full run deletes it under the store's lock and prints
`retired`, GOH.SEEDPRUNE), while a tracked entry is deleted by the repo in the commit that declares
the floor. Pick a floor at or a little under that count (this repo uses nine tenths): set too high
it reds a clean repo and is lowered with the tree in the same commit; set too low it never fires.
Full scope only: at `--staged` a scanner reads only what the commit brings. **The pairing with the
stale-entry check**, both directions: under its floor a seed's stale entries are withheld and
`--seed` refuses (`goh fixed-sleep`, `goh status-markers`, `goh early-exit-pipe`, `goh
subprocess-stdin`), because a blind scan finds nothing and re-seeding it would launder the blindness
into a smaller ratchet; at or above it a stale entry still fails, because a floor counts the subject
whole and cannot vouch for the file that lost its findings. Not covered, by what they read: `no
tracked file its .gitignore ignores` and `no vendored copies of house files` judge the shared
listing itself (whose emptiness `check_empty_scope.py` refuses), `credential-urls`, `lock-version`
and `version provenance` read one named file each, and the opt-in steps carry their own refusals
(`GOH_REQUIRES_CALL` exits 2 on a rule that judges nothing).
