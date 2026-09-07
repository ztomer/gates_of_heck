#!/usr/bin/env python3
"""Structural gate for an agent SKILLS corpus (~/.claude/skills, ~/.agents/skills).

A skills corpus is code that nothing compiles, so every defect in it is silent.
Measured on one corpus of 38 skills before this gate existed: two skills with no
frontmatter at all (so nothing could ever trigger them — dead code that reads as
live), two `[[wikilinks]]` pointing at skills that do not exist, duplicated
"Related" blocks, and one SKILL.md that had grown to 2,294 lines because nothing
applied compaction pressure.

Six checks, each of which found a real defect on its first run:

  1. frontmatter    every SKILL.md opens with ---, carrying name: and description:
  2. name           the frontmatter name equals the directory name
  3. wikilinks      every [[target]] resolves to a sibling skill directory
  4. reflinks       every ](references/x.md) resolves to a file on disk
  5. size           SKILL.md stays under the word ceiling — this is the whole
                    point: it is what loads on invoke, and it grows without
                    bound unless something says no
  6. duplicates     no two skills carry the same LESSON-shaped ## title, which
                    is how one lesson comes to be restated in four places

The ceiling is a RATCHET, not a cap. Today's oversized skills are seeded into a
baseline with a reason each; a skill may only shrink, a NEW skill must be under
the ceiling, and an entry whose skill has come under the ceiling is STALE and
fails — so the burn-down cannot be quietly abandoned half-done.

    check_skills_corpus.py --root ~/.claude/skills
    check_skills_corpus.py --root ~/.claude/skills --max-words 5000
    check_skills_corpus.py --root ~/.claude/skills --update-baseline   # deliberate re-record

Exit 0 clean, 1 on any violation, 2 on a scope that cannot be measured.
"""
import argparse
import json
import re
import sys
from pathlib import Path

# The floor exists because "0 skills scanned, 0 violations" and a clean corpus
# print the same word. A renamed or unmounted tree must fail, not pass.
DEFAULT_MIN_SKILLS = 5
DEFAULT_MAX_WORDS = 5000
BASELINE_NAME = "skills_size_baseline.json"

FM_NAME = re.compile(r"^name:\s*(\S+)\s*$", re.M)
FM_DESC = re.compile(r"^description:\s*\S", re.M)
WIKILINK = re.compile(r"\[\[([A-Za-z0-9_\-]+)\]\]")
REFLINK = re.compile(r"\]\((references/[^)]+\.md)\)")
SECTION = re.compile(r"^##\s+(.+?)\s*$", re.M)

# Structural headings (Related, Checklist, The move, Anti-patterns...) recur by
# design — they are the corpus's shared skeleton, and flagging them is how a
# gate cries wolf and gets switched off. The first draft of this check did
# exactly that: 11 of its 12 findings were "Related appears in 15 skills".
#
# A LESSON heading is a sentence ("A PIPELINE SWALLOWS THE EXIT CODE YOU ARE
# CHECKING"); a structural one is a label ("Related", "The move"). Length
# separates them with no list to maintain, so a new structural heading nobody
# has thought of yet is exempt for free while a restated lesson is not.
LESSON_TITLE_CHARS = 30


def _fail(msg):
    print(f"  ✗ {msg}")


def _frontmatter(text):
    """Return the frontmatter block, or None when the file has none.

    Deliberately strict about the opening fence: a SKILL.md that merely
    CONTAINS `---` somewhere is not a skill with frontmatter, and treating it
    as one is how an untriggerable skill reads as fine.
    """
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 3)
    if end == -1:
        return None
    return text[4:end]


def _skill_dirs(root):
    return sorted(
        d for d in root.iterdir()
        if d.is_dir() and not d.name.startswith(".") and (d / "SKILL.md").exists()
    )


def _load_baseline(path):
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        # Refuse rather than fall back to {}: an unreadable baseline that reads
        # as "no exemptions" turns every seeded entry into a fresh violation and
        # buries the real signal in noise.
        print(f"  ✗ baseline {path} is unreadable: {exc}")
        sys.exit(2)
    return raw.get("oversized", {})


