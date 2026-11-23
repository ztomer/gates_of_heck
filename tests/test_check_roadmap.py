"""One roadmap of open entries (`goh roadmap`, GOH.ROADMAPGATE; owner 2026-11-08: "rework, rephase,
harden the roadmaps and present the result to me").

The 2026-11-08 audit of every repo's roadmap found completed rows left in place, duplicate plan
files and entries the umbrella queue had already closed. The rework fixed the instances; this gate
is the class. Each of its four rules is planted once on a fixture and watched go red (house rule
#2), the clean shape passes, and the ratchet holds in every direction -- a stale seed entry fails,
the seed cannot rise, and a rule the run cannot judge (no queue) is withheld, never read as fixed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _gatesrc_kit import declared, write_gatesrc
from conftest import commit_all, git, hermetic_env, run_goh, stage, write

GOH_ROOT = Path(__file__).resolve().parent.parent

GATESRC = "GOH_ROADMAP=ROADMAP.md\n"

CLEAN = """# Roadmap

## Rulings in force

- R1 (owner 2026-11-08): one plan file.

## Open work

### Phase 1 — now

- [ ] **RT.ONE — the first thing.** (queue: Q.OPEN.)
  - **Done when:** a red-first test passes.
- **P0.4 Host chords.**
  *Done when:* the owner reports.

### Phase 2 — parked

#### AU.WAF — attended solve

