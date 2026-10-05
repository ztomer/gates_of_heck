"""`check_no_credential_urls.py`: a credential in a git remote URL is invisible to every other gate.

    tests/test_check_no_credential_urls.py

`check_no_secrets.py` reads TRACKED FILES. `.git/config` is untracked by definition, so a live token
in a remote URL survives every sweep the estate runs — and it was proven in a scratch repo, with a
`gho_` token in `remote.origin.url`, while `check_no_secrets.py` printed `OK`. That is not a gap in
one checker's patterns; it is a scope no commit-scoped checker can reach.

So the rule is stated over REMOTES, which is the only place the class lives, and the interesting
work is the FALSE POSITIVES: `ssh://git@github.com/...` is a userinfo and must stay silent,
`https://user@host/...` is a userinfo that is very often just a username, and a `file://` or
`/local/path` remote has no host to be a credential against.

Every row below is measured by running `git config --get-regexp` over a scratch repo with that URL
planted — the table in the checker's docstring is that measurement, and `--probe` re-runs it.
"""

import subprocess

import pytest

from conftest import REPO_ROOT as ROOT  # noqa: F401
from conftest import git, run_check

CHECK = "checks/check_no_credential_urls.py"

LIVE_TOKEN = "gho_" + "a" * 36


def remote(repo, name, url):
    git(repo, "config", f"remote.{name}.url", url)
    return repo


def findings(repo, *args):
    return run_check(repo, CHECK, *args)


def out_of(got):
    return got.stdout + got.stderr


# ── it must SEE the class its sibling cannot ────────────────────────────────


def test_a_token_in_a_remote_url_is_refused(repo):
    """The incident, in miniature. And the sibling's verdict on the same repo is asserted below,
    because a checker that only passes where another one fails is not covering a new class."""
    remote(repo, "origin", f"https://user:{LIVE_TOKEN}@github.com/o/r.git")
    got = findings(repo)
    assert got.returncode == 1, out_of(got)
    assert "origin" in out_of(got)
    assert "github.com" in out_of(got)


def test_the_secret_checker_says_OK_over_the_same_repo(repo):
    """Why this checker exists, as a test rather than as a claim in a docstring.

    Both checkers run over one repo holding a live-looking `gho_` token in `remote.origin.url`.
    `check_no_secrets.py` must report green: it is commit-scoped, and `.git/config` is untracked.
    If that ever goes red, the scope argument is wrong and this checker's reason to exist is wrong
    with it.
    """
    remote(repo, "origin", f"https://user:{LIVE_TOKEN}@github.com/o/r.git")
    sibling = run_check(repo, "checks/check_no_secrets.py")
    assert sibling.returncode == 0, out_of(sibling)
    assert findings(repo).returncode == 1, out_of(findings(repo))


def test_the_token_only_shape_is_refused(repo):
    """`https://TOKEN@host/...` — the shape with no `user:` and no colon at all, which is what
    `netrc` and several hosts' own docs produce. A rule that only looked for `user:pass` would miss
    it, and a rule that keyed on `@` would also catch every ssh remote."""
    remote(repo, "origin", f"https://{LIVE_TOKEN}@github.com/o/r.git")
    got = findings(repo)
    assert got.returncode == 1, out_of(got)
    assert "github.com" in out_of(got)


def test_the_credential_is_never_printed(repo):
    """The estate already learned this with `gho_` and `ghp_`: a gate that prints the secret puts
    it in a terminal scrollback, a CI log and a paste. The finding carries a FINGERPRINT instead."""
    remote(repo, "origin", f"https://user:{LIVE_TOKEN}@github.com/o/r.git")
    got = findings(repo)
    assert got.returncode == 1, out_of(got)
    assert LIVE_TOKEN not in out_of(got)
    assert "ghp_" not in out_of(got)
    assert "sha256:" in out_of(got) or len([c for c in out_of(got) if c in "0123456789abcdef"]) >= 8


# ── and it must stay QUIET on the shapes that are not credentials ───────────


def test_an_ssh_remote_is_silent(repo):
    """The single most common remote in the estate, and a userinfo by every syntactic reading:
    `git@github.com` is userinfo. It is a username, and refusing it would make this gate noise in
    every repo on the first day."""
    remote(repo, "origin", "ssh://git@github.com/o/r.git")
    assert findings(repo).returncode == 0, out_of(findings(repo))


