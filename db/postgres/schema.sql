CREATE TABLE IF NOT EXISTS pb_revision (
    id INTEGER PRIMARY KEY CHECK(id = 1), revision BIGINT NOT NULL,
    next_part_id BIGINT NOT NULL, next_thread_order BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS parts (
    id BIGINT PRIMARY KEY, part_category TEXT NOT NULL,
    profile TEXT NOT NULL CHECK(profile IN ('passive', 'discrete_ic')),
    value TEXT, package TEXT, part_number TEXT UNIQUE, quantity BIGINT NOT NULL,
    manufacturer TEXT, description TEXT, datasheet_url TEXT,
    passive_identity TEXT UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS part_field_provenance (
    part_id BIGINT NOT NULL REFERENCES parts(id), field_name TEXT NOT NULL,
    field_value TEXT, source_tier TEXT NOT NULL, source_kind TEXT NOT NULL,
    source_locator TEXT, extraction_method TEXT NOT NULL, confidence_marker TEXT,
    conflict_status TEXT NOT NULL, normalization_method TEXT, evidence TEXT,
    competing_candidates TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY(part_id, field_name)
);
CREATE TABLE IF NOT EXISTS part_pending_field_review (
    part_id BIGINT NOT NULL REFERENCES parts(id), field_name TEXT NOT NULL,
    proposed_value TEXT, provenance_json TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(part_id, field_name)
);
CREATE TABLE IF NOT EXISTS part_specifications (
    part_id BIGINT PRIMARY KEY REFERENCES parts(id), facts_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS part_specification_reviews (
    part_id BIGINT PRIMARY KEY REFERENCES parts(id), facts_json TEXT NOT NULL, snapshot_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_approvals (
    request_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL, snapshot_json TEXT NOT NULL,
    decision INTEGER CHECK(decision IN (0, 1)), result_json TEXT
);
CREATE TABLE IF NOT EXISTS agent_operations (
    operation_id TEXT PRIMARY KEY, tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL, result_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_executions (
    execution_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
    context_json TEXT NOT NULL, status TEXT NOT NULL,
    worker_id TEXT, lease_until DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_execution_approvals (
    request_id TEXT PRIMARY KEY, execution_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_threads (
    thread_id TEXT PRIMARY KEY, runtime TEXT NOT NULL CHECK(runtime IN ('openai', 'local', 'codex')),
    thread_order BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_events (
    thread_id TEXT NOT NULL REFERENCES agent_threads(thread_id), sequence BIGINT NOT NULL,
    kind TEXT NOT NULL, runtime TEXT NOT NULL, data_json TEXT NOT NULL, PRIMARY KEY(thread_id, sequence)
);
CREATE TABLE IF NOT EXISTS agent_event_keys (
    event_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, sequence BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS supplied_enrichment_cache (
    key TEXT PRIMARY KEY, expires DOUBLE PRECISION NOT NULL, lease_until DOUBLE PRECISION NOT NULL, result TEXT
);
CREATE TABLE IF NOT EXISTS pb_imports (
    source_digest TEXT PRIMARY KEY, counts_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pb_legacy_sessions (
    thread_id TEXT PRIMARY KEY, data_json TEXT NOT NULL
);
