/* Shell: sidebar, hash router, API helper, formatting, charts registry. Pages register
   themselves with App.page(name, {title, render(el, args), destroy()}); a hash like
   #/strategy/iron_condor/today renders page "strategy" with args ["iron_condor", "today"]. No build step. */

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
  async function api(path, params = {}, { method = 'GET', body } = {}) {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== ''));
    const url = '/api/' + path + (qs.toString() ? '?' + qs : '');
    const dot = document.getElementById('conn-dot');
    try {
      const headers = method === 'GET' ? {} : { 'X-Algo-Desk': '1' };    // the server accepts writes only with it
      if (body) headers['Content-Type'] = 'application/json';
      const res = await fetch(url, { cache: 'no-store', method, headers,
        body: body ? JSON.stringify(body) : undefined });
      if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
      dot.className = 'conn-dot ok';
      return res.status === 204 ? null : await res.json();
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

  function timeOf(iso, seconds = true) {
    if (!iso) return '—';
    return iso.slice(11, seconds ? 19 : 16) || '—';
  }
  function duration(s) {
    if (s === null || s === undefined) return '—';
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.round(s % 60);
    return h ? `${h}h ${m}m` : m ? `${m}m ${sec}s` : `${sec}s`;
  }
  // "09:35" for the same day as ``dayIso``, else "Mon 2 Nov 09:35" (carried positions).
  function whenOn(iso, dayIso) {
    if (!iso) return '—';
    if (!dayIso || iso.slice(0, 10) === dayIso) return timeOf(iso);
    const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
    const label = new Date(y, m - 1, d).toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short' });
    return `${label} ${timeOf(iso, false)}`;
  }
  const pct = (v, d = 2) => (v === null || v === undefined ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(d)}%`);

  // Shared labels and small building blocks used by several pages.
  const REASONS = {
    TIME_EXIT: 'Time exit', STOP_LOSS: 'Stop loss', TARGET: 'Target', PREMIUM_BLOWOUT: 'Premium blow-out',
    KILL_SWITCH: 'Kill switch', STALE_FEED: 'Stale feed', ENTRY_FAILED: 'Entry failed', MAX_DAILY_LOSS: 'Max daily loss',
    TRAILING_STOP: 'Trailing stop', STALL: 'Stalled', MAX_HOLD: 'Max hold time', RESTART_PARTIAL: 'Restart (partial)',
    CARRY_EXIT: 'Next-day exit', RENKO_REVERSAL: 'Renko reversal', TIME_STOP: 'Time stop', BIAS_FLIP: 'Bias flip',
    BREAKEVEN_STOP: 'Breakeven stop', MANAGED: 'Closed by algo',
  };
  const reason = (r) => (r ? r.split(/,\s*/).map((x) => REASONS[x] || x).join(', ') : '—');
  function chip(label, cls, live = false) {
    return `<span class="chip ${cls}${live ? ' live' : ''}"><span class="chip-dot"></span>${label}</span>`;
  }
  function kpi(label, value, cls, icon, toneCls, foot = '') {
    return `<div class="kpi-tile">
      <div class="kpi-head"><span class="kpi-icon ${toneCls}"><i class="fa-solid ${icon}"></i></span>
        <span class="stat-label">${label}</span></div>
      <div class="stat-value ${cls}">${value}</div>
      ${foot ? `<div class="stat-foot">${foot}</div>` : ''}
    </div>`;
  }
  function emptyState(icon, title, text, extra = '') {
    return `<div class="section-card empty-state"><i class="fa-solid ${icon}"></i>
      <div class="empty-title">${title}</div><p>${text}</p>${extra}</div>`;
  }

  // Stable colour per strategy, in the order algos are registered.
  const PALETTE = ['#10b981', '#3b82f6', '#f59e0b', '#8b5cf6', '#f43f5e', '#06b6d4', '#84cc16', '#ec4899'];
  function colorFor(algoId) {
    const ids = (meta?.strategies || meta?.algos || []).map((a) => a.algo_id);
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

  // #/compare?runs=a,b -> path "compare", query URLSearchParams("runs=a,b"); pages get the query as
  // render's third argument.
  function route() {
    const [rawPath, rawQuery = ''] = (location.hash.replace(/^#\/?/, '') || 'overview').split('?');
    const path = rawPath || 'overview';
    const parts = path.split('/').filter(Boolean).map(decodeURIComponent);
    return { path, name: parts[0] || 'overview', args: parts.slice(1), query: new URLSearchParams(rawQuery) };
  }

  // A nav button is active on its page and the pages under it, unless data-not names a sub-path
  // that has its own button (Backtests vs Market data).
  // data-exact: only its own page and the pages under it named in capitals (Swing › Sectors owns
  // #/swing/NIFTY%20AUTO but not #/swing/picks, which has its own button).
  function isActive(b, path) {
    const p = b.dataset.page;
    const below = path.startsWith(p + '/');
    const hit = path === p || (below && (!b.dataset.exact || !/^[a-z]/.test(path.slice(p.length + 1))));
    const not = b.dataset.not;
    return hit && !(not && (path === not || path.startsWith(not + '/')));
  }
  function markActive(path) {
    document.querySelectorAll('.nav-btn[data-page]').forEach((b) => b.classList.toggle('active', isActive(b, path)));
  }

  async function go() {
    const { path, name, args, query } = route();
    const def = pages[name] || pages.overview;
    if (current?.destroy) current.destroy();
    destroyCharts();
    current = def;
    showMenu(SUBMENUS.includes(name) ? name : 'main');
    markActive(path);
    document.title = `${typeof def.title === 'function' ? def.title(args, query) : def.title} · Algo Desk`;
    const el = document.getElementById('page');
    el.innerHTML = '';
    try { await def.render(el, args, query); }
    catch (err) { console.error(err); toast('Could not load the page: ' + err.message, 'error'); }
  }

  // Go to a hash, re-rendering even when it is already the current one.
  function navigate(hash) {
    if (location.hash === hash) go();
    else location.hash = hash;
  }

  function buildStrategyNav() {
    const box = document.getElementById('nav-strategies');
    if (!box) return;
    const list = (meta?.strategies || []).filter((a) => !a.retired);
    box.innerHTML = list.length ? list.map((a) => `
      <button class="nav-btn nav-sub" data-page="strategy/${esc(a.algo_id)}" data-tooltip="${esc(a.name)}">
        <i class="fa-solid fa-circle strategy-dot" style="color:${colorFor(a.algo_id)}"></i>
        <span class="nav-label">${esc(a.name.replace(/^NIFTY\s+/, ''))}</span>
      </button>`).join('') : '<div class="nav-empty nav-label">No strategies found</div>';
    box.querySelectorAll('.nav-btn[data-page]').forEach((b) =>
      b.addEventListener('click', () => { location.hash = '#/' + b.dataset.page; }));
  }

  // ── sidebar: two menus (main / a sub-menu such as Backtests) and collapsible sections ──
  const SUBMENUS = ['backtests', 'swing'];          // a route whose first part is one of these shows its sub-menu

  function showMenu(which) {
    const box = document.getElementById('sidebar-menus');
    if (!box || box.dataset.menu === which) return;
    box.dataset.menu = which;
    // Only the menu on screen is reachable by keyboard and screen readers.
    box.querySelectorAll('.menu').forEach((m) => {
      const on = m.classList.contains('menu-' + which);
      m.toggleAttribute('inert', !on);
      m.setAttribute('aria-hidden', on ? 'false' : 'true');
    });
  }

  function initSections() {
    document.querySelectorAll('.nav-group[data-group]').forEach((g) => {
      const key = 'nav.open.' + g.dataset.group;
      const head = g.querySelector('.nav-section');
      const set = (open) => { g.classList.toggle('closed', !open); head.setAttribute('aria-expanded', String(open)); };
      set(prefs.get(key, true));
      head.addEventListener('click', () => {
        const open = g.classList.contains('closed');
        set(open);
        prefs.set(key, open);
      });
    });
  }

  // Recent runs in the Backtests sub-menu: [{name, state, label?}], newest first.
  function setRecent(runs) {
    const box = document.getElementById('nav-recent');
    if (!box) return;
    box.innerHTML = runs.length ? runs.map((r) => `
      <button class="nav-btn nav-sub" data-page="backtests/run/${esc(r.name)}" data-tooltip="${esc(r.name)} (${esc(r.label || r.state)})" title="${esc(r.name)} · ${esc(r.label || r.state)}">
        <span class="run-dot ${esc(r.state)}"></span><span class="nav-label">${esc(r.name)}</span>
      </button>`).join('') : '<div class="nav-empty nav-label">No runs yet</div>';
    box.querySelectorAll('.nav-btn[data-page]').forEach((b) =>
      b.addEventListener('click', () => { location.hash = '#/backtests/run/' + encodeURIComponent(b.dataset.page.slice(14)); }));
    markActive(route().path);
  }

  // The Swing sub-menu's watchlists: [{id, name, kind, count}].
  function setWatchlists(lists) {
    const box = document.getElementById('nav-watchlists');
    if (!box) return;
    box.innerHTML = lists.length ? lists.map((w) => `
      <button class="nav-btn nav-sub" data-page="swing/watch/${w.id}" data-tooltip="${esc(w.name)}" title="${esc(w.name)} · ${w.count} stocks">
        <i class="fa-solid ${w.kind === 'auto' ? 'fa-robot' : 'fa-star'}"></i><span class="nav-label">${esc(w.name)}</span><span class="nav-count">${w.count}</span>
      </button>`).join('') : '<div class="nav-empty nav-label">No watchlists</div>';
    box.querySelectorAll('.nav-btn[data-page]').forEach((b) =>
      b.addEventListener('click', () => { location.hash = '#/' + b.dataset.page; }));
    markActive(route().path);
  }

  function initSidebar() {
    const sb = document.getElementById('sidebar');
    // Narrow windows always start collapsed (icons only); wide ones follow the saved choice.
    const narrow = window.matchMedia('(max-width: 900px)');
    const apply = () => sb.classList.toggle('collapsed', narrow.matches || prefs.get('sidebarCollapsed', false));
    apply();
    narrow.addEventListener('change', apply);
    document.getElementById('sidebar-toggle').addEventListener('click', () => {
      sb.classList.toggle('collapsed');
      if (!narrow.matches) prefs.set('sidebarCollapsed', sb.classList.contains('collapsed'));
    });
    initSections();
    document.querySelector('.menu-main')?.setAttribute('aria-hidden', 'false');
    document.querySelectorAll('.menu:not(.menu-main)').forEach((m) => m.toggleAttribute('inert', true));
    document.querySelectorAll('.nav-btn[data-page]').forEach((b) =>
      b.addEventListener('click', () => {
        // A sub-menu button on its own section (e.g. back in Backtests after ← Main menu) only swaps
        // the menu; from anywhere else it opens the section's first page.
        const sub = b.dataset.submenu;
        if (sub && route().name === sub) { showMenu(sub); return; }
        location.hash = '#/' + b.dataset.page;
      }));
    // ← Main menu swaps the menu and leaves the page as it is.
    document.querySelectorAll('.nav-back').forEach((b) => b.addEventListener('click', () => showMenu('main')));
  }

  async function start() {
    initSidebar();
    try {
      meta = await api('meta');
      document.getElementById('app-version').textContent = 'v' + meta.version;
      buildStrategyNav();
    } catch (err) {
      toast('Dashboard server not reachable', 'error');
    }
    window.addEventListener('hashchange', go);
    // The first page shows its menu in place (no slide when a #/backtests link is opened).
    const menus = document.getElementById('sidebar-menus');
    menus?.classList.add('instant');
    go();
    requestAnimationFrame(() => requestAnimationFrame(() => menus?.classList.remove('instant')));
  }

  return {
    start, page, api, prefs, toast, chart, destroyCharts, colorFor, navigate, route, setRecent, setWatchlists, showMenu,
    money, num, tone, esc, isoDate, shiftDate, prettyDate, timeOf, whenOn, duration, pct,
    REASONS, reason, chip, kpi, emptyState,
    get meta() { return meta; },
  };
})();
