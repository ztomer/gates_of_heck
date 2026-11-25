"""`tiny_goh(tree)`: a goh checkout whose `goh` is a two-file crate (GOH.FSEVQUIET).

Two tests build goh for real, to prove WHICH SOURCE a build reads: HEAD's or the working tree's. The
goh they built was the whole workspace, ~4,800 files and 150 crates each, and none of it is what
they prove (the rust gate builds that). This one keeps what they do: goh's own build.rs and the one
stamp rule (lib/goh_version.sh), the package and binary names, `source-tree`, `--version` and a
help text to plant in.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from timing_bounds import patience

HELP = "Fail when a crate is exempt from its workspace lint policy"
FILES = {
    "Cargo.toml": '[workspace]\nresolver = "2"\nmembers = ["crates/goh"]\n',
    "Cargo.lock": 'version = 4\n\n[[package]]\nname = "goh"\nversion = "0.0.0"\n',
    "crates/goh/Cargo.toml": (
        '[package]\nname = "goh"\nversion = "0.0.0"\nedition = "2021"\n'
        '[[bin]]\nname = "goh"\npath = "src/main.rs"\n'
    ),
    "docs/config.md": "no build input\n",
    "crates/goh/src/cli.rs": f'pub const HELP: &str = "{HELP}";\n',
    "crates/goh/src/main.rs": (
        "mod cli;\n"
        "fn main() {\n"
        "    match std::env::args().nth(1).as_deref() {\n"
        '        Some("source-tree") => println!("{}", env!("GOH_SOURCE_STAMP")),\n'
        '        Some("--version") => println!("goh {}", env!("GOH_VERSION")),\n'
        '        _ => println!("{}", cli::HELP),\n'
        "    }\n"
        "}\n"
    ),
}


def tiny_goh(tree: Path) -> None:
    """Replace `tree`'s Rust with FILES, build.rs kept, and commit it: HEAD is the tiny goh."""
    build_rs = (tree / "crates" / "goh" / "build.rs").read_text()
    for part in ("crates", "tests", "changelog", "docs", "hooks"):  # no build input but the first
        shutil.rmtree(tree / part, ignore_errors=True)
    for rel, text in {**FILES, "crates/goh/build.rs": build_rs}.items():
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (tree / rel).write_text(text)
    for args in (("add", "-A"), ("-c", "user.name=t", "-c", "user.email=t@t", "-c",
                 "core.hooksPath=/dev/null", "commit", "-q", "-m", "a tiny goh")):  # fmt: skip
        subprocess.run(["git", "-C", str(tree), *args], check=True, stdin=subprocess.DEVNULL,
                       capture_output=True, timeout=patience(60))  # fmt: skip
