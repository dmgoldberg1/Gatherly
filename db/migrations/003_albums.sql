CREATE TABLE IF NOT EXISTS albums (
    id BIGSERIAL PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    owner_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 120),
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT','PUBLISHED')),
    publication_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS album_photos (
    id UUID PRIMARY KEY,
    album_id BIGINT NOT NULL REFERENCES albums(id) ON DELETE CASCADE,
    thumbnail_key TEXT NOT NULL,
    gallery_key TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_albums_event ON albums(event_id, id);
CREATE INDEX IF NOT EXISTS idx_album_photos_album ON album_photos(album_id, id);
