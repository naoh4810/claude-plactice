-- クリック1回 = 1行。IPアドレスそのものは保存しない
CREATE TABLE IF NOT EXISTS clicks (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts          TEXT    NOT NULL,              -- UTC ISO8601
  src         TEXT,                          -- 配布元ラベル（?src=instagram など）
  country     TEXT,                          -- JP など
  region_code TEXT,                          -- 13 など（ISO 3166-2）
  region      TEXT,                          -- Tokyo など（Cloudflare推定）
  city        TEXT,
  device      TEXT,                          -- iPhone / iPad / Android / PC / other
  referer     TEXT
);
CREATE INDEX IF NOT EXISTS idx_clicks_ts ON clicks(ts);
