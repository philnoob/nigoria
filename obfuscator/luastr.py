"""Compact Lua byte-string literal emission.

Bytes that are safe printable ASCII are emitted verbatim; everything else uses
an unambiguous 3-digit ``\\ddd`` escape. This keeps embedded payloads a few
times smaller than escaping every byte.
"""

from __future__ import annotations

_SAFE = set(range(32, 127)) - {ord('"'), ord("\\")}


def compact_lua_bytes(data: bytes) -> str:
    out = ['"']
    for b in data:
        if b in _SAFE:
            out.append(chr(b))
        else:
            out.append("\\%03d" % b)
    out.append('"')
    return "".join(out)
