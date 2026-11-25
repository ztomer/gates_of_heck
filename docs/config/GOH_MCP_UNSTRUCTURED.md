# GOH_MCP_UNSTRUCTURED
Area: local-ci
Kind: setting
Order: 40
Default: unset (every reply is structured)

One `tool: reason` per line: a tool whose reply genuinely is not structured (prose, an image), so it
needs no `outputSchema`. Why a list and not an observation: a reply's structure is seen only by
CALLING the tool, and the gate never calls one (a call can act on the world, and readOnlyHint is the
hint being checked). A reason is required (exit 2 without). A RATCHET: an entry no server lists, or
whose tool now declares an `outputSchema`, is red.
