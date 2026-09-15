"""AST node definitions for the Lua subset the parser understands.

Nodes are intentionally lightweight dataclasses. The generator walks them to
emit source and the transforms mutate/replace them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


class Node:
    pass


# --- expressions -----------------------------------------------------------
@dataclass
class Nil(Node):
    pass


@dataclass
class TrueLit(Node):
    pass


@dataclass
class FalseLit(Node):
    pass


@dataclass
class Vararg(Node):
    pass


@dataclass
class Number(Node):
    raw: str


@dataclass
class String(Node):
    value: str
    style: str = '"'


@dataclass
class Raw(Node):
    """An opaque source fragment kept verbatim (e.g. interpolated strings)."""
    text: str


@dataclass
class Name(Node):
    name: str
    # Filled in by the resolver: True if this name refers to a local binding.
    is_local: bool = False


@dataclass
class Index(Node):
    obj: Node
    key: Node
    # dot=True means obj.key syntax, otherwise obj[key]
    dot: bool = False


@dataclass
class Call(Node):
    func: Node
    args: List[Node]


@dataclass
class MethodCall(Node):
    obj: Node
    method: str
    args: List[Node]


@dataclass
class BinOp(Node):
    op: str
    left: Node
    right: Node


@dataclass
class UnOp(Node):
    op: str
    operand: Node


@dataclass
class TableField(Node):
    # key is None for array-style entries, a String for name-style, or any
    # expression for [expr]=value style.
    key: Optional[Node]
    value: Node
    bracketed: bool = False


@dataclass
class Table(Node):
    fields: List[TableField] = field(default_factory=list)


@dataclass
class FunctionExpr(Node):
    params: List[str]
    is_vararg: bool
    body: "Block"


@dataclass
class Paren(Node):
    expr: Node


# --- statements ------------------------------------------------------------
@dataclass
class LocalAssign(Node):
    names: List[str]
    exprs: List[Node]
    attribs: List[Optional[str]] = field(default_factory=list)


@dataclass
class Assign(Node):
    targets: List[Node]
    exprs: List[Node]


@dataclass
class CallStat(Node):
    call: Node


@dataclass
class Do(Node):
    body: "Block"


@dataclass
class While(Node):
    cond: Node
    body: "Block"


@dataclass
class Repeat(Node):
    body: "Block"
    cond: Node


@dataclass
class If(Node):
    # list of (condition, block); the final else has condition None.
    clauses: List[tuple]


@dataclass
class NumericFor(Node):
    var: str
    start: Node
    stop: Node
    step: Optional[Node]
    body: "Block"


@dataclass
class GenericFor(Node):
    names: List[str]
    exprs: List[Node]
    body: "Block"


@dataclass
class FunctionDecl(Node):
    # target is the name path (Name or Index chain); method True for a:b()
    target: Node
    is_method: bool
    func: FunctionExpr
    is_local: bool = False
    local_name: Optional[str] = None


@dataclass
class Return(Node):
    exprs: List[Node]


@dataclass
class Break(Node):
    pass


@dataclass
class Continue(Node):
    pass


@dataclass
class Goto(Node):
    label: str


@dataclass
class Label(Node):
    name: str


@dataclass
class RawStat(Node):
    """A statement kept verbatim (used when the parser skips constructs)."""
    text: str


@dataclass
class Block(Node):
    stmts: List[Node] = field(default_factory=list)