Done when: a headed run returns the page.
"""

# One planted violation per rule, with the line the gate must name.
PLANTED = {
    "completed": (CLEAN + "\n- **RT.TWO — old.** DONE\n", "ROADMAP.md:22"),
    "struck": (CLEAN + "\n- ~~RT.TWO — old~~\n", "ROADMAP.md:22"),
    "no-id": (CLEAN.replace("**P0.4 Host chords.**", "**Host chords.**"), "ROADMAP.md:13"),
    "no-done-when": (CLEAN.replace("  *Done when:* the owner reports.\n", ""), "ROADMAP.md:13"),
    "orphan": (CLEAN.replace("- R1 (owner", "Done when: never.\n- R1 (owner"), "ROADMAP.md:5"),
    "queue-done": (CLEAN.replace("Q.OPEN", "Q.DONE"), "ROADMAP.md:11"),
}

QUEUE = {"items": [{"id": "Q.OPEN", "status": "todo"}, {"id": "Q.DONE", "status": "done"}]}


@pytest.fixture
def queue(tmp_path: Path) -> Path:
    path = tmp_path / "queue.json"
    path.write_text(json.dumps(QUEUE))
    return path


def roadmap(repo: Path, queue: Path | None, *args: str, env_extra: dict[str, str] | None = None):
    env = hermetic_env(
        GOH_ROADMAP_QUEUE=str(queue) if queue else str(repo / "no-queue.json"), **(env_extra or {})
    )
    return run_goh(repo, "roadmap", *args, env=env)


def fixture(repo: Path, text: str, **extra: str) -> None:
    write(repo, ".gatesrc", GATESRC)
    write(repo, "ROADMAP.md", text)
    for rel, body in extra.items():
        write(repo, rel, body)
    commit_all(repo)


def test_the_reworked_shape_passes(repo, queue):
    fixture(repo, CLEAN)
    r = roadmap(repo, queue)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "3 entries open in 2 phases" in r.stdout, r.stdout


@pytest.mark.parametrize("rule", sorted(PLANTED))
def test_each_rule_planted_once_is_red_naming_the_line(repo, queue, rule):
    text, where = PLANTED[rule]
    fixture(repo, text)
    r = roadmap(repo, queue)
    assert r.returncode == 1, r.stdout + r.stderr
    assert where in r.stderr, r.stderr


def test_prose_that_says_shipped_marks_no_entry_and_stays_green(repo, queue):
    """ztools e556eb5: three PROSE lines matched the brief's bare regex -- "What shipped is in
    CHANGELOG.md", "in the shipped chain", "shipped-work text". Rule 2 judges entries (a heading,
    list item or table row carrying an id), so these pass while the planted entry beside them is
    red."""
    prose = (
        "\nWhat shipped is in CHANGELOG.md.\n\n- read the shipped chain first\n\n"
        "| Column | shipped-work text |\n|---|---|\n"
    )
    fixture(repo, CLEAN + prose)
    r = roadmap(repo, queue)
    assert r.returncode == 0, r.stdout + r.stderr
    write(repo, "ROADMAP.md", CLEAN + prose + "| RT.NINE | shipped |\n")
    r = roadmap(repo, queue)
    assert r.returncode == 1 and "ROADMAP.md:28" in r.stderr, r.stderr


def test_rule_1_a_second_plan_file_is_red_and_a_missing_roadmap_is_red_outright(repo, queue):
    fixture(repo, CLEAN, **{"docs/BACKLOG.md": "# old plan\n"})
    r = roadmap(repo, queue)
    assert r.returncode == 1 and "docs/BACKLOG.md:1: a second plan file" in r.stderr, r.stderr
    git(repo, "rm", "-q", "docs/BACKLOG.md")
    write(repo, ".gatesrc", "GOH_ROADMAP=PLAN.md\n")
    commit_all(repo)
    r = roadmap(repo, queue)
    assert r.returncode == 1 and "GOH_ROADMAP=PLAN.md is not a tracked file" in r.stderr, r.stderr
    # ...and it cannot be seeded away.
    assert roadmap(repo, queue, "--seed").returncode == 1


def test_a_lower_case_document_about_a_plan_is_not_a_plan_file(repo, queue):
    fixture(repo, CLEAN, **{"tests/fixtures/weekend_plan.md": "x\n", "docs/timeplan.md": "x\n"})
    assert roadmap(repo, queue).returncode == 0


def test_an_unset_roadmap_is_red_in_a_fixture_too(repo, queue, tmp_path):
    """GOH.ROADMAPHOOK: every gated repo declares GOH_ROADMAP (a path or none), and so does every
    fixture. The test seam GOH_ROADMAP_PENDING once stood a listed repo (`*`: every fixture) pending
    with a warning; it is gone, so a list naming this repo excuses nothing."""
    write(repo, "ROADMAP.md", CLEAN)
    commit_all(repo)
    pending = tmp_path / "pending.txt"
    pending.write_text("*\nproj\n")
    r = roadmap(repo, queue, env_extra={"GOH_ROADMAP_PENDING": str(pending)})
    assert r.returncode == 1 and "GOH_ROADMAP is unset" in r.stderr, (r.stdout, r.stderr)
    assert "Plan files here: ROADMAP.md" in r.stderr and "pending" not in r.stdout, r.stdout


def test_the_fixture_kit_declares_a_roadmap_unless_the_body_names_one(tmp_path):
    """tests/_gatesrc_kit.py is how a fixture writes `.gatesrc` (GOH.ROADMAPHOOK)."""
    assert declared("GOH_MAX_LINES=10") == "GOH_MAX_LINES=10\nGOH_ROADMAP=none\n"
    assert declared("") == "GOH_ROADMAP=none\n"
    assert declared("GOH_ROADMAP=PLAN.md\n") == "GOH_ROADMAP=PLAN.md\n"
    write_gatesrc(tmp_path, "X=1\n")
    assert (tmp_path / ".gatesrc").read_text() == "X=1\nGOH_ROADMAP=none\n"


def test_no_shipped_pending_list_is_left_to_go_stale(repo, queue):
    """GOH.ROADMAPREQ's staged turn-on is over: every repo the shipped list named declares, so the
    list is gone, and with no seam set an unset GOH_ROADMAP is red in every repo -- no line naming
    a repo that already declares can linger."""
    assert not (GOH_ROOT / "crates/goh/src/roadmap/pending.txt").exists()
    write(repo, "ROADMAP.md", CLEAN)
    commit_all(repo)
    env = hermetic_env()
    env["GOH_ROADMAP_QUEUE"] = str(queue)
    r = run_goh(repo, "roadmap", env=env)
    assert r.returncode == 1 and "GOH_ROADMAP is unset" in r.stderr, (r.stdout, r.stderr)


def test_none_makes_every_plan_file_a_finding(repo, queue):
    write(repo, "ROADMAP.md", CLEAN)
    write(repo, ".gatesrc", "GOH_ROADMAP=none\n")
    commit_all(repo)
    r = roadmap(repo, queue)
    assert r.returncode == 1 and "ROADMAP.md:1: a second plan file" in r.stderr, r.stderr


def test_the_seed_holds_todays_debt_and_only_falls(repo, queue):
    text, _ = PLANTED["completed"]
    fixture(repo, text)
    assert roadmap(repo, queue, "--seed").returncode == 0
    seed = json.loads((repo / "roadmap_seed.json").read_text())
    assert seed["entries"] == {"ROADMAP.md#completed": 1}, seed
    commit_all(repo)
    assert roadmap(repo, queue).returncode == 0
    # A second completed row is over the seed: red.
    write(repo, "ROADMAP.md", text + "- **RT.THREE.** shipped\n")
    r = roadmap(repo, queue)
    assert r.returncode == 1 and "seeded with 1; it has 2" in r.stderr, r.stderr
    # ...and seeding it is refused: the seed only falls.
    r = roadmap(repo, queue, "--seed")
    assert r.returncode == 1 and "would rise" in r.stderr, r.stderr
    # Prune the row: the seed entry is now STALE, red until lowered.
    write(repo, "ROADMAP.md", CLEAN)
    r = roadmap(repo, queue)
    assert r.returncode == 1 and "lower the seed" in r.stderr, r.stderr
    assert roadmap(repo, queue, "--seed").returncode == 0
    assert json.loads((repo / "roadmap_seed.json").read_text())["entries"] == {}
    assert roadmap(repo, queue).returncode == 0


def test_a_seed_raised_by_hand_against_head_is_refused(repo, queue):
    text, _ = PLANTED["completed"]
    fixture(repo, text)
    assert roadmap(repo, queue, "--seed").returncode == 0
    commit_all(repo)
    write(repo, "ROADMAP.md", text + "- **RT.THREE.** shipped\n")
    seed = json.loads((repo / "roadmap_seed.json").read_text())
    seed["entries"]["ROADMAP.md#completed"] = 2
    write(repo, "roadmap_seed.json", json.dumps(seed))
    r = roadmap(repo, queue)
    assert r.returncode == 1 and "rose from 1 to 2" in r.stderr, r.stderr


def test_no_queue_withholds_rule_4_with_a_stated_reason_never_a_crash(repo, queue):
    text, _ = PLANTED["queue-done"]
    fixture(repo, text)
    assert roadmap(repo, queue, "--seed").returncode == 0
    commit_all(repo)
    r = roadmap(repo, None)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "NOT JUDGED" in r.stdout and "no-queue.json" in r.stdout, r.stdout
    # A malformed queue is the same: a stated reason, not a traceback.
    bad = repo.parent / "bad-queue.json"
    bad.write_text("{not json")
    r = roadmap(repo, bad)
    assert r.returncode == 0 and "unreadable" in r.stdout, r.stdout
    # The --queue flag is the seam too, and wins over the environment.
    r = roadmap(repo, None, "--queue", str(queue))
    assert r.returncode == 0 and "NOT JUDGED" not in r.stdout, r.stdout


def test_a_queue_id_the_queue_does_not_hold_is_named_not_refused(repo, queue):
    fixture(repo, CLEAN.replace("Q.OPEN", "Q.GONE"))
    r = roadmap(repo, queue)
    assert r.returncode == 0 and "(queue: Q.GONE) names no item" in r.stdout, r.stdout


def _structural(repo: Path, queue: Path, scope: str):
    env = hermetic_env(GOH_ROADMAP_QUEUE=str(queue))
    extra = ("--keep-going",) if scope == "--full" else ()
    return run_goh(repo, "structural", scope, *extra, env=env)


def test_a_planted_done_row_is_refused_at_commit(repo, queue):
    fixture(repo, CLEAN)
    write(repo, "ROADMAP.md", CLEAN + "\n- **RT.TWO — old.** DONE\n")
    stage(repo, "ROADMAP.md")
    r = _structural(repo, queue, "--staged")
    assert r.returncode == 1 and "ROADMAP.md:22" in r.stdout + r.stderr, r.stdout + r.stderr


def test_a_commit_that_brings_no_plan_is_not_billed_for_the_queue_but_the_push_is(repo, queue):
    fixture(repo, PLANTED["queue-done"][0])
    write(repo, "a.py", "x = 1\n")
    stage(repo, "a.py")
    r = _structural(repo, queue, "--staged")
    assert "one roadmap, open entries only" in r.stdout, r.stdout
    assert "ROADMAP.md:11" not in r.stdout + r.stderr, r.stdout + r.stderr
    commit_all(repo)
    r = _structural(repo, queue, "--full")
    assert r.returncode == 1 and "ROADMAP.md:11" in r.stdout + r.stderr, r.stdout + r.stderr


# GOH.ROADMAPIDS: a tracked file citing a roadmap id the roadmap no longer holds.
G6 = "ROADMAP G6"  # roadmap-ok: the fixture's dead citation
OLD = "backlog 6.2"  # roadmap-ok: the fixture's dead citation, by the plan file's stem
ALIVE = "roadmap RT.ONE"  # roadmap-ok: held by the fixture's roadmap
HELD = "ROADMAP.md P0.4, roadmap AU.WAF and `ROADMAP` R1"  # roadmap-ok: all held
CITING = {
    ".github/workflows/ci.yml": f"# closes {G6} (dead)\n# see {ALIVE} (alive)\n",
    "src/a.py": f"# {HELD} are all held\n",
    "src/b.rs": f"// ({OLD}) dead too: the plan's own stem is read, any case\n",
    "src/c.py": f"# {G6} roadmap-ok: a fixture quoting it on purpose\n",
    "CHANGELOG.md": f"- closes {G6}: history keeps the ids it closed\n",
    "changelog/X.md": f"closes {G6}\n",
    "vendor/lib.py": f"# {G6} is someone else's roadmap\n",
}


def test_a_dead_roadmap_id_cited_in_a_tracked_file_is_red(repo, queue):
    fixture(repo, CLEAN, **CITING)
    write(repo, ".gatesrc", "GOH_ROADMAP=docs/BACKLOG.md\n")
    write(repo, "docs/BACKLOG.md", CLEAN)
    git(repo, "rm", "-q", "ROADMAP.md")
    commit_all(repo)
    r = roadmap(repo, queue, env_extra={"GOH_GRACE": "over"})
    assert r.returncode == 1, r.stdout + r.stderr
    assert f".github/workflows/ci.yml:1: {G6}" in r.stderr, r.stderr
    assert f"src/b.rs:1: {OLD}" in r.stderr, r.stderr
    for clean in ("RT.ONE", "src/a.py", "src/c.py", "CHANGELOG", "changelog/", "vendor/"):
        assert clean not in r.stderr, (clean, r.stderr)


def test_a_dead_roadmap_id_is_a_warning_within_its_grace_and_seeds(repo, queue):
    fixture(repo, CLEAN, **{"ci.yml": f"# closes {G6}\n"})
    r = roadmap(repo, queue, env_extra={"GOH_GRACE": "within"})
    out = r.stdout + r.stderr
    assert r.returncode == 0 and f"ci.yml:1: {G6}" in out, out
    # the week is the consumer's to seed in: the new key may rise against HEAD's seed
    assert roadmap(repo, queue, "--seed", env_extra={"GOH_GRACE": "within"}).returncode == 0
    seed = json.loads((repo / "roadmap_seed.json").read_text())
    assert seed["entries"] == {"ci.yml#dead-id": 1}, seed
    stage(repo, "roadmap_seed.json")
    env = hermetic_env(GOH_ROADMAP_QUEUE=str(queue), GOH_GRACE="within")
    r = run_goh(repo, "structural", "--staged", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    commit_all(repo)
    r = roadmap(repo, queue, env_extra={"GOH_GRACE": "over"})
    assert r.returncode == 0, r.stdout + r.stderr
