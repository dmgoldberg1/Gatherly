CREATE TABLE IF NOT EXISTS recommendation_profiles (
    user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    vector REAL[],
    excluded_event_ids BIGINT[] NOT NULL DEFAULT '{}',
    generation TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
