"""Light optimizations: constant folding and trivial dead-code removal.

Kept conservative so behaviour never changes. Folding reduces literals the
number/string passes would otherwise have to process and shrinks output.
"""

from __future__ import annotations

from .. import ast_nodes as A
from ..walker import NodeTransformer


class Optimizer(NodeTransformer):
    def visit_BinOp(self, node: A.BinOp, **ctx) -> A.Node:
        node.left = self.visit(node.left)
        node.right = self.visit(node.right)
        l, r = node.left, node.right
        # Numeric constant folding (integers only, exact).
        li, ri = _int(l), _int(r)
        if li is not None and ri is not None:
            try:
                if node.op == "+":
                    return _num(li + ri)
                if node.op == "-":
                    return _num(li - ri)
                if node.op == "*":
                    return _num(li * ri)
            except (OverflowError, ValueError):
                pass
        # String concatenation folding.
        if node.op == ".." and isinstance(l, A.String) and isinstance(r, A.String):
            return A.String(l.value + r.value, l.style)
        return node

    def visit_Block(self, node: A.Block, **ctx) -> A.Block:
        node.stmts = [self.visit(s) for s in node.stmts]
        # Drop statements after an unconditional return in the same block.
        cleaned = []
        for s in node.stmts:
            cleaned.append(s)
            if isinstance(s, A.Return):
                break
        node.stmts = cleaned
        return node


def _int(node) -> int | None:
    if isinstance(node, A.Number):
        s = node.raw.strip().replace("_", "")
        try:
            if s.lower().startswith("0x"):
                return int(s, 16)
            if s.isdigit():
                return int(s)
        except ValueError:
            return None
    if isinstance(node, A.UnOp) and node.op == "-":
        v = _int(node.operand)
        return -v if v is not None else None
    return None


def _num(val: int) -> A.Node:
    if val < 0:
        return A.UnOp("-", A.Number(str(-val)))
    return A.Number(str(val))


def optimize(block: A.Block) -> A.Block:
    return Optimizer().visit(block)
