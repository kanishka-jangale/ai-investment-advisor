/* Shared helpers used by every page */
const CSRF = document.querySelector('meta[name="csrf"]').content;
const CUR = document.body.dataset.currency || 'INR';
const PALETTE = ['#4f46e5', '#12b981', '#f5a623', '#e5484d', '#0ea5e9', '#a855f7', '#64748b', '#ec4899'];

const nf = new Intl.NumberFormat(CUR === 'INR' ? 'en-IN' : 'en-US', { style: 'currency', currency: CUR, maximumFractionDigits: 2 });
const money = v => (v === null || v === undefined ? '-' : nf.format(v));
const num = (v, d = 2) => (v === null || v === undefined ? '-' : Number(v).toLocaleString(CUR === 'INR' ? 'en-IN' : 'en-US', { maximumFractionDigits: d }));
const pct = (v, d = 2) => (v === null || v === undefined ? '-' : (v > 0 ? '+' : '') + Number(v).toFixed(d) + '%');
const cls = v => (v > 0 ? 'pos' : v < 0 ? 'neg' : '');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const tip = t => `<span class="tip" tabindex="0" data-tip="${esc(t)}">?</span>`;

async function api(url, opts = {}) {
  const o = { method: 'GET', headers: {}, ...opts };
  if (o.body && !(o.body instanceof FormData)) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(o.body); }
  if (o.method !== 'GET') o.headers['X-CSRF-Token'] = CSRF;
  const r = await fetch(url, o);
  let data = {};
  try { data = await r.json(); } catch (e) { /* not json */ }
  if (r.status === 401) { location.href = '/login'; throw new Error('Signed out'); }
  if (!r.ok) { const e = new Error(data.error || 'Something went wrong.'); e.fields = data.fields; e.details = data.details; throw e; }
  return data;
}

function toast(msg, type = '') {
  const el = document.createElement('div');
  el.className = 'toast ' + type; el.textContent = msg;
  $('#toasts').appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

const charts = {};
function drawChart(id, config) {
  if (charts[id]) charts[id].destroy();
  const ctx = document.getElementById(id);
  if (!ctx) return;
  config.options = Object.assign({ responsive: true, maintainAspectRatio: false }, config.options || {});
  charts[id] = new Chart(ctx, config);
}
function lineOpts(title, yTitle) {
  return {
    plugins: { title: { display: !!title, text: title }, legend: { position: 'bottom' } },
    interaction: { mode: 'index', intersect: false },
    scales: { x: { ticks: { maxTicksLimit: 8 }, title: { display: true, text: 'Date' } }, y: { title: { display: !!yTitle, text: yTitle } } },
    elements: { point: { radius: 0 }, line: { borderWidth: 2 } }
  };
}

function setFieldErrors(form, fields) {
  $$('.err', form).forEach(e => (e.textContent = ''));
  $$('.invalid', form).forEach(e => e.classList.remove('invalid'));
  Object.entries(fields || {}).forEach(([k, msg]) => {
    const input = form.querySelector(`[name="${k}"]`);
    const slot = form.querySelector(`[data-err="${k}"]`);
    if (input) input.classList.add('invalid');
    if (slot) slot.textContent = msg;
  });
}

function modal(html) {
  const bg = document.createElement('div');
  bg.className = 'modal-bg';
  bg.innerHTML = `<div class="modal">${html}</div>`;
  bg.addEventListener('click', e => { if (e.target === bg) bg.remove(); });
  document.body.appendChild(bg);
  return bg;
}

function insightHTML(i) {
  const label = { high: 'High attention', medium: 'Worth a look', low: 'Minor', info: 'Info', good: 'OK' }[i.severity];
  const badge = { high: 'red', medium: '', low: '', info: 'blue', good: 'green' }[i.severity];
  return `<div class="insight ${i.severity}">
    <span class="badge ${badge}">${label}</span>
    <h3>${esc(i.title)}</h3><div>${esc(i.message)}</div>
    <details><summary>Why did this appear?</summary>
      <div><b>Rule:</b> ${esc(i.why)}</div>
      <div><b>Inputs used:</b><ul>${i.inputs.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>
      ${i.assumptions ? `<div><b>Assumptions:</b> ${esc(i.assumptions)}</div>` : ''}
    </details></div>`;
}

/* Notification bell (in-app alerts only) */
async function loadBell() {
  const bell = $('#bell'); if (!bell) return;
  try {
    const d = await api('/api/alerts');
    const hit = d.alerts.filter(a => a.triggered);
    $('#bell-count').textContent = hit.length; $('#bell-count').classList.toggle('hidden', !hit.length);
    $('#bell-list').innerHTML = hit.length
      ? hit.map(a => `<div class="small" style="margin-bottom:.6rem"><b>${esc(a.label)}</b><br>${esc(a.message)}<br><span class="muted">${esc(a.basis)}</span></div>`).join('')
      : '<div class="small muted">No reminders triggered. Create reminders on the Watchlist page.</div>';
  } catch (e) { /* ignore */ }
}
document.addEventListener('DOMContentLoaded', () => {
  const b = $('#bell-btn'); if (b) b.addEventListener('click', () => $('#bell-panel').classList.toggle('hidden'));
  loadBell();
});

function segControl(el, onChange) {
  $$('button', el).forEach(btn => btn.addEventListener('click', () => {
    $$('button', el).forEach(x => x.classList.remove('on')); btn.classList.add('on'); onChange(btn.dataset.v);
  }));
}
