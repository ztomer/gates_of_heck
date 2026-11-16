* The bash 5 guard (`lib/bash5.sh`) installs Homebrew's bash on a GitHub Actions runner that has
  none (GOH.INSTALLBASH5). Consumer workflows run `install.sh` before goh-floors, so on the hosted
  macOS runner, Apple's bash 3.2 plus Homebrew but no Homebrew bash, `install.sh` refused to run and
  every such job went red before any repo step (ztools run 43796046671). An executed script that
  finds no bash >= 5 with `GITHUB_ACTIONS=true` now runs `brew install bash` (output on stderr) and
  re-execs under it; off a runner nothing is installed and the error still names the fix. New seam
  `GOH_BASH_BREW` (`=` empty never installs).
