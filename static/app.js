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
const COLORS = {x:'#111',threads:'#333',instagram:'#d62976',facebook:'#0866ff',messenger:'#0084ff',whatsapp:'#25d366',wechat:'#07c160',tiktok:'#ff0050',douyin:'#161823',xiaohongshu:'#ff2442',icloud:'#3693f3',web:'#6b7280'};
let PLATFORMS = [], ACCOUNTS = [], VAULT = {enabled: false};
const pname = id => (PLATFORMS.find(p => p.id === id) || {}).name || id;
// Platform badge: real brand logo (static/logos/<id>.svg, from Simple Icons) on the brand colour + the platform name.
const pbadge = id => `<span class="pbadge" title="${esc(pname(id))}">${pmark(id, 'xs')}<span class="pn">${esc(pname(id))}</span></span>`;
const fdate = s => s ? new Date(s).toLocaleDateString(undefined, {year:'numeric', month:'short', day:'numeric'}) : '';
let MODAL_RET = null, MODAL_ON_CLOSE = null, MODAL_SEQ = 0;
const modal = (html, cls = '', labelledby = '') => {
  const m = $('#modal'), c = $('#modalcard');
  if (m.classList.contains('hidden')) MODAL_RET = document.activeElement;
  c.className = 'modal-card ' + cls; c.innerHTML = html;
  labelledby ? c.setAttribute('aria-labelledby', labelledby) : c.removeAttribute('aria-labelledby');
  const seq = ++MODAL_SEQ; m.classList.remove('hidden'); requestAnimationFrame(() => { if (seq === MODAL_SEQ) m.classList.add('show'); });   // closed before the frame? stay closed
  if (!c.contains(document.activeElement)) setTimeout(() => { if (!c.contains(document.activeElement)) { const f = c.querySelector('input,select,textarea,button'); f && f.focus(); } }, 30);
};
const closeModal = () => {
  const m = $('#modal'); if (m.classList.contains('hidden')) return;
  const seq = ++MODAL_SEQ; m.classList.remove('show'); setTimeout(() => { if (seq === MODAL_SEQ) m.classList.add('hidden'); }, 180);
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
  const [h, p, a, v] = await Promise.all([api('/api/health'), api('/api/platforms'), api('/api/accounts?limit=5000'), api('/api/vault/status').catch(() => ({enabled: false, reason: 'Vault status unavailable'}))]);
  PLATFORMS = p; ACCOUNTS = a.accounts; VAULT = v; STATS = h.stats;
  $('#statline').textContent = `${h.stats.items} items · ${h.stats.contacts} contacts · ${h.stats.accounts} accounts · tagger: ${h.ai.active}`;
  $('#samplebar').classList.toggle('hidden', !h.stats.samples); $('#samplecount').textContent = h.stats.samples;
}
async function clearSamples() { if (!confirm('Delete all SAMPLE items, contacts and accounts?')) return; const r = await jpost('/api/samples/clear'); toast(`Removed ${r.items} items, ${r.contacts} contacts, ${r.accounts} accounts`); await refreshMeta(); route(); }
const accountOptions = (sel, platform) => `<option value="">All accounts</option>` + ACCOUNTS.filter(a => !platform || a.platform === platform).map(a => `<option value="${a.id}" ${String(sel) === String(a.id) ? 'selected' : ''}>${esc(pname(a.platform))} · ${esc(a.display_name || a.handle)}${a.is_sample ? ' (sample)' : ''}</option>`).join('');

// ---------------- Accounts ----------------
const PMETA = {
  x:           {mark: 'X',  bg: '#000', at: true, ph: 'username', hint: 'Your X username, e.g. @jsyip'},
  threads:     {mark: '@',  bg: '#000', at: true, ph: 'username', hint: 'Usually the same as your Instagram username'},
  instagram:   {mark: 'IG', bg: 'linear-gradient(45deg,#f09433,#e6683c 25%,#dc2743 50%,#cc2366 75%,#bc1888)', at: true, ph: 'username'},
  facebook:    {mark: 'f',  bg: '#0866ff', pre: 'fb.com/', ph: 'username or profile id'},
  messenger:   {mark: 'm',  bg: 'linear-gradient(45deg,#0099ff,#a033ff 60%,#ff5280)', pre: 'm.me/', ph: 'username'},
  whatsapp:    {mark: 'WA', bg: '#25d366', pre: 'tel', ph: '+60 12-345 6789', label: 'Phone number', hint: 'The phone number this WhatsApp account uses'},
  wechat:      {mark: '微', bg: '#07c160', pre: 'ID', ph: 'WeChat ID · 微信号', label: 'WeChat ID 微信号'},
  tiktok:      {mark: 'TT', bg: '#010101', at: true, ph: 'username', fx: 'tt'},
  douyin:      {mark: '抖', bg: '#161823', pre: 'ID', ph: '抖音号', label: 'Douyin ID 抖音号', fx: 'tt'},
  xiaohongshu: {mark: '红', bg: '#ff2442', pre: 'ID', ph: '小红书号', label: 'RED ID 小红书号'},
  icloud:      {mark: 'iC', bg: '#3693f3', at: true, ph: 'appleid-name', label: 'Apple ID name',
                hint: 'A short name for this Apple ID (letters, digits . _ -), e.g. js.yip for js.yip@icloud.com. Then log in once in a terminal on the box: python -m savehub.collector login icloud --account @js.yip (Apple ID, password, 2FA code; the password is never stored)'},
  web:         {mark: 'www', bg: '#6b7280', ph: 'username', label: 'Username'},
};
const pm = id => PMETA[id] || {mark: String(id || '?').slice(0, 2).toUpperCase(), bg: '#555', ph: 'username'};
const LOGOS = new Set(['x', 'threads', 'instagram', 'facebook', 'messenger', 'whatsapp', 'wechat', 'tiktok', 'douyin', 'xiaohongshu', 'icloud', 'web']);
const logoImg = id => `<img class="plogo" src="/static/logos/${id}.svg" alt="" width="24" height="24" decoding="async" draggable="false">`;
// Brand mark: white brand logo on the platform colour (falls back to the letter mark for unknown platforms).
const pmark = (id, size = '') => LOGOS.has(id)
  ? `<span class="pmark ${size} haslogo" data-logo="${id}" style="background:${pm(id).bg}" aria-hidden="true">${logoImg(id)}</span>`
  : `<span class="pmark ${size} ${pm(id).fx || ''}" style="background:${pm(id).bg}" aria-hidden="true">${esc(pm(id).mark)}</span>`;
const PURL = {x: h => `https://x.com/${h}`, threads: h => `https://www.threads.net/@${h}`, instagram: h => `https://www.instagram.com/${h}/`,
  facebook: h => `https://www.facebook.com/${h}`, messenger: h => `https://m.me/${h}`, tiktok: h => `https://www.tiktok.com/@${h}`,
  whatsapp: h => { const d = h.replace(/\D/g, ''); return d.length >= 7 ? `https://wa.me/${d}` : ''; }};
