* The push gate names an in-tree cargo build dir (GOH.PUSHINTREEBUILD): the export is deleted after
  every push, so a tracked `.cargo/config.toml` with `build-dir = ".build"` compiled every
  dependency cold on every push, silently (a private server workspace: 187 s against 108 s warm). The gate now
  warns, naming the dir; `_push_build_dirs.py --in-tree` judges the build dirs cargo reports.
