"""A planted MCP server for tests/test_check_mcp_wire.py: it says on the wire what its spec says.

    python3 server.py <spec.json>

Spec keys (all optional): `mode` "modern" (answers server/discover with `versions`) or "legacy"
(refuses server/discover, answers initialize with `version`); `tools`, split over `pages` pages;
`hang` (read everything, answer nothing); `ask` (send the client a request before listing, and
list only once it is answered); `child` (start a long `sleep` in this process group) and
`detached` (start one in a session of its own), each writing its pid to the file named.
"""

import json
import os
import sys

with open(sys.argv[1], encoding="utf-8") as _f:
    spec = json.load(_f)
tools = spec.get("tools", [])
pages = max(1, int(spec.get("pages", 1)))
size = max(1, -(-len(tools) // pages))


def say(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def sleeper(pidfile, detached):
    """The leak the gate must reap: a bounded `sleep 60` this server never waits on, because the
    gate's stop is what must end it. Forked rather than spawned so it can leave the group
    (`detached`); the TEST owns it and kills it whatever the verdict (`_reap_after`)."""
    pid = os.fork()
    if pid == 0:
        if detached:
            os.setsid()
        os.execv("/bin/sleep", ["sleep", "60"])
    with open(pidfile, "w", encoding="utf-8") as f:
        f.write(str(pid))


for key, detached in (("child", False), ("detached", True)):
    if spec.get(key):
        sleeper(spec[key], detached)

pending_list = None
for raw in sys.stdin:
    if spec.get("hang"):
        continue
    msg = json.loads(raw)
    method, mid = msg.get("method"), msg.get("id")
    if method is None and mid == "ask-1" and pending_list is not None:
        say(pending_list)
        pending_list = None
        continue
    if mid is None:
        continue
    if method == "server/discover":
        if spec.get("mode", "modern") == "modern":
            say({"jsonrpc": "2.0", "id": mid, "result": {"supportedVersions": spec["versions"]}})
        else:
            say({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "no"}})
    elif method == "initialize":
        say({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": spec["version"]}})
    elif method == "tools/list":
        page = int((msg.get("params") or {}).get("cursor") or 0)
        result = {"tools": tools[page * size : (page + 1) * size]}
        if page + 1 < pages:
            result["nextCursor"] = str(page + 1)
        reply = {"jsonrpc": "2.0", "id": mid, "result": result}
        if spec.get("ask") and page == 0:
            pending_list = reply
            say({"jsonrpc": "2.0", "id": "ask-1", "method": "roots/list", "params": {}})
        else:
            say(reply)
    else:
        say({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": method}})
os._exit(0)