def check_corpus(root, max_words, min_skills, baseline_path, update=False):
    skills = _skill_dirs(root)
    if len(skills) < min_skills:
        print(f"  ✗ only {len(skills)} skill(s) under {root} "
              f"(floor {min_skills}) — the scope is wrong, not the corpus clean")
        return 2, {}

    names = {d.name for d in skills}
    baseline = _load_baseline(baseline_path)
    violations = 0
    sections = {}
    measured = {}
    files_read = 0

    for d in skills:
        f = d / "SKILL.md"
        text = f.read_text(errors="replace")
        files_read += 1
        rel = f"{d.name}/SKILL.md"

        fm = _frontmatter(text)
        if fm is None:
            _fail(f"{rel}: no YAML frontmatter — this skill can never be triggered")
            violations += 1
        else:
            m = FM_NAME.search(fm)
            if not m:
                _fail(f"{rel}: frontmatter has no `name:`")
                violations += 1
            elif m.group(1) != d.name:
                _fail(f"{rel}: name `{m.group(1)}` does not match directory `{d.name}`")
                violations += 1
            if not FM_DESC.search(fm):
                _fail(f"{rel}: frontmatter has no `description:` — nothing to match on")
                violations += 1

        # Links are checked across SKILL.md AND its references, because a dead
        # pointer in a reference file is exactly as dead.
        for md in [f] + sorted(d.glob("references/*.md")):
            body = md.read_text(errors="replace") if md != f else text
            if md != f:
                files_read += 1
            here = f"{d.name}/{md.relative_to(d)}"
            for target in set(WIKILINK.findall(body)):
                if target not in names:
                    _fail(f"{here}: [[{target}]] matches no skill")
                    violations += 1
            for target in set(REFLINK.findall(body)):
                if not (d / target).exists():
                    _fail(f"{here}: link to {target} — file does not exist")
                    violations += 1

        words = len(text.split())
        measured[d.name] = words
        allowed = baseline.get(d.name, {}).get("words")
        if words > max_words:
            if allowed is None:
                _fail(f"{rel}: {words} words > ceiling {max_words}. Split the case "
                      f"studies into references/ (progressive disclosure), or seed a "
                      f"baseline entry with a reason if it genuinely must be large.")
                violations += 1
            elif words > allowed:
                _fail(f"{rel}: {words} words, up from a baselined {allowed}. "
                      f"The ratchet only shrinks.")
                violations += 1
        elif allowed is not None:
            _fail(f"{rel}: {words} words is under the ceiling but still baselined "
                  f"as oversized — delete the stale entry; a fixed violation must "
                  f"take its exemption with it.")
            violations += 1

        for title in SECTION.findall(text):
            sections.setdefault(title.strip().lower(), []).append(d.name)

    for title, owners in sorted(sections.items()):
        if len(owners) > 1 and len(title) >= LESSON_TITLE_CHARS:
            _fail(f"section \"{title[:60]}\" appears in {len(owners)} skills "
                  f"({', '.join(sorted(owners))}) — one lesson, one home")
            violations += 1

    if update:
        over = {n: {"words": w, "reason": baseline.get(n, {}).get("reason", "unreviewed")}
                for n, w in measured.items() if w > max_words}
        baseline_path.write_text(json.dumps({"oversized": over}, indent=2, sort_keys=True) + "\n")
        print(f"  → baseline re-recorded: {len(over)} oversized skill(s)")
        return 0, measured

    if violations:
        print(f"  ✗ [skills_corpus] {violations} violation(s) across "
              f"{len(skills)} skills, {files_read} files")
        return 1, measured

    print(f"  ✓ [skills_corpus] OK — {len(skills)} skills, {files_read} files, "
          f"largest SKILL.md {max(measured.values())} words (ceiling {max_words})")
    return 0, measured


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    # Default to the CWD, never to ~/.claude/skills. A convenience default
    # pointing at a fixed path elsewhere made this checker measure the real
    # corpus no matter which tree it was aimed at — so it reported "37 skills
    # OK" while running inside an empty fixture, and the estate's own
    # skeleton-tree canary caught it on the first sweep. A checker measures
    # where it is pointed, or it is not measuring anything.
    ap.add_argument("--root", default=".",
                    help="corpus root (default: the current directory)")
    ap.add_argument("--max-words", type=int, default=DEFAULT_MAX_WORDS)
    ap.add_argument("--min-skills", type=int, default=DEFAULT_MIN_SKILLS)
    ap.add_argument("--baseline", default=None,
                    help=f"path to {BASELINE_NAME} (default: alongside --root)")
    ap.add_argument("--update-baseline", action="store_true",
                    help="re-record today's oversized set; deliberate, never automatic")
    args = ap.parse_args()

    root = Path(args.root).expanduser()
    if not root.is_dir():
        print(f"  ✗ {root} is not a directory")
        return 2
    baseline = Path(args.baseline).expanduser() if args.baseline else root / BASELINE_NAME

    code, _ = check_corpus(root, args.max_words, args.min_skills, baseline,
                           update=args.update_baseline)
    return code


if __name__ == "__main__":
    sys.exit(main())
