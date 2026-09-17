"""Junk / decoy code injection.

Prepends unused local declarations, decoy tables and dead helper functions to
blocks. Everything injected is pure and never referenced by real code, so
behaviour is preserved while the output grows and gains misleading structure
that slows manual and automated deobfuscation.

Runs LAST (after every other AST transform) so it never interferes with
renaming, control-flow flattening, or the string/global passes.
"""

from __future__ import annotations

from .. import ast_nodes as A


class JunkInjector:
    def __init__(self, namer, rng, intensity: int = 1,
                 apply_to_functions: bool = True):
        self.namer = namer
        self.rng = rng
        self.intensity = max(1, intensity)
        self.apply_to_functions = apply_to_functions

    # Cap how many function bodies get junk so total overhead stays bounded
    # regardless of how many functions the script has.
    MAX_FUNC_INJECTIONS = 8

    def run(self, chunk: A.Block) -> A.Block:
        if self.apply_to_functions:
            funcs = _all_function_exprs(chunk)
            if len(funcs) > self.MAX_FUNC_INJECTIONS:
                funcs = self.rng.sample(funcs, self.MAX_FUNC_INJECTIONS)
            for fe in funcs:
                fe.body.stmts = self._make_junk() + fe.body.stmts
        chunk.stmts = self._make_junk() + chunk.stmts
        return chunk

    def _num_expr(self, depth: int = 0) -> A.Node:
        # Purely numeric so the (immediately-evaluated) assignment never errors.
        if depth > 2 or self.rng.random() < 0.5:
            return A.Number(str(self.rng.randint(1, 999999)))
        return A.BinOp(self.rng.choice(["+", "-", "*"]),
                       self._num_expr(depth + 1), self._num_expr(depth + 1))

    def _literal(self) -> A.Node:
        # A safe standalone value: number or string (no mixed-type arithmetic).
        if self.rng.random() < 0.5:
            return self._num_expr()
        return A.String(_rand_string(self.rng))

    def _make_junk(self) -> list[A.Node]:
        count = 2 + self.intensity * 2
        stmts: list[A.Node] = []
        for _ in range(count):
            kind = self.rng.randint(0, 3)
            nm = self.namer.new()
            if kind == 3:
                # dead helper function (its body is never evaluated)
                param = self.namer.new()
                body = A.Block([A.Return([A.BinOp(
                    "+", A.Name(param), self._num_expr())])])
                stmts.append(A.FunctionDecl(
                    target=None, is_method=False,
                    func=A.FunctionExpr([param], False, body),
                    is_local=True, local_name=nm))
            elif kind == 2:
                # decoy table of safe literals
                fields = [A.TableField(None, self._literal())
                          for _ in range(self.rng.randint(1, 4))]
                stmts.append(A.LocalAssign([nm], [A.Table(fields)], [None]))
            else:
                stmts.append(A.LocalAssign([nm], [self._literal()], [None]))
        return stmts


def _rand_string(rng) -> str:
    alphabet = "abcdef0123456789_"
    return "".join(rng.choice(alphabet) for _ in range(rng.randint(6, 18)))


def _all_function_exprs(node: A.Node):
    out: list[A.FunctionExpr] = []

    def walk(x):
        if isinstance(x, A.FunctionExpr):
            out.append(x)
        if hasattr(x, "__dict__"):
            for v in vars(x).values():
                _walk_value(v)

    def _walk_value(v):
        if isinstance(v, A.Node):
            walk(v)
        elif isinstance(v, (list, tuple)):
            for it in v:
                _walk_value(it)

    walk(node)
    return out


def inject_junk(chunk: A.Block, namer, rng, intensity: int = 1,
                apply_to_functions: bool = True) -> A.Block:
    return JunkInjector(namer, rng, intensity, apply_to_functions).run(chunk)
