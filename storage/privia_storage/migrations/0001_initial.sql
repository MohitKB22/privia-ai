-- PRIVIA initial schema.
-- Every table is local to the user's machine. No secret material is stored in
-- plaintext here: credentials live in the OS keychain or the encrypted secrets
-- file managed by privia_security.secrets.

CREATE TABLE users (
    id              TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL DEFAULT 'You',
    locale          TEXT NOT NULL DEFAULT 'en',
    timezone        TEXT NOT NULL DEFAULT 'UTC',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE sessions (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title           TEXT NOT NULL DEFAULT 'New conversation',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    ended_at        TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_sessions_user_updated ON sessions(user_id, updated_at DESC);

CREATE TABLE messages (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    run_id          TEXT,
    role            TEXT NOT NULL CHECK (role IN ('user','assistant','system','tool')),
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    metadata_json   TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_messages_session_created ON messages(session_id, created_at);
CREATE INDEX idx_messages_run ON messages(run_id);

CREATE TABLE runs (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    request_id          TEXT NOT NULL,
    input_text          TEXT NOT NULL,
    intent              TEXT NOT NULL DEFAULT 'unknown',
    status              TEXT NOT NULL DEFAULT 'pending',
    phase               TEXT NOT NULL DEFAULT 'input',
    processing_location TEXT NOT NULL DEFAULT 'local',
    model_used          TEXT,
    response_text       TEXT NOT NULL DEFAULT '',
    duration_ms         INTEGER NOT NULL DEFAULT 0,
    error_code          TEXT,
    error               TEXT,
    run_json            TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL
);
CREATE INDEX idx_runs_session_created ON runs(session_id, created_at DESC);
CREATE INDEX idx_runs_request ON runs(request_id);

CREATE TABLE tool_calls (
    id                    TEXT PRIMARY KEY,
    run_id                TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    session_id            TEXT NOT NULL,
    tool_name             TEXT NOT NULL,
    arguments_json        TEXT NOT NULL DEFAULT '{}',
    risk_level            TEXT NOT NULL DEFAULT 'low',
    justification         TEXT NOT NULL DEFAULT '',
    requires_confirmation INTEGER NOT NULL DEFAULT 0,
    created_at            TEXT NOT NULL
);
CREATE INDEX idx_tool_calls_run ON tool_calls(run_id);
CREATE INDEX idx_tool_calls_tool ON tool_calls(tool_name, created_at DESC);

CREATE TABLE tool_results (
    id              TEXT PRIMARY KEY,
    call_id         TEXT NOT NULL REFERENCES tool_calls(id) ON DELETE CASCADE,
    run_id          TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    success         INTEGER NOT NULL,
    data_json       TEXT,
    error           TEXT,
    error_code      TEXT,
    duration_ms     INTEGER NOT NULL DEFAULT 0,
    truncated       INTEGER NOT NULL DEFAULT 0,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL
);
CREATE INDEX idx_tool_results_call ON tool_results(call_id);
CREATE INDEX idx_tool_results_run ON tool_results(run_id);

CREATE TABLE permissions (
    id              TEXT PRIMARY KEY,
    session_id      TEXT,                     -- NULL means a persistent grant
    scope           TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'not_requested',
    resources_json  TEXT NOT NULL DEFAULT '[]',
    session_only    INTEGER NOT NULL DEFAULT 0,
    granted_at      TEXT,
    expires_at      TEXT,
    note            TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_permissions_scope ON permissions(scope, IFNULL(session_id,''));

CREATE TABLE memories (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    content         TEXT NOT NULL,
    tags_json       TEXT NOT NULL DEFAULT '[]',
    provenance      TEXT NOT NULL DEFAULT 'user:explicit',
    session_id      TEXT,
    pinned          INTEGER NOT NULL DEFAULT 0,
    use_count       INTEGER NOT NULL DEFAULT 0,
    last_used_at    TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX idx_memories_kind ON memories(kind, updated_at DESC);
CREATE INDEX idx_memories_session ON memories(session_id);

CREATE TABLE memory_vectors (
    memory_id       TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    model           TEXT NOT NULL,
    dim             INTEGER NOT NULL,
    vector_blob     BLOB NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE notes (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    body            TEXT NOT NULL DEFAULT '',
    tags_json       TEXT NOT NULL DEFAULT '[]',
    pinned          INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX idx_notes_updated ON notes(updated_at DESC);

CREATE VIRTUAL TABLE notes_fts USING fts5(
    title, body, content='notes', content_rowid='rowid'
);

CREATE TABLE email_drafts (
    id              TEXT PRIMARY KEY,
    to_json         TEXT NOT NULL DEFAULT '[]',
    cc_json         TEXT NOT NULL DEFAULT '[]',
    bcc_json        TEXT NOT NULL DEFAULT '[]',
    subject         TEXT NOT NULL DEFAULT '',
    body            TEXT NOT NULL DEFAULT '',
    in_reply_to     TEXT,
    attachments_json TEXT NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL DEFAULT 'draft',
    sent_at         TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX idx_drafts_status ON email_drafts(status, updated_at DESC);

CREATE TABLE integrations (
    name            TEXT PRIMARY KEY,
    family          TEXT NOT NULL,
    provider        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'not_configured',
    authenticated   INTEGER NOT NULL DEFAULT 0,
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    detail          TEXT NOT NULL DEFAULT '',
    checked_at      TEXT,
    config_json     TEXT NOT NULL DEFAULT '{}'   -- non-secret config only
);

CREATE TABLE audit_events (
    id              TEXT PRIMARY KEY,
    timestamp       TEXT NOT NULL,
    action          TEXT NOT NULL,
    session_id      TEXT,
    run_id          TEXT,
    request_id      TEXT,
    actor           TEXT NOT NULL DEFAULT 'user',
    tool_name       TEXT,
    target          TEXT,
    outcome         TEXT NOT NULL DEFAULT 'success',
    detail_json     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_audit_timestamp ON audit_events(timestamp DESC);
CREATE INDEX idx_audit_action ON audit_events(action, timestamp DESC);
CREATE INDEX idx_audit_run ON audit_events(run_id);

CREATE TABLE settings (
    key             TEXT PRIMARY KEY,
    value_json      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE confirmations (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    session_id      TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    resolved        INTEGER NOT NULL DEFAULT 0,
    approved        INTEGER,
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    resolved_at     TEXT
);
CREATE INDEX idx_confirmations_session ON confirmations(session_id, resolved);
