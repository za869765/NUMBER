-- v1.0.40 audit 紀錄表：每次 /verify 都寫一筆
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,         -- ISO timestamp
  ip TEXT,                  -- cf-connecting-ip
  country TEXT,             -- cf-ipcountry
  ua TEXT,                  -- user-agent（最多 200 字）
  ok INTEGER,               -- 0/1
  reason TEXT               -- 失敗原因（成功時為空）
);

CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_ip ON audit(ip);
