"""A command that prints a stack's resolved secrets is refused at the tool boundary (SRV.NTFYROTATE).

THE CLASS. During SRV.NTFY (2026-11-07) an agent ran, over `ssh media-server 'bash -s'`,

    docker compose config 2>&1 | grep NTFY_AUTH | sed -E 's/...\\$2a\\$10\\$.../<bcrypt>/'

`docker compose config` interpolates `.env` (and inlines every `env_file:`) into what it prints, and
it re-escapes `$` as `$$` on the way out, so the redaction never matched and both ntfy password
hashes landed in the transcript; both passwords and the publish token had to be rotated. A
redaction applied after the render is a guess about the render's spelling. The invariant: a
command whose output reaches the transcript never carries an interpolated value.

The Claude Code Bash guard refuses the shapes that print one (`lib/secret_render.py`): a compose
`config` that renders values, and a `docker inspect` that dumps a container's environment -- in
the command, AND in the code it hands to `ssh`/a shell (a quoted script, a heredoc body). Prose is
not code: a heredoc written to a file, a commit message, an `echo` string. `# secrets-ok: <reason>`
is the one escape.

The allowed forms are calibrated against REAL compose over a fixture whose `.env` and `env_file`
carry planted values: every form the guard lets through must print none of them, and the bare form
must print them (else the fixture is blind and proves nothing).
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from test_claude_load_guard import _decide

PLANTED_ENV = "plantedEnvSecret7Q"
PLANTED_FILE = "plantedEnvFileSecret9Z"

REFUSED = [
    "docker compose config",
    "docker compose config | grep NTFY",
    "cd ~/prj/media_server && docker compose config 2>&1 | grep -E NTFY_AUTH",
    "docker compose -p media_server --env-file ~/prj/media_server/.env config",
    "docker compose -f compose/infra.yml config ntfy",
    "docker compose config --format json",
    "docker compose config --environment",
    "docker compose config -o /tmp/render.yml",
    "docker-compose config",
    "docker --context default compose config",
    "sudo -n docker compose config",
    "timeout 30 docker compose config",
    # the measured form: a script handed to the host over ssh in a heredoc
    (
        "ssh media-server 'bash -s' <<'EOF'\ncd ~/prj/media_server\n"
        "docker compose config 2>&1 | grep NTFY_AUTH\nEOF"
    ),
    "ssh media-server 'cd ~/prj/media_server && docker compose config'",
    'ssh media-server "cd ~/prj/media_server && docker compose config | grep ntfy"',
    "bash -c 'docker compose config'",
    "bash -s <<EOF\ndocker compose config\nEOF",
    'echo "$(docker compose config)"',
    # a container's resolved environment, the same values by another door
    "docker inspect ntfy",
    "docker container inspect ntfy",
    "docker inspect --format json ntfy",
    "docker inspect -f '{{json .Config}}' ntfy",
    "docker inspect -f '{{json .}}' ntfy",
    "docker inspect --format '{{.Config.Env}}' ntfy",
    "ssh media-server 'docker inspect ntfy | head -50'",
]

ALLOWED = [
    "docker compose config --no-interpolate",
    "docker compose config --no-interpolate --format json",
    "docker compose config --services",
    "docker compose config --images",
    "docker compose config --volumes",
    "docker compose config --networks",
    "docker compose config --profiles",
    "docker compose config --variables",
    "docker compose config --hash '*'",
    "docker compose config -q",
    "docker compose config --quiet",
    "docker compose -p proj -f - config --hash '*'",
    "docker compose ps",
    "docker compose up -d --no-deps ntfy",
    "ssh media-server 'docker compose config --services | wc -l'",
    "docker inspect -f '{{.State.Health.Status}}' ntfy",
    "docker inspect --format='{{json .Config.Labels}}' ntfy",
    # prose is not code: a commit message, an echo, a heredoc written to a file, a grep pattern
    "git commit -m 'gates: refuse docker compose config (it prints secrets)'",
    "echo 'never run docker compose config here'",
    "cat > msg.txt <<'EOF'\n`docker compose config` prints interpolated secrets\nEOF",
    "grep -rn 'docker compose config' docs/",
    # the escape
    "docker compose config  # secrets-ok: a fixture stack with no secrets",
]


@pytest.mark.parametrize("command", REFUSED)
def test_a_command_that_prints_resolved_secrets_is_refused(command: str, tmp_path: Path) -> None:
    reason = _decide(command, tmp_path)
    assert reason is not None, f"the guard let a secret-printing command run: {command!r}"
    assert "secrets-ok" in reason, reason


@pytest.mark.parametrize("command", ALLOWED)
def test_a_command_that_prints_no_value_runs(command: str, tmp_path: Path) -> None:
    assert _decide(command, tmp_path) is None, command


def test_the_refusal_names_the_ways_out(tmp_path: Path) -> None:
    reason = _decide("docker compose config", tmp_path)
    assert reason is not None
    for want in ("--no-interpolate", "--services", "--hash", "deploy.sh --plan", "secrets-ok"):
        assert want in reason, (want, reason)
    reason = _decide("docker inspect ntfy", tmp_path)
    assert reason is not None
    assert "--format" in reason, reason


def _compose() -> list[str] | None:
    """The compose CLI on this machine: the docker plugin, else the standalone binary."""
    docker = shutil.which("docker")
    if docker:
        probe = subprocess.run(
            [docker, "compose", "version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
        if probe.returncode == 0:
            return [docker, "compose"]
    standalone = shutil.which("docker-compose")
    return [standalone] if standalone else None


@pytest.fixture
def planted(tmp_path: Path) -> Path:
    (tmp_path / ".env").write_text(f"PLANT_A={PLANTED_ENV}\n")
    (tmp_path / "side.env").write_text(f"PLANT_B={PLANTED_FILE}\n")
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  t:\n    image: busybox\n    env_file: side.env\n"
        "    environment:\n      - A=${PLANT_A}\n"
    )
    return tmp_path


def _compose_forms(commands: list[str]) -> list[list[str]]:
    """The argv after `compose` of each plain, local `docker compose ... config` command."""
    forms = []
    for command in commands:
        if "secrets-ok" in command:
            continue
        argv = shlex.split(command)
        if argv[:2] == ["docker", "compose"] and "config" in argv and "|" not in argv:
            forms.append(argv[2:])
    return forms


def test_every_allowed_compose_form_prints_no_planted_value(planted: Path) -> None:
    """The allow list is calibrated against compose itself, never assumed: run each form the guard
    lets through over a fixture whose `.env` and `env_file` carry planted values."""
    compose = _compose()
    if compose is None:
        pytest.skip("no compose CLI (docker compose / docker-compose) on this machine")
    forms = [f for f in _compose_forms(ALLOWED) if "-" not in f]  # `-f -` reads stdin
    assert forms, "no allowed compose form to calibrate"
    for form in forms:
        out = subprocess.run(
            [*compose, *form],
            cwd=planted,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        assert out.returncode == 0, (form, out.stderr)
        printed = out.stdout + out.stderr
        assert PLANTED_ENV not in printed and PLANTED_FILE not in printed, (
            f"`compose {' '.join(form)}` is allowed by the guard and printed a planted secret"
        )


def test_the_refused_forms_do_print_the_planted_values(planted: Path) -> None:
    """The instrument can see a leak: the forms the guard refuses really print the values, so the
    calibration above is not green because the fixture is blind."""
    compose = _compose()
    if compose is None:
        pytest.skip("no compose CLI (docker compose / docker-compose) on this machine")
    leaked = 0
    for form in (["config"], ["config", "--format", "json"], ["config", "--environment"]):
        out = subprocess.run(
            [*compose, *form],
            cwd=planted,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        assert out.returncode == 0, (form, out.stderr)
        leaked += PLANTED_ENV in out.stdout
    assert leaked == 3, f"only {leaked} of 3 refused forms printed the planted .env value"
