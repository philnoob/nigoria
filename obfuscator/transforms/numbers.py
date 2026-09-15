"""Numeric literal obfuscation.

Integer literals are rewritten as arithmetic expressions that evaluate to the
same value using only +, - and * so the result is identical on Lua 5.1, Luau
and Lua 5.4 (no reliance on bitwise ops or integer/float distinctions).
"""

from __future__ import annotations

from .. import ast_nodes as A
from ..walker import NodeTransformer


class NumberObfuscator(NodeTransformer):
    def __init__(self, rng, intensity: int = 1):
        self.rng = rng
        self.intensity = max(1, intensity)

    def visit_Number(self, node: A.Number, **ctx) -> A.Node:
        val = self._parse_int(node.raw)
        if val is None:
            return node  # leave floats / hex-floats untouched
        return self._encode_int(val, depth=self.intensity)

    @staticmethod
    def _parse_int(raw: str) -> int | None:
        s = raw.strip().replace("_", "")
        try:
            if s.lower().startswith("0x") and "." not in s and "p" not in s.lower():
                return int(s, 16)
            if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
                return int(s)
        except ValueError:
            return None
        return None

    def _encode_int(self, val: int, depth: int) -> A.Node:
        if depth <= 0 or -3 < val < 3:
            return self._literal(val)
        choice = self.rng.randint(0, 2)
        if choice == 0:  # val = a + b
            a = self.rng.randint(-(abs(val) + 50), abs(val) + 50)
            b = val - a
            return A.BinOp("+", self._encode_int(a, depth - 1),
                           self._encode_int(b, depth - 1))
        if choice == 1:  # val = a - b
            b = self.rng.randint(-(abs(val) + 50), abs(val) + 50)
            a = val + b
            return A.BinOp("-", self._encode_int(a, depth - 1),
                           self._encode_int(b, depth - 1))
        # val = a * f + r  (keep factors small)
        f = self.rng.choice([2, 3, 4, 5])
        a = val // f
        r = val - a * f
        prod = A.BinOp("*", self._encode_int(a, depth - 1), self._literal(f))
        if r == 0:
            return prod
        return A.BinOp("+", prod, self._literal(r))

    @staticmethod
    def _literal(val: int) -> A.Node:
        if val < 0:
            return A.UnOp("-", A.Number(str(-val)))
        return A.Number(str(val))


def obfuscate_numbers(block: A.Block, rng, intensity: int = 1) -> A.Block:
    return NumberObfuscator(rng, intensity).visit(block)
