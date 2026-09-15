"""Control-flow flattening.

A straight-line block is turned into a dispatcher loop driven by an opaque
state variable. Each original top-level statement becomes one case; the cases
are emitted in a shuffled order while a "next state" chain preserves execution
order, so the source order no longer reflects the runtime order.

Correctness relies on running AFTER renaming: all locals are globally unique, so
hoisting their declarations to the top of the block cannot capture or shadow any
other name. Blocks containing top-level break/goto/label/<const> are left alone
(a break would otherwise target the dispatcher loop instead of the real loop).
"""

from __future__ import annotations

from .. import ast_nodes as A


class ControlFlow:
    def __init__(self, rng, state_namer, apply_to_functions: bool = True):
        self.rng = rng
        self.state_namer = state_namer
        self.apply_to_functions = apply_to_functions

    def run(self, chunk: A.Block) -> A.Block:
        if self.apply_to_functions:
            # Flatten every function body first (order among them is irrelevant
            # because each body is an independent block object).
            for fe in _all_function_exprs(chunk):
                self._flatten_inplace(fe.body)
        self._flatten_inplace(chunk)
        return chunk

    def _flatten_inplace(self, block: A.Block) -> None:
        new = self._flatten(block.stmts)
        if new is not None:
            block.stmts = new

    # --- the actual flattening --------------------------------------------
    def _flatten(self, stmts: list[A.Node]) -> list[A.Node] | None:
        if len(stmts) < 3:
            return None
        for s in stmts:
            if isinstance(s, (A.Break, A.Continue, A.Goto, A.Label)):
                return None
            if isinstance(s, A.LocalAssign) and any(s.attribs):
                return None
        for s in stmts[:-1]:
            if isinstance(s, A.Return):
                return None  # early return would need real jump handling

        hoist: list[str] = []
        cases: list[A.Node | None] = []
        for s in stmts:
            if isinstance(s, A.LocalAssign):
                hoist.extend(s.names)
                if s.exprs:
                    targets = [A.Name(nm) for nm in s.names]
                    cases.append(A.Assign(targets, s.exprs))
                else:
                    cases.append(None)
            elif isinstance(s, A.FunctionDecl) and s.is_local:
                hoist.append(s.local_name)
                cases.append(A.Assign([A.Name(s.local_name)], [s.func]))
            else:
                cases.append(s)

        n = len(cases)
        labels = self._unique_labels(n)
        state_var = self.state_namer.new()

        order = list(range(n))
        self.rng.shuffle(order)
        clauses: list[tuple] = []
        for k in order:
            body_stmts: list[A.Node] = []
            case = cases[k]
            is_return = isinstance(case, A.Return)
            if case is not None:
                body_stmts.append(case)
            nxt = labels[k + 1] if k + 1 < n else 0
            if not is_return:
                body_stmts.append(
                    A.Assign([A.Name(state_var)], [A.Number(str(nxt))]))
            cond = A.BinOp("==", A.Name(state_var), A.Number(str(labels[k])))
            clauses.append((cond, A.Block(body_stmts)))
        dispatch = A.If(clauses)

        result: list[A.Node] = []
        if hoist:
            result.append(A.LocalAssign(list(hoist), [], [None] * len(hoist)))
        result.append(
            A.LocalAssign([state_var], [A.Number(str(labels[0]))], [None]))
        loop_cond = A.BinOp("~=", A.Name(state_var), A.Number("0"))
        result.append(A.While(loop_cond, A.Block([dispatch])))
        return result

    def _unique_labels(self, n: int) -> list[int]:
        labels: list[int] = []
        seen = {0}
        while len(labels) < n:
            v = self.rng.randint(1000, 9_999_999)
            if v not in seen:
                seen.add(v)
                labels.append(v)
        return labels


def _all_function_exprs(node: A.Node):
    """Yield every FunctionExpr anywhere under ``node`` (deepest last)."""
    out: list[A.FunctionExpr] = []

    def walk(x):
        if isinstance(x, A.FunctionExpr):
            out.append(x)
        if isinstance(x, A.FunctionDecl):
            walk(x.func)
            if x.target is not None:
                walk(x.target)
            return
        if hasattr(x, "__dict__"):
            for v in vars(x).values():
                _walk_value(v)

    def _walk_value(v):
        if isinstance(v, A.Node):
            walk(v)
        elif isinstance(v, list):
            for it in v:
                _walk_value(it)
        elif isinstance(v, tuple):
            for it in v:
                _walk_value(it)

    walk(node)
    return out


def flatten(chunk: A.Block, rng, state_namer, apply_to_functions=True) -> A.Block:
    return ControlFlow(rng, state_namer, apply_to_functions).run(chunk)
