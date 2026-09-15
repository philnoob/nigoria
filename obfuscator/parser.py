"""A recursive-descent parser for Lua 5.1 with best-effort Luau support.

If the parser encounters something it cannot represent faithfully it raises
``ParseError``; callers (the pipeline) then fall back to source-level packing
so obfuscation still succeeds on any input.
"""

from __future__ import annotations

from . import ast_nodes as A
from .lexer import Token, tokenize


class ParseError(Exception):
    pass


# Binary operator precedence (left, right). Higher binds tighter.
BINPRI = {
    "or": (1, 1), "and": (2, 2),
    "<": (3, 3), ">": (3, 3), "<=": (3, 3), ">=": (3, 3), "~=": (3, 3), "==": (3, 3),
    "|": (4, 4), "~": (5, 5), "&": (6, 6),
    "<<": (7, 7), ">>": (7, 7),
    "..": (9, 8),          # right associative
    "+": (10, 10), "-": (10, 10),
    "*": (11, 11), "/": (11, 11), "//": (11, 11), "%": (11, 11),
    "^": (14, 13),         # right associative, binds tighter than unary
}
UNARY_PRI = 12
UNARY_OPS = {"not", "-", "#", "~"}
COMPOUND = {"+=", "-=", "*=", "/=", "%=", "^=", "..=", "//=", "<<=", ">>="}


