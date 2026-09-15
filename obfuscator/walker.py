"""A generic AST transformer.

Subclasses override ``visit_<NodeType>`` to return a replacement node (or the
same node). The default behaviour recurses into children and rebuilds them in
place. Context flags let a transform know when it is inside a position where a
replacement would be unsafe (e.g. a dotted field key).
"""

from __future__ import annotations

from . import ast_nodes as A


class NodeTransformer:
    def visit(self, node, **ctx):
        if node is None:
            return None
        method = getattr(self, "visit_" + type(node).__name__, None)
        if method is not None:
            return method(node, **ctx)
        return self.generic_visit(node, **ctx)

    # --- generic recursion -------------------------------------------------
    def generic_visit(self, node, **ctx):
        t = type(node)
        if t is A.Block:
            node.stmts = [self.visit(s) for s in node.stmts]
            return node
        if t is A.LocalAssign:
            node.exprs = [self.visit(e) for e in node.exprs]
            return node
        if t is A.Assign:
            node.targets = [self.visit(x, target=True) for x in node.targets]
            node.exprs = [self.visit(e) for e in node.exprs]
            return node
        if t is A.CallStat:
            node.call = self.visit(node.call)
            return node
        if t is A.Do:
            node.body = self.visit(node.body)
            return node
        if t is A.While:
            node.cond = self.visit(node.cond)
            node.body = self.visit(node.body)
            return node
        if t is A.Repeat:
            node.body = self.visit(node.body)
            node.cond = self.visit(node.cond)
            return node
        if t is A.If:
            node.clauses = [
                (None if c is None else self.visit(c), self.visit(b))
                for c, b in node.clauses
            ]
            return node
        if t is A.NumericFor:
            node.start = self.visit(node.start)
            node.stop = self.visit(node.stop)
            node.step = self.visit(node.step) if node.step is not None else None
            node.body = self.visit(node.body)
            return node
        if t is A.GenericFor:
            node.exprs = [self.visit(e) for e in node.exprs]
            node.body = self.visit(node.body)
            return node
        if t is A.FunctionDecl:
            # The target is a name path; do not rewrite string keys there.
            node.func = self.visit(node.func)
            return node
        if t is A.Return:
            node.exprs = [self.visit(e) for e in node.exprs]
            return node
        if t is A.FunctionExpr:
            node.body = self.visit(node.body)
            return node
        if t is A.Index:
            node.obj = self.visit(node.obj)
            if not node.dot:
                node.key = self.visit(node.key)
            return node
        if t is A.Call:
            node.func = self.visit(node.func)
            node.args = [self.visit(a) for a in node.args]
            return node
        if t is A.MethodCall:
            node.obj = self.visit(node.obj)
            node.args = [self.visit(a) for a in node.args]
            return node
        if t is A.BinOp:
            node.left = self.visit(node.left)
            node.right = self.visit(node.right)
            return node
        if t is A.UnOp:
            node.operand = self.visit(node.operand)
            return node
        if t is A.Paren:
            node.expr = self.visit(node.expr)
            return node
        if t is A.Table:
            for f in node.fields:
                if f.key is not None and f.bracketed:
                    f.key = self.visit(f.key)
                f.value = self.visit(f.value)
            return node
        # Leaf nodes: Nil, Bool, Number, String, Name, Vararg, Raw, etc.
        return node
