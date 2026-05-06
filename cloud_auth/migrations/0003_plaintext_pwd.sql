-- v1.0.43：admin UI 顯示兩組密碼明文 + 從 UI 改管理員密碼
-- 1. 加 password_plain 欄
ALTER TABLE auth ADD COLUMN password_plain TEXT DEFAULT '';

-- 2. 新增 id=2 那列存管理員密碼（id=1 是主密碼）
INSERT OR IGNORE INTO auth (id, password_hash, password_plain, message, killed, updated_at)
VALUES (2, '', '', '', 0, datetime('now'));
