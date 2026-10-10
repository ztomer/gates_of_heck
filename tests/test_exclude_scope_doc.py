"""`GOH_EXCLUDE`'s documented scope is the scope the code applies.

docs/config.md said the key exempted three checks; the code applied it to ten, the secrets scan
among them, and a repo that excluded a vendored crate from house style lost its credential scan
over that tree without a word (app_updates, 2026-10-08). Two lists that must agree, kept by hand,
drift; this test holds them equal. A new consumer of the key goes red here until it is named in
CONSUMERS and in the doc row -- and a security check may not become one.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "crates/goh/src"

# Every file that reads the key -> the names the doc row must give the checks it exempts.
CONSUMERS: dict[str, tuple[str, ...]] = {
    "crates/goh/src/steps.rs": ("emoji scan", "length cap", "home paths"),
    "crates/goh/src/steps_rust.rs": ("`#[allow]`/`#[expect]`", "emptiness asserts"),
    "crates/goh/src/shell_lint.rs": ("shell lint",),
    "crates/goh/src/earlypipe/mod.rs": ("early-exit pipe",),
    "crates/goh/src/deadexec/mod.rs": ("code after exec",),
    "crates/goh/src/hookindex/mod.rs": ("bare hook index",),
    "crates/goh/src/killname/mod.rs": ("kill-by-name",),
    "crates/goh/src/unreaped/mod.rs": ("unreaped spawn",),
    "crates/goh/src/mdlinks.rs": ("md-links",),
    "crates/goh/src/claims/mod.rs": ("claim-derivation",),
    "crates/goh/src/substdin/mod.rs": ("subprocess-stdin",),
    "gates/rust_gate.sh": ("`#[allow]`/`#[expect]`", "emptiness asserts"),
    "gates/rust_each_crate.sh": ("`--each-crate`",),
}
# Where the key is defined and parsed, not applied.
DEFINERS = {"crates/goh/src/gatesrc.rs"}
READS_KEY = re.compile(r"cfg\.exclude\b|length_exclude\(cfg\)|\$\{?GOH_EXCLUDE\b")


def _row() -> str:
    text = (ROOT / "docs/config.md").read_text(encoding="utf-8")
    return next(line for line in text.splitlines() if line.startswith("| `GOH_EXCLUDE` |"))


def _readers() -> set[str]:
    paths = [*SRC.rglob("*.rs"), *(ROOT / "gates").glob("*.sh")]
    return {
        str(p.relative_to(ROOT)) for p in paths if READS_KEY.search(p.read_text(encoding="utf-8"))
    } - DEFINERS


def test_every_reader_of_the_key_is_listed() -> None:
    assert _readers() == set(CONSUMERS), (
        "the files that read GOH_EXCLUDE changed; name each new one in CONSUMERS and in the "
        f"GOH_EXCLUDE row of docs/config.md:\n  found:  {sorted(_readers())}\n"
        f"  listed: {sorted(CONSUMERS)}"
    )


def test_the_doc_row_names_every_exempted_check() -> None:
    exempt = _row().split("**Exempt from**", 1)[1].split("**Never", 1)[0]
    missing = sorted({n for names in CONSUMERS.values() for n in names if n not in exempt})
    assert not missing, f"docs/config.md's GOH_EXCLUDE row does not name: {missing}"


def test_the_secrets_scan_cannot_take_a_path_exemption() -> None:
    """Structural, not documentary: the scanner has no parameter to receive one."""
    assert "**Never exempt from the secrets scan**" in _row()
    secrets = (SRC / "secrets.rs").read_text(encoding="utf-8")
    assert "PathFilter" not in secrets, "crates/goh/src/secrets.rs takes a path exemption"
    step = (SRC / "steps.rs").read_text(encoding="utf-8").split("pub fn step_secrets", 1)[1]
    step = step.split("\n}\n", 1)[0]
    assert "cfg" not in step and "compile_exclude" not in step, step


def test_the_checkout_credentials_scan_cannot_take_a_path_exemption() -> None:
    """The same rule for the token a checkout leaves in git config: its one escape is the
    reasoned `persist-credentials-ok:` marker on the step, never a path pattern."""
    assert "nor the checkout-credentials scan" in _row()
    mod = SRC / "checkoutcreds"
    for f in mod.glob("*.rs"):
        text = f.read_text(encoding="utf-8")
        assert "PathFilter" not in text and "compile_exclude" not in text, f
        assert not READS_KEY.search(text), f