def test_an_ssh_remote_with_a_token_is_refused(repo):
    """The row that killed the first version's scheme allow-list, kept as a test because a removed
    rule is exactly the rule that comes back.

    Measured rather than argued: git reads an `ssh://` userinfo as the SSH USERNAME and hands the
    rest to `ssh`, which ignores a password — so it is not a live credential on the wire. It is also
    a plaintext secret in `.git/config`, which is what this gate is for. A scheme allow-list of
    `http`/`https` called it clean.
    """
    remote(repo, "origin", f"ssh://{LIVE_TOKEN}@github.com/o/r.git")
    assert findings(repo).returncode == 1, out_of(findings(repo))
    remote(repo, "origin", "ssh://user:sekritpass@github.com/o/r.git")
    assert findings(repo).returncode == 1, out_of(findings(repo))


def test_a_password_containing_an_at_sign_names_the_right_host(repo):
    """`https://user:p@ss@github.com` — where a one-line `url.split("@")` reads `p@ss` as the host
    and reports the finding against a host that does not exist.

    RFC 3986 says the LAST `@` separates the userinfo from the host, and that is what `urlsplit`
    does. The row is here because the wrong version of it still exits 1: the verdict looked right and
    the report pointed at the wrong machine, which is the failure nobody reads past the exit code.
    """
    remote(repo, "origin", "https://user:p@ss@github.com/o/r.git")
    got = findings(repo)
    assert got.returncode == 1, out_of(got)
    assert "github.com" in out_of(got), out_of(got)
    assert "p@ss" not in out_of(got).split("github.com")[0], out_of(got)
    # ...and the FINGERPRINT is over `user:p@ss` and not over `user`, which is the other half of the
    # same split. Two credentials differing only after the `@` must not share a handle, or the
    # fingerprint stops being able to tell them apart.
    # The fingerprint is over `user:p@ss` and not over `user`. Pinned to the DIGEST rather than to
    # an equality between two calls, because comparing two runs cannot see a split that is wrong in
    # both of them -- which is exactly what the naive version does.
    import hashlib

    want = "sha256:" + hashlib.sha256(b"user:p@ss").hexdigest()[:12]
    assert want in out_of(got), f"the fingerprint is not over the whole userinfo: {out_of(got)}"


def test_the_scp_form_is_silent(repo):
    """`git@github.com:o/r.git` is not a URL at all — `urlsplit` gives an empty scheme and no
    netloc. Measured, not assumed: it is the shape `git clone git@github.com:...` writes."""
    remote(repo, "origin", "git@github.com:o/r.git")
    assert findings(repo).returncode == 0, out_of(findings(repo))


def test_a_clean_https_remote_is_silent(repo):
    remote(repo, "origin", "https://github.com/o/r.git")
    got = findings(repo)
    assert got.returncode == 0, out_of(got)
    assert "remote" in out_of(got).lower()


@pytest.mark.parametrize(
    "url,why",
    [
        ("file:///Users/x/thing", "a local path, no host and no credential"),
        ("/Users/x/thing", "a local path git accepts verbatim"),
        ("../sibling", "a relative path"),
        ("https://github.com", "a remote with no path at all"),
        ("git://github.com/o/r.git", "the unauthenticated git protocol"),
        ("https://[::1]:8080/o/r", "an IPv6 literal, whose netloc is full of colons"),
    ],
)
def test_shapes_that_are_not_credentials_stay_silent(repo, url, why):
    remote(repo, "origin", url)
    got = findings(repo)
    assert got.returncode == 0, f"{url} — {why}\n{out_of(got)}"


def test_a_username_only_userinfo_is_not_a_finding_by_itself(repo):
    """`https://user@github.com/...` is a BARE userinfo: some hosts use one, and a token is also
    shaped like one. Measured over this estate's own remotes: 0 of them use a bare username, and
    none of them carry a token — but the honest rule is that a bare userinfo is only a finding when
    it LOOKS like a credential, and this row is the control that says the rule did not become
    "any `@`"."""
    remote(repo, "origin", "https://someuser@github.com/o/r.git")
    assert findings(repo).returncode == 0, out_of(findings(repo))


def test_a_bare_userinfo_that_looks_like_a_token_is_refused(repo):
    """...and the same shape with a credential-shaped value IS one. Without this row, "bare userinfo
    is never a finding" would be a rule that silently drops the `https://TOKEN@host/` case."""
    remote(repo, "origin", "https://ghp_" + "b" * 36 + "@github.com/o/r.git")
    assert findings(repo).returncode == 1, out_of(findings(repo))


# ── every remote is read, and a second one is not lost ──────────────────────


def test_every_remote_is_judged_not_just_origin(repo):
    remote(repo, "origin", "https://github.com/o/r.git")
    remote(repo, "backup", f"https://u:{LIVE_TOKEN}@gitlab.com/o/r.git")
    got = findings(repo)
    assert got.returncode == 1, out_of(got)
    assert "backup" in out_of(got)
    assert "gitlab.com" in out_of(got)


