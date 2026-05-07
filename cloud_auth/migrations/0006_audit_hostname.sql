-- v1.0.56：audit 加 hostname column，讓「7 天活躍機器」用 COUNT(DISTINCT hostname) 算
-- 比 IP 準（同機換網路會被算成 1 台、共用網段不同人會被算成 N 台）
ALTER TABLE audit ADD COLUMN hostname TEXT DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_audit_hostname ON audit(hostname);
