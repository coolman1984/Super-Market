-- Super Market cloud copy: a Cloudflare Worker + D1 that holds a COPY of each shop's data.
-- The shop PC's own database stays the one source of truth. Nothing here is posted money:
-- the cloud stores rows as the shop PC sent them, hands them to phones/other devices read-only,
-- and relays "commands" from those devices back to the shop PC, which decides (see docs/CLOUD_SYNC.md).
-- Every statement is safe to run again.
CREATE TABLE IF NOT EXISTS stores (
  id         TEXT PRIMARY KEY,               -- 128-bit hex, made by the admin call
  name       TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  seq        INTEGER NOT NULL DEFAULT 0,     -- last change number handed out for this store (pull cursor)
  last_push  INTEGER                         -- unix seconds of the last accepted push
);
CREATE TABLE IF NOT EXISTS devices (
  store_id   TEXT NOT NULL,
  id         TEXT NOT NULL,                  -- 64-bit hex
  name       TEXT NOT NULL,
  role       TEXT NOT NULL CHECK (role IN ('shop', 'mobile')),   -- shop = the hub that writes; mobile = reads + sends commands
  created_at INTEGER NOT NULL,
  last_seen  INTEGER,
  revoked_at INTEGER,                        -- set = this device is cut off at once
  PRIMARY KEY (store_id, id)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS pairings (        -- one-time codes that connect a new device; only a hash is kept
  code_hash  TEXT PRIMARY KEY,
  store_id   TEXT NOT NULL,
  role       TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  used_at    INTEGER
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS records (         -- the copy: one row per (table, row id) of the shop database, latest state
  store_id TEXT NOT NULL,
  tbl      TEXT NOT NULL,
  rid      INTEGER NOT NULL,
  v        INTEGER NOT NULL,                 -- version from the shop PC; an older push never overwrites a newer one
  seq      INTEGER NOT NULL,                 -- change number in this store (what devices pull by)
  deleted  INTEGER NOT NULL DEFAULT 0,       -- 1 = tombstone: the row was deleted on the shop PC
  data     TEXT,                             -- the row as JSON (null for a tombstone)
  PRIMARY KEY (store_id, tbl, rid)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS records_seq ON records (store_id, seq);
CREATE TABLE IF NOT EXISTS commands (        -- device -> shop PC requests; the shop PC applies them with its own rules
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  store_id    TEXT NOT NULL,
  key         TEXT NOT NULL,                 -- made by the sender: retrying never duplicates
  kind        TEXT NOT NULL,
  payload     TEXT NOT NULL,
  from_device TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'applied', 'rejected', 'expired')),
  result      TEXT,
  created_at  INTEGER NOT NULL,
  done_at     INTEGER,
  UNIQUE (store_id, key)
);
CREATE INDEX IF NOT EXISTS commands_pending ON commands (store_id, status, id);
CREATE TABLE IF NOT EXISTS nonces (nonce TEXT PRIMARY KEY, at INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS rate (key TEXT NOT NULL, window INTEGER NOT NULL, count INTEGER NOT NULL, PRIMARY KEY (key, window)) WITHOUT ROWID;
-- First-run enrolment (worker 1.2.0). New tables only, so this file stays safe to run again on a live database.
CREATE TABLE IF NOT EXISTS store_meta (      -- extra facts about stores made by an installed program (stores made by hand have no row = active)
  store_id      TEXT PRIMARY KEY,
  status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'active', 'suspended')),  -- pending = trial; the licence hub will activate
  build_id      TEXT,
  trial_until   INTEGER,
  created_via   TEXT,
  recovery_hash TEXT                         -- sha256 of the recovery code; the code itself is derived from MASTER_KEY, never stored
) WITHOUT ROWID;
CREATE UNIQUE INDEX IF NOT EXISTS store_meta_recovery ON store_meta (recovery_hash);
CREATE TABLE IF NOT EXISTS enrollments (     -- one row per installation (hash of its random install id): a retry never makes a second store
  install_hash TEXT PRIMARY KEY,
  store_id     TEXT NOT NULL,
  device_id    TEXT NOT NULL,
  created_at   INTEGER NOT NULL
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS builds (          -- builds whose token is cut off (a leaked build token)
  build_id   TEXT PRIMARY KEY,
  revoked_at INTEGER
) WITHOUT ROWID;
