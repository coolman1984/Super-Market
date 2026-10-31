-- Super Market cloud backup. The shop PC's own database is the truth; these are copies of it.
CREATE TABLE IF NOT EXISTS pcs (            -- one row per shop PC that connected itself; its secret is derived, never stored
  id           TEXT PRIMARY KEY,            -- 128-bit hex
  created_at   INTEGER NOT NULL,
  last_seen    INTEGER NOT NULL,
  install_hash TEXT NOT NULL UNIQUE,        -- sha256 of the PC's install id; the id itself is not stored
  label        TEXT NOT NULL DEFAULT ''     -- the shop name the PC sent, to tell the PCs apart in the dashboard
);
CREATE TABLE IF NOT EXISTS copies (         -- the copies of each PC; the bytes are in R2 at pc/<pc>/<name>.db.gz
  pc        TEXT NOT NULL,
  name      TEXT NOT NULL,                  -- hourly-YYYYMMDD-HHMMSS or nightly-YYYYMMDD-HHMMSS (PC clock)
  kind      TEXT NOT NULL,
  size      INTEGER NOT NULL,
  sha256    TEXT NOT NULL,                  -- of the compressed file
  db_sha256 TEXT NOT NULL,                  -- of the database inside
  schema    INTEGER NOT NULL,
  app       TEXT NOT NULL,
  at        INTEGER NOT NULL,
  PRIMARY KEY (pc, name)
);
CREATE TABLE IF NOT EXISTS usage (pc TEXT NOT NULL, day INTEGER NOT NULL, bytes INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (pc, day));
CREATE TABLE IF NOT EXISTS nonces (nonce TEXT PRIMARY KEY, at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS rate (key TEXT NOT NULL, window INTEGER NOT NULL, count INTEGER NOT NULL, PRIMARY KEY (key, window));
