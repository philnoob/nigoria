"""Scope-aware renaming of local variables to opaque identifiers.

Globals (things not bound by a local/param/loop variable) are left untouched so
that references to Roblox APIs (``game``, ``print``, ``workspace`` ...) keep
working. New names are globally unique to avoid any capture.
"""

from __future__ import annotations

from .. import ast_nodes as A
from ..namegen import NameGenerator


class Scope:
    def __init__(self, parent: "Scope | None"):
        self.parent = parent
        self.map: dict[str, str] = {}

    def declare(self, name: str, new: str) -> None:
        self.map[name] = new

    def resolve(self, name: str) -> str | None:
        s: Scope | None = self
        while s is not None:
            if name in s.map:
                return s.map[name]
            s = s.parent
        return None


class Renamer:
    def __init__(self, namegen: NameGenerator, rename: bool = True):
        self.ng = namegen
        self.rename = rename

    def _fresh(self, original: str) -> str:
        # When renaming is disabled we keep the original name but still
        # record the binding so is_local resolution works for later passes.
        return self.ng.new() if self.rename else original

    def run(self, block: A.Block) -> A.Block:
        self._block(block, Scope(None))
        return block

    # --- blocks ------------------------------------------------------------
    def _block(self, block: A.Block, parent: Scope) -> None:
        scope = Scope(parent)
        for stmt in block.stmts:
            self._stmt(stmt, scope)

    def _stmt(self, node: A.Node, scope: Scope) -> None:
        t = type(node).__name__
        getattr(self, "_stmt_" + t, self._stmt_default)(node, scope)

    def _stmt_default(self, node: A.Node, scope: Scope) -> None:
        # Statements that only contain expressions (CallStat, etc.)
        for attr in ("call", "cond", "expr"):
            if hasattr(node, attr):
                self._expr(getattr(node, attr), scope)

    def _stmt_LocalAssign(self, n: A.LocalAssign, scope: Scope) -> None:
        # RHS is evaluated in the *outer* scope (before new bindings exist).
        for e in n.exprs:
            self._expr(e, scope)
        new_names = []
        for name in n.names:
            new = self._fresh(name)
            scope.declare(name, new)
            new_names.append(new)
        n.names = new_names

    def _stmt_Assign(self, n: A.Assign, scope: Scope) -> None:
        for e in n.exprs:
            self._expr(e, scope)
        for t in n.targets:
            self._expr(t, scope)

    def _stmt_CallStat(self, n: A.CallStat, scope: Scope) -> None:
        self._expr(n.call, scope)

    def _stmt_Do(self, n: A.Do, scope: Scope) -> None:
        self._block(n.body, scope)

    def _stmt_While(self, n: A.While, scope: Scope) -> None:
        self._expr(n.cond, scope)
        self._block(n.body, scope)

    def _stmt_Repeat(self, n: A.Repeat, scope: Scope) -> None:
        # In `repeat ... until c`, the condition can see locals from the body.
        inner = Scope(scope)
        for stmt in n.body.stmts:
            self._stmt(stmt, inner)
        self._expr(n.cond, inner)

    def _stmt_If(self, n: A.If, scope: Scope) -> None:
        new_clauses = []
        for cond, body in n.clauses:
            if cond is not None:
                self._expr(cond, scope)
            self._block(body, scope)
            new_clauses.append((cond, body))
        n.clauses = new_clauses

    def _stmt_NumericFor(self, n: A.NumericFor, scope: Scope) -> None:
        self._expr(n.start, scope)
        self._expr(n.stop, scope)
        if n.step is not None:
            self._expr(n.step, scope)
        inner = Scope(scope)
        new = self._fresh(n.var)
        inner.declare(n.var, new)
        n.var = new
        for stmt in n.body.stmts:
            self._stmt(stmt, inner)

    def _stmt_GenericFor(self, n: A.GenericFor, scope: Scope) -> None:
        for e in n.exprs:
            self._expr(e, scope)
        inner = Scope(scope)
        new_names = []
        for name in n.names:
            new = self._fresh(name)
            inner.declare(name, new)
            new_names.append(new)
        n.names = new_names
        for stmt in n.body.stmts:
            self._stmt(stmt, inner)

    def _stmt_FunctionDecl(self, n: A.FunctionDecl, scope: Scope) -> None:
        if n.is_local:
            # Name is visible inside its own body (recursion).
            new = self._fresh(n.local_name)
            scope.declare(n.local_name, new)
            n.local_name = new
            self._funcexpr(n.func, scope)
        else:
            # Global/method function: the target path may reference a local
            # (e.g. `function localTbl.foo()`); rename inside the target.
            self._expr(n.target, scope)
            self._funcexpr(n.func, scope)

    def _stmt_Return(self, n: A.Return, scope: Scope) -> None:
        for e in n.exprs:
            self._expr(e, scope)

    def _stmt_Break(self, n, scope): pass
    def _stmt_Goto(self, n, scope): pass
    def _stmt_Label(self, n, scope): pass
    def _stmt_RawStat(self, n, scope): pass

    # --- expressions -------------------------------------------------------
    def _funcexpr(self, func: A.FunctionExpr, scope: Scope) -> None:
        inner = Scope(scope)
        new_params = []
        for p in func.params:
            new = self._fresh(p)
            inner.declare(p, new)
            new_params.append(new)
        func.params = new_params
        for stmt in func.body.stmts:
            self._stmt(stmt, inner)

    def _expr(self, node: A.Node, scope: Scope) -> None:
        t = type(node).__name__
        getattr(self, "_expr_" + t, self._expr_noop)(node, scope)

    def _expr_noop(self, node, scope): pass

    def _expr_Name(self, n: A.Name, scope: Scope) -> None:
        new = scope.resolve(n.name)
        if new is not None:
            n.name = new
            n.is_local = True

    def _expr_Index(self, n: A.Index, scope: Scope) -> None:
        self._expr(n.obj, scope)
        # Only rename the key if it is a computed (bracketed) expression; a
        # dotted field name (obj.field) is a string key, never a variable.
        if not n.dot:
            self._expr(n.key, scope)

    def _expr_Call(self, n: A.Call, scope: Scope) -> None:
        self._expr(n.func, scope)
        for a in n.args:
            self._expr(a, scope)

    def _expr_MethodCall(self, n: A.MethodCall, scope: Scope) -> None:
        self._expr(n.obj, scope)
        for a in n.args:
            self._expr(a, scope)

    def _expr_BinOp(self, n: A.BinOp, scope: Scope) -> None:
        self._expr(n.left, scope)
        self._expr(n.right, scope)

    def _expr_UnOp(self, n: A.UnOp, scope: Scope) -> None:
        self._expr(n.operand, scope)

    def _expr_Paren(self, n: A.Paren, scope: Scope) -> None:
        self._expr(n.expr, scope)

    def _expr_FunctionExpr(self, n: A.FunctionExpr, scope: Scope) -> None:
        self._funcexpr(n, scope)

    def _expr_Table(self, n: A.Table, scope: Scope) -> None:
        for f in n.fields:
            if f.key is not None and f.bracketed:
                self._expr(f.key, scope)
            self._expr(f.value, scope)


def rename_locals(block: A.Block, namegen: NameGenerator,
                  rename: bool = True) -> A.Block:
    return Renamer(namegen, rename).run(block)
