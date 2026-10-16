const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api = async (p, o = {}) => { const r = await fetch(p, o); const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.detail || r.statusText); return j; };
const jpost = (p, b, m = 'POST') => api(p, {method: m, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b || {})});
const toast = (m, o = {}) => {
  const t = $('#toast'); clearTimeout(t._h);
  t.className = 'toast ' + (o.kind || ''); t.innerHTML = `<span class="tmsg">${esc(m)}</span>`;
  if (o.action) { const b = document.createElement('button'); b.className = 'tact'; b.textContent = o.action.label; b.onclick = () => { t.classList.remove('show'); o.action.fn(); }; t.appendChild(b); }
  requestAnimationFrame(() => t.classList.add('show'));
  t._h = setTimeout(() => t.classList.remove('show'), o.action ? 6000 : 3500);
};
const COLORS = {x:'#111',threads:'#333',instagram:'#d62976',facebook:'#1877f2',messenger:'#0084ff',whatsapp:'#25d366',wechat:'#07c160',tiktok:'#ff0050',douyin:'#161823',xiaohongshu:'#ff2442',web:'#6b7280'};
let PLATFORMS = [], ACCOUNTS = [];
const pname = id => (PLATFORMS.find(p => p.id === id) || {}).name || id;
const pbadge = id => `<span class="pbadge" style="background:${COLORS[id] || '#555'}">${esc(pname(id))}</span>`;
const fdate = s => s ? new Date(s).toLocaleDateString(undefined, {year:'numeric', month:'short', day:'numeric'}) : '';
let MODAL_RET = null, MODAL_ON_CLOSE = null;
const modal = (html, cls = '', labelledby = '') => {
  const m = $('#modal'), c = $('#modalcard');
  if (m.classList.contains('hidden')) MODAL_RET = document.activeElement;
  c.className = 'modal-card ' + cls; c.innerHTML = html;
  labelledby ? c.setAttribute('aria-labelledby', labelledby) : c.removeAttribute('aria-labelledby');
  m.classList.remove('hidden'); requestAnimationFrame(() => m.classList.add('show'));
  if (!c.contains(document.activeElement)) setTimeout(() => { if (!c.contains(document.activeElement)) { const f = c.querySelector('input,select,textarea,button'); f && f.focus(); } }, 30);
};
const closeModal = () => {
  const m = $('#modal'); if (m.classList.contains('hidden')) return;
  m.classList.remove('show'); setTimeout(() => { if (!m.classList.contains('show')) m.classList.add('hidden'); }, 180);
  const cb = MODAL_ON_CLOSE; MODAL_ON_CLOSE = null; if (cb) cb();
  if (MODAL_RET && document.contains(MODAL_RET)) MODAL_RET.focus(); MODAL_RET = null;
};
document.addEventListener('keydown', e => {
  const m = $('#modal'); if (m.classList.contains('hidden')) return;
  if (e.key === 'Escape') { e.preventDefault(); closeModal(); return; }
  if (e.key === 'Tab') {   // keep focus inside the dialog
    const f = [...$('#modalcard').querySelectorAll('a[href],button:not([disabled]),input:not([type=hidden]):not(.hidden),select,textarea,[tabindex="0"]')].filter(x => x.offsetParent !== null);
    if (!f.length) return; const i = f.indexOf(document.activeElement);
    if (e.shiftKey && i <= 0) { e.preventDefault(); f[f.length - 1].focus(); } else if (!e.shiftKey && i === f.length - 1) { e.preventDefault(); f[0].focus(); }
  }
});
$('#modal').addEventListener('click', e => { if (e.target.id === 'modal') closeModal(); });

