-- v1.0.53：使用者端 EXE 紅色錯誤自動上報
CREATE TABLE IF NOT EXISTS error_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,           -- ISO timestamp
  ip TEXT,
  country TEXT,
  ua TEXT,                    -- HIV-Auth-Client/x.y.z (Windows)
  version TEXT,               -- EXE 版本，e.g. 1.0.53
  trigger TEXT,               -- 觸發報告的紅色 log 訊息（前 200 字）
  log_text TEXT               -- 完整 log 內容（最多保留 100KB）
);
CREATE INDEX IF NOT EXISTS idx_error_reports_ts ON error_reports(ts DESC);