const safeUrl = u => /^https?:\/\//i.test(String(u || '').trim()) ? String(u).trim() : '';   // no javascript:/data: links
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
const avatar = (a, big) => { const n = a.display_name || a.handle; return `<span class="avatar ${big ? 'big' : ''}" style="--h:${hue(n)}" aria-hidden="true">${esc(initials(n))}<i class="pdot" data-logo="${esc(a.platform)}" style="background:${pm(a.platform).bg}" title="${esc(pname(a.platform))}">${LOGOS.has(a.platform) ? logoImg(a.platform) : ''}</i></span>`; };
const IC = {
  edit: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>',
  trash: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>',
  mail: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/></svg>',
  phone: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="7" y="2" width="10" height="20" rx="2"/><path d="M11 18h2"/></svg>',
  link: '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M14 4h6v6"/><path d="M20 4 10 14"/><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/></svg>',
  grid: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>',
  list: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg>',
  plus: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
  eye: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>',
  eyeoff: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 3l18 18"/><path d="M10.6 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4.2M6.6 6.6C3.8 8.4 2 12 2 12s3.5 7 10 7a9.6 9.6 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>',
  key: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="7.5" cy="15.5" r="4.5"/><path d="M10.7 12.3 21 2M16 7l3 3M18 5l2 2"/></svg>',
  shield: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6Z"/><path d="m9 12 2 2 4-4"/></svg>',
  warn: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M12 3 2 21h20Z"/><path d="M12 10v5M12 18h.01"/></svg>',
  search: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
  sync: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 0 1-15.5 6.2L3 16"/><path d="M3 21v-5h5"/><path d="M3 12a9 9 0 0 1 15.5-6.2L21 8"/><path d="M21 3v5h-5"/></svg>',
};
// ---------------- shared UI: media, covers, badges, tag chips, empty states, skeletons ----------------
const ITEMS = new Map();          // id -> item, for the detail view and image fallbacks
let STATS = {items: 0};
const sameOrigin = u => /^\/static\/[\w./-]+$/.test(u) && !u.includes('..');
// remote thumbnails always go through the authenticated, allow-listed image proxy (CSP img-src is same-origin only)
const mediaSrc = u => { u = String(u || '').trim(); return sameOrigin(u) ? u : /^https?:\/\/[^\s]+$/i.test(u) ? '/api/media?u=' + encodeURIComponent(u) : ''; };
const videoSrc = u => { u = String(u || '').trim(); return sameOrigin(u) || /^https:\/\/[^\s]+$/i.test(u) ? u : ''; };
const fdur = s => { s = Math.round(+s || 0); if (!s) return ''; const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = String(s % 60).padStart(2, '0'); return h ? `${h}:${String(m).padStart(2, '0')}:${x}` : `${m}:${x}`; };
const fdatetime = s => s ? new Date(s).toLocaleString(undefined, {year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'}) : '';
const TYPE_LABEL = {post: 'Post', video: 'Video', photo: 'Photo', note: 'Note', file: 'File', contact: 'Contact'};
const BRAND = {x: ['#16181c', '#3b4250'], threads: ['#141414', '#4a4b50'], instagram: ['#f58529', '#dd2a7b', '#8134af'], facebook: ['#0b5fe0', '#3f8cff'],
  messenger: ['#0084ff', '#a033ff'], whatsapp: ['#0b7a6b', '#1fae5b'], wechat: ['#058a45', '#20b866'], tiktok: ['#121212', '#c2134a'], douyin: ['#161823', '#b8123f'],
  xiaohongshu: ['#e0102f', '#ff5a6e'], icloud: ['#1f6fd1', '#4aa3f5'], web: ['#3b4556', '#5b6474']};
const ICX = (p, w = 16) => `<svg viewBox="0 0 24 24" width="${w}" height="${w}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${p}</svg>`;
Object.assign(IC, {
  play: '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" focusable="false"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.4-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5Z" fill="currentColor"/></svg>',
  images: ICX('<rect x="7" y="7" width="14" height="14" rx="2"/><path d="M3 17V5a2 2 0 0 1 2-2h12"/>', 13),
  chevL: ICX('<path d="m15 18-6-6 6-6"/>', 18), chevR: ICX('<path d="m9 18 6-6-6-6"/>', 18), close: ICX('<path d="M18 6 6 18M6 6l12 12"/>', 18),
  copy: ICX('<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/>', 15), spark: ICX('<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>', 15),
  video: ICX('<rect x="3" y="6" width="13" height="12" rx="2"/><path d="m16 10 5-3v10l-5-3"/>', 13), photo: ICX('<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="m21 16-5-5-9 9"/>', 13),
  note: ICX('<path d="M5 3h10l4 4v14H5z"/><path d="M9 12h6M9 16h4"/>', 13), post: ICX('<path d="M4 5h16v11H8l-4 4z"/>', 13), file: ICX('<path d="M6 2h8l5 5v15H6z"/><path d="M14 2v5h5"/>', 13),
  contact: ICX('<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>', 13), chat: ICX('<path d="M21 12a8 8 0 0 1-11.6 7.1L3 21l1.9-6.4A8 8 0 1 1 21 12Z"/>', 14),
  wa: ICX('<path d="M3 21l1.7-5A8.5 8.5 0 1 1 8 19.3z"/><path d="M9 9.5c.3 2 2.5 4.2 4.5 4.5l1.2-1.1 1.8.8"/>', 14), upload: ICX('<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>', 15),
  check: ICX('<path d="m5 12 5 5L20 7"/>', 14), refresh: ICX('<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>', 15), terminal: ICX('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3M13 15h4"/>', 15),
});
const TYPE_IC = t => IC[t] || IC.post;

// Generated cover for items without media: brand gradient, platform logo and the first words of the title. Never empty.
const coverWords = i => { const t = String(i.title || i.url || pname(i.platform)).replace(/\s+/g, ' ').trim(), cjk = /[\u3400-\u9fff]/.test(t), max = cjk ? 26 : 64;
  return t.length > max ? t.slice(0, max).replace(cjk ? /$/ : /\s+\S*$/, '') + '…' : t; };
const genInner = i => { const c = BRAND[i.platform] || BRAND.web;
  return `<div class="gen" style="--g1:${c[0]};--g2:${c[1]};--g3:${c[2] || c[1]}" data-gen>${LOGOS.has(i.platform) ? `<img class="gen-logo" src="/static/logos/${esc(i.platform)}.svg" alt="" draggable="false">` : ''}
    <span class="gen-t">${esc(coverWords(i))}</span><span class="gen-k">${TYPE_IC(i.type)}${esc(TYPE_LABEL[i.type] || 'Post')}${(m => i.type === 'file' && m ? ' · ' + esc(m[1].toUpperCase()) : '')(String(i.title || '').match(/\.(\w{1,6})$/))}</span></div>`; };
const firstImg = i => i.thumbnail_url || (i.images || [])[0] || '';
const coverAspect = (i, lo = 0.8, hi = 1.78) => Math.min(hi, Math.max(lo, +i.aspect || (i.type === 'video' && ['tiktok', 'douyin'].includes(i.platform) ? 0.5625 : (firstImg(i) ? 1.5 : 1.6))));
function cardCover(i, cls = '') {
  const src = mediaSrc(firstImg(i)), n = (i.images || []).length;
  return `<div class="cover ${cls}" style="aspect-ratio:${coverAspect(i).toFixed(3)}">
    ${src ? `<img class="cover-img" src="${esc(src)}" alt="" loading="lazy" decoding="async" draggable="false" data-id="${i.id}">` : genInner(i)}
    <span class="cv-plat" title="${esc(pname(i.platform))}">${pmark(i.platform, 'sm')}</span>
    ${i.type === 'video' ? `<span class="cv-play" aria-hidden="true">${IC.play}</span>${i.duration ? `<span class="cv-badge dur" aria-label="Duration ${fdur(i.duration)}">${fdur(i.duration)}</span>` : ''}` : ''}
    ${n > 1 ? `<span class="cv-badge cnt" title="${n} images">${IC.images}${n}</span>` : ''}</div>`;
}
// broken remote image -> generated cover / initials, never an empty box
document.addEventListener('error', e => {
  const t = e.target; if (!(t instanceof HTMLImageElement)) return;
  if (t.classList.contains('cover-img') || t.classList.contains('g-img') || t.classList.contains('d-cover-img')) {
    const it = ITEMS.get(+t.dataset.id) || {title: t.alt, platform: 'web'}, box = t.parentElement; t.remove();
    if (box && !box.querySelector('[data-gen]')) box.insertAdjacentHTML('afterbegin', genInner(it));
  } else if (t.classList.contains('av-img')) t.remove();
}, true);

// person avatar (author / contact): photo when we have one, initials on a soft hue otherwise
const personAv = (name, url, size = '') => { const s = mediaSrc(url);
  return `<span class="pav ${size}" style="--h:${hue(name)}" aria-hidden="true">${esc(initials(name))}${s ? `<img class="av-img" src="${esc(s)}" alt="" loading="lazy" decoding="async">` : ''}</span>`; };
// account badge: avatar circle (initials) + platform logo overlaid bottom-right (>= 14px) + handle; tooltip = full account
const acctOf = i => i && i.account_id ? (ACCOUNTS.find(a => a.id === i.account_id) || {id: i.account_id, platform: i.account_platform || i.platform, display_name: i.account_name, handle: i.account_handle}) : null;
const acctFull = a => `${a.display_name || showHandle(a.platform, a.handle) || 'Account'}${a.handle && a.display_name ? ' · ' + showHandle(a.platform, a.handle) : ''} — ${pname(a.platform)}${a.is_sample ? ' (sample)' : ''}`;
const acctBadge = (a, o = {}) => !a ? '' : `<span class="abadge ${o.size || ''}" title="${esc(acctFull(a))}">${avatar(a, false)}<span class="ab-h">${esc(o.name ? (a.display_name || showHandle(a.platform, a.handle)) : (showHandle(a.platform, a.handle) || a.display_name || 'account'))}</span>${o.name && a.handle && a.display_name ? `<span class="ab-s">${esc(showHandle(a.platform, a.handle))}</span>` : ''}</span>`;

// tag chips: soft colour per tag (deterministic hue), AA contrast in light and dark, removable in edit mode
const thue = t => { let h = 2166136261; for (const c of String(t).toLowerCase()) { h ^= c.codePointAt(0); h = Math.imul(h, 16777619); } return (h >>> 0) % 360; };
const tagChip = (t, o = {}) => { const el = o.el || 'span';
  return `<${el} class="tag ${o.cls || ''}" style="--th:${thue(t)}" data-tag="${esc(t)}" ${el === 'button' ? 'type="button"' : ''} ${o.attrs || ''}><span class="hash" aria-hidden="true">#</span>${esc(t)}${o.n != null ? `<span class="tn">${o.n}</span>` : ''}${o.remove ? `<button type="button" class="tag-x" data-rm="${esc(t)}" aria-label="Remove tag ${esc(t)}">${ICX('<path d="M18 6 6 18M6 6l12 12"/>', 12)}</button>` : ''}</${el}>`; };
const tagList = (tags, max = 3, o = {}) => { tags = tags || []; return tags.slice(0, max).map(t => tagChip(t, o)).join('') +
  (tags.length > max ? `<span class="tag more" title="${esc(tags.slice(max).map(x => '#' + x).join('  '))}">+${tags.length - max}</span>` : ''); };

// ---- empty states: inline SVG illustration (theme-aware via CSS vars), headline, one helpful line, a clear action ----
const SV = (b) => `<svg class="illu-svg" viewBox="0 0 200 140" width="200" height="140" aria-hidden="true" focusable="false">${b}</svg>`;
const ILLU = {
  library: SV(`<ellipse class="i-bg" cx="100" cy="78" rx="90" ry="56"/><g class="i-float"><g transform="rotate(-9 66 74)"><rect class="i-card" x="32" y="36" width="66" height="80" rx="11"/><rect class="i-soft2" x="40" y="44" width="50" height="32" rx="7"/><rect class="i-line" x="40" y="84" width="42" height="5" rx="2.5"/><rect class="i-line" x="40" y="95" width="28" height="5" rx="2.5"/></g>
    <g transform="rotate(7 132 70)"><rect class="i-card" x="98" y="28" width="68" height="84" rx="11"/><rect class="i-soft" x="106" y="36" width="52" height="34" rx="7"/><circle class="i-ac2" cx="118" cy="53" r="5"/><path class="i-ac2 i-op" d="m110 68 14-12 10 8 8-6 16 10z"/><rect class="i-line" x="106" y="78" width="44" height="5" rx="2.5"/><rect class="i-line" x="106" y="89" width="30" height="5" rx="2.5"/><path class="i-ac" d="M144 22h15v28l-7.5-5.5L144 50z"/></g></g>
    <circle class="i-ac2" cx="30" cy="34" r="4"/><path class="i-spark" d="m172 100 3 6.5 6.5 3-6.5 3-3 6.5-3-6.5-6.5-3 6.5-3z"/><circle class="i-ac" cx="178" cy="40" r="3"/>`),
  search: SV(`<ellipse class="i-bg" cx="100" cy="78" rx="90" ry="56"/><rect class="i-card" x="44" y="34" width="84" height="78" rx="12"/><rect class="i-line" x="56" y="50" width="54" height="6" rx="3"/><rect class="i-line" x="56" y="64" width="40" height="6" rx="3"/><rect class="i-line" x="56" y="78" width="48" height="6" rx="3"/>
    <g class="i-float"><circle class="i-glass" cx="128" cy="78" r="26"/><path class="i-stroke-ac" d="m147 97 18 18" stroke-width="9" stroke-linecap="round"/><path class="i-q" d="M121 71a7 7 0 1 1 10 6.3c-2 1-3 2.3-3 4.2v1.5M128 89v.5" stroke-width="4" stroke-linecap="round" fill="none"/></g><circle class="i-ac2" cx="34" cy="40" r="4"/><path class="i-spark" d="m170 34 2.5 5 5 2.5-5 2.5-2.5 5-2.5-5-5-2.5 5-2.5z"/>`),
  filter: SV(`<ellipse class="i-bg" cx="100" cy="78" rx="90" ry="56"/><g class="i-float"><path class="i-card" d="M52 34h96a6 6 0 0 1 4.6 9.8L116 86v24a5 5 0 0 1-2.8 4.5l-16 8A5 5 0 0 1 90 118V86L47.4 43.8A6 6 0 0 1 52 34Z"/><path class="i-soft" d="M60 44h80l-28 32H88z"/></g>
    <circle class="i-ac" cx="40" cy="96" r="6"/><circle class="i-ac2" cx="162" cy="96" r="6"/><circle class="i-spark" cx="164" cy="44" r="4"/><path class="i-stroke-mut" d="M28 70h14M158 70h14" stroke-width="3" stroke-linecap="round"/>`),
  accounts: SV(`<ellipse class="i-bg" cx="100" cy="78" rx="90" ry="56"/><g class="i-float"><circle class="i-card" cx="64" cy="66" r="24"/><circle class="i-soft2" cx="64" cy="60" r="8"/><path class="i-soft2" d="M48 80a16 12 0 0 1 32 0z"/><circle class="i-card" cx="136" cy="66" r="24"/><circle class="i-soft" cx="136" cy="60" r="8"/><path class="i-soft" d="M120 80a16 12 0 0 1 32 0z"/>
    <circle class="i-card i-hi" cx="100" cy="58" r="30"/><circle class="i-ac i-op" cx="100" cy="51" r="10"/><path class="i-ac i-op" d="M80 76a20 14 0 0 1 40 0z"/></g><circle class="i-ac" cx="124" cy="94" r="13"/><path class="i-plus" d="M124 88v12M118 94h12" stroke-width="3.5" stroke-linecap="round"/><circle class="i-ac2" cx="32" cy="40" r="4"/><path class="i-spark" d="m170 34 2.5 5 5 2.5-5 2.5-2.5 5-2.5-5-5-2.5 5-2.5z"/>`),
  contacts: SV(`<ellipse class="i-bg" cx="100" cy="78" rx="90" ry="56"/><g class="i-float"><rect class="i-card" x="56" y="26" width="88" height="96" rx="12"/><rect class="i-soft2" x="56" y="26" width="88" height="30" rx="12"/><circle class="i-card" cx="100" cy="58" r="16"/><circle class="i-ac2 i-op" cx="100" cy="54" r="6"/><path class="i-ac2 i-op" d="M89 68a11 8 0 0 1 22 0z"/>
    <rect class="i-line" x="72" y="84" width="56" height="6" rx="3"/><rect class="i-line" x="80" y="97" width="40" height="5" rx="2.5"/><rect class="i-ac" x="46" y="40" width="10" height="10" rx="3"/><rect class="i-ac" x="46" y="62" width="10" height="10" rx="3"/><rect class="i-ac" x="46" y="84" width="10" height="10" rx="3"/></g>
    <circle class="i-ac" cx="160" cy="98" r="12"/><path class="i-plus" d="M155 98h10M160 93v10" stroke-width="3" stroke-linecap="round"/><path class="i-spark" d="m34 36 2.5 5 5 2.5-5 2.5-2.5 5-2.5-5-5-2.5 5-2.5z"/>`),
  summary: SV(`<ellipse class="i-bg" cx="100" cy="78" rx="90" ry="56"/><rect class="i-card" x="38" y="30" width="124" height="86" rx="12"/><path class="i-stroke-mut" d="M52 100h96" stroke-width="2"/><g class="i-float"><rect class="i-soft2" x="58" y="78" width="14" height="22" rx="4"/><rect class="i-soft" x="80" y="66" width="14" height="34" rx="4"/><rect class="i-soft2" x="102" y="84" width="14" height="16" rx="4"/><rect class="i-ac i-op" x="124" y="56" width="14" height="44" rx="4"/></g>
    <path class="i-stroke-ac2" d="M56 62c12-14 24 4 36-6s20-12 30-4 18-10 22-14" fill="none" stroke-width="3" stroke-linecap="round" stroke-dasharray="4 6"/><path class="i-spark" d="m170 26 2.5 5 5 2.5-5 2.5-2.5 5-2.5-5-5-2.5 5-2.5z"/><circle class="i-ac2" cx="28" cy="102" r="4"/>`),
  connect: SV(`<ellipse class="i-bg" cx="100" cy="78" rx="90" ry="56"/><g class="i-float"><rect class="i-card" x="28" y="44" width="56" height="56" rx="16"/><rect class="i-soft2" x="40" y="56" width="32" height="32" rx="9"/><rect class="i-card" x="116" y="44" width="56" height="56" rx="16"/><rect class="i-ac i-op" x="128" y="56" width="32" height="32" rx="9"/></g>
    <path class="i-stroke-ac2" d="M86 72h28" stroke-width="3.5" stroke-linecap="round" stroke-dasharray="3 7"/><circle class="i-ac2" cx="100" cy="72" r="7"/><path class="i-spark" d="m100 22 2.5 5 5 2.5-5 2.5-2.5 5-2.5-5-5-2.5 5-2.5z"/>`),
  login: SV(`<ellipse class="i-bg" cx="100" cy="78" rx="90" ry="56"/><g class="i-float"><rect class="i-card" x="58" y="58" width="84" height="62" rx="14"/><path class="i-stroke-mut" d="M76 58V46a24 24 0 0 1 46-9" fill="none" stroke-width="7" stroke-linecap="round"/><circle class="i-ac" cx="100" cy="84" r="8"/><rect class="i-ac" x="97" y="88" width="6" height="16" rx="3"/></g>
    <circle class="i-warn" cx="146" cy="44" r="14"/><path class="i-plus" d="M146 37v8M146 50v.5" stroke-width="3.5" stroke-linecap="round"/><circle class="i-ac2" cx="40" cy="40" r="4"/>`),
  error: SV(`<ellipse class="i-bg" cx="100" cy="78" rx="90" ry="56"/><g class="i-float"><path class="i-card" d="M64 104a26 26 0 0 1-2-52 34 34 0 0 1 64-6 28 28 0 0 1 12 58z"/><path class="i-stroke-mut" d="m84 70 10 10m0-10L84 80m28-10 10 10m0-10-10 10" stroke-width="4" stroke-linecap="round"/><path class="i-stroke-mut" d="M88 94c8-6 16-6 24 0" fill="none" stroke-width="4" stroke-linecap="round"/></g><circle class="i-warn" cx="156" cy="40" r="5"/><path class="i-spark" d="m36 36 2.5 5 5 2.5-5 2.5-2.5 5-2.5-5-5-2.5 5-2.5z"/>`),
};
// actions: {label, href} (a link) or {label, act} (a button handled by EMPTY_ACTS) or {label, html} (custom, e.g. a file picker)
function emptyState({art = 'library', title, text, actions = [], extra = '', compact = false, id = ''}) {
  const btn = (a, k) => a.html ? a.html : a.href ? `<a class="btn ${k === 0 ? 'big' : 'ghost'}" href="${a.href}" ${a.attrs || ''}>${a.icon || ''}${esc(a.label)}</a>`
    : `<button type="button" class="btn ${k === 0 ? 'big' : 'ghost'}" data-empty-act="${a.act}" ${a.data ? `data-arg="${esc(a.data)}"` : ''} ${a.attrs || ''}>${a.icon || ''}${esc(a.label)}</button>`;
  return `<div class="empty ${compact ? 'compact' : ''}" ${id ? `id="${id}"` : ''} data-empty="${art}"><div class="illu">${ILLU[art] || ILLU.library}</div>
    <h3>${esc(title)}</h3><p class="lead">${text}</p>${actions.length ? `<div class="row center eacts">${actions.map(btn).join('')}</div>` : ''}${extra}</div>`;
}
const EMPTY_ACTS = {};
document.addEventListener('click', e => { const b = e.target.closest('[data-empty-act]'); if (b && EMPTY_ACTS[b.dataset.emptyAct]) { e.preventDefault(); EMPTY_ACTS[b.dataset.emptyAct](b.dataset.arg, b); } });
// skeleton shimmer placeholders while loading
const skelCards = (n = 8) => `<div class="igrid skel-grid" aria-hidden="true">${Array.from({length: n}, (_, k) => `<div class="card skel"><div class="sk sk-cover" style="aspect-ratio:${[1.5, 0.8, 1.2, 1.78][k % 4]}"></div><div class="ibody"><div class="sk sk-line w60"></div><div class="sk sk-line"></div><div class="sk sk-line w80"></div><div class="row"><span class="sk sk-pill"></span><span class="sk sk-pill"></span></div></div></div>`).join('')}</div>`;
const skelRows = (n = 6, cls = 'skel-rows') => `<div class="${cls}" aria-hidden="true">${Array.from({length: n}, () => `<div class="skrow"><span class="sk sk-av"></span><div class="grow"><div class="sk sk-line w40"></div><div class="sk sk-line w70"></div></div></div>`).join('')}</div>`;
const skelPanels = () => `<div class="kpis" aria-hidden="true">${'<div class="panel skel"><div class="sk sk-line w40"></div><div class="sk sk-kpi"></div></div>'.repeat(4)}</div><div class="panel skel" style="margin-top:16px"><div class="sk sk-line w30"></div><div class="sk sk-block"></div></div>`;
async function copyText(t, what = 'Link') {
  try { await navigator.clipboard.writeText(t); }
  catch { const x = document.createElement('textarea'); x.value = t; x.style.position = 'fixed'; x.style.opacity = '0'; document.body.appendChild(x); x.select(); document.execCommand('copy'); x.remove(); }
  toast(`${what} copied`, {kind: 'ok'});
}
const statusPill = s => `<span class="spill s-${esc(s || 'active')}">${esc((STATUSES.find(x => x[0] === s) || [0, s || 'active'])[1])}</span>`;
const acctLabel = a => a.display_name || showHandle(a.platform, a.handle) || 'Untitled';
const findAcct = id => (A.all || []).find(a => a.id === id) || ACCOUNTS.find(a => a.id === id);
const isDup = (p, h, exceptId) => { h = cleanHandle(h).toLowerCase(); return h && ACCOUNTS.find(a => a.platform === p && (a.handle || '').toLowerCase() === h && a.id !== exceptId); };

// ---- password vault UI ----
const vaultWarn = (compact) => VAULT.storage === 'persistent' ? '' : `<div class="vwarn ${VAULT.storage === 'ephemeral' ? 'strong' : ''}" role="note">${IC.warn}<div><b>${VAULT.storage === 'ephemeral' ? 'Saved passwords will NOT persist on this server yet.' : 'Heads-up for the hosted version.'}</b>
  ${compact ? 'Render’s free tier has no persistent disk — the database (incl. passwords) is wiped on every restart, redeploy or idle spin-down until a paid persistent disk is attached. Keep a copy in a password manager.'
  : 'Render’s free tier has no persistent disk, so the SQLite database — including every saved password — is wiped on each restart, redeploy or idle spin-down. Passwords only persist once a persistent disk (paid plan) is attached. Keep a copy in your password manager.'}</div></div>`;
const vaultStrip = () => VAULT.enabled
  ? `<div class="vstrip on">${IC.shield}<span><b>Password vault on</b> · ${esc(VAULT.algorithm)} encryption at rest · ${VAULT.count} saved</span></div>${vaultWarn()}`
  : `<div class="vstrip off">${IC.key}<span><b>Password vault off.</b> ${esc((VAULT.reason || '').replace(/^Password vault is off:\s*/i, ''))}</span></div>`;
const pwRow = a => !a.has_password ? '' : `<div class="pwrow" data-pw="${a.id}">${IC.key}<span class="pwval" aria-live="polite">••••••••</span><span class="grow"></span>
  ${VAULT.enabled ? `<button type="button" class="btn ghost xs" data-act="reveal" data-id="${a.id}" aria-pressed="false" aria-label="Reveal password for ${esc(acctLabel(a))}">${IC.eye}<span>Reveal</span></button><button type="button" class="btn ghost xs" data-act="copypw" data-id="${a.id}" aria-label="Copy password for ${esc(acctLabel(a))}">Copy</button>`
    : `<span class="muted small" title="${esc(VAULT.reason || '')}">locked</span>`}</div>`;
const revealSecret = id => jpost(`/api/accounts/${id}/secret/reveal`, {});
async function revealPw(btn, id) {
  const row = btn.closest('.pwrow'), val = row.querySelector('.pwval');
  if (btn.getAttribute('aria-pressed') === 'true') { maskPw(row); return; }
  try { const r = await revealSecret(id); val.textContent = r.password; val.classList.add('shown'); btn.setAttribute('aria-pressed', 'true'); btn.innerHTML = `${IC.eyeoff}<span>Hide</span>`;
    clearTimeout(row._t); row._t = setTimeout(() => maskPw(row), 30000); } catch (e) { toast(e.message, {kind: 'err'}); }
}
function maskPw(row) { const val = row.querySelector('.pwval'), btn = row.querySelector('[data-act=reveal]'); clearTimeout(row._t); val.textContent = '••••••••'; val.classList.remove('shown'); if (btn) { btn.setAttribute('aria-pressed', 'false'); btn.innerHTML = `${IC.eye}<span>Reveal</span>`; } }
async function copyPw(id) {
  try { const {password} = await revealSecret(id);
    try { await navigator.clipboard.writeText(password); } catch { const t = document.createElement('textarea'); t.value = password; t.style.position = 'fixed'; t.style.opacity = '0'; document.body.appendChild(t); t.select(); document.execCommand('copy'); t.remove(); }
    toast('Password copied to clipboard', {kind: 'ok'}); } catch (e) { toast(e.message, {kind: 'err'}); }
}
const vaultField = e => {
  if (!VAULT.enabled) return `<div class="full vfield off"><span class="lbl">Password <span class="opt">optional</span></span><div class="adorn"><input type="password" disabled placeholder="Vault off — passwords can’t be saved" aria-label="Password (disabled)"></div><span class="hint">${IC.key} ${esc(VAULT.reason || 'Password vault is not enabled.')}</span></div>`;
  return `<div class="full vfield"><label for="f_password" class="lbl">Password <span class="opt">optional · encrypted</span></label>
    <div class="adorn pw"><input id="f_password" type="password" autocomplete="new-password" spellcheck="false" autocapitalize="off" placeholder="${e.has_password ? '•••••••• saved — type to replace' : 'Login password for this account'}" aria-describedby="h_password"><button type="button" class="eye" id="pwEye" aria-label="Show password" aria-pressed="false" title="Show / hide">${IC.eye}</button></div>
    <span class="hint" id="h_password">${IC.shield} Encrypted at rest (${esc(VAULT.algorithm)}) with a key that only lives in the server environment; shown only to you after login.</span>
    ${e.has_password ? `<label class="chk"><input type="checkbox" id="f_pw_remove"> Remove the saved password</label>` : ''}
    ${vaultWarn(true)}</div>`;
};
async function saveSecretFor(id) {
  const pw = $('#f_password') ? $('#f_password').value : '', rm = $('#f_pw_remove') && $('#f_pw_remove').checked;
  if (pw) await jpost(`/api/accounts/${id}/secret`, {password: pw}, 'PUT');
  else if (rm) await api(`/api/accounts/${id}/secret`, {method: 'DELETE', headers: {'Content-Type': 'application/json'}});
  return pw ? 'saved' : rm ? 'removed' : '';
}

// ---- auto-sync UI (browser collector; incremental, never un-saves) ----
let SYNC = null, SYNC_T = null;
const ago = sec => sec == null ? '' : sec < 60 ? 'just now' : sec < 3600 ? `${Math.round(sec / 60)} min ago` : sec < 86400 ? `${Math.round(sec / 3600)} h ago` : `${Math.round(sec / 86400)} d ago`;
const syncFor = a => !SYNC || !SYNC.accounts ? null : (SYNC.accounts.find(s => s.account_id === a.id)
  || SYNC.accounts.find(s => s.platform === a.platform && s.account.toLowerCase() === '@' + cleanHandle(a.handle).toLowerCase()));
const syncText = s => ({syncing: 'Syncing…', needs_login: 'Needs login', locked: 'Locked: vault key missing', busy: 'Busy, retrying shortly',
  disabled: 'Collector not available', pending: 'Waiting for first sync',
  error: `Error${s.consecutive_errors > 1 ? ` (${s.consecutive_errors}×)` : ''}${s.next_run ? ' · retry ' + new Date(s.next_run).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) : ''}`,
  synced: `Synced ${ago(s.last_success_ago_s)}${s.items_added_last_run ? ` · +${s.items_added_last_run} new` : ''}`})[s.state] || s.state;
const syncTitle = s => s.state === 'needs_login' ? `Session expired. On the box run: ${s.fix || ''}` : s.last_error ? s.last_error : s.next_run ? `Next sync ${new Date(s.next_run).toLocaleString()}` : '';
const syncRow = s => !s ? '' : `<div class="syncrow st-${esc(s.state)}" title="${esc(syncTitle(s))}">${IC.sync}<span class="stxt">${esc(syncText(s))}</span><span class="grow"></span>${s.state === 'needs_login' ? `<button type="button" class="btn xs" data-empty-act="signin" data-arg="${esc(s.platform)}|${esc(s.account)}">Sign in to ${esc(pname(s.platform))}</button>` : ''}${SYNC.available ? `<button type="button" class="btn ghost xs" data-act="syncnow" data-p="${esc(s.platform)}" data-a="${esc(s.account)}" ${s.state === 'syncing' ? 'disabled' : ''} aria-label="Sync ${esc(s.platform)} ${esc(s.account)} now">Sync now</button>` : ''}</div>`;
const INTERVALS = [2, 5, 10, 15, 30, 60, 120];
function syncStrip() {
  if (!SYNC) return '';
  if (SYNC.error) return `<div class="vstrip off">${IC.sync}<span><b>Auto-sync status unavailable.</b> ${esc(SYNC.error)}</span></div>`;
  if (!SYNC.available) return `<div class="vstrip off">${IC.sync}<span><b>Auto-sync unavailable on this server.</b> ${esc(SYNC.reason || '')}</span></div>`;
  const n = SYNC.accounts.length, nl = SYNC.accounts.filter(a => a.state === 'needs_login').length, er = SYNC.accounts.filter(a => a.state === 'error').length;
  const loop = !SYNC.enabled ? 'paused' : SYNC.loop.running ? 'running' : 'no sync loop running on the box';
  const ivs = INTERVALS.includes(SYNC.interval_minutes) ? INTERVALS : [...INTERVALS, SYNC.interval_minutes].sort((x, y) => x - y);
  const orphans = SYNC.accounts.filter(s => !s.account_id);
  return `<div class="vstrip sync ${SYNC.enabled ? 'on' : ''}">${IC.sync}
    <label class="chk"><input type="checkbox" id="asToggle" ${SYNC.enabled ? 'checked' : ''}> <b>Auto-sync</b></label>
    <label class="chk">every <select id="asInterval" aria-label="Auto-sync interval">${ivs.map(v => `<option value="${v}" ${v === SYNC.interval_minutes ? 'selected' : ''}>${v} min</option>`).join('')}</select></label>
    <span class="muted grow" title="${SYNC.loop.running ? `pid ${esc(SYNC.loop.pid)} · ${esc(SYNC.loop.where)}` : 'Start it on the box: python -m savehub.collector autosync'}">${n} signed-in session${n === 1 ? '' : 's'}${nl ? ` · <b class="warntxt">${nl} need login</b>` : ''}${er ? ` · ${er} with errors` : ''} · ${loop} · never un-saves${SYNC.session_encryption === 'on' ? ' · sessions encrypted' : ''}</span>
    <button type="button" class="btn ghost xs" data-act="syncall" ${n ? '' : 'disabled'}>Sync all now</button></div>
    ${nl ? needsLoginPanel(SYNC.accounts.filter(a => a.state === 'needs_login')) : ''}
    ${orphans.length ? `<div class="syncorph">${orphans.map(s => `<div class="syncchip">${pmark(s.platform, 'xs')}<b>${esc(s.account)}</b>${syncRow(s)}</div>`).join('')}</div>` : ''}`;
}
function needsLoginPanel(list) {
  const f = list[0];
  return `<div class="needlogin big" id="needsLogin" role="status"><div class="nlart">${ILLU.login}</div><div class="grow"><b>${list.length === 1 ? `${esc(pname(f.platform))} needs you to sign in again` : `${list.length} sessions need you to sign in again`}</b>
    <p class="small">${list.length === 1 ? `${esc(f.account)} was signed out, so auto-sync is paused for it. Nothing you saved is lost.` : list.map(s => `${esc(pname(s.platform))} ${esc(s.account)}`).join(' · ') + ' — auto-sync is paused for these. Nothing you saved is lost.'}</p>
    <div class="row">${list.slice(0, 3).map((s, k) => `<button type="button" class="btn ${k ? 'ghost' : ''} small" data-empty-act="signin" data-arg="${esc(s.platform)}|${esc(s.account)}">Sign in to ${esc(pname(s.platform))}${list.length > 1 ? ' ' + esc(s.account) : ''}</button>`).join('')}</div></div></div>`;
}
// per-platform collector backend (auto = open-source library first, browser on error; libraries never un-save)
const BK_LABEL = {auto: 'Auto', playwright: 'Browser'};
function backendPick(p) {
  const b = SYNC && SYNC.backends && SYNC.backends[p];
  if (b && b.native) return `<span class="bkpick muted" title="${esc((b.libraries || []).map(l => `${l.name} ${l.version || '(not installed)'} · ${l.license} · ${l.note}`).join('\n'))}">Collector: ${esc(b.backend)} · read-only</span>`;
  if (!b || !b.options || b.options.length < 2) return '';
  const libs = Object.fromEntries((b.libraries || []).map(l => [l.name, l]));
  const tip = `Collector backend. Auto: ${(b.libraries || []).map(l => l.name).join(', ')} first, browser if it fails. Browser: Playwright scraper (the only one that can un-save).` +
    (b.libraries || []).map(l => `\n${l.name} ${l.version || '(not installed)'} · ${l.license} · ${l.note}`).join('');
  return `<label class="bkpick" title="${esc(tip)}"><span class="muted">Collector</span><select data-act="backend" data-p="${esc(p)}" aria-label="${esc(pname(p))} collector backend">${b.options.map(o =>
    `<option value="${esc(o)}" ${o === b.backend ? 'selected' : ''} ${libs[o] && !libs[o].installed ? 'disabled' : ''}>${esc(BK_LABEL[o] || o)}${o === b.default ? ' (default)' : ''}</option>`).join('')}</select></label>`;
}
async function onBackendChange(e) {
  const sel = e.target.closest('select[data-act="backend"]'); if (!sel) return;
  sel.disabled = true;
  try { SYNC = await jpost('/api/collector/backends', {platform: sel.dataset.p, backend: sel.value}, 'PUT');
    toast(`${pname(sel.dataset.p)}: collector ${BK_LABEL[sel.value] || sel.value}`, {kind: 'ok'}); }
  catch (err) { toast(err.message, {kind: 'err'}); }
  renderAccounts();
}
async function loadSync() {
  try { SYNC = await api('/api/autosync'); } catch (e) { SYNC = {error: e.message, accounts: []}; }
  if (!$('#syncbar')) { clearInterval(SYNC_T); SYNC_T = null; return; }
  $('#syncbar').innerHTML = syncStrip(); renderAccounts();
}
async function syncNowFor(btn, platform, account) {
  if (btn) btn.disabled = true;
  try { const r = await jpost('/api/autosync/run', platform ? {platform, account} : {});
    toast(r.started.length ? `Syncing ${r.started.length} account${r.started.length === 1 ? '' : 's'}…` : (r.busy.length ? 'Already syncing' : 'Nothing to sync'), {kind: 'ok'});
  } catch (e) { toast(e.message, {kind: 'err'}); }
  setTimeout(loadSync, 1500);
}
function onSyncBarClick(e) {
  const b = e.target.closest('[data-act]'); if (!b) return;
  if (b.dataset.act === 'syncall') syncNowFor(b);
  else if (b.dataset.act === 'syncnow') syncNowFor(b, b.dataset.p, b.dataset.a);
}
async function onSyncBarChange(e) {
  const body = e.target.id === 'asToggle' ? {enabled: e.target.checked} : e.target.id === 'asInterval' ? {interval_minutes: +e.target.value} : null;
  if (!body) return;
  try { SYNC = await jpost('/api/autosync', body, 'PUT'); toast(body.enabled === undefined ? `Auto-sync every ${SYNC.interval_minutes} min` : `Auto-sync ${SYNC.enabled ? 'on' : 'paused'}`, {kind: 'ok'}); }
  catch (err) { toast(err.message, {kind: 'err'}); }
  loadSync();
}

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
  <div id="vaultbar">${vaultStrip()}</div>
  <div id="syncbar" aria-live="polite"></div>
  <div class="segbar" id="psegs" role="toolbar" aria-label="Filter by platform"></div>
  <div class="toolbar acct-tools">
    <div class="searchwrap">${IC.search}<input class="search" id="aq" type="search" aria-label="Search accounts" placeholder="Search name, handle, email, phone, notes, tags…" value="${esc(A.q)}"><kbd>/</kbd></div>
    <div class="seg" id="sseg" role="group" aria-label="Filter by status"></div>
    <select id="asort" aria-label="Sort accounts">${[['platform', 'Platform'], ['display_name', 'Name'], ['handle', 'Handle'], ['created_at', 'Date added'], ['status', 'Status']].map(([v, l]) => `<option value="${v}" ${A.sort === v ? 'selected' : ''}>Sort: ${l}</option>`).join('')}</select>
    <button class="btn ghost" id="aorder" aria-label="Toggle sort order" title="Toggle sort order">${A.order === 'desc' ? '↓' : '↑'}</button>
    <div class="seg" role="group" aria-label="Layout"><button class="${A.view === 'grid' ? 'on' : ''}" data-v="grid" aria-pressed="${A.view === 'grid'}" title="Cards grouped by platform">${IC.grid}<span>Cards</span></button><button class="${A.view === 'list' ? 'on' : ''}" data-v="list" aria-pressed="${A.view === 'list'}" title="Compact table">${IC.list}<span>Table</span></button></div>
  </div>
  <div id="alist" aria-live="polite">${skelRows(6, 'agrid skel-rows')}</div>
  <p class="muted small lock">🔒 Passwords are optional and are only ever stored <b>encrypted</b> (never in plaintext, never in CSV exports, never via MCP). Items and contacts can be linked to any account.</p>`;
  let t; $('#aq').oninput = e => { clearTimeout(t); t = setTimeout(() => { A.q = e.target.value.trim(); loadAccounts(); }, 180); };
  $('#asort').onchange = e => { A.sort = e.target.value; A.order = e.target.value === 'created_at' ? 'desc' : 'asc'; $('#aorder').textContent = A.order === 'desc' ? '↓' : '↑'; loadAccounts(); };
  $('#aorder').onclick = e => { A.order = A.order === 'desc' ? 'asc' : 'desc'; e.currentTarget.textContent = A.order === 'desc' ? '↓' : '↑'; loadAccounts(); };
  document.querySelectorAll('[data-v]').forEach(b => b.onclick = () => { A.view = localStorage.acctView = b.dataset.v; document.querySelectorAll('[data-v]').forEach(x => { x.classList.toggle('on', x === b); x.setAttribute('aria-pressed', x === b); }); renderAccounts(); });
  $('#addAcct').onclick = () => openAddAccount(A.platform);
  $('#pasteAcct').onclick = () => openAddAccount(A.platform, 'paste');
  $('#psegs').onclick = e => { const b = e.target.closest('[data-p]'); if (b) { A.platform = b.dataset.p; renderAccounts(); } };
  $('#sseg').onclick = e => { const b = e.target.closest('[data-s]'); if (b) { A.status = b.dataset.s; renderAccounts(); } };
  $('#alist').onclick = onAcctListClick;
  $('#alist').onchange = onBackendChange;
  $('#syncbar').onclick = onSyncBarClick; $('#syncbar').onchange = onSyncBarChange;
  clearInterval(SYNC_T); SYNC_T = setInterval(() => { if (document.visibilityState === 'visible') loadSync(); }, 20000);
  loadSync();
  await loadAccounts();
}
async function loadAccounts() {
  const r = await api('/api/accounts?' + new URLSearchParams({q: A.q, sort: A.sort, order: A.order, limit: 5000}));
  A.all = r.accounts; if ($('#vaultbar')) $('#vaultbar').innerHTML = vaultStrip(); renderAccounts();
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
    el.innerHTML = emptyState({art: 'accounts', id: 'emptyAccounts', title: 'Bring all your accounts together',
      text: 'Add the X, Instagram, TikTok, 小红书, WhatsApp and other accounts you use — then link saved items and contacts to them.',
      actions: [{label: 'Add your first account', act: 'addAcct', icon: IC.plus}, {label: 'Paste a list of handles', act: 'pasteAcct'}],
      extra: `<div class="empty-marks">${['instagram', 'x', 'tiktok', 'xiaohongshu', 'whatsapp', 'wechat', 'facebook', 'douyin', 'icloud'].map(p => pmark(p, 'sm')).join('')}</div>`});
    return;
  }
  if (!list.length) {
    el.innerHTML = emptyState({art: A.q ? 'search' : 'filter', id: 'emptyAcctMatch', title: A.q ? `No accounts match “${A.q.length > 30 ? A.q.slice(0, 30) + '…' : A.q}”` : 'No accounts match these filters',
      text: 'Try a different search or clear the filters to see every account.',
      actions: [{label: 'Clear filters', act: 'clearAcct'}, ...(A.platform ? [{label: `Add ${pname(A.platform)} account`, act: 'addAcct', data: A.platform, icon: IC.plus}] : [])]});
    return;
  }
  if (A.view === 'list') { el.innerHTML = acctTable(list); return; }
  const groups = PLATFORMS.map(p => [p.id, list.filter(a => a.platform === p.id)]).filter(([, l]) => l.length);
  list.filter(a => !PLATFORMS.find(p => p.id === a.platform)).forEach(a => { let g = groups.find(x => x[0] === a.platform); if (!g) groups.push(g = [a.platform, []]); g[1].push(a); });
  el.innerHTML = groups.map(([p, l]) => `<section class="agroup" aria-label="${esc(pname(p))} accounts">
    <header class="agroup-h">${pmark(p, 'sm')}<h3>${esc(pname(p))}</h3><span class="count">${l.length}</span><span class="grow"></span>${backendPick(p)}<button class="btn ghost small" data-act="addp" data-p="${p}" aria-label="Add ${esc(pname(p))} account">${IC.plus} Add</button></header>
    <div class="agrid">${l.map(acctCard).join('')}</div></section>`).join('');
}
EMPTY_ACTS.addAcct = p => openAddAccount(p || '');
EMPTY_ACTS.pasteAcct = () => openAddAccount('', 'paste');
EMPTY_ACTS.clearAcct = () => clearAcctFilters();
function clearAcctFilters() { A.q = ''; A.platform = ''; A.status = ''; const q = $('#aq'); if (q) q.value = ''; loadAccounts(); }
function acctCard(a) {
  if (A.editing === a.id) return acctQuickEdit(a);
  const h = showHandle(a.platform, a.handle);
  return `<article class="acard" data-id="${a.id}" tabindex="-1">
    <div class="acard-top">${avatar(a)}<div class="who"><div class="nm" title="${esc(acctLabel(a))}">${esc(a.display_name || h || 'Untitled')}${a.is_sample ? ' <span class="sample">SAMPLE</span>' : ''}</div>
      <div class="hd">${h ? (safeUrl(a.profile_url) ? `<a href="${esc(safeUrl(a.profile_url))}" target="_blank" rel="noopener noreferrer">${esc(h)} ${IC.link}</a>` : esc(h)) : '<span class="muted">no handle</span>'}</div></div>${statusPill(a.status)}</div>
    ${a.linked_email || a.linked_phone ? `<div class="acard-meta">${a.linked_email ? `<span>${IC.mail}${esc(a.linked_email)}</span>` : ''}${a.linked_phone ? `<span>${IC.phone}${esc(a.linked_phone)}</span>` : ''}</div>` : ''}
    ${(a.tags || []).length ? `<div class="tags">${a.tags.map(t => tagChip(t, {el: 'button', attrs: `data-act="tag" data-t="${esc(t)}" aria-label="Search accounts tagged ${esc(t)}"`})).join('')}</div>` : ''}
    ${pwRow(a)}
    ${syncRow(syncFor(a))}
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
    <td><div class="tacct">${avatar(a)}<div><b>${esc(a.display_name || showHandle(a.platform, a.handle) || 'Untitled')}</b>${a.is_sample ? ' <span class="sample">SAMPLE</span>' : ''}<div class="muted small">${safeUrl(a.profile_url) ? `<a href="${esc(safeUrl(a.profile_url))}" target="_blank" rel="noopener noreferrer">${esc(showHandle(a.platform, a.handle) || a.profile_url)}</a>` : esc(showHandle(a.platform, a.handle))}</div>${pwRow(a)}${syncRow(syncFor(a))}</div></div></td>
    <td class="small">${esc(a.linked_email || '')}${a.linked_email && a.linked_phone ? '<br>' : ''}${esc(a.linked_phone || '')}</td>
    <td><div class="tags">${(a.tags || []).map(t => tagChip(t, {el: 'button', attrs: `data-act="tag" data-t="${esc(t)}"`})).join('')}</div></td><td>${statusPill(a.status)}</td>
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
  else if (act === 'reveal') revealPw(b, id);
  else if (act === 'copypw') copyPw(id);
  else if (act === 'syncnow') syncNowFor(b, b.dataset.p, b.dataset.a);
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
  const linked = a.item_count + a.contact_count + (a.has_password ? 1 : 0);
  const parts = [`This removes the ${esc(pname(a.platform))} account record.`];
  if (a.item_count + a.contact_count) parts.push(`Its ${a.item_count} item(s) and ${a.contact_count} contact(s) are kept but will be unlinked.`);
  if (a.has_password) parts.push('<b>Its saved password is permanently deleted.</b>');
  parts.push(`Nothing on ${esc(pname(a.platform))} itself is changed.`);
  const ok = await confirmDialog({title: `Delete ${esc(acctLabel(a))}?`, body: parts.join(' ')});
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
      ${vaultField(e)}
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
  if ($('#pwEye')) $('#pwEye').onclick = () => { const i = $('#f_password'), show = i.type === 'password'; i.type = show ? 'text' : 'password'; const b = $('#pwEye'); b.setAttribute('aria-pressed', show); b.setAttribute('aria-label', show ? 'Hide password' : 'Show password'); b.innerHTML = show ? IC.eyeoff : IC.eye; i.focus(); };
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
      let pwMsg = '';
      try { const s = await saveSecretFor(AM.edit.id); pwMsg = s ? ` · password ${s}` : ''; } catch (err) { toast('Account saved, but the password was NOT stored: ' + err.message, {kind: 'err'}); closeModal(); await refreshMeta(); if ($('#alist')) loadAccounts(); return; }
      closeModal(); toast(`Saved ${b.display_name || showHandle(b.platform, b.handle)}${pwMsg}`, {kind: 'ok'});
    } else {
      const r = await jpost('/api/accounts', b);
      if (!r.created) { $('#e_handle').textContent = 'This account already exists'; btn.disabled = false; return; }
      let pwMsg = '', pwFailed = false;
      try { const s = await saveSecretFor(r.id); pwMsg = s ? ' · password encrypted' : ''; } catch (err) { pwFailed = true; toast('Account added, but the password was NOT stored: ' + err.message, {kind: 'err'}); }
      AM.added.push({id: r.id, platform: b.platform, label: b.display_name || showHandle(b.platform, b.handle)});
      AM.lastStatus = b.status; AM.lastTags = b.tags; AM.lastVia = b.connected_via === 'manual' ? '' : b.connected_via;
      if (!pwFailed) toast(`Added ${showHandle(b.platform, b.handle) || b.display_name} to ${pname(b.platform)}${pwMsg}`, {kind: 'ok'});
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
        <span class="hint" id="bhelp">Formats: <code>@handle</code> · <code>handle, Display name</code> · <code>ig @handle</code> (prefix: x, threads, ig, fb, messenger, wa, wechat/微信, tiktok, douyin/抖音, xhs/小红书, icloud) · profile links. <kbd>Ctrl</kbd>+<kbd>Enter</kbd> to add.</span></label>
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

// ---------------- Library ----------------
const L = {q: '', platforms: new Set(), tags: new Set(), account: '', type: '', sort: 'saved_at', order: 'desc',
  view: ['grid', 'list'].includes(localStorage.libView) ? localStorage.libView : 'grid', list: [], total: 0, seq: 0, tags_all: []};
const TYPES_UI = [['', 'All'], ['video', 'Videos'], ['photo', 'Photos'], ['note', 'Notes'], ['post', 'Posts'], ['file', 'Files']];
const libFiltered = () => !!(L.platforms.size || L.tags.size || L.account || L.type);
async function viewLibrary(params) {
  if (params.get('q') !== null) L.q = params.get('q');
  if (params.get('tag')) { L.tags = new Set(params.get('tag').toLowerCase().split(',').filter(Boolean)); }
  $('#view').innerHTML = `
  <div class="filterbar" id="filterbar">
    <div class="fb-row">
      <div class="searchwrap grow">${IC.search}<input class="search" id="q" type="search" aria-label="Search your library" placeholder="Search everything… (English / 中文, e.g. coffee, 咖啡, python)" value="${esc(L.q)}"><kbd>/</kbd></div>
      <select id="acct" aria-label="Filter by account">${accountOptions(L.account)}</select>
      <select id="sort" aria-label="Sort items">${[['saved_at', 'Date saved'], ['relevance', 'Relevance'], ['platform', 'Platform'], ['author', 'Author'], ['title', 'Title']].map(([v, l]) => `<option value="${v}" ${L.sort === v ? 'selected' : ''}>Sort: ${l}</option>`).join('')}</select>
      <button class="btn ghost" id="order" aria-label="Toggle sort order" title="Toggle sort order">${L.order === 'desc' ? '↓ Desc' : '↑ Asc'}</button>
      <div class="seg" role="group" aria-label="Layout"><button class="${L.view === 'grid' ? 'on' : ''}" data-lv="grid" aria-pressed="${L.view === 'grid'}" title="Grid">${IC.grid}<span>Grid</span></button><button class="${L.view === 'list' ? 'on' : ''}" data-lv="list" aria-pressed="${L.view === 'list'}" title="List">${IC.list}<span>List</span></button></div>
      <button class="btn" id="addLink">${IC.plus}<span>Add link</span></button>
    </div>
    <div class="fb-row fb-chips">
      <div class="chips scrollx" id="pchips" role="toolbar" aria-label="Filter by app">${PLATFORMS.filter(p => p.enabled).map(p => `<button type="button" class="chip pchip ${L.platforms.has(p.id) ? 'on' : ''}" data-p="${p.id}" aria-pressed="${L.platforms.has(p.id)}">${pmark(p.id, 'xs')}${esc(p.name)}<span class="n">${p.count}</span></button>`).join('')}</div>
    </div>
    <div class="fb-row fb-sub">
      <div class="seg tseg" id="tseg" role="group" aria-label="Filter by type">${TYPES_UI.map(([v, l]) => `<button type="button" class="${L.type === v ? 'on' : ''}" data-ty="${v}" aria-pressed="${L.type === v}">${v ? TYPE_IC(v) : ''}<span>${l}</span></button>`).join('')}</div>
      <span class="grow"></span><span class="muted small" id="count" aria-live="polite"></span>
      <button class="btn ghost small hidden" id="clearf">Clear filters</button>
    </div>
  </div>
  <div class="chips tagrow" id="tchips" aria-label="Filter by tag"></div>
  <div id="results" aria-live="polite" aria-busy="true">${skelCards()}</div>
  <div class="row muted small libfoot"><span class="grow"></span><button class="btn ghost small" onclick="retagAll()">${IC.refresh} Re-run auto-tagging</button></div>`;
  let t; $('#q').oninput = e => { clearTimeout(t); t = setTimeout(() => { L.q = e.target.value; loadItems(); }, 250); };
  $('#acct').onchange = e => { L.account = e.target.value; loadItems(); };
  $('#sort').onchange = e => { L.sort = e.target.value; loadItems(); };
  $('#order').onclick = e => { L.order = L.order === 'desc' ? 'asc' : 'desc'; e.currentTarget.textContent = L.order === 'desc' ? '↓ Desc' : '↑ Asc'; loadItems(); };
  document.querySelectorAll('[data-lv]').forEach(b => b.onclick = () => { L.view = localStorage.libView = b.dataset.lv; document.querySelectorAll('[data-lv]').forEach(x => { x.classList.toggle('on', x === b); x.setAttribute('aria-pressed', x === b); }); renderItems(); });
  $('#addLink').onclick = addLinkModal;
  $('#pchips').onclick = e => { const c = e.target.closest('[data-p]'); if (!c) return; const p = c.dataset.p; L.platforms.has(p) ? L.platforms.delete(p) : L.platforms.add(p); c.classList.toggle('on'); c.setAttribute('aria-pressed', L.platforms.has(p)); loadItems(); };
  $('#tseg').onclick = e => { const b = e.target.closest('[data-ty]'); if (!b) return; L.type = b.dataset.ty; $('#tseg').querySelectorAll('button').forEach(x => { x.classList.toggle('on', x === b); x.setAttribute('aria-pressed', x === b); }); loadItems(); };
  $('#tchips').onclick = e => { const c = e.target.closest('[data-tag]'); if (c) toggleTag(c.dataset.tag); };
  $('#clearf').onclick = clearLibFilters;
  const res = $('#results');
  res.onclick = e => {
    const tg = e.target.closest('[data-tag]'); if (tg && res.contains(tg) && tg.closest('.item')) { e.stopPropagation(); toggleTag(tg.dataset.tag); return; }
    if (e.target.closest('a,button,select,input')) return;
    const c = e.target.closest('.item[data-id]'); if (c) openItem(+c.dataset.id);
  };
  res.onkeydown = e => { const c = e.target.closest('.item[data-id]'); if (c && e.target === c && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); openItem(+c.dataset.id); } };
  api('/api/tags').then(tags => { L.tags_all = tags; renderTagRow(); }).catch(() => {});
  await loadItems();
}
function renderTagRow() {
  const el = $('#tchips'); if (!el) return;
  const top = L.tags_all.slice(0, 24), extra = [...L.tags].filter(x => !top.some(t => t.tag === x)).map(x => ({tag: x, n: (L.tags_all.find(t => t.tag === x) || {}).n || 0}));
  el.innerHTML = `<span class="muted small lbl">Tags</span>` + [...top, ...extra].map(t => tagChip(t.tag, {el: 'button', n: t.n, cls: 'tchip' + (L.tags.has(t.tag.toLowerCase()) ? ' on' : ''), attrs: `aria-pressed="${L.tags.has(t.tag.toLowerCase())}"`})).join('');
}
function toggleTag(t) { t = String(t).toLowerCase(); L.tags.has(t) ? L.tags.delete(t) : L.tags.add(t); renderTagRow(); loadItems(); }
function clearLibFilters() {
  L.platforms.clear(); L.tags.clear(); L.account = ''; L.type = '';
  if (!$('#results')) { location.hash = '#/library'; return; }
  $('#acct').value = ''; document.querySelectorAll('#pchips .chip').forEach(c => { c.classList.remove('on'); c.setAttribute('aria-pressed', 'false'); });
  $('#tseg').querySelectorAll('button').forEach(x => { const on = !x.dataset.ty; x.classList.toggle('on', on); x.setAttribute('aria-pressed', on); });
  renderTagRow(); loadItems();
}
function clearLibSearch() { L.q = ''; if ($('#q')) { $('#q').value = ''; $('#q').focus(); } loadItems(); }
async function loadItems() {
  const seq = ++L.seq, res = $('#results'); if (!res) return;
  res.setAttribute('aria-busy', 'true');
  if (!res.querySelector('.item')) res.innerHTML = skelCards();
  const p = new URLSearchParams({q: L.q, platform: [...L.platforms].join(','), tag: [...L.tags].join(','), account_id: L.account, type: L.type,
    sort: L.sort === 'relevance' && !L.q ? 'saved_at' : L.sort, order: L.order, limit: 200});
  let r;
  try { r = await api('/api/items?' + p); }
  catch (e) {
    if (seq !== L.seq) return;
    res.innerHTML = emptyState({art: 'error', title: 'We couldn’t load your library', text: `${esc(e.message || 'The server didn’t answer.')} Check your connection, then try again.`, actions: [{label: 'Try again', act: 'retryItems', icon: IC.refresh}]});
    res.setAttribute('aria-busy', 'false'); return;
  }
  if (seq !== L.seq) return;
  const first = !L.list.length; L.list = r.items.map(i => i.id); L.total = r.total; r.items.forEach(i => ITEMS.set(i.id, i));
  $('#count').textContent = `${r.total} item${r.total === 1 ? '' : 's'}${L.q ? ` matching “${L.q}”` : ''}`;
  $('#clearf').classList.toggle('hidden', !libFiltered());
  renderItems(r.items);
  res.setAttribute('aria-busy', 'false');
  if (D.open && D.id != null && !D.edit && first && ITEMS.get(D.id) && ITEMS.get(D.id).content !== undefined) renderDrawer(ITEMS.get(D.id));
  if (D.open && D.id != null) { const c = document.querySelector(`.item[data-id="${D.id}"]`); if (c) c.classList.add('current'); }
}
EMPTY_ACTS.retryItems = () => loadItems();
EMPTY_ACTS.clearFilters = () => clearLibFilters();
EMPTY_ACTS.clearSearch = () => clearLibSearch();
EMPTY_ACTS.addLink = () => addLinkModal();
EMPTY_ACTS.search = q => { L.q = q; if ($('#q')) $('#q').value = q; loadItems(); };
EMPTY_ACTS.tag = t => { L.q = ''; if ($('#q')) $('#q').value = ''; L.tags = new Set([t]); renderTagRow(); loadItems(); };
function libEmpty() {
  const one = L.platforms.size === 1 && !L.tags.size && !L.account && !L.type ? [...L.platforms][0] : '';
  if (L.q) {
    const pop = (L.tags_all || []).slice(0, 8);
    const filt = libFiltered();
    return emptyState({art: 'search', id: 'emptySearch', title: `No results for “${L.q.length > 40 ? L.q.slice(0, 40) + '…' : L.q}”`,
      text: filt ? 'Nothing matches that search with the current filters. Try clearing them or using fewer words.' : 'Nothing in your library matches yet. Try fewer or different words — search covers titles, notes, authors and tags.',
      actions: [{label: 'Clear search', act: 'clearSearch'}, ...(filt ? [{label: 'Clear filters', act: 'clearFilters'}] : [])],
      extra: `<div class="suggest"><div class="sg-col"><h4>Search tips</h4><ul class="tips"><li>Use one or two keywords, like <button type="button" class="linkish" data-empty-act="search" data-arg="coffee">coffee</button> or <button type="button" class="linkish" data-empty-act="search" data-arg="咖啡">咖啡</button></li><li>Chinese and English both work — part of a word is enough</li><li>Search an author or handle, e.g. <button type="button" class="linkish" data-empty-act="search" data-arg="python">python</button></li></ul></div>
        ${pop.length ? `<div class="sg-col"><h4>Popular tags</h4><div class="chips">${pop.map(t => tagChip(t.tag, {el: 'button', n: t.n, attrs: `data-empty-act="tag" data-arg="${esc(t.tag)}"`})).join('')}</div></div>` : ''}</div>`});
  }
  if (one) {
    const p = PLATFORMS.find(x => x.id === one) || {id: one, method: 'import'};
    return emptyState({art: 'connect', id: 'emptyPlatform', title: `Nothing from ${pname(one)} yet`,
      text: p.method === 'collector' ? `Sign in to ${esc(pname(one))} once on the box and auto-sync will pull in your saves.` : `Import your ${esc(pname(one))} export or paste a few links to get started.`,
      actions: [{label: `Connect ${pname(one)}`, href: '#/connect'}, {label: 'Clear filters', act: 'clearFilters'}]});
  }
  if (libFiltered()) {
    const bits = [...[...L.platforms].map(pname), L.type ? (TYPES_UI.find(t => t[0] === L.type) || [, L.type])[1].toLowerCase() : '', ...[...L.tags].map(t => '#' + t), L.account ? 'this account' : ''].filter(Boolean);
    return emptyState({art: 'filter', id: 'emptyFilter', title: 'No matches for these filters', text: `Nothing fits ${esc(bits.join(' + '))}. Loosen a filter or start fresh.`,
      actions: [{label: 'Clear filters', act: 'clearFilters'}]});
  }
  return emptyState({art: 'library', id: 'emptyLibrary', title: 'Your library is ready for its first save',
    text: 'Connect an app to bring in bookmarks and saved posts automatically, or add a link by hand.',
    actions: [{label: 'Connect an app', href: '#/connect'}, {label: 'Add a link', act: 'addLink', icon: IC.plus}]});
}
const itemDate = i => fdate(i.saved_at);
function itemCard(i) {
  const acc = acctOf(i), au = i.author || i.author_handle || '';
  return `<article class="card item" data-id="${i.id}" tabindex="0" role="button" aria-haspopup="dialog" aria-label="${esc(`${TYPE_LABEL[i.type] || 'Post'} from ${pname(i.platform)}: ${i.title || i.url}`)}">
    ${cardCover(i)}
    <div class="ibody">
      ${au ? `<div class="author">${personAv(au, i.author_avatar_url, 'sm')}<span class="an">${esc(i.author || '')}</span>${i.author_handle ? `<span class="ah">${esc(i.author_handle.startsWith('@') ? i.author_handle : '@' + i.author_handle)}</span>` : ''}</div>` : ''}
      <h3 class="ititle">${esc(i.title || i.url)}</h3>
      ${(i.tags || []).length ? `<div class="tags">${tagList(i.tags, 3)}</div>` : ''}
      <div class="ifoot"><time datetime="${esc(i.saved_at || '')}">${itemDate(i)}</time>${i.is_sample ? '<span class="sample">SAMPLE</span>' : ''}<span class="grow"></span>${acctBadge(acc)}</div>
    </div></article>`;
}
function itemRow(i) {
  const acc = acctOf(i), src = mediaSrc(firstImg(i));
  return `<article class="lrow item" data-id="${i.id}" tabindex="0" role="button" aria-haspopup="dialog" aria-label="${esc(`${TYPE_LABEL[i.type] || 'Post'} from ${pname(i.platform)}: ${i.title || i.url}`)}">
    <div class="lthumb">${src ? `<img class="cover-img" src="${esc(src)}" alt="" loading="lazy" decoding="async" data-id="${i.id}">` : genInner(i)}${i.type === 'video' ? `<span class="cv-play sm" aria-hidden="true">${IC.play}</span>` : ''}${(i.images || []).length > 1 ? `<span class="cv-badge cnt">${IC.images}${i.images.length}</span>` : ''}</div>
    <div class="lmain"><div class="lmeta">${pbadge(i.platform)}<span class="ltype">${TYPE_IC(i.type)}${esc(TYPE_LABEL[i.type] || 'Post')}${i.duration ? ' · ' + fdur(i.duration) : ''}</span>${i.is_sample ? '<span class="sample">SAMPLE</span>' : ''}</div>
      <h3 class="ititle">${esc(i.title || i.url)}</h3>
      <div class="lsub muted small">${esc(i.author || '')}${i.summary ? (i.author ? ' · ' : '') + esc(i.summary) : ''}</div>
      ${(i.tags || []).length ? `<div class="tags">${tagList(i.tags, 4)}</div>` : ''}</div>
    <div class="lside"><time class="small muted" datetime="${esc(i.saved_at || '')}">${itemDate(i)}</time>${acctBadge(acc)}</div></article>`;
}
function renderItems(items) {
  const el = $('#results'); if (!el) return;
  items = items || L.list.map(id => ITEMS.get(id)).filter(Boolean);
  if (!items.length) { el.innerHTML = libEmpty(); return; }
  el.innerHTML = L.view === 'list' ? `<div class="ilist">${items.map(itemRow).join('')}</div>` : `<div class="igrid masonry">${items.map(itemCard).join('')}</div>`;
  masonry(el.querySelector('.igrid'));
}
// masonry: cards keep their natural (aspect-aware) height; each spans as many 4px rows as it needs, DOM/tab order stays row-wise
let MASONRY_RO = null;
function masonry(g) {
  if (MASONRY_RO) MASONRY_RO.disconnect(); if (!g || !window.ResizeObserver) return;
  const fit = c => { const gap = parseFloat(getComputedStyle(g).columnGap) || 16; c.style.gridRowEnd = 'span ' + Math.ceil((c.getBoundingClientRect().height + gap) / 4); };
  MASONRY_RO = new ResizeObserver(es => es.forEach(e => fit(e.target)));
  [...g.children].forEach(c => { fit(c); MASONRY_RO.observe(c); });
}
async function retagAll() { toast('Tagging…'); const r = await jpost('/api/tag', {force: true}); toast(`Re-tagged ${r.retagged} items with ${r.provider.active}`, {kind: 'ok'}); L.tags_all = await api('/api/tags'); renderTagRow(); loadItems(); }

// ---- detail drawer: gallery / video, full text, AI summary, editable tags, account, collections, next/prev; deep link #/library/item/<id> ----
const D = {open: false, skipRoute: false, id: null, pushed: false, ret: null, edit: false, tags: [], g: 0};
const itemHash = id => '#/library/item/' + id;
function openItem(id) {
  D.ret = document.activeElement && document.activeElement.closest ? document.activeElement.closest('.item') || document.activeElement : null;
  if (location.hash !== itemHash(id)) { D.pushed = true; history.pushState(null, '', itemHash(id)); }
  showItem(id);
}
async function showItem(id) {
  const dr = $('#drawer'), wasOpen = D.open; D.open = true;
  D.id = id; D.edit = false; D.g = 0;
  let i = ITEMS.get(id);
  if (!wasOpen) { dr.classList.remove('hidden'); document.body.classList.add('noscroll'); requestAnimationFrame(() => dr.classList.add('show')); }
  if (!i) $('#drawerBody').innerHTML = `<div class="dskel">${'<div class="sk sk-block"></div><div class="sk sk-line w60"></div><div class="sk sk-line"></div><div class="sk sk-line w80"></div>'}</div>`;
  try { i = await api('/api/items/' + id); ITEMS.set(id, Object.assign(ITEMS.get(id) || {}, i)); i = ITEMS.get(id); }
  catch (e) { if (!ITEMS.get(id)) { $('#drawerBody').innerHTML = emptyState({art: 'error', title: 'This item isn’t here any more', text: 'It may have been deleted or the link is out of date.', actions: [{label: 'Back to library', act: 'closeDrawer'}]}); return; } }
  if (D.id !== id) return;
  D.tags = [...(i.tags || [])];
  renderDrawer(i);
  if (!wasOpen) setTimeout(() => { const f = $('#dClose'); if (f && D.open && D.id === id && !$('#drawerBody').contains(document.activeElement)) f.focus(); }, 40);
  document.querySelectorAll('.item.current').forEach(x => x.classList.remove('current'));
  const card = document.querySelector(`.item[data-id="${id}"]`); if (card) card.classList.add('current');
}
EMPTY_ACTS.closeDrawer = () => closeItem();
$('#drawerScrim').onclick = () => closeItem();
function closeItem() {
  const dr = $('#drawer'); if (!D.open) return; D.open = false;
  dr.classList.remove('show'); document.body.classList.remove('noscroll');
  const v = dr.querySelector('video'); if (v) v.pause();
  setTimeout(() => { if (!dr.classList.contains('show')) { dr.classList.add('hidden'); $('#drawerBody').innerHTML = ''; } }, 220);
  const id = D.id; D.id = null;
  if (location.hash.startsWith('#/library/item/')) { if (D.pushed) { D.pushed = false; D.skipRoute = true; history.back(); } else history.replaceState(null, '', '#/library'); }
  const back = (D.ret && document.contains(D.ret) && D.ret) || document.querySelector(`.item[data-id="${id}"]`);
  if (back) back.focus({preventScroll: false}); D.ret = null;
}
const neighbours = id => { const k = L.list.indexOf(id); return k < 0 ? [null, null] : [L.list[k - 1] ?? null, L.list[k + 1] ?? null]; };
function stepItem(d) {
  const [p, n] = neighbours(D.id), id = d < 0 ? p : n; if (id == null) return;
  history.replaceState(null, '', itemHash(id)); D.ret = document.querySelector(`.item[data-id="${id}"]`) || D.ret; showItem(id);
  const c = document.querySelector(`.item[data-id="${id}"]`); if (c) c.scrollIntoView({block: 'nearest'});
}
function galleryHtml(i) {
  const imgs = [...new Set([...(i.images || []), ...((i.images || []).length ? [] : [i.thumbnail_url])].filter(Boolean))];
  const vs = videoSrc(i.video_url), poster = mediaSrc(i.thumbnail_url || imgs[0] || '');
  const ar = Math.min(1.9, Math.max(0.56, +i.aspect || (imgs.length ? 1.33 : 1.6)));
  if (i.type === 'video' && vs) return `<div class="dmedia video" style="--ar:${ar}"><video id="dVideo" controls playsinline preload="metadata" ${poster ? `poster="${esc(poster)}"` : ''} aria-label="${esc('Video: ' + (i.title || ''))}"><source src="${esc(vs)}" type="video/mp4"></video></div>`;
  if (!imgs.length) return `<div class="dmedia gen-wrap" style="--ar:1.6">${genInner(i)}${i.type === 'video' ? `<a class="cv-play big" href="${esc(safeUrl(i.url) || '#')}" target="_blank" rel="noopener noreferrer" aria-label="Play on ${esc(pname(i.platform))}">${IC.play}</a>` : ''}</div>`;
  const many = imgs.length > 1;
  return `<div class="dmedia gallery ${many ? 'many' : ''}" id="gallery" style="--ar:${ar}" ${many ? `tabindex="0" role="region" aria-roledescription="carousel" aria-label="${imgs.length} images — use the arrow keys"` : ''}>
    <div class="gtrack" id="gtrack">${imgs.map((u, k) => `<div class="gslide" ${many ? `role="group" aria-roledescription="slide" aria-label="${k + 1} of ${imgs.length}"` : ''}><img class="g-img" src="${esc(mediaSrc(u))}" alt="${esc(`${i.title || 'Image'}${many ? ` (${k + 1} of ${imgs.length})` : ''}`)}" draggable="false" data-id="${i.id}"></div>`).join('')}</div>
    ${i.type === 'video' ? `<a class="cv-play big" href="${esc(safeUrl(i.url) || '#')}" target="_blank" rel="noopener noreferrer" aria-label="Play on ${esc(pname(i.platform))}">${IC.play}</a>${i.duration ? `<span class="cv-badge dur">${fdur(i.duration)}</span>` : ''}` : ''}
    ${many ? `<button type="button" class="gnav prev" id="gPrev" aria-label="Previous image">${IC.chevL}</button><button type="button" class="gnav next" id="gNext" aria-label="Next image">${IC.chevR}</button>
      <div class="gdots" role="tablist" aria-label="Choose image">${imgs.map((_, k) => `<button type="button" role="tab" class="gdot ${k ? '' : 'on'}" data-g="${k}" aria-label="Image ${k + 1}" aria-selected="${!k}"></button>`).join('')}</div><span class="gcount" id="gCount" aria-live="polite">1 / ${imgs.length}</span>` : ''}</div>`;
}
function gGo(k) {
  const tr = $('#gtrack'); if (!tr) return; const n = tr.children.length; D.g = Math.max(0, Math.min(n - 1, k));
  tr.style.transform = `translateX(${-100 * D.g}%)`;
  document.querySelectorAll('.gdot').forEach((d, j) => { d.classList.toggle('on', j === D.g); d.setAttribute('aria-selected', j === D.g); });
  const c = $('#gCount'); if (c) c.textContent = `${D.g + 1} / ${n}`;
  const p = $('#gPrev'), x = $('#gNext'); if (p) p.disabled = D.g === 0; if (x) x.disabled = D.g === n - 1;
}
function wireGallery() {
  const g = $('#gallery'); if (!g || !g.classList.contains('many')) return;
  $('#gPrev').onclick = () => gGo(D.g - 1); $('#gNext').onclick = () => gGo(D.g + 1);
  g.querySelector('.gdots').onclick = e => { const d = e.target.closest('[data-g]'); if (d) gGo(+d.dataset.g); };
  g.onkeydown = e => { if (e.key === 'ArrowLeft') { e.preventDefault(); e.stopPropagation(); gGo(D.g - 1); } else if (e.key === 'ArrowRight') { e.preventDefault(); e.stopPropagation(); gGo(D.g + 1); } };
  let x0 = null, t0 = 0; const tr = $('#gtrack');
  g.addEventListener('pointerdown', e => { if (e.target.closest('button,a')) return; x0 = e.clientX; t0 = Date.now(); tr.style.transition = 'none'; g.setPointerCapture && g.setPointerCapture(e.pointerId); });
  g.addEventListener('pointermove', e => { if (x0 == null) return; tr.style.transform = `translateX(calc(${-100 * D.g}% + ${e.clientX - x0}px))`; });
  const end = e => { if (x0 == null) return; const dx = e.clientX - x0; x0 = null; tr.style.transition = ''; const w = g.clientWidth || 1;
    if (Math.abs(dx) > w * 0.18 || (Math.abs(dx) > 30 && Date.now() - t0 < 300)) gGo(D.g + (dx < 0 ? 1 : -1)); else gGo(D.g); };
  g.addEventListener('pointerup', end); g.addEventListener('pointercancel', end);
  gGo(0);
}
const tagEditor = () => `<div class="tagedit" id="tagEdit"><div class="tags">${D.tags.map(t => tagChip(t, {remove: true})).join('')}<input id="tagInput" class="taginput" aria-label="Add a tag" placeholder="${D.tags.length ? 'Add tag…' : 'Type a tag and press Enter'}" autocomplete="off" list="tagSuggest"></div>
  <datalist id="tagSuggest">${(L.tags_all || []).slice(0, 60).map(t => `<option value="${esc(t.tag)}">`).join('')}</datalist>
  <div class="row"><button type="button" class="btn small" id="tagSave">Save tags</button><button type="button" class="btn ghost small" id="tagCancel">Cancel</button><span class="muted small">Enter or comma adds · Backspace removes the last</span></div></div>`;
function renderDrawer(i) {
  const fid = document.activeElement && document.activeElement.id;
  const acc = acctOf(i), [prev, next] = neighbours(i.id), k = L.list.indexOf(i.id), url = safeUrl(i.url), cols = i.collections || [];
  const body = String(i.content || '').trim(), sum = String(i.summary || '').trim(), showSum = sum && sum !== body && sum !== String(i.title || '').trim() && !body.startsWith(sum.replace(/…$/, ''));
  $('#drawerBody').innerHTML = `
  <header class="dhead">
    <div class="dnav"><button type="button" class="icon" id="dPrev" ${prev == null ? 'disabled' : ''} aria-label="Previous item" title="Previous (K)">${IC.chevL}</button><button type="button" class="icon" id="dNext" ${next == null ? 'disabled' : ''} aria-label="Next item" title="Next (J)">${IC.chevR}</button>${k >= 0 ? `<span class="muted small">${k + 1} of ${L.list.length}</span>` : ''}</div>
    <div class="dhead-mid">${pbadge(i.platform)}<span class="ltype">${TYPE_IC(i.type)}${esc(TYPE_LABEL[i.type] || 'Post')}${i.duration ? ' · ' + fdur(i.duration) : ''}</span>${i.is_sample ? '<span class="sample">SAMPLE</span>' : ''}</div>
    <button type="button" class="icon" id="dClose" aria-label="Close details" title="Close (Esc)">${IC.close}</button>
  </header>
  <div class="dgrid">
    <div class="dleft">${galleryHtml(i)}
      ${i.type === 'video' && !videoSrc(i.video_url) && url ? `<a class="btn open-on" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${IC.play}Open on ${esc(pname(i.platform))}</a>` : ''}</div>
    <div class="dright">
      ${i.author || i.author_handle ? `<div class="author big">${personAv(i.author || i.author_handle, i.author_avatar_url, 'md')}<div><div class="an">${esc(i.author || '')}</div>${i.author_handle ? `<div class="ah">${esc(i.author_handle.startsWith('@') ? i.author_handle : '@' + i.author_handle)}</div>` : ''}</div></div>` : ''}
      <h2 class="dtitle" id="dTitle">${esc(i.title || i.url)}</h2>
      ${body ? `<div class="dtext">${esc(body)}</div>` : ''}
      ${showSum ? `<div class="aisum"><div class="aihead">${IC.spark}<b>AI summary</b><span class="muted small">${esc(i.tagger ? 'by ' + i.tagger : '')}</span></div><p>${esc(sum)}</p></div>` : ''}
      <section class="dsec"><div class="dsec-h"><h4>Tags</h4>${D.edit ? '' : `<button type="button" class="btn ghost xs" id="tagEditBtn">${IC.edit}Edit</button>`}</div>
        <div id="tagBox">${D.edit ? tagEditor() : (D.tags.length ? `<div class="tags">${D.tags.map(t => tagChip(t, {el: 'button', attrs: 'data-dtag="1"'})).join('')}</div>` : '<span class="muted small">No tags yet</span>')}</div></section>
      <dl class="dmeta">
        <dt>Account</dt><dd><div class="row">${acc ? acctBadge(acc, {name: true}) : '<span class="muted">Not linked</span>'}<select id="eacct" class="xs" aria-label="Change linked account">${accountOptions(i.account_id, null).replace('All accounts', '— none —')}</select></div></dd>
        ${cols.length ? `<dt>Collections</dt><dd><div class="chips">${cols.map(c => `<span class="coll">${IC.images}${esc(c)}</span>`).join('')}</div></dd>` : ''}
        <dt>Saved</dt><dd>${esc(fdatetime(i.saved_at))}</dd>
        ${i.posted_at ? `<dt>Posted</dt><dd>${esc(fdatetime(i.posted_at))}</dd>` : ''}
        <dt>Source</dt><dd>${esc(i.source || '—')}${i.tagger ? ` · tagged by ${esc(i.tagger)}` : ''}</dd>
        ${url ? `<dt>Original</dt><dd class="durl"><a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url.replace(/^https?:\/\/(www\.)?/, ''))} ${IC.link}</a></dd>` : ''}
      </dl>
      <div class="row dacts">${url ? `<a class="btn" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${IC.link}Open on ${esc(pname(i.platform))}</a><button type="button" class="btn ghost" id="dCopy">${IC.copy}Copy link</button>` : ''}<span class="grow"></span><button type="button" class="btn ghost danger" id="dDel">${IC.trash}Delete</button></div>
    </div></div>`;
  $('#dClose').onclick = closeItem; $('#dPrev').onclick = () => stepItem(-1); $('#dNext').onclick = () => stepItem(1);
  if ($('#dCopy')) $('#dCopy').onclick = () => copyText(url);
  $('#dDel').onclick = () => delItem(i.id);
  $('#eacct').onchange = async e => { try { const r = await jpost('/api/items/' + i.id, {account_id: e.target.value ? +e.target.value : null}, 'PATCH'); mergeItem(r.item || r); toast('Account updated', {kind: 'ok'}); } catch (err) { toast(err.message, {kind: 'err'}); } };
  const eb = $('#tagEditBtn'); if (eb) eb.onclick = () => { D.edit = true; renderDrawer(ITEMS.get(i.id)); const t = $('#tagInput'); t && t.focus(); };
  $('#tagBox').onclick = e => { const d = e.target.closest('[data-dtag]'); if (d) { closeItem(); setTimeout(() => { L.tags = new Set([d.dataset.tag.toLowerCase()]); if ($('#results')) { renderTagRow(); loadItems(); } }, 0); } };
  if (D.edit) wireTagEditor(i);
  wireGallery();
  const fe = fid && document.getElementById(fid); if (fe && $('#drawerBody').contains(fe) && !fe.disabled) fe.focus();
}
function mergeItem(it) { if (!it || !it.id) return; const cur = ITEMS.get(it.id) || {}; Object.assign(cur, it); ITEMS.set(it.id, cur);
  if (D.id === it.id) renderDrawer(cur);
  const c = document.querySelector(`.item[data-id="${it.id}"]`); if (c) c.outerHTML = (L.view === 'list' ? itemRow : itemCard)(cur); const c2 = document.querySelector(`.item[data-id="${it.id}"]`); if (c2 && D.id === it.id) c2.classList.add('current'); }