def test_a_push_url_is_judged_too(repo):
    r"""`remote.<name>.pushurl` is a URL git will push to and fetch never reads, so a rule scoped to
    `.url` leaves the write side open. Measured: `git config --get-regexp 'remote\..*\.url'` does NOT
    match `pushurl`... and a regex written as `remote\..*url` does. The checker must ask for both by
    name rather than by suffix."""
    git(repo, "config", "remote.origin.pushurl", f"https://u:{LIVE_TOKEN}@github.com/o/r.git")
    got = findings(repo)
    assert got.returncode == 1, out_of(got)
    assert "pushurl" in out_of(got)


def test_an_instead_of_rewrite_is_judged(repo):
    """`url.<base>.insteadOf` rewrites a remote at fetch time, so a credential can sit there while
    every `remote.*.url` is clean — which is exactly the shape a gate reading only remotes misses.

    The credential is in the KEY, not the value: the key is the base URL git rewrites TO and the
    value is the prefix it rewrites away from. A checker that reads values here sees
    `https://github.com/` and passes.
    """
    git(
        repo,
        "config",
        f"url.https://u:{LIVE_TOKEN}@proxy.invalid/.insteadOf",
        "https://github.com/",
    )
    got = findings(repo)
    assert got.returncode == 1, out_of(got)
    assert "insteadOf" in out_of(got)
    assert "proxy.invalid" in out_of(got)
    assert LIVE_TOKEN not in out_of(got)


def test_an_instead_of_rewrite_with_a_bare_username_is_silent(repo):
    """The control, and it is why the rule is one rule: a proxy URL carrying a USERNAME looks exactly
    like one carrying a token, so the discriminator cannot be "there is an `@` here" -- it is the same
    token-prefix / opaque-length test every other row uses. Without this row the insteadOf path could
    have been written as "any userinfo is a finding" and stayed green on the rows above."""
    git(repo, "config", "url.https://buildbot@proxy.invalid/.insteadOf", "https://github.com/")
    assert findings(repo).returncode == 0, out_of(findings(repo))


# ── the self-proof, and the refusals ────────────────────────────────────────


def test_the_probe_goes_red_on_a_planted_credential_and_green_on_the_rest(repo):
    got = run_check(ROOT, CHECK, "--probe")
    assert got.returncode == 0, out_of(got)


def test_a_repo_with_no_remote_is_a_named_non_run_not_a_pass(repo):
    """The empty-scope case, and it was FOUND by `check_empty_scope.py` rather than by me.

    A skeleton repo has a `.git/config` and no remote in it. This gate read "zero URLs, zero
    findings" as a clean pass, and a gate that reports compliance having inspected nothing is the
    exact failure `checks/empty_scope_allow.json` exists to refuse — the excuse file is for a gate
    that CANNOT answer, and this one can: it has nothing to judge, and says so.

    The phrase matters as much as the exit code. `check_empty_scope.py` reads the literal string
    `not applicable` and nothing else, so this is the gate declaring the non-run rather than the
    sweep inferring it.
    """
    # Hermetic: the gate also judges global/system helpers, and this machine has some.
    got = subprocess.run(
        ["python3", str(ROOT / CHECK)],
        cwd=repo,
        capture_output=True,
        text=True,
        env=dict(__import__("os").environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1"),
    )
    assert got.returncode == 0, out_of(got)
    assert "not applicable" in out_of(got), out_of(got)
    assert "OK —" not in out_of(got), out_of(got)


def test_the_empty_scope_sweep_accepts_this_gate(tmp_path):
    """...and the sweep agrees, end to end, over a real skeleton. The string assertion above is the
    contract; this is the counterparty reading it."""
    import shutil

    root = tmp_path / "skeleton"
    (root / "checks").mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "s@e.invalid")
    git(root, "config", "user.name", "s")
    for name in (
        "check_no_credential_urls.py",
        "_credential_config.py",
        "_credential_urls_probe.py",
        "check_no_secrets.py",
        "_gitutil.py",
    ):
        shutil.copy(ROOT / "checks" / name, root / "checks" / name)
    shutil.copytree(ROOT / "tui", root / "tui")
    git(root, "add", "-A")
    git(root, "-c", "user.name=s", "-c", "user.email=s@e.invalid", "commit", "-qm", "skeleton")
    got = subprocess.run(
        ["python3", str(ROOT / "checks" / "check_empty_scope.py"), "--root", str(root)],
        capture_output=True,
        text=True,
    )
    assert "check_no_credential_urls.py PASSED over an empty tree" not in out_of(got), out_of(got)
    assert "check_no_credential_urls.py gave NO verdict" not in out_of(got), out_of(got)


