-- 短縮リンクと、そのクリック記録（IP アドレスや User-Agent の全文は保存しない）
CREATE TABLE IF NOT EXISTS links (
  code       TEXT PRIMARY KEY,
  target     TEXT NOT NULL,
  label      TEXT NOT NULL DEFAULT '',
  source     TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clicks (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  code         TEXT NOT NULL,
  ts           TEXT NOT NULL,          -- UTC の ISO 日時
  country      TEXT,                   -- 例: JP
  region_code  TEXT,                   -- 都道府県コード（例: 13 = 東京都）
  region       TEXT,                   -- 例: Tokyo
  city         TEXT,
  referer_host TEXT,
  device       TEXT,                   -- ios / android / desktop / other
  app          TEXT,                   -- instagram / facebook / line / tiktok（アプリ内ブラウザのとき）
  is_bot       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_clicks_code_ts ON clicks (code, ts);
CREATE INDEX IF NOT EXISTS idx_clicks_ts ON clicks (ts);
