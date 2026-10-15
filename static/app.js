const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api = async (p, o = {}) => { const r = await fetch(p, o); const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.detail || r.statusText); return j; };
const jpost = (p, b, m = 'POST') => api(p, {method: m, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(b || {})});
const toast = m => { const t = $('#toast'); t.textContent = m; t.classList.remove('hidden'); clearTimeout(t._h); t._h = setTimeout(() => t.classList.add('hidden'), 3500); };
const COLORS = {x:'#111',threads:'#333',instagram:'#d62976',facebook:'#1877f2',messenger:'#0084ff',whatsapp:'#25d366',wechat:'#07c160',tiktok:'#ff0050',douyin:'#161823',xiaohongshu:'#ff2442',web:'#6b7280'};
let PLATFORMS = [], ACCOUNTS = [];
const pname = id => (PLATFORMS.find(p => p.id === id) || {}).name || id;
const pbadge = id => `<span class="pbadge" style="background:${COLORS[id] || '#555'}">${esc(pname(id))}</span>`;
const fdate = s => s ? new Date(s).toLocaleDateString(undefined, {year:'numeric', month:'short', day:'numeric'}) : '';
const modal = html => { $('#modalcard').innerHTML = html; $('#modal').classList.remove('hidden'); };
const closeModal = () => $('#modal').classList.add('hidden');
$('#modal').addEventListener('click', e => { if (e.target.id === 'modal') closeModal(); });

// theme
const setTheme = t => { document.documentElement.dataset.theme = t; localStorage.theme = t; };
setTheme(localStorage.theme || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'));
$('#theme').onclick = () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');

async function refreshMeta() {
  const [h, p, a] = await Promise.all([api('/api/health'), api('/api/platforms'), api('/api/accounts')]);
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
const A = {q: '', platform: '', sort: 'platform', order: 'asc'};
async function viewAccounts() {
  $('#view').innerHTML = `<div class="row" style="margin-bottom:10px"><h2 style="margin:0;flex:1">Accounts</h2>
    <button class="btn" onclick="accountModal()">+ Add account</button>
    <label class="btn ghost">⬆ Import CSV<input type="file" accept=".csv" class="hidden" onchange="importAccounts(this)"></label>
    <a class="btn ghost" href="/api/accounts/export.csv">⬇ Export CSV</a></div>
  <p class="muted small">All your accounts across platforms (multiple per platform). Items and contacts can be linked to an account. 🔒 SaveHub intentionally <b>does not store passwords</b> — keep those in a password manager.</p>
  <div class="toolbar"><input class="search" id="aq" placeholder="Search name, handle, email, phone, notes, tags…" value="${esc(A.q)}">
    <select id="ap"><option value="">All platforms</option>${PLATFORMS.map(p => `<option value="${p.id}" ${A.platform===p.id?'selected':''}>${esc(p.name)}</option>`).join('')}</select></div>
  <div id="atable"></div>`;
  let t; $('#aq').oninput = e => { clearTimeout(t); t = setTimeout(() => { A.q = e.target.value; loadAccounts(); }, 200); };
  $('#ap').onchange = e => { A.platform = e.target.value; loadAccounts(); };
  loadAccounts();
}
async function loadAccounts() {
  const r = await api('/api/accounts?' + new URLSearchParams(A));
  const cols = [['platform','Platform'],['display_name','Name'],['handle','Handle'],['','Linked email / phone'],['','Tags'],['status','Status'],['','Connected via'],['','Items'],['created_at','Created'],['','']];
  $('#atable').innerHTML = `<div class="muted small" style="margin-bottom:6px">${r.total} accounts</div><table><thead><tr>${cols.map(([k,l]) => `<th ${k?`onclick="A.order=A.sort==='${k}'&&A.order==='asc'?'desc':'asc';A.sort='${k}';loadAccounts()"`:''}>${l}${A.sort===k?(A.order==='desc'?' ↓':' ↑'):''}</th>`).join('')}</tr></thead><tbody>
  ${r.accounts.map(a => `<tr><td>${pbadge(a.platform)}</td><td><b>${esc(a.display_name||'')}</b> ${a.is_sample?'<span class="sample">SAMPLE</span>':''}<div class="muted small">${esc(a.notes||'')}</div></td>
    <td>${a.profile_url?`<a href="${esc(a.profile_url)}" target="_blank">@${esc(a.handle||'')}</a>`:esc(a.handle||'')}</td><td class="small">${esc(a.linked_email||'')}<br>${esc(a.linked_phone||'')}</td>
    <td>${(a.tags||[]).map(t => `<span class="tag">${esc(t)}</span>`).join(' ')}</td><td class="small">${esc(a.status||'')}</td><td class="small">${esc(a.connected_via||'')}</td>
    <td class="small"><a href="#" onclick="L.account='${a.id}';location.hash='#/library';return false">${a.item_count} items</a><br>${a.contact_count} contacts</td><td class="small">${fdate(a.created_at)}</td>
    <td><button class="btn ghost small" onclick='accountModal(${JSON.stringify(a).replace(/'/g, "&#39;")})'>Edit</button></td></tr>`).join('')}</tbody></table>`;
}
function accountModal(a = {}) {
  const f = (k, l, full) => `<label class="${full?'full':''}">${l}<input id="f_${k}" value="${esc(Array.isArray(a[k]) ? a[k].join(', ') : a[k] || '')}"></label>`;
  modal(`<h3>${a.id ? 'Edit' : 'Add'} account</h3><div class="form">
    <label>Platform<select id="f_platform">${PLATFORMS.map(p => `<option value="${p.id}" ${a.platform===p.id?'selected':''}>${esc(p.name)}</option>`).join('')}</select></label>
    ${f('display_name','Display name')}${f('handle','Handle / username')}${f('profile_url','Profile URL')}${f('linked_email','Linked email')}${f('linked_phone','Linked phone')}
    ${f('tags','Tags (comma separated)')}<label>Status<select id="f_status">${['active','connected','dormant','closed'].map(s => `<option ${a.status===s?'selected':''}>${s}</option>`).join('')}</select></label>
    ${f('connected_via','Connected via')}${f('notes','Notes', true)}</div><p class="muted small">🔒 No password field by design.</p>
    <div class="row"><button class="btn" onclick="saveAccount(${a.id || 0})">Save</button><button class="btn ghost" onclick="closeModal()">Cancel</button><span style="flex:1"></span>${a.id ? `<button class="btn danger" onclick="delAccount(${a.id})">Delete</button>` : ''}</div>`);
}
async function saveAccount(id) {
  const b = {}; ['platform','display_name','handle','profile_url','linked_email','linked_phone','tags','status','connected_via','notes'].forEach(k => b[k] = $('#f_' + k).value);
  b.tags = b.tags.split(',').map(s => s.trim()).filter(Boolean);
  if (id) await jpost('/api/accounts/' + id, b, 'PUT'); else await jpost('/api/accounts', b);
  closeModal(); await refreshMeta(); loadAccounts();
}
async function delAccount(id) { if (!confirm('Delete account? Linked items/contacts are kept (unlinked).')) return; await api('/api/accounts/' + id, {method: 'DELETE'}); closeModal(); await refreshMeta(); loadAccounts(); }
async function importAccounts(inp) { const fd = new FormData(); fd.append('kind', 'accounts_csv'); fd.append('file', inp.files[0]); const r = await api('/api/import', {method: 'POST', body: fd}); toast(`Accounts: ${r.created} added, ${r.duplicates} duplicates`); await refreshMeta(); loadAccounts(); }

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
