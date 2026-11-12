-- ─────────────────────────────────────────────────────────────────────────────
-- Garmin Dashboard — PostgreSQL schema
-- Runs automatically on first `docker compose up` (via initdb.d).
-- Safe to re-run: all statements use IF NOT EXISTS / DO NOTHING.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS activities (
    id               SERIAL          PRIMARY KEY,

    -- Core activity fields (sourced from Garmin CSV via Claude tool-use)
    activity_date    DATE            NOT NULL,
    activity_type    VARCHAR(50)     NOT NULL,
    duration_min     NUMERIC(7, 2)   NOT NULL,           -- e.g. 62.23
    distance_km      NUMERIC(8, 3)   NOT NULL DEFAULT 0, -- 0 for gym/yoga
    avg_heart_rate   INTEGER,                            -- NULL if not recorded
    max_heart_rate   INTEGER,
    calories         INTEGER,
    elevation_gain_m NUMERIC(8, 1)   NOT NULL DEFAULT 0,
    avg_pace_per_km  VARCHAR(10),                        -- "6:12", NULL for cycling/gym

    -- Audit
    created_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- Dedup constraint: prevents re-importing the same workout.
    -- Same date + type + duration = same session (even if sent twice).
    CONSTRAINT activities_dedup
        UNIQUE (activity_date, activity_type, duration_min)
);

-- ── Indexes ──────────────────────────────────────────────────────────────────

-- Power BI date slicers (most common filter)
CREATE INDEX IF NOT EXISTS idx_activities_date
    ON activities (activity_date DESC);

-- Activity type filtering / breakdown charts
CREATE INDEX IF NOT EXISTS idx_activities_type
    ON activities (activity_type);

-- Combined: date range + type (covers most dashboard queries)
CREATE INDEX IF NOT EXISTS idx_activities_date_type
    ON activities (activity_date DESC, activity_type);

-- ─────────────────────────────────────────────────────────────────────────────
-- Note: n8n creates its own tables (n8n_*) in this same database automatically.
-- They do not conflict with the activities table above.
-- ─────────────────────────────────────────────────────────────────────────────
