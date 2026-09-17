"""Per-build opcode / symbol maps with aliasing.

Each operation is assigned SEVERAL distinct random numbers (aliases). The
compiler picks one at random for every emitted node, so the same operation
appears under different numbers throughout the bytecode — defeating frequency
analysis and one-to-one opcode mapping. Serialization tags and signal kinds get
a single number. ``free`` holds numbers assigned to nothing, for decoy handlers.
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
TAGS = ["T_arr", "T_key"]
STAGS = ["ST_int", "ST_arr", "ST_nil"]

ALIASABLE = EXPR_OPS + STMT_OPS + BINOPS + UNOPS + TAGS


class OpMap:
    def __init__(self, seed: int | None = None):
        rng = random.Random(seed)
        pool = list(range(1, 512))       # 9-bit space, plenty of room
        rng.shuffle(pool)
        it = iter(pool)

        def take(k):
            return [next(it) for _ in range(k)]

        self.aliases: dict[str, list[int]] = {}
        used: set[int] = set()
        for name in ALIASABLE:
            vals = take(rng.randint(2, 3))
            self.aliases[name] = vals
            used.update(vals)
        # Serialization tags are written as raw bytes, so keep them <=255 and
        # distinct from each other (they live in a different read context, so
        # overlap with opcode numbers is harmless).
        byte_pool = [n for n in range(1, 256) if n not in used]
        rng.shuffle(byte_pool)
        for i, name in enumerate(STAGS):
            self.aliases[name] = [byte_pool[i]]
        self.map = {n: v[0] for n, v in self.aliases.items()}
        self.free = [n for n in list(it) if n <= 255]
        rng.shuffle(self.free)

    def all(self, name: str) -> list[int]:
        return self.aliases[name]

    def pick(self, name: str, rng: random.Random) -> int:
        return rng.choice(self.aliases[name])

    def __getitem__(self, name: str) -> int:
        return self.map[name]
