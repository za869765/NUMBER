// HIV 取號工具 — 雲端授權 Worker
// 端點：
//   POST /verify         EXE 用：{password} → {ok, reason}
//   GET  /admin          管理 UI（HTML 頁面）
//   POST /admin/state    取得目前狀態：{admin_pwd} → {state}
//   POST /admin/update   更新：{admin_pwd, action, value}
//
// 環境變數（用 wrangler secret put 設定）：
//   ADMIN_PASSWORD_HASH  管理員密碼的 SHA-256 hex
//
// D1 binding: DB（hiv-auth-db）

const JSON_HDR = { 'Content-Type': 'application/json;charset=utf-8' };

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    try {
      if (path === '/verify' && request.method === 'POST') {
        return await handleVerify(request, env);
      }
      if (path === '/admin' && request.method === 'GET') {
        return new Response(ADMIN_HTML, {
          headers: { 'Content-Type': 'text/html;charset=utf-8' },
        });
      }
      if (path === '/admin/state' && request.method === 'POST') {
        return await handleAdminState(request, env);
      }
      if (path === '/admin/update' && request.method === 'POST') {
        return await handleAdminUpdate(request, env);
      }
      if (path === '/admin/audit' && request.method === 'POST') {
        return await handleAdminAudit(request, env);
      }
      if (path === '/admin/audit/stats' && request.method === 'POST') {
        return await handleAdminStats(request, env);
      }
      if (path === '/admin/audit/clear' && request.method === 'POST') {
        return await handleAdminAuditClear(request, env);
      }
      if (path === '/admin/audit/daily' && request.method === 'POST') {
        return await handleAdminDaily(request, env);
      }
      if (path === '/' || path === '/health') {
        return new Response('hiv-auth ok', { status: 200 });
      }
      return new Response('Not Found', { status: 404 });
    } catch (e) {
      // v1.0.43: L5 不洩漏 internal error message
      console.error('worker error:', e && e.stack || e);
      return jsonReply({ ok: false, reason: '伺服器錯誤，請稍後再試' }, 500);
    }
  },
};

// ── v1.0.43 速率限制（H3）──────────────────────
// 用 audit 表反查同 IP 過去 1 分鐘紀錄；30 全部 / 10 失敗即鎖
async function isRateLimited(env, ip) {
  if (!ip) return false;
  try {
    const r = await env.DB.prepare(
      `SELECT COUNT(*) AS total,
              SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) AS fails
       FROM audit WHERE ip = ?1 AND ts >= datetime('now', '-1 minute')`
    ).bind(ip).first();
    const total = (r && r.total) || 0;
    const fails = (r && r.fails) || 0;
    return total >= 30 || fails >= 10;
  } catch {
    return false; // fail-open：D1 錯也讓正常驗證流程接手
  }
}

// ── EXE 驗證 ──────────────────────────────────
async function handleVerify(request, env) {
  // v1.0.43 H3：先擋頻繁試誤
  const ip = request.headers.get('cf-connecting-ip') || '';
  if (await isRateLimited(env, ip)) {
    await logAudit(request, env, false, '速率限制');
    return jsonReply({ ok: false, reason: '嘗試太頻繁，請稍後（約 1 分鐘）再試' }, 429);
  }
  let body;
  try {
    body = await request.json();
  } catch {
    await logAudit(request, env, false, '格式錯誤');
    return jsonReply({ ok: false, reason: '格式錯誤' }, 400);
  }
  const pwd = (body && body.password) || '';
  if (!pwd) {
    await logAudit(request, env, false, '密碼空白');
    return jsonReply({ ok: false, reason: '密碼空白' }, 400);
  }
  const row = await env.DB.prepare(
    'SELECT password_hash, message, killed FROM auth WHERE id = 1'
  ).first();
  if (!row || !row.password_hash) {
    await logAudit(request, env, false, '伺服器未設密碼');
    return jsonReply({ ok: false, reason: '伺服器尚未設定密碼，請聯絡管理員' }, 503);
  }
  if (row.killed) {
    await logAudit(request, env, false, 'killed');
    return jsonReply({ ok: false, reason: row.message || '本服務已停用，請聯絡管理員' }, 403);
  }
  // v1.0.43 M1：PBKDF2 驗證；老 SHA-256 紀錄成功時透明遷移
  const v = await verifyPassword(pwd, row.password_hash);
  if (v.ok) {
    if (v.legacy) {
      try {
        const newHash = await pbkdf2Hash(pwd);
        await env.DB.prepare(
          'UPDATE auth SET password_hash = ?1 WHERE id = 1'
        ).bind(newHash).run();
      } catch { /* 遷移失敗不影響本次登入 */ }
    }
    await logAudit(request, env, true, '');
    return jsonReply({ ok: true });
  }
  await logAudit(request, env, false, '密碼錯誤');
  return jsonReply({ ok: false, reason: '密碼錯誤' }, 401);
}

// 寫一筆 audit log（fire-and-forget，失敗不影響主流程）
async function logAudit(request, env, ok, reason) {
  try {
    const ip = request.headers.get('cf-connecting-ip') || '';
    const country =
      (request.cf && request.cf.country) ||
      request.headers.get('cf-ipcountry') ||
      '';
    const ua = (request.headers.get('user-agent') || '').slice(0, 200);
    const ts = new Date().toISOString();
    await env.DB.prepare(
      'INSERT INTO audit (ts, ip, country, ua, ok, reason) VALUES (?1, ?2, ?3, ?4, ?5, ?6)'
    )
      .bind(ts, ip, country, ua, ok ? 1 : 0, reason || '')
      .run();
  } catch {
    /* swallow */
  }
}

// ── 管理員：讀狀態 ────────────────────────────
async function handleAdminState(request, env) {
  const body = await request.json().catch(() => ({}));
  if (!(await checkAdmin(body.admin_pwd, env))) {
    return jsonReply({ ok: false, reason: '管理員密碼錯誤' }, 401);
  }
  const main =
    (await env.DB.prepare(
      'SELECT password_hash, password_plain, message, killed, updated_at FROM auth WHERE id = 1'
    ).first()) || {};
  const admin =
    (await env.DB.prepare(
      'SELECT password_plain FROM auth WHERE id = 2'
    ).first()) || {};
  return jsonReply({
    ok: true,
    state: {
      password_set: !!main.password_hash,
      password_plain: main.password_plain || '',
      admin_plain: admin.password_plain || '',
      message: main.message || '',
      killed: !!main.killed,
      updated_at: main.updated_at || '',
    },
  });
}

// ── 管理員：更新 ──────────────────────────────
async function handleAdminUpdate(request, env) {
  const body = await request.json().catch(() => ({}));
  if (!(await checkAdmin(body.admin_pwd, env))) {
    return jsonReply({ ok: false, reason: '管理員密碼錯誤' }, 401);
  }
  const action = body.action;
  const value = body.value;
  const now = new Date().toISOString();

  if (action === 'set_password') {
    if (!value || typeof value !== 'string' || value.length < 4) {
      return jsonReply({ ok: false, reason: '密碼至少 4 字' }, 400);
    }
    const hash = await pbkdf2Hash(value);
    // v1.0.43 M3：UPSERT 避免 schema drift 時 silent fail
    await env.DB.prepare(
      `INSERT INTO auth (id, password_hash, password_plain, message, killed, updated_at)
       VALUES (1, ?1, ?2, '', 0, ?3)
       ON CONFLICT(id) DO UPDATE SET
         password_hash = excluded.password_hash,
         password_plain = excluded.password_plain,
         updated_at = excluded.updated_at`
    )
      .bind(hash, value, now)
      .run();
    return jsonReply({ ok: true, msg: '程式授權密碼已更新' });
  }

  if (action === 'set_admin_password') {
    if (!value || typeof value !== 'string' || value.length < 4) {
      return jsonReply({ ok: false, reason: '密碼至少 4 字' }, 400);
    }
    const hash = await pbkdf2Hash(value);
    await env.DB.prepare(
      `INSERT INTO auth (id, password_hash, password_plain, message, killed, updated_at)
       VALUES (2, ?1, ?2, '', 0, ?3)
       ON CONFLICT(id) DO UPDATE SET
         password_hash = excluded.password_hash,
         password_plain = excluded.password_plain,
         updated_at = excluded.updated_at`
    )
      .bind(hash, value, now)
      .run();
    return jsonReply({
      ok: true,
      msg: '管理員密碼已更新（下次登入請用新密碼）',
    });
  }

  if (action === 'set_message') {
    await env.DB.prepare(
      'UPDATE auth SET message = ?1, updated_at = ?2 WHERE id = 1'
    )
      .bind(String(value || ''), now)
      .run();
    return jsonReply({ ok: true, msg: '停用訊息已更新' });
  }

  if (action === 'set_killed') {
    const k = value ? 1 : 0;
    await env.DB.prepare(
      'UPDATE auth SET killed = ?1, updated_at = ?2 WHERE id = 1'
    )
      .bind(k, now)
      .run();
    return jsonReply({
      ok: true,
      msg: k ? '已停用：所有 EXE 下次啟動會被擋下' : '已恢復服務',
    });
  }

  return jsonReply({ ok: false, reason: '不明動作' }, 400);
}

