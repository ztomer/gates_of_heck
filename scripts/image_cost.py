"""image_cost.py -- what an image tool result costs, in tokens from its pixels (GOH.IMGREAD).

Anthropic's vision docs (platform.claude.com/docs/en/build-with-claude/vision, "Calculate image
costs") price an image at width * height / 750 tokens. The size is read from the PNG/JPEG/WebP
header of the base64 copy a transcript keeps, the one Claude Code sent after its own resize
(code.claude.com/docs/en/tools-reference.md, Read, "Images"). Never its bytes: a screenshot's
base64 runs to ~250 KB yet costs ~2k tokens, so a byte share overstates images ~10x (27% of the
tool-result bytes of 2026-11-16..23 were images, 1.6% of their tokens). An image whose size
cannot be read is None, never a guess. Pinned by tests/test_spend_watch.py.
"""

from __future__ import annotations

import base64
import struct
from typing import Any


def image_tokens(block: dict[str, Any]) -> int | None:
    """An image block's tokens (width * height / 750, rounded up); None if its size is unknown."""
    data = str((block.get("source") or {}).get("data") or "")
    try:
        raw = base64.b64decode(data[: 1 << 16] + "=" * (-min(len(data), 1 << 16) % 4))
    except ValueError:
        return None
    size: tuple[int, int] | None = None
    if raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) >= 24:
        size = struct.unpack(">II", raw[16:24])
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP" and raw[12:16] == b"VP8X" and len(raw) >= 30:
        size = (int.from_bytes(raw[24:27], "little") + 1, int.from_bytes(raw[27:30], "little") + 1)
    elif raw[:2] == b"\xff\xd8":
        i = 2
        while i + 9 <= len(raw) and raw[i] == 0xFF:
            if raw[i + 1] in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", raw[i + 5 : i + 9])
                size = (w, h)
                break
            i += 2 + struct.unpack(">H", raw[i + 2 : i + 4])[0]
    return -(-size[0] * size[1] // 750) if size else None
