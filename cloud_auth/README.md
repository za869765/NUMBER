# HIV 取號工具 — 雲端授權

放在 Cloudflare Worker + D1 的小服務。EXE 啟動時連線驗證主密碼，
管理員可在網頁 UI 改密碼或停用所有 EXE。

## 部署步驟（一次性）

### 1. 安裝 wrangler
```cmd
cd cloud_auth
npm install
```

### 2. 登入 Cloudflare
```cmd
npx wrangler login
```
（瀏覽器會跳出授權頁）

### 3. 建 D1 資料庫
```cmd
npx wrangler d1 create hiv-auth-db
```
複製輸出中的 `database_id`，貼到 `wrangler.toml` 對應位置。

### 4. 初始化 schema（跑全部 migrations）
```cmd
npm run migrate
```
（內部走 `wrangler d1 migrations apply hiv-auth-db --remote`，會依 `migrations/` 順序跑、不會重跑已套用的）

### 5. 設**初次**管理員密碼（一次性）
v1.0.43 起密碼存 D1 + 用 PBKDF2，但**初次部署 D1 還沒有管理員密碼**，需要靠 wrangler secret 當入口：

先用 [hash_admin_pwd.html](./hash_admin_pwd.html) 算 SHA-256（直接在瀏覽器算，不上網路）。

```cmd
npx wrangler secret put ADMIN_PASSWORD_HASH
```
貼上 hash 按 Enter。

> 💡 進去 admin UI 後在「更新管理員密碼」改一次，密碼就會以 PBKDF2 雜湊存到 D1，**之後改密碼都從 UI 改即可**，不用再跑 wrangler secret。

### 6. 部署
```cmd
npm run deploy
```
記下產出的網址，例如 `https://hiv-auth.<你的帳號>.workers.dev`。

### 7. 設第一次主密碼
打開 `https://hiv-auth.<你的帳號>.workers.dev/admin`，
用管理員密碼登入，在「更新主密碼」設定第一次主密碼（會以 PBKDF2 雜湊儲存）。

### 8. 把 worker URL 寫進 EXE
編輯 `..\hiv_code.py` 找 `CLOUD_AUTH_URL`，改成你的 worker URL，
然後 `..\build_release.bat` 重編 EXE。

---

## 日常使用

| 想做什麼 | 怎麼做 |
|---------|--------|
| 改主密碼（EXE 用） | 登入 `/admin` → 更新主密碼 |
| 改管理員密碼（後台用） | 登入 `/admin` → 更新管理員密碼 |
| 暫時停用所有 EXE | `/admin` → 停用開關 → 全部停用 |
| 寫一行訊息給違規者 | `/admin` → 停用訊息 → 儲存 |
| 看目前狀態 | `/admin` → 重新整理 |
| 重置管理員密碼（忘記了） | 兩步：(1) 清掉 D1 既有：`npx wrangler d1 execute hiv-auth-db --remote --command "UPDATE auth SET password_hash='' WHERE id=2"` (2) `npx wrangler secret put ADMIN_PASSWORD_HASH` 重設 → 用新密碼登入 UI 再改成想要的 |
| 跑新 migration | `npm run migrate` |

## 端點

| Method | Path | 用途 |
|--------|------|------|
| POST | `/verify` | EXE 用：`{password}` → `{ok, reason}` |
| GET | `/admin` | 管理 UI |
| POST | `/admin/state` | 取得狀態 |
| POST | `/admin/update` | 改密碼/訊息/停用 |
| GET | `/health` | 存活檢查 |
