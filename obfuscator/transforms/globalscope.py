"""Hardcore globals.

Every reference to a global (a Name the renamer did not mark ``is_local``) is
rewritten to an indexing of a captured environment table: ``print`` becomes
``ENV["print"]``. Combined with the string pass the key itself is encrypted, so
no readable global name survives in the output.

MUST run after renaming (so ``is_local`` is populated) and before the string
pass (so the emitted key strings get encrypted).
"""

from __future__ import annotations

from .. import ast_nodes as A
from ..walker import NodeTransformer


class GlobalHardener(NodeTransformer):
    def __init__(self, env_name: str):
        self.env_name = env_name
        self.used = False

    def _env(self) -> A.Name:
        n = A.Name(self.env_name)
        n.is_local = True  # already resolved: never re-wrap it
        return n

    def visit_Name(self, node: A.Name, **ctx) -> A.Node:
        if node.is_local:
            return node
        if node.name == self.env_name:
            return node
        self.used = True
        return A.Index(self._env(), A.String(node.name), dot=False)

    def visit_FunctionDecl(self, node: A.FunctionDecl, **ctx) -> A.Node:
        if node.is_local:
            node.func = self.visit(node.func)
            return node
        # Convert `function a.b()` / `function a:b()` into an assignment so the
        # global base name can be wrapped like any other global.
        func = self.visit(node.func)
        target = self.visit(node.target)
        return A.Assign([target], [func])


def harden_globals(block: A.Block, env_name: str):
    hardener = GlobalHardener(env_name)
    block = hardener.visit(block)
    if not hardener.used:
        return block, ""
    preamble = (
        f"local {env_name}=(getfenv and getfenv(0)) or _ENV or _G;"
    )
    return block, preamble
