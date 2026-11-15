"""`goh seed-add` writes an entry where its repository's other seeds live (GOH.GRACEFWD).

Found by EST.GRACEFIX (2026-11-15): `goh seed-add --file floor_seed.json` wrote monitor's entry
to the private store, though monitor keeps its seeds tracked, so its hosted CI -- which cannot
read the store -- would have gone red on the next push. An entry is routed by its root commit:

* to the tracked file of the repository that has it (the one the command runs in, or one the
  estate registry names) when that repository tracks a `<kind>_seed.json` at its root, or its CI
  runs goh; a duplicate of it in the store is moved out, since one entry in two homes is refused;
* to the private store otherwise, as before.
"""

from __future__ import annotations

import json
from pathlib import Path

from _fast_git import fast_init
from conftest import commit_all, git, hermetic_env, run_goh, write

FILE = "floor_seed.json"
WHY = "the undeclared floors on the day the requirement landed"


def _root(repo: Path) -> str:
    return git(repo, "rev-list", "--max-parents=0", "HEAD").strip()


def _entry(repo: Path, key: str = "GOH_MOVING_TAGS_MIN_REFS") -> dict:
    return {"root": _root(repo), "repo": repo.name, "path": key, "count": 3, "reason": "r"}


def _pending(tmp: Path, *entries: dict) -> Path:
    p = tmp / "pending.json"
    p.write_text(json.dumps({"why": WHY, "entries": list(entries)}), encoding="utf-8")
    return p


def _env(tmp: Path, *estate: Path) -> dict[str, str]:
    state = tmp / "state"
    state.mkdir(exist_ok=True)
    (state / "installed_repos").write_text("".join(f"{p}\n" for p in estate), encoding="utf-8")
    return hermetic_env(
        GOH_SEED_DIR=str(tmp / "store"), GOH_ESTATE_STATE=str(state / "estate_audit.json")
    )


def _paths(f: Path) -> list[str]:
    return [e["path"] for e in json.loads(f.read_text(encoding="utf-8"))["entries"]]


def test_an_entry_for_a_repo_with_tracked_seeds_goes_to_its_tracked_file(
    repo: Path, tmp_path: Path
) -> None:
    write(repo, "tui_marks_seed.json", json.dumps({"why": "w", "entries": []}) + "\n")
    commit_all(repo)
    store = tmp_path / "store"
    store.mkdir()
    # A duplicate already in the store (the 2026-11-15 shape) moves out with the write.
    (store / FILE).write_text(json.dumps({"why": WHY, "entries": [_entry(repo)]}), encoding="utf-8")
    r = run_goh(
        repo, "seed-add", "--file", FILE, str(_pending(tmp_path, _entry(repo))), env=_env(tmp_path)
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert _paths(repo / FILE) == ["GOH_MOVING_TAGS_MIN_REFS"], r.stdout + r.stderr
    assert _paths(store / FILE) == [], "one fact in one place"
    assert "commit floor_seed.json there" in r.stdout + r.stderr, r.stdout + r.stderr


def test_the_estate_registry_finds_a_repo_the_command_does_not_run_in(
    repo: Path, tmp_path: Path
) -> None:
    checkout = "      - uses: actions/checkout@v5\n        with:\n"
    write(
        repo,
        ".github/workflows/ci.yml",
        f"jobs:\n  g:\n    steps:\n{checkout}          repository: o/gates_of_heck\n",
    )
    commit_all(repo)
    other = tmp_path / "other"
    fast_init(other)
    write(other, "README.md", "# other\n")
    commit_all(other, "root")
    here = _entry(repo)
    away = _entry(other)
    r = run_goh(
        other,
        "seed-add",
        "--file",
        FILE,
        str(_pending(tmp_path, here, away)),
        env=_env(tmp_path, repo),
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert _paths(repo / FILE) == ["GOH_MOVING_TAGS_MIN_REFS"], "its CI runs goh: tracked"
    assert not (other / FILE).exists(), "no tracked seed and no goh CI: the store"
    assert [e["root"] for e in json.loads((tmp_path / "store" / FILE).read_text())["entries"]] == [
        away["root"]
    ]


def test_a_repo_with_no_tracked_seeds_keeps_its_entries_in_the_store(
    repo: Path, tmp_path: Path
) -> None:
    r = run_goh(
        repo, "seed-add", "--file", FILE, str(_pending(tmp_path, _entry(repo))), env=_env(tmp_path)
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (repo / FILE).exists()
    assert _paths(tmp_path / "store" / FILE) == ["GOH_MOVING_TAGS_MIN_REFS"]
