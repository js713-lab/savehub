// SaveHub · automatic sign-in panel (Accounts page). Self-contained so the main UI (app.js) stays untouched.
// Username + password go ONLY to POST /api/accounts/connect (same origin, authenticated dashboard) -> encrypted vault.
// Nothing here ever reads a password back: the status API returns state only.
(() => {
  const PLAT = {x:'X',instagram:'Instagram',threads:'Threads',facebook:'Facebook',messenger:'Messenger',tiktok:'TikTok',
    douyin:'抖音 Douyin',xiaohongshu:'小红书 RED',icloud:'iCloud',whatsapp:'WhatsApp (QR)',wechat:'WeChat 微信 (QR)'};
  const QR_ONLY = new Set(['whatsapp','wechat']);
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const css = `.aa-panel{margin:12px 0 18px;padding:14px;border:1px solid var(--line,#ccc);border-radius:10px}
  .aa-panel h3{margin:0 0 8px}.aa-form{display:flex;flex-wrap:wrap;gap:8px;align-items:end}
  .aa-form label{display:flex;flex-direction:column;font-size:12px;gap:2px}
  .aa-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px;margin-top:12px}
  .aa-card{border:1px solid var(--line,#ccc);border-radius:8px;padding:10px;font-size:13px}.aa-st{font-weight:600}
  .aa-st.ok{color:#15803d}.aa-st.warn{color:#b45309}.aa-st.err{color:#b91c1c}
  .aa-card img{max-width:200px;display:block;margin:6px 0;background:#fff}
  .aa-row{display:flex;gap:6px;margin-top:6px;flex-wrap:wrap}.aa-msg{font-size:12px;margin-top:6px}`;
  const cls = s => ({connected:'ok',done:'ok',syncing:'ok',signing_in:'warn',needs_code:'warn',needs_qr:'warn',needs_captcha:'warn',
                     needs_login:'warn',wrong_password:'err',error:'err'}[s]||'');
  const post = (u, b) => fetch(u, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(b||{})})
    .then(async r => { const j = await r.json().catch(()=>({})); if (!r.ok) throw new Error(j.detail || r.status); return j; });

  function mount(root) {
    if (!root || root.querySelector('#aa-root')) return;
    if (!document.getElementById('aa-css')) {
      const st = document.createElement('style'); st.id = 'aa-css'; st.textContent = css; document.head.appendChild(st);
    }
    const wrap = document.createElement('div'); wrap.id = 'aa-root'; wrap.className = 'aa-panel';
    wrap.innerHTML = `<h3>Automatic sign-in</h3>
      <p class="muted small">Type the app login once. The password is stored only in SaveHub's encrypted vault (AES-256-GCM), never shown again,
      and SaveHub signs in headlessly and starts syncing. 2FA codes and QR scans are handled on the card below.</p>
      <form class="aa-form" id="aa-form">
        <label>App <select id="aa-plat" aria-label="App to connect">${Object.entries(PLAT).map(([k,v])=>`<option value="${k}">${esc(v)}</option>`).join('')}</select></label>
        <label id="aa-ulab">Username / e-mail <input id="aa-user" autocomplete="username" required></label>
        <label id="aa-plab">Password <input id="aa-pass" type="password" autocomplete="current-password" required></label>
        <label>Handle (optional) <input id="aa-handle" placeholder="@me"></label>
        <button class="btn" type="submit">Connect &amp; sync</button>
      </form>
      <div class="aa-msg" id="aa-msg"></div>
      <div class="aa-cards" id="aa-cards"></div>`;
    root.prepend(wrap);
    const plat = wrap.querySelector('#aa-plat');
    const syncFields = () => {
      const qr = QR_ONLY.has(plat.value);
      wrap.querySelector('#aa-ulab').style.display = qr ? 'none' : '';
      wrap.querySelector('#aa-plab').style.display = qr ? 'none' : '';
      wrap.querySelector('#aa-user').required = !qr;
      wrap.querySelector('#aa-pass').required = !qr;
    };
    plat.addEventListener('change', syncFields); syncFields();
    wrap.querySelector('#aa-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const body = {platform: plat.value, username: wrap.querySelector('#aa-user').value, password: wrap.querySelector('#aa-pass').value,
                    handle: wrap.querySelector('#aa-handle').value};
      wrap.querySelector('#aa-pass').value = '';
      wrap.querySelector('#aa-msg').textContent = 'Signing in…';
      try {
        const r = await post('/api/accounts/connect', body);
        wrap.querySelector('#aa-msg').textContent = `${r.account}: ${r.status}. Status updates below.`;
        body.password = ''; refresh();
      } catch (err) { wrap.querySelector('#aa-msg').textContent = 'Error: ' + err.message; }
    });
    refresh();
    if (!window._aaPoll) window._aaPoll = setInterval(() => { if (location.hash.includes('accounts')) refresh(); }, 4000);
  }

  async function refresh() {
    const cards = document.getElementById('aa-cards'); if (!cards) return;
    try {
      const r = await fetch('/api/autologin'); const j = await r.json();
      if (!r.ok) { cards.innerHTML = `<div class="muted small">${esc(j.detail || 'status unavailable')}</div>`; return; }
      cards.innerHTML = (j.accounts || []).map(a => {
        const st = a.status || '';
        let extra = '';
        if (st === 'needs_code') extra = `<form class="aa-row aa-code" data-p="${esc(a.platform)}" data-a="${esc(a.account)}">
          <input name="code" placeholder="Enter the code" autocomplete="one-time-code" aria-label="Verification code" required>
          <button class="btn small" type="submit">Submit code</button></form>`;
        if (st === 'needs_qr') extra = `<img alt="Sign-in QR" src="/api/autologin/qr?platform=${encodeURIComponent(a.platform)}&account=${encodeURIComponent(a.account)}&t=${Date.now()}">`;
        if (st === 'wrong_password' || st === 'error') extra += `<div class="aa-row"><button class="btn ghost small aa-retry" data-id="${a.account_id||''}">Retry sign-in</button></div>`;
        return `<div class="aa-card" data-p="${esc(a.platform)}" data-a="${esc(a.account)}">
          <div><b>${esc(PLAT[a.platform]||a.platform)}</b> · ${esc(a.account)}</div>
          <div class="aa-st ${cls(st)}">${esc(a.label || st)}</div>
          <div class="muted small">${esc(a.detail||'')}${a.last_sync ? ' · last sync ' + esc(a.last_sync) : ''}</div>${extra}</div>`;
      }).join('') || '<div class="muted small">No automatic accounts yet.</div>';
      cards.querySelectorAll('.aa-code').forEach(f => f.addEventListener('submit', async (e) => {
        e.preventDefault();
        const code = f.code.value; f.code.value = '';
        try { await post('/api/autologin/code', {platform: f.dataset.p, account: f.dataset.a, code}); refresh(); }
        catch (err) { alert(err.message); }
      }));
      cards.querySelectorAll('.aa-retry').forEach(b => b.addEventListener('click', async () => {
        if (!b.dataset.id) return;
        try { await post('/api/accounts/' + b.dataset.id + '/autologin', {}); refresh(); } catch (err) { alert(err.message); }
      }));
    } catch (_) {}
  }

  function tryMount() {
    if (!location.hash.includes('accounts')) return;
    const root = document.querySelector('#view') || document.querySelector('main') || document.body;
    mount(root);
  }
  window.addEventListener('hashchange', () => setTimeout(tryMount, 200));
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => setTimeout(tryMount, 400));
  else setTimeout(tryMount, 400);
})();
