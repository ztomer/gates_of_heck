"""The cases of the shared-gate canary whose gate RUNS a program the repo names (a generator, an
MCP server), split out of `_canary_cases.py` at its 500-line cap.

The MCP wire case is one planted stdio server, spoken to live by `check_mcp_wire.py`: newest protocol by discovery, one
tool, and its annotations are the variable -- none (red), both hints stated (green). The version is
the checker's own, never a second spelling of it.
"""

from check_mcp_wire import NEWEST_PROTOCOL

_MCP_SRV = (
    "import json, sys\n"
    f"V = {NEWEST_PROTOCOL!r}\n"
    "T = {'name': 't', 'inputSchema': {'type': 'object'}, 'outputSchema': {'type': 'object'},"
    " 'annotations': %s}\n"
    "for line in sys.stdin:\n"
    "    m = json.loads(line)\n"
    "    if 'id' not in m: continue\n"
    "    r = {'supportedVersions': [V]} if m['method'] == 'server/discover' else {'tools': [T]}\n"
    "    print(json.dumps({'jsonrpc': '2.0', 'id': m['id'], 'result': r}), flush=True)\n"
)


# (label, script, argv, {path: violating content}, {path: clean content})
CASES = [
    # The generator writes into a sandbox dir handed to it as $1 (the out-dir contract).
    (
        "generated artifacts are fresh",
        "check_generated_fresh.py",
        ["--generator", "sh gen.sh", "out.txt"],
        {"gen.sh": 'mkdir -p "$1"\necho generated > "$1/out.txt"\n', "out.txt": "stale\n"},
        {"gen.sh": 'mkdir -p "$1"\necho generated > "$1/out.txt"\n', "out.txt": "generated\n"},
    ),
    # A planted stdio MCP server, spoken to live: one tool, whose annotations are the variable.
    (
        "an MCP server states every tool's effect",
        "check_mcp_wire.py",
        [],
        {
            ".gatesrc": "GOH_MCP_SERVERS='python3 srv.py'\nGOH_MCP_TIMEOUT=30\n",
            "srv.py": _MCP_SRV % "{}",
        },
        {
            ".gatesrc": "GOH_MCP_SERVERS='python3 srv.py'\nGOH_MCP_TIMEOUT=30\n",
            "srv.py": _MCP_SRV % '{"readOnlyHint": True, "destructiveHint": False}',
        },
    ),
]
