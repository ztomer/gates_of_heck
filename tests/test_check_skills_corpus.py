"""check_skills_corpus.py — an agent skills corpus is code nothing compiles.

The defects this pins were all measured on a real 38-skill corpus
(~/.claude/skills, 2026-09-07) that had never been gated:

  - TWO skills carried no frontmatter at all, so nothing could ever trigger
    them. Dead code that reads as live — prove-before-claim's own opening case.
  - TWO `[[wikilinks]]` pointed at skills that do not exist.
  - One SKILL.md had reached 2,294 lines because nothing applied compaction
    pressure, and SKILL.md is the file that loads on invoke.

Every test here is a direction the checker was watched to FAIL in before the
gate was trusted, including the two nobody writes: a corpus that came UNDER
the ceiling but kept its baseline entry (stale exemptions must fail, or a
burn-down can be abandoned half-done), and an empty scope (which must refuse,
not report a clean corpus over nothing).
"""

import json

import pytest

from conftest import run_check, write

SCRIPT = "checks/check_skills_corpus.py"

GOOD = "---\nname: {name}\ndescription: does a thing worth triggering on.\n---\n\n# {name}\n\nbody\n"


def _corpus(root, n=6, **overrides):
    """A corpus of `n` valid skills, with named files overridden."""
    for i in range(n):
        name = f"skill-{i}"
        write(root, f"{name}/SKILL.md", GOOD.format(name=name))
    for rel, content in overrides.items():
        write(root, rel, content)
    return root


@pytest.fixture
def corpus(tmp_path):
    return _corpus(tmp_path / "skills")


def _run(root, *args):
    return run_check(root, SCRIPT, "--root", str(root), *args)


def test_a_clean_corpus_passes_and_prints_its_denominator(corpus):
    r = _run(corpus)
    assert r.returncode == 0, r.stdout
    # A bare "OK" cannot be told from a run that scanned nothing.
    assert "6 skills" in r.stdout


def test_missing_frontmatter_fails(corpus):
    write(corpus, "skill-1/SKILL.md", "# skill-1\n\nno frontmatter at all\n")
    r = _run(corpus)
    assert r.returncode == 1
    assert "can never be triggered" in r.stdout


def test_frontmatter_name_must_match_the_directory(corpus):
    write(corpus, "skill-1/SKILL.md", GOOD.format(name="skill-one"))
    r = _run(corpus)
    assert r.returncode == 1
    assert "does not match directory" in r.stdout


def test_missing_description_fails(corpus):
    write(corpus, "skill-1/SKILL.md", "---\nname: skill-1\n---\n\nbody\n")
    r = _run(corpus)
    assert r.returncode == 1
    assert "no `description:`" in r.stdout


def test_a_wikilink_to_no_skill_fails(corpus):
    write(corpus, "skill-1/SKILL.md", GOOD.format(name="skill-1") + "\nsee [[ghost-skill]]\n")
    r = _run(corpus)
    assert r.returncode == 1
    assert "[[ghost-skill]] matches no skill" in r.stdout


def test_a_wikilink_to_a_real_skill_passes(corpus):
    write(corpus, "skill-1/SKILL.md", GOOD.format(name="skill-1") + "\nsee [[skill-2]]\n")
    assert _run(corpus).returncode == 0


def test_a_dead_reference_link_fails(corpus):
    write(corpus, "skill-1/SKILL.md", GOOD.format(name="skill-1") + "\n[x](references/gone.md)\n")
    r = _run(corpus)
    assert r.returncode == 1
    assert "references/gone.md" in r.stdout


def test_a_live_reference_link_passes_and_is_itself_scanned(corpus):
    write(corpus, "skill-1/SKILL.md", GOOD.format(name="skill-1") + "\n[x](references/cases.md)\n")
    write(corpus, "skill-1/references/cases.md", "# cases\n\nsee [[ghost-skill]]\n")
    r = _run(corpus)
    # the link resolves, but the reference file's own dead wikilink does not:
    # a dead pointer in a reference is exactly as dead as one in SKILL.md
    assert r.returncode == 1
    assert "references/cases.md: [[ghost-skill]]" in r.stdout


