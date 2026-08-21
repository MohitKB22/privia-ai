-- Support the Activity screen, which filters recent runs by status.
CREATE INDEX IF NOT EXISTS idx_runs_status_created ON runs(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_pinned ON memories(pinned, updated_at DESC);