function wireTagEditor(i) {
  const inp = $('#tagInput'), box = $('#tagEdit');
  const redraw = () => { box.querySelector('.tags').innerHTML = D.tags.map(t => tagChip(t, {remove: true})).join('') + ''; box.querySelector('.tags').appendChild(inp); inp.placeholder = D.tags.length ? 'Add tag…' : 'Type a tag and press Enter'; inp.focus(); };
  const add = v => { v.split(',').map(s => s.trim().replace(/^#/, '').toLowerCase()).filter(Boolean).forEach(t => { if (!D.tags.includes(t) && t.length <= 40) D.tags.push(t); }); inp.value = ''; redraw(); };
  inp.onkeydown = e => {
    if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); if (inp.value.trim()) add(inp.value); else if (e.key === 'Enter') save(); }
    else if (e.key === 'Backspace' && !inp.value && D.tags.length) { D.tags.pop(); redraw(); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); cancel(); }
  };
  inp.onblur = () => { if (inp.value.trim()) add(inp.value); };
  box.onclick = e => { const x = e.target.closest('[data-rm]'); if (x) { D.tags = D.tags.filter(t => t !== x.dataset.rm); redraw(); } };
  const cancel = () => { D.edit = false; D.tags = [...(ITEMS.get(i.id).tags || [])]; renderDrawer(ITEMS.get(i.id)); const b = $('#tagEditBtn'); b && b.focus(); };
  const save = async () => { if (inp.value.trim()) add(inp.value);
    try { const r = await jpost('/api/items/' + i.id, {tags: D.tags}, 'PATCH'); D.edit = false; mergeItem(Object.assign({}, r.item || r, {id: i.id, tags: (r.item || r).tags || D.tags})); toast('Tags saved', {kind: 'ok'});
      api('/api/tags').then(t => { L.tags_all = t; renderTagRow(); }).catch(() => {}); const b = $('#tagEditBtn'); b && b.focus(); }
    catch (err) { toast(err.message, {kind: 'err'}); } };
  $('#tagSave').onclick = save; $('#tagCancel').onclick = cancel;
}
document.addEventListener('keydown', e => {
  const dr = $('#drawer'); if (!dr || !D.open || !$('#modal').classList.contains('hidden')) return;
  const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName);
  if (e.key === 'Escape') { e.preventDefault(); closeItem(); return; }
  if (!typing && !e.target.closest('#gallery') && (e.key === 'j' || e.key === 'PageDown' || (e.key === 'ArrowRight' && !e.target.closest('.dright')))) { e.preventDefault(); stepItem(1); return; }
  if (!typing && !e.target.closest('#gallery') && (e.key === 'k' || e.key === 'PageUp' || (e.key === 'ArrowLeft' && !e.target.closest('.dright')))) { e.preventDefault(); stepItem(-1); return; }
  if (e.key === 'Tab') {   // keep focus inside the drawer
    const f = [...dr.querySelectorAll('a[href],button:not([disabled]),input,select,textarea,video,[tabindex="0"]')].filter(x => x.offsetParent !== null);
    if (!f.length) return; const k = f.indexOf(document.activeElement);
    if (e.shiftKey && k <= 0) { e.preventDefault(); f[f.length - 1].focus(); } else if (!e.shiftKey && k === f.length - 1) { e.preventDefault(); f[0].focus(); }
  }
});
document.addEventListener('keydown', e => {
  if ((location.hash || '#/library').startsWith('#/library') && e.key === '/' && !D.open && $('#modal').classList.contains('hidden') && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); $('#q') && $('#q').focus(); }
});
async function delItem(id) {
  const i = ITEMS.get(id) || {};
  if (!await confirmDialog({title: 'Delete this item?', body: `“${esc((i.title || i.url || '').slice(0, 120))}” will be removed from SaveHub. The original on ${esc(pname(i.platform))} is not affected.`})) return;
  await api('/api/items/' + id, {method: 'DELETE'});
  const [p, n] = neighbours(id); L.list = L.list.filter(x => x !== id); ITEMS.delete(id);
  toast('Item deleted', {kind: 'ok'}); await refreshMeta();
  const nx = n ?? p;
  if (nx != null && D.open) { history.replaceState(null, '', itemHash(nx)); showItem(nx); } else closeItem();
  if ($('#results')) loadItems();
}
function addLinkModal() {
  modal(`<h3>Add a link</h3><div class="form">
    <label class="full">URL<input id="nurl" placeholder="https://…"></label><label class="full">Title<input id="ntitle"></label>
    <label class="full">Notes / content<textarea id="ncontent"></textarea></label>
    <label>Platform<select id="nplat"><option value="">Auto-detect</option>${PLATFORMS.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select></label>
    <label>Account<select id="nacct">${accountOptions('', null).replace('All accounts','— none —')}</select></label></div>
    <p class="row"><button class="btn" onclick="addLink()">Save & auto-tag</button><button class="btn ghost" onclick="closeModal()">Cancel</button></p>`);
}
async function addLink() {
  const url = $('#nurl').value.trim();
  if (!safeUrl(url)) { toast('Enter a link starting with http:// or https://', {kind: 'err'}); $('#nurl').focus(); return; }
  let r;
  try { r = await jpost('/api/items', {url, title: $('#ntitle').value, content: $('#ncontent').value, platform: $('#nplat').value, account_id: $('#nacct').value || null}); }
  catch (e) { toast(e.message, {kind: 'err'}); return; }
  closeModal(); toast(r.created ? 'Saved & tagged: ' + (r.item.tags || []).join(', ') : 'Already in library'); await refreshMeta(); route();
}

