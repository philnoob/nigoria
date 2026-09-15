"""String-literal encryption ("static environment").

Every value-position string literal is moved into an encoded byte pool and the
literal is replaced by a call to a runtime decoder. The cipher is a reversible
position-dependent additive transform (no bitwise ops), so it decodes correctly
on Lua 5.1 / Luau / 5.4. This is obfuscation, not cryptography: the goal is to
keep readable text out of the emitted source.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import ast_nodes as A
from ..luastr import compact_lua_bytes as _compact_lua_bytes
from ..walker import NodeTransformer


@dataclass
class StringNames:
    decoder: str
    pool: str
    idx: str
    cache: str
    sb: str   # local alias for string.byte
    sc: str   # local alias for string.char


class StringCollector(NodeTransformer):
    def __init__(self, decoder_name: str):
        self.decoder_name = decoder_name
        self.pool: list[str] = []
        self.index: dict[str, int] = {}

    def _intern(self, value: str) -> int:
        if value in self.index:
            return self.index[value]
        self.pool.append(value)
        idx = len(self.pool)  # 1-based
        self.index[value] = idx
        return idx

    def visit_String(self, node: A.String, **ctx) -> A.Node:
        idx = self._intern(node.value)
        return A.Call(A.Name(self.decoder_name), [A.Number(str(idx))])


def _key(i: int, j: int) -> int:
    return (i * 7 + j * 3 + 0x5A) % 256


def _lua_byte_literal(data: bytes) -> str:
    return _compact_lua_bytes(data)


def encrypt_strings(block: A.Block, names: StringNames):
    """Return (transformed_block, preamble_lua)."""
    collector = StringCollector(names.decoder)
    block = collector.visit(block)
    if not collector.pool:
        return block, ""

    concatenated = bytearray()
    offsets: list[tuple[int, int]] = []
    for i, s in enumerate(collector.pool, start=1):
        raw = s.encode("utf-8", "surrogatepass")
        off = len(concatenated) + 1
        for j, b in enumerate(raw, start=1):
            concatenated.append((b + _key(i, j)) % 256)
        offsets.append((off, len(raw)))

    pool_lit = _lua_byte_literal(bytes(concatenated))
    idx_parts: list[str] = []
    for off, length in offsets:
        idx_parts.append(str(off))
        idx_parts.append(str(length))
    idx_lit = "{" + ",".join(idx_parts) + "}"

    preamble = (
        f"local {names.pool}={pool_lit};"
        f"local {names.idx}={idx_lit};"
        f"local {names.cache}={{}};"
        f"local {names.sb}=string.byte;"
        f"local {names.sc}=string.char;"
        f"local function {names.decoder}(i)"
        f"local c={names.cache}[i];if c then return c;end;"
        f"local o={names.idx}[i*2-1];local n={names.idx}[i*2];"
        f"local b={{}};"
        f"for j=1,n do "
        f"local v={names.sb}({names.pool},o+j-1);"
        f"b[j]={names.sc}((v-((i*7+j*3+90)%256))%256);"
        f"end;"
        f"c=table.concat(b);{names.cache}[i]=c;return c;"
        f"end;"
    )
    return block, preamble
