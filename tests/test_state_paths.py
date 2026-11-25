"""lib/state_paths.py (GOH.REPOKEY): the roots by one precedence in Python and in the binary's own
unit tests (crates/goh/src/goh_config.rs), a path and a repo in both forms, and a token only at the
start of a path field."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "lib"))
import db_core
import goh_db
import ship_records
import state_paths


@pytest.fixture
def host(monkeypatch, tmp_path: Path) -> Path:
    """A host with its own home, state and checkouts, none of them inside another's default."""
    for var in ("GOH_STATE_ROOT", "GOH_REPOS_ROOT", "GOH_LOOP_QUEUE", "GOH_LANDQ_DIR", "GOH_DB",
                "GOH_LOOPCTL", "GOH_SEED_DIR", "GOH_SHIP_DIR", "XDG_STATE_HOME"):  # fmt: skip
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "conf"))
    return tmp_path


def conf(host: Path, text: str) -> None:
    at = host / "conf" / "goh" / "config.toml"
    at.parent.mkdir(parents=True, exist_ok=True)
    at.write_text(text)


def test_the_defaults_are_under_the_home_and_xdg_moves_the_state(host: Path, monkeypatch):
    home = host / "home"
    assert state_paths.state_root() == home / ".local/state/goh"
    assert state_paths.repos_root() == home / "Projects"
    assert state_paths.loop_queue() == home / "Projects/.loop/queue.json"
    monkeypatch.setenv("XDG_STATE_HOME", str(host / "x"))
    assert state_paths.state_root() == host / "x/goh"
    # the ship directory ignored XDG_STATE_HOME while goh.db honoured it
    assert ship_records.ship_dir() == host / "x/goh/ship"
    assert goh_db.path() == host / "x/goh/goh.db"
    monkeypatch.setenv("XDG_STATE_HOME", "relative")
    assert state_paths.state_root() == home / ".local/state/goh"


def test_the_config_names_the_roots_every_seam_defaults_to_and_the_environment_wins(
    host: Path, monkeypatch
):
    conf(host, f'state_root = "{host}/s"\nrepos_root = "~/src"\nloop_queue = "/q/queue.json"\n')
    monkeypatch.setenv("XDG_STATE_HOME", str(host / "x"))
    assert state_paths.state_root() == host / "s"
    assert goh_db.path() == host / "s/goh.db"
    assert ship_records.ship_dir() == host / "s/ship"
    assert state_paths.landq_dir() == host / "s/landq"
    assert state_paths.seed_dir() == host / "s/seeds"
    assert state_paths.repos_root() == host / "home/src"
    assert state_paths.loopctl() == host / "home/src/.loop/tools/loopctl.py"
    assert state_paths.loop_queue() == Path("/q/queue.json")
    monkeypatch.setenv("GOH_STATE_ROOT", str(host / "e"))
    monkeypatch.setenv("GOH_REPOS_ROOT", "~/r")
    monkeypatch.setenv("GOH_LOOP_QUEUE", "q.json")
    assert state_paths.state_root() == host / "e"
    assert state_paths.repos_root() == host / "home/r"
    assert state_paths.loop_queue() == Path("q.json")


@pytest.mark.parametrize("text", ["state_root = 5\n", 'state_root = "rel"\n', "state_root =\n"])
def test_an_unusable_config_is_named_never_a_default(host: Path, text: str):
    conf(host, text)
    with pytest.raises(state_paths.BadConfig, match=r"config\.toml"):
        state_paths.state_root()


def test_an_unusable_override_or_config_file_is_named(host: Path, monkeypatch):
    (host / "conf/goh/config.toml").mkdir(parents=True)
    with pytest.raises(state_paths.BadConfig, match=r"config\.toml"):
        state_paths.repos_root()
    monkeypatch.setenv("GOH_REPOS_ROOT", "rel")
    with pytest.raises(state_paths.BadConfig, match="GOH_REPOS_ROOT"):
        state_paths.repos_root()


def test_a_suite_never_opens_the_database_the_real_homes_config_names(host: Path, monkeypatch):
    real = host / "real"
    (real / ".config/goh").mkdir(parents=True)
    (real / ".config/goh/config.toml").write_text(f'state_root = "{host}/elsewhere"\n')
    monkeypatch.setenv(db_core.REAL_HOME_ENV, str(real))
    with pytest.raises(db_core.RealHomeOpen):
        db_core.guard(host / "elsewhere/goh.db")