// ---------------- Summary ----------------
const TYPE_COLOR = {post: 'var(--c1)', video: 'var(--c2)', photo: 'var(--c3)', note: 'var(--c4)', file: 'var(--c5)', contact: 'var(--c6)'};
function dayCols(byDay, days) {   // by_day keys are UTC dates (saved_at is stored in UTC)
  const out = [], now = Date.now();
  for (let k = days - 1; k >= 0; k--) { const key = new Date(now - k * 864e5).toISOString().slice(0, 10); out.push({d: new Date(key + 'T12:00:00'), n: byDay[key] || 0}); }
  return out;
}
function spark(vals) {
  const w = 120, h = 32, m = Math.max(1, ...vals), step = vals.length > 1 ? w / (vals.length - 1) : w;
  const pts = vals.map((v, k) => `${(k * step).toFixed(1)},${(h - 3 - (v / m) * (h - 8)).toFixed(1)}`).join(' ');
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true" preserveAspectRatio="none"><polyline points="0,${h} ${pts} ${w},${h}" class="sp-fill"/><polyline points="${pts}" class="sp-line"/></svg>`;
}
function columnChart(cols) {
  const m = Math.max(1, ...cols.map(c => c.n)), n = cols.length, every = n > 16 ? Math.ceil(n / 8) : n > 8 ? 2 : 1;
  const lab = c => c.d.toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
  return `<div class="colchart" role="img" aria-label="Items saved per day: ${esc(cols.map(c => lab(c) + ' ' + c.n).join(', '))}">
    <div class="cc-y" aria-hidden="true"><span>${m}</span><span>${Math.round(m / 2)}</span><span>0</span></div>
    <div class="cc-plot" aria-hidden="true">${cols.map((c, k) => `<div class="cc-col" title="${esc(lab(c))}: ${c.n} saved"><span class="cc-v">${c.n || ''}</span><span class="cc-bar ${c.n ? '' : 'zero'}" style="height:${c.n ? Math.max(3, c.n / m * 100).toFixed(1) : 1.5}%;animation-delay:${(k * 0.02).toFixed(2)}s"></span><span class="cc-x">${k % every === 0 || k === n - 1 ? esc(lab(c)) : ''}</span></div>`).join('')}</div></div>`;
}
function donut(byType) {
  const ent = Object.entries(byType), tot = ent.reduce((a, [, n]) => a + n, 0) || 1, R = 52, C = 2 * Math.PI * R; let off = 0;
  return `<div class="donutwrap"><svg class="donut" viewBox="0 0 140 140" width="140" height="140" role="img" aria-label="Saved items by type">
    <circle cx="70" cy="70" r="${R}" class="dn-bg"/>${ent.map(([t, n]) => { const len = C * n / tot, s = `<circle cx="70" cy="70" r="${R}" stroke="${TYPE_COLOR[t] || 'var(--c1)'}" stroke-dasharray="${len.toFixed(2)} ${(C - len).toFixed(2)}" stroke-dashoffset="${(-off).toFixed(2)}" class="dn-seg"><title>${esc(TYPE_LABEL[t] || t)}: ${n}</title></circle>`; off += len; return s; }).join('')}
    <text x="70" y="68" text-anchor="middle" class="dn-n">${ent.length ? tot : 0}</text><text x="70" y="86" text-anchor="middle" class="dn-l">items</text></svg>
    <ul class="legend">${ent.map(([t, n]) => `<li><i style="background:${TYPE_COLOR[t] || 'var(--c1)'}"></i>${TYPE_IC(t)}<span>${esc(TYPE_LABEL[t] || t)}</span><b>${n}</b><span class="muted">${Math.round(n / tot * 100)}%</span></li>`).join('')}</ul></div>`;
}
EMPTY_ACTS.days = d => { location.hash = '#/summary?days=' + d; };
async function viewSummary(params) {
  const days = +params.get('days') || 7;
  const head = `<div class="pagehead"><div><h2>Digest</h2><p class="muted small">What you saved in the last ${days} days</p></div><div class="seg" role="group" aria-label="Time range">${[7, 14, 30].map(d => `<a class="chip ${d == days ? 'on' : ''}" href="#/summary?days=${d}" ${d == days ? 'aria-current="true"' : ''}>${d} days</a>`).join('')}</div></div>`;
  $('#view').innerHTML = head + skelPanels();
  let s;
  try { s = await api('/api/summary?days=' + days); }
  catch (e) { $('#view').innerHTML = head + emptyState({art: 'error', title: 'The digest didn’t load', text: esc(e.message) + ' — try again in a moment.', actions: [{label: 'Try again', act: 'days', data: String(days)}]}); return; }
  if (!s.total) {
    $('#view').innerHTML = head + emptyState({art: 'summary', id: 'emptySummary', title: `A quiet ${days === 7 ? 'week' : days + ' days'}`,
      text: STATS.items ? `You haven’t saved anything in the last ${days} days. Look further back, or connect another app.` : 'Your digest fills in as you save things. Connect an app to get your first chart.',
      actions: days < 30 && STATS.items ? [{label: 'Show 30 days', act: 'days', data: '30'}, {label: 'Connect an app', href: '#/connect'}] : [{label: 'Connect an app', href: '#/connect'}, {label: 'Open library', href: '#/library'}]});
    return;
  }
  const cols = dayCols(s.by_day || {}, days), vals = cols.map(c => c.n), maxP = Math.max(1, ...Object.values(s.by_platform)), totP = Object.values(s.by_platform).reduce((a, b) => a + b, 0) || 1;
  const best = cols.reduce((a, c) => c.n > a.n ? c : a, {n: 0}), activeDays = cols.filter(c => c.n).length;
  $('#view').innerHTML = head + `<div class="kpis">
    <div class="panel kcard"><div class="muted small">Items saved</div><div class="kpi">${s.total}</div>${spark(vals)}<div class="muted small">since ${fdate(s.since)}</div></div>
    <div class="panel kcard"><div class="muted small">Apps active</div><div class="kpi">${Object.keys(s.by_platform).length}</div><div class="kmarks">${Object.keys(s.by_platform).slice(0, 6).map(p => pmark(p, 'xs')).join('')}</div></div>
    <div class="panel kcard"><div class="muted small">Busiest day</div><div class="kpi">${best.n || 0}</div><div class="muted small">${best.d ? best.d.toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'}) : '—'} · ${activeDays} active day${activeDays === 1 ? '' : 's'}</div></div>
    <div class="panel kcard"><div class="muted small">Top tag</div><div class="kpi ktag">${s.top_tags[0] ? tagChip(s.top_tags[0][0]) : '—'}</div><div class="muted small">${s.top_tags[0] ? `${s.top_tags[0][1]} item${s.top_tags[0][1] === 1 ? '' : 's'}` : ''}</div></div>
  </div>
  <div class="panel aisum big" style="margin-top:16px"><div class="aihead">${IC.spark}<b>Summary</b><span class="muted small">${s.digest_by === 'local' ? 'template — no AI key set' : 'written by ' + esc(s.digest_by)}</span></div><div class="digest">${esc(s.digest)}</div></div>
  <div class="sgrid">
    <div class="panel wide"><h4>Saves per day</h4>${columnChart(cols)}</div>
    <div class="panel"><h4>By app</h4><div class="hbars">${Object.entries(s.by_platform).map(([p, n]) => `<div class="hbar"><span class="hl">${pmark(p, 'xs')}<span>${esc(pname(p))}</span></span><span class="track"><span class="fill" style="width:${(n / maxP * 100).toFixed(1)}%;--bc:${(BRAND[p] || BRAND.web)[['x', 'threads', 'douyin', 'tiktok'].includes(p) ? 1 : 0]}"></span></span><b>${n}</b><span class="muted small pct">${Math.round(n / totP * 100)}%</span></div>`).join('')}</div></div>
    <div class="panel"><h4>By type</h4>${donut(s.by_type || {})}</div>
    <div class="panel"><h4>Top tags</h4><div class="chips">${s.top_tags.map(([t, n]) => tagChip(t, {el: 'a', n, attrs: `href="#/library?tag=${encodeURIComponent(t)}"`})).join('')}</div>
      <h4 style="margin-top:16px">Top authors</h4><ul class="authors">${s.top_authors.map(([a, n]) => `<li>${personAv(a, '', 'sm')}<span class="grow">${esc(a)}</span><b>${n}</b></li>`).join('')}</ul></div>
  </div>
  <div class="panel" style="margin-top:16px"><div class="dsec-h"><h4>Recent saves</h4><a class="small" href="#/library">Open library →</a></div>
    <div class="recent">${s.recent.map(i => { ITEMS.set(i.id, Object.assign(ITEMS.get(i.id) || {}, i)); return `<a class="rcard" href="#/library/item/${i.id}" aria-label="${esc(i.title || i.url)}">${cardCover(i, 'sm')}<span class="rt">${esc(i.title || i.url)}</span><span class="muted small">${fdate(i.saved_at)}</span></a>`; }).join('')}</div></div>`;
}

