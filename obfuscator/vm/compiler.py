"""Compile a Lua AST (subset) into a serialized VM program.

Opcodes come from a per-build ``OpMap`` so the numbers differ every build. Every
constant (including numbers, stored as their textual form) is interned into a
single pool, so the emitted program is a tree of plain integers — ready to be
flattened into an encrypted bytecode stream by the emitter.
"""

from __future__ import annotations

from .. import ast_nodes as A
import random

from .opcodes import OpMap


class Unsupported(Exception):
    """Raised when the AST uses a construct the VM does not implement."""


BINOP_NAMES = {
    "+": "B_add", "-": "B_sub", "*": "B_mul", "/": "B_div", "%": "B_mod",
    "^": "B_pow", "//": "B_idiv", "..": "B_concat", "==": "B_eq", "~=": "B_ne",
    "<": "B_lt", "<=": "B_le", ">": "B_gt", ">=": "B_ge", "and": "B_and",
    "or": "B_or", "&": "B_band", "|": "B_bor", "~": "B_bxor", "<<": "B_shl",
    ">>": "B_shr",
}
UNOP_NAMES = {"-": "U_neg", "not": "U_not", "#": "U_len", "~": "U_bnot"}


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
    def __init__(self, ops: OpMap, rng: random.Random | None = None):
        self.ops = ops
        self.rng = rng or random.Random()
        self.next_slot = 1
        self.consts: list[str] = []       # string form of every constant
        self.const_index: dict[str, int] = {}
        self.protos: list = []

    def op(self, name: str) -> int:
        # random alias each time -> the same operation appears under several
        # different numbers throughout the bytecode
        return self.ops.pick(name, self.rng)

    def new_slot(self) -> int:
        s = self.next_slot
        self.next_slot += 1
        return s

    def intern(self, value: str) -> int:
        if value in self.const_index:
            return self.const_index[value]
        self.consts.append(value)
        idx = len(self.consts)
        self.const_index[value] = idx
        return idx

    # numbers are interned by a tagged key so "1" (number) never collides with
    # the string "1"
    def intern_num(self, raw: str) -> int:
        # Store the numeric text plainly; EKNUM does tonumber(K[idx]) at
        # runtime. Sharing a slot with an identical string literal is harmless.
        return self.intern(raw)

    def compile_chunk(self, block: A.Block):
        top = ScopeC(None)
        body = self._block(block, top)
        self.protos.append({"np": 0, "va": 1, "params": [], "body": body})
        return {
            "chunk": len(self.protos),
            "protos": self.protos,
            "consts": self.consts,
            "_ops": self.ops,
        }

    def _block(self, block: A.Block, scope: ScopeC) -> list:
        return [self._stmt(s, scope) for s in block.stmts]

    def _stmt(self, node: A.Node, scope: ScopeC) -> list:
        t = type(node)
        o = self.op
        if t is A.LocalAssign:
            if any(node.attribs):
                raise Unsupported("local attributes")
            exprs = [self._expr(e, scope) for e in node.exprs]
            slots = []
            for name in node.names:
                sid = self.new_slot()
                scope.map[name] = sid
                slots.append(sid)
            return [o("SLOCAL"), slots, exprs]
        if t is A.Assign:
            exprs = [self._expr(e, scope) for e in node.exprs]
            targets = [self._target(tg, scope) for tg in node.targets]
            return [o("SASSIGN"), targets, exprs]
        if t is A.CallStat:
            return [o("SCALL"), self._expr(node.call, scope)]
        if t is A.Do:
            return [o("SDO"), self._block_scoped(node.body, scope)]
        if t is A.While:
            return [o("SWHILE"), self._expr(node.cond, scope),
                    self._block_scoped(node.body, scope)]
        if t is A.Repeat:
            inner = ScopeC(scope)
            body = self._block(node.body, inner)
            cond = self._expr(node.cond, inner)
            return [o("SREPEAT"), body, cond]
        if t is A.If:
            clauses = []
            else_block = None
            for cond, blk in node.clauses:
                if cond is None:
                    else_block = self._block_scoped(blk, scope)
                else:
                    clauses.append([self._expr(cond, scope),
                                    self._block_scoped(blk, scope)])
            return [o("SIF"), clauses, else_block]
        if t is A.NumericFor:
            start = self._expr(node.start, scope)
            stop = self._expr(node.stop, scope)
            step = self._expr(node.step, scope) if node.step is not None else None
            inner = ScopeC(scope)
            sid = self.new_slot()
            inner.map[node.var] = sid
            body = self._block(node.body, inner)
            return [o("SNUMFOR"), sid, start, stop, step, body]
        if t is A.GenericFor:
            exprs = [self._expr(e, scope) for e in node.exprs]
            inner = ScopeC(scope)
            slots = []
            for name in node.names:
                sid = self.new_slot()
                inner.map[name] = sid
                slots.append(sid)
            body = self._block(node.body, inner)
            return [o("SGENFOR"), slots, exprs, body]
        if t is A.Return:
            return [o("SRETURN"), [self._expr(e, scope) for e in node.exprs]]
        if t is A.Break:
            return [o("SBREAK")]
        if t is A.Continue:
            return [o("SCONTINUE")]
        if t is A.FunctionDecl:
            if node.is_local:
                sid = self.new_slot()
                scope.map[node.local_name] = sid
                proto = self._funcexpr(node.func, scope)
                return [o("SLOCAL"), [sid], [proto]]
            target = self._target(node.target, scope)
            proto = self._funcexpr(node.func, scope)
            return [o("SASSIGN"), [target], [proto]]
        if t in (A.Goto, A.Label):
            raise Unsupported("goto/label")
        if t is A.RawStat:
            raise Unsupported("raw statement")
        raise Unsupported(f"stmt {t.__name__}")

    def _block_scoped(self, block: A.Block, scope: ScopeC) -> list:
        return self._block(block, ScopeC(scope))

    def _target(self, node: A.Node, scope: ScopeC) -> list:
        o = self.op
        if isinstance(node, A.Name):
            sid = scope.resolve(node.name)
            if sid is not None:
                return [o("ELOCAL"), sid]
            return [o("EGLOBAL"), self.intern(node.name)]
        if isinstance(node, A.Index):
            return self._expr(node, scope)
        raise Unsupported("assignment target")

    def _expr(self, node: A.Node, scope: ScopeC) -> list:
        t = type(node)
        o = self.op
        if t is A.Nil:
            return [o("ENIL")]
        if t is A.TrueLit:
            return [o("ETRUE")]
        if t is A.FalseLit:
            return [o("EFALSE")]
        if t is A.Vararg:
            return [o("EVARARG")]
        if t is A.Number:
            return [o("EKNUM"), self.intern_num(_clean_number(node.raw))]
        if t is A.String:
            return [o("EK"), self.intern(node.value)]
        if t is A.Raw:
            raise Unsupported("interpolated string")
        if t is A.Name:
            sid = scope.resolve(node.name)
            if sid is not None:
                return [o("ELOCAL"), sid]
            return [o("EGLOBAL"), self.intern(node.name)]
        if t is A.Index:
            key = node.key
            if node.dot and isinstance(key, A.String):
                kexpr = [o("EK"), self.intern(key.value)]
            else:
                kexpr = self._expr(key, scope)
            return [o("EINDEX"), self._expr(node.obj, scope), kexpr]
        if t is A.Call:
            return [o("ECALL"), self._expr(node.func, scope),
                    [self._expr(a, scope) for a in node.args]]
        if t is A.MethodCall:
            return [o("EMETHOD"), self._expr(node.obj, scope),
                    self.intern(node.method),
                    [self._expr(a, scope) for a in node.args]]
        if t is A.BinOp:
            name = BINOP_NAMES.get(node.op)
            if name is None:
                raise Unsupported(f"binop {node.op}")
            return [o("EBIN"), o(name), self._expr(node.left, scope),
                    self._expr(node.right, scope)]
        if t is A.UnOp:
            name = UNOP_NAMES.get(node.op)
            if name is None:
                raise Unsupported(f"unop {node.op}")
            return [o("EUN"), o(name), self._expr(node.operand, scope)]
        if t is A.Paren:
            return [o("EPAREN"), self._expr(node.expr, scope)]
        if t is A.FunctionExpr:
            return self._funcexpr(node, scope)
        if t is A.Table:
            fields = []
            for f in node.fields:
                if f.key is None:
                    fields.append([o("T_arr"), self._expr(f.value, scope)])
                elif isinstance(f.key, A.String) and not f.bracketed:
                    fields.append([o("T_key"), [o("EK"), self.intern(f.key.value)],
                                   self._expr(f.value, scope)])
                else:
                    fields.append([o("T_key"), self._expr(f.key, scope),
                                   self._expr(f.value, scope)])
            return [o("ETABLE"), fields]
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
        return [self.op("EFUNC"), len(self.protos)]


def _clean_number(raw: str) -> str:
    return raw.strip().replace("_", "")


def compile_chunk(block: A.Block, ops: OpMap | None = None,
                  rng: random.Random | None = None):
    return Compiler(ops or OpMap(), rng).compile_chunk(block)
