"""GOH_PROVEN_CLOSURE, the switch for closure keying (GOH.PROVENCLOSURE2): off, the default until
GOH.REUSEAUDIT samples `step`, keys every proven step on the whole goh content as before
GOH.PROVENCLOSURE and computes no closure; `on` keys each step on its own closure line."""

import subprocess
from pathlib import Path

from test_proven import STEP, env, goh, proven, repo, runs  # noqa: F401  (fixtures)


def _sourced_key(goh: Path, repo: Path, env: dict) -> tuple[str, str]:  # noqa: F811
    """proven_key on the bash face, and the pre-closure formula (07bb29d2) on its identity."""
    script = (
        f'. "{goh}/gates/_proven.sh"; _proven_native_key() {{ return 1; }}; '
        "proven_settings || exit 2; "
        'PROVEN_BY_CLOSURE=1; s=\'true\'; pair="$(proven_key "$s")" || exit 1; '
        'want="$({ printf \'proven v1\\ntree %s\\nstep %s\\n\' "${pair#* }" "$s"; '
        'proven_identity; } | hash_hex /dev/stdin)"; printf \'%s\\n%s\\n\' "${pair% *}" "$want"'
    )
    r = subprocess.run(["bash", "-c", script], cwd=repo, env=env, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, check=False)  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr
    got, want = r.stdout.split()
    return got, want


def test_closure_keying_is_off_by_default_and_keys_on_the_whole_content(goh, repo, env):  # noqa: F811
    """GOH.PROVENCLOSURE2: unset, a step's key is the pre-closure key (the bare step, the goh
    content lines in the identity) and no closure is computed; `on` keys on the closure line."""
    got, want = _sourced_key(goh, repo, env)
    assert got == want
    r = proven(goh, repo, env, STEP, "--explain")
    lines = r.stdout.splitlines()
    assert lines[2] == f"step {STEP}" and lines[3].startswith("goh content "), r.stdout
    assert not any(x.startswith("closure ") for x in lines), r.stdout
    proven(goh, repo, env)
    assert not (repo / ".git" / "goh-proven-closure").exists(), "a closure was computed"
    on = dict(env, GOH_PROVEN_CLOSURE="on")
    got_on, want_on = _sourced_key(goh, repo, on)
    assert got_on != want_on
    r = proven(goh, repo, on, STEP, "--explain")
    assert r.stdout.splitlines()[3].startswith("closure "), r.stdout


def test_a_bad_closure_setting_is_a_usage_error(goh, repo, env):  # noqa: F811
    r = proven(goh, repo, dict(env, GOH_PROVEN_CLOSURE="yes"))
    assert r.returncode == 2 and "'yes'" in r.stderr
    assert runs(env) == 0
