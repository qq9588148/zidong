from dataclasses import dataclass, replace
from decimal import Decimal

from .statistics import conservative_unit_return, fixed_unit_return, wilson_lower


def classify_level(sample_count: int, blind_count: int, blind_profit_micros: int) -> str:
    profitable = blind_count > 0 and blind_profit_micros > 0
    if sample_count >= 500 and blind_count >= 200 and profitable:
        return "core"
    if sample_count >= 200 and blind_count >= 50 and profitable:
        return "formal"
    if sample_count >= 30:
        return "candidate"
    return "observed"


@dataclass(frozen=True)
class ProfileMetrics:
    sample_count: int
    wins: int
    losses: int
    pushes: int
    raw_win_rate: Decimal
    all_wilson_lower: Decimal
    recent_wilson_lower: Decimal
    conservative_win_rate: Decimal
    unit_return: Decimal
    conservative_unit_return: Decimal


@dataclass(frozen=True)
class ProfileState:
    sample_count: int
    wins: int
    losses: int
    pushes: int
    recent_outcomes: tuple[int, ...]
    blind_count: int
    blind_wins: int
    blind_losses: int
    blind_profit_micros: int
    blind_peak_micros: int
    blind_max_drawdown_micros: int

    @classmethod
    def empty(cls):
        return cls(0, 0, 0, 0, (), 0, 0, 0, 0, 0, 0)

    def observe(self, outcome: int):
        if outcome not in (-1, 0, 1):
            raise ValueError("outcome must be -1, 0 or 1")
        return replace(
            self,
            sample_count=self.sample_count + 1,
            wins=self.wins + (outcome == 1),
            losses=self.losses + (outcome == -1),
            pushes=self.pushes + (outcome == 0),
            recent_outcomes=(*self.recent_outcomes, outcome)[-200:],
        )

    def observe_blind(self, outcome: int):
        if outcome not in (-1, 0, 1):
            raise ValueError("outcome must be -1, 0 or 1")
        profit = {1: 960000, -1: -1000000, 0: 0}[outcome]
        equity = self.blind_profit_micros + profit
        peak = max(self.blind_peak_micros, equity)
        drawdown = peak - equity
        return replace(
            self,
            blind_count=self.blind_count + 1,
            blind_wins=self.blind_wins + (outcome == 1),
            blind_losses=self.blind_losses + (outcome == -1),
            blind_profit_micros=equity,
            blind_peak_micros=peak,
            blind_max_drawdown_micros=max(self.blind_max_drawdown_micros, drawdown),
        )

    def metrics(self) -> ProfileMetrics:
        decisive = self.wins + self.losses
        recent_wins = sum(value == 1 for value in self.recent_outcomes)
        recent_decisive = sum(value != 0 for value in self.recent_outcomes)
        all_lower = wilson_lower(self.wins, decisive)
        recent_lower = wilson_lower(recent_wins, recent_decisive)
        conservative = min(all_lower, recent_lower) if self.sample_count >= 50 else all_lower
        raw = Decimal(self.wins) / decisive if decisive else Decimal(0)
        return ProfileMetrics(
            self.sample_count,
            self.wins,
            self.losses,
            self.pushes,
            raw,
            all_lower,
            recent_lower,
            conservative,
            fixed_unit_return(self.wins, self.losses),
            conservative_unit_return(conservative),
        )

    @property
    def level(self) -> str:
        return classify_level(self.sample_count, self.blind_count, self.blind_profit_micros)
