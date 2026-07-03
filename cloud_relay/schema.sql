CREATE TABLE IF NOT EXISTS location_samples (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id        TEXT    NOT NULL,
  sample_uuid      TEXT    UNIQUE,
  latitude         REAL    NOT NULL,
  longitude        REAL    NOT NULL,
  accuracy_meters  REAL,
  source           TEXT,
  recorded_at      TEXT,
  created_at       TEXT    DEFAULT (datetime('now')),
  lease_expires_at TEXT,
  acked_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_pending
  ON location_samples (id)
  WHERE acked_at IS NULL;
