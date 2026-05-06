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
      if (path === '/' || path === '/health') {
        return new Response('hiv-auth ok', { status: 200 });
      }
      return new Response('Not Found', { status: 404 });
    } catch (e) {
      return jsonReply({ ok: false, reason: 'server error: ' + e.message }, 500);
    }
  },
};

// ── EXE 驗證 ──────────────────────────────────
async function handleVerify(request, env) {
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
  const hash = await sha256Hex(pwd);
  if (hash === row.password_hash) {
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
    const hash = await sha256Hex(value);
    await env.DB.prepare(
      'UPDATE auth SET password_hash = ?1, password_plain = ?2, updated_at = ?3 WHERE id = 1'
    )
      .bind(hash, value, now)
      .run();
    return jsonReply({ ok: true, msg: '主密碼已更新' });
  }

  if (action === 'set_admin_password') {
    if (!value || typeof value !== 'string' || value.length < 4) {
      return jsonReply({ ok: false, reason: '密碼至少 4 字' }, 400);
    }
    const hash = await sha256Hex(value);
    await env.DB.prepare(
      'UPDATE auth SET password_hash = ?1, password_plain = ?2, updated_at = ?3 WHERE id = 2'
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

// ── 管理員：清空 audit ───────────────────────
async function handleAdminAuditClear(request, env) {
  const body = await request.json().catch(() => ({}));
  if (!(await checkAdmin(body.admin_pwd, env))) {
    return jsonReply({ ok: false, reason: '管理員密碼錯誤' }, 401);
  }
  const days = parseInt(body.older_than_days, 10);
  if (days > 0) {
    const r = await env.DB.prepare(
      `DELETE FROM audit WHERE ts < datetime('now', ?1)`
    )
      .bind(`-${days} days`)
      .run();
    return jsonReply({ ok: true, msg: `已清掉 ${days} 天前的紀錄`, meta: r.meta });
  }
  // 全清
  const r = await env.DB.prepare(`DELETE FROM audit`).run();
  return jsonReply({ ok: true, msg: '全部紀錄已清空', meta: r.meta });
}

// ── 工具 ───────────────────────────────────────
// v1.0.43：admin pwd 改放 D1 row id=2，wrangler secret 留作 fallback
async function checkAdmin(pwd, env) {
  if (!pwd) return false;
  const h = await sha256Hex(pwd);
  try {
    const row = await env.DB.prepare(
      'SELECT password_hash FROM auth WHERE id = 2'
    ).first();
    if (row && row.password_hash) return h === row.password_hash;
  } catch {
    /* fall through to env */
  }
  if (env.ADMIN_PASSWORD_HASH) return h === env.ADMIN_PASSWORD_HASH;
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
      <h2>📊 使用統計</h2>
      <div class="row" style="margin-bottom:10px">
        <h3 style="margin:0;font-size:13px;color:#546e7a">最近 24 小時</h3>
      </div>
      <div class="stat-grid" id="stat_24h">
        <div class="stat-box"><div class="label">總請求</div><div class="num" id="t24_total">—</div></div>
        <div class="stat-box"><div class="label">不同 IP</div><div class="num" id="t24_ip">—</div></div>
        <div class="stat-box"><div class="label">成功</div><div class="num ok" id="t24_ok">—</div></div>
        <div class="stat-box"><div class="label">失敗</div><div class="num bad" id="t24_fail">—</div></div>
      </div>
      <div class="row" style="margin:14px 0 10px">
        <h3 style="margin:0;font-size:13px;color:#546e7a">最近 7 天</h3>
      </div>
      <div class="stat-grid" id="stat_7d">
        <div class="stat-box"><div class="label">總請求</div><div class="num" id="t7_total">—</div></div>
        <div class="stat-box"><div class="label">不同 IP</div><div class="num" id="t7_ip">—</div></div>
        <div class="stat-box"><div class="label">成功</div><div class="num ok" id="t7_ok">—</div></div>
        <div class="stat-box"><div class="label">失敗</div><div class="num bad" id="t7_fail">—</div></div>
      </div>
      <h3 style="margin:14px 0 6px;font-size:13px;color:#546e7a">📌 活躍 IP（過去 7 天 Top 10）</h3>
      <div class="scroll" style="max-height:200px">
        <table id="top_ip_table">
          <thead><tr><th>IP</th><th>國</th><th>總</th><th>成</th><th>敗</th><th>最後</th></tr></thead>
          <tbody></tbody>
        </table>
      </div>
      <button class="ghost" onclick="loadStats()" style="margin-top:10px">重新整理統計</button>
    </div>

    <div class="card">
      <h2>📋 活動紀錄</h2>
      <div class="row" style="gap:6px">
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
        <button class="danger" onclick="clearAudit()">清空舊紀錄</button>
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
  loadStats();
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
async function loadStats(){
  const r = await api('/admin/audit/stats', {admin_pwd: ADM});
  if (!r.ok) return toast(r.reason || '失敗', true);
  const a = r.last_24h || {}, b = r.last_7d || {};
  t24_total.textContent = a.total || 0;
  t24_ip.textContent = a.uniq_ip || 0;
  t24_ok.textContent = a.ok_cnt || 0;
  t24_fail.textContent = a.fail_cnt || 0;
  t7_total.textContent = b.total || 0;
  t7_ip.textContent = b.uniq_ip || 0;
  t7_ok.textContent = b.ok_cnt || 0;
  t7_fail.textContent = b.fail_cnt || 0;
  const tb = document.querySelector('#top_ip_table tbody');
  tb.innerHTML = '';
  for (const row of (r.top_ips || [])){
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
  const days = prompt('清掉多少天前的紀錄？（輸入數字；輸入 0 全清）', '30');
  if (days === null) return;
  const n = parseInt(days, 10);
  if (isNaN(n) || n < 0) return toast('請輸入有效數字', true);
  if (!confirm(n === 0 ? '確定全部清光？' : '確定清掉 ' + n + ' 天前的紀錄？')) return;
  const r = await api('/admin/audit/clear', {admin_pwd: ADM, older_than_days: n});
  if (!r.ok) return toast(r.reason || '失敗', true);
  toast(r.msg || '已清');
  loadStats();
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
  if (!confirm('確定要把管理員密碼改成「' + v + '」？下次登入這個後台要用新密碼。')) return;
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
