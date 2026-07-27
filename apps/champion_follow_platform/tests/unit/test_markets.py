import pytest

from champion_follow.domain.markets import (
    ALL_MARKETS,
    Direction,
    MarketFamily,
    parse_play,
    settle_direction,
)


def test_first_version_has_exactly_fifteen_markets_and_thirty_plays():
    assert len(ALL_MARKETS) == 15
    plays = {
        f"P{position}:{direction.value}"
        for position in range(1, 6)
        for direction in Direction
    }
    assert len(plays) == 30
    assert parse_play("P5:合").market == "P5:prime_composite"


@pytest.mark.parametrize(
    ("digit", "family", "expected"),
    [
        (0, MarketFamily.SIZE, Direction.SMALL),
        (5, MarketFamily.SIZE, Direction.BIG),
        (8, MarketFamily.PARITY, Direction.EVEN),
        (9, MarketFamily.PARITY, Direction.ODD),
        (1, MarketFamily.PRIME_COMPOSITE, Direction.PRIME),
        (4, MarketFamily.PRIME_COMPOSITE, Direction.COMPOSITE),
    ],
)
def test_settlement_matches_the_frozen_ffc_contract(digit, family, expected):
    assert settle_direction(digit, family) == expected


@pytest.mark.parametrize("play", ["P0:大", "P6:小", "P1:数字", "P1:龙", "P1:和", "P1:big"])
def test_out_of_scope_plays_are_rejected(play):
    with pytest.raises(ValueError, match="unsupported play"):
        parse_play(play)


class StringablePlay:
    def __str__(self):
        return "P1:大"


@pytest.mark.parametrize("play", ["P１:大", StringablePlay(), "P1:大小"])
def test_parse_play_requires_an_exact_supported_string(play):
    with pytest.raises(ValueError, match="unsupported play"):
        parse_play(play)


@pytest.mark.parametrize("digit", [True, "5", 5.0])
def test_settlement_requires_an_exact_integer_digit(digit):
    with pytest.raises((TypeError, ValueError), match="digit"):
        settle_direction(digit, MarketFamily.SIZE)


@pytest.mark.parametrize("family", ["size", Direction.BIG, None])
def test_settlement_requires_an_actual_market_family(family):
    with pytest.raises((TypeError, ValueError), match="family"):
        settle_direction(5, family)
