"""A command that prints a stack's resolved secrets into the transcript (SRV.NTFYROTATE, 2026-11-08).

THE CLASS. During SRV.NTFY an agent ran, over `ssh media-server 'bash -s'` with a heredoc,
`docker compose config 2>&1 | grep NTFY_AUTH | sed -E 's/...\\$2a\\$10\\$.../<bcrypt>/'`.
`docker compose config` interpolates `.env` and inlines every `env_file:` into what it prints, and
re-escapes `$` as `$$` on the way out -- so the redaction never matched, both ntfy password hashes
landed in the transcript, and both passwords and the publish token had to be rotated. A
redaction applied after the render is a guess about the render's spelling. The invariant: a
command whose output reaches the transcript never carries an interpolated value. `docker inspect`
without a narrow `--format` is the same values by another door (`.Config.Env`).

THE RULE. In the CODE of a command, a `docker compose ... config` (or `docker-compose`) is refused
unless it asks for a part that holds no value -- `--no-interpolate`, `--services`, `--images`,
`--volumes`, `--networks`, `--profiles`, `--variables`, `--hash`, `-q`/`--quiet` (each calibrated
against real compose over planted values: tests/test_secret_render_guard.py); `--environment`
is refused even beside one of them. A `docker inspect` / `docker container inspect` is refused
unless its `--format` names something narrower than the whole object, its `.Config`, or an `Env`.

WHAT IS CODE. The command itself (quoted spans, heredoc bodies and comments masked by
`busyloop.mask`), AND the text it hands to another shell to run: a quoted argument or a heredoc
body of a simple command whose program is `ssh` or a shell (`bash -s <<EOF`, `bash -c '...'`,
`ssh host '...'`), and a double-quoted span carrying a command substitution. The leak was a heredoc
body sent to `ssh ... 'bash -s'`, so a guard that read heredocs as text would have let it through.
Everything else quoted is TEXT: a commit message, an `echo`, a heredoc written to a file, a grep
pattern.

Not judged: a FILE. The fleet's scripts capture the render into a variable to hash it
(`compose_converge_lib.sh`); what reaches a transcript is a command's output.

The one escape is `# secrets-ok: <reason>`.
"""

from __future__ import annotations

import os
import re
import shlex

import busyloop

ESCAPE = re.compile(r"#\s*secrets-ok:\s*\S")

_SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
_HANDS_CODE_ON = _SHELLS | {"ssh"}
_WRAPPERS = {"sudo", "doas", "env", "timeout", "nohup", "exec", "command", "nice", "time", "rtk"}
_DOCKER = re.compile(r"(?<![\w./-])(?P<prog>docker-compose|docker)(?![\w.-])")
_TERMINATORS = set("|;&)`<>\n")

# docker's and compose's global options that take a value (the next token, when not `--x=v`).
_DOCKER_VALUED = {"-c", "--context", "--config", "-H", "--host", "-l", "--log-level",
                  "--tlscacert", "--tlscert", "--tlskey"}  # fmt: skip
_COMPOSE_VALUED = {"-f", "--file", "-p", "--project-name", "--env-file", "--project-directory",
                   "--profile", "--ansi", "--progress", "--parallel"}  # fmt: skip

# `config` flags whose output holds no interpolated value (calibrated, see the module docstring).
SAFE_CONFIG = {"--no-interpolate", "--services", "--images", "--volumes", "--networks",
               "--profiles", "--variables", "--hash", "-q", "--quiet"}  # fmt: skip
# `inspect --format` templates that print the whole object, its config, or its environment.
_WIDE_FORMAT = re.compile(
    r"^\s*json\s*$|\{\{\s*(?:json\s+)?\.\s*\}\}|json\s+\.Config\s*\}\}|\bEnv\b"
)

WAY_OUT = (
    "Read the definition without the values: `docker compose config --no-interpolate` (it keeps "
    "`${VAR}`), or ask only for what you need: `--services`, `--images`, `--volumes`, "
    "`--networks`, `--profiles`, `--variables` (names, not values), `--hash '*'`, `-q` (validate). "
    "To predict what a deploy recreates, `deploy.sh --plan <tag>` names it (`converge` lines). "
    "For a container, `docker inspect --format '{{.State.Health.Status}}'` (any narrow field). "
    "To check a secret is set, count it (`grep -c '^KEY=.' .env`), never show it. To print it "
    "on purpose, add `# secrets-ok: <reason>` to the command."
)


def _reason(what: str) -> str:
    return (
        f"This prints resolved secrets into the transcript: {what} On 2026-11-07 (SRV.NTFY) a "
        "`docker compose config | grep | sed <redact>` sent over ssh echoed both ntfy password "
        "hashes -- compose re-escapes `$` as `$$` on output, so the redaction never matched -- "
        f"and both passwords and the publish token had to be rotated. {WAY_OUT}"
    )


COMPOSE_REASON = _reason(
    "`docker compose config` interpolates `.env` and inlines every `env_file:` into what it "
    "prints (`--environment` prints `.env` itself)."
)
INSPECT_REASON = _reason(
    "`docker inspect` with no narrow `--format` prints the container's whole config, "
    "`.Config.Env` -- every secret compose resolved into it -- included."
)


