import re
from dataclasses import dataclass
from enum import StrEnum


class Direction(StrEnum):
    BIG = "大"
    SMALL = "小"
    ODD = "单"
    EVEN = "双"
    PRIME = "质"
    COMPOSITE = "合"


class MarketFamily(StrEnum):
    SIZE = "size"
    PARITY = "parity"
    PRIME_COMPOSITE = "prime_composite"


FAMILY_DIRECTIONS = {
    MarketFamily.SIZE: (Direction.BIG, Direction.SMALL),
    MarketFamily.PARITY: (Direction.ODD, Direction.EVEN),
    MarketFamily.PRIME_COMPOSITE: (Direction.PRIME, Direction.COMPOSITE),
}
DIRECTION_FAMILY = {
    direction: family
    for family, directions in FAMILY_DIRECTIONS.items()
    for direction in directions
}
OPPOSITE = {
    Direction.BIG: Direction.SMALL,
    Direction.SMALL: Direction.BIG,
    Direction.ODD: Direction.EVEN,
    Direction.EVEN: Direction.ODD,
    Direction.PRIME: Direction.COMPOSITE,
    Direction.COMPOSITE: Direction.PRIME,
}
ALL_MARKETS = tuple(
    f"P{position}:{family.value}"
    for position in range(1, 6)
    for family in MarketFamily
)
PLAY = re.compile(r"P([1-5]):(大|小|单|双|质|合)")


@dataclass(frozen=True)
class ParsedPlay:
    position: int
    direction: Direction
    family: MarketFamily

    @property
    def market(self) -> str:
        return f"P{self.position}:{self.family.value}"

    @property
    def play(self) -> str:
        return f"P{self.position}:{self.direction.value}"


def parse_play(value: str) -> ParsedPlay:
    if type(value) is not str:
        raise ValueError("unsupported play")
    match = PLAY.fullmatch(value)
    if match is None:
        raise ValueError("unsupported play")
    position = int(match.group(1))
    direction = Direction(match.group(2))
    return ParsedPlay(position, direction, DIRECTION_FAMILY[direction])


def settle_direction(digit: int, family: MarketFamily) -> Direction:
    if type(digit) is not int:
        raise TypeError("digit must be an integer")
    if not 0 <= digit <= 9:
        raise ValueError("digit out of range")
    if not isinstance(family, MarketFamily):
        raise TypeError("family must be a MarketFamily")
    if family is MarketFamily.SIZE:
        return Direction.BIG if digit >= 5 else Direction.SMALL
    if family is MarketFamily.PARITY:
        return Direction.ODD if digit % 2 else Direction.EVEN
    if family is MarketFamily.PRIME_COMPOSITE:
        return Direction.PRIME if digit in {1, 2, 3, 5, 7} else Direction.COMPOSITE
    raise ValueError("unsupported family")
