CREATE TABLE identity_namespaces (
    id UUID PRIMARY KEY,
    version VARCHAR(64) NOT NULL UNIQUE,
    mode VARCHAR(16) NOT NULL CHECK (mode IN ('active','baseline')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX one_active_identity_namespace
    ON identity_namespaces ((mode)) WHERE mode='active';

CREATE TABLE anonymous_actors (
    namespace_id UUID NOT NULL REFERENCES identity_namespaces(id),
    actor_key CHAR(64) NOT NULL CHECK (actor_key ~ '^[0-9a-f]{64}$'),
    display_no BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    first_seen_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (namespace_id,actor_key)
);

CREATE TABLE collectors (
    id UUID PRIMARY KEY,
    namespace_id UUID NOT NULL REFERENCES identity_namespaces(id),
    wire_id VARCHAR(80) NOT NULL UNIQUE
        CHECK (wire_id ~ '^collector-[a-z0-9-]{3,64}$'),
    label VARCHAR(64) NOT NULL UNIQUE CHECK (label ~ '^[a-z0-9][a-z0-9._-]{2,63}$'),
    parser_version VARCHAR(64) NOT NULL,
    bearer_sha256 CHAR(64) NOT NULL UNIQUE CHECK (bearer_sha256 ~ '^[0-9a-f]{64}$'),
    ack_sequence BIGINT NOT NULL DEFAULT 0 CHECK (ack_sequence >= 0),
    ack_event_key VARCHAR(80)
        CHECK (ack_event_key IS NULL OR ack_event_key ~ '^[0-9a-f]{64}(:(block|close|[0-9]{1,15}))?$'),
    history_anchor_event_key VARCHAR(80)
        CHECK (
            history_anchor_event_key IS NULL
            OR history_anchor_event_key ~ '^[0-9a-f]{64}(:(block|close|[0-9]{1,15}))?$'
        ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (namespace_id,id),
    CHECK (
        (ack_sequence=0 AND ack_event_key IS NULL)
        OR (ack_sequence>=1 AND ack_event_key IS NOT NULL)
    )
);

CREATE TABLE collector_heartbeats (
    collector_id UUID PRIMARY KEY REFERENCES collectors(id) ON DELETE CASCADE,
    issue VARCHAR(16) CHECK (issue IS NULL OR issue ~ '^[0-9]{8,16}$'),
    phase VARCHAR(16) NOT NULL CHECK (phase IN ('BETTING','CLOSED','UNKNOWN')),
    countdown_ms BIGINT NOT NULL CHECK (countdown_ms >= 0),
    observed_at_ms BIGINT NOT NULL CHECK (observed_at_ms >= 0),
    last_journal_sequence BIGINT NOT NULL CHECK (last_journal_sequence >= 0),
    capture_healthy BOOLEAN NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE import_batches (
    id UUID PRIMARY KEY,
    namespace_id UUID NOT NULL REFERENCES identity_namespaces(id),
    partition VARCHAR(16) NOT NULL CHECK (partition IN ('current','baseline')),
    source_label VARCHAR(128) NOT NULL,
    source_sha256 CHAR(64) NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    parser_version VARCHAR(64) NOT NULL,
    row_count BIGINT NOT NULL CHECK (row_count >= 0),
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (namespace_id,source_sha256),
    UNIQUE (namespace_id,id,partition)
);

CREATE TABLE game_issues (
    issue VARCHAR(16) PRIMARY KEY CHECK (issue ~ '^[0-9]{8,16}$'),
    issue_no NUMERIC(16,0) NOT NULL UNIQUE,
    CHECK (issue_no = issue::NUMERIC)
);

CREATE TABLE issue_evaluations (
    namespace_id UUID NOT NULL REFERENCES identity_namespaces(id),
    issue VARCHAR(16) NOT NULL REFERENCES game_issues(issue),
    closed_ms BIGINT,
    result_ms BIGINT,
    result_digits SMALLINT[],
    integrity_status VARCHAR(16) NOT NULL DEFAULT 'pending'
        CHECK (integrity_status IN ('pending','complete','incomplete','processed')),
    integrity_reasons TEXT[] NOT NULL DEFAULT '{}',
    integrity_version VARCHAR(64),
    processed_at TIMESTAMPTZ,
    PRIMARY KEY (namespace_id,issue),
    CHECK (closed_ms IS NULL OR closed_ms >= 0),
    CHECK (result_ms IS NULL OR result_ms >= 0),
    CHECK (closed_ms IS NULL OR result_ms IS NULL OR result_ms >= closed_ms),
    CHECK (
        result_digits IS NULL OR (
            array_ndims(result_digits)=1
            AND cardinality(result_digits)=5
            AND array_position(result_digits,NULL) IS NULL
            AND result_digits <@ ARRAY[0,1,2,3,4,5,6,7,8,9]::SMALLINT[]
        )
    ),
    CHECK (
        COALESCE(array_ndims(integrity_reasons),1)=1
        AND cardinality(integrity_reasons) <= 16
        AND array_position(integrity_reasons,NULL) IS NULL
        AND integrity_reasons::TEXT
            ~ '^\{([a-z0-9_]+(,[a-z0-9_]+)*)?\}$'
    ),
    CHECK (
        (integrity_status='pending'
            AND closed_ms IS NULL AND result_ms IS NULL AND result_digits IS NULL
            AND cardinality(integrity_reasons)=0 AND integrity_version IS NULL
            AND processed_at IS NULL)
        OR
        (integrity_status='incomplete'
            AND cardinality(integrity_reasons)>=1 AND integrity_version IS NOT NULL
            AND processed_at IS NULL)
        OR
        (integrity_status='complete'
            AND closed_ms IS NOT NULL AND result_ms IS NOT NULL AND result_digits IS NOT NULL
            AND cardinality(integrity_reasons)=0 AND integrity_version IS NOT NULL
            AND processed_at IS NULL)
        OR
        (integrity_status='processed'
            AND closed_ms IS NOT NULL AND result_ms IS NOT NULL AND result_digits IS NOT NULL
            AND cardinality(integrity_reasons)=0 AND integrity_version IS NOT NULL
            AND processed_at IS NOT NULL)
    )
);

CREATE TABLE source_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    namespace_id UUID NOT NULL REFERENCES identity_namespaces(id),
    partition VARCHAR(16) NOT NULL CHECK (partition IN ('current','baseline')),
    collector_id UUID,
    import_batch_id UUID,
    stream_sequence BIGINT CHECK (stream_sequence IS NULL OR stream_sequence >= 1),
    event_key VARCHAR(80) NOT NULL
        CHECK (event_key ~ '^[0-9a-f]{64}(:(block|close|[0-9]{1,15}))?$'),
    payload_sha256 CHAR(64) NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    actor_key CHAR(64),
    issue VARCHAR(16) NOT NULL REFERENCES game_issues(issue),
    kind VARCHAR(24) NOT NULL
        CHECK (kind IN (
            'bet','cancel','unattributed_cancel','close','result','capture_gap','issue_status'
        )),
    history_anchor_event_key VARCHAR(80) GENERATED ALWAYS AS (
        CASE WHEN partition='current' AND kind IN ('bet','cancel') THEN event_key END
    ) STORED,
    source_ms BIGINT NOT NULL CHECK (source_ms >= 0),
    received_at TIMESTAMPTZ NOT NULL,
    position SMALLINT CHECK (position BETWEEN 1 AND 5),
    direction VARCHAR(4) CHECK (direction IN ('大','小','单','双','质','合')),
    amount_fen BIGINT CHECK (amount_fen > 0),
    result_digits SMALLINT[],
    gap_reason VARCHAR(64) CHECK (gap_reason IS NULL OR gap_reason ~ '^[a-z0-9_]+$'),
    reported_complete BOOLEAN,
    reported_reasons TEXT[],
    parser_version VARCHAR(64) NOT NULL,
    source_label VARCHAR(128) NOT NULL,
    UNIQUE (namespace_id,event_key),
    UNIQUE (namespace_id,event_key,payload_sha256),
    UNIQUE (namespace_id,history_anchor_event_key),
    FOREIGN KEY (namespace_id,actor_key)
        REFERENCES anonymous_actors(namespace_id,actor_key),
    FOREIGN KEY (namespace_id,collector_id)
        REFERENCES collectors(namespace_id,id),
    FOREIGN KEY (namespace_id,import_batch_id,partition)
        REFERENCES import_batches(namespace_id,id,partition),
    FOREIGN KEY (namespace_id,issue)
        REFERENCES issue_evaluations(namespace_id,issue),
    CHECK (
        (collector_id IS NOT NULL AND import_batch_id IS NULL
            AND stream_sequence IS NOT NULL AND partition='current')
        OR
        (collector_id IS NULL AND import_batch_id IS NOT NULL AND stream_sequence IS NULL)
    ),
    CHECK (
        result_digits IS NULL OR (
            array_ndims(result_digits)=1
            AND cardinality(result_digits)=5
            AND array_position(result_digits,NULL) IS NULL
            AND result_digits <@ ARRAY[0,1,2,3,4,5,6,7,8,9]::SMALLINT[]
        )
    ),
    CHECK (
        (kind IN ('bet','cancel') AND actor_key IS NOT NULL AND position IS NOT NULL
            AND direction IS NOT NULL AND amount_fen IS NOT NULL AND result_digits IS NULL
            AND gap_reason IS NULL AND reported_complete IS NULL AND reported_reasons IS NULL)
        OR
        (kind IN ('unattributed_cancel','close') AND actor_key IS NULL AND position IS NULL
            AND direction IS NULL AND amount_fen IS NULL AND result_digits IS NULL
            AND gap_reason IS NULL AND reported_complete IS NULL AND reported_reasons IS NULL)
        OR
        (kind='result' AND actor_key IS NULL AND position IS NULL AND direction IS NULL
            AND amount_fen IS NULL AND result_digits IS NOT NULL
            AND gap_reason IS NULL AND reported_complete IS NULL AND reported_reasons IS NULL)
        OR
        (kind='capture_gap' AND actor_key IS NULL AND position IS NULL AND direction IS NULL
            AND amount_fen IS NULL AND result_digits IS NULL AND gap_reason IS NOT NULL
            AND reported_complete IS NULL AND reported_reasons IS NULL)
        OR
        (kind='issue_status' AND actor_key IS NULL AND position IS NULL AND direction IS NULL
            AND amount_fen IS NULL AND result_digits IS NULL AND gap_reason IS NULL
            AND reported_complete IS NOT NULL AND reported_reasons IS NOT NULL
            AND COALESCE(array_ndims(reported_reasons),1)=1
            AND cardinality(reported_reasons) <= 16
            AND array_position(reported_reasons,NULL) IS NULL
            AND reported_reasons::TEXT
                ~ '^\{([a-z0-9_]+(,[a-z0-9_]+)*)?\}$'
            AND (
                (reported_complete AND cardinality(reported_reasons)=0)
                OR (NOT reported_complete AND cardinality(reported_reasons)>=1)
            ))
    )
);
CREATE UNIQUE INDEX source_stream_sequence_once
    ON source_events(collector_id,stream_sequence) WHERE collector_id IS NOT NULL;

CREATE TABLE collector_event_receipts (
    namespace_id UUID NOT NULL,
    collector_id UUID NOT NULL,
    stream_sequence BIGINT NOT NULL CHECK (stream_sequence >= 1),
    event_key VARCHAR(80) NOT NULL
        CHECK (event_key ~ '^[0-9a-f]{64}(:(block|close|[0-9]{1,15}))?$'),
    payload_sha256 CHAR(64) NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    wire_sha256 CHAR(64) CHECK (wire_sha256 IS NULL OR wire_sha256 ~ '^[0-9a-f]{64}$'),
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (collector_id,stream_sequence),
    UNIQUE (collector_id,stream_sequence,event_key),
    FOREIGN KEY (namespace_id,collector_id)
        REFERENCES collectors(namespace_id,id),
    FOREIGN KEY (namespace_id,event_key,payload_sha256)
        REFERENCES source_events(namespace_id,event_key,payload_sha256)
);
ALTER TABLE collectors
    ADD CONSTRAINT collector_ack_references_receipt
    FOREIGN KEY (id,ack_sequence,ack_event_key)
    REFERENCES collector_event_receipts(collector_id,stream_sequence,event_key)
    DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE collectors
    ADD CONSTRAINT collector_history_anchor_references_source_event
    FOREIGN KEY (namespace_id,history_anchor_event_key)
    REFERENCES source_events(namespace_id,history_anchor_event_key);
CREATE INDEX source_issue_order ON source_events(namespace_id,issue,source_ms,event_key);
CREATE INDEX source_actor_order ON source_events(namespace_id,actor_key,issue,source_ms);

CREATE TABLE capture_gaps (
    id UUID PRIMARY KEY,
    collector_id UUID NOT NULL REFERENCES collectors(id),
    from_sequence BIGINT NOT NULL CHECK (from_sequence >= 1),
    to_sequence BIGINT NOT NULL CHECK (to_sequence >= from_sequence),
    affected_issue VARCHAR(16) NOT NULL REFERENCES game_issues(issue),
    reason VARCHAR(64) NOT NULL CHECK (reason ~ '^[a-z0-9_]+$'),
    opened_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    recovered_at TIMESTAMPTZ,
    CHECK (recovered_at IS NULL OR recovered_at >= opened_at),
    UNIQUE (collector_id,from_sequence,to_sequence)
);

CREATE TABLE prediction_samples (
    id UUID PRIMARY KEY,
    namespace_id UUID NOT NULL REFERENCES identity_namespaces(id),
    actor_key CHAR(64) NOT NULL,
    issue VARCHAR(16) NOT NULL REFERENCES game_issues(issue),
    market VARCHAR(32) NOT NULL
        CHECK (market ~ '^P[1-5]:(size|parity|prime_composite)$'),
    direction VARCHAR(4) NOT NULL CHECK (direction IN ('大','小','单','双','质','合')),
    signal_source_ms BIGINT NOT NULL CHECK (signal_source_ms >= 0),
    lead_ms BIGINT NOT NULL CHECK (lead_ms >= 0),
    outcome SMALLINT NOT NULL CHECK (outcome IN (-1,0,1)),
    unit_profit_micros INTEGER NOT NULL CHECK (unit_profit_micros IN (-1000000,0,960000)),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (namespace_id,actor_key,issue,market),
    FOREIGN KEY (namespace_id,actor_key)
        REFERENCES anonymous_actors(namespace_id,actor_key),
    FOREIGN KEY (namespace_id,issue)
        REFERENCES issue_evaluations(namespace_id,issue),
    CHECK (
        (market ~ '^P[1-5]:size$' AND direction IN ('大','小'))
        OR (market ~ '^P[1-5]:parity$' AND direction IN ('单','双'))
        OR (market ~ '^P[1-5]:prime_composite$' AND direction IN ('质','合'))
    ),
    CHECK (
        (outcome=1 AND unit_profit_micros=960000)
        OR (outcome=0 AND unit_profit_micros=0)
        OR (outcome=-1 AND unit_profit_micros=-1000000)
    )
);
CREATE INDEX prediction_market_issue ON prediction_samples(market,issue);

CREATE TABLE actor_profiles (
    namespace_id UUID NOT NULL REFERENCES identity_namespaces(id),
    actor_key CHAR(64) NOT NULL,
    scope VARCHAR(32) NOT NULL
        CHECK (scope='overall' OR scope ~ '^P[1-5]:(size|parity|prime_composite)$'),
    sample_count BIGINT NOT NULL DEFAULT 0,
    wins BIGINT NOT NULL DEFAULT 0,
    losses BIGINT NOT NULL DEFAULT 0,
    pushes BIGINT NOT NULL DEFAULT 0,
    recent_outcomes SMALLINT[] NOT NULL DEFAULT '{}',
    raw_win_rate NUMERIC(18,12) NOT NULL DEFAULT 0,
    all_wilson_lower NUMERIC(18,12) NOT NULL DEFAULT 0,
    recent_wilson_lower NUMERIC(18,12) NOT NULL DEFAULT 0,
    conservative_win_rate NUMERIC(18,12) NOT NULL DEFAULT 0,
    unit_return NUMERIC(18,12) NOT NULL DEFAULT 0,
    conservative_unit_return NUMERIC(18,12) NOT NULL DEFAULT -1,
    blind_count BIGINT NOT NULL DEFAULT 0,
    blind_wins BIGINT NOT NULL DEFAULT 0,
    blind_losses BIGINT NOT NULL DEFAULT 0,
    blind_profit_micros BIGINT NOT NULL DEFAULT 0,
    blind_peak_micros BIGINT NOT NULL DEFAULT 0,
    blind_max_drawdown_micros BIGINT NOT NULL DEFAULT 0,
    level VARCHAR(16) NOT NULL DEFAULT 'observed'
        CHECK (level IN ('observed','candidate','formal','core')),
    first_seen_at TIMESTAMPTZ,
    last_seen_at TIMESTAMPTZ,
    statistics_version VARCHAR(64) NOT NULL,
    updated_through_issue VARCHAR(16),
    PRIMARY KEY (namespace_id,actor_key,scope),
    FOREIGN KEY (namespace_id,actor_key)
        REFERENCES anonymous_actors(namespace_id,actor_key),
    FOREIGN KEY (namespace_id,updated_through_issue)
        REFERENCES issue_evaluations(namespace_id,issue),
    CHECK (sample_count >= 0 AND wins >= 0 AND losses >= 0 AND pushes >= 0),
    CHECK (sample_count = wins + losses + pushes),
    CHECK (
        COALESCE(array_ndims(recent_outcomes),1)=1
        AND cardinality(recent_outcomes) = LEAST(sample_count,200)
        AND array_position(recent_outcomes,NULL) IS NULL
        AND recent_outcomes <@ ARRAY[-1,0,1]::SMALLINT[]
    ),
    CHECK (
        raw_win_rate BETWEEN 0 AND 1
        AND all_wilson_lower BETWEEN 0 AND 1
        AND recent_wilson_lower BETWEEN 0 AND 1
        AND conservative_win_rate BETWEEN 0 AND 1
        AND all_wilson_lower <= raw_win_rate
        AND conservative_win_rate <= raw_win_rate
    ),
    CHECK (
        unit_return BETWEEN -1 AND 0.96
        AND conservative_unit_return BETWEEN -1 AND 0.96
    ),
    CHECK (
        raw_win_rate = CASE
            WHEN wins + losses = 0 THEN 0
            ELSE round(wins::NUMERIC / (wins + losses),12)
        END
    ),
    CHECK (
        unit_return = CASE
            WHEN wins + losses = 0 THEN 0
            ELSE round((wins::NUMERIC * 0.96 - losses) / (wins + losses),12)
        END
    ),
    CHECK (
        conservative_win_rate = CASE
            WHEN sample_count < 50 THEN all_wilson_lower
            ELSE LEAST(all_wilson_lower,recent_wilson_lower)
        END
        AND conservative_unit_return = round(1.96 * conservative_win_rate - 1,12)
    ),
    CHECK (
        blind_count >= 0 AND blind_wins >= 0 AND blind_losses >= 0
        AND blind_wins + blind_losses <= blind_count
    ),
    CHECK (blind_profit_micros = blind_wins * 960000 - blind_losses * 1000000),
    CHECK (
        blind_peak_micros >= 0
        AND blind_peak_micros >= blind_profit_micros
        AND blind_max_drawdown_micros >= 0
        AND blind_max_drawdown_micros >= blind_peak_micros - blind_profit_micros
    ),
    CHECK (
        (first_seen_at IS NULL AND last_seen_at IS NULL)
        OR (first_seen_at IS NOT NULL AND last_seen_at IS NOT NULL
            AND last_seen_at >= first_seen_at)
    ),
    CHECK (
        scope<>'overall' OR (
            (level='observed' AND sample_count < 30)
            OR (
                level='candidate' AND sample_count >= 30
                AND NOT (sample_count >= 200 AND blind_count >= 50
                    AND blind_profit_micros > 0)
            )
            OR (
                level='formal' AND sample_count >= 200 AND blind_count >= 50
                AND blind_profit_micros > 0
                AND NOT (sample_count >= 500 AND blind_count >= 200)
            )
            OR (
                level='core' AND sample_count >= 500 AND blind_count >= 200
                AND blind_profit_micros > 0
            )
        )
    )
);
CREATE INDEX profile_market_rank
    ON actor_profiles(scope,conservative_unit_return DESC,sample_count DESC,actor_key);

CREATE TABLE ranking_snapshots (
    id UUID PRIMARY KEY,
    namespace_id UUID NOT NULL REFERENCES identity_namespaces(id),
    issue VARCHAR(16) NOT NULL REFERENCES game_issues(issue),
    scope VARCHAR(32) NOT NULL
        CHECK (scope='overall' OR scope ~ '^P[1-5]:(size|parity|prime_composite)$'),
    frozen_at TIMESTAMPTZ NOT NULL,
    statistics_version VARCHAR(64) NOT NULL,
    manifest_sha256 CHAR(64) NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (namespace_id,issue,scope),
    UNIQUE (namespace_id,id),
    UNIQUE (namespace_id,id,scope),
    UNIQUE (namespace_id,id,issue,scope,frozen_at,statistics_version),
    FOREIGN KEY (namespace_id,issue)
        REFERENCES issue_evaluations(namespace_id,issue)
);

CREATE TABLE ranking_entries (
    namespace_id UUID NOT NULL REFERENCES identity_namespaces(id),
    snapshot_id UUID NOT NULL,
    actor_key CHAR(64) NOT NULL,
    rank INTEGER NOT NULL CHECK (rank >= 1),
    sample_count BIGINT NOT NULL,
    wins BIGINT NOT NULL,
    losses BIGINT NOT NULL,
    pushes BIGINT NOT NULL,
    raw_win_rate NUMERIC(18,12) NOT NULL,
    all_wilson_lower NUMERIC(18,12) NOT NULL,
    recent_wilson_lower NUMERIC(18,12) NOT NULL,
    conservative_win_rate NUMERIC(18,12) NOT NULL,
    unit_return NUMERIC(18,12) NOT NULL,
    conservative_unit_return NUMERIC(18,12) NOT NULL,
    blind_count BIGINT NOT NULL,
    blind_profit_micros BIGINT NOT NULL,
    blind_max_drawdown_micros BIGINT NOT NULL,
    level VARCHAR(16) NOT NULL
        CHECK (level IN ('observed','candidate','formal','core')),
    PRIMARY KEY (snapshot_id,actor_key),
    UNIQUE (snapshot_id,rank),
    UNIQUE (namespace_id,snapshot_id,actor_key,rank),
    FOREIGN KEY (namespace_id,snapshot_id)
        REFERENCES ranking_snapshots(namespace_id,id) ON DELETE CASCADE,
    FOREIGN KEY (namespace_id,actor_key)
        REFERENCES anonymous_actors(namespace_id,actor_key),
    CHECK (sample_count >= 0 AND wins >= 0 AND losses >= 0 AND pushes >= 0),
    CHECK (sample_count = wins + losses + pushes),
    CHECK (
        raw_win_rate BETWEEN 0 AND 1
        AND all_wilson_lower BETWEEN 0 AND 1
        AND recent_wilson_lower BETWEEN 0 AND 1
        AND conservative_win_rate BETWEEN 0 AND 1
        AND all_wilson_lower <= raw_win_rate
        AND conservative_win_rate <= raw_win_rate
    ),
    CHECK (
        unit_return BETWEEN -1 AND 0.96
        AND conservative_unit_return BETWEEN -1 AND 0.96
    ),
    CHECK (
        raw_win_rate = CASE
            WHEN wins + losses = 0 THEN 0
            ELSE round(wins::NUMERIC / (wins + losses),12)
        END
    ),
    CHECK (
        unit_return = CASE
            WHEN wins + losses = 0 THEN 0
            ELSE round((wins::NUMERIC * 0.96 - losses) / (wins + losses),12)
        END
    ),
    CHECK (
        conservative_win_rate = CASE
            WHEN sample_count < 50 THEN all_wilson_lower
            ELSE LEAST(all_wilson_lower,recent_wilson_lower)
        END
        AND conservative_unit_return = round(1.96 * conservative_win_rate - 1,12)
    ),
    CHECK (blind_count >= 0 AND blind_max_drawdown_micros >= 0)
);

CREATE TABLE asof_candidates (
    id UUID PRIMARY KEY,
    namespace_id UUID NOT NULL REFERENCES identity_namespaces(id),
    snapshot_id UUID NOT NULL,
    issue VARCHAR(16) NOT NULL REFERENCES game_issues(issue),
    market VARCHAR(32) NOT NULL
        CHECK (market ~ '^P[1-5]:(size|parity|prime_composite)$'),
    actor_key CHAR(64) NOT NULL,
    direction VARCHAR(4) NOT NULL CHECK (direction IN ('大','小','单','双','质','合')),
    signal_source_ms BIGINT NOT NULL CHECK (signal_source_ms >= 0),
    lead_ms BIGINT NOT NULL CHECK (lead_ms >= 0),
    prior_lead_times_ms BIGINT[] NOT NULL,
    profile_level VARCHAR(16) NOT NULL
        CHECK (profile_level IN ('observed','candidate','formal','core')),
    profile_sample_count BIGINT NOT NULL,
    profile_wins BIGINT NOT NULL,
    profile_losses BIGINT NOT NULL,
    profile_raw_win_rate NUMERIC(18,12) NOT NULL,
    profile_conservative_win_rate NUMERIC(18,12) NOT NULL,
    profile_conservative_unit_return NUMERIC(18,12) NOT NULL,
    base_rank INTEGER NOT NULL CHECK (base_rank >= 1),
    statistics_version VARCHAR(64) NOT NULL,
    frozen_at TIMESTAMPTZ NOT NULL,
    outcome SMALLINT CHECK (outcome IN (-1,0,1)),
    unit_profit_micros INTEGER CHECK (unit_profit_micros IN (-1000000,0,960000)),
    settled_at TIMESTAMPTZ,
    UNIQUE (namespace_id,issue,market,actor_key),
    FOREIGN KEY (namespace_id,actor_key)
        REFERENCES anonymous_actors(namespace_id,actor_key),
    FOREIGN KEY (
        namespace_id,snapshot_id,issue,market,frozen_at,statistics_version
    ) REFERENCES ranking_snapshots(
        namespace_id,id,issue,scope,frozen_at,statistics_version
    ),
    FOREIGN KEY (namespace_id,snapshot_id,actor_key,base_rank)
        REFERENCES ranking_entries(namespace_id,snapshot_id,actor_key,rank),
    CHECK (
        (market ~ '^P[1-5]:size$' AND direction IN ('大','小'))
        OR (market ~ '^P[1-5]:parity$' AND direction IN ('单','双'))
        OR (market ~ '^P[1-5]:prime_composite$' AND direction IN ('质','合'))
    ),
    CHECK (
        COALESCE(array_ndims(prior_lead_times_ms),1)=1
        AND array_position(prior_lead_times_ms,NULL) IS NULL
        AND 0 <= ALL(prior_lead_times_ms)
        AND cardinality(prior_lead_times_ms) <= profile_sample_count
    ),
    CHECK (
        profile_sample_count >= 0 AND profile_wins >= 0 AND profile_losses >= 0
        AND profile_wins + profile_losses <= profile_sample_count
    ),
    CHECK (
        profile_raw_win_rate BETWEEN 0 AND 1
        AND profile_conservative_win_rate BETWEEN 0 AND 1
        AND profile_conservative_win_rate <= profile_raw_win_rate
        AND profile_conservative_unit_return BETWEEN -1 AND 0.96
    ),
    CHECK (
        profile_raw_win_rate = CASE
            WHEN profile_wins + profile_losses = 0 THEN 0
            ELSE round(profile_wins::NUMERIC / (profile_wins + profile_losses),12)
        END
        AND profile_conservative_unit_return =
            round(1.96 * profile_conservative_win_rate - 1,12)
    ),
    CHECK (
        (outcome IS NULL AND unit_profit_micros IS NULL AND settled_at IS NULL)
        OR (
            outcome IS NOT NULL AND unit_profit_micros IS NOT NULL AND settled_at IS NOT NULL
            AND settled_at >= frozen_at
            AND (
                (outcome=1 AND unit_profit_micros=960000)
                OR (outcome=0 AND unit_profit_micros=0)
                OR (outcome=-1 AND unit_profit_micros=-1000000)
            )
        )
    )
);
CREATE INDEX asof_window ON asof_candidates(frozen_at,issue,market);

CREATE TABLE processing_state (
    namespace_id UUID PRIMARY KEY REFERENCES identity_namespaces(id),
    last_issue_no NUMERIC(16,0) NOT NULL DEFAULT 0 CHECK (last_issue_no >= 0),
    last_issue VARCHAR(16),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (namespace_id,last_issue)
        REFERENCES issue_evaluations(namespace_id,issue),
    CHECK (
        (last_issue_no=0 AND last_issue IS NULL)
        OR (last_issue IS NOT NULL AND last_issue_no=last_issue::NUMERIC)
    )
);

CREATE TABLE threshold_previews (
    id UUID PRIMARY KEY,
    namespace_id UUID NOT NULL REFERENCES identity_namespaces(id),
    request_sha256 CHAR(64) NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    safe_lead_ms BIGINT NOT NULL CHECK (safe_lead_ms >= 0),
    request_config JSONB NOT NULL CHECK (jsonb_typeof(request_config)='object'),
    as_of TIMESTAMPTZ NOT NULL,
    watermark_snapshot_id UUID NOT NULL,
    watermark_scope VARCHAR(7) GENERATED ALWAYS AS ('overall') STORED,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (namespace_id,request_sha256),
    FOREIGN KEY (namespace_id,watermark_snapshot_id,watermark_scope)
        REFERENCES ranking_snapshots(namespace_id,id,scope)
);

CREATE TABLE threshold_preview_windows (
    preview_id UUID NOT NULL REFERENCES threshold_previews(id) ON DELETE CASCADE,
    window_days SMALLINT NOT NULL CHECK (window_days IN (7,30)),
    frozen_signal_count BIGINT NOT NULL CHECK (frozen_signal_count >= 0),
    executable_signal_count BIGINT NOT NULL CHECK (
        executable_signal_count >= 0 AND executable_signal_count <= frozen_signal_count
    ),
    win_count BIGINT NOT NULL CHECK (win_count >= 0),
    loss_count BIGINT NOT NULL CHECK (loss_count >= 0),
    unit_profit_micros BIGINT NOT NULL,
    raw_win_rate NUMERIC(18,12) NOT NULL CHECK (raw_win_rate BETWEEN 0 AND 1),
    conservative_win_rate NUMERIC(18,12) NOT NULL
        CHECK (conservative_win_rate BETWEEN 0 AND 1),
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (preview_id,window_days),
    CHECK (win_count + loss_count <= executable_signal_count),
    CHECK (unit_profit_micros = win_count * 960000 - loss_count * 1000000),
    CHECK (
        raw_win_rate = CASE
            WHEN win_count + loss_count = 0 THEN 0
            ELSE round(win_count::NUMERIC / (win_count + loss_count),12)
        END
    ),
    CHECK (conservative_win_rate <= raw_win_rate),
    CHECK (window_end > window_start)
);
