-- v1.0.59：每次批次取號結束精確上報，給 admin UI「今日已產生總筆數」+ per-machine 累計用
CREATE TABLE IF NOT EXISTS batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,           -- ISO timestamp
  hostname TEXT,
  win_user TEXT,
  mac TEXT,
  os_ver TEXT,
  ip TEXT,
  country TEXT,
  version TEXT,
  count INTEGER NOT NULL,     -- 該次批次產出的代碼數
  duration_sec INTEGER,       -- 該次批次跑了多久
  status TEXT                 -- completed / aborted / partial
);
CREATE INDEX IF NOT EXISTS idx_batches_ts ON batches(ts DESC);
CREATE INDEX IF NOT EXISTS idx_batches_host ON batches(hostname);
