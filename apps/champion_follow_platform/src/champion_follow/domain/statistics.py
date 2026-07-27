from decimal import Decimal, ROUND_HALF_UP, localcontext


STATISTICS_VERSION = "statistics-v1-z16448536269514722-recent200"
Z = Decimal("1.6448536269514722")
ODDS = Decimal("1.96")
BREAK_EVEN_RATE = Decimal(1) / ODDS
MICROS = Decimal(1_000_000)


def _quantize(value: Decimal) -> Decimal:
    # PostgreSQL round(numeric, scale) rounds ties away from zero.
    return value.quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_UP)


def wilson_lower(wins: int, decisive: int) -> Decimal:
    if decisive <= 0:
        return Decimal(0)
    with localcontext() as context:
        context.prec = 48
        n = Decimal(decisive)
        p = Decimal(wins) / n
        z2 = Z * Z
        variance = (p * (Decimal(1) - p) + z2 / (Decimal(4) * n)) / n
        lower = (
            p + z2 / (Decimal(2) * n) - Z * variance.sqrt()
        ) / (Decimal(1) + z2 / n)
        return _quantize(max(Decimal(0), min(Decimal(1), lower)))


def fixed_unit_return(wins: int, losses: int) -> Decimal:
    decisive = wins + losses
    if decisive <= 0:
        return Decimal(0)
    return _quantize((Decimal(wins) * Decimal("0.96") - Decimal(losses)) / decisive)


def conservative_unit_return(conservative_rate: Decimal) -> Decimal:
    return _quantize(ODDS * Decimal(conservative_rate) - Decimal(1))
