"""A tokenizer for Lua 5.1 with the common Luau extensions.

The lexer is deliberately forgiving: constructs it does not understand (for
example some Luau type-annotation syntax) are still emitted as generic tokens
so the parser can skip over them, and the pipeline can always fall back to
source-level packing if a full parse is not possible.
"""

from __future__ import annotations

from dataclasses import dataclass


KEYWORDS = {
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function",
    "goto", "if", "in", "local", "nil", "not", "or", "repeat", "return",
    "then", "true", "until", "while",
    # Luau soft keywords are treated as names except where the parser expects
    # them; keeping them out of KEYWORDS keeps `type` usable as an identifier.
}

# Multi-character symbols, longest first so the scanner is greedy.
SYMBOLS = [
    "...", "..=", "//=", "<<=", ">>=",
    "==", "~=", "<=", ">=", "..", "::",
    "+=", "-=", "*=", "/=", "%=", "^=", "//", "<<", ">>",
    "+", "-", "*", "/", "%", "^", "#", "&", "~", "|", "<", ">", "=",
    "(", ")", "{", "}", "[", "]", ";", ":", ",", ".",
]


@dataclass
class Token:
    type: str        # NAME, NUMBER, STRING, KEYWORD, SYMBOL, EOF
    value: str       # raw source text of the token
    line: int
    col: int
    # For strings we keep the decoded value and how it was quoted so the
    # generator can round-trip or re-encode it precisely.
    string_value: str | None = None
    string_style: str | None = None  # '"', "'", or "[[" style marker

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Token({self.type}, {self.value!r}, {self.line}:{self.col})"


class LexError(Exception):
    def __init__(self, message: str, line: int, col: int):
        super().__init__(f"{message} at line {line}, col {col}")
        self.line = line
        self.col = col


