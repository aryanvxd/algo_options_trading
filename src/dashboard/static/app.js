/* Shell: sidebar, hash router, API helper, formatting, charts registry. Pages register
   themselves with App.page(name, {title, render(el), destroy()}). No build step. */

const App = (() => {
  const pages = {};
  let current = null;
  let meta = null;

  // ── per-viewer preferences (localStorage may be unavailable; never required) ──
  const prefs = {
    get(key, fallback) {
      try { const v = localStorage.getItem('algo.' + key); return v === null ? fallback : JSON.parse(v); }
      catch { return fallback; }
    },
    set(key, value) { try { localStorage.setItem('algo.' + key, JSON.stringify(value)); } catch { /* ignore */ } },
  };

  // ── API ──
  async function api(path, params = {}) {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== ''));
    const url = '/api/' + path + (qs.toString() ? '?' + qs : '');
    const dot = document.getElementById('conn-dot');
    try {
      const res = await fetch(url, { cache: 'no-store' });
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
      dot.className = 'conn-dot ok';
      return await res.json();
    } catch (err) {
      dot.className = 'conn-dot down';
      throw err;
    }
  }

  // ── formatting ──
  const inr0 = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });
  const inr2 = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  function money(v, { sign = true, decimals = 0 } = {}) {
    if (v === null || v === undefined) return '—';
    const f = decimals ? inr2 : inr0;
    const s = '₹' + f.format(Math.abs(v));
    if (v < 0) return '−' + s;
    return (sign && v > 0 ? '+' : '') + s;
  }
  const num = (v, d = 2) => (v === null || v === undefined ? '—' : Number(v).toFixed(d));
  const tone = (v) => (v > 0 ? 'pos' : v < 0 ? 'neg' : '');
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function isoDate(d) {
    const z = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`;
  }
  function shiftDate(iso, days) {
    const [y, m, d] = iso.split('-').map(Number);
    return isoDate(new Date(y, m - 1, d + days));
  }
  function prettyDate(iso) {
    const [y, m, d] = iso.split('-').map(Number);
    return new Date(y, m - 1, d).toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
  }

  // Stable colour per strategy, in the order algos are registered.
  const PALETTE = ['#10b981', '#3b82f6', '#f59e0b', '#8b5cf6', '#f43f5e', '#06b6d4', '#84cc16', '#ec4899'];
  function colorFor(algoId) {
    const ids = (meta?.algos || []).map((a) => a.algo_id);
    const i = ids.indexOf(algoId);
    return PALETTE[(i >= 0 ? i : ids.length) % PALETTE.length];
  }

  // ── charts: destroyed on page change / re-render ──
  const charts = new Set();
  function chart(canvas, config) {
    const c = new Chart(canvas, config);
    charts.add(c);
    return c;
  }
  function destroyCharts() { charts.forEach((c) => c.destroy()); charts.clear(); }

  if (window.Chart) {
    Chart.defaults.font.family = "'Segoe UI', system-ui, sans-serif";
    Chart.defaults.font.size = 11;
    Chart.defaults.color = '#94a3b8';
    Chart.defaults.borderColor = '#f1f5f9';
    Chart.defaults.plugins.legend.display = false;
    Chart.defaults.animation.duration = 250;
  }

  // ── toast ──
  function toast(msg, kind = 'info') {
    const el = document.createElement('div');
    el.className = 'toast ' + kind;
    el.innerHTML = `<i class="fa-solid ${kind === 'error' ? 'fa-circle-exclamation' : 'fa-circle-info'}"></i>${esc(msg)}`;
    document.getElementById('toast-container').appendChild(el);
    setTimeout(() => el.remove(), 4500);
  }

  // ── router ──
  function page(name, def) { pages[name] = def; }

  async function go() {
    const name = (location.hash.replace(/^#\/?/, '') || 'overview').split('?')[0];
    const def = pages[name] || pages.overview;
    if (current?.destroy) current.destroy();
    destroyCharts();
    current = def;
    document.querySelectorAll('.nav-btn[data-page]').forEach((b) => b.classList.toggle('active', b.dataset.page === name));
    document.title = `${def.title} · Algo Desk`;
    const el = document.getElementById('page');
    el.innerHTML = '';
    try { await def.render(el); }
    catch (err) { console.error(err); toast('Could not load the page: ' + err.message, 'error'); }
  }

  function initSidebar() {
    const sb = document.getElementById('sidebar');
    if (prefs.get('sidebarCollapsed', false)) sb.classList.add('collapsed');
    document.getElementById('sidebar-toggle').addEventListener('click', () => {
      sb.classList.toggle('collapsed');
      prefs.set('sidebarCollapsed', sb.classList.contains('collapsed'));
    });
    document.querySelectorAll('.nav-btn[data-page]').forEach((b) =>
      b.addEventListener('click', () => { location.hash = '#/' + b.dataset.page; }));
  }

  async function start() {
    initSidebar();
    try {
      meta = await api('meta');
      document.getElementById('app-version').textContent = 'v' + meta.version;
    } catch (err) {
      toast('Dashboard server not reachable', 'error');
    }
    window.addEventListener('hashchange', go);
    go();
  }

  return {
    start, page, api, prefs, toast, chart, destroyCharts, colorFor,
    money, num, tone, esc, isoDate, shiftDate, prettyDate,
    get meta() { return meta; },
  };
})();