// ── 管理員：audit log 列表 ────────────────────
async function handleAdminAudit(request, env) {
  const body = await request.json().catch(() => ({}));
  if (!(await checkAdmin(body.admin_pwd, env))) {
    return jsonReply({ ok: false, reason: '管理員密碼錯誤' }, 401);
  }
  const limit = Math.min(500, Math.max(10, parseInt(body.limit, 10) || 50));
  const filter = body.filter || 'all'; // all / ok / fail
  let where = '';
  if (filter === 'ok') where = 'WHERE ok = 1';
  else if (filter === 'fail') where = 'WHERE ok = 0';
  const sql = `SELECT id, ts, ip, country, ua, ok, reason FROM audit ${where} ORDER BY id DESC LIMIT ?1`;
  const r = await env.DB.prepare(sql).bind(limit).all();
  return jsonReply({ ok: true, rows: r.results || [] });
}

// ── 管理員：統計摘要 ────────────────────────
async function handleAdminStats(request, env) {
  const body = await request.json().catch(() => ({}));
  if (!(await checkAdmin(body.admin_pwd, env))) {
    return jsonReply({ ok: false, reason: '管理員密碼錯誤' }, 401);
  }
  const stats = {};
  // 24h
  const r24 = await env.DB.prepare(
    `SELECT COUNT(*) AS total,
            SUM(CASE WHEN ok=1 THEN 1 ELSE 0 END) AS ok_cnt,
            SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) AS fail_cnt,
            COUNT(DISTINCT ip) AS uniq_ip
     FROM audit WHERE ts >= datetime('now', '-1 day')`
  ).first();
  // 7d
  const r7 = await env.DB.prepare(
    `SELECT COUNT(*) AS total,
            SUM(CASE WHEN ok=1 THEN 1 ELSE 0 END) AS ok_cnt,
            SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) AS fail_cnt,
            COUNT(DISTINCT ip) AS uniq_ip
     FROM audit WHERE ts >= datetime('now', '-7 days')`
  ).first();
  // top IPs（過去 7 天）
  const topIps = await env.DB.prepare(
    `SELECT ip, country,
            COUNT(*) AS total,
            SUM(CASE WHEN ok=1 THEN 1 ELSE 0 END) AS ok_cnt,
            SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) AS fail_cnt,
            MAX(ts) AS last_seen
     FROM audit WHERE ts >= datetime('now', '-7 days')
     GROUP BY ip ORDER BY total DESC LIMIT 10`
  ).all();
  return jsonReply({
    ok: true,
    last_24h: r24 || {},
    last_7d: r7 || {},
    top_ips: topIps.results || [],
  });
}

// ── 管理員：每日統計（長條圖用）──────────────
async function handleAdminDaily(request, env) {
  const body = await request.json().catch(() => ({}));
  if (!(await checkAdmin(body.admin_pwd, env))) {
    return jsonReply({ ok: false, reason: '管理員密碼錯誤' }, 401);
  }
  const days = Math.min(60, Math.max(7, parseInt(body.days, 10) || 14));
  const r = await env.DB.prepare(
    `SELECT DATE(ts) AS day,
            COUNT(*) AS total,
            SUM(CASE WHEN ok=1 THEN 1 ELSE 0 END) AS ok_cnt,
            SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) AS fail_cnt,
            COUNT(DISTINCT ip) AS uniq_ip,
            MIN(ts) AS first_ts,
            MAX(ts) AS last_ts
     FROM audit
     WHERE ts >= datetime('now', ?1)
     GROUP BY DATE(ts)
     ORDER BY day`
  )
    .bind(`-${days} days`)
    .all();
  return jsonReply({ ok: true, days, rows: r.results || [] });
}

// ── 管理員：清空 audit ───────────────────────
async function handleAdminAuditClear(request, env) {
  const body = await request.json().catch(() => ({}));
  if (!(await checkAdmin(body.admin_pwd, env))) {
    return jsonReply({ ok: false, reason: '管理員密碼錯誤' }, 401);
  }
  // v1.0.43 M2：擋負數/NaN，避免「days=-1 全清」footgun
  const raw = body.older_than_days;
  const days = parseInt(raw, 10);
  if (isNaN(days) || days < 0) {
    return jsonReply({ ok: false, reason: '天數無效（請填 0 或正整數，0=全清）' }, 400);
  }
  if (days > 0) {
    const r = await env.DB.prepare(
      `DELETE FROM audit WHERE ts < datetime('now', ?1)`
    )
      .bind(`-${days} days`)
      .run();
    return jsonReply({ ok: true, msg: `已清掉 ${days} 天前的紀錄`, meta: r.meta });
  }
  // days === 0：全清
  const r = await env.DB.prepare(`DELETE FROM audit`).run();
  return jsonReply({ ok: true, msg: '全部紀錄已清空', meta: r.meta });
}

// ── 工具 ───────────────────────────────────────
// v1.0.43：admin pwd 從 D1 row id=2 讀，wrangler secret 留 fallback
//          M1：PBKDF2 主用，舊 SHA-256 自動透明遷移
async function checkAdmin(pwd, env) {
  if (!pwd) return false;
  try {
    const row = await env.DB.prepare(
      'SELECT password_hash FROM auth WHERE id = 2'
    ).first();
    if (row && row.password_hash) {
      const v = await verifyPassword(pwd, row.password_hash);
      if (v.ok && v.legacy) {
        try {
          const newHash = await pbkdf2Hash(pwd);
          await env.DB.prepare(
            'UPDATE auth SET password_hash = ?1 WHERE id = 2'
          ).bind(newHash).run();
        } catch { /* swallow */ }
      }
      return v.ok;
    }
  } catch {
    /* fall through to env */
  }
  // env fallback 還是 SHA-256（一次性、用於初次部署）
  if (env.ADMIN_PASSWORD_HASH) {
    const h = await sha256Hex(pwd);
    return h === env.ADMIN_PASSWORD_HASH;
  }
  return false;
}

async function sha256Hex(text) {
  const buf = await crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(text)
  );
  return [...new Uint8Array(buf)]
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

// ── v1.0.43 PBKDF2 雜湊（M1）───────────────────
// 格式：pbkdf2$<iter>$<saltHex>$<hashHex>
// 舊 SHA-256 是 64 字 hex，無 $，可由 verifyPassword 自動辨識
const PBKDF2_ITER = 100000;
const PBKDF2_HASH_BYTES = 32;
const PBKDF2_SALT_BYTES = 16;

