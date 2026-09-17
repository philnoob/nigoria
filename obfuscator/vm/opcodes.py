"""Per-build opcode / symbol maps for the VM.

Every symbol the VM uses (expression ops, statement ops, binary/unary operator
codes, table-field tags, serialization type tags) is assigned a distinct random
integer per build. The compiler emits these numbers and the emitter generates a
matching interpreter, so the bytecode of one build is meaningless to a
devirtualizer written for another.
"""

from __future__ import annotations

import random

EXPR_OPS = [
    "EK", "EKNUM", "ENIL", "ETRUE", "EFALSE", "EVARARG", "ELOCAL", "EGLOBAL",
    "EINDEX", "ECALL", "EMETHOD", "EBIN", "EUN", "EFUNC", "ETABLE", "EPAREN",
]
STMT_OPS = [
    "SLOCAL", "SASSIGN", "SCALL", "SIF", "SWHILE", "SREPEAT", "SNUMFOR",
    "SGENFOR", "SRETURN", "SBREAK", "SCONTINUE", "SDO",
]
BINOPS = [
    "B_add", "B_sub", "B_mul", "B_div", "B_mod", "B_pow", "B_idiv", "B_concat",
    "B_eq", "B_ne", "B_lt", "B_le", "B_gt", "B_ge", "B_and", "B_or",
    "B_band", "B_bor", "B_bxor", "B_shl", "B_shr",
]
UNOPS = ["U_neg", "U_not", "U_len", "U_bnot"]
TAGS = ["T_arr", "T_key"]              # table-field kinds
STAGS = ["ST_int", "ST_arr", "ST_nil"]  # serialization value tags

ALL_SYMBOLS = EXPR_OPS + STMT_OPS + BINOPS + UNOPS + TAGS + STAGS


class OpMap:
    def __init__(self, seed: int | None = None):
        rng = random.Random(seed)
        # distinct random values across every symbol
        pool = list(range(1, 256))
        rng.shuffle(pool)
        if len(ALL_SYMBOLS) > len(pool):  # pragma: no cover - impossible today
            raise RuntimeError("not enough opcode space")
        self.map = {name: pool[i] for i, name in enumerate(ALL_SYMBOLS)}

    def __getitem__(self, name: str) -> int:
        return self.map[name]
