"""Seeds and a portable random generator shared by the minigames."""

import secrets

MASK32 = 0xFFFFFFFF


class Rng:
    """mulberry32, so courses don't depend on Python's random module across versions."""

    def __init__(self, seed: int):
        self.state = seed & MASK32

    def next(self) -> int:
        self.state = (self.state + 0x6D2B79F5) & MASK32
        z = self.state
        z = ((z ^ (z >> 15)) * (z | 1)) & MASK32
        z ^= (z + (((z ^ (z >> 7)) * (z | 61)) & MASK32)) & MASK32
        return (z ^ (z >> 14)) & MASK32

    def below(self, n: int) -> int:
        return self.next() % n

    def choice(self, items):
        return items[self.below(len(items))]


def new_seed() -> int:
    return secrets.randbits(32)