// ---------------- Contacts ----------------
const C = {q: '', account: ''};
EMPTY_ACTS.importContacts = () => { const f = $('#cfile'); f && f.click(); };
EMPTY_ACTS.clearContacts = () => { C.q = ''; C.account = ''; $('#cq').value = ''; $('#cacct').value = ''; window._loadContacts(); };
const telHref = p => { const d = String(p).replace(/[^\d+]/g, ''); return d.replace(/\D/g, '').length >= 5 ? 'tel:' + d : ''; };
const waHref = p => { const d = String(p).replace(/\D/g, ''); return d.length >= 7 ? 'https://wa.me/' + d : ''; };
const mailHref = m => /^[^\s@<>"']+@[^\s@<>"']+\.[^\s@<>"']+$/.test(String(m).trim()) ? 'mailto:' + String(m).trim() : '';
function contactCard(c) {
  const acc = c.account_id ? (ACCOUNTS.find(a => a.id === c.account_id) || {id: c.account_id, platform: c.platform, display_name: c.account_name}) : null;
  const ph = c.phones || [], em = c.emails || [], tel = ph[0] && telHref(ph[0]), wa = ph[0] && waHref(ph[0]), mail = em[0] && mailHref(em[0]);
  return `<article class="ccard" data-id="${c.id}">
    <div class="ctop">${personAv(c.name, c.avatar_url, 'lg')}${c.platform && LOGOS.has(c.platform) ? `<i class="pdot cdot" data-logo="${esc(c.platform)}" style="background:${pm(c.platform).bg}" title="${esc(pname(c.platform))}">${logoImg(c.platform)}</i>` : ''}
      <div class="who"><div class="nm" title="${esc(c.name)}">${esc(c.name || 'Unnamed')}</div><div class="muted small org">${c.is_sample ? '<span class="sample">SAMPLE</span> ' : ''}${esc(c.org || '')}</div></div>
      <button type="button" class="icon danger cdel" onclick="delContact(${c.id})" aria-label="Delete contact ${esc(c.name)}" title="Delete">${IC.trash}</button></div>
    <div class="cinfo">${ph.map(p => `<div class="cline">${IC.phone}<span>${esc(p)}</span></div>`).join('')}${em.map(m => `<div class="cline">${IC.mail}<span>${esc(m)}</span></div>`).join('')}${!ph.length && !em.length ? '<div class="cline muted small">No phone or email</div>' : ''}</div>
    ${tel || wa || mail ? `<div class="cacts">${tel ? `<a class="qa" href="${esc(tel)}" aria-label="Call ${esc(c.name)}" title="Call ${esc(ph[0])}">${IC.phone}<span>Call</span></a>` : ''}${wa ? `<a class="qa wa" href="${esc(wa)}" target="_blank" rel="noopener noreferrer" aria-label="WhatsApp ${esc(c.name)}" title="WhatsApp ${esc(ph[0])}">${IC.wa}<span>WhatsApp</span></a>` : ''}${mail ? `<a class="qa" href="${esc(mail)}" aria-label="Email ${esc(c.name)}" title="Email ${esc(em[0])}">${IC.mail}<span>Email</span></a>` : ''}</div>` : ''}
    <div class="cfoot">${c.platform ? pbadge(c.platform) : ''}${acc ? acctBadge(acc) : ''}</div></article>`;
}
async function viewContacts() {
  $('#view').innerHTML = `<div class="pagehead"><div><h2>Contacts</h2><p class="muted small" id="csub">Loading…</p></div>
    <label class="btn ghost" tabindex="0" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();this.querySelector('input').click()}">${IC.upload} Import vCard / CSV<input type="file" id="cfile" accept=".vcf,.csv" class="hidden" onchange="importContacts(this)" aria-label="Import contacts file"></label></div>
    <div class="toolbar"><div class="searchwrap grow">${IC.search}<input class="search" id="cq" type="search" aria-label="Search contacts" placeholder="Search contacts by name, phone, email, org…" value="${esc(C.q)}"></div><select id="cacct" aria-label="Filter contacts by account">${accountOptions(C.account)}</select></div><div id="ctable" aria-live="polite">${skelRows(6, 'cgrid skel-rows')}</div>`;
  const load = async () => {
    C.q = $('#cq').value; C.account = $('#cacct').value;
    let r; try { r = await api('/api/contacts?' + new URLSearchParams({q: C.q, account_id: C.account, limit: 500})); }
    catch (e) { $('#ctable').innerHTML = emptyState({art: 'error', title: 'Contacts didn’t load', text: esc(e.message), actions: [{label: 'Try again', act: 'clearContacts'}]}); return; }
    $('#csub').textContent = `${r.total} contact${r.total === 1 ? '' : 's'}${C.q ? ` matching “${C.q}”` : ''}`;
    if (!r.contacts.length) {
      $('#ctable').innerHTML = C.q || C.account
        ? emptyState({art: 'search', id: 'emptyContactsSearch', title: 'No contacts match', text: C.q ? `Nobody matches “${esc(C.q)}”. Try part of a name, a phone number or an email.` : 'No contacts are linked to this account yet.', actions: [{label: 'Clear search', act: 'clearContacts'}]})
        : emptyState({art: 'contacts', id: 'emptyContacts', title: 'No contacts yet', text: 'Import a vCard or CSV from your phone, or sync WhatsApp, Messenger and iCloud to fill this in.',
          actions: [{label: 'Import contacts', act: 'importContacts', icon: IC.upload}, {label: 'Connect an app', href: '#/connect'}]});
      return;
    }
    $('#ctable').innerHTML = `<div class="cgrid">${r.contacts.map(contactCard).join('')}</div>`;
  };
  window._loadContacts = load; let t; $('#cq').oninput = () => { clearTimeout(t); t = setTimeout(load, 200); }; $('#cacct').onchange = load; load();
}
async function delContact(id) {
  if (!await confirmDialog({title: 'Delete this contact?', body: 'It is removed from SaveHub only.'})) return;
  await api('/api/contacts/' + id, {method: 'DELETE'}); toast('Contact deleted', {kind: 'ok'}); window._loadContacts();
}
async function importContacts(inp) {
  const f = inp.files[0]; if (!f) return;
  const fd = new FormData(); fd.append('kind', f.name.toLowerCase().endsWith('.vcf') ? 'vcf' : 'contacts_csv'); fd.append('file', f);
  inp.value = '';   // so picking the same file again still triggers change
  try { const r = await api('/api/import', {method: 'POST', body: fd}); toast(`Contacts: ${r.created} added, ${r.duplicates} duplicates`, {kind: 'ok'}); await refreshMeta(); window._loadContacts(); }
  catch (e) { toast('Import failed: ' + e.message, {kind: 'err'}); }
}

