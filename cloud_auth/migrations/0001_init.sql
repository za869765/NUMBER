-- D1 初始化：單列 auth 表
CREATE TABLE IF NOT EXISTS auth (
  id INTEGER PRIMARY KEY,
  password_hash TEXT NOT NULL DEFAULT '',
  message TEXT DEFAULT '',
  killed INTEGER DEFAULT 0,
  updated_at TEXT
);

-- 確保 id=1 那筆存在（空密碼狀態，等 admin UI 設定）
INSERT OR IGNORE INTO auth (id, password_hash, message, killed, updated_at)
VALUES (1, '', '', 0, '2026-01-01T00:00:00Z');
