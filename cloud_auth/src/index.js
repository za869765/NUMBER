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
    return jsonReply({ ok: false, reason: '格式錯誤' }, 400);
  }
  const pwd = (body && body.password) || '';
  if (!pwd) {
    return jsonReply({ ok: false, reason: '密碼空白' }, 400);
  }
  const row = await env.DB.prepare(
    'SELECT password_hash, message, killed FROM auth WHERE id = 1'
  ).first();
  if (!row || !row.password_hash) {
    return jsonReply({ ok: false, reason: '伺服器尚未設定密碼，請聯絡管理員' }, 503);
  }
  if (row.killed) {
    return jsonReply({ ok: false, reason: row.message || '本服務已停用，請聯絡管理員' }, 403);
  }
  const hash = await sha256Hex(pwd);
  if (hash === row.password_hash) {
    return jsonReply({ ok: true });
  }
  return jsonReply({ ok: false, reason: '密碼錯誤' }, 401);
}

// ── 管理員：讀狀態 ────────────────────────────
async function handleAdminState(request, env) {
  const body = await request.json().catch(() => ({}));
  if (!(await checkAdmin(body.admin_pwd, env))) {
    return jsonReply({ ok: false, reason: '管理員密碼錯誤' }, 401);
  }
  const row =
    (await env.DB.prepare(
      'SELECT password_hash, message, killed, updated_at FROM auth WHERE id = 1'
    ).first()) || {};
  return jsonReply({
    ok: true,
    state: {
      password_set: !!row.password_hash,
      message: row.message || '',
      killed: !!row.killed,
      updated_at: row.updated_at || '',
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
      'UPDATE auth SET password_hash = ?1, updated_at = ?2 WHERE id = 1'
    )
      .bind(hash, now)
      .run();
    return jsonReply({ ok: true, msg: '主密碼已更新' });
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

// ── 工具 ───────────────────────────────────────
async function checkAdmin(pwd, env) {
  if (!pwd || !env.ADMIN_PASSWORD_HASH) return false;
  const h = await sha256Hex(pwd);
  return h === env.ADMIN_PASSWORD_HASH;
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
  .wrap{max-width:640px;margin:0 auto}
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
  .row{display:flex;gap:8px;align-items:flex-end}
  .row>:first-child{flex:1}
  .toast{position:fixed;top:20px;right:20px;background:#0d47a1;color:#fff;padding:12px 20px;border-radius:6px;box-shadow:0 4px 12px rgba(0,0,0,.2);z-index:99;animation:slide .3s ease;max-width:400px}
  .toast.err{background:#c62828}
  @keyframes slide{from{transform:translateX(120%)}to{transform:translateX(0)}}
  .hidden{display:none}
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
        <div class="k">主密碼</div><div class="v" id="s_pwd">—</div>
        <div class="k">服務狀態</div><div class="v" id="s_kill">—</div>
        <div class="k">停用訊息</div><div class="v" id="s_msg">—</div>
        <div class="k">最近更新</div><div class="v" id="s_at">—</div>
      </div>
      <button class="ghost" onclick="loadState()" style="margin-top:14px">重新整理</button>
    </div>

    <div class="card">
      <h2>更新主密碼</h2>
      <p style="margin:0 0 8px;color:#666;font-size:13px">所有 EXE 都會立刻使用新密碼。舊密碼立刻失效。</p>
      <label>新密碼（至少 4 字）</label>
      <input id="newpwd" type="password">
      <button onclick="setPwd()">送出新密碼</button>
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
}
async function loadState(){
  const r = await api('/admin/state', {admin_pwd: ADM});
  if (!r.ok) return toast(r.reason || '失敗', true);
  renderState(r.state);
}
function renderState(s){
  const pwd = document.getElementById('s_pwd');
  const kill = document.getElementById('s_kill');
  pwd.innerHTML = s.password_set
    ? '<span class="pill ok">已設定</span>'
    : '<span class="pill bad">未設定（EXE 無法登入）</span>';
  kill.innerHTML = s.killed
    ? '<span class="pill bad">🛑 已停用</span>'
    : '<span class="pill ok">✅ 服務中</span>';
  document.getElementById('s_msg').textContent = s.message || '(空)';
  document.getElementById('s_at').textContent = s.updated_at || '—';
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
