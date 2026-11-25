# GOH_MCP_SERVERS
Area: local-ci
Kind: setting
Order: 30
Default: unset (no MCP gate)

**The opt-in of the MCP wire gate** (`checks/check_mcp_wire.py`, queue MCP.G): one stdio start
command per line (`#` lines ignored), each run under `/bin/sh -c` in the repo root, in its own
session, with `GOH_MCP_SCRATCH` naming a fresh empty directory -- point the server's home or data at
it, so the gate never reads the owner's. Setting it is the whole wiring: `structural.sh --full` runs
the gate (never `--staged`: a commit starts no server). The gate speaks the newest protocol
(`server/discover`, falling back to `initialize`), lists every tool and never calls one; red when
the negotiated version is not the newest goh knows, a tool's `annotations` do not state readOnlyHint
and destructiveHint as booleans, a destructive tool is listed by default, or a tool has no
`outputSchema` (type `object`) and is not named in `GOH_MCP_UNSTRUCTURED`. Name only a server that
starts read-only with no owner data, accounts or devices.
