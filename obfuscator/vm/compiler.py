"""Compile a Lua AST (subset) into a serialized VM program.

Output is a plain nested structure of ints / RawNum / lists that the emitter
turns into a Lua table literal. Locals become unique integer slot ids; strings
are interned into a pool; nested functions become protos.
"""

from __future__ import annotations

from .. import ast_nodes as A


class Unsupported(Exception):
    """Raised when the AST uses a construct the VM does not implement."""


class RawNum:
    """A numeric literal emitted verbatim (keeps hex/float exactness)."""
    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text


# expression opcodes
EK, ENIL, ETRUE, EFALSE, EVARARG = 1, 2, 3, 4, 5
ELOCAL, EGLOBAL, EINDEX, ECALL, EMETHOD = 6, 7, 8, 9, 10
EBIN, EUN, EFUNC, ETABLE, EPAREN, EKN = 11, 12, 13, 14, 15, 16

# statement opcodes
SLOCAL, SASSIGN, SCALL, SIF, SWHILE = 1, 2, 3, 4, 5
SREPEAT, SNUMFOR, SGENFOR, SRETURN, SBREAK, SCONTINUE, SDO = 6, 7, 8, 9, 10, 11, 12

BINOPS = {
    "+": 1, "-": 2, "*": 3, "/": 4, "%": 5, "^": 6, "//": 7, "..": 8,
    "==": 9, "~=": 10, "<": 11, "<=": 12, ">": 13, ">=": 14,
    "and": 15, "or": 16, "&": 17, "|": 18, "~": 19, "<<": 20, ">>": 21,
}
UNOPS = {"-": 1, "not": 2, "#": 3, "~": 4}


class ScopeC:
    def __init__(self, parent):
        self.parent = parent
        self.map: dict[str, int] = {}

    def resolve(self, name: str):
        s = self
        while s is not None:
            if name in s.map:
                return s.map[name]
            s = s.parent
        return None