// ---------------- Connect apps ----------------
const KIND_FOR = {x: [], instagram: ['instagram_json'], facebook: ['facebook_json'], threads: ['threads_json'], messenger: ['messenger_json'], tiktok: ['tiktok_json'], whatsapp: ['whatsapp_txt'],
  wechat: ['paste','csv','json','html'], douyin: ['paste','csv','json','html'], xiaohongshu: ['paste','csv','json','html'], web: ['bookmarks_html','paste','csv','json']};
const METHOD_LABEL = {live_api: 'LIVE API', collector: 'ON-BOX', import: 'IMPORT'};
async function viewConnect(params) {
  $('#view').innerHTML = `<div class="pagehead"><div><h2>Connect apps</h2></div></div>${skelRows(4)}`;
  const st = await api('/api/settings');
  try { if (!SYNC) SYNC = await api('/api/autosync'); } catch { SYNC = SYNC || {accounts: []}; }
  if (params.get('x')) toast('X connected ✔', {kind: 'ok'}); if (params.get('x_error')) toast('X error: ' + params.get('x_error'), {kind: 'err'});
  const sessions = p => ((SYNC && SYNC.accounts) || []).filter(s => s.platform === p);
  const card = p => {
    const ss = sessions(p.id), nl = ss.filter(s => s.state === 'needs_login'), connected = p.count > 0 || ss.length > 0 || (p.id === 'x' && st.x.connected);
    const loginCmd = `python -m savehub.collector login ${p.id} --account @${nl[0] ? String(nl[0].account || '').replace(/^@/, '') : '<handle>'}`;
    return `<div class="card ccon ${p.enabled ? '' : 'off'} ${connected ? 'is-on' : 'not-on'}" data-p="${p.id}">
    <div class="row chead">${pbadge(p.id)}<span class="method ${p.method}" title="${p.method === 'collector' ? 'Collected on the box by the SaveHub collector' : p.method === 'live_api' ? 'Official API' : 'Import export files or paste links'}">${METHOD_LABEL[p.method] || 'IMPORT'}</span><span style="flex:1"></span>
      <label class="small row switch" style="white-space:nowrap"><input type="checkbox" ${p.enabled ? 'checked' : ''} onchange="togglePlatform('${p.id}', this.checked)" aria-label="Collect from ${esc(p.name)}"> enabled</label></div>
    <div class="small cdesc">${esc(p.detail)}</div>
    ${nl.length ? `<div class="needlogin" role="status"><div class="nlart">${ILLU.login}</div><div><b>Needs login</b><p class="small">${esc(nl[0].account)} was signed out. Sign in again on the box to resume auto-sync.</p>
        <button type="button" class="btn small" data-empty-act="signin" data-arg="${esc(p.id)}|${esc(nl[0].account)}">Sign in to ${esc(p.name)}</button></div></div>`
      : connected ? `<div class="cstat ok">${IC.check}<span>${p.count} item${p.count === 1 ? '' : 's'} in library${ss.length ? ` · ${ss.length} signed-in session${ss.length === 1 ? '' : 's'}` : ''}</span></div>`
      : `<div class="cstat none"><span class="dotring" aria-hidden="true"></span><span><b>Not connected yet</b> · ${p.method === 'collector' ? 'sign in once on the box' : p.method === 'live_api' ? 'connect with OAuth' : 'import an export or paste links'}</span></div>`}
    ${p.id === 'x' ? `<div class="small muted">Client ID: ${st.x.client_id_set ? '✔ set' : '✖ not set (Settings)'} · ${st.x.connected ? '✔ connected as @' + esc(st.x.user?.username || '') : 'not connected'}</div>
      <div class="row"><a class="btn small" href="/api/x/auth">Connect X (OAuth 2.0 PKCE)</a><button class="btn ghost small" onclick="xSync()">Sync bookmarks</button></div>`
    : p.method === 'collector' ? `<details class="cmd"><summary>How to sign in</summary><div class="small">In a terminal on the box: <code>${esc(loginCmd)}</code> <button type="button" class="btn ghost xs" data-empty-act="copycmd" data-arg="${esc(loginCmd)}" aria-label="Copy login command">${IC.copy}Copy</button><br>Hand it to someone on the box desktop: <code>python -m savehub.collector login-terminal ${esc(p.id)} --account @&lt;handle&gt;</code></div></details>`
    : `<div class="row"><button class="btn small ${connected ? 'ghost' : ''}" onclick="importModal('${p.id}')">Import…</button></div>`}
  </div>`; };
  $('#view').innerHTML = `<div class="pagehead"><div><h2>Connect apps</h2><p class="muted small">Choose which apps SaveHub collects from.</p></div></div>
  <p class="muted small intro">Honest status: only <span class="method live_api">LIVE API</span> X has an official bookmarks API. Every other app has <b>no public API for your saved/favourite items</b>, so SaveHub imports their <b>official data-export files</b> or links you paste. On the box, the browser collector + auto-sync (see <a href="#/accounts">Accounts</a>) also picks up new saves from logged-in accounts.</p>
  <div class="grid cgrid2">${PLATFORMS.map(card).join('')}</div>
  <div class="panel" style="margin-top:16px"><h4>Quick paste links (any app — 小红书 / 抖音 / 微信 share text works)</h4>
    <textarea id="paste" placeholder="Paste one or more links or share texts here…" aria-label="Links to import"></textarea>
    <div class="row" style="margin-top:8px"><select id="pplat" aria-label="Platform of the pasted links"><option value="auto">Auto-detect platform</option>${PLATFORMS.map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join('')}</select>
    <select id="pacct" aria-label="Account to link the pasted links to">${accountOptions('').replace('All accounts','— link to account —')}</select><button class="btn" onclick="doPaste()">Import pasted links</button></div></div>`;
}
EMPTY_ACTS.copycmd = c => copyText(c, 'Command');
EMPTY_ACTS.signin = arg => { const [p, acc] = String(arg).split('|'); signInModal(p, acc); };
function signInModal(p, account) {
  const h = String(account || '').replace(/^@/, '') || '<handle>', cmd = `python -m savehub.collector login ${p} --account @${h}`;
  modal(`<div class="signin"><div class="illu sm">${ILLU.login}</div><h3 id="siT">Sign in to ${esc(pname(p))}</h3>
    <p class="muted">SaveHub never asks for your ${esc(pname(p))} password here. Sign in once in a real browser on the box; the session is stored encrypted and auto-sync resumes on its own.</p>
    <ol class="steps2"><li>Open a terminal on the box (the SaveHub machine).</li><li>Run this, then finish the login in the window that opens:<div class="cmdline"><code>${esc(cmd)}</code><button type="button" class="btn ghost xs" data-empty-act="copycmd" data-arg="${esc(cmd)}">${IC.copy}Copy</button></div></li><li>Come back here — the badge turns green after the next sync.</li></ol>
    <div class="row end"><button type="button" class="btn ghost" onclick="closeModal()">Close</button>${SYNC && SYNC.available ? `<button type="button" class="btn" onclick="closeModal();syncNowFor(null,'${esc(p)}','@${esc(h)}')">I’ve signed in — sync now</button>` : ''}</div></div>`, 'narrow', 'siT');
}
async function togglePlatform(id, on) {
  const st = (await api('/api/settings')).settings; const s = new Set(st.enabled_platforms); on ? s.add(id) : s.delete(id);
  await jpost('/api/settings', {enabled_platforms: [...s]}, 'PUT'); await refreshMeta(); toast(`${pname(id)} ${on ? 'enabled' : 'disabled'}`, {kind: 'ok'});
  const c = document.querySelector(`.ccon[data-p="${id}"]`); if (c) c.classList.toggle('off', !on);
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
let CURVIEW = '';
async function route() {
  const [path0, qs] = (location.hash.slice(2) || 'library').split('?');
  const m = path0.match(/^library\/item\/(\d+)$/), path = m ? 'library' : (ROUTES[path0] ? path0 : 'library');
  document.querySelectorAll('#nav a').forEach(a => { const on = a.getAttribute('href') === '#/' + path; a.classList.toggle('active', on); on ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current'); });
  if (!m && D.skipRoute) { D.skipRoute = false; if (path === 'library' && CURVIEW === 'library' && $('#results')) return; }
  if (!m && D.open) { D.pushed = false; closeItem(); D.skipRoute = false; if (path === 'library' && CURVIEW === 'library' && $('#results') && !qs) return; }
  if (m) { if (CURVIEW !== 'library' || !$('#results')) { CURVIEW = 'library'; viewLibrary(new URLSearchParams(qs || '')).catch(() => {}); } if (D.id !== +m[1]) showItem(+m[1]); return; }
  CURVIEW = path; const v = $('#view'); v.classList.remove('enter'); void v.offsetWidth; v.classList.add('enter');
  try { await ROUTES[path](new URLSearchParams(qs || '')); }
  catch (e) { $('#view').innerHTML = emptyState({art: 'error', title: 'Something went wrong', text: esc(e.message) + ' — reload the page or try again.', actions: [{label: 'Try again', act: 'reroute', icon: IC.refresh}]}); }
}
EMPTY_ACTS.reroute = () => route();
window.addEventListener('hashchange', route);
window.addEventListener('unhandledrejection', e => { const m = (e.reason && e.reason.message) || String(e.reason || 'Something went wrong'); toast(m, {kind: 'err'}); e.preventDefault(); });
refreshMeta().then(route);
// keep the sticky filter bar right under the (wrapping) top bar
(() => { const top = document.querySelector('header.top'); if (!top || !window.ResizeObserver) return; new ResizeObserver(() => document.documentElement.style.setProperty('--topbar', top.offsetHeight + 'px')).observe(top); })();