async function pbkdf2Hash(password) {
  const salt = crypto.getRandomValues(new Uint8Array(PBKDF2_SALT_BYTES));
  const key = await crypto.subtle.importKey(
    'raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveBits']
  );
  const bits = await crypto.subtle.deriveBits(
    { name: 'PBKDF2', salt, iterations: PBKDF2_ITER, hash: 'SHA-256' },
    key, PBKDF2_HASH_BYTES * 8
  );
  return `pbkdf2$${PBKDF2_ITER}$${bytesToHex(salt)}$${bytesToHex(new Uint8Array(bits))}`;
}

async function pbkdf2Verify(password, stored) {
  const parts = stored.split('$');
  if (parts.length !== 4 || parts[0] !== 'pbkdf2') return false;
  const iter = parseInt(parts[1], 10);
  if (!iter || iter < 1000) return false;
  const salt = hexToBytes(parts[2]);
  const expected = parts[3];
  const key = await crypto.subtle.importKey(
    'raw', new TextEncoder().encode(password), 'PBKDF2', false, ['deriveBits']
  );
  const bits = await crypto.subtle.deriveBits(
    { name: 'PBKDF2', salt, iterations: iter, hash: 'SHA-256' },
    key, PBKDF2_HASH_BYTES * 8
  );
  const got = bytesToHex(new Uint8Array(bits));
  return constantTimeEq(got, expected);
}

async function verifyPassword(password, stored) {
  if (!stored) return { ok: false, legacy: false };
  if (typeof stored === 'string' && stored.startsWith('pbkdf2$')) {
    return { ok: await pbkdf2Verify(password, stored), legacy: false };
  }
  // legacy SHA-256（64 字 hex）
  const ok = (await sha256Hex(password)) === stored;
  return { ok, legacy: true };
}

