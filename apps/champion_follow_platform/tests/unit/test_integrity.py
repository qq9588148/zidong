from dataclasses import replace

from champion_follow.domain.integrity import IssueEvent, evaluate_issue


def ev(
    key,
    kind,
    *,
    actor="a" * 64,
    position=1,
    direction="大",
    amount=100,
    time=100,
    reported_complete=None,
    reported_reasons=None,
):
    return IssueEvent(
        event_key=key,
        kind=kind,
        actor_key=actor,
        issue="2607270001",
        position=position,
        direction=direction,
        amount_fen=amount,
        source_ms=time,
        result_digits=None,
        reported_complete=reported_complete,
        reported_reasons=reported_reasons,
    )


def result(time=300):
    return IssueEvent(
        event_key="r",
        kind="result",
        actor_key=None,
        issue="2607270001",
        position=None,
        direction=None,
        amount_fen=None,
        source_ms=time,
        result_digits=(5, 2, 1, 0, 9),
        reported_complete=None,
        reported_reasons=None,
    )


def marker(key, kind, time, *, complete=None, reasons=None):
    return replace(
        result(time),
        event_key=key,
        kind=kind,
        result_digits=None,
        reported_complete=complete,
        reported_reasons=reasons,
    )


def close(time=250):
    return marker("c", "close", time)


def test_identified_cancel_is_applied_before_testing_opposite_directions():
    evaluation = evaluate_issue(
        "2607270001",
        [
            ev("b1", "bet", direction="大", time=100),
            ev("x", "cancel", direction="大", time=150),
            ev("b2", "bet", direction="小", time=180),
            close(),
            result(),
        ],
        unresolved_gap=False,
    )

    assert evaluation.complete
    assert [(row.market, row.direction) for row in evaluation.predictions] == [
        ("P1:size", "小")
    ]
    assert evaluation.predictions[0].outcome == -1


def test_opposite_net_after_cancel_is_an_integrity_failure():
    evaluation = evaluate_issue(
        "2607270001",
        [
            ev("b1", "bet", direction="大"),
            ev("b2", "bet", direction="小"),
            close(),
            result(),
        ],
        unresolved_gap=False,
    )

    assert not evaluation.complete
    assert evaluation.predictions == ()
    assert "opposing_net" in evaluation.reasons


def test_unattributed_cancel_and_gap_exclude_the_whole_issue():
    evaluation = evaluate_issue(
        "2607270001",
        [
            ev("b1", "bet"),
            marker("u", "unattributed_cancel", 150),
            close(),
            result(),
        ],
        unresolved_gap=True,
    )

    assert not evaluation.complete
    assert set(evaluation.reasons) == {"unattributed_cancel", "capture_gap"}


def test_persisted_collector_gap_is_sticky_but_true_status_does_not_override_it():
    evaluation = evaluate_issue(
        "2607270001",
        [
            ev("b1", "bet"),
            marker("g", "capture_gap", 140),
            marker("s", "issue_status", 160, complete=True, reasons=()),
            close(),
            result(),
        ],
        unresolved_gap=False,
    )

    assert not evaluation.complete
    assert evaluation.reasons == ("capture_gap",)


def test_false_status_is_sticky_and_merges_allowlisted_reasons():
    evaluation = evaluate_issue(
        "2607270001",
        [
            ev("b1", "bet"),
            marker(
                "s1",
                "issue_status",
                140,
                complete=False,
                reasons=("decrypt_failed",),
            ),
            marker("s2", "issue_status", 160, complete=True, reasons=()),
            close(),
            result(),
        ],
        unresolved_gap=False,
    )

    assert not evaluation.complete
    assert set(evaluation.reasons) == {"reported_incomplete", "decrypt_failed"}


def test_same_direction_additions_become_one_prediction():
    evaluation = evaluate_issue(
        "2607270001",
        [
            ev("b1", "bet", time=100),
            ev("b2", "bet", amount=200, time=120),
            close(),
            result(),
        ],
        unresolved_gap=False,
    )

    assert len(evaluation.predictions) == 1
    assert evaluation.predictions[0].signal_source_ms == 120


def test_over_cancel_missing_close_and_result_order_fail_closed():
    over_cancel = evaluate_issue(
        "2607270001",
        [ev("b", "bet"), ev("x", "cancel", amount=101), close(), result()],
        unresolved_gap=False,
    )
    missing_close = evaluate_issue(
        "2607270001",
        [ev("b", "bet"), result()],
        unresolved_gap=False,
    )
    result_before_close = evaluate_issue(
        "2607270001",
        [ev("b", "bet"), result(200), close(250)],
        unresolved_gap=False,
    )

    assert "over_cancel" in over_cancel.reasons
    assert "missing_close" in missing_close.reasons
    assert "result_before_close" in result_before_close.reasons
    assert result_before_close.result_ms is None
    assert result_before_close.result_digits is None


def test_money_after_close_is_rejected_before_negative_lead_can_be_persisted():
    evaluation = evaluate_issue(
        "2607270001",
        [close(250), ev("b", "bet", time=260), result(300)],
        unresolved_gap=False,
    )

    assert not evaluation.complete
    assert "money_after_close" in evaluation.reasons
    assert evaluation.predictions == ()