def test_outside_a_git_repo_is_a_usage_error_not_a_pass(tmp_path):
    """Contract 7: a check that could not read its subject must say so. `.git/config` is the
    subject, so a directory that is not a repo has nothing to judge — and printing OK there would be
    the checker reporting success over nothing."""
    import os

    bare = tmp_path / "not-a-repo"
    bare.mkdir()
    got = subprocess.run(
        ["python3", str(ROOT / CHECK)],
        cwd=str(bare),
        capture_output=True,
        text=True,
        env=dict(os.environ, GIT_CEILING_DIRECTORIES=str(tmp_path)),
    )
    assert got.returncode == 2, out_of(got)
    assert "not a git repo" in out_of(got)


# ── the two NON-URL shapes in .git/config: a credential helper and an extra header ─────────────
# Both were stated out of scope when this checker shipped, each measured to carry a token. They are
# judged with check_no_secrets.py's own patterns plus the two shapes those patterns cannot see: an
# unquoted `password=` inside a helper script, and the credential after an Authorization scheme.


@pytest.mark.parametrize(
    "key,value",
    [
        ("credential.helper", "!f() { echo username=x; echo password=s3cretvalue; }; f"),
        ("credential.helper", f"!f() {{ echo password={LIVE_TOKEN}; }}; f"),
        ("credential.https://github.com.helper", "!echo password=hunter22hunter22"),
        ("http.extraheader", "AUTHORIZATION: basic dXNlcjpwYXNzd29yZA=="),
        ("http.https://github.com/.extraHeader", f"Authorization: Bearer {LIVE_TOKEN}"),
        ("http.extraheader", "PRIVATE-TOKEN: glpat-abcdefghijklmnopqrst"),
    ],
)
def test_a_credential_in_a_helper_or_header_is_refused(repo, key, value):
    git(repo, "config", "--add", key, value)
    got = findings(repo)
    assert got.returncode == 1, out_of(got)
    secret = value.split("password=")[-1].split(";")[0].split()[-1]
    assert secret not in out_of(got), "the report printed the credential"
    assert "sha256:" in out_of(got), out_of(got)


@pytest.mark.parametrize(
    "key,value",
    [
        ("credential.helper", "osxkeychain"),
        ("credential.helper", "store"),
        ("credential.helper", "cache --timeout=3600"),
        ("credential.helper", "!gh auth git-credential"),
        ("credential.helper", "!f() { echo username=x; echo password=$GH_TOKEN; }; f"),
        ("credential.helper", ""),
        ("http.extraheader", "X-Trace: on"),
        ("http.extraheader", "Authorization: Bearer ${TOKEN}"),
    ],
)
def test_a_helper_or_header_holding_no_credential_is_silent(repo, key, value):
    git(repo, "config", "--add", key, value)
    got = findings(repo)
    assert got.returncode == 0, out_of(got)


def test_a_config_credential_is_judged_in_a_repo_with_no_remote(repo):
    """The `not applicable` path is for a config with nothing to judge -- a helper IS something."""
    git(repo, "config", "credential.helper", f"!echo password={LIVE_TOKEN}")
    got = findings(repo)
    assert got.returncode == 1 and "not applicable" not in out_of(got), out_of(got)


def test_global_helpers_alone_are_judged_but_do_not_make_the_repo_applicable(repo, tmp_path):
    """Found by the empty-tree sweep: a repo with no remote, on a machine whose ~/.gitconfig holds
    helpers, printed `OK` -- compliance over a repo config with nothing in it. The global values are
    still JUDGED (a token there leaks too) and the verdict says the repo had nothing of its own."""
    import os

    glob = tmp_path / "global.gitconfig"
    glob.write_text("[credential]\n\thelper = osxkeychain\n")
    env = dict(os.environ, GIT_CONFIG_GLOBAL=str(glob), GIT_CONFIG_NOSYSTEM="1")
    got = subprocess.run(
        ["python3", str(ROOT / CHECK)], cwd=repo, capture_output=True, text=True, env=env
    )
    assert got.returncode == 0 and "not applicable" in out_of(got), out_of(got)
    glob.write_text(f"[credential]\n\thelper = !echo password={LIVE_TOKEN}\n")
    got = subprocess.run(
        ["python3", str(ROOT / CHECK)], cwd=repo, capture_output=True, text=True, env=env
    )
    assert got.returncode == 1 and str(glob) in out_of(got), out_of(got)