// theme
const setTheme = t => { document.documentElement.dataset.theme = t; localStorage.theme = t; };
setTheme(localStorage.theme || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'));
$('#theme').onclick = () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');

async function refreshMeta() {
  const [h, p, a] = await Promise.all([api('/api/health'), api('/api/platforms'), api('/api/accounts?limit=5000')]);
  PLATFORMS = p; ACCOUNTS = a.accounts;
  $('#statline').textContent = `${h.stats.items} items · ${h.stats.contacts} contacts · ${h.stats.accounts} accounts · tagger: ${h.ai.active}`;
  $('#samplebar').classList.toggle('hidden', !h.stats.samples); $('#samplecount').textContent = h.stats.samples;
}
async function clearSamples() { if (!confirm('Delete all SAMPLE items, contacts and accounts?')) return; const r = await jpost('/api/samples/clear'); toast(`Removed ${r.items} items, ${r.contacts} contacts, ${r.accounts} accounts`); await refreshMeta(); route(); }
const accountOptions = (sel, platform) => `<option value="">All accounts</option>` + ACCOUNTS.filter(a => !platform || a.platform === platform).map(a => `<option value="${a.id}" ${String(sel) === String(a.id) ? 'selected' : ''}>${esc(pname(a.platform))} · ${esc(a.display_name || a.handle)}${a.is_sample ? ' (sample)' : ''}</option>`).join('');

// ---------------- Library ----------------
const L = {q: '', platforms: new Set(), tags: new Set(), account: '', sort: 'saved_at', order: 'desc', view: localStorage.view || 'cards'};
async function viewLibrary(params) {
  if (params.get('q') !== null) L.q = params.get('q');
  const tags = await api('/api/tags');
  $('#view').innerHTML = `
  <div class="toolbar">
    <input class="search" id="q" placeholder="Search everything… (English / 中文, e.g. coffee, 咖啡, python)" value="${esc(L.q)}">
    <select id="acct">${accountOptions(L.account)}</select>
    <select id="sort">${[['saved_at','Date saved'],['relevance','Relevance'],['platform','Platform'],['author','Author'],['title','Title']].map(([v,l]) => `<option value="${v}" ${L.sort===v?'selected':''}>Sort: ${l}</option>`).join('')}</select>
    <button class="btn ghost" id="order" title="Toggle order">${L.order === 'desc' ? '↓ Desc' : '↑ Asc'}</button>
    <button class="btn ghost" id="vt">${L.view === 'cards' ? '☰ Table' : '▦ Cards'}</button>
    <button class="btn" onclick="addLinkModal()">+ Add link</button>
  </div>
  <div class="chips" id="pchips">${PLATFORMS.filter(p => p.enabled).map(p => `<span class="chip ${L.platforms.has(p.id)?'on':''}" data-p="${p.id}">${esc(p.name)}<span class="n">${p.count}</span></span>`).join('')}</div>
  <div class="chips" id="tchips"><span class="muted small" style="align-self:center">Tags:</span>${tags.slice(0, 24).map(t => `<span class="chip ${L.tags.has(t.tag)?'on':''}" data-t="${esc(t.tag)}">#${esc(t.tag)}<span class="n">${t.n}</span></span>`).join('')}</div>
  <div class="row muted small" style="margin:6px 0 12px"><span id="count"></span><span style="flex:1"></span><button class="btn ghost small" onclick="retagAll()">↻ Re-run auto-tagging</button></div>
  <div id="results"></div>`;
  let t; $('#q').oninput = e => { clearTimeout(t); t = setTimeout(() => { L.q = e.target.value; if (L.q && L.sort === 'saved_at') {} loadItems(); }, 250); };
  $('#acct').onchange = e => { L.account = e.target.value; loadItems(); };
  $('#sort').onchange = e => { L.sort = e.target.value; loadItems(); };
  $('#order').onclick = () => { L.order = L.order === 'desc' ? 'asc' : 'desc'; viewLibrary(new URLSearchParams()); };
  $('#vt').onclick = () => { L.view = localStorage.view = L.view === 'cards' ? 'table' : 'cards'; viewLibrary(new URLSearchParams()); };
  $('#pchips').onclick = e => { const c = e.target.closest('[data-p]'); if (!c) return; const p = c.dataset.p; L.platforms.has(p) ? L.platforms.delete(p) : L.platforms.add(p); c.classList.toggle('on'); loadItems(); };
  $('#tchips').onclick = e => { const c = e.target.closest('[data-t]'); if (!c) return; toggleTag(c.dataset.t); };
  loadItems();
}
function toggleTag(t) { L.tags.has(t) ? L.tags.delete(t) : L.tags.add(t); viewLibrary(new URLSearchParams()); }
async function loadItems() {
  const p = new URLSearchParams({q: L.q, platform: [...L.platforms].join(','), tag: [...L.tags].join(','), account_id: L.account, sort: L.sort === 'relevance' && !L.q ? 'saved_at' : L.sort, order: L.order, limit: 200});
  const r = await api('/api/items?' + p);
  $('#count').textContent = `${r.total} item${r.total === 1 ? '' : 's'}${L.q ? ` matching “${L.q}”` : ''}`;
  const el = $('#results');
  if (!r.items.length) { el.innerHTML = `<div class="panel muted">No items. Try another search, or import from <a href="#/connect">Connect apps</a>.</div>`; return; }
  const tagsHtml = i => (i.tags || []).map(t => `<span class="tag" onclick="toggleTag('${esc(t)}')">#${esc(t)}</span>`).join(' ');
  if (L.view === 'cards') el.innerHTML = `<div class="grid">${r.items.map(i => `
    <div class="card">
      <div class="meta">${pbadge(i.platform)}${i.is_sample ? '<span class="sample">SAMPLE</span>' : ''}<span>${esc(i.author || '')}${i.author_handle ? ' @' + esc(i.author_handle) : ''}</span></div>
      <h3><a href="${esc(i.url || '#')}" target="_blank" rel="noopener">${esc(i.title)}</a></h3>
      <div class="sum">${esc(i.summary || '')}</div>
      <div class="tags">${tagsHtml(i)}</div>
      <div class="foot"><span>Saved ${fdate(i.saved_at)}${i.account_name ? ' · via ' + esc(i.account_name) : ''}</span><span><a href="#" onclick="itemModal(${i.id});return false">details</a></span></div>
    </div>`).join('')}</div>`;
  else el.innerHTML = `<table><thead><tr>${[['title','Title'],['platform','Platform'],['author','Author'],['', 'Tags'],['', 'Account'],['saved_at','Saved']].map(([k,l]) => `<th ${k?`onclick="L.sort='${k}';L.order=L.order==='desc'?'asc':'desc';viewLibrary(new URLSearchParams())"`:''}>${l}${L.sort===k?(L.order==='desc'?' ↓':' ↑'):''}</th>`).join('')}</tr></thead><tbody>
    ${r.items.map(i => `<tr><td><a href="${esc(i.url||'#')}" target="_blank">${esc(i.title)}</a> ${i.is_sample?'<span class="sample">SAMPLE</span>':''}<div class="muted small">${esc(i.summary||'')}</div></td><td>${pbadge(i.platform)}</td><td>${esc(i.author||'')}</td><td>${tagsHtml(i)}</td><td class="small">${esc(i.account_name||'')}</td><td class="small">${fdate(i.saved_at)}</td></tr>`).join('')}</tbody></table>`;
}
async function retagAll() { toast('Tagging…'); const r = await jpost('/api/tag', {force: true}); toast(`Re-tagged ${r.retagged} items with ${r.provider.active}`); viewLibrary(new URLSearchParams()); }
async function itemModal(id) {
  const i = await api('/api/items/' + id);
  modal(`<div class="row">${pbadge(i.platform)}${i.is_sample?'<span class="sample">SAMPLE</span>':''}<span class="muted small">tagged by ${esc(i.tagger||'?')} · source ${esc(i.source||'')}</span></div>
    <h3>${esc(i.title)}</h3><p>${esc(i.content || '')}</p><p class="muted">${esc(i.summary || '')}</p>
    <div class="form"><label class="full">Tags (comma separated)<input id="etags" value="${esc((i.tags||[]).join(', '))}"></label>
    <label class="full">Account<select id="eacct">${accountOptions(i.account_id, null).replace('All accounts','— none —')}</select></label></div>
    <p class="small"><a href="${esc(i.url||'#')}" target="_blank">${esc(i.url||'')}</a><br>Saved ${esc(i.saved_at)}</p>
    <div class="row"><button class="btn" onclick="saveItem(${i.id})">Save</button><button class="btn ghost" onclick="closeModal()">Close</button><span style="flex:1"></span><button class="btn danger" onclick="delItem(${i.id})">Delete</button></div>`);
}
async function saveItem(id) { await jpost('/api/items/' + id, {tags: $('#etags').value.split(',').map(s => s.trim()).filter(Boolean), account_id: $('#eacct').value || null}, 'PATCH'); closeModal(); loadItems(); }
async function delItem(id) { if (!confirm('Delete item?')) return; await api('/api/items/' + id, {method: 'DELETE'}); closeModal(); await refreshMeta(); loadItems(); }
function addLinkModal() {
  modal(`<h3>Add a link</h3><div class="form">
    <label class="full">URL<input id="nurl" placeholder="https://…"></label><label class="full">Title<input id="ntitle"></label>
    <label class="full">Notes / content<textarea id="ncontent"></textarea></label>
    <label>Platform<select id="nplat"><option value="">Auto-detect</option>${PLATFORMS.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select></label>
    <label>Account<select id="nacct">${accountOptions('', null).replace('All accounts','— none —')}</select></label></div>
    <p class="row"><button class="btn" onclick="addLink()">Save & auto-tag</button><button class="btn ghost" onclick="closeModal()">Cancel</button></p>`);
}
async function addLink() {
  const r = await jpost('/api/items', {url: $('#nurl').value, title: $('#ntitle').value, content: $('#ncontent').value, platform: $('#nplat').value, account_id: $('#nacct').value || null});
  closeModal(); toast(r.created ? 'Saved & tagged: ' + (r.item.tags || []).join(', ') : 'Already in library'); await refreshMeta(); route();
}

// ---------------- Summary ----------------
async function viewSummary(params) {
  const days = params.get('days') || 7;
  $('#view').innerHTML = '<div class="panel muted">Building digest…</div>';
  const s = await api('/api/summary?days=' + days);
  const max = Math.max(1, ...Object.values(s.by_platform));
  $('#view').innerHTML = `<div class="row" style="margin-bottom:12px"><h2 style="margin:0;flex:1">Digest · last ${days} days</h2>
    ${[7,14,30].map(d => `<a class="chip ${d==days?'on':''}" href="#/summary?days=${d}">${d} days</a>`).join('')}</div>
  <div class="cols">
    <div class="panel"><div class="muted small">Items saved</div><div class="kpi">${s.total}</div><div class="muted small">since ${fdate(s.since)}</div></div>
    <div class="panel"><div class="muted small">Platforms active</div><div class="kpi">${Object.keys(s.by_platform).length}</div></div>
    <div class="panel"><div class="muted small">Top tag</div><div class="kpi">${s.top_tags[0] ? '#' + esc(s.top_tags[0][0]) : '—'}</div></div>
  </div>
  <div class="panel" style="margin-top:14px"><h4>📝 Summary <span class="muted small">(${s.digest_by === 'local' ? 'template — no AI key set' : 'written by ' + esc(s.digest_by)})</span></h4><div class="digest">${esc(s.digest)}</div></div>
  <div class="cols" style="margin-top:14px">
    <div class="panel"><h4>By platform</h4>${Object.entries(s.by_platform).map(([p, n]) => `<div class="barrow"><span>${esc(pname(p))}</span><div class="bar" style="width:${n/max*100}%;background:${['x','threads','douyin'].includes(p)?'#8b949e':(COLORS[p]||'var(--accent)')}"></div><b>${n}</b></div>`).join('') || '<span class="muted">—</span>'}</div>
    <div class="panel"><h4>Top tags</h4><div class="chips">${s.top_tags.map(([t, n]) => `<span class="tag">#${esc(t)} ${n}</span>`).join(' ')}</div>
      <h4 style="margin-top:14px">Top authors</h4>${s.top_authors.map(([a, n]) => `<div class="small">${esc(a)} <span class="muted">· ${n}</span></div>`).join('')}</div>
  </div>
  <div class="panel" style="margin-top:14px"><h4>Recent saves</h4>${s.recent.map(i => `<div class="row" style="margin:6px 0">${pbadge(i.platform)}<a href="${esc(i.url||'#')}" target="_blank">${esc(i.title)}</a><span class="muted small">${fdate(i.saved_at)}</span></div>`).join('')}</div>`;
}

// ---------------- Accounts ----------------
const PMETA = {
  x:           {mark: 'X',  bg: '#000', at: true, ph: 'username', hint: 'Your X username, e.g. @jsyip'},
  threads:     {mark: '@',  bg: '#000', at: true, ph: 'username', hint: 'Usually the same as your Instagram username'},
  instagram:   {mark: 'IG', bg: 'linear-gradient(45deg,#f09433,#e6683c 25%,#dc2743 50%,#cc2366 75%,#bc1888)', at: true, ph: 'username'},
  facebook:    {mark: 'f',  bg: '#1877f2', pre: 'fb.com/', ph: 'username or profile id'},
  messenger:   {mark: 'm',  bg: 'linear-gradient(45deg,#0099ff,#a033ff 60%,#ff5280)', pre: 'm.me/', ph: 'username'},
  whatsapp:    {mark: 'WA', bg: '#25d366', pre: 'tel', ph: '+60 12-345 6789', label: 'Phone number', hint: 'The phone number this WhatsApp account uses'},
  wechat:      {mark: '微', bg: '#07c160', pre: 'ID', ph: 'WeChat ID · 微信号', label: 'WeChat ID 微信号'},
  tiktok:      {mark: 'TT', bg: '#010101', at: true, ph: 'username', fx: 'tt'},
  douyin:      {mark: '抖', bg: '#161823', pre: 'ID', ph: '抖音号', label: 'Douyin ID 抖音号', fx: 'tt'},
  xiaohongshu: {mark: '红', bg: '#ff2442', pre: 'ID', ph: '小红书号', label: 'RED ID 小红书号'},
  web:         {mark: 'www', bg: '#6b7280', ph: 'username', label: 'Username'},
};
const pm = id => PMETA[id] || {mark: String(id || '?').slice(0, 2).toUpperCase(), bg: '#555', ph: 'username'};
const pmark = (id, size = '') => `<span class="pmark ${size} ${pm(id).fx || ''}" style="background:${pm(id).bg}" aria-hidden="true">${esc(pm(id).mark)}</span>`;
const PURL = {x: h => `https://x.com/${h}`, threads: h => `https://www.threads.net/@${h}`, instagram: h => `https://www.instagram.com/${h}/`,
  facebook: h => `https://www.facebook.com/${h}`, messenger: h => `https://m.me/${h}`, tiktok: h => `https://www.tiktok.com/@${h}`,
  whatsapp: h => { const d = h.replace(/\D/g, ''); return d.length >= 7 ? `https://wa.me/${d}` : ''; }};
const cleanHandle = h => String(h || '').trim().replace(/^@+/, '').trim();
const autoUrl = (p, h) => { h = cleanHandle(h); if (!h || (/\s/.test(h) && p !== 'whatsapp')) return ''; return PURL[p] ? PURL[p](h) : ''; };
const showHandle = (p, h) => h ? (pm(p).at ? '@' + h : h) : '';
const STATUSES = [['active', 'Active'], ['connected', 'Connected'], ['dormant', 'Dormant'], ['closed', 'Closed']];
const hue = s => [...String(s || '?')].reduce((a, c) => (a * 31 + c.codePointAt(0)) % 360, 17);
const initials = s => {
  const w = String(s || '').match(/[\p{L}\p{N}]+/gu) || [];
  if (!w.length) return '?';
  const cjk = c => /[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]/.test(c), f = [...w[0]][0];
  if (cjk(f)) return f;
  const second = w[1] && !cjk([...w[1]][0]) ? [...w[1]][0] : ([...w[0]].find((c, i) => i > 0 && !cjk(c)) || '');
  return (f + second).toUpperCase();
};
const avatar = (a, big) => { const n = a.display_name || a.handle; return `<span class="avatar ${big ? 'big' : ''}" style="--h:${hue(n)}" aria-hidden="true">${esc(initials(n))}<i class="pdot" style="background:${pm(a.platform).bg}"></i></span>`; };
const IC = {
  edit: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>',
  trash: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>',
  mail: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/></svg>',
  phone: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="7" y="2" width="10" height="20" rx="2"/><path d="M11 18h2"/></svg>',
  link: '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M14 4h6v6"/><path d="M20 4 10 14"/><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/></svg>',
  grid: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>',
  list: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg>',
  plus: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
  search: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
};
const statusPill = s => `<span class="spill s-${esc(s || 'active')}">${esc((STATUSES.find(x => x[0] === s) || [0, s || 'active'])[1])}</span>`;
const acctLabel = a => a.display_name || showHandle(a.platform, a.handle) || 'Untitled';
const findAcct = id => (A.all || []).find(a => a.id === id) || ACCOUNTS.find(a => a.id === id);
const isDup = (p, h, exceptId) => { h = cleanHandle(h).toLowerCase(); return h && ACCOUNTS.find(a => a.platform === p && (a.handle || '').toLowerCase() === h && a.id !== exceptId); };

const A = {q: '', platform: '', status: '', sort: 'platform', order: 'asc', view: localStorage.acctView || 'grid', all: [], editing: null};
async function viewAccounts() {
  $('#view').innerHTML = `
  <section class="acct-head">
    <div><h2>Accounts</h2><p class="muted small" id="acctsub">Loading…</p></div>
    <div class="row acct-actions">
      <button class="btn big" id="addAcct">${IC.plus} Add account</button>
      <button class="btn ghost" id="pasteAcct" title="Paste many handles at once">Paste handles</button>
      <label class="btn ghost" title="Import accounts from a CSV file" tabindex="0">Import CSV<input type="file" accept=".csv" class="hidden" onchange="importAccounts(this)" aria-label="Import accounts CSV"></label>
      <a class="btn ghost" href="/api/accounts/export.csv" title="Download all accounts as CSV">Export CSV</a>
    </div>
  </section>
  <div class="segbar" id="psegs" role="toolbar" aria-label="Filter by platform"></div>
  <div class="toolbar acct-tools">
    <div class="searchwrap">${IC.search}<input class="search" id="aq" type="search" aria-label="Search accounts" placeholder="Search name, handle, email, phone, notes, tags…" value="${esc(A.q)}"><kbd>/</kbd></div>
    <div class="seg" id="sseg" role="group" aria-label="Filter by status"></div>
    <select id="asort" aria-label="Sort accounts">${[['platform', 'Platform'], ['display_name', 'Name'], ['handle', 'Handle'], ['created_at', 'Date added'], ['status', 'Status']].map(([v, l]) => `<option value="${v}" ${A.sort === v ? 'selected' : ''}>Sort: ${l}</option>`).join('')}</select>
    <button class="btn ghost" id="aorder" aria-label="Toggle sort order" title="Toggle sort order">${A.order === 'desc' ? '↓' : '↑'}</button>
    <div class="seg" role="group" aria-label="Layout"><button class="${A.view === 'grid' ? 'on' : ''}" data-v="grid" aria-pressed="${A.view === 'grid'}" title="Cards grouped by platform">${IC.grid}<span>Cards</span></button><button class="${A.view === 'list' ? 'on' : ''}" data-v="list" aria-pressed="${A.view === 'list'}" title="Compact table">${IC.list}<span>Table</span></button></div>
  </div>
  <div id="alist" aria-live="polite"></div>
  <p class="muted small lock">🔒 SaveHub intentionally <b>never stores passwords</b> — keep those in a password manager. Items and contacts can be linked to any account.</p>`;
  let t; $('#aq').oninput = e => { clearTimeout(t); t = setTimeout(() => { A.q = e.target.value.trim(); loadAccounts(); }, 180); };
  $('#asort').onchange = e => { A.sort = e.target.value; A.order = e.target.value === 'created_at' ? 'desc' : 'asc'; $('#aorder').textContent = A.order === 'desc' ? '↓' : '↑'; loadAccounts(); };
  $('#aorder').onclick = e => { A.order = A.order === 'desc' ? 'asc' : 'desc'; e.currentTarget.textContent = A.order === 'desc' ? '↓' : '↑'; loadAccounts(); };
  document.querySelectorAll('[data-v]').forEach(b => b.onclick = () => { A.view = localStorage.acctView = b.dataset.v; document.querySelectorAll('[data-v]').forEach(x => { x.classList.toggle('on', x === b); x.setAttribute('aria-pressed', x === b); }); renderAccounts(); });
  $('#addAcct').onclick = () => openAddAccount(A.platform);
  $('#pasteAcct').onclick = () => openAddAccount(A.platform, 'paste');
  $('#psegs').onclick = e => { const b = e.target.closest('[data-p]'); if (b) { A.platform = b.dataset.p; renderAccounts(); } };
  $('#sseg').onclick = e => { const b = e.target.closest('[data-s]'); if (b) { A.status = b.dataset.s; renderAccounts(); } };
  $('#alist').onclick = onAcctListClick;
  await loadAccounts();
}
async function loadAccounts() {
  const r = await api('/api/accounts?' + new URLSearchParams({q: A.q, sort: A.sort, order: A.order, limit: 5000}));
  A.all = r.accounts; renderAccounts();
}
function renderAccounts() {
  if (!$('#alist')) return;
  const all = A.all, byP = {}, byS = {};
  all.forEach(a => { byP[a.platform] = (byP[a.platform] || 0) + 1; });
  const inP = all.filter(a => !A.platform || a.platform === A.platform);
  inP.forEach(a => { byS[a.status || 'active'] = (byS[a.status || 'active'] || 0) + 1; });
  const list = inP.filter(a => !A.status || (a.status || 'active') === A.status);
  const nPlat = Object.keys(byP).length;
  $('#acctsub').textContent = A.q ? `${all.length} match${all.length === 1 ? '' : 'es'} for “${A.q}”` : `${all.length} account${all.length === 1 ? '' : 's'} across ${nPlat} app${nPlat === 1 ? '' : 's'}`;
  const plats = PLATFORMS.filter(p => byP[p.id] || p.id === A.platform);
  $('#psegs').innerHTML = `<button class="pseg ${!A.platform ? 'on' : ''}" data-p="" aria-pressed="${!A.platform}">All<span class="n">${all.length}</span></button>` +
    plats.map(p => `<button class="pseg ${A.platform === p.id ? 'on' : ''}" data-p="${p.id}" aria-pressed="${A.platform === p.id}">${pmark(p.id, 'xs')}${esc(p.name)}<span class="n">${byP[p.id] || 0}</span></button>`).join('');
  $('#sseg').innerHTML = [['', 'All', inP.length], ...STATUSES.map(([v, l]) => [v, l, byS[v] || 0])].filter(([v, , n]) => !v || n || A.status === v)
    .map(([v, l, n]) => `<button class="${A.status === v ? 'on' : ''}" data-s="${v}" aria-pressed="${A.status === v}">${v ? `<i class="sdot s-${v}"></i>` : ''}${l}<span class="n">${n}</span></button>`).join('');
  const el = $('#alist');
  if (!all.length && !A.q) {
    el.innerHTML = `<div class="empty"><div class="empty-marks">${['instagram', 'x', 'tiktok', 'xiaohongshu', 'whatsapp', 'wechat', 'facebook', 'douyin'].map(p => pmark(p, 'lg')).join('')}</div>
      <h3>Bring all your accounts together</h3><p class="muted">Add every X, Threads, Instagram, Facebook, Messenger, WhatsApp, WeChat, TikTok, 抖音 and 小红书 account you own — then link saved items and contacts to them.</p>
      <div class="row center"><button class="btn big" onclick="openAddAccount()">${IC.plus} Add your first account</button><button class="btn ghost" onclick="openAddAccount('', 'paste')">Paste a list of handles</button></div></div>`;
    return;
  }
  if (!list.length) {
    el.innerHTML = `<div class="empty small-empty"><h3>No accounts match</h3><p class="muted">Try a different search or clear the filters.</p><div class="row center"><button class="btn ghost" onclick="clearAcctFilters()">Clear filters</button>${A.platform ? `<button class="btn" onclick="openAddAccount('${A.platform}')">${IC.plus} Add ${esc(pname(A.platform))} account</button>` : ''}</div></div>`;
    return;
  }
  if (A.view === 'list') { el.innerHTML = acctTable(list); return; }
  const groups = PLATFORMS.map(p => [p.id, list.filter(a => a.platform === p.id)]).filter(([, l]) => l.length);
  list.filter(a => !PLATFORMS.find(p => p.id === a.platform)).forEach(a => { let g = groups.find(x => x[0] === a.platform); if (!g) groups.push(g = [a.platform, []]); g[1].push(a); });
  el.innerHTML = groups.map(([p, l]) => `<section class="agroup" aria-label="${esc(pname(p))} accounts">
    <header class="agroup-h">${pmark(p, 'sm')}<h3>${esc(pname(p))}</h3><span class="count">${l.length}</span><span class="grow"></span><button class="btn ghost small" data-act="addp" data-p="${p}" aria-label="Add ${esc(pname(p))} account">${IC.plus} Add</button></header>
    <div class="agrid">${l.map(acctCard).join('')}</div></section>`).join('');
}
function clearAcctFilters() { A.q = ''; A.platform = ''; A.status = ''; const q = $('#aq'); if (q) q.value = ''; loadAccounts(); }
function acctCard(a) {
  if (A.editing === a.id) return acctQuickEdit(a);
  const h = showHandle(a.platform, a.handle);
  return `<article class="acard" data-id="${a.id}" tabindex="-1">
    <div class="acard-top">${avatar(a)}<div class="who"><div class="nm" title="${esc(acctLabel(a))}">${esc(a.display_name || h || 'Untitled')}${a.is_sample ? ' <span class="sample">SAMPLE</span>' : ''}</div>
      <div class="hd">${h ? (a.profile_url ? `<a href="${esc(a.profile_url)}" target="_blank" rel="noopener">${esc(h)} ${IC.link}</a>` : esc(h)) : '<span class="muted">no handle</span>'}</div></div>${statusPill(a.status)}</div>
    ${a.linked_email || a.linked_phone ? `<div class="acard-meta">${a.linked_email ? `<span>${IC.mail}${esc(a.linked_email)}</span>` : ''}${a.linked_phone ? `<span>${IC.phone}${esc(a.linked_phone)}</span>` : ''}</div>` : ''}
    ${(a.tags || []).length ? `<div class="tags">${a.tags.map(t => `<span class="tag" data-act="tag" data-t="${esc(t)}">#${esc(t)}</span>`).join('')}</div>` : ''}
    ${a.notes ? `<div class="notes">${esc(a.notes)}</div>` : ''}
    <div class="acard-foot"><a href="#" class="muted small" data-act="items" data-id="${a.id}">${a.item_count} item${a.item_count === 1 ? '' : 's'} · ${a.contact_count} contact${a.contact_count === 1 ? '' : 's'}</a>
      <span class="acts"><button class="icon" data-act="qedit" data-id="${a.id}" aria-label="Quick edit ${esc(acctLabel(a))}" title="Quick edit">${IC.edit}</button><button class="icon danger" data-act="del" data-id="${a.id}" aria-label="Delete ${esc(acctLabel(a))}" title="Delete">${IC.trash}</button></span></div>
  </article>`;
}
function acctQuickEdit(a) {
  return `<article class="acard editing" data-id="${a.id}"><form class="qedit" data-id="${a.id}" onsubmit="saveQuickEdit(event, ${a.id})" aria-label="Quick edit account">
    <div class="acard-top">${avatar(a)}<div class="who"><div class="nm">${esc(pname(a.platform))}</div><div class="hd muted small">Enter to save · Esc to cancel</div></div></div>
    <label>Display name<input name="display_name" value="${esc(a.display_name || '')}" autocomplete="off"></label>
    <label>${esc(pm(a.platform).label || 'Handle')}<div class="adorn">${pm(a.platform).at ? '<span class="pre">@</span>' : ''}<input name="handle" value="${esc(a.handle || '')}" autocomplete="off"></div></label>
    <div class="two"><label>Status<select name="status">${STATUSES.map(([v, l]) => `<option value="${v}" ${(a.status || 'active') === v ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
    <label>Tags<input name="tags" value="${esc((a.tags || []).join(', '))}" placeholder="work, food"></label></div>
    <label>Notes<input name="notes" value="${esc(a.notes || '')}"></label>
    <div class="row"><button class="btn small" type="submit">Save</button><button class="btn ghost small" type="button" data-act="qcancel">Cancel</button><span class="grow"></span><button class="btn ghost small" type="button" data-act="fulledit" data-id="${a.id}">All fields…</button></div>
  </form></article>`;
}
function acctTable(list) {
  const cols = [['platform', 'App'], ['display_name', 'Account'], ['', 'Linked email / phone'], ['', 'Tags'], ['status', 'Status'], ['', 'Linked'], ['created_at', 'Added'], ['', '']];
  return `<div class="tablewrap"><table class="atable"><thead><tr>${cols.map(([k, l]) => `<th ${k ? `tabindex="0" data-sort="${k}" aria-sort="${A.sort === k ? (A.order === 'desc' ? 'descending' : 'ascending') : 'none'}"` : ''}>${l}${A.sort === k ? (A.order === 'desc' ? ' ↓' : ' ↑') : ''}</th>`).join('')}</tr></thead><tbody>
  ${list.map(a => A.editing === a.id ? `<tr class="editing"><td colspan="8">${acctQuickEdit(a)}</td></tr>` : `<tr data-id="${a.id}"><td><span class="tplat">${pmark(a.platform, 'xs')}${esc(pname(a.platform))}</span></td>
    <td><div class="tacct">${avatar(a)}<div><b>${esc(a.display_name || showHandle(a.platform, a.handle) || 'Untitled')}</b>${a.is_sample ? ' <span class="sample">SAMPLE</span>' : ''}<div class="muted small">${a.profile_url ? `<a href="${esc(a.profile_url)}" target="_blank" rel="noopener">${esc(showHandle(a.platform, a.handle) || a.profile_url)}</a>` : esc(showHandle(a.platform, a.handle))}</div></div></div></td>
    <td class="small">${esc(a.linked_email || '')}${a.linked_email && a.linked_phone ? '<br>' : ''}${esc(a.linked_phone || '')}</td>
    <td>${(a.tags || []).map(t => `<span class="tag" data-act="tag" data-t="${esc(t)}">#${esc(t)}</span>`).join(' ')}</td><td>${statusPill(a.status)}</td>
    <td class="small"><a href="#" data-act="items" data-id="${a.id}">${a.item_count} items</a> · ${a.contact_count} contacts</td><td class="small muted">${fdate(a.created_at)}</td>
    <td class="tacts"><button class="icon" data-act="qedit" data-id="${a.id}" aria-label="Quick edit ${esc(acctLabel(a))}" title="Quick edit">${IC.edit}</button><button class="icon danger" data-act="del" data-id="${a.id}" aria-label="Delete ${esc(acctLabel(a))}" title="Delete">${IC.trash}</button></td></tr>`).join('')}
  </tbody></table></div>`;
}
function onAcctListClick(e) {
  const th = e.target.closest('th[data-sort]');
  if (th) { const k = th.dataset.sort; A.order = A.sort === k && A.order === 'asc' ? 'desc' : 'asc'; A.sort = k; $('#asort').value = k; $('#aorder').textContent = A.order === 'desc' ? '↓' : '↑'; loadAccounts(); return; }
  const b = e.target.closest('[data-act]'); if (!b) return;
  const id = +b.dataset.id, act = b.dataset.act;
  if (act === 'items') { e.preventDefault(); L.account = String(id); location.hash = '#/library'; }
  else if (act === 'tag') { A.q = b.dataset.t; $('#aq').value = A.q; loadAccounts(); }
  else if (act === 'addp') openAddAccount(b.dataset.p);
  else if (act === 'qedit') { A.editing = id; renderAccounts(); const f = document.querySelector(`form.qedit[data-id="${id}"] input`); if (f) { f.focus(); f.select(); } }
  else if (act === 'qcancel') { const fid = A.editing; A.editing = null; renderAccounts(); focusCard(fid); }
  else if (act === 'fulledit') { A.editing = null; renderAccounts(); openAddAccount(findAcct(id).platform, 'single', findAcct(id)); }
  else if (act === 'del') deleteAccountFlow(id);
}
const focusCard = id => { const el = document.querySelector(`[data-id="${id}"] [data-act="qedit"]`); if (el) el.focus(); };
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && A.editing && e.target.closest && e.target.closest('form.qedit')) { const id = A.editing; A.editing = null; renderAccounts(); focusCard(id); e.stopPropagation(); }
}, true);
async function saveQuickEdit(e, id) {
  e.preventDefault(); const f = e.target, b = Object.fromEntries(new FormData(f));
  b.handle = cleanHandle(b.handle); b.tags = b.tags.split(',').map(s => s.trim()).filter(Boolean);
  const a = findAcct(id);
  if (!b.handle && !b.display_name.trim()) { toast('Add a handle or a display name', {kind: 'err'}); return; }
  if (isDup(a.platform, b.handle, id)) { toast(`${showHandle(a.platform, b.handle)} already exists on ${pname(a.platform)}`, {kind: 'err'}); return; }
  if (b.handle !== (a.handle || '') && (!a.profile_url || a.profile_url === autoUrl(a.platform, a.handle))) b.profile_url = autoUrl(a.platform, b.handle);
  try { await jpost('/api/accounts/' + id, b, 'PUT'); } catch (err) { toast(err.message, {kind: 'err'}); return; }
  A.editing = null; toast(`Saved ${b.display_name || showHandle(a.platform, b.handle)}`, {kind: 'ok'}); await refreshMeta(); await loadAccounts(); focusCard(id);
}
function confirmDialog({title, body, ok = 'Delete', danger = true}) {
  return new Promise(res => {
    modal(`<div class="confirm" role="alertdialog" aria-labelledby="cfT" aria-describedby="cfB"><h3 id="cfT">${title}</h3><p id="cfB" class="muted">${body}</p>
      <div class="row end"><button class="btn ghost" id="cfNo">Cancel</button><button class="btn ${danger ? 'danger' : ''}" id="cfYes">${esc(ok)}</button></div></div>`, 'narrow');
    const done = v => { MODAL_ON_CLOSE = null; closeModal(); res(v); };
    MODAL_ON_CLOSE = () => res(false);
    $('#cfNo').onclick = () => done(false); $('#cfYes').onclick = () => done(true); $('#cfNo').focus();
  });
}
async function deleteAccountFlow(id, fromModal) {
  const a = findAcct(id); if (!a) return;
  const linked = a.item_count + a.contact_count;
  const ok = await confirmDialog({title: `Delete ${esc(acctLabel(a))}?`, body: `This removes the ${esc(pname(a.platform))} account record${linked ? `. Its ${a.item_count} item(s) and ${a.contact_count} contact(s) are kept but will be unlinked` : ''}. Nothing on ${esc(pname(a.platform))} itself is changed.`});
  if (!ok) { if (fromModal) openAddAccount(a.platform, 'single', a); return; }
  await api('/api/accounts/' + id, {method: 'DELETE'});
  const restore = {}; ['platform', 'display_name', 'handle', 'profile_url', 'linked_email', 'linked_phone', 'notes', 'tags', 'status', 'connected_via', 'created_at'].forEach(k => restore[k] = a[k]);
  toast(`Deleted ${acctLabel(a)}`, {kind: 'ok', action: linked ? null : {label: 'Undo', fn: async () => { await jpost('/api/accounts', restore); toast('Restored', {kind: 'ok'}); await refreshMeta(); loadAccounts(); }}});
  await refreshMeta(); if ($('#alist')) loadAccounts();
}
async function importAccounts(inp) {
  const fd = new FormData(); fd.append('kind', 'accounts_csv'); fd.append('file', inp.files[0]);
  try { const r = await api('/api/import', {method: 'POST', body: fd}); toast(`Accounts: ${r.created} added, ${r.duplicates} already existed`, {kind: 'ok'}); } catch (e) { toast(e.message, {kind: 'err'}); }
  inp.value = ''; closeModal(); await refreshMeta(); if ($('#alist')) loadAccounts();
}

// ---- Add / edit account modal ----
let AM = {};
function openAddAccount(platform = '', mode = 'single', edit = null) {
  AM = {step: platform || mode === 'paste' || edit ? 2 : 1, platform: platform || (edit && edit.platform) || '', mode, added: AM.keepAdded ? AM.added : [], edit, tried: false, filter: ''};
  if (mode === 'paste' && !platform) AM.platform = '';
  renderAM();
}
const amSteps = () => AM.edit ? '' : `<ol class="steps" aria-label="Progress"><li class="${AM.step === 1 ? 'on' : 'done'}">${AM.step > 1 ? `<button type="button" onclick="amBack()">1 · App</button>` : '1 · App'}</li><li class="${AM.step === 2 ? 'on' : ''}">2 · ${AM.mode === 'paste' ? 'Paste' : 'Details'}</li></ol>`;
const amClose = `<button class="x" type="button" onclick="closeModal()" aria-label="Close dialog">×</button>`;
function amBack() { AM.step = 1; renderAM(); }
function renderAM() {
  if (AM.step === 1) {
    const cnt = {}; ACCOUNTS.forEach(a => cnt[a.platform] = (cnt[a.platform] || 0) + 1);
    modal(`<div class="am-top">${amSteps()}${amClose}</div>
      <h3 id="amT" class="am-title">Add an account</h3><p class="muted am-sub">Which app is it on? <span class="small kbdhint">(use arrow keys, Enter to choose)</span></p>
      <div class="ptiles" role="listbox" aria-labelledby="amT">${PLATFORMS.map((p, i) => `<button type="button" class="ptile" role="option" data-p="${p.id}" style="--pc:${COLORS[p.id] || '#555'}" ${i === 0 ? '' : 'tabindex="-1"'}>
        ${pmark(p.id, 'lg')}<span class="pn">${esc(p.name)}</span><span class="pc">${cnt[p.id] ? `${cnt[p.id]} added` : 'none yet'}</span></button>`).join('')}</div>
      <div class="am-alt muted small">Got a long list? <button type="button" class="linkbtn" onclick="AM.mode='paste';AM.step=2;renderAM()">Paste many handles</button> or <label class="linkbtn" tabindex="0">import a CSV<input type="file" accept=".csv" class="hidden" onchange="importAccounts(this)"></label></div>`, 'wide am', 'amT');
    const tiles = [...document.querySelectorAll('.ptile')];
    tiles.forEach((t, i) => { t.onclick = () => { AM.platform = t.dataset.p; AM.step = 2; AM.mode = AM.mode || 'single'; renderAM(); };
      t.onkeydown = e => { const cols = getComputedStyle(t.parentElement).gridTemplateColumns.split(' ').length;
        const d = {ArrowRight: 1, ArrowLeft: -1, ArrowDown: cols, ArrowUp: -cols}[e.key]; if (d === undefined) return; e.preventDefault();
        const n = tiles[Math.max(0, Math.min(tiles.length - 1, i + d))]; tiles.forEach(x => x.tabIndex = -1); n.tabIndex = 0; n.focus(); }; });
    tiles[0] && tiles[0].focus();
    return;
  }
  const p = AM.platform, m = pm(p), e = AM.edit || {};
  const platHead = `<div class="am-plat">${p ? pmark(p, 'lg') : '<span class="pmark lg mixed" aria-hidden="true">+</span>'}<div class="grow"><div class="muted small">${AM.edit ? 'Editing' : 'Adding to'}</div><b>${p ? esc(pname(p)) : 'Several apps'}</b></div>
    ${AM.edit ? '' : `<button type="button" class="btn ghost small" onclick="amBack()">Change app</button>`}</div>
    ${AM.edit ? '' : `<div class="tabs" role="tablist" aria-label="How to add"><button type="button" role="tab" aria-selected="${AM.mode !== 'paste'}" class="${AM.mode !== 'paste' ? 'on' : ''}" onclick="amMode('single')" ${p ? '' : 'disabled title="Choose an app first"'}>One at a time</button><button type="button" role="tab" aria-selected="${AM.mode === 'paste'}" class="${AM.mode === 'paste' ? 'on' : ''}" onclick="amMode('paste')">Paste many</button></div>`}`;
  if (AM.mode === 'paste') { renderPaste(platHead); return; }
  const fld = (k, l, o = {}) => `<label class="${o.full ? 'full' : ''}" for="f_${k}"><span class="lbl">${l}${o.opt ? ' <span class="opt">optional</span>' : ''}</span>
    ${o.html || `<input id="f_${k}" name="${k}" type="${o.type || 'text'}" value="${esc(Array.isArray(e[k]) ? e[k].join(', ') : e[k] || '')}" placeholder="${esc(o.ph || '')}" autocomplete="off" ${o.attrs || ''} aria-describedby="e_${k}">`}
    <span class="err" id="e_${k}" role="alert"></span>${o.hint ? `<span class="hint" id="h_${k}">${o.hint}</span>` : ''}</label>`;
  const pre = m.at ? '@' : (m.pre || '');
  const status = e.status || AM.lastStatus || 'active';
  modal(`<div class="am-top">${amSteps()}${amClose}</div>
    <h3 id="amT" class="am-title">${AM.edit ? 'Edit account' : `Add ${esc(pname(p))} account`}</h3>
    ${platHead}
    <form id="amForm" class="form amform" novalidate>
      ${fld('handle', esc(m.label || 'Handle / username'), {full: true, html: `<div class="adorn">${pre ? `<span class="pre">${esc(pre)}</span>` : ''}<input id="f_handle" name="handle" value="${esc(e.handle || '')}" placeholder="${esc(m.ph)}" autocomplete="off" autocapitalize="off" spellcheck="false" aria-describedby="e_handle h_handle"></div>`, hint: esc(m.hint || 'Tip: you can paste a profile link too')})}
      ${fld('display_name', 'Display name', {ph: 'e.g. Js — Food', opt: true})}
      ${fld('profile_url', 'Profile URL', {type: 'url', ph: PURL[p] ? 'Auto-filled from handle' : 'https://…', opt: true})}
      ${fld('linked_email', 'Linked email', {type: 'email', ph: 'you@example.com', opt: true})}
      ${fld('linked_phone', 'Linked phone', {type: 'tel', ph: '+60 12-345 6789', opt: true})}
      <fieldset class="full statusset"><legend class="lbl">Status</legend><div class="seg" role="radiogroup">${STATUSES.map(([v, l]) => `<label class="${status === v ? 'on' : ''}"><input type="radio" name="status" value="${v}" ${status === v ? 'checked' : ''}><i class="sdot s-${v}"></i>${l}</label>`).join('')}</div></fieldset>
      ${fld('tags', 'Tags', {ph: 'personal, work, food', opt: true})}
      ${fld('connected_via', 'Connected via', {ph: 'e.g. Google login, phone', opt: true})}
      ${fld('notes', 'Notes', {full: true, opt: true, html: `<textarea id="f_notes" name="notes" rows="2" placeholder="Anything to remember (no passwords!)">${esc(e.notes || '')}</textarea>`})}
      <div class="full urlprev muted small" id="urlprev" aria-live="polite"></div>
    </form>
    <div id="amAdded" class="added" aria-live="polite"></div>
    <div class="am-foot">${AM.edit ? `<button type="button" class="btn ghost danger-text" onclick="deleteAccountFlow(${e.id}, true)">${IC.trash} Delete</button>` : `<span class="muted small kbdhint"><kbd>Enter</kbd> save · <kbd>Esc</kbd> close</span>`}<span class="grow"></span>
      ${AM.edit ? `<button type="button" class="btn ghost" onclick="closeModal()">Cancel</button><button type="button" class="btn" id="amSave">Save changes</button>`
        : `<button type="button" class="btn ghost" id="amDone">${AM.added.length ? 'Done' : 'Cancel'}</button><button type="button" class="btn ghost" id="amSaveClose">Save &amp; close</button><button type="button" class="btn" id="amSave">Save &amp; add another</button>`}</div>`, 'wide am', 'amT');
  // fix the tags field value (fld uses e[k] — prefer sticky tags for new accounts)
  if (!AM.edit && AM.lastTags) $('#f_tags').value = AM.lastTags.join(', ');
  if (!AM.edit && AM.lastVia) $('#f_connected_via').value = AM.lastVia;
  const form = $('#amForm');
  form.querySelectorAll('.statusset input').forEach(r => r.onchange = () => form.querySelectorAll('.statusset label').forEach(l => l.classList.toggle('on', l.querySelector('input').checked)));
  form.oninput = ev => { if (ev.target.id === 'f_handle') onHandleInput(/paste|drop/i.test(ev.inputType || '')); updateUrlPreview(); if (AM.tried) validateAM(); };
  $('#f_handle').addEventListener('change', () => { onHandleInput(true); updateUrlPreview(); });
  form.addEventListener('focusout', ev => { if (ev.target.value) validateAM(ev.target.name); });
  form.onkeydown = ev => { if (ev.key === 'Enter' && (ev.target.tagName !== 'TEXTAREA' || ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); saveAM(!AM.edit && !(ev.ctrlKey || ev.metaKey)); } };
  $('#amSave').onclick = () => saveAM(!AM.edit);
  if ($('#amSaveClose')) $('#amSaveClose').onclick = () => saveAM(false);
  if ($('#amDone')) $('#amDone').onclick = closeModal;
  renderAdded(); updateUrlPreview();
  const hf = $('#f_handle'); if (hf) { hf.focus(); hf.select(); }
}
function amMode(mode) { AM.mode = mode; if (mode === 'single' && !AM.platform) { AM.step = 1; } renderAM(); }
function onHandleInput(full) {
  const h = $('#f_handle'), v = h.value.trim();
  if (full && /^https?:\/\/[^/]+\/./i.test(v)) {   // pasted a profile link -> split into handle + URL
    const path = v.replace(/^https?:\/\/[^/]+/i, '').split('?')[0].split('/').filter(Boolean);
    const hh = cleanHandle(path[0] === 'user' || path[0] === 'profile' ? path[path.length - 1] : path[0] || '');
    if (!$('#f_profile_url').value) $('#f_profile_url').value = v;
    h.value = hh;
  } else if (v.startsWith('@')) h.value = cleanHandle(v);
}
function updateUrlPreview() {
  const el = $('#urlprev'); if (!el) return;
  const u = $('#f_profile_url').value.trim(), au = autoUrl(AM.platform, $('#f_handle').value);
  el.innerHTML = !u && au ? `Profile link will be <a href="${esc(au)}" target="_blank" rel="noopener">${esc(au)}</a>` : '';
}
function amData() {
  const f = $('#amForm'), b = Object.fromEntries(new FormData(f));
  b.handle = cleanHandle(b.handle); b.platform = AM.platform;
  b.tags = (b.tags || '').split(/[,，]/).map(s => s.trim().replace(/^#/, '')).filter(Boolean);
  Object.keys(b).forEach(k => typeof b[k] === 'string' && (b[k] = b[k].trim()));
  b.status = b.status || 'active';
  return b;
}
function validateAM(only) {
  const b = amData(), errs = {}, m = pm(b.platform);
  if (!b.handle && !b.display_name) errs.handle = 'Add a handle or a display name';
  if (b.handle && m.at && /\s/.test(b.handle)) errs.handle = 'Usernames can’t contain spaces';
  if (b.handle && b.platform === 'whatsapp' && b.handle.replace(/\D/g, '').length < 7) errs.handle = 'Enter the full phone number, e.g. +60 12-345 6789';
  const d = isDup(b.platform, b.handle, AM.edit && AM.edit.id); if (d) errs.handle = `You already added ${showHandle(b.platform, b.handle)} on ${pname(b.platform)}`;
  if (b.profile_url && !/^https?:\/\/\S+\.\S+/i.test(b.profile_url)) errs.profile_url = 'Use a full link starting with https://';
  if (b.linked_email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(b.linked_email)) errs.linked_email = 'That email doesn’t look right';
  if (b.linked_phone && !/^\+?[\d\s\-().]{6,}$/.test(b.linked_phone)) errs.linked_phone = 'Use digits, spaces, + - ( ) only';
  ['handle', 'profile_url', 'linked_email', 'linked_phone'].forEach(k => {
    if (only && only !== k && !AM.tried) return;
    const el = $('#e_' + k), inp = $('#f_' + k); if (!el) return;
    el.textContent = errs[k] || ''; inp.setAttribute('aria-invalid', errs[k] ? 'true' : 'false'); inp.closest('label').classList.toggle('bad', !!errs[k]);
  });
  return {b, errs};
}
async function saveAM(another) {
  AM.tried = true; if ($('#f_handle')) onHandleInput(true);
  const {b, errs} = validateAM();
  const first = Object.keys(errs)[0];
  if (first) { const i = $('#f_' + first); i.focus(); i.closest('label').classList.remove('shake'); void i.offsetWidth; i.closest('label').classList.add('shake'); return; }
  if (!b.profile_url) b.profile_url = autoUrl(b.platform, b.handle);
  if (!AM.edit && !b.connected_via) b.connected_via = 'manual';
  const btn = $('#amSave'); btn.disabled = true;
  try {
    if (AM.edit) {
      await jpost('/api/accounts/' + AM.edit.id, b, 'PUT');
      closeModal(); toast(`Saved ${b.display_name || showHandle(b.platform, b.handle)}`, {kind: 'ok'});
    } else {
      const r = await jpost('/api/accounts', b);
      if (!r.created) { $('#e_handle').textContent = 'This account already exists'; btn.disabled = false; return; }
      AM.added.push({id: r.id, platform: b.platform, label: b.display_name || showHandle(b.platform, b.handle)});
      AM.lastStatus = b.status; AM.lastTags = b.tags; AM.lastVia = b.connected_via === 'manual' ? '' : b.connected_via;
      toast(`Added ${showHandle(b.platform, b.handle) || b.display_name} to ${pname(b.platform)}`, {kind: 'ok'});
      await refreshMeta();
      if (another) { AM.tried = false; renderAM(); } else closeModal();
    }
  } catch (err) { toast(err.message, {kind: 'err'}); if (btn) btn.disabled = false; return; }
  if (AM.edit) await refreshMeta();
  if ($('#alist')) loadAccounts();
}
function renderAdded() {
  const el = $('#amAdded'); if (!el) return;
  if (!AM.added.length) { el.innerHTML = ''; return; }
  el.innerHTML = `<div class="added-h"><span class="okdot" aria-hidden="true">✓</span><b>${AM.added.length}</b> added just now</div><div class="added-l">${AM.added.map((a, i) => `<span class="achip">${pmark(a.platform, 'xs')}${esc(a.label)}<button type="button" aria-label="Undo adding ${esc(a.label)}" title="Undo" onclick="undoAdded(${i})">×</button></span>`).join('')}</div>`;
}
async function undoAdded(i) {
  const a = AM.added[i]; await api('/api/accounts/' + a.id, {method: 'DELETE'}); AM.added.splice(i, 1); renderAdded(); toast(`Removed ${a.label}`); await refreshMeta(); if ($('#alist')) loadAccounts();
  const h = $('#f_handle'); if (h) h.focus();
}
// ---- bulk paste ----
function renderPaste(platHead) {
  const p = AM.platform;
  modal(`<div class="am-top">${amSteps()}${amClose}</div>
    <h3 id="amT" class="am-title">Paste many handles</h3>
    ${platHead}
    <div class="form amform">
      <label for="bp"><span class="lbl">App for lines without a prefix</span><select id="bp"><option value="">Detect from each line</option>${PLATFORMS.map(x => `<option value="${x.id}" ${x.id === p ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select></label>
      <label for="bs"><span class="lbl">Status for all</span><select id="bs">${STATUSES.map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}</select></label>
      <label class="full" for="bt"><span class="lbl">Tags for all <span class="opt">optional</span></span><input id="bt" placeholder="personal, work" autocomplete="off"></label>
      <label class="full" for="btext"><span class="lbl">One account per line</span>
        <textarea id="btext" rows="7" spellcheck="false" placeholder="@jsyip\n@jsyip.food, Js Eats\ninstagram @js.travel Travel\nxhs js_red_id\nhttps://www.tiktok.com/@jsyip" aria-describedby="bhelp"></textarea>
        <span class="hint" id="bhelp">Formats: <code>@handle</code> · <code>handle, Display name</code> · <code>ig @handle</code> (prefix: x, threads, ig, fb, messenger, wa, wechat/微信, tiktok, douyin/抖音, xhs/小红书) · profile links. <kbd>Ctrl</kbd>+<kbd>Enter</kbd> to add.</span></label>
    </div>
    <div id="bprev" class="bprev" aria-live="polite"></div>
    <div id="amAdded" class="added" aria-live="polite"></div>
    <div class="am-foot"><span class="grow"></span><button type="button" class="btn ghost" onclick="closeModal()">${AM.added.length ? 'Done' : 'Cancel'}</button><button type="button" class="btn" id="bgo" disabled>Add accounts</button></div>`, 'wide am', 'amT');
  let t; const upd = () => { clearTimeout(t); t = setTimeout(previewPaste, 150); };
  $('#btext').oninput = upd;
  $('#bp').onchange = () => { AM.platform = $('#bp').value; const q = AM.platform;
    document.querySelector('.am-plat').firstElementChild.outerHTML = q ? pmark(q, 'lg') : '<span class="pmark lg mixed" aria-hidden="true">+</span>';
    document.querySelector('.am-plat b').textContent = q ? pname(q) : 'Several apps';
    const t1 = document.querySelector('.tabs [role=tab]'); if (t1) { t1.disabled = !q; } upd(); };
  $('#btext').onkeydown = e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); submitPaste(); } };
  $('#bgo').onclick = submitPaste;
  renderAdded(); $('#btext').focus();
}
async function previewPaste() {
  const text = $('#btext').value, el = $('#bprev'), go = $('#bgo');
  if (!text.trim()) { el.innerHTML = ''; go.disabled = true; go.textContent = 'Add accounts'; return; }
  const r = await jpost('/api/accounts/preview', {text, platform: $('#bp').value});
  const seen = new Set(); let ready = 0;
  const rows = r.accounts.map(a => { const k = a.platform + ':' + a.handle.toLowerCase(); const dup = isDup(a.platform, a.handle) || seen.has(k); seen.add(k); if (!dup) ready++;
    return `<li class="${dup ? 'dup' : ''}">${pmark(a.platform, 'xs')}<b>${esc(showHandle(a.platform, a.handle))}</b>${a.display_name ? `<span class="muted">${esc(a.display_name)}</span>` : ''}<span class="grow"></span>${dup ? '<span class="badge warn">already added</span>' : '<span class="badge ok">new</span>'}</li>`; });
  const errs = r.errors.map(x => `<li class="bad"><span class="badge bad">line ${x.line}</span><span class="muted">${esc(x.text)}</span><span class="grow"></span><span class="small">${esc(x.error)}</span></li>`);
  el.innerHTML = `<div class="bprev-h"><b>${ready}</b> ready to add${r.accounts.length - ready ? ` · ${r.accounts.length - ready} already exist` : ''}${r.errors.length ? ` · <span class="errtxt">${r.errors.length} need attention</span>` : ''}</div><ul>${rows.join('')}${errs.join('')}</ul>`;
  go.disabled = !ready; go.textContent = ready ? `Add ${ready} account${ready === 1 ? '' : 's'}` : 'Add accounts';
}
async function submitPaste() {
  const go = $('#bgo'); if (go.disabled) return; go.disabled = true;
  try {
    const r = await jpost('/api/accounts/bulk', {text: $('#btext').value, platform: $('#bp').value, status: $('#bs').value, tags: $('#bt').value});
    r.created_accounts.forEach(a => AM.added.push({id: a.id, platform: a.platform, label: showHandle(a.platform, a.handle)}));
    toast(`Added ${r.created} account${r.created === 1 ? '' : 's'}${r.duplicates ? ` · ${r.duplicates} already existed` : ''}${r.errors.length ? ` · ${r.errors.length} skipped` : ''}`, {kind: 'ok'});
    await refreshMeta();
    $('#btext').value = r.errors.map(x => x.text).join('\n'); await previewPaste(); renderAdded(); $('#btext').focus();
    const cancel = document.querySelector('.am-foot .btn.ghost'); if (cancel) cancel.textContent = 'Done';
    if ($('#alist')) loadAccounts();
  } catch (e) { toast(e.message, {kind: 'err'}); go.disabled = false; }
}
document.addEventListener('keydown', e => {
  if (location.hash.startsWith('#/accounts') && $('#modal').classList.contains('hidden') && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
    if (e.key === '/') { e.preventDefault(); $('#aq') && $('#aq').focus(); }
    else if (e.key === 'n' && !e.ctrlKey && !e.metaKey) { e.preventDefault(); openAddAccount(A.platform); }
  }
});

// ---------------- Contacts ----------------
async function viewContacts() {
  $('#view').innerHTML = `<div class="row" style="margin-bottom:10px"><h2 style="margin:0;flex:1">Contacts</h2>
    <label class="btn ghost">⬆ Import vCard / CSV<input type="file" accept=".vcf,.csv" class="hidden" onchange="importContacts(this)"></label></div>
    <div class="toolbar"><input class="search" id="cq" placeholder="Search contacts by name, phone, email, org…"><select id="cacct">${accountOptions('')}</select></div><div id="ctable"></div>`;
  const load = async () => {
    const r = await api('/api/contacts?' + new URLSearchParams({q: $('#cq').value, account_id: $('#cacct').value}));
    $('#ctable').innerHTML = `<div class="muted small" style="margin-bottom:6px">${r.total} contacts</div><table><thead><tr><th>Name</th><th>Phones</th><th>Emails</th><th>Org</th><th>Platform</th><th>Account</th><th></th></tr></thead><tbody>
      ${r.contacts.map(c => `<tr><td><b>${esc(c.name)}</b> ${c.is_sample?'<span class="sample">SAMPLE</span>':''}</td><td class="small">${c.phones.map(esc).join('<br>')}</td><td class="small">${c.emails.map(esc).join('<br>')}</td><td>${esc(c.org||'')}</td><td>${c.platform?pbadge(c.platform):''}</td><td class="small">${esc(c.account_name||'')}</td><td><button class="btn ghost small" onclick="delContact(${c.id})">✕</button></td></tr>`).join('')}</tbody></table>`;
  };
  window._loadContacts = load; let t; $('#cq').oninput = () => { clearTimeout(t); t = setTimeout(load, 200); }; $('#cacct').onchange = load; load();
}
async function delContact(id) { await api('/api/contacts/' + id, {method: 'DELETE'}); window._loadContacts(); }
async function importContacts(inp) { const f = inp.files[0]; const fd = new FormData(); fd.append('kind', f.name.toLowerCase().endsWith('.vcf') ? 'vcf' : 'contacts_csv'); fd.append('file', f); const r = await api('/api/import', {method: 'POST', body: fd}); toast(`Contacts: ${r.created} added, ${r.duplicates} duplicates`); await refreshMeta(); window._loadContacts(); }

// ---------------- Connect apps ----------------
const KIND_FOR = {x: [], instagram: ['instagram_json'], facebook: ['facebook_json'], threads: ['threads_json'], messenger: ['messenger_json'], tiktok: ['tiktok_json'], whatsapp: ['whatsapp_txt'],
  wechat: ['paste','csv','json','html'], douyin: ['paste','csv','json','html'], xiaohongshu: ['paste','csv','json','html'], web: ['bookmarks_html','paste','csv','json']};
async function viewConnect(params) {
  const st = await api('/api/settings');
  if (params.get('x')) toast('X connected ✔'); if (params.get('x_error')) toast('X error: ' + params.get('x_error'));
  $('#view').innerHTML = `<h2>Connect apps</h2>
  <p class="muted">Choose which apps SaveHub collects from (toggle). Honest status: only <span class="method live_api">LIVE API</span> X has an official bookmarks API. Every other app has <b>no public API for your saved/favourite items</b>, so SaveHub imports their <b>official data-export files</b> or links you paste.</p>
  <div class="grid">${PLATFORMS.map(p => `<div class="card">
    <div class="row">${pbadge(p.id)}<span class="method ${p.method}">${p.method === 'live_api' ? 'LIVE API' : 'IMPORT'}</span><span style="flex:1"></span>
      <label class="small row"><input type="checkbox" ${p.enabled ? 'checked' : ''} onchange="togglePlatform('${p.id}', this.checked)"> enabled</label></div>
    <div class="small">${esc(p.detail)}</div><div class="muted small">${p.count} items in library</div>
    ${p.id === 'x' ? `<div class="small">Client ID: ${st.x.client_id_set ? '✔ set' : '✖ not set (Settings)'} · ${st.x.connected ? '✔ connected as @' + esc(st.x.user?.username || '') : 'not connected'}</div>
      <div class="row"><a class="btn small" href="/api/x/auth">Connect X (OAuth 2.0 PKCE)</a><button class="btn ghost small" onclick="xSync()">Sync bookmarks</button></div>`
    : `<div class="row"><button class="btn small" onclick="importModal('${p.id}')">Import…</button></div>`}
  </div>`).join('')}</div>
  <div class="panel" style="margin-top:16px"><h4>Quick paste links (any app — 小红书 / 抖音 / 微信 share text works)</h4>
    <textarea id="paste" placeholder="Paste one or more links or share texts here…"></textarea>
    <div class="row" style="margin-top:8px"><select id="pplat"><option value="auto">Auto-detect platform</option>${PLATFORMS.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select>
    <select id="pacct">${accountOptions('').replace('All accounts','— link to account —')}</select><button class="btn" onclick="doPaste()">Import pasted links</button></div></div>`;
}
async function togglePlatform(id, on) {
  const st = (await api('/api/settings')).settings; const s = new Set(st.enabled_platforms); on ? s.add(id) : s.delete(id);
  await jpost('/api/settings', {enabled_platforms: [...s]}, 'PUT'); await refreshMeta(); toast(`${pname(id)} ${on ? 'enabled' : 'disabled'}`);
}
async function xSync() { try { const r = await jpost('/api/x/sync'); toast(`X: fetched ${r.fetched}, new ${r.created}`); } catch (e) { toast(e.message); } }
const KIND_HELP = {instagram_json: "Instagram app → Accounts Center → Your information → Download your information → JSON. Upload your_instagram_activity/saved/saved_posts.json (or saved_collections.json).",
  facebook_json: "Facebook → Settings → Download your information → JSON. Upload saved_items_and_collections/saved_items_and_collections.json.",
  threads_json: "Accounts Center → Download your information → JSON, include Threads. Upload the threads saved/liked JSON.",
  messenger_json: "Download your information → Messages (JSON). Upload a message_1.json — links shared in the chat become items.",
  tiktok_json: "TikTok → Settings → Account → Download your data → JSON. Upload user_data.json (Favorite Videos & Like List are imported).",
  whatsapp_txt: "Open chat → ⋮ → More → Export chat → Without media. Upload the .txt — every link becomes an item (author = sender).",
  bookmarks_html: "Chrome/Edge: Bookmarks manager → ⋮ → Export bookmarks. Firefox: Library → Import and Backup → Export to HTML.",
  html: "Any HTML page/file with links.", csv: "CSV with columns: url,title,author,date,notes,platform (header row required).",
  json: 'JSON array of links or objects {"url","title","author","date","notes"}.', paste: "Paste share texts or links, one per line."};
function importModal(pid) {
  const kinds = KIND_FOR[pid] || ['paste'];
  modal(`<h3>Import into ${esc(pname(pid))}</h3><div class="form">
    <label>Format<select id="ikind" onchange="document.getElementById('ihelp').textContent=KIND_HELP[this.value]">${kinds.map(k => `<option value="${k}">${k}</option>`).join('')}</select></label>
    <label>Link to account<select id="iacct">${accountOptions('', pid).replace('All accounts','— none —')}</select></label>
    <p class="full small muted" id="ihelp">${esc(KIND_HELP[kinds[0]])}</p>
    <label class="full">File<input type="file" id="ifile"></label><label class="full">…or paste text<textarea id="itext"></textarea></label></div>
    <div class="row"><button class="btn" onclick="doImport('${pid}')">Import</button><button class="btn ghost" onclick="closeModal()">Cancel</button></div>`);
}
async function doImport(pid) {
  const fd = new FormData(); fd.append('kind', $('#ikind').value); fd.append('platform', pid); fd.append('account_id', $('#iacct').value);
  if ($('#ifile').files[0]) fd.append('file', $('#ifile').files[0]); else fd.append('text', $('#itext').value);
  try { const r = await api('/api/import', {method: 'POST', body: fd}); closeModal(); toast(`Imported ${r.created} new (${r.duplicates} duplicates, ${r.parsed} parsed)`); await refreshMeta(); route(); } catch (e) { toast(e.message); }
}
async function doPaste() {
  const fd = new FormData(); fd.append('kind', 'paste'); fd.append('platform', $('#pplat').value); fd.append('account_id', $('#pacct').value); fd.append('text', $('#paste').value);
  try { const r = await api('/api/import', {method: 'POST', body: fd}); toast(`Imported ${r.created} new (${r.duplicates} duplicates)`); await refreshMeta(); route(); } catch (e) { toast(e.message); }
}

// ---------------- Settings ----------------
async function viewSettings() {
  const {settings: s, ai, x} = await api('/api/settings');
  $('#view').innerHTML = `<h2>Settings</h2><div class="cols">
  <div class="panel"><h4>🤖 AI tagging provider</h4><p class="small muted">Active now: <b>${esc(ai.active)}</b> (${esc(ai.model)}). Keys come from env only: OPENAI_API_KEY ${ai.openai_key ? '✔' : '✖'} · XAI_API_KEY ${ai.xai_key ? '✔' : '✖'}. Without a key the local keyword + TF-IDF tagger is used.</p>
    <div class="form"><label>Provider<select id="s_prov">${['auto','openai','xai','local'].map(v => `<option ${s.ai.provider===v?'selected':''}>${v}</option>`).join('')}</select></label>
    <label>Model (optional)<input id="s_model" value="${esc(s.ai.model)}" placeholder="gpt-4o-mini / grok-3-mini"></label><label class="full">Base URL (optional, OpenAI-compatible)<input id="s_base" value="${esc(s.ai.base_url)}"></label></div></div>
  <div class="panel"><h4>𝕏 X API (OAuth 2.0 PKCE)</h4><p class="small muted">Create an app at developer.x.com, enable OAuth 2.0 (type: Native/public), add the callback URL below. Bookmarks require an X API tier that includes bookmark lookup.</p>
    <div class="form"><label class="full">Client ID<input id="s_xid" value="${esc(s.x.client_id)}"></label><label class="full">Client secret (only for confidential apps)<input id="s_xsec" value="${esc(s.x.client_secret)}"></label>
    <label class="full">Callback URL<input id="s_xcb" value="${esc(s.x.redirect_uri)}"></label></div><p class="small">${x.connected ? '✔ Connected' : 'Not connected'}</p></div>
  <div class="panel"><h4>Notion sync <span class="muted small">(optional · untested without your token)</span></h4>
    <label class="row small"><input type="checkbox" id="s_nen" ${s.notion.enabled?'checked':''}> Enable Notion sync</label>
    <div class="form"><label class="full">Integration token<input id="s_ntok" value="${esc(s.notion.token)}" placeholder="secret_… / ntn_…"></label><label class="full">Database ID<input id="s_ndb" value="${esc(s.notion.database_id)}"></label></div>
    <p class="small muted">Database properties: Name (title), URL, Platform (select), Tags (multi-select), Saved (date), Summary (text). Share the database with your integration.</p></div>
  <div class="panel"><h4>Google Drive export <span class="muted small">(optional)</span></h4>
    <label class="row small"><input type="checkbox" id="s_den" ${s.drive.enabled?'checked':''}> Enable Drive sync</label>
    <div class="form"><label>Mode<select id="s_dmode"><option value="folder" ${s.drive.mode==='folder'?'selected':''}>folder (Drive for desktop synced folder)</option><option value="service_account" ${s.drive.mode==='service_account'?'selected':''}>service_account (untested)</option></select></label>
    <label>Folder path<input id="s_dpath" value="${esc(s.drive.folder_path)}"></label><label>Service account JSON path<input id="s_dsa" value="${esc(s.drive.service_account_file)}"></label><label>Drive folder ID<input id="s_dfid" value="${esc(s.drive.folder_id)}"></label></div>
    <p class="small muted">Writes savehub_export.json / .csv / .md.</p></div></div>
  <div class="row" style="margin-top:14px"><button class="btn" onclick="saveSettings()">Save settings</button><button class="btn ghost" onclick="syncNow()">Sync now</button><button class="btn ghost" onclick="jpost('/api/samples/seed').then(()=>{refreshMeta();toast('Samples re-seeded')})">Re-seed samples</button><button class="btn danger" onclick="clearSamples()">Clear sample data</button></div>`;
}
async function saveSettings() {
  await jpost('/api/settings', {ai: {provider: $('#s_prov').value, model: $('#s_model').value, base_url: $('#s_base').value},
    x: {client_id: $('#s_xid').value, client_secret: $('#s_xsec').value, redirect_uri: $('#s_xcb').value},
    notion: {enabled: $('#s_nen').checked, token: $('#s_ntok').value, database_id: $('#s_ndb').value},
    drive: {enabled: $('#s_den').checked, mode: $('#s_dmode').value, folder_path: $('#s_dpath').value, service_account_file: $('#s_dsa').value, folder_id: $('#s_dfid').value}}, 'PUT');
  toast('Settings saved'); await refreshMeta(); viewSettings();
}
async function syncNow() { const r = await jpost('/api/sync'); toast(Object.entries(r).map(([k, v]) => `${k}: ${v.skipped || (v.ok ? 'ok' : v.error || JSON.stringify(v.errors))}`).join(' · ')); }

// ---------------- router ----------------
const ROUTES = {library: viewLibrary, summary: viewSummary, accounts: viewAccounts, contacts: viewContacts, connect: viewConnect, settings: viewSettings};
async function route() {
  const [path, qs] = (location.hash.slice(2) || 'library').split('?');
  document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('active', a.getAttribute('href') === '#/' + path));
  try { await (ROUTES[path] || viewLibrary)(new URLSearchParams(qs || '')); } catch (e) { $('#view').innerHTML = `<div class="panel">Error: ${esc(e.message)}</div>`; }
}
window.addEventListener('hashchange', route);
refreshMeta().then(route);
