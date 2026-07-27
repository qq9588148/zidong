from champion_follow.domain.profiles import ProfileState, classify_level


def test_recent_window_is_capped_and_conservative_rate_uses_the_lower_window():
    state = ProfileState.empty()
    for _ in range(50):
        state = state.observe(1)
    for _ in range(90):
        state = state.observe(1)
    for _ in range(110):
        state = state.observe(-1)
    metrics = state.metrics()
    assert metrics.sample_count == 250
    assert len(state.recent_outcomes) == 200
    assert metrics.conservative_win_rate == metrics.recent_wilson_lower
    assert metrics.conservative_win_rate < metrics.all_wilson_lower


def test_blind_follow_tracks_equity_peak_and_max_drawdown_without_reset():
    state = ProfileState.empty()
    state = state.observe_blind(1).observe_blind(1).observe_blind(-1)
    assert state.blind_count == 3
    assert state.blind_profit_micros == 920000
    assert state.blind_max_drawdown_micros == 1000000
    assert state.blind_peak_micros == 1920000


def test_levels_are_global_and_require_both_sample_and_blind_profit_gates():
    assert classify_level(29, 100, 1) == "observed"
    assert classify_level(30, 0, 0) == "candidate"
    assert classify_level(200, 50, 1) == "formal"
    assert classify_level(200, 50, 0) == "candidate"
    assert classify_level(500, 200, 1) == "core"
    assert classify_level(500, 200, 0) == "candidate"