def test_over_the_word_ceiling_fails(corpus):
    write(corpus, "skill-1/SKILL.md", GOOD.format(name="skill-1") + "word " * 200)
    r = _run(corpus, "--max-words", "50")
    assert r.returncode == 1
    assert "> ceiling 50" in r.stdout


def test_a_baselined_skill_may_stay_over_the_ceiling(corpus, tmp_path):
    write(corpus, "skill-1/SKILL.md", GOOD.format(name="skill-1") + "word " * 200)
    bl = tmp_path / "baseline.json"
    bl.write_text(json.dumps({"oversized": {"skill-1": {"words": 5000, "reason": "mid-split"}}}))
    assert _run(corpus, "--max-words", "50", "--baseline", str(bl)).returncode == 0


def test_a_baselined_skill_that_GREW_fails(corpus, tmp_path):
    write(corpus, "skill-1/SKILL.md", GOOD.format(name="skill-1") + "word " * 200)
    bl = tmp_path / "baseline.json"
    bl.write_text(json.dumps({"oversized": {"skill-1": {"words": 100, "reason": "mid-split"}}}))
    r = _run(corpus, "--max-words", "50", "--baseline", str(bl))
    assert r.returncode == 1
    assert "only shrinks" in r.stdout


def test_a_STALE_baseline_entry_fails(corpus, tmp_path):
    """The entry outlived the violation. Without this the burn-down list can be
    abandoned half-done and still read as compliant."""
    bl = tmp_path / "baseline.json"
    bl.write_text(json.dumps({"oversized": {"skill-1": {"words": 5000, "reason": "mid-split"}}}))
    r = _run(corpus, "--max-words", "50000", "--baseline", str(bl))
    assert r.returncode == 1
    assert "stale entry" in r.stdout


def test_an_unreadable_baseline_refuses_rather_than_reading_as_no_exemptions(corpus, tmp_path):
    bl = tmp_path / "baseline.json"
    bl.write_text("{ this is not json")
    r = _run(corpus, "--baseline", str(bl))
    assert r.returncode == 2
    assert "unreadable" in r.stdout


def test_one_lesson_restated_in_two_skills_fails(corpus):
    head = "\n## A PIPELINE SWALLOWS THE EXIT CODE YOU ARE CHECKING\n\nbody\n"
    for n in ("skill-1", "skill-2"):
        write(corpus, f"{n}/SKILL.md", GOOD.format(name=n) + head)
    r = _run(corpus)
    assert r.returncode == 1
    assert "appears in 2 skills" in r.stdout


def test_structural_headings_may_repeat_freely(corpus):
    """The first draft flagged "Related appears in 15 skills" — 11 of its 12
    findings were the corpus's shared skeleton. A gate that cries wolf on
    correct structure is a gate that gets switched off."""
    for n in ("skill-1", "skill-2", "skill-3"):
        write(corpus, f"{n}/SKILL.md",
              GOOD.format(name=n) + "\n## Related\n\nx\n\n## The move\n\ny\n\n## Checklist\n\nz\n")
    assert _run(corpus).returncode == 0


def test_an_empty_scope_refuses_instead_of_reporting_a_clean_corpus(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    r = _run(empty)
    assert r.returncode == 2
    assert "the scope is wrong" in r.stdout


def test_the_default_root_is_the_cwd_not_a_path_elsewhere(tmp_path):
    """A convenience default of ~/.claude/skills made this checker measure the
    real corpus whatever tree it was aimed at — it reported "37 skills OK" from
    inside an empty fixture, and the estate's skeleton-tree canary caught it.
    A checker measures where it is pointed, or it measures nothing."""
    empty = tmp_path / "empty"
    empty.mkdir()
    r = run_check(empty, SCRIPT)          # no --root
    assert r.returncode == 2
    assert "the scope is wrong" in r.stdout


def test_update_baseline_records_todays_oversized_set(corpus, tmp_path):
    write(corpus, "skill-1/SKILL.md", GOOD.format(name="skill-1") + "word " * 200)
    bl = tmp_path / "baseline.json"
    r = _run(corpus, "--max-words", "50", "--baseline", str(bl), "--update-baseline")
    assert r.returncode == 0
    recorded = json.loads(bl.read_text())["oversized"]
    assert set(recorded) == {"skill-1"}
    # an entry with no argued reason says so, rather than looking decided
    assert recorded["skill-1"]["reason"] == "unreviewed"
