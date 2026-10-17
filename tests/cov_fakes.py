"""Shared shell plumbing for the fake `cargo` scripts of the rust coverage tests."""

from __future__ import annotations

# The rust coverage mode's plumbing, for a fake `cargo` (_coverage_rust.sh: show-env, the build
# with its artifact list, the prune, the run, the report). A fake splices this in after its own
# `metadata` answer and models the run (`test`) and the report (`llvm-cov report`) itself.
SHOW_ENV_FLOW = """\
if [ "$1 $2" = "llvm-cov show-env" ]; then
  echo "export LLVM_PROFILE_FILE='$PWD/target/llvm-cov/fake-%p.profraw'"
  echo "export CARGO_LLVM_COV_TARGET_DIR='$PWD/target/llvm-cov'"
  exit 0
fi
case " $* " in
  *" --no-run "*)
    exe="$CARGO_LLVM_COV_TARGET_DIR/debug/deps/fake"
    mkdir -p "${exe%/*}"; : >"$exe"; chmod +x "$exe"
    printf '{"reason":"compiler-artifact","filenames":[],"executable":"%s"}\\n' "$exe"
    exit 0 ;;
esac
"""
