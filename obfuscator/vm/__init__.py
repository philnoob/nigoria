"""Custom VM: compile Lua AST to a serialized program run by a Lua interpreter.

The point is that once the loader layers are peeled the attacker sees a generic
interpreter plus an opaque numeric program table and an encrypted constant pool
— not readable (if obfuscated) Lua statements. Compilation raises
``Unsupported`` for constructs the VM does not implement so the pipeline can
fall back to the plain-transform path and never break a script.
"""

from .compiler import Unsupported, compile_chunk
from .emitter import emit_vm
from .opcodes import OpMap

__all__ = ["Unsupported", "compile_chunk", "emit_vm", "OpMap"]
