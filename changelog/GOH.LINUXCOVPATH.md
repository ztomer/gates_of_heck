* The rust coverage mode runs on the cargo-llvm-cov its manifest floor names (0.6.24): it calls
  `show-env --export-prefix`, never the later alias `--sh`, and a refused `show-env` is named in
  the export log instead of leaving it empty. The coverage fakes link the running Python beside
  their shims, so they pass where Python is not in /usr/bin (the remote Linux worker).
