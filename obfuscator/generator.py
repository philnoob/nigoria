"""Turn an AST back into Lua source.

BinOp/UnOp results are always single values, so wrapping them in parentheses is
always semantically safe and lets us avoid tracking operator precedence during
emission. Calls are never auto-parenthesised (that would truncate multi-value
results).
"""

from __future__ import annotations

from . import ast_nodes as A


def _encode_string(value: str, style: str) -> str:
    if style and style.startswith("["):
        # Long-bracket form; only valid if the content has no closing bracket
        # at the same level. Fall back to a quoted string otherwise.
        level = style.count("=")
        close = "]" + "=" * level + "]"
        if close not in value and not value.endswith("]"):
            return f"[{'=' * level}[{value}]{'=' * level}]"
        style = '"'
    q = '"' if style not in ("'", '"') else style
    out = [q]
    for ch in value:
        o = ord(ch)
        if ch == q:
            out.append("\\" + ch)
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif o < 32 or o == 127:
            out.append("\\%d" % o)
        else:
            out.append(ch)
    out.append(q)
    return "".join(out)


class Generator:
    def __init__(self, indent: str = ""):
        self.indent_unit = indent  # "" produces compact output
        self.parts: list[str] = []

    def generate(self, block: A.Block) -> str:
        self._block(block, 0)
        return "".join(self.parts)

    # --- helpers -----------------------------------------------------------
    def _w(self, s: str) -> None:
        self.parts.append(s)

    def _pad(self, level: int) -> str:
        return self.indent_unit * level if self.indent_unit else ""

    def _nl(self) -> str:
        return "\n" if self.indent_unit else " "

    # --- blocks ------------------------------------------------------------
    def _block(self, block: A.Block, level: int) -> None:
        for stmt in block.stmts:
            self._w(self._pad(level))
            self._stmt(stmt, level)
            self._w(self._nl())

    def _stmt(self, node: A.Node, level: int) -> None:
        m = getattr(self, "_stmt_" + type(node).__name__, None)
        if m is None:
            raise TypeError(f"cannot generate statement {type(node).__name__}")
        m(node, level)

    def _stmt_LocalAssign(self, n: A.LocalAssign, level: int) -> None:
        self._w("local " + ", ".join(n.names))
        if n.exprs:
            self._w(" = ")
            self._exprlist(n.exprs)
        self._w(";")

    def _stmt_Assign(self, n: A.Assign, level: int) -> None:
        self._w(", ".join(self._expr_str(t) for t in n.targets))
        self._w(" = ")
        self._exprlist(n.exprs)
        self._w(";")

    def _stmt_CallStat(self, n: A.CallStat, level: int) -> None:
        self._w(self._expr_str(n.call))
        self._w(";")

    def _stmt_Do(self, n: A.Do, level: int) -> None:
        self._w("do" + self._nl())
        self._block(n.body, level + 1)
        self._w(self._pad(level) + "end")

    def _stmt_While(self, n: A.While, level: int) -> None:
        self._w("while " + self._expr_str(n.cond) + " do" + self._nl())
        self._block(n.body, level + 1)
        self._w(self._pad(level) + "end")

    def _stmt_Repeat(self, n: A.Repeat, level: int) -> None:
        self._w("repeat" + self._nl())
        self._block(n.body, level + 1)
        self._w(self._pad(level) + "until " + self._expr_str(n.cond))

    def _stmt_If(self, n: A.If, level: int) -> None:
        for i, (cond, body) in enumerate(n.clauses):
            if i == 0:
                self._w("if " + self._expr_str(cond) + " then" + self._nl())
            elif cond is None:
                self._w(self._pad(level) + "else" + self._nl())
            else:
                self._w(self._pad(level) + "elseif " + self._expr_str(cond)
                        + " then" + self._nl())
            self._block(body, level + 1)
        self._w(self._pad(level) + "end")

    def _stmt_NumericFor(self, n: A.NumericFor, level: int) -> None:
        parts = [self._expr_str(n.start), self._expr_str(n.stop)]
        if n.step is not None:
            parts.append(self._expr_str(n.step))
        self._w("for " + n.var + " = " + ", ".join(parts) + " do" + self._nl())
        self._block(n.body, level + 1)
        self._w(self._pad(level) + "end")

    def _stmt_GenericFor(self, n: A.GenericFor, level: int) -> None:
        self._w("for " + ", ".join(n.names) + " in ")
        self._exprlist(n.exprs)
        self._w(" do" + self._nl())
        self._block(n.body, level + 1)
        self._w(self._pad(level) + "end")

    def _stmt_FunctionDecl(self, n: A.FunctionDecl, level: int) -> None:
        if n.is_local:
            self._w("local function " + n.local_name)
            self._func_tail(n.func, level)
            return
        self._w("function " + self._expr_str(n.target))
        self._func_tail(n.func, level)

    def _func_tail(self, func: A.FunctionExpr, level: int) -> None:
        params = list(func.params)
        # `self` is implicit for methods; it was injected during parse and is a
        # normal parameter here, so keep it only for non-method function exprs
        # when present. Method decls set is_method and we already added self.
        self._w("(" + ", ".join(params + (["..."] if func.is_vararg else [])) + ")"
                + self._nl())
        self._block(func.body, level + 1)
        self._w(self._pad(level) + "end")

    def _stmt_Return(self, n: A.Return, level: int) -> None:
        if n.exprs:
            self._w("return ")
            self._exprlist(n.exprs)
        else:
            self._w("return")
        self._w(";")

    def _stmt_Break(self, n: A.Break, level: int) -> None:
        self._w("break;")

    def _stmt_Continue(self, n: A.Continue, level: int) -> None:
        self._w("continue;")

    def _stmt_Goto(self, n: A.Goto, level: int) -> None:
        self._w("goto " + n.label + ";")

    def _stmt_Label(self, n: A.Label, level: int) -> None:
        self._w("::" + n.name + "::")

    def _stmt_RawStat(self, n: A.RawStat, level: int) -> None:
        self._w(n.text)

    # --- expressions -------------------------------------------------------
    def _exprlist(self, exprs: list[A.Node]) -> None:
        self._w(", ".join(self._expr_str(e) for e in exprs))

    def _expr_str(self, node: A.Node) -> str:
        m = getattr(self, "_expr_" + type(node).__name__, None)
        if m is None:
            raise TypeError(f"cannot generate expression {type(node).__name__}")
        return m(node)

    def _expr_Nil(self, n): return "nil"
    def _expr_TrueLit(self, n): return "true"
    def _expr_FalseLit(self, n): return "false"
    def _expr_Vararg(self, n): return "..."
    def _expr_Number(self, n): return n.raw
    def _expr_Raw(self, n): return n.text
    def _expr_String(self, n): return _encode_string(n.value, n.style)
    def _expr_Name(self, n): return n.name

    def _expr_Index(self, n: A.Index) -> str:
        base = self._expr_str(n.obj)
        if n.dot and isinstance(n.key, A.String):
            return base + "." + n.key.value
        return base + "[" + self._expr_str(n.key) + "]"

    def _expr_Call(self, n: A.Call) -> str:
        return (self._expr_str(n.func) + "("
                + ", ".join(self._expr_str(a) for a in n.args) + ")")

    def _expr_MethodCall(self, n: A.MethodCall) -> str:
        return (self._expr_str(n.obj) + ":" + n.method + "("
                + ", ".join(self._expr_str(a) for a in n.args) + ")")

    def _expr_BinOp(self, n: A.BinOp) -> str:
        return ("(" + self._expr_str(n.left) + " " + n.op + " "
                + self._expr_str(n.right) + ")")

    def _expr_UnOp(self, n: A.UnOp) -> str:
        sep = " " if n.op == "not" else ""
        return "(" + n.op + sep + self._expr_str(n.operand) + ")"

    def _expr_Paren(self, n: A.Paren) -> str:
        return "(" + self._expr_str(n.expr) + ")"

    def _expr_FunctionExpr(self, n: A.FunctionExpr) -> str:
        sub = Generator(self.indent_unit)
        params = list(n.params) + (["..."] if n.is_vararg else [])
        sub._w("function(" + ", ".join(params) + ")" + sub._nl())
        sub._block(n.body, 1)
        sub._w("end")
        return "".join(sub.parts)

    def _expr_Table(self, n: A.Table) -> str:
        items = []
        for f in n.fields:
            if f.key is None:
                items.append(self._expr_str(f.value))
            elif f.bracketed:
                items.append("[" + self._expr_str(f.key) + "] = "
                             + self._expr_str(f.value))
            elif isinstance(f.key, A.String):
                items.append(f.key.value + " = " + self._expr_str(f.value))
            else:
                items.append("[" + self._expr_str(f.key) + "] = "
                             + self._expr_str(f.value))
        return "{" + ", ".join(items) + "}"


def generate(block: A.Block, indent: str = "") -> str:
    return Generator(indent).generate(block)