class Lexer:
    def __init__(self, src: str):
        self.src = src
        self.n = len(src)
        self.i = 0
        self.line = 1
        self.col = 1

    # --- low level helpers -------------------------------------------------
    def _peek(self, off: int = 0) -> str:
        j = self.i + off
        return self.src[j] if j < self.n else ""

    def _advance(self) -> str:
        ch = self.src[self.i]
        self.i += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def _startswith(self, s: str) -> bool:
        return self.src.startswith(s, self.i)

    # --- long-bracket handling (strings and comments) ----------------------
    def _match_long_bracket_open(self) -> int | None:
        """If positioned at a long bracket ``[==[`` return its level, else None."""
        if self._peek() != "[":
            return None
        j = self.i + 1
        level = 0
        while j < self.n and self.src[j] == "=":
            level += 1
            j += 1
        if j < self.n and self.src[j] == "[":
            return level
        return None

    def _read_long_bracket(self, level: int) -> str:
        # consume opening [==[
        self._advance()  # [
        for _ in range(level):
            self._advance()  # =
        self._advance()  # [
        # A newline immediately after the opening bracket is skipped in Lua.
        if self._peek() == "\r":
            self._advance()
        if self._peek() == "\n":
            self._advance()
        close = "]" + "=" * level + "]"
        buf = []
        while self.i < self.n:
            if self._startswith(close):
                for _ in range(len(close)):
                    self._advance()
                return "".join(buf)
            buf.append(self._advance())
        raise LexError("unterminated long bracket", self.line, self.col)

    # --- string handling ---------------------------------------------------
    def _read_quoted_string(self) -> Token:
        line, col = self.line, self.col
        quote = self._advance()
        raw = [quote]
        value = []
        while self.i < self.n:
            ch = self._peek()
            if ch == "\\":
                raw.append(self._advance())  # backslash
                esc = self._peek()
                raw.append(self._advance())
                value.append(self._decode_escape(esc, raw, value))
            elif ch == quote:
                raw.append(self._advance())
                return Token("STRING", "".join(raw), line, col,
                             string_value="".join(value), string_style=quote)
            elif ch == "\n":
                raise LexError("unterminated string", line, col)
            else:
                raw.append(self._advance())
                value.append(ch)
        raise LexError("unterminated string", line, col)

    def _decode_escape(self, esc: str, raw: list, value: list) -> str:
        simple = {
            "n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b",
            "f": "\f", "v": "\v", "\\": "\\", '"': '"', "'": "'", "\n": "\n",
        }
        if esc in simple:
            return simple[esc]
        if esc == "x":  # \xHH
            h = ""
            for _ in range(2):
                if self._peek() in "0123456789abcdefABCDEF":
                    c = self._advance(); raw.append(c); h += c
            return chr(int(h, 16)) if h else "x"
        if esc == "z":  # \z skips following whitespace
            while self._peek() in " \t\r\n":
                raw.append(self._advance())
            return ""
        if esc.isdigit():  # \ddd decimal
            d = esc
            for _ in range(2):
                if self._peek().isdigit():
                    c = self._advance(); raw.append(c); d += c
            return chr(int(d) & 0xFF)
        return esc  # unknown escape: keep the char

    # --- number handling ---------------------------------------------------
    def _read_number(self) -> Token:
        line, col = self.line, self.col
        start = self.i
        if self._peek() == "0" and self._peek(1) in ("x", "X"):
            self._advance(); self._advance()
            while self._peek() in "0123456789abcdefABCDEF.pP+-_":
                # stop sign consumption unless it directly follows p/P
                if self._peek() in "+-" and self.src[self.i - 1] not in "pP":
                    break
                self._advance()
        else:
            while self._peek() in "0123456789.eE+-_xXbBoO":
                if self._peek() in "+-" and self.src[self.i - 1] not in "eE":
                    break
                self._advance()
        text = self.src[start:self.i]
        return Token("NUMBER", text, line, col)

    # --- identifiers -------------------------------------------------------
    def _read_name(self) -> Token:
        line, col = self.line, self.col
        start = self.i
        while self._peek().isalnum() or self._peek() == "_":
            self._advance()
        text = self.src[start:self.i]
        ttype = "KEYWORD" if text in KEYWORDS else "NAME"
        return Token(ttype, text, line, col)

    # --- top level scan ----------------------------------------------------
    def tokenize(self) -> list[Token]:
        tokens: list[Token] = []
        # Skip a shebang line if present (some scripts start with #!).
        if self.src.startswith("#"):
            while self.i < self.n and self._peek() != "\n":
                self._advance()
        while self.i < self.n:
            ch = self._peek()
            if ch in " \t\r\n":
                self._advance()
                continue
            # comments
            if ch == "-" and self._peek(1) == "-":
                self._advance(); self._advance()
                lvl = self._match_long_bracket_open()
                if lvl is not None:
                    self._read_long_bracket(lvl)
                else:
                    while self.i < self.n and self._peek() != "\n":
                        self._advance()
                continue
            # long string
            lvl = self._match_long_bracket_open()
            if lvl is not None:
                line, col = self.line, self.col
                text = self._read_long_bracket(lvl)
                tokens.append(Token("STRING", None, line, col,
                                    string_value=text,
                                    string_style="[" + "=" * lvl + "["))
                continue
            if ch in "\"'":
                tokens.append(self._read_quoted_string())
                continue
            if ch == "`":  # Luau interpolated string: keep as raw string token
                tokens.append(self._read_interp_string())
                continue
            if ch.isdigit() or (ch == "." and self._peek(1).isdigit()):
                tokens.append(self._read_number())
                continue
            if ch.isalpha() or ch == "_":
                tokens.append(self._read_name())
                continue
            matched = None
            for sym in SYMBOLS:
                if self._startswith(sym):
                    matched = sym
                    break
            if matched is None:
                raise LexError(f"unexpected character {ch!r}", self.line, self.col)
            line, col = self.line, self.col
            for _ in range(len(matched)):
                self._advance()
            tokens.append(Token("SYMBOL", matched, line, col))
        tokens.append(Token("EOF", "", self.line, self.col))
        return tokens

    def _read_interp_string(self) -> Token:
        """Read a Luau backtick interpolated string as an opaque RAW token."""
        line, col = self.line, self.col
        raw = [self._advance()]  # `
        while self.i < self.n:
            ch = self._peek()
            if ch == "\\":
                raw.append(self._advance())
                if self.i < self.n:
                    raw.append(self._advance())
            elif ch == "`":
                raw.append(self._advance())
                break
            else:
                raw.append(self._advance())
        return Token("RAW", "".join(raw), line, col)


def tokenize(src: str) -> list[Token]:
    return Lexer(src).tokenize()