class Parser:
    def __init__(self, tokens: list[Token]):
        self.toks = tokens
        self.pos = 0

    # --- token helpers -----------------------------------------------------
    @property
    def cur(self) -> Token:
        return self.toks[self.pos]

    def _peek(self, off: int = 1) -> Token:
        j = self.pos + off
        return self.toks[j] if j < len(self.toks) else self.toks[-1]

    def _advance(self) -> Token:
        t = self.toks[self.pos]
        if t.type != "EOF":
            self.pos += 1
        return t

    def _check(self, ttype: str, value: str | None = None) -> bool:
        t = self.cur
        if t.type != ttype:
            return False
        return value is None or t.value == value

    def _accept(self, ttype: str, value: str | None = None) -> Token | None:
        if self._check(ttype, value):
            return self._advance()
        return None

    def _expect(self, ttype: str, value: str | None = None) -> Token:
        if not self._check(ttype, value):
            want = value or ttype
            raise ParseError(
                f"expected {want!r} but got {self.cur.value!r} "
                f"at line {self.cur.line}")
        return self._advance()

    def _is_kw(self, *words: str) -> bool:
        return self.cur.type == "KEYWORD" and self.cur.value in words

    def _is_sym(self, *syms: str) -> bool:
        return self.cur.type == "SYMBOL" and self.cur.value in syms

    # --- entry point -------------------------------------------------------
    def parse_chunk(self) -> A.Block:
        block = self._parse_block()
        if self.cur.type != "EOF":
            raise ParseError(f"unexpected {self.cur.value!r} at line {self.cur.line}")
        return block

    BLOCK_END = {"end", "else", "elseif", "until"}

    def _parse_block(self) -> A.Block:
        stmts: list[A.Node] = []
        while True:
            if self.cur.type == "EOF":
                break
            if self._is_kw(*self.BLOCK_END):
                break
            if self._is_kw("return"):
                stmts.append(self._parse_return())
                break
            stmt = self._parse_statement()
            if stmt is not None:
                stmts.append(stmt)
        return A.Block(stmts)

    # --- statements --------------------------------------------------------
    def _parse_statement(self) -> A.Node | None:
        t = self.cur
        if self._is_sym(";"):
            self._advance()
            return None
        if self._is_sym("::"):
            return self._parse_label()
        if t.type == "KEYWORD":
            handler = {
                "local": self._parse_local,
                "if": self._parse_if,
                "while": self._parse_while,
                "repeat": self._parse_repeat,
                "for": self._parse_for,
                "do": self._parse_do,
                "function": self._parse_function_stat,
                "break": self._parse_break,
                "goto": self._parse_goto,
            }.get(t.value)
            if handler:
                return handler()
            # Luau soft keyword `type` alias declaration -> bail to fallback.
            raise ParseError(f"unsupported statement {t.value!r} at line {t.line}")
        # Luau soft-keyword `continue` used as a bare statement.
        if t.type == "NAME" and t.value == "continue":
            nxt = self._peek()
            if (nxt.type == "EOF"
                    or (nxt.type == "KEYWORD" and nxt.value in self.BLOCK_END)
                    or (nxt.type == "SYMBOL" and nxt.value == ";")):
                self._advance()
                return A.Continue()
        # Otherwise: assignment or call statement.
        return self._parse_expr_statement()

    def _parse_label(self) -> A.Label:
        self._expect("SYMBOL", "::")
        name = self._expect("NAME").value
        self._expect("SYMBOL", "::")
        return A.Label(name)

    def _parse_goto(self) -> A.Goto:
        self._advance()
        name = self._expect("NAME").value
        return A.Goto(name)

    def _parse_break(self) -> A.Break:
        self._advance()
        return A.Break()

    def _parse_do(self) -> A.Do:
        self._advance()
        body = self._parse_block()
        self._expect("KEYWORD", "end")
        return A.Do(body)

    def _parse_while(self) -> A.While:
        self._advance()
        cond = self._parse_expr()
        self._expect("KEYWORD", "do")
        body = self._parse_block()
        self._expect("KEYWORD", "end")
        return A.While(cond, body)

    def _parse_repeat(self) -> A.Repeat:
        self._advance()
        body = self._parse_block()
        self._expect("KEYWORD", "until")
        cond = self._parse_expr()
        return A.Repeat(body, cond)

    def _parse_return(self) -> A.Return:
        self._advance()
        exprs: list[A.Node] = []
        if not (self._is_kw(*self.BLOCK_END) or self.cur.type == "EOF"
                or self._is_sym(";")):
            exprs = self._parse_exprlist()
        self._accept("SYMBOL", ";")
        return A.Return(exprs)

    def _parse_if(self) -> A.If:
        self._advance()
        clauses = []
        cond = self._parse_expr()
        self._expect("KEYWORD", "then")
        clauses.append((cond, self._parse_block()))
        while self._is_kw("elseif"):
            self._advance()
            c = self._parse_expr()
            self._expect("KEYWORD", "then")
            clauses.append((c, self._parse_block()))
        if self._accept("KEYWORD", "else"):
            clauses.append((None, self._parse_block()))
        self._expect("KEYWORD", "end")
        return A.If(clauses)

    def _parse_for(self) -> A.Node:
        self._advance()
        first = self._expect("NAME").value
        self._skip_type_annotation()
        if self._is_sym("="):
            self._advance()
            start = self._parse_expr()
            self._expect("SYMBOL", ",")
            stop = self._parse_expr()
            step = None
            if self._accept("SYMBOL", ","):
                step = self._parse_expr()
            self._expect("KEYWORD", "do")
            body = self._parse_block()
            self._expect("KEYWORD", "end")
            return A.NumericFor(first, start, stop, step, body)
        names = [first]
        while self._accept("SYMBOL", ","):
            names.append(self._expect("NAME").value)
            self._skip_type_annotation()
        self._expect("KEYWORD", "in")
        exprs = self._parse_exprlist()
        self._expect("KEYWORD", "do")
        body = self._parse_block()
        self._expect("KEYWORD", "end")
        return A.GenericFor(names, exprs, body)

    def _parse_local(self) -> A.Node:
        self._advance()
        if self._accept("KEYWORD", "function"):
            name = self._expect("NAME").value
            func = self._parse_funcbody(is_method=False)
            return A.FunctionDecl(target=None, is_method=False, func=func,
                                  is_local=True, local_name=name)
        names = [self._expect("NAME").value]
        attribs = [self._parse_attrib()]
        self._skip_type_annotation()
        while self._accept("SYMBOL", ","):
            names.append(self._expect("NAME").value)
            attribs.append(self._parse_attrib())
            self._skip_type_annotation()
        exprs: list[A.Node] = []
        if self._accept("SYMBOL", "="):
            exprs = self._parse_exprlist()
        return A.LocalAssign(names, exprs, attribs)

    def _parse_attrib(self) -> str | None:
        # Lua 5.4 <const>/<close>. Skip if present.
        if self._is_sym("<"):
            self._advance()
            name = self._expect("NAME").value
            self._expect("SYMBOL", ">")
            return name
        return None

    def _parse_function_stat(self) -> A.FunctionDecl:
        self._advance()
        target: A.Node = A.Name(self._expect("NAME").value)
        is_method = False
        while self._is_sym("."):
            self._advance()
            key = self._expect("NAME").value
            target = A.Index(target, A.String(key), dot=True)
        if self._accept("SYMBOL", ":"):
            key = self._expect("NAME").value
            target = A.Index(target, A.String(key), dot=True)
            is_method = True
        func = self._parse_funcbody(is_method)
        return A.FunctionDecl(target=target, is_method=is_method, func=func)

    def _parse_funcbody(self, is_method: bool) -> A.FunctionExpr:
        self._skip_generics()
        self._expect("SYMBOL", "(")
        params: list[str] = ["self"] if is_method else []
        is_vararg = False
        if not self._is_sym(")"):
            while True:
                if self._is_sym("..."):
                    self._advance()
                    is_vararg = True
                    break
                params.append(self._expect("NAME").value)
                self._skip_type_annotation()
                if not self._accept("SYMBOL", ","):
                    break
        self._expect("SYMBOL", ")")
        self._skip_return_type()
        body = self._parse_block()
        self._expect("KEYWORD", "end")
        return A.FunctionExpr(params, is_vararg, body)

    def _parse_expr_statement(self) -> A.Node:
        expr = self._parse_suffixed_expr()
        if self._is_sym("=") or self._is_sym(","):
            targets = [expr]
            while self._accept("SYMBOL", ","):
                targets.append(self._parse_suffixed_expr())
            self._expect("SYMBOL", "=")
            exprs = self._parse_exprlist()
            for tgt in targets:
                if not isinstance(tgt, (A.Name, A.Index)):
                    raise ParseError("invalid assignment target")
            return A.Assign(targets, exprs)
        if self.cur.type == "SYMBOL" and self.cur.value in COMPOUND:
            op = self._advance().value[:-1]  # strip '='
            rhs = self._parse_expr()
            if not isinstance(expr, (A.Name, A.Index)):
                raise ParseError("invalid compound assignment target")
            # desugar a += b  ->  a = a + b
            return A.Assign([expr], [A.BinOp(op, expr, A.Paren(rhs))])
        if isinstance(expr, (A.Call, A.MethodCall)):
            return A.CallStat(expr)
        raise ParseError(f"unexpected expression statement at line {self.cur.line}")

    # --- expressions -------------------------------------------------------
    def _parse_exprlist(self) -> list[A.Node]:
        exprs = [self._parse_expr()]
        while self._accept("SYMBOL", ","):
            exprs.append(self._parse_expr())
        return exprs

    def _parse_expr(self, limit: int = 0) -> A.Node:
        # unary
        if (self.cur.type == "KEYWORD" and self.cur.value == "not") or \
           (self.cur.type == "SYMBOL" and self.cur.value in ("-", "#", "~")):
            op = self._advance().value
            operand = self._parse_expr(UNARY_PRI)
            left: A.Node = A.UnOp(op, operand)
        else:
            left = self._parse_simple_expr()
        while True:
            op = None
            if self.cur.type == "KEYWORD" and self.cur.value in ("and", "or"):
                op = self.cur.value
            elif self.cur.type == "SYMBOL" and self.cur.value in BINPRI:
                op = self.cur.value
            if op is None:
                break
            lp, rp = BINPRI[op]
            if lp <= limit:
                break
            self._advance()
            right = self._parse_expr(rp)
            left = A.BinOp(op, left, right)
        return left

    def _parse_simple_expr(self) -> A.Node:
        t = self.cur
        if t.type == "NUMBER":
            self._advance()
            return A.Number(t.value)
        if t.type == "STRING":
            self._advance()
            return A.String(t.string_value, t.string_style or '"')
        if t.type == "RAW":
            self._advance()
            return A.Raw(t.value)
        if t.type == "KEYWORD":
            if t.value == "nil":
                self._advance(); return A.Nil()
            if t.value == "true":
                self._advance(); return A.TrueLit()
            if t.value == "false":
                self._advance(); return A.FalseLit()
            if t.value == "function":
                self._advance()
                return self._parse_funcbody(is_method=False)
        if self._is_sym("..."):
            self._advance()
            return A.Vararg()
        if self._is_sym("{"):
            return self._parse_table()
        return self._parse_suffixed_expr()

    def _parse_primary_expr(self) -> A.Node:
        if self._is_sym("("):
            self._advance()
            inner = self._parse_expr()
            self._expect("SYMBOL", ")")
            return A.Paren(inner)
        if self.cur.type == "NAME":
            return A.Name(self._advance().value)
        raise ParseError(
            f"unexpected token {self.cur.value!r} at line {self.cur.line}")

    def _parse_suffixed_expr(self) -> A.Node:
        expr = self._parse_primary_expr()
        while True:
            if self._is_sym("."):
                self._advance()
                key = self._expect("NAME").value
                expr = A.Index(expr, A.String(key), dot=True)
            elif self._is_sym("["):
                self._advance()
                key = self._parse_expr()
                self._expect("SYMBOL", "]")
                expr = A.Index(expr, key, dot=False)
            elif self._is_sym(":"):
                self._advance()
                method = self._expect("NAME").value
                args = self._parse_call_args()
                expr = A.MethodCall(expr, method, args)
            elif self._is_sym("(") or self._is_sym("{") or self.cur.type == "STRING":
                args = self._parse_call_args()
                expr = A.Call(expr, args)
            else:
                break
        return expr

    def _parse_call_args(self) -> list[A.Node]:
        if self.cur.type == "STRING":
            t = self._advance()
            return [A.String(t.string_value, t.string_style or '"')]
        if self._is_sym("{"):
            return [self._parse_table()]
        self._expect("SYMBOL", "(")
        args: list[A.Node] = []
        if not self._is_sym(")"):
            args = self._parse_exprlist()
        self._expect("SYMBOL", ")")
        return args

    def _parse_table(self) -> A.Table:
        self._expect("SYMBOL", "{")
        fields: list[A.TableField] = []
        while not self._is_sym("}"):
            if self._is_sym("["):
                self._advance()
                key = self._parse_expr()
                self._expect("SYMBOL", "]")
                self._expect("SYMBOL", "=")
                value = self._parse_expr()
                fields.append(A.TableField(key, value, bracketed=True))
            elif self.cur.type == "NAME" and self._peek().type == "SYMBOL" \
                    and self._peek().value == "=":
                key = A.String(self._advance().value)
                self._advance()  # =
                value = self._parse_expr()
                fields.append(A.TableField(key, value))
            else:
                value = self._parse_expr()
                fields.append(A.TableField(None, value))
            if not (self._accept("SYMBOL", ",") or self._accept("SYMBOL", ";")):
                break
        self._expect("SYMBOL", "}")
        return A.Table(fields)

    # --- Luau type-annotation skipping ------------------------------------
    def _skip_generics(self) -> None:
        if not self._is_sym("<"):
            return
        depth = 0
        while self.cur.type != "EOF":
            if self._is_sym("<"):
                depth += 1
            elif self._is_sym(">"):
                depth -= 1
                self._advance()
                if depth == 0:
                    return
                continue
            elif self._is_sym(">>"):
                depth -= 2
                self._advance()
                if depth <= 0:
                    return
                continue
            self._advance()
        raise ParseError("unterminated generic parameter list")

    def _skip_return_type(self) -> None:
        if self._is_sym(":"):
            self._advance()
            self._skip_type_expr()

    def _skip_type_annotation(self) -> None:
        if self._is_sym(":"):
            self._advance()
            self._skip_type_expr()

    STOP_TYPE = {",", "=", ")", "]", "}", ";"}

    def _skip_type_expr(self) -> None:
        """Consume a Luau type expression via bracket balancing."""
        depth = 0
        while self.cur.type != "EOF":
            if self.cur.type == "SYMBOL":
                v = self.cur.value
                if v in ("(", "[", "{", "<"):
                    depth += 1
                elif v in (")", "]", "}", ">"):
                    if depth == 0 and v in self.STOP_TYPE:
                        return
                    depth -= 1
                    if depth < 0:
                        return
                elif depth == 0 and v in self.STOP_TYPE:
                    return
            elif self.cur.type == "KEYWORD":
                # A statement keyword at depth 0 ends the type.
                if depth == 0 and self.cur.value not in ("nil", "true", "false"):
                    return
            self._advance()


def parse(src: str) -> A.Block:
    return Parser(tokenize(src)).parse_chunk()
