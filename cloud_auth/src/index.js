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
    return jsonReply({ ok: true, msg: '主密碼已更新' });
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
  *{box-sizing:border-box}
  body{font:14px/1.6 "Microsoft JhengHei","Segoe UI",sans-serif;background:#eef5fa;color:#1a2a3a;margin:0;padding:24px}
  .wrap{max-width:880px;margin:0 auto}
  h1{margin:0 0 8px;color:#1565c0;font-size:22px}
  .sub{color:#666;margin-bottom:24px}
  .card{background:#fff;border-radius:8px;padding:18px 22px;margin-bottom:16px;box-shadow:0 2px 8px rgba(21,101,192,.08)}
  .card h2{margin:0 0 12px;font-size:15px;color:#0d47a1;border-bottom:2px solid #1565c0;padding-bottom:6px}
  label{display:block;margin:8px 0 4px;font-weight:600;color:#37474f}
  input[type=text],input[type=password],textarea{width:100%;padding:8px 10px;border:1px solid #cfd8dc;border-radius:4px;font:inherit}
  textarea{min-height:60px;resize:vertical}
  button{background:#1565c0;color:#fff;border:0;padding:8px 16px;border-radius:4px;font:inherit;font-weight:600;cursor:pointer;margin-top:8px}
  button:hover{background:#0d47a1}
  button.danger{background:#c62828}
  button.danger:hover{background:#8b0000}
  button.ghost{background:transparent;color:#1565c0;border:1px solid #1565c0}
  button.ghost:hover{background:#1565c0;color:#fff}
  .state{display:grid;grid-template-columns:120px 1fr;gap:6px 12px;font-size:13px}
  .state .k{color:#546e7a}
  .state .v{font-weight:600}
  .pill{display:inline-block;padding:2px 10px;border-radius:10px;font-size:12px;font-weight:600}
  .pill.ok{background:#c8e6c9;color:#1b5e20}
  .pill.bad{background:#ffcdd2;color:#b71c1c}
  .pill.muted{background:#eceff1;color:#546e7a}
  .row{display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap}
  .row>:first-child{flex:1}
  .toast{position:fixed;top:20px;right:20px;background:#0d47a1;color:#fff;padding:12px 20px;border-radius:6px;box-shadow:0 4px 12px rgba(0,0,0,.2);z-index:99;animation:slide .3s ease;max-width:400px}
  .toast.err{background:#c62828}
  @keyframes slide{from{transform:translateX(120%)}to{transform:translateX(0)}}
  .hidden{display:none}
  .stat-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
  .stat-box{background:#f5f9fc;padding:10px 14px;border-radius:6px;border-left:3px solid #1565c0}
  .stat-box .label{color:#546e7a;font-size:12px}
  .stat-box .num{font-size:18px;font-weight:700;color:#0d47a1}
  .stat-box .num.bad{color:#c62828}
  .stat-box .num.ok{color:#1b5e20}
  table{width:100%;border-collapse:collapse;font-size:12px;margin-top:10px}
  th{background:#1565c0;color:#fff;padding:6px 8px;text-align:left;font-weight:600;position:sticky;top:0}
  td{padding:5px 8px;border-bottom:1px solid #eceff1;vertical-align:top}
  tr:hover td{background:#f5f9fc}
  td.ip{font-family:Consolas,monospace}
  td.ts{white-space:nowrap;color:#546e7a}
  td.ua{color:#546e7a;font-size:11px;max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .scroll{max-height:380px;overflow-y:auto;border:1px solid #cfd8dc;border-radius:4px}
  select{padding:6px 8px;border:1px solid #cfd8dc;border-radius:4px;font:inherit}
</style>
</head>
<body>
<div class="wrap">
  <h1>🔐 HIV 取號工具 — 雲端授權管理</h1>
  <div class="sub">控制所有 EXE 是否能登入。改密碼後所有舊密碼立刻失效。</div>

  <div id="login_card" class="card">
    <h2>管理員登入</h2>
    <label>管理員密碼</label>
    <input id="adm" type="password" autocomplete="current-password">
    <button onclick="login()">登入</button>
  </div>

  <div id="main_card" class="hidden">
    <div class="card">
      <h2>目前狀態</h2>
      <div class="state">
        <div class="k">主密碼（EXE）</div>
        <div class="v">
          <span id="s_pwd_plain" style="font-family:Consolas,monospace;background:#f5f9fc;padding:2px 8px;border-radius:3px"></span>
          <button class="ghost" style="padding:2px 8px;font-size:11px;margin-left:6px" onclick="togglePwd('pwd')">👁 顯示</button>
        </div>
        <div class="k">管理員密碼</div>
        <div class="v">
          <span id="s_admin_plain" style="font-family:Consolas,monospace;background:#f5f9fc;padding:2px 8px;border-radius:3px"></span>
          <button class="ghost" style="padding:2px 8px;font-size:11px;margin-left:6px" onclick="togglePwd('admin')">👁 顯示</button>
        </div>
        <div class="k">服務狀態</div><div class="v" id="s_kill">—</div>
        <div class="k">停用訊息</div><div class="v" id="s_msg">—</div>
        <div class="k">最近更新</div><div class="v" id="s_at">—</div>
      </div>
      <button class="ghost" onclick="loadState()" style="margin-top:14px">重新整理</button>
    </div>

    <div class="card">
      <h2>更新主密碼（EXE 登入用）</h2>
      <p style="margin:0 0 8px;color:#666;font-size:13px">所有 EXE 都會立刻使用新密碼。舊密碼立刻失效。</p>
      <label>新密碼（至少 4 字）</label>
      <input id="newpwd" type="password">
      <button onclick="setPwd()">送出新密碼</button>
    </div>

    <div class="card">
      <h2>更新管理員密碼（這個後台用）</h2>
      <p style="margin:0 0 8px;color:#c62828;font-size:13px">⚠ 改完後，下次登入這個後台要用新密碼。</p>
      <label>新管理員密碼（至少 4 字）</label>
      <input id="new_admin_pwd" type="password">
      <button onclick="setAdminPwd()">送出新管理員密碼</button>
    </div>

    <div class="card">
      <h2>停用訊息</h2>
      <p style="margin:0 0 8px;color:#666;font-size:13px">「停用所有 EXE」開啟時，使用者會看到這則訊息。</p>
      <textarea id="msg" placeholder="例：請聯絡 OOO 取得授權"></textarea>
      <button onclick="setMsg()">儲存訊息</button>
    </div>

    <div class="card">
      <h2>停用開關（Kill Switch）</h2>
      <p style="margin:0 0 8px;color:#666;font-size:13px">開啟後，所有 EXE 下次啟動會被擋下。</p>
      <div class="row">
        <button class="danger" onclick="setKill(1)">🛑 全部停用</button>
        <button onclick="setKill(0)">✅ 恢復服務</button>
      </div>
    </div>

    <div class="card">
      <h2>📊 每日使用量</h2>
      <div class="row" style="gap:6px;margin-bottom:8px">
        <select id="chart_days" onchange="loadDaily(false)">
          <option value="7">最近 7 天</option>
          <option value="14" selected>最近 14 天</option>
          <option value="30">最近 30 天</option>
          <option value="60">最近 60 天</option>
        </select>
        <button class="ghost" onclick="loadDaily(true)">重新整理</button>
        <span style="margin-left:auto;color:#546e7a;font-size:12px">數字 = 不同 IP 數（≈使用人數）</span>
      </div>
      <div id="chart"></div>
      <div class="row" style="margin-top:6px;font-size:12px;color:#546e7a;gap:14px">
        <span><span style="display:inline-block;width:12px;height:12px;background:#43a047;vertical-align:middle"></span> 成功</span>
        <span><span style="display:inline-block;width:12px;height:12px;background:#e53935;vertical-align:middle"></span> 失敗</span>
        <span style="margin-left:auto" id="chart_total">—</span>
      </div>
      <h3 style="margin:18px 0 6px;font-size:13px;color:#546e7a">📌 活躍 IP（過去 7 天 Top 10）</h3>
      <div class="scroll" style="max-height:200px">
        <table id="top_ip_table">
          <thead><tr><th>IP</th><th>國</th><th>總</th><th>成</th><th>敗</th><th>最後</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <h2>📋 活動紀錄</h2>
      <div class="row" style="gap:6px;align-items:center;flex-wrap:wrap">
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
        <button class="ghost" onclick="loadAudit()">重新整理</button>
        <span style="display:inline-flex;align-items:center;gap:4px;margin-left:auto">
          <span style="color:#546e7a;font-size:12px">清掉</span>
          <input id="audit_clear_days" type="number" value="30" min="0" max="365" style="width:60px;padding:4px 6px">
          <span style="color:#546e7a;font-size:12px">天前（0=全清）</span>
          <button class="danger" onclick="clearAudit()">清</button>
        </span>
      </div>
      <div class="scroll" style="margin-top:10px">
        <table id="audit_table">
          <thead><tr><th>時間</th><th>結果</th><th>IP</th><th>國</th><th>原因</th><th>UA</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<script>
let ADM = '';
function toast(msg, err){
  const t = document.createElement('div');
  t.className = 'toast' + (err ? ' err' : '');
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}
async function api(path, body){
  const r = await fetch(path, {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body || {})
  });
  return r.json();
}
async function login(){
  const v = document.getElementById('adm').value;
  if (!v) return;
  ADM = v;
  const r = await api('/admin/state', {admin_pwd: v});
  if (!r.ok) {
    toast(r.reason || '登入失敗', true);
    ADM = '';
    return;
  }
  document.getElementById('login_card').classList.add('hidden');
  document.getElementById('main_card').classList.remove('hidden');
  renderState(r.state);
  loadDaily(true);  // 初次：含 Top IP
  loadAudit();
}
function fmt(ts){
  if(!ts) return '—';
  try{
    let s = String(ts);
    // 兩種格式：ISO ("2026-05-06T16:47:35.735Z") 或 SQLite ("2026-05-06 16:47:35")
    if (!s.includes('T')) s = s.replace(' ', 'T');
    if (!s.endsWith('Z') && !/[+-]\\d{2}:?\\d{2}$/.test(s)) s += 'Z';
    const d = new Date(s);
    if (isNaN(d.getTime())) return ts;
    return d.toLocaleString('zh-TW',{hour12:false});
  }catch{return ts;}
}
// v1.0.43 L1：daily 只拉每日資料；stats（Top IP，固定 7 天）獨立呼叫
async function loadDaily(refreshStats){
  const days = parseInt(document.getElementById('chart_days').value, 10);
  const d = await api('/admin/audit/daily', {admin_pwd: ADM, days});
  if (!d.ok) return toast(d.reason || '失敗', true);
  renderChart(d.rows, days);
  if (refreshStats) await loadStats();
}
async function loadStats(){
  const s = await api('/admin/audit/stats', {admin_pwd: ADM});
  if (!s.ok) return;
  const tb = document.querySelector('#top_ip_table tbody');
  tb.innerHTML = '';
  for (const row of (s.top_ips || [])){
    const tr = document.createElement('tr');
    tr.innerHTML = '<td class="ip">'+(row.ip||'—')+'</td>'+
                   '<td>'+(row.country||'')+'</td>'+
                   '<td>'+(row.total||0)+'</td>'+
                   '<td style="color:#1b5e20">'+(row.ok_cnt||0)+'</td>'+
                   '<td style="color:#c62828">'+(row.fail_cnt||0)+'</td>'+
                   '<td class="ts">'+fmt(row.last_seen)+'</td>';
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
    const r = map.get(key) || {day:key, total:0, ok_cnt:0, fail_cnt:0, uniq_ip:0};
    series.push(r);
  }
  const W = 760, H = 220, PAD_L = 36, PAD_R = 8, PAD_T = 18, PAD_B = 38;
  const innerW = W - PAD_L - PAD_R, innerH = H - PAD_T - PAD_B;
  const maxTotal = Math.max(1, ...series.map(s => s.total || 0));
  const slot = innerW / series.length;
  const barW = Math.max(6, slot * 0.7);
  let svg = '<svg viewBox="0 0 '+W+' '+H+'" style="width:100%;height:auto;background:#fafafa;border-radius:4px">';
  for (let i = 0; i <= 4; i++){
    const y = PAD_T + innerH - (innerH * i / 4);
    const v = Math.round(maxTotal * i / 4);
    svg += '<line x1="'+PAD_L+'" y1="'+y+'" x2="'+(W-PAD_R)+'" y2="'+y+'" stroke="#eee" />';
    svg += '<text x="'+(PAD_L-4)+'" y="'+(y+3.5)+'" text-anchor="end" font-size="10" fill="#888">'+v+'</text>';
  }
  let totalAll = 0, totalOk = 0, totalFail = 0;
  series.forEach((s, i) => {
    totalAll += s.total||0; totalOk += s.ok_cnt||0; totalFail += s.fail_cnt||0;
    const x = PAD_L + slot * i + (slot - barW) / 2;
    const okH = (s.ok_cnt || 0) / maxTotal * innerH;
    const failH = (s.fail_cnt || 0) / maxTotal * innerH;
    const totalH = okH + failH;
    const yTop = PAD_T + innerH - totalH;
    const tip = s.day + '\\n成功 ' + (s.ok_cnt||0) + '\\n失敗 ' + (s.fail_cnt||0) + '\\n不同 IP ' + (s.uniq_ip||0);
    if (okH > 0)
      svg += '<rect x="'+x+'" y="'+(PAD_T+innerH-okH)+'" width="'+barW+'" height="'+okH+'" fill="#43a047"><title>'+tip+'</title></rect>';
    if (failH > 0)
      svg += '<rect x="'+x+'" y="'+(PAD_T+innerH-okH-failH)+'" width="'+barW+'" height="'+failH+'" fill="#e53935"><title>'+tip+'</title></rect>';
    if ((s.uniq_ip||0) > 0)
      svg += '<text x="'+(x+barW/2)+'" y="'+(yTop-4)+'" text-anchor="middle" font-size="10" fill="#0d47a1" font-weight="600">'+s.uniq_ip+'</text>';
    const lbl = s.day.slice(5);
    svg += '<text x="'+(x+barW/2)+'" y="'+(H-PAD_B+14)+'" text-anchor="middle" font-size="9" fill="#666">'+lbl+'</text>';
  });
  svg += '<line x1="'+PAD_L+'" y1="'+(PAD_T+innerH)+'" x2="'+(W-PAD_R)+'" y2="'+(PAD_T+innerH)+'" stroke="#aaa" />';
  svg += '</svg>';
  document.getElementById('chart').innerHTML = svg;
  document.getElementById('chart_total').textContent =
    days+' 天總計：'+totalAll+' 筆（成功 '+totalOk+' / 失敗 '+totalFail+'）';
}
async function loadAudit(){
  const filter = document.getElementById('audit_filter').value;
  const limit = parseInt(document.getElementById('audit_limit').value, 10);
  const r = await api('/admin/audit', {admin_pwd: ADM, filter, limit});
  if (!r.ok) return toast(r.reason || '失敗', true);
  const tb = document.querySelector('#audit_table tbody');
  tb.innerHTML = '';
  for (const row of r.rows){
    const tr = document.createElement('tr');
    const okPill = row.ok
      ? '<span class="pill ok">✓</span>'
      : '<span class="pill bad">✗</span>';
    const ua = (row.ua || '').replace(/</g,'&lt;');
    tr.innerHTML = '<td class="ts">'+fmt(row.ts)+'</td>'+
                   '<td>'+okPill+'</td>'+
                   '<td class="ip">'+(row.ip||'—')+'</td>'+
                   '<td>'+(row.country||'')+'</td>'+
                   '<td>'+(row.reason||'')+'</td>'+
                   '<td class="ua" title="'+ua+'">'+ua+'</td>';
    tb.appendChild(tr);
  }
  if (!r.rows.length){
    tb.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#999;padding:20px">沒有紀錄</td></tr>';
  }
}
async function clearAudit(){
  const n = parseInt(document.getElementById('audit_clear_days').value, 10);
  if (isNaN(n) || n < 0) return toast('請輸入 0 或以上的數字', true);
  if (!confirm(n === 0 ? '確定全部清光所有紀錄？' : '確定清掉 ' + n + ' 天前的紀錄？')) return;
  const r = await api('/admin/audit/clear', {admin_pwd: ADM, older_than_days: n});
  if (!r.ok) return toast(r.reason || '失敗', true);
  toast(r.msg || '已清');
  loadDaily(true);  // 清過後 Top IP 也要重抓
  loadAudit();
}
async function loadState(){
  const r = await api('/admin/state', {admin_pwd: ADM});
  if (!r.ok) return toast(r.reason || '失敗', true);
  renderState(r.state);
}
let _pwdShown = {pwd:false, admin:false};
let _curState = {};
function maskPwd(s){
  if (!s) return '(尚未存明文 — 改一次密碼後才看得到)';
  return '•'.repeat(Math.min(12, s.length));
}
function togglePwd(which){
  _pwdShown[which] = !_pwdShown[which];
  const elId = which === 'pwd' ? 's_pwd_plain' : 's_admin_plain';
  const v = which === 'pwd' ? _curState.password_plain : _curState.admin_plain;
  document.getElementById(elId).textContent = _pwdShown[which] ? (v || '(尚未存明文)') : maskPwd(v);
}
function renderState(s){
  _curState = s;
  document.getElementById('s_pwd_plain').textContent = maskPwd(s.password_plain);
  document.getElementById('s_admin_plain').textContent = maskPwd(s.admin_plain);
  _pwdShown = {pwd:false, admin:false};
  const kill = document.getElementById('s_kill');
  kill.innerHTML = s.killed
    ? '<span class="pill bad">🛑 已停用</span>'
    : '<span class="pill ok">✅ 服務中</span>';
  document.getElementById('s_msg').textContent = s.message || '(空)';
  document.getElementById('s_at').textContent = fmt(s.updated_at) || '—';
  document.getElementById('msg').value = s.message || '';
}
async function setPwd(){
  const v = document.getElementById('newpwd').value;
  if (!v || v.length < 4) return toast('密碼至少 4 字', true);
  const r = await api('/admin/update', {admin_pwd: ADM, action:'set_password', value:v});
  if (!r.ok) return toast(r.reason || '失敗', true);
  document.getElementById('newpwd').value = '';
  toast(r.msg || '已更新');
  loadState();
}
async function setAdminPwd(){
  const v = document.getElementById('new_admin_pwd').value;
  if (!v || v.length < 4) return toast('密碼至少 4 字', true);
  // v1.0.43 L2：confirm 不顯示明文，避免路人看到
  const masked = '•'.repeat(v.length) + ' (' + v.length + ' 字)';
  if (!confirm('確定把管理員密碼改成 ' + masked + '？下次登入這個後台要用新密碼。')) return;
  const r = await api('/admin/update', {admin_pwd: ADM, action:'set_admin_password', value:v});
  if (!r.ok) return toast(r.reason || '失敗', true);
  document.getElementById('new_admin_pwd').value = '';
  ADM = v;  // 更新內部 token，避免下個 API 失敗
  toast(r.msg || '已更新');
  loadState();
}
async function setMsg(){
  const v = document.getElementById('msg').value;
  const r = await api('/admin/update', {admin_pwd: ADM, action:'set_message', value:v});
  if (!r.ok) return toast(r.reason || '失敗', true);
  toast(r.msg || '已更新');
  loadState();
}
async function setKill(k){
  const r = await api('/admin/update', {admin_pwd: ADM, action:'set_killed', value: !!k});
  if (!r.ok) return toast(r.reason || '失敗', true);
  toast(r.msg || '已更新');
  loadState();
}
document.getElementById('adm').addEventListener('keydown', e => {
  if (e.key === 'Enter') login();
});
</script>
</body>
</html>`;