def roots(host: Path, monkeypatch) -> None:
    monkeypatch.setenv("GOH_STATE_ROOT", str(host / "home/state"))
    monkeypatch.setenv("GOH_REPOS_ROOT", str(host / "home/src"))


def test_a_path_goes_to_its_token_and_back_the_longest_root_first(host: Path, monkeypatch):
    roots(host, monkeypatch)
    for path, token in (("home/state/ship/a.json", "$STATE/ship/a.json"), ("home/state", "$STATE"),
                        ("home/src/group/app", "$REPOS/group/app"), ("home/x", "$HOMEDIR/x"),
                        ("home/state-old/a", "$HOMEDIR/state-old/a")):  # fmt: skip
        full = str(host / path)
        assert state_paths.encode(full) == token
        assert state_paths.decode(token) == full
        assert state_paths.forms(full) == state_paths.forms(token) == [full, token]
    for other in ("/elsewhere/a", "a/b", "$STATELY/a", "the log $STATE/a", "", None, 5):
        assert state_paths.encode(other) == other
        assert state_paths.decode(other) == other


def test_a_root_that_is_a_link_is_the_same_root(host: Path, monkeypatch):
    (host / "disk").mkdir()
    (host / "link").symlink_to(host / "disk")
    monkeypatch.setenv("GOH_STATE_ROOT", str(host / "link"))
    real = str((host / "disk").resolve())
    assert state_paths.encode(f"{real}/landq") == "$STATE/landq"
    assert state_paths.forms(f"{real}/landq") == [f"{real}/landq", "$STATE/landq"]


def test_a_repo_goes_to_its_key_and_back_under_every_spelling(host: Path, monkeypatch):
    roots(host, monkeypatch)
    home = host / "home"
    for path, key in ((home / "src/app", "app"), (home / "src/group/app", "group/app"),
                      (home / "tools/x", "~/tools/x")):  # fmt: skip
        assert state_paths.repo_key(str(path)) == key
        assert state_paths.repo(key) == state_paths.repo(str(path)) == str(path)
    assert state_paths.repo("$REPOS/group/app") == str(home / "src/group/app")
    full, key, token, slug = state_paths.repo_forms(home / "src/group/app")
    assert (full, key) == (str(home / "src/group/app"), "group/app")
    assert (token, slug) == ("$REPOS/group/app", "group_app")
    for other in ("", "two words", "?", None):
        assert state_paths.repo(other) == other
    assert state_paths.repo_key("/elsewhere/app") == "/elsewhere/app"


def test_another_home_reads_the_same_rows_at_its_own_paths(host: Path, monkeypatch):
    roots(host, monkeypatch)
    rec = {"repo": str(host / "home/src/app"), "log": str(host / "home/state/l.log")}
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    assert state_paths.put(con, "lands", rec) == rec  # meta.paths unset: today's form
    con.execute("INSERT INTO meta VALUES ('paths', 'token')")
    stored = state_paths.put(con, "lands", rec)
    assert stored == {"repo": "app", "log": "$STATE/l.log"}
    for var in ("GOH_STATE_ROOT", "GOH_REPOS_ROOT"):
        monkeypatch.delenv(var)
    monkeypatch.setenv("HOME", "/h2")
    assert state_paths.got("lands", stored) == {"repo": "/h2/Projects/app",
                                                "log": "/h2/.local/state/goh/l.log"}  # fmt: skip
    con.close()


def test_a_token_in_text_is_left_alone(host: Path, monkeypatch):
    roots(host, monkeypatch)
    con = sqlite3.connect(":memory:")
    assert not state_paths.tokens(con)  # a file from before `meta`
    con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO meta VALUES ('paths', 'token')")
    said = f"{host}/home/state/a.log is gone"
    rec = {"why": "$STATE/a.log is gone", "guards": [said], "cov_risk": {"said": said}, "log": 5,
           "red_logs": "$STATE"}  # fmt: skip
    assert state_paths.got("lands", rec) == rec
    assert state_paths.put(con, "lands", rec) == rec
    con.close()