class Compiler:
    def __init__(self):
        self.next_slot = 1
        self.consts: list[str] = []
        self.const_index: dict[str, int] = {}
        self.protos: list = []

    # --- helpers ----------------------------------------------------------
    def new_slot(self) -> int:
        s = self.next_slot
        self.next_slot += 1
        return s

    def intern(self, value: str) -> int:
        if value in self.const_index:
            return self.const_index[value]
        self.consts.append(value)
        idx = len(self.consts)  # 1-based
        self.const_index[value] = idx
        return idx

    # --- entry ------------------------------------------------------------
    def compile_chunk(self, block: A.Block):
        top = ScopeC(None)
        body = self._block(block, top)
        # chunk proto: vararg, no params
        self.protos.append({"np": 0, "va": 1, "params": [], "body": body})
        chunk_index = len(self.protos)
        return {
            "chunk": chunk_index,
            "protos": self.protos,
            "consts": self.consts,
        }

    # --- blocks / statements ---------------------------------------------
    def _block(self, block: A.Block, scope: ScopeC) -> list:
        return [self._stmt(s, scope) for s in block.stmts]

    def _stmt(self, node: A.Node, scope: ScopeC) -> list:
        t = type(node)
        if t is A.LocalAssign:
            if any(node.attribs):
                raise Unsupported("local attributes")
            exprs = [self._expr(e, scope) for e in node.exprs]
            slots = []
            for name in node.names:
                sid = self.new_slot()
                scope.map[name] = sid
                slots.append(sid)
            return [SLOCAL, slots, exprs]
        if t is A.Assign:
            exprs = [self._expr(e, scope) for e in node.exprs]
            targets = [self._target(tg, scope) for tg in node.targets]
            return [SASSIGN, targets, exprs]
        if t is A.CallStat:
            return [SCALL, self._expr(node.call, scope)]
        if t is A.Do:
            return [SDO, self._block_scoped(node.body, scope)]
        if t is A.While:
            cond = self._expr(node.cond, scope)
            return [SWHILE, cond, self._block_scoped(node.body, scope)]
        if t is A.Repeat:
            # condition can see body locals -> compile in the same inner scope
            inner = ScopeC(scope)
            body = self._block(node.body, inner)
            cond = self._expr(node.cond, inner)
            return [SREPEAT, body, cond]
        if t is A.If:
            clauses = []
            else_block = None
            for cond, blk in node.clauses:
                if cond is None:
                    else_block = self._block_scoped(blk, scope)
                else:
                    clauses.append([self._expr(cond, scope),
                                    self._block_scoped(blk, scope)])
            return [SIF, clauses, else_block]
        if t is A.NumericFor:
            start = self._expr(node.start, scope)
            stop = self._expr(node.stop, scope)
            step = self._expr(node.step, scope) if node.step is not None else None
            inner = ScopeC(scope)
            sid = self.new_slot()
            inner.map[node.var] = sid
            body = self._block(node.body, inner)
            return [SNUMFOR, sid, start, stop, step, body]
        if t is A.GenericFor:
            exprs = [self._expr(e, scope) for e in node.exprs]
            inner = ScopeC(scope)
            slots = []
            for name in node.names:
                sid = self.new_slot()
                inner.map[name] = sid
                slots.append(sid)
            body = self._block(node.body, inner)
            return [SGENFOR, slots, exprs, body]
        if t is A.Return:
            return [SRETURN, [self._expr(e, scope) for e in node.exprs]]
        if t is A.Break:
            return [SBREAK]
        if t is A.Continue:
            return [SCONTINUE]
        if t is A.FunctionDecl:
            if node.is_local:
                sid = self.new_slot()
                scope.map[node.local_name] = sid
                proto = self._funcexpr(node.func, scope)
                return [SLOCAL, [sid], [proto]]
            target = self._target(node.target, scope)
            proto = self._funcexpr(node.func, scope)
            return [SASSIGN, [target], [proto]]
        if t in (A.Goto, A.Label):
            raise Unsupported("goto/label")
        if t is A.RawStat:
            raise Unsupported("raw statement")
        raise Unsupported(f"stmt {t.__name__}")

    def _block_scoped(self, block: A.Block, scope: ScopeC) -> list:
        return self._block(block, ScopeC(scope))

    def _target(self, node: A.Node, scope: ScopeC) -> list:
        if isinstance(node, A.Name):
            sid = scope.resolve(node.name)
            if sid is not None:
                return [ELOCAL, sid]
            return [EGLOBAL, self.intern(node.name)]
        if isinstance(node, A.Index):
            return self._expr(node, scope)
        raise Unsupported("assignment target")

    # --- expressions ------------------------------------------------------
    def _expr(self, node: A.Node, scope: ScopeC) -> list:
        t = type(node)
        if t is A.Nil:
            return [ENIL]
        if t is A.TrueLit:
            return [ETRUE]
        if t is A.FalseLit:
            return [EFALSE]
        if t is A.Vararg:
            return [EVARARG]
        if t is A.Number:
            return [EKN, RawNum(_clean_number(node.raw))]
        if t is A.String:
            return [EK, self.intern(node.value)]
        if t is A.Raw:
            raise Unsupported("interpolated string")
        if t is A.Name:
            sid = scope.resolve(node.name)
            if sid is not None:
                return [ELOCAL, sid]
            return [EGLOBAL, self.intern(node.name)]
        if t is A.Index:
            key = node.key
            if node.dot and isinstance(key, A.String):
                kexpr = [EK, self.intern(key.value)]
            else:
                kexpr = self._expr(key, scope)
            return [EINDEX, self._expr(node.obj, scope), kexpr]
        if t is A.Call:
            return [ECALL, self._expr(node.func, scope),
                    [self._expr(a, scope) for a in node.args]]
        if t is A.MethodCall:
            return [EMETHOD, self._expr(node.obj, scope),
                    self.intern(node.method),
                    [self._expr(a, scope) for a in node.args]]
        if t is A.BinOp:
            code = BINOPS.get(node.op)
            if code is None:
                raise Unsupported(f"binop {node.op}")
            return [EBIN, code, self._expr(node.left, scope),
                    self._expr(node.right, scope)]
        if t is A.UnOp:
            code = UNOPS.get(node.op)
            if code is None:
                raise Unsupported(f"unop {node.op}")
            return [EUN, code, self._expr(node.operand, scope)]
        if t is A.Paren:
            return [EPAREN, self._expr(node.expr, scope)]
        if t is A.FunctionExpr:
            return self._funcexpr(node, scope)
        if t is A.Table:
            fields = []
            for f in node.fields:
                if f.key is None:
                    fields.append([0, self._expr(f.value, scope)])
                elif isinstance(f.key, A.String) and not f.bracketed:
                    fields.append([1, [EK, self.intern(f.key.value)],
                                   self._expr(f.value, scope)])
                else:
                    fields.append([1, self._expr(f.key, scope),
                                   self._expr(f.value, scope)])
            return [ETABLE, fields]
        raise Unsupported(f"expr {t.__name__}")

    def _funcexpr(self, func: A.FunctionExpr, scope: ScopeC) -> list:
        inner = ScopeC(scope)
        params = []
        for p in func.params:
            sid = self.new_slot()
            inner.map[p] = sid
            params.append(sid)
        body = self._block(func.body, inner)
        self.protos.append({
            "np": len(params), "va": 1 if func.is_vararg else 0,
            "params": params, "body": body,
        })
        return [EFUNC, len(self.protos)]


def _clean_number(raw: str) -> str:
    s = raw.strip().replace("_", "")
    return s


def compile_chunk(block: A.Block):
    return Compiler().compile_chunk(block)
