#!/usr/bin/env python3
"""tap_rewrite.py -- move a Homebrew formula's or cask's version, urls and sha256s TOGETHER
(BACKLOG 6.15; called by tap_bump.sh, which owns the clone, the tap's gate and the push).

tap_bump.sh used to do this with two `sed` expressions: one rewrote a `/tags/vX.tar.gz` url, the
other EVERY `sha256 "..."` line. Measured 2026-10-14 on a scratch copy of homebrew-tap, that wrote
bumps pointing at the wrong artifact, and the tap's gate passed them:

  * a cask (`version "0.41.1"`, `url ".../v#{version}/..."`) got the new digest and kept the old
    version, so brew would fetch the OLD dmg and refuse it on the NEW digest;
  * multitop.rb's two agent `resource`s got the source tarball's digest and kept their v0.54.0
    urls.

So the file is read as the DSL it is, by block: the top-level `version`, `url` and `sha256`, and
each `resource "NAME" do ... end` with its own `url` and `sha256`. The old version is the cask's
`version`, or the tag in the formula's url; every url moves from it to the new one (a url that
interpolates `#{version}` moves with the version line); a resource's digest is the digest of ITS
artifact -- given by `--resource NAME=PATH|URL`, else downloaded from its NEW url. Any shape this
does not know (a url or sha256 inside an `on_arm` block, a url carrying no version, a resource
with no sha256) is refused by name, never guessed. The result is read back and checked before
it is written.

    tap_rewrite.py FILE --tag vX.Y.Z --sha SHA256 [--resource NAME=PATH|URL ...]
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tempfile
import urllib.request

STRING = r'"([^"\\]*(?:\\.[^"\\]*)*)"'
OPEN = re.compile(r"\bdo(\s*\|[^|]*\|)?\s*$")
RESOURCE = re.compile(r"^(\s*)resource\s+" + STRING + r"\s+do\s*$")
FIELD = re.compile(r"^(\s*)(version|url|sha256)\s+" + STRING + r"(.*)$", re.DOTALL)
END = re.compile(r"^(\s*)end\b")
TAGGED = re.compile(r"/(?:tags|download)/v?(\d+(?:\.\d+)+)(?=[/.])")


class Shape(Exception):
    """A shape this rewriter does not know. Refused by name, never guessed."""


HEREDOC = re.compile(r"<<[~-]?([A-Z_][A-Z0-9_]*)")
KEYWORD = re.compile(r"^(?:if|unless|case|while|until|begin|for)\b")


def code_of(line: str) -> str:
    """The line without its comment: a `#` outside a string (`#{...}` inside one is not)."""
    quote = None
    i = 0
    while i < len(line):
        c = line[i]
        if quote:
            if c == "\\":
                i += 1
            elif c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "#":
            return line[:i]
        i += 1
    return line


def scan(lines: list[str]) -> dict:
    """{"top": {field: index}, "resources": {name: {field: index}}} by line index. A url or
    sha256 in any other block (`on_arm`, `livecheck`, a method body) is refused by name."""
    top: dict[str, int] = {}
    resources: dict[str, dict[str, int]] = {}
    stack: list[str] = []  # block names, outermost first; a resource is "resource NAME"
    heredoc = None
    for i, line in enumerate(lines):
        if heredoc is not None:
            if line.strip() == heredoc:
                heredoc = None
            continue
        code = code_of(line)
        stripped = code.strip()
        doc = HEREDOC.search(code)
        if doc:
            heredoc = doc.group(1)
        res = RESOURCE.match(code)
        field = FIELD.match(code)
        if res and stack in (["class"], ["cask"]):
            stack.append(f"resource {res.group(2)}")
            resources[res.group(2)] = {}
        elif field:
            name = field.group(2)
            if stack in (["class"], ["cask"]):
                if name in top:
                    raise Shape(f"line {i + 1}: a second top-level `{name}`")
                top[name] = i
            elif len(stack) == 2 and stack[1].startswith("resource "):
                resources[stack[1].removeprefix("resource ")][name] = i
            elif name != "version":
                where = " > ".join(stack) or "the file"
                raise Shape(f"line {i + 1}: `{name}` inside {where}, a block this does not rewrite")
        elif re.match(r"^class\s+\w+\s*<\s*Formula\b", stripped):
            stack.append("class")
        elif re.match(r"^cask\s+" + STRING + r"\s+do\b", stripped):
            stack.append("cask")
        elif re.match(r"^def\s", stripped) and not re.search(r"\bend\s*$", stripped):
            stack.append("def")
        elif KEYWORD.match(stripped) or OPEN.search(code):
            stack.append(stripped.split()[0].split("(")[0])
        elif END.match(code):
            if not stack:
                raise Shape(f"line {i + 1}: an `end` with no block open")
            stack.pop()
    if stack or heredoc is not None:
        raise Shape(f"unbalanced blocks at end of file: {stack or heredoc}")
    return {"top": top, "resources": resources}


def value(line: str) -> str:
    m = FIELD.match(line)
    assert m
    return m.group(3)


def replace(line: str, new: str) -> str:
    m = FIELD.match(line)
    assert m
    return f'{m.group(1)}{m.group(2)} "{new}"{m.group(4)}'


def digest_of(src: str, label: str) -> str:
    """sha256 of a local file or of what a URL serves; a failed fetch is refused, never hashed."""
    if re.match(r"^[a-z]+://", src):
        try:
            with urllib.request.urlopen(src, timeout=120) as r, tempfile.TemporaryFile() as f:
                h = hashlib.sha256()
                while chunk := r.read(1 << 20):
                    h.update(chunk)
                    f.write(chunk)
                return h.hexdigest()
        except (OSError, ValueError) as e:
            raise Shape(f"{label}: could not download {src}: {e}") from e
    try:
        with open(src, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError as e:
        raise Shape(f"{label}: artifact not readable: {src}: {e}") from e


def moved(url: str, old: str, new: str, label: str) -> str:
    if "#{version}" in url:
        return url
    if old not in url:
        raise Shape(f"{label}: its url carries no version string ({old}) to move: {url}")
    return url.replace(old, new)


def rewrite(text: str, tag: str, sha: str, given: dict[str, str]) -> str:
    lines = text.split("\n")
    shape = scan(lines)
    top = shape["top"]
    if "url" not in top or "sha256" not in top:
        raise Shape("no top-level `url` and `sha256` pair")
    new = tag.removeprefix("v")
    if "version" in top:
        old = value(lines[top["version"]])
    else:
        found = TAGGED.search(value(lines[top["url"]]))
        if not found:
            raise Shape(f"no version line, and no tag in the url: {value(lines[top['url']])}")
        old = found.group(1)
    unknown = set(given) - set(shape["resources"])
    if unknown:
        raise Shape(f"--resource names no resource in the file: {sorted(unknown)}")
    if "version" in top:
        lines[top["version"]] = replace(lines[top["version"]], new)
    lines[top["url"]] = replace(lines[top["url"]], moved(value(lines[top["url"]]), old, new, "url"))
    lines[top["sha256"]] = replace(lines[top["sha256"]], sha)
    want = {}
    for name, fields in shape["resources"].items():
        if "url" not in fields or "sha256" not in fields:
            raise Shape(f"resource {name!r}: no url and sha256 pair")
        url = moved(value(lines[fields["url"]]), old, new, f"resource {name!r}")
        lines[fields["url"]] = replace(lines[fields["url"]], url)
        want[name] = digest_of(given.get(name, url), f"resource {name!r}")
        lines[fields["sha256"]] = replace(lines[fields["sha256"]], want[name])
    out = "\n".join(lines)
    check(out, old, new, sha, want)
    return out


def check(text: str, old: str, new: str, sha: str, want: dict[str, str]) -> None:
    """Read the result back: version, every url and every digest moved together."""
    lines = text.split("\n")
    shape = scan(lines)
    top = shape["top"]
    version = value(lines[top["version"]]) if "version" in top else new

    def url_of(i: int) -> str:
        return value(lines[i]).replace("#{version}", version)

    if version != new:
        raise Shape(f"read back: version is {version}, not {new}")
    if value(lines[top["sha256"]]) != sha:
        raise Shape("read back: the top-level sha256 is not the artifact's")
    urls = [("url", url_of(top["url"]))]
    for name, fields in shape["resources"].items():
        urls.append((f"resource {name!r}", url_of(fields["url"])))
        if value(lines[fields["sha256"]]) != want[name]:
            raise Shape(f"read back: resource {name!r} does not carry its own artifact's digest")
    for label, url in urls:
        if new not in url or (old != new and old in url):
            raise Shape(f"read back: {label} url still names {old}: {url}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("file")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--sha", required=True)
    ap.add_argument("--resource", action="append", default=[], metavar="NAME=PATH|URL")
    args = ap.parse_args(argv)
    given = {}
    for item in args.resource:
        name, sep, src = item.partition("=")
        if not sep or not name or not src:
            print(f"tap_rewrite: --resource wants NAME=PATH|URL, got {item!r}", file=sys.stderr)
            return 2
        given[name] = src
    with open(args.file, encoding="utf-8") as f:
        text = f.read()
    try:
        out = rewrite(text, args.tag, args.sha, given)
    except Shape as e:
        print(f"tap_rewrite: {args.file}: {e}", file=sys.stderr)
        return 1
    with open(args.file, "w", encoding="utf-8") as f:
        f.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