function bytesToHex(bytes) {
  return [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function hexToBytes(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(hex.substr(i * 2, 2), 16);
  }
  return out;
}

function constantTimeEq(a, b) {
  if (a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) {
    r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return r === 0;
}

function jsonReply(obj, status = 200) {
  return new Response(JSON.stringify(obj), { status, headers: JSON_HDR });
}

// ── 管理 UI（HTML）──────────────────────────────
const ADMIN_HTML = `<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HIV 取號工具 — 雲端授權管理</title>
<style>
  /* ============ Tokens ============ */
  :root{
    --bg: #f4f1ec;
    --surface: #ffffff;
    --surface-2: #faf8f4;
    --ink: #1c2530;
    --ink-2: #4a5563;
    --ink-3: #7a8593;
    --line: #e5e0d6;
    --line-2: #efece4;
    --accent: #2f6b6a;
    --accent-2: #1f4d4c;
    --accent-soft: #e6efee;
    --ok: #2f6b3f;
    --ok-soft: #e2eee2;
    --bad: #a13a2c;
    --bad-soft: #f3e0db;
    --warn: #8a6a1c;
    --warn-soft: #f3ead0;
    --shadow: 0 1px 0 rgba(28,37,48,.04), 0 1px 2px rgba(28,37,48,.04);
    --radius: 6px;
    --radius-lg: 10px;
    --mono: ui-monospace, SFMono-Regular, "SF Mono", Consolas, "Liberation Mono", monospace;
    --sans: "Microsoft JhengHei", "PingFang TC", "Helvetica Neue", Helvetica, Arial, system-ui, sans-serif;
  }

  *{box-sizing:border-box}
  html,body{margin:0;padding:0}
  body{
    font: 14px/1.55 var(--sans);
    background: var(--bg);
    color: var(--ink);
    -webkit-font-smoothing: antialiased;
    min-height: 100vh;
  }
  ::selection{ background: var(--accent-soft); }

  a{ color: var(--accent); }
  code, .mono{ font-family: var(--mono); }

  /* ============ Login ============ */
  .login-wrap{
    min-height: 100vh;
    display: grid; place-items: center;
    padding: 24px;
  }
  .login-card{
    width: 100%; max-width: 380px;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--radius-lg);
    padding: 32px 28px 28px;
    box-shadow: var(--shadow);
  }
  .brand{
    display: flex; align-items: center; gap: 10px;
    margin-bottom: 22px;
  }
  .brand-mark{
    width: 32px; height: 32px;
    border-radius: 8px;
    background: var(--accent);
    color: #fff;
    display: grid; place-items: center;
    font-weight: 700; font-size: 14px;
    letter-spacing: 0.5px;
  }
  .brand-name{
    font-size: 13px; color: var(--ink-2);
    letter-spacing: 0.3px;
  }
  .brand-name strong{ color: var(--ink); font-weight: 600; }
  .login-card h1{
    font-size: 18px; margin: 0 0 4px; font-weight: 600;
  }
  .login-card .lead{
    font-size: 13px; color: var(--ink-3); margin: 0 0 22px;
  }

  /* ============ App shell ============ */
  .app{
    display: grid;
    grid-template-columns: 240px minmax(0, 1fr);
    min-height: 100vh;
  }
  .sidebar{
    background: var(--surface-2);
    border-right: 1px solid var(--line);
    padding: 22px 14px;
    position: sticky; top: 0;
    height: 100vh;
    overflow-y: auto;
  }
  .sidebar .brand{ margin-bottom: 24px; padding: 0 8px; }
  .nav{
    display: flex; flex-direction: column; gap: 2px;
    margin-bottom: 18px;
  }
  .nav a{
    display: flex; align-items: center; gap: 10px;
    padding: 8px 10px;
    border-radius: var(--radius);
    color: var(--ink-2);
    text-decoration: none;
    font-size: 13px;
    font-weight: 500;
    transition: background .12s, color .12s;
  }
  .nav a:hover{ background: var(--line-2); color: var(--ink); }
  .nav a.active{
    background: var(--accent-soft);
    color: var(--accent-2);
  }
  .nav-icon{
    width: 16px; height: 16px;
    flex-shrink: 0;
    opacity: 0.85;
  }
  .nav-section{
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: var(--ink-3);
    padding: 14px 10px 6px;
    font-weight: 600;
  }
  .sidebar-footer{
    margin-top: auto;
    padding: 12px 10px;
    border-top: 1px solid var(--line);
    font-size: 11px;
    color: var(--ink-3);
  }
  .status-dot{
    display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    background: var(--ok); margin-right: 6px; vertical-align: middle;
  }
  .status-dot.bad{ background: var(--bad); }

  .main{
    padding: 28px 36px 60px;
    max-width: 1080px;
    width: 100%;
  }
  .page-head{
    display: flex; align-items: baseline; justify-content: space-between;
    margin-bottom: 22px;
    gap: 12px; flex-wrap: wrap;
  }
  .page-title{
    font-size: 20px; font-weight: 600; margin: 0;
  }
  .page-meta{
    font-size: 12px; color: var(--ink-3);
  }
  .page-meta .sep{ margin: 0 6px; opacity: .5; }

  /* ============ Section / card ============ */
  .section{
    margin-bottom: 32px;
    scroll-margin-top: 24px;
  }
  .section-head{
    display: flex; align-items: center; gap: 10px;
    margin-bottom: 12px;
  }
  .section-title{
    font-size: 14px; font-weight: 600; margin: 0;
    color: var(--ink);
    letter-spacing: 0.2px;
  }
  .section-sub{
    font-size: 12px; color: var(--ink-3);
    margin-left: 2px;
  }

  .card{
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow);
    overflow: hidden;
  }
  .card + .card{ margin-top: 12px; }
  .card-body{ padding: 18px 20px; }
  .card-header{
    padding: 14px 20px;
    border-bottom: 1px solid var(--line-2);
    display: flex; align-items: center; gap: 10px; justify-content: space-between;
  }
  .card-header h3{
    margin: 0; font-size: 13px; font-weight: 600;
  }
  .card-header .help{
    font-size: 12px; color: var(--ink-3);
  }
  .card-divider{
    height: 1px; background: var(--line-2);
    margin: 16px -20px;
  }

  /* ============ Form ============ */
  label{
    display: block;
    font-size: 12px; font-weight: 600;
    color: var(--ink-2);
    margin: 0 0 6px;
    letter-spacing: 0.2px;
  }
  input[type=text], input[type=password], input[type=number], textarea, select{
    width: 100%;
    padding: 9px 11px;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    font: inherit;
    color: var(--ink);
    transition: border-color .12s, box-shadow .12s;
  }
  input:focus, textarea:focus, select:focus{
    outline: none;
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-soft);
  }
  textarea{ min-height: 72px; resize: vertical; line-height: 1.5; }
  select{
    appearance: none;
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'><path d='M1 1l4 4 4-4' stroke='%237a8593' stroke-width='1.5' fill='none' stroke-linecap='round'/></svg>");
    background-repeat: no-repeat;
    background-position: right 11px center;
    padding-right: 28px;
  }

  .field-row{
    display: flex; gap: 10px; align-items: flex-end;
    flex-wrap: wrap;
  }
  .field-row > .field{ flex: 1; min-width: 220px; }
  .field-help{
    font-size: 12px; color: var(--ink-3);
    margin: 0 0 12px;
    line-height: 1.5;
  }
  .field-help.warn{ color: var(--warn); }

  /* ============ Button ============ */
  .btn{
    display: inline-flex; align-items: center; justify-content: center; gap: 6px;
    padding: 8px 14px;
    background: var(--accent);
    color: #fff;
    border: 1px solid var(--accent);
    border-radius: var(--radius);
    font: inherit; font-weight: 600; font-size: 13px;
    cursor: pointer;
    transition: background .12s, border-color .12s, color .12s, transform .05s;
    white-space: nowrap;
  }
  .btn:hover{ background: var(--accent-2); border-color: var(--accent-2); }
  .btn:active{ transform: translateY(1px); }
  .btn:disabled{ opacity: .55; cursor: not-allowed; transform: none; }
  .btn.ghost{
    background: transparent; color: var(--ink-2);
    border-color: var(--line);
  }
  .btn.ghost:hover{ background: var(--line-2); color: var(--ink); border-color: var(--line); }
  .btn.danger{
    background: var(--bad); border-color: var(--bad);
  }
  .btn.danger:hover{ background: #832d22; border-color: #832d22; }
  .btn.sm{ padding: 4px 10px; font-size: 12px; }
  .btn.tiny{ padding: 2px 8px; font-size: 11px; font-weight: 500; }

  .btn-spinner{
    display: inline-block; width: 12px; height: 12px;
    border-radius: 50%;
    border: 2px solid currentColor;
    border-top-color: transparent;
    animation: spin .7s linear infinite;
  }
  @keyframes spin{ to { transform: rotate(360deg); } }

  /* ============ Pills / badges ============ */
  .pill{
    display: inline-flex; align-items: center; gap: 5px;
    padding: 2px 9px; border-radius: 999px;
    font-size: 11px; font-weight: 600;
    line-height: 1.6;
    letter-spacing: 0.2px;
  }
  .pill.ok{ background: var(--ok-soft); color: var(--ok); }
  .pill.bad{ background: var(--bad-soft); color: var(--bad); }
  .pill.muted{ background: var(--line-2); color: var(--ink-2); }
  .pill .dot{ width: 6px; height: 6px; border-radius: 50%; background: currentColor; }

  /* ============ State grid ============ */
  .state-grid{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 14px;
  }
  .state-cell{
    background: var(--surface-2);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 12px 14px;
  }
  .state-cell .k{
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    color: var(--ink-3);
    font-weight: 600;
    margin-bottom: 6px;
  }
  .state-cell .v{
    font-size: 14px;
    color: var(--ink);
    display: flex; align-items: center; gap: 8px;
    min-height: 24px;
  }
  .pwd-display{
    font-family: var(--mono);
    background: var(--surface);
    padding: 4px 9px;
    border-radius: 4px;
    border: 1px solid var(--line);
    font-size: 12px;
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .pwd-display.empty{ color: var(--ink-3); font-family: var(--sans); font-style: italic; }

  /* ============ Stat boxes for chart card ============ */
  .stat-row{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 10px;
    margin-bottom: 14px;
  }
  .stat-box{
    padding: 10px 14px;
    background: var(--surface-2);
    border: 1px solid var(--line);
    border-radius: var(--radius);
  }
  .stat-box .label{
    font-size: 11px; color: var(--ink-3);
    text-transform: uppercase; letter-spacing: 0.6px;
    font-weight: 600;
  }
  .stat-box .num{
    font-size: 22px; font-weight: 700; color: var(--ink);
    margin-top: 2px;
    font-variant-numeric: tabular-nums;
  }
  .stat-box .num.ok{ color: var(--ok); }
  .stat-box .num.bad{ color: var(--bad); }

  /* ============ Chart ============ */
  .chart-toolbar{
    display: flex; gap: 8px; align-items: center;
    flex-wrap: wrap;
    margin-bottom: 12px;
  }
  .chart-toolbar select{ width: auto; min-width: 140px; }
  .chart-toolbar .spacer{ flex: 1; }
  .chart-legend{
    display: flex; gap: 16px; align-items: center;
    font-size: 12px; color: var(--ink-3);
    margin-top: 8px;
    flex-wrap: wrap;
  }
  .chart-legend .swatch{
    display: inline-block; width: 10px; height: 10px;
    border-radius: 2px; vertical-align: middle;
    margin-right: 6px;
  }
  .chart-total{
    margin-left: auto;
    font-variant-numeric: tabular-nums;
    color: var(--ink-2);
  }
  #chart{
    width: 100%;
    border: 1px solid var(--line-2);
    border-radius: var(--radius);
    background: var(--surface-2);
    padding: 8px;
  }
  #chart svg{ display: block; }

  /* ============ Tables ============ */
  .scroll{
    border: 1px solid var(--line);
    border-radius: var(--radius);
    overflow: auto;
    max-height: 420px;
    background: var(--surface);
  }
  table{
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }
  th{
    background: var(--surface-2);
    color: var(--ink-2);
    padding: 9px 12px;
    text-align: left;
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    position: sticky; top: 0;
    border-bottom: 1px solid var(--line);
    z-index: 1;
  }
  td{
    padding: 8px 12px;
    border-bottom: 1px solid var(--line-2);
    vertical-align: middle;
  }
  tr:last-child td{ border-bottom: 0; }
  tbody tr:hover td{ background: var(--surface-2); }
  td.ip, td.mono{ font-family: var(--mono); font-size: 12px; }
  td.ts{ white-space: nowrap; color: var(--ink-2); font-variant-numeric: tabular-nums; }
  td.ua{
    color: var(--ink-3); font-size: 11px;
    max-width: 280px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  td.num{ font-variant-numeric: tabular-nums; text-align: right; }
  td.num.ok{ color: var(--ok); font-weight: 600; }
  td.num.bad{ color: var(--bad); font-weight: 600; }
  .empty-row td{
    text-align: center;
    padding: 32px 12px;
    color: var(--ink-3);
    font-style: italic;
  }

  .filter-row{
    display: flex; gap: 8px; align-items: center;
    flex-wrap: wrap;
    margin-bottom: 12px;
  }
  .filter-row select, .filter-row input{ width: auto; }
  .filter-row .spacer{ flex: 1; }
  .clear-block{
    display: flex; align-items: center; gap: 6px;
    color: var(--ink-3); font-size: 12px;
  }
  .clear-block input{ width: 64px; padding: 5px 8px; font-size: 12px; }

  /* ============ Toast ============ */
  .toast{
    position: fixed;
    top: 16px; right: 16px;
    background: var(--ink);
    color: #fff;
    padding: 11px 16px;
    border-radius: var(--radius);
    box-shadow: 0 8px 24px rgba(28,37,48,.18);
    z-index: 999;
    font-size: 13px;
    font-weight: 500;
    max-width: 360px;
    animation: toast-in .22s cubic-bezier(.2,.7,.3,1);
  }
  .toast.ok{ background: var(--accent-2); }
  .toast.err{ background: var(--bad); }
  @keyframes toast-in{
    from{ transform: translateY(-12px); opacity: 0; }
    to{ transform: translateY(0); opacity: 1; }
  }

  .hidden{ display: none !important; }

  /* ============ Mobile ============ */
  @media (max-width: 860px){
    .app{ grid-template-columns: 1fr; }
    .sidebar{
      position: relative; height: auto;
      padding: 12px 14px;
      border-right: 0;
      border-bottom: 1px solid var(--line);
    }
    .sidebar .brand{ margin-bottom: 10px; }
    .nav{
      flex-direction: row;
      flex-wrap: wrap;
      gap: 4px;
      margin-bottom: 0;
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
    }
    .nav a{
      flex-shrink: 0;
      padding: 6px 10px;
      font-size: 12px;
    }
    .nav-section, .sidebar-footer{ display: none; }
    .main{ padding: 18px 16px 40px; }
    .toast{
      top: 12px; left: 12px; right: 12px;
      max-width: none;
    }
    .card-body{ padding: 14px 16px; }
    .card-header{ padding: 12px 16px; }
    .card-divider{ margin: 14px -16px; }
  }
</style>
</head>
<body>

<!-- ============ LOGIN ============ -->
<div class="login-wrap" id="login_card">
  <div class="login-card">
    <div class="brand">
      <div class="brand-mark">HA</div>
      <div class="brand-name">HIV 取號工具<br><strong>雲端授權管理</strong></div>
    </div>
    <h1>管理員登入</h1>
    <p class="lead">輸入管理員密碼以管理 EXE 授權</p>
    <div class="field" style="margin-bottom: 14px;">
      <label for="adm">管理員密碼</label>
      <input id="adm" type="password" autocomplete="current-password" placeholder="••••••••">
    </div>
    <button class="btn" style="width:100%" onclick="login()" id="login_btn">登入</button>
  </div>
</div>

<!-- ============ MAIN ============ -->
<div id="main_card" class="hidden">
  <div class="app">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">HA</div>
        <div class="brand-name">HIV 取號工具<br><strong>雲端授權管理</strong></div>
      </div>

      <div class="nav-section">總覽</div>
      <nav class="nav">
        <a href="#sec-overview" class="active">
          <svg class="nav-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="2" y="2" width="5" height="5" rx="1"/><rect x="9" y="2" width="5" height="5" rx="1"/><rect x="2" y="9" width="5" height="5" rx="1"/><rect x="9" y="9" width="5" height="5" rx="1"/></svg>
          目前狀態
        </a>
      </nav>

      <div class="nav-section">設定</div>
      <nav class="nav">
        <a href="#sec-passwords">
          <svg class="nav-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="7" width="10" height="7" rx="1.5"/><path d="M5 7V5a3 3 0 0 1 6 0v2"/></svg>
          密碼管理
        </a>
        <a href="#sec-service">
          <svg class="nav-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="8" cy="8" r="6"/><path d="M8 4v4l2.5 2.5"/></svg>
          服務控制
        </a>
      </nav>

      <div class="nav-section">監控</div>
      <nav class="nav">
        <a href="#sec-usage">
          <svg class="nav-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M2 13h12"/><rect x="3" y="8" width="2" height="5"/><rect x="7" y="5" width="2" height="8"/><rect x="11" y="9" width="2" height="4"/></svg>
          使用統計
        </a>
        <a href="#sec-audit">
          <svg class="nav-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="2" width="10" height="12" rx="1"/><path d="M5.5 6h5M5.5 8.5h5M5.5 11h3"/></svg>
          活動紀錄
        </a>
      </nav>

      <div class="sidebar-footer">
        <span class="status-dot" id="nav_status_dot"></span>
        <span id="nav_status_text">載入中…</span>
      </div>
    </aside>

    <main class="main">
      <div class="page-head">
        <h1 class="page-title">控制台</h1>
        <div class="page-meta">
          最近更新 <span id="s_at">—</span>
          <span class="sep">·</span>
          <button class="btn ghost sm" onclick="loadState()">重新整理</button>
        </div>
      </div>

      <!-- ===== 目前狀態 ===== -->
      <section class="section" id="sec-overview">
        <div class="section-head">
          <h2 class="section-title">目前狀態</h2>
          <span class="section-sub">所有 EXE 共用此設定</span>
        </div>
        <div class="card">
          <div class="card-body">
            <div class="state-grid">
              <div class="state-cell">
                <div class="k">服務狀態</div>
                <div class="v" id="s_kill">—</div>
              </div>
              <div class="state-cell">
                <div class="k">程式授權密碼（EXE 登入用）</div>
                <div class="v">
                  <span class="pwd-display" id="s_pwd_plain">—</span>
                  <button class="btn ghost tiny" onclick="togglePwd('pwd')">顯示</button>
                </div>
              </div>
              <div class="state-cell">
                <div class="k">管理員密碼（後台登入用）</div>
                <div class="v">
                  <span class="pwd-display" id="s_admin_plain">—</span>
                  <button class="btn ghost tiny" onclick="togglePwd('admin')">顯示</button>
                </div>
              </div>
              <div class="state-cell" style="grid-column: 1 / -1;">
                <div class="k">停用訊息（顯示給被擋下的使用者）</div>
                <div class="v" id="s_msg" style="color: var(--ink-2); font-size: 13px;">—</div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- ===== 密碼管理 ===== -->
      <section class="section" id="sec-passwords">
        <div class="section-head">
          <h2 class="section-title">密碼管理</h2>
          <span class="section-sub">變更後舊密碼立刻失效</span>
        </div>

        <div class="card">
          <div class="card-header">
            <h3>程式授權密碼 — EXE 登入用</h3>
            <span class="help">改完後所有 EXE 立刻使用新密碼</span>
          </div>
          <div class="card-body">
            <div class="field-row">
              <div class="field">
                <label for="newpwd">新程式授權密碼（至少 4 字）</label>
                <input id="newpwd" type="password" placeholder="輸入新程式授權密碼">
              </div>
              <button class="btn" onclick="setPwd()" id="btn_setpwd">送出</button>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-header">
            <h3>管理員密碼 — 此後台登入用</h3>
            <span class="help" style="color: var(--bad);">改完後下次登入要用新密碼</span>
          </div>
          <div class="card-body">
            <div class="field-row">
              <div class="field">
                <label for="new_admin_pwd">新管理員密碼（至少 4 字）</label>
                <input id="new_admin_pwd" type="password" placeholder="輸入新管理員密碼">
              </div>
              <button class="btn" onclick="setAdminPwd()" id="btn_setadm">送出</button>
            </div>
          </div>
        </div>
      </section>

      <!-- ===== 服務控制 ===== -->
      <section class="section" id="sec-service">
        <div class="section-head">
          <h2 class="section-title">服務控制</h2>
          <span class="section-sub">緊急時停用所有 EXE</span>
        </div>

        <div class="card">
          <div class="card-header">
            <h3>停用訊息</h3>
            <span class="help">被擋下的使用者會看到這則訊息</span>
          </div>
          <div class="card-body">
            <div class="field" style="margin-bottom: 10px;">
              <label for="msg">訊息內容</label>
              <textarea id="msg" placeholder="例：請聯絡 OOO 取得授權"></textarea>
            </div>
            <button class="btn" onclick="setMsg()" id="btn_setmsg">儲存訊息</button>
          </div>
        </div>

        <div class="card">
          <div class="card-header">
            <h3>停用開關（Kill Switch）</h3>
            <span class="help">開啟後所有 EXE 下次啟動會被擋下</span>
          </div>
          <div class="card-body">
            <div style="display: flex; gap: 10px; flex-wrap: wrap;">
              <button class="btn danger" onclick="setKill(1)" id="btn_kill1">
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"><circle cx="8" cy="8" r="6"/><path d="M5 5l6 6"/></svg>
                全部停用
              </button>
              <button class="btn ghost" onclick="setKill(0)" id="btn_kill0">
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 8l3.5 3.5L13 5"/></svg>
                恢復服務
              </button>
            </div>
          </div>
        </div>
      </section>

      <!-- ===== 使用統計 ===== -->
      <section class="section" id="sec-usage">
        <div class="section-head">
          <h2 class="section-title">使用統計</h2>
          <span class="section-sub">每日使用人數與活躍 IP</span>
        </div>

        <div class="card">
          <div class="card-header">
            <h3>每日使用量</h3>
            <span class="help">數字 = 不同 IP 數（≈使用人數）</span>
          </div>
          <div class="card-body">
            <div class="chart-toolbar">
              <select id="chart_days" onchange="loadDaily(false)">
                <option value="7">最近 7 天</option>
                <option value="14" selected>最近 14 天</option>
                <option value="30">最近 30 天</option>
                <option value="60">最近 60 天</option>
              </select>
              <button class="btn ghost sm" onclick="loadDaily(true)">重新整理</button>
            </div>
            <div id="chart"></div>
            <div class="chart-legend">
              <span><span class="swatch" style="background: var(--ok);"></span>成功</span>
              <span><span class="swatch" style="background: var(--bad);"></span>失敗</span>
              <span class="chart-total" id="chart_total">—</span>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-header">
            <h3>活躍 IP — 過去 7 天 Top 10</h3>
          </div>
          <div class="card-body" style="padding: 0;">
            <div class="scroll" style="max-height: 280px; border: 0; border-radius: 0;">
              <table id="top_ip_table">
                <thead>
                  <tr>
                    <th>IP</th>
                    <th>國家</th>
                    <th style="text-align:right">總計</th>
                    <th style="text-align:right">成功</th>
                    <th style="text-align:right">失敗</th>
                    <th>最後使用</th>
                  </tr>
                </thead>
                <tbody></tbody>
              </table>
            </div>
          </div>
        </div>
      </section>

      <!-- ===== 活動紀錄 ===== -->
      <section class="section" id="sec-audit">
        <div class="section-head">
          <h2 class="section-title">活動紀錄</h2>
          <span class="section-sub">每筆登入嘗試的稽核</span>
        </div>

        <div class="card">
          <div class="card-body">
            <div class="filter-row">
              <select id="audit_filter" onchange="loadAudit()">
                <option value="all">全部</option>
                <option value="ok">僅成功</option>
                <option value="fail">僅失敗</option>
              </select>
              <select id="audit_limit" onchange="loadAudit()">
                <option value="20">最近 20 筆</option>
                <option value="50" selected>最近 50 筆</option>
                <option value="100">最近 100 筆</option>
                <option value="200">最近 200 筆</option>
                <option value="500">最近 500 筆</option>
              </select>
              <button class="btn ghost sm" onclick="loadAudit()">重新整理</button>
              <span class="spacer"></span>
              <span class="clear-block">
                清掉
                <input id="audit_clear_days" type="number" value="30" min="0" max="365">
                天前（0 = 全清）
                <button class="btn danger sm" onclick="clearAudit()" id="btn_clear">清除</button>
              </span>
            </div>
            <div class="scroll">
              <table id="audit_table">
                <thead>
                  <tr>
                    <th>時間</th>
                    <th>結果</th>
                    <th>IP</th>
                    <th>國家</th>
                    <th>原因</th>
                    <th>UA</th>
                  </tr>
                </thead>
                <tbody></tbody>
              </table>
            </div>
          </div>
        </div>
      </section>
    </main>
  </div>
</div>

<script>
// ============ PREVIEW MOCK (only active on file:// or non-worker hosts) ============
// Removes itself in production. Detects by absence of the worker host.
(function installMockIfNeeded(){
  const isProd = /workers\\.dev$/.test(location.hostname);
  if (isProd) return;
  const _fetch = window.fetch;
  const state = {
    password_set: true,
    password_plain: 'demo-pass-2026',
    admin_plain: 'admin-demo-9090',
    message: '系統維護中,請稍後再試。',
    killed: false,
    updated_at: new Date().toISOString()
  };
  const ADMIN_OK = 'demo';
  const audit = [];
  const countries = ['TW','TW','TW','JP','US','HK','TW','TW','SG','TW'];
  const uas = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15',
    'HIV-Tool/1.4 (Windows; .NET 4.8)',
    'Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)'
  ];
  const reasons_ok = ['ok','ok','ok'];
  const reasons_fail = ['wrong_password','killed','rate_limited','wrong_password'];
  // Seed 14 days of audit
  const now = Date.now();
  for (let d = 0; d < 14; d++){
    const dayStart = now - d*86400000;
    const ok = Math.floor(8 + Math.random()*22);
    const fail = Math.floor(Math.random()*5);
    for (let i = 0; i < ok; i++){
      audit.push({
        id: audit.length+1,
        ts: new Date(dayStart - Math.random()*86400000).toISOString(),
        ip: '203.74.' + (10+Math.floor(Math.random()*40)) + '.' + Math.floor(Math.random()*255),
        country: countries[Math.floor(Math.random()*countries.length)],
        ua: uas[Math.floor(Math.random()*uas.length)],
        ok: 1, reason: reasons_ok[0]
      });
    }
    for (let i = 0; i < fail; i++){
      audit.push({
        id: audit.length+1,
        ts: new Date(dayStart - Math.random()*86400000).toISOString(),
        ip: '36.229.' + Math.floor(Math.random()*255) + '.' + Math.floor(Math.random()*255),
        country: countries[Math.floor(Math.random()*countries.length)],
        ua: uas[Math.floor(Math.random()*uas.length)],
        ok: 0, reason: reasons_fail[Math.floor(Math.random()*reasons_fail.length)]
      });
    }
  }
  audit.sort((a,b)=> new Date(b.ts) - new Date(a.ts));

  window.fetch = async function(url, opts){
    if (!url.startsWith('/admin/')) return _fetch(url, opts);
    await new Promise(r => setTimeout(r, 220));
    const body = JSON.parse(opts.body || '{}');
    const auth = body.admin_pwd === ADMIN_OK;
    let resp = { ok: false, reason: 'bad_password' };
    if (!auth) return new Response(JSON.stringify(resp), {headers:{'Content-Type':'application/json'}});

    if (url === '/admin/state'){
      resp = { ok: true, state: {...state} };
    } else if (url === '/admin/update'){
      if (body.action === 'set_password'){ state.password_plain = body.value; state.password_set = true; }
      else if (body.action === 'set_admin_password'){ state.admin_plain = body.value; }
      else if (body.action === 'set_message'){ state.message = body.value; }
      else if (body.action === 'set_killed'){ state.killed = !!body.value; }
      state.updated_at = new Date().toISOString();
      resp = { ok: true, msg: '已更新(預覽)' };
    } else if (url === '/admin/audit'){
      let rows = audit.slice();
      if (body.filter === 'ok') rows = rows.filter(r => r.ok);
      else if (body.filter === 'fail') rows = rows.filter(r => !r.ok);
      rows = rows.slice(0, body.limit || 50);
      resp = { ok: true, rows };
    } else if (url === '/admin/audit/stats'){
      const ipMap = new Map();
      const cutoff = Date.now() - 7*86400000;
      audit.filter(r => new Date(r.ts).getTime() > cutoff).forEach(r => {
        const e = ipMap.get(r.ip) || {ip:r.ip, country:r.country, total:0, ok_cnt:0, fail_cnt:0, last_seen:r.ts};
        e.total++; if (r.ok) e.ok_cnt++; else e.fail_cnt++;
        if (new Date(r.ts) > new Date(e.last_seen)) e.last_seen = r.ts;
        ipMap.set(r.ip, e);
      });
      const top = [...ipMap.values()].sort((a,b)=>b.total-a.total).slice(0,10);
      resp = { ok: true, last_24h: 0, last_7d: 0, top_ips: top };
    } else if (url === '/admin/audit/daily'){
      const days = body.days || 14;
      const byDay = new Map();
      audit.forEach(r => {
        const day = r.ts.slice(0,10);
        const e = byDay.get(day) || {day, total:0, ok_cnt:0, fail_cnt:0, ips:new Set()};
        e.total++; if (r.ok) e.ok_cnt++; else e.fail_cnt++;
        e.ips.add(r.ip);
        byDay.set(day, e);
      });
      const rows = [...byDay.values()].map(e => ({...e, uniq_ip: e.ips.size, ips: undefined}));
      resp = { ok: true, days, rows };
    } else if (url === '/admin/audit/clear'){
      const n = body.older_than_days;
      if (n === 0) audit.length = 0;
      else {
        const cutoff = Date.now() - n*86400000;
        for (let i = audit.length-1; i >= 0; i--){
          if (new Date(audit[i].ts).getTime() < cutoff) audit.splice(i,1);
        }
      }
      resp = { ok: true, msg: '已清除(預覽)' };
    }
    return new Response(JSON.stringify(resp), {headers:{'Content-Type':'application/json'}});
  };
  // Hint
  console.info('%c[預覽模式] 管理員密碼輸入 demo','color:#2f6b6a;font-weight:600');
})();

let ADM = '';
let _toastTimer = null;

function toast(msg, kind){
  // kind: 'ok' | 'err' | undefined
  const existing = document.querySelector('.toast');
  if (existing) existing.remove();
  if (_toastTimer) clearTimeout(_toastTimer);
  const t = document.createElement('div');
  t.className = 'toast' + (kind ? ' ' + kind : ' ok');
  t.textContent = msg;
  document.body.appendChild(t);
  _toastTimer = setTimeout(() => t.remove(), 3000);
}

async function api(path, body){
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {})
  });
  return r.json();
}

function withLoading(btnId, fn){
  return async function(){
    const btn = document.getElementById(btnId);
    if (!btn) return fn.apply(this, arguments);
    if (btn.disabled) return;
    const prev = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="btn-spinner"></span>處理中…';
    try { await fn.apply(this, arguments); }
    finally {
      btn.disabled = false;
      btn.innerHTML = prev;
    }
  };
}

async function login(){
  const btn = document.getElementById('login_btn');
  const v = document.getElementById('adm').value;
  if (!v) return;
  if (btn.disabled) return;
  ADM = v;
  const prev = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span class="btn-spinner"></span>登入中…';
  try {
    const r = await api('/admin/state', { admin_pwd: v });
    if (!r.ok) {
      toast(r.reason || '登入失敗', 'err');
      ADM = '';
      return;
    }
    document.getElementById('login_card').classList.add('hidden');
    document.getElementById('main_card').classList.remove('hidden');
    renderState(r.state);
    loadDaily(true);
    loadAudit();
  } finally {
    btn.disabled = false;
    btn.innerHTML = prev;
  }
}

function fmt(ts){
  if (!ts) return '—';
  try {
    let s = String(ts);
    if (!s.includes('T')) s = s.replace(' ', 'T');
    if (!s.endsWith('Z') && !/[+-]\\d{2}:?\\d{2}$/.test(s)) s += 'Z';
    const d = new Date(s);
    if (isNaN(d.getTime())) return ts;
    return d.toLocaleString('zh-TW', { hour12: false });
  } catch { return ts; }
}

async function loadDaily(refreshStats){
  const days = parseInt(document.getElementById('chart_days').value, 10);
  const d = await api('/admin/audit/daily', { admin_pwd: ADM, days });
  if (!d.ok) return toast(d.reason || '失敗', 'err');
  renderChart(d.rows, days);
  if (refreshStats) await loadStats();
}

async function loadStats(){
  const s = await api('/admin/audit/stats', { admin_pwd: ADM });
  if (!s.ok) return;
  const tb = document.querySelector('#top_ip_table tbody');
  tb.innerHTML = '';
  const rows = s.top_ips || [];
  if (!rows.length){
    tb.innerHTML = '<tr class="empty-row"><td colspan="6">過去 7 天沒有活躍 IP</td></tr>';
    return;
  }
  for (const row of rows){
    const tr = document.createElement('tr');
    tr.innerHTML =
      '<td class="ip">' + (row.ip || '—') + '</td>' +
      '<td>' + (row.country || '') + '</td>' +
      '<td class="num">' + (row.total || 0) + '</td>' +
      '<td class="num ok">' + (row.ok_cnt || 0) + '</td>' +
      '<td class="num bad">' + (row.fail_cnt || 0) + '</td>' +
      '<td class="ts">' + fmt(row.last_seen) + '</td>';
    tb.appendChild(tr);
  }
}

function renderChart(rows, days){
  const today = new Date();
  const map = new Map((rows || []).map(r => [r.day, r]));
  const series = [];
  for (let i = days - 1; i >= 0; i--){
    const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() - i);
    const key = d.toISOString().slice(0, 10);
    const r = map.get(key) || { day: key, total: 0, ok_cnt: 0, fail_cnt: 0, uniq_ip: 0 };
    series.push(r);
  }
  const W = 760, H = 220, PAD_L = 36, PAD_R = 8, PAD_T = 18, PAD_B = 38;
  const innerW = W - PAD_L - PAD_R, innerH = H - PAD_T - PAD_B;
  const maxTotal = Math.max(1, ...series.map(s => s.total || 0));
  const slot = innerW / series.length;
  const barW = Math.max(6, slot * 0.66);

  const OK = '#3a8a4f';
  const BAD = '#a13a2c';
  const GRID = '#e5e0d6';
  const AXIS = '#7a8593';

  let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMidYMid meet" style="width:100%;height:auto">';
  // gridlines
  for (let i = 0; i <= 4; i++){
    const y = PAD_T + innerH - (innerH * i / 4);
    const v = Math.round(maxTotal * i / 4);
    svg += '<line x1="' + PAD_L + '" y1="' + y + '" x2="' + (W - PAD_R) + '" y2="' + y + '" stroke="' + GRID + '" stroke-dasharray="2 3" />';
    svg += '<text x="' + (PAD_L - 6) + '" y="' + (y + 3.5) + '" text-anchor="end" font-size="10" fill="' + AXIS + '" font-family="ui-monospace, monospace">' + v + '</text>';
  }
  let totalAll = 0, totalOk = 0, totalFail = 0;
  series.forEach((s, i) => {
    totalAll += s.total || 0; totalOk += s.ok_cnt || 0; totalFail += s.fail_cnt || 0;
    const x = PAD_L + slot * i + (slot - barW) / 2;
    const okH = (s.ok_cnt || 0) / maxTotal * innerH;
    const failH = (s.fail_cnt || 0) / maxTotal * innerH;
    const totalH = okH + failH;
    const yTop = PAD_T + innerH - totalH;
    const tip = s.day + '\\n成功 ' + (s.ok_cnt || 0) + '\\n失敗 ' + (s.fail_cnt || 0) + '\\n不同 IP ' + (s.uniq_ip || 0);
    if (okH > 0)
      svg += '<rect x="' + x + '" y="' + (PAD_T + innerH - okH) + '" width="' + barW + '" height="' + okH + '" fill="' + OK + '" rx="1.5"><title>' + tip + '</title></rect>';
    if (failH > 0)
      svg += '<rect x="' + x + '" y="' + (PAD_T + innerH - okH - failH) + '" width="' + barW + '" height="' + failH + '" fill="' + BAD + '" rx="1.5"><title>' + tip + '</title></rect>';
    if ((s.uniq_ip || 0) > 0)
      svg += '<text x="' + (x + barW / 2) + '" y="' + (yTop - 5) + '" text-anchor="middle" font-size="10" fill="#1f4d4c" font-weight="600" font-family="ui-monospace, monospace">' + s.uniq_ip + '</text>';
    const lbl = s.day.slice(5);
    // skip every-other label if too dense
    const showLbl = days <= 14 || i % 2 === 0;
    if (showLbl)
      svg += '<text x="' + (x + barW / 2) + '" y="' + (H - PAD_B + 16) + '" text-anchor="middle" font-size="9" fill="' + AXIS + '" font-family="ui-monospace, monospace">' + lbl + '</text>';
  });
  svg += '<line x1="' + PAD_L + '" y1="' + (PAD_T + innerH) + '" x2="' + (W - PAD_R) + '" y2="' + (PAD_T + innerH) + '" stroke="' + AXIS + '" />';
  svg += '</svg>';
  document.getElementById('chart').innerHTML = svg;
  document.getElementById('chart_total').textContent =
    days + ' 天總計 ' + totalAll + ' 筆 · 成功 ' + totalOk + ' / 失敗 ' + totalFail;
}

async function loadAudit(){
  const filter = document.getElementById('audit_filter').value;
  const limit = parseInt(document.getElementById('audit_limit').value, 10);
  const r = await api('/admin/audit', { admin_pwd: ADM, filter, limit });
  if (!r.ok) return toast(r.reason || '失敗', 'err');
  const tb = document.querySelector('#audit_table tbody');
  tb.innerHTML = '';
  if (!r.rows.length){
    tb.innerHTML = '<tr class="empty-row"><td colspan="6">沒有紀錄</td></tr>';
    return;
  }
  for (const row of r.rows){
    const tr = document.createElement('tr');
    const okPill = row.ok
      ? '<span class="pill ok"><span class="dot"></span>成功</span>'
      : '<span class="pill bad"><span class="dot"></span>失敗</span>';
    const ua = (row.ua || '').replace(/</g, '&lt;');
    tr.innerHTML =
      '<td class="ts">' + fmt(row.ts) + '</td>' +
      '<td>' + okPill + '</td>' +
      '<td class="ip">' + (row.ip || '—') + '</td>' +
      '<td>' + (row.country || '') + '</td>' +
      '<td>' + (row.reason || '') + '</td>' +
      '<td class="ua" title="' + ua + '">' + ua + '</td>';
    tb.appendChild(tr);
  }
}

window.clearAudit = withLoading('btn_clear', async function(){
  const n = parseInt(document.getElementById('audit_clear_days').value, 10);
  if (isNaN(n) || n < 0) return toast('請輸入 0 或以上的數字', 'err');
  if (!confirm(n === 0 ? '確定全部清光所有紀錄？' : '確定清掉 ' + n + ' 天前的紀錄？')) return;
  const r = await api('/admin/audit/clear', { admin_pwd: ADM, older_than_days: n });
  if (!r.ok) return toast(r.reason || '失敗', 'err');
  toast(r.msg || '已清除', 'ok');
  loadDaily(true);
  loadAudit();
});

async function loadState(){
  const r = await api('/admin/state', { admin_pwd: ADM });
  if (!r.ok) return toast(r.reason || '失敗', 'err');
  renderState(r.state);
}

let _pwdShown = { pwd: false, admin: false };
let _curState = {};

function maskPwd(s){
  if (!s) return null;
  return '•'.repeat(Math.min(12, s.length));
}

function setPwdDisplay(elId, plain, shown){
  const el = document.getElementById(elId);
  if (!plain){
    el.textContent = '尚未存明文（改一次密碼後才看得到）';
    el.classList.add('empty');
    return;
  }
  el.classList.remove('empty');
  el.textContent = shown ? plain : maskPwd(plain);
}

function togglePwd(which){
  _pwdShown[which] = !_pwdShown[which];
  const elId = which === 'pwd' ? 's_pwd_plain' : 's_admin_plain';
  const v = which === 'pwd' ? _curState.password_plain : _curState.admin_plain;
  setPwdDisplay(elId, v, _pwdShown[which]);
}

function renderState(s){
  _curState = s;
  _pwdShown = { pwd: false, admin: false };
  setPwdDisplay('s_pwd_plain', s.password_plain, false);
  setPwdDisplay('s_admin_plain', s.admin_plain, false);

  const kill = document.getElementById('s_kill');
  kill.innerHTML = s.killed
    ? '<span class="pill bad"><span class="dot"></span>已停用</span>'
    : '<span class="pill ok"><span class="dot"></span>服務中</span>';

  const navDot = document.getElementById('nav_status_dot');
  const navTxt = document.getElementById('nav_status_text');
  if (navDot && navTxt){
    navDot.className = 'status-dot' + (s.killed ? ' bad' : '');
    navTxt.textContent = s.killed ? '服務已停用' : '服務運作中';
  }

  const msgEl = document.getElementById('s_msg');
  if (s.message){
    msgEl.textContent = s.message;
    msgEl.style.fontStyle = 'normal';
    msgEl.style.color = 'var(--ink-2)';
  } else {
    msgEl.textContent = '（未設定）';
    msgEl.style.fontStyle = 'italic';
    msgEl.style.color = 'var(--ink-3)';
  }
  document.getElementById('s_at').textContent = fmt(s.updated_at) || '—';
  document.getElementById('msg').value = s.message || '';
}

window.setPwd = withLoading('btn_setpwd', async function(){
  const v = document.getElementById('newpwd').value;
  if (!v || v.length < 4) return toast('密碼至少 4 字', 'err');
  const r = await api('/admin/update', { admin_pwd: ADM, action: 'set_password', value: v });
  if (!r.ok) return toast(r.reason || '失敗', 'err');
  document.getElementById('newpwd').value = '';
  toast(r.msg || '已更新', 'ok');
  loadState();
});

window.setAdminPwd = withLoading('btn_setadm', async function(){
  const v = document.getElementById('new_admin_pwd').value;
  if (!v || v.length < 4) return toast('密碼至少 4 字', 'err');
  const masked = '•'.repeat(v.length) + ' (' + v.length + ' 字)';
  if (!confirm('確定把管理員密碼改成 ' + masked + '？下次登入這個後台要用新密碼。')) return;
  const r = await api('/admin/update', { admin_pwd: ADM, action: 'set_admin_password', value: v });
  if (!r.ok) return toast(r.reason || '失敗', 'err');
  document.getElementById('new_admin_pwd').value = '';
  ADM = v;
  toast(r.msg || '已更新', 'ok');
  loadState();
});

window.setMsg = withLoading('btn_setmsg', async function(){
  const v = document.getElementById('msg').value;
  const r = await api('/admin/update', { admin_pwd: ADM, action: 'set_message', value: v });
  if (!r.ok) return toast(r.reason || '失敗', 'err');
  toast(r.msg || '已更新', 'ok');
  loadState();
});

async function setKill(k){
  const btnId = k ? 'btn_kill1' : 'btn_kill0';
  const btn = document.getElementById(btnId);
  if (btn.disabled) return;
  const prev = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<span class="btn-spinner"></span>處理中…';
  try {
    const r = await api('/admin/update', { admin_pwd: ADM, action: 'set_killed', value: !!k });
    if (!r.ok) return toast(r.reason || '失敗', 'err');
    toast(r.msg || '已更新', 'ok');
    loadState();
  } finally {
    btn.disabled = false;
    btn.innerHTML = prev;
  }
}

document.getElementById('adm').addEventListener('keydown', e => {
  if (e.key === 'Enter') login();
});

// Sidebar nav active-state on scroll + click
(function setupNav(){
  const links = document.querySelectorAll('.nav a[href^="#"]');
  links.forEach(a => {
    a.addEventListener('click', e => {
      const id = a.getAttribute('href').slice(1);
      const target = document.getElementById(id);
      if (target){
        e.preventDefault();
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        links.forEach(l => l.classList.remove('active'));
        a.classList.add('active');
      }
    });
  });
  // Update active link based on viewport position
  const sections = ['sec-overview','sec-passwords','sec-service','sec-usage','sec-audit'];
  let ticking = false;
  function update(){
    ticking = false;
    let active = sections[0];
    for (const id of sections){
      const el = document.getElementById(id);
      if (!el) continue;
      const rect = el.getBoundingClientRect();
      if (rect.top <= 120) active = id;
    }
    links.forEach(l => {
      l.classList.toggle('active', l.getAttribute('href') === '#' + active);
    });
  }
  window.addEventListener('scroll', () => {
    if (!ticking){ requestAnimationFrame(update); ticking = true; }
  }, { passive: true });
})();
</script>
</body>
</html>
`;
