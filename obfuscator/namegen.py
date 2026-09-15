"""Opaque identifier generation shared by transforms."""

from __future__ import annotations

import random


class NameGenerator:
    """Produces valid, globally-unique, hard-to-read Lua identifiers.

    Using globally-unique names (rather than per-scope) removes any chance of
    accidental variable capture when scopes are flattened or merged later.
    """

    # A confusing but valid character set. First char must not be a digit.
    _HEAD = "lI"
    _BODY = "lI1"

    def __init__(self, seed: int | None = None, prefix: str = ""):
        self.rng = random.Random(seed)
        self.counter = 0
        self.prefix = prefix
        self.used: set[str] = set()

    def new(self) -> str:
        while True:
            self.counter += 1
            length = 6 + (self.counter % 9)
            head = self.rng.choice(self._HEAD)
            body = "".join(self.rng.choice(self._BODY) for _ in range(length))
            # Interleave the counter in a hidden way to guarantee uniqueness
            # without printing digits that would break the l/I aesthetic.
            name = self.prefix + head + body + ("l" * (self.counter % 3))
            if name not in self.used:
                self.used.add(name)
                # Guarantee uniqueness even if random collides by appending a
                # deterministic suffix built from the counter in the same set.
                unique = name + self._encode_counter(self.counter)
                if unique not in self.used:
                    self.used.add(unique)
                    return unique

    def _encode_counter(self, n: int) -> str:
        digits = "lI"
        if n == 0:
            return "l"
        out = []
        while n:
            out.append(digits[n & 1])
            n >>= 1
        return "".join(reversed(out))


class PreambleNamer:
    """Names for runtime-helper locals, drawn from a charset the
    ``NameGenerator`` never uses (O/o/0/_/Z), guaranteeing no collision with
    renamed user locals."""

    _HEAD = "OoZ"
    _BODY = "Oo0_"

    def __init__(self, seed: int | None = None):
        import random as _r
        self.rng = _r.Random(seed)
        self.used: set[str] = set()
        self.counter = 0

    def new(self) -> str:
        while True:
            self.counter += 1
            length = 5 + (self.counter % 6)
            name = (self.rng.choice(self._HEAD)
                    + "".join(self.rng.choice(self._BODY) for _ in range(length)))
            n = self.counter
            tail = ""
            while n:
                tail += "O0"[n & 1]
                n >>= 1
            name += tail
            if name not in self.used:
                self.used.add(name)
                return name
