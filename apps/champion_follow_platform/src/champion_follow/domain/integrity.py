import re
from dataclasses import dataclass

from .markets import DIRECTION_FAMILY, Direction, settle_direction


SAFE_REASON = re.compile(r"^[a-z0-9_]+$")
MONEY_KINDS = {"bet", "cancel"}
RESULT_CLOSE_CLOCK_SKEW_MS = 250


@dataclass(frozen=True)
class IssueEvent:
    event_key: str
    kind: str
    actor_key: str | None
    issue: str
    position: int | None
    direction: str | None
    amount_fen: int | None
    source_ms: int
    result_digits: tuple[int, ...] | None
    reported_complete: bool | None = None
    reported_reasons: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Prediction:
    actor_key: str
    market: str
    direction: str
    signal_source_ms: int
    outcome: int
    unit_profit_micros: int


@dataclass(frozen=True)
class IssueEvaluation:
    issue: str
    complete: bool
    reasons: tuple[str, ...]
    closed_ms: int | None
    result_ms: int | None
    result_digits: tuple[int, ...] | None
    predictions: tuple[Prediction, ...]


def _valid_result(value):
    return (
        isinstance(value, tuple)
        and len(value) == 5
        and all(type(digit) is int and 0 <= digit <= 9 for digit in value)
    )


def evaluate_issue(issue, events, *, unresolved_gap):
    ordered = sorted(events, key=lambda event: (event.source_ms, event.event_key))
    reasons = set()
    balances = {}
    last_changes = {}
    closes = []
    results = []

    if unresolved_gap:
        reasons.add("capture_gap")

    for event in ordered:
        if event.issue != issue:
            reasons.add("issue_mismatch")
            continue
        if event.kind in MONEY_KINDS:
            try:
                direction = Direction(event.direction)
                family = DIRECTION_FAMILY[direction]
            except (KeyError, ValueError):
                reasons.add("invalid_money")
                continue
            if (
                event.actor_key is None
                or event.position not in range(1, 6)
                or type(event.amount_fen) is not int
                or event.amount_fen <= 0
            ):
                reasons.add("invalid_money")
                continue
            market = f"P{event.position}:{family.value}"
            key = (event.actor_key, market, direction.value)
            current = balances.get(key, 0)
            if event.kind == "cancel":
                if event.amount_fen > current:
                    reasons.add("over_cancel")
                    continue
                balances[key] = current - event.amount_fen
            else:
                balances[key] = current + event.amount_fen
            last_changes[(event.actor_key, market)] = event.source_ms
        elif event.kind == "unattributed_cancel":
            reasons.add("unattributed_cancel")
        elif event.kind == "capture_gap":
            reasons.add("capture_gap")
        elif event.kind == "close":
            closes.append(event)
        elif event.kind == "result":
            results.append(event)
        elif event.kind == "issue_status":
            if event.reported_complete is False:
                reasons.add("reported_incomplete")
                reasons.update(
                    reason
                    for reason in event.reported_reasons or ()
                    if type(reason) is str and SAFE_REASON.fullmatch(reason)
                )
        else:
            reasons.add("unsupported_event")

    closed_ms = closes[0].source_ms if len(closes) == 1 else None
    result_event = results[0] if len(results) == 1 else None
    result_ms = result_event.source_ms if result_event is not None else None
    result_digits = result_event.result_digits if result_event is not None else None

    if not closes:
        reasons.add("missing_close")
    elif len(closes) > 1:
        reasons.add("multiple_close")
    if not results:
        reasons.add("missing_result")
    elif len(results) > 1:
        reasons.add("multiple_result")
    elif not _valid_result(result_digits):
        reasons.add("invalid_result")
    if closed_ms is not None and any(
        event.issue == issue
        and event.kind in MONEY_KINDS
        and event.source_ms > closed_ms
        for event in ordered
    ):
        reasons.add("money_after_close")
    if closed_ms is not None and result_ms is not None and result_ms < closed_ms:
        money_after_result = any(
            event.issue == issue
            and event.kind in MONEY_KINDS
            and event.source_ms > result_ms
            for event in ordered
        )
        if (
            closed_ms - result_ms <= RESULT_CLOSE_CLOCK_SKEW_MS
            and not money_after_result
        ):
            closed_ms = result_ms
        else:
            reasons.add("result_before_close")
            result_ms = None
            result_digits = None

    selected = []
    by_actor_market = {}
    for (actor_key, market, direction), amount in balances.items():
        if amount > 0:
            by_actor_market.setdefault((actor_key, market), []).append(direction)
    for (actor_key, market), directions in by_actor_market.items():
        if len(directions) > 1:
            reasons.add("opposing_net")
        else:
            selected.append(
                (actor_key, market, directions[0], last_changes[(actor_key, market)])
            )

    if reasons:
        return IssueEvaluation(
            issue=issue,
            complete=False,
            reasons=tuple(sorted(reasons)),
            closed_ms=closed_ms,
            result_ms=result_ms,
            result_digits=result_digits if _valid_result(result_digits) else None,
            predictions=(),
        )

    predictions = []
    for actor_key, market, direction, signal_source_ms in sorted(selected):
        position_text, family_text = market.split(":", 1)
        position = int(position_text[1:])
        family = DIRECTION_FAMILY[Direction(direction)]
        actual = settle_direction(result_digits[position - 1], family).value
        outcome = 1 if direction == actual else -1
        predictions.append(
            Prediction(
                actor_key=actor_key,
                market=f"P{position}:{family_text}",
                direction=direction,
                signal_source_ms=signal_source_ms,
                outcome=outcome,
                unit_profit_micros=960000 if outcome == 1 else -1000000,
            )
        )
    return IssueEvaluation(
        issue=issue,
        complete=True,
        reasons=(),
        closed_ms=closed_ms,
        result_ms=result_ms,
        result_digits=result_digits,
        predictions=tuple(predictions),
    )