def _end_of_command(text: str, start: int) -> int:
    """Where the simple command that starts at `start` ends: the first unquoted terminator."""
    quote = ""
    index = start
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\" and quote == '"':
                index += 1
            elif char == quote:
                quote = ""
        elif char == "\\":
            index += 1
        elif char in "'\"":
            quote = char
        elif char in _TERMINATORS or (char == "#" and text[index - 1 : index] in ("", " ", "\t")):
            break
        index += 1
    return index


def _skip_options(argv: list[str], valued: set[str]) -> list[str]:
    """`argv` from its first word that is not an option (or an option's value)."""
    index = 0
    while index < len(argv):
        tok = argv[index]
        if not tok.startswith("-") or tok == "-":
            return argv[index:]
        index += 2 if tok in valued else 1
    return []


def _option_value(argv: list[str], names: tuple[str, ...]) -> str | None:
    for index, tok in enumerate(argv):
        for name in names:
            if tok == name and index + 1 < len(argv):
                return argv[index + 1]
            if tok.startswith(name + "=") and name.startswith("--"):
                return tok[len(name) + 1 :]
            if not name.startswith("--") and tok.startswith(name) and len(tok) > len(name):
                return tok[len(name) :]
    return None


def judge_argv(prog: str, argv: list[str]) -> str | None:
    """The refusal for one docker/docker-compose invocation (`argv` after the program), or None."""
    if prog == "docker":
        rest = _skip_options(argv, _DOCKER_VALUED)
        if rest[:1] == ["compose"]:
            rest = rest[1:]
        elif rest[:2] == ["container", "inspect"] or rest[:1] == ["inspect"]:
            args = rest[2:] if rest[0] == "container" else rest[1:]
            fmt = _option_value(args, ("--format", "-f"))
            if fmt is None or _WIDE_FORMAT.search(fmt):
                return INSPECT_REASON
            return None
        else:
            return None
    else:
        rest = argv
    rest = _skip_options(rest, _COMPOSE_VALUED)
    if rest[:1] != ["config"]:
        return None
    flags = {tok.split("=", 1)[0] for tok in rest[1:]}
    if "--environment" in flags or not flags & SAFE_CONFIG:
        return COMPOSE_REASON
    return None


def _owner(masked: str, anchor: int) -> str:
    """The program of the simple command that holds `anchor`, past assignments and wrappers."""
    start = anchor
    while start > 0 and masked[start - 1] not in "|;&\n(":
        start -= 1
    words = masked[start:anchor].split()
    for word in words:
        name = os.path.basename(word)
        if "=" in word and not word.startswith("-") or word.startswith("-"):
            continue
        if name in _WRAPPERS or re.fullmatch(r"[\d.]+[smhd]?", word):
            continue
        return name
    return ""


def _spans(text: str) -> list[tuple[int, int, int, str]]:
    """(anchor, inner start, inner end, kind) of each quoted span and heredoc body in `text`.

    The anchor is where the span's simple command is judged from: the quote itself, or the `<<`
    of a heredoc (its body sits on later lines, after the command that owns it).
    """
    found: list[tuple[int, int, int, str]] = []
    index = 0
    length = len(text)
    pending: list[tuple[int, str]] = []  # heredocs opened on this line: (anchor, delimiter)
    while index < length:
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "'":
            end = text.find("'", index + 1)
            end = length if end < 0 else end
            found.append((index, index + 1, end, "'"))
            index = end + 1
            continue
        if char == '"':
            end = busyloop._end_of_double(text, index)
            found.append((index, index + 1, max(index + 1, end - 1), '"'))
            index = end
            continue
        if char == "#" and (index == 0 or text[index - 1] in " \t\n;&|(<>"):
            end = text.find("\n", index)
            index = length if end < 0 else end
            continue
        if text.startswith("<<", index) and not text.startswith("<<<", index):
            match = re.match(r"<<-?\s*(['\"]?)([^\s;&|<>()'\"]+)\1", text[index:])
            if match:
                pending.append((index, match.group(2)))
                index += match.end()
                continue
        if char == "\n" and pending:
            body = index + 1
            for anchor, delimiter in pending:
                stop = body
                while stop < length:
                    line_end = text.find("\n", stop)
                    line_end = length if line_end < 0 else line_end
                    if text[stop:line_end].strip() == delimiter:
                        found.append((anchor, body, stop, "<<"))
                        body = min(length, line_end + 1)
                        break
                    stop = line_end + 1
                else:
                    found.append((anchor, body, length, "<<"))
                    body = length
            pending = []
            index = body
            continue
        index += 1
    return found


def _code_texts(text: str, depth: int = 0) -> list[str]:
    """`text` and every text it hands to another shell to run, recursively (bounded)."""
    texts = [text]
    if depth >= 4:
        return texts
    masked = busyloop.mask(text)
    for anchor, start, end, kind in _spans(text):
        inner = text[start:end]
        substitutes = kind == '"' and ("$(" in inner or "`" in inner)
        if substitutes or _owner(masked, anchor) in _HANDS_CODE_ON:
            texts += _code_texts(inner, depth + 1)
    return texts


def judge(command: str) -> str | None:
    """The refusal for a command that prints resolved secrets, or None when it runs."""
    if ESCAPE.search(command):
        return None
    for text in _code_texts(command):
        masked = busyloop.mask(text)
        for match in _DOCKER.finditer(masked):
            stop = _end_of_command(text, match.end())
            try:
                argv = shlex.split(text[match.end() : stop], posix=True)
            except ValueError:
                continue
            reason = judge_argv(match.group("prog"), argv)
            if reason:
                return reason
    return None
