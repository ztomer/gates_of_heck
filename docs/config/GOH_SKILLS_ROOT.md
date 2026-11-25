# GOH_SKILLS_ROOT
Area: structural
Kind: setting
Order: 180
Default: the repo root

Corpus root, when the skills tree is a subdirectory rather than the repo itself. A root OUTSIDE the
repo is skipped by name at `--staged` (no part of the commit) and, when it is its own repository,
judged at ITS LAST COMMIT at full scope -- never its working tree, which another session may be
mid-edit in (C2). A root in no repository is read as it stands.
