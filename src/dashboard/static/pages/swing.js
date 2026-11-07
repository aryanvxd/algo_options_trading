/* Swing review: #/swing (sectors, then their stocks), #/swing/holdings (your holdings against
   their zones), #/swing/orders (your GTTs, ATOs and alerts), #/swing/charts (flip through charts)
   and #/swing/<SYMBOL> (chart and zones; an index also lists its stocks). Information for your own
   decisions: it makes no setups and only reads from Kite. The lists come from the last
   `algo swing scan`; a stock's zones are computed when its page opens. */

(() => {
  const POSITIONS = ['near support', 'near resistance', 'in a zone', 'mid-range'];
  const POS_CLASS = { 'near support': 'sw-sup', 'near resistance': 'sw-res', 'in a zone': 'sw-flip' };
  const ZONE_RGB = { support: '16, 185, 129', resistance: '239, 68, 68', flipped: '245, 158, 11', inside: '59, 130, 246' };

  // Kept between pages, so coming back from a chart keeps the filters and the sort.
  const list = { index: '', position: '', q: '', sectorSort: null, stockSort: null, heldSort: null, memberSort: null,
    memberPosition: '', orderKind: '', orderActive: true, orderQ: '', chartGroup: 'indices' };
  const ORDER_KINDS = [['', 'All'], ['gtt', 'GTT'], ['ato', 'ATO'], ['alert', 'Alerts']];
  const ORDER_COLOUR = { gtt: '#8b5cf6', ato: '#0891b2', alert: '#f59e0b' };
  const MEMBER_REFRESH_MS = 30000;
  const ORDERS_MAX_AGE_MS = 60000;
  let chart = null;
  let data = null;
  let held = null;
  let members = null;
  let memberTimer = null;
  let ordersCache = null;
  let renderId = 0;                      // async results for a page already left are dropped

  const tabs = (active) => `<nav class="tabs" aria-label="Swing sections">${
    [['list', '#/swing', 'fa-layer-group', 'Sectors and stocks'], ['holdings', '#/swing/holdings', 'fa-briefcase', 'Holdings'],
      ['orders', '#/swing/orders', 'fa-bell', 'Orders'], ['charts', '#/swing/charts', 'fa-chart-column', 'Charts']]
      .map(([k, href, icon, label]) => `<a class="tab${k === active ? ' active' : ''}" href="${href}"><i class="fa-solid ${icon}"></i>${label}</a>`).join('')}
  </nav>`;

  async function getOrders(force = false) {
    if (!force && ordersCache && Date.now() - ordersCache.at < ORDERS_MAX_AGE_MS) return ordersCache.data;
    const out = await App.api('swing/orders');
    ordersCache = { at: Date.now(), data: out };
    return out;
  }

  const position = (p) => `<span class="${POS_CLASS[p] || 'muted'}">${App.esc(p)}</span>`;
  const atr = (v) => (v === null || v === undefined ? '—' : v.toFixed(1));
  const range = (z) => (z ? `${App.num(z[0])}–${App.num(z[1])}` : '—');
  const change = (v) => `<span class="${App.tone(v)}">${App.pct(v, 1)}</span>`;
  const link = (s) => `#/swing/${encodeURIComponent(s)}`;
  const zoneKey = (z) => (z.flipped ? 'flipped' : z.role);
  const zoneName = (z) => (z.flipped ? `flipped ${z.role}` : z.role);

  function spark(values) {
    if (!values || values.length < 2) return '';
    const lo = Math.min(...values), hi = Math.max(...values), w = 72, h = 22;
    const pts = values.map((v, i) => `${(i / (values.length - 1) * w).toFixed(1)},${(h - 2 - (v - lo) / ((hi - lo) || 1) * (h - 4)).toFixed(1)}`);
    const colour = values[values.length - 1] >= values[0] ? 'var(--profit)' : 'var(--loss)';
    return `<svg class="sw-spark" viewBox="0 0 ${w} ${h}" aria-hidden="true"><polyline points="${pts.join(' ')}" stroke="${colour}" /></svg>`;
  }

  function sorted(rows, sort) {
    if (!sort) return rows;
    const val = (r) => r[sort.key];
    const dir = sort.up ? 1 : -1;
    return [...rows].sort((a, b) => {
      const x = val(a), y = val(b);
      if (x === null || x === undefined) return 1;           // blanks last, either way
      if (y === null || y === undefined) return -1;
      return (typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y))) * dir;
    });
  }

  function header(cols, sort) {
    return `<tr>${cols.map(([label, key, right]) => `<th data-key="${key}" class="sw-sort${right ? '' : ' sw-left'}">${label}${
      sort?.key === key ? ` <i class="fa-solid fa-caret-${sort.up ? 'up' : 'down'}"></i>` : ''}</th>`).join('')}</tr>`;
  }

  function bindSort(table, which, redraw) {
    table.querySelectorAll('th[data-key]').forEach((th) => th.addEventListener('click', () => {
      const key = th.dataset.key;
      const cur = list[which];
      list[which] = { key, up: cur?.key === key ? !cur.up : true };
      redraw();
    }));
  }

  // ── list page ──
  const SECTOR_COLS = [['Index', 'symbol'], ['Kind', 'kind'], ['', 'symbol'], ['Close', 'close', 1], ['1 week', 'week_pct', 1],
    ['1 month', 'month_pct', 1], ['Position', 'position'], ['Support', 'support_atr', 1], ['ATR away', 'support_atr', 1],
    ['Resistance', 'resistance_atr', 1], ['ATR away', 'resistance_atr', 1], ['Stocks', 'stocks', 1],
    ['Near support', 'near_support', 1], ['Near resistance', 'near_resistance', 1]];
  const STOCK_COLS = [['Stock', 'symbol'], ['Industry', 'industry'], ['Close', 'close', 1], ['1 week', 'week_pct', 1],
    ['1 month', 'month_pct', 1], ['Position', 'position'], ['Support', 'support_atr', 1], ['ATR away', 'support_atr', 1],
    ['Resistance', 'resistance_atr', 1], ['ATR away', 'resistance_atr', 1], ['₹ crore a day', 'traded_cr', 1]];

  function drawSectors() {
    const table = document.getElementById('sw-sectors');
    if (!table) return;
    table.querySelector('thead').innerHTML = header(SECTOR_COLS, list.sectorSort);
    table.querySelector('tbody').innerHTML = sorted(data.sectors, list.sectorSort).map((r) => `
      <tr data-index="${App.esc(r.symbol)}"${r.symbol === list.index ? ' class="sw-on"' : ''}>
        <td><a href="${link(r.symbol)}">${App.esc(r.symbol)}</a></td><td class="sw-left muted">${App.esc(r.kind)}</td>
        <td class="sw-left">${spark(r.spark)}</td><td>${App.num(r.close)}</td><td>${change(r.week_pct)}</td><td>${change(r.month_pct)}</td>
        <td class="sw-left">${position(r.position)}</td><td>${range(r.support)}</td><td>${atr(r.support_atr)}</td>
        <td>${range(r.resistance)}</td><td>${atr(r.resistance_atr)}</td><td>${r.stocks}</td>
        <td>${r.near_support}</td><td>${r.near_resistance}</td>
      </tr>`).join('');
    bindSort(table, 'sectorSort', drawSectors);
    table.querySelectorAll('tbody tr').forEach((tr) => tr.addEventListener('click', (e) => {
      if (e.target.closest('a')) return;
      list.index = list.index === tr.dataset.index ? '' : tr.dataset.index;
      document.getElementById('sw-index').value = list.index;
      drawSectors();
      drawStocks();
    }));
  }

  function drawStocks() {
    const table = document.getElementById('sw-stocks');
    if (!table) return;
    const q = list.q.trim().toLowerCase();
    const rows = data.stocks.filter((r) => (!list.index || r.indices.includes(list.index))
      && (!list.position || r.position === list.position)
      && (!q || `${r.symbol} ${r.industry}`.toLowerCase().includes(q)));
    document.getElementById('sw-count').textContent = `${rows.length} of ${data.stocks.length} stocks`;
    table.querySelector('thead').innerHTML = header(STOCK_COLS, list.stockSort);
    table.querySelector('tbody').innerHTML = sorted(rows, list.stockSort).map((r) => `
      <tr data-symbol="${App.esc(r.symbol)}">
        <td><a href="${link(r.symbol)}">${App.esc(r.symbol)}</a></td><td class="sw-left muted">${App.esc(r.industry)}</td>
        <td>${App.num(r.close)}</td><td>${change(r.week_pct)}</td><td>${change(r.month_pct)}</td>
        <td class="sw-left">${position(r.position)}</td><td>${range(r.support)}</td><td>${atr(r.support_atr)}</td>
        <td>${range(r.resistance)}</td><td>${atr(r.resistance_atr)}</td><td>${App.num(r.traded_cr, 0)}</td>
      </tr>`).join('');
    bindSort(table, 'stockSort', drawStocks);
    table.querySelectorAll('tbody tr').forEach((tr) => tr.addEventListener('click', (e) => {
      if (!e.target.closest('a')) location.hash = link(tr.dataset.symbol);
    }));
  }

  async function renderList(el) {
    el.innerHTML = '<div class="section-card"><div class="skeleton" style="height:160px"></div></div>';
    data = await App.api('swing/overview');
    if (!data.available) {
      el.innerHTML = App.emptyState('fa-layer-group', 'No scan yet',
        'Run <code>algo swing scan</code> after <code>algo login</code>. It needs no market hours.');
      return;
    }
    const count = (rows, p) => rows.filter((r) => r.position === p).length;
    el.innerHTML = `
      <div class="page-header">
        <div><div class="page-title"><i class="fa-solid fa-layer-group"></i>Swing review</div>
          <div class="page-subtitle">Where each sector and stock sits against its support and resistance zones</div></div>
        <div class="header-meta">Candles to ${App.prettyDate(data.candles_to)}</div>
      </div>
      ${tabs('list')}
      <p class="sw-note"><i class="fa-solid fa-circle-info"></i>${App.esc(data.note)}</p>
      <div class="kpi-strip">
        ${App.kpi('Sectors near support', count(data.sectors, 'near support'), '', 'fa-arrow-down', 'tone-green', `of ${data.sectors.length} indices`)}
        ${App.kpi('Sectors near resistance', count(data.sectors, 'near resistance'), '', 'fa-arrow-up', 'tone-red', `within ${data.near_atr} ATR`)}
        ${App.kpi('Stocks near support', count(data.stocks, 'near support'), '', 'fa-arrow-down', 'tone-green', `of ${data.stocks.length} liquid stocks`)}
        ${App.kpi('Stocks near resistance', count(data.stocks, 'near resistance'), '', 'fa-arrow-up', 'tone-red', `within ${data.near_atr} ATR`)}
        ${App.kpi('Stocks in a zone', count(data.stocks, 'in a zone'), '', 'fa-grip-lines', 'tone-amber', `of ${data.stocks.length} liquid stocks`)}
      </div>
      <div class="section-card">
        <div class="section-title sw-gap">Sectors and themes</div>
        <div class="table-wrap"><table class="data-table clickable" id="sw-sectors"><thead></thead><tbody></tbody></table></div>
      </div>
      <div class="section-card">
        <div class="top-bar">
          <div class="section-title">Stocks</div>
          <div class="top-bar-group">
            <select class="input-field" id="sw-index" aria-label="Index"><option value="">All indices</option>${
              data.sectors.map((s) => `<option${s.symbol === list.index ? ' selected' : ''}>${App.esc(s.symbol)}</option>`).join('')}</select>
            <select class="input-field" id="sw-position" aria-label="Position"><option value="">Any position</option>${
              POSITIONS.map((p) => `<option${p === list.position ? ' selected' : ''}>${p}</option>`).join('')}</select>
            <input class="input-field" id="sw-find" placeholder="Search stock or industry" aria-label="Search" value="${App.esc(list.q)}" />
            <span class="muted" id="sw-count"></span>
          </div>
        </div>
        <div class="table-wrap"><table class="data-table clickable" id="sw-stocks"><thead></thead><tbody></tbody></table></div>
      </div>`;
    drawSectors();
    drawStocks();
    el.querySelector('#sw-index').addEventListener('change', (e) => { list.index = e.target.value; drawSectors(); drawStocks(); });
    el.querySelector('#sw-position').addEventListener('change', (e) => { list.position = e.target.value; drawStocks(); });
    el.querySelector('#sw-find').addEventListener('input', (e) => { list.q = e.target.value; drawStocks(); });
  }

  // ── holdings page ──
  const HELD_COLS = [['Stock', 'symbol'], ['Industry', 'industry'], ['Qty', 'quantity', 1], ['Average', 'average', 1], ['Close', 'close', 1],
    ['Value', 'value', 1], ['P&L', 'pnl', 1], ['P&L %', 'pnl_pct', 1], ['ATRs from average', 'vs_average_atr', 1], ['Position', 'position'],
    ['Support', 'support_atr', 1], ['ATR away', 'support_atr', 1], ['Resistance', 'resistance_atr', 1], ['ATR away', 'resistance_atr', 1],
    ['Average in zone', 'average_zone']];

  function drawHeld() {
    const table = document.getElementById('sw-held');
    if (!table) return;
    table.querySelector('thead').innerHTML = header(HELD_COLS, list.heldSort);
    table.querySelector('tbody').innerHTML = sorted(held.rows, list.heldSort).map((r) => `
      <tr${r.chart ? ` data-symbol="${App.esc(r.symbol)}"` : ''}>
        <td>${r.chart ? `<a href="${link(r.symbol)}">${App.esc(r.symbol)}</a>` : App.esc(r.symbol)}</td>
        <td class="sw-left muted">${App.esc(r.industry) || '—'}</td><td>${r.quantity}</td><td>${App.num(r.average)}</td><td>${App.num(r.close)}</td>
        <td>${App.money(r.value, { sign: false })}</td><td class="${App.tone(r.pnl)}">${App.money(r.pnl)}</td><td>${change(r.pnl_pct)}</td>
        <td>${App.num(r.vs_average_atr, 1)}</td>
        <td class="sw-left">${r.position ? position(r.position) : `<span class="muted">${App.esc(r.note)}</span>`}${
          r.no_support_below ? ' <span class="sw-res">· no support below</span>' : ''}</td>
        <td>${range(r.support)}</td><td>${atr(r.support_atr)}</td><td>${range(r.resistance)}</td><td>${atr(r.resistance_atr)}</td>
        <td class="sw-left">${r.average_zone ? range(r.average_zone) : '—'}</td>
      </tr>`).join('');
    bindSort(table, 'heldSort', drawHeld);
    table.querySelectorAll('tbody tr[data-symbol]').forEach((tr) => tr.addEventListener('click', (e) => {
      if (!e.target.closest('a')) location.hash = link(tr.dataset.symbol);
    }));
  }

  async function renderHoldings(el) {
    el.innerHTML = '<div class="section-card"><div class="skeleton" style="height:160px"></div></div>';
    held = await App.api('swing/holdings');
    const head = `<div class="page-header">
        <div><div class="page-title"><i class="fa-solid fa-layer-group"></i>Swing review</div>
          <div class="page-subtitle">Your holdings against their support and resistance zones</div></div>
        ${held.available ? `<div class="header-meta">Read from Kite ${App.esc(held.fetched_at.slice(0, 16).replace('T', ' '))}</div>` : ''}
      </div>${tabs('holdings')}`;
    if (!held.available) {
      el.innerHTML = head + App.emptyState('fa-briefcase', 'Holdings not read yet',
        'Run <code>algo swing holdings</code> after <code>algo login</code>, or <code>algo swing scan</code>, which does it too.');
      return;
    }
    const t = held.totals;
    const count = (f) => held.rows.filter(f).length;
    el.innerHTML = `${head}
      <p class="sw-note"><i class="fa-solid fa-circle-info"></i>${App.esc(held.note)}</p>
      <div class="kpi-strip">
        ${App.kpi('Holdings', t.holdings, '', 'fa-briefcase', 'tone-blue', `${App.money(t.invested, { sign: false })} invested`)}
        ${App.kpi('Value', App.money(t.value, { sign: false }), '', 'fa-wallet', 'tone-slate', 'at the last close')}
        ${App.kpi('P&L', App.money(t.pnl), App.tone(t.pnl), 'fa-chart-line', t.pnl >= 0 ? 'tone-green' : 'tone-red', App.pct(t.pnl_pct, 1))}
        ${App.kpi('Near support', count((r) => r.position === 'near support'), '', 'fa-arrow-down', 'tone-green', 'within 1 ATR')}
        ${App.kpi('Near resistance', count((r) => r.position === 'near resistance'), '', 'fa-arrow-up', 'tone-red', 'within 1 ATR')}
        ${App.kpi('No support below', count((r) => r.no_support_below), '', 'fa-arrow-trend-down', 'tone-amber', 'below all its zones')}
      </div>
      <div class="section-card">
        <div class="table-wrap"><table class="data-table clickable" id="sw-held"><thead></thead><tbody></tbody></table></div>
      </div>`;
    drawHeld();
  }

  // ── orders page ──
  function drawOrders(out) {
    const q = list.orderQ.trim().toLowerCase();
    const shown = out.rows.filter((r) => (!list.orderKind || r.kind === list.orderKind) && (!list.orderActive || r.active)
      && (!q || `${r.symbol} ${r.name}`.toLowerCase().includes(q)));
    const pool = out.rows.filter((r) => !list.orderActive || r.active);
    document.getElementById('sw-kinds').innerHTML = ORDER_KINDS.map(([k, label]) => {
      const n = k ? pool.filter((r) => r.kind === k).length : pool.length;
      return `<button class="btn-secondary sw-kind-btn${k === list.orderKind ? ' sw-active' : ''}" data-kind="${k}">${label} (${n})</button>`;
    }).join('');
    document.querySelectorAll('.sw-kind-btn').forEach((b) => b.addEventListener('click', () => { list.orderKind = b.dataset.kind; drawOrders(out); }));
    const pill = { gtt: 'tone-violet', ato: 'tone-blue', alert: 'tone-amber' };
    const body = document.getElementById('sw-orders-body');
    body.innerHTML = shown.length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>Stock</th><th class="sw-left">Type</th>
      <th class="sw-left">Condition</th><th class="sw-left">Order</th><th class="sw-left">Status</th><th class="sw-left">Created</th><th class="sw-left">Expires</th></tr></thead>
      <tbody>${shown.map((r) => `<tr>
        <td>${['NSE', 'BSE', 'INDICES'].includes(r.exchange) ? `<a href="${link(r.symbol)}">${App.esc(r.symbol)}</a>` : App.esc(r.symbol)}</td>
        <td class="sw-left"><span class="sw-pill ${pill[r.kind]}">${r.kind === 'alert' ? 'Alert' : r.kind.toUpperCase()}</span></td>
        <td class="sw-left">${App.esc(r.condition)}${r.name ? `<div class="muted sw-small">${App.esc(r.name)}</div>` : ''}</td>
        <td class="sw-left">${r.orders.map(App.esc).join('<br>') || '—'}</td>
        <td class="sw-left ${r.active ? 'sw-sup' : 'muted'}">${App.esc(r.status)}</td>
        <td class="sw-left muted">${App.esc((r.created || '').slice(0, 10)) || '—'}</td>
        <td class="sw-left muted">${App.esc((r.ends || '').slice(0, 10)) || '—'}</td></tr>`).join('')}</tbody></table></div>`
      : '<p class="muted sw-pad">Nothing to show with these filters.</p>';
  }

  async function renderOrders(el, force = false) {
    el.innerHTML = '<div class="section-card"><div class="skeleton" style="height:160px"></div></div>';
    const token = renderId;
    const out = await getOrders(force);
    if (token !== renderId) return;
    const head = `<div class="page-header">
        <div><div class="page-title"><i class="fa-solid fa-layer-group"></i>Swing review</div>
          <div class="page-subtitle">Your GTTs, ATOs and alerts on Kite. Read-only: manage them in Kite</div></div>
        <div class="header-meta">${out.as_of ? `Read from Kite at ${App.timeOf(out.as_of)}` : ''}
          <button class="btn-secondary" id="sw-orders-refresh"><i class="fa-solid fa-rotate"></i>Refresh</button></div>
      </div>${tabs('orders')}`;
    if (!out.available) {
      el.innerHTML = head + App.emptyState('fa-bell', 'Not connected to Kite', `${App.esc(out.reason)} Your orders aren't stored, so this page needs a login.`);
    } else {
      el.innerHTML = `${head}
        ${out.errors.map((e) => `<p class="sw-note sw-res"><i class="fa-solid fa-triangle-exclamation"></i>${App.esc(e)}</p>`).join('')}
        <div class="section-card">
          <div class="top-bar">
            <div class="top-bar-group" id="sw-kinds" role="group" aria-label="Type"></div>
            <div class="top-bar-group">
              <label class="sw-check"><input type="checkbox" id="sw-active-only"${list.orderActive ? ' checked' : ''} />Active only</label>
              <input class="input-field" id="sw-orders-find" placeholder="Search symbol or name" aria-label="Search" value="${App.esc(list.orderQ)}" />
            </div>
          </div>
          <div id="sw-orders-body"></div>
        </div>`;
      drawOrders(out);
      el.querySelector('#sw-active-only').addEventListener('change', (e) => { list.orderActive = e.target.checked; drawOrders(out); });
      el.querySelector('#sw-orders-find').addEventListener('input', (e) => { list.orderQ = e.target.value; drawOrders(out); });
    }
    el.querySelector('#sw-orders-refresh').addEventListener('click', () => renderOrders(el, true));
  }

  // ── charts page: pick a list, then step through its charts ──
  async function renderCharts(el, symbol) {
    el.innerHTML = '<div class="section-card"><div class="skeleton" style="height:160px"></div></div>';
    const token = renderId;
    const [ov, hd, od] = await Promise.all([App.api('swing/overview'), App.api('swing/holdings'), getOrders().catch(() => null)]);
    if (token !== renderId) return;
    if (!ov.available) {
      el.innerHTML = tabs('charts') + App.emptyState('fa-chart-column', 'No scan yet', 'Run <code>algo swing scan</code> first.');
      return;
    }
    const indices = ov.sectors.map((s) => s.symbol);
    const stocks = ov.stocks.map((s) => s.symbol);
    const heldSymbols = chartable(hd);
    const withOrders = [...new Set((od?.rows || []).filter((r) => r.active && ['NSE', 'BSE', 'INDICES'].includes(r.exchange)).map((r) => r.symbol))].sort();
    const known = new Set([...indices, ...stocks, ...heldSymbols, ...withOrders]);
    const groups = [['indices', 'All indices', indices], ['holdings', 'Holdings', heldSymbols], ['orders', 'With orders or alerts', withOrders],
      ['stocks', 'All liquid stocks', stocks],
      ...indices.map((n) => [`index:${n}`, `Stocks in ${n}`, ov.stocks.filter((s) => s.indices.includes(n)).map((s) => s.symbol)])];
    let group = groups.find((g) => g[0] === list.chartGroup) || groups[0];
    if (symbol && !group[2].includes(symbol)) group = groups.find((g) => g[2].includes(symbol)) || group;
    list.chartGroup = group[0];
    const names = group[2];
    const current = symbol || names[0];
    const at = names.indexOf(current);
    const go = (s) => { if (s) location.hash = `#/swing/charts/${encodeURIComponent(s)}`; };
    el.innerHTML = `
      <div class="page-header"><div><div class="page-title"><i class="fa-solid fa-layer-group"></i>Swing review</div>
        <div class="page-subtitle">Pick a list, then step through its charts</div></div></div>
      ${tabs('charts')}
      <div class="top-bar">
        <div class="top-bar-group">
          <select class="input-field" id="sw-chart-group" aria-label="List">${groups.map(([k, label, n]) =>
            `<option value="${App.esc(k)}"${k === group[0] ? ' selected' : ''}>${App.esc(label)} (${n.length})</option>`).join('')}</select>
          <input class="input-field" id="sw-chart-find" list="sw-chart-names" placeholder="Go to a symbol" aria-label="Go to a symbol" />
          <datalist id="sw-chart-names">${[...known].sort().map((s) => `<option value="${App.esc(s)}"></option>`).join('')}</datalist>
        </div>
        <div class="top-bar-group date-nav">
          <button class="btn-icon-box" id="sw-chart-prev" title="Previous" aria-label="Previous"${at > 0 ? '' : ' disabled'}><i class="fa-solid fa-chevron-left"></i></button>
          <span class="muted">${names.length ? `${at + 1} of ${names.length}` : 'Empty list'}</span>
          <button class="btn-icon-box" id="sw-chart-next" title="Next" aria-label="Next"${at >= 0 && at < names.length - 1 ? '' : ' disabled'}><i class="fa-solid fa-chevron-right"></i></button>
        </div>
      </div>
      <div id="sw-chart-one"></div>`;
    el.querySelector('#sw-chart-group').addEventListener('change', (e) => {
      list.chartGroup = e.target.value;
      const first = groups.find((g) => g[0] === e.target.value)?.[2][0];
      if (first) go(first); else App.navigate('#/swing/charts');
    });
    el.querySelector('#sw-chart-find').addEventListener('change', (e) => { const s = e.target.value.trim().toUpperCase(); if (known.has(s)) go(s); });
    el.querySelector('#sw-chart-prev').addEventListener('click', () => go(names[at - 1]));
    el.querySelector('#sw-chart-next').addEventListener('click', () => go(names[at + 1]));
    if (current) await renderStock(el.querySelector('#sw-chart-one'), current, { embedded: true });
    else el.querySelector('#sw-chart-one').innerHTML = '<p class="muted sw-pad">This list is empty.</p>';
  }

  const chartable = (hd) => (hd?.rows || []).filter((r) => r.chart).map((r) => r.symbol);

  // ── index page: its stocks, placed at the last price ──
  const MEMBER_COLS = [['Stock', 'symbol'], ['Industry', 'industry'], ['Price', 'price', 1], ['Since scan', 'change_pct', 1], ['Position', 'position'],
    ['In zone', 'zone'], ['Support', 'support_atr', 1], ['ATR away', 'support_atr', 1], ['Resistance', 'resistance_atr', 1], ['ATR away', 'resistance_atr', 1]];
  const NEAREST_FIRST = ['near support', 'near resistance', 'in a zone', 'mid-range', 'no zones'];

  function drawMembers() {
    const box = document.getElementById('sw-members');
    if (!box || !members) return;
    const count = (p) => members.rows.filter((r) => r.position === p).length;
    const pick = [['', `All (${members.rows.length})`], ...POSITIONS.map((p) => [p, `${p[0].toUpperCase()}${p.slice(1)} (${count(p)})`])];
    const rows = members.rows.filter((r) => !list.memberPosition || r.position === list.memberPosition);
    const ordered = list.memberSort ? sorted(rows, list.memberSort)
      : [...rows].sort((a, b) => NEAREST_FIRST.indexOf(a.position) - NEAREST_FIRST.indexOf(b.position)
        || Math.min(a.support_atr ?? 99, a.resistance_atr ?? 99) - Math.min(b.support_atr ?? 99, b.resistance_atr ?? 99));
    box.innerHTML = `
      <div class="top-bar">
        <div><div class="section-title">Stocks in this index</div>
          <div class="muted sw-small">${members.live ? `<span class="sw-sup">Kite last price</span> at ${App.timeOf(members.as_of)}, refreshing every 30 seconds`
            : `Close of ${App.prettyDate(members.candles_to)}. Log in to Kite for live prices`}</div></div>
        <div class="top-bar-group" role="group" aria-label="Position">${pick.map(([p, label]) =>
          `<button class="btn-secondary sw-member-btn${p === list.memberPosition ? ' sw-active' : ''}" data-position="${p}">${label}</button>`).join('')}</div>
      </div>
      ${rows.length ? `<div class="table-wrap"><table class="data-table clickable" id="sw-member-table"><thead>${header(MEMBER_COLS, list.memberSort)}</thead>
      <tbody>${ordered.map((r) => `<tr data-symbol="${App.esc(r.symbol)}">
        <td><a href="${link(r.symbol)}">${App.esc(r.symbol)}</a></td><td class="sw-left muted">${App.esc(r.industry)}</td>
        <td>${App.num(r.price)}</td><td>${change(r.change_pct)}</td><td class="sw-left">${position(r.position)}</td>
        <td class="sw-left">${range(r.zone)}</td><td>${range(r.support)}</td><td>${atr(r.support_atr)}</td><td>${range(r.resistance)}</td><td>${atr(r.resistance_atr)}</td>
      </tr>`).join('')}</tbody></table></div>` : '<p class="muted sw-pad">No stocks in this position.</p>'}`;
    box.querySelectorAll('.sw-member-btn').forEach((b) => b.addEventListener('click', () => { list.memberPosition = b.dataset.position; drawMembers(); }));
    const table = box.querySelector('#sw-member-table');
    if (!table) return;
    bindSort(table, 'memberSort', drawMembers);
    table.querySelectorAll('tbody tr').forEach((tr) => tr.addEventListener('click', (e) => {
      if (!e.target.closest('a')) location.hash = link(tr.dataset.symbol);
    }));
  }

  async function loadMembers(name, token) {
    let out = null;
    try { out = await App.api(`swing/index/${encodeURIComponent(name)}/stocks`); } catch { /* not in the scan yet */ }
    if (token !== renderId) return;
    const box = document.getElementById('sw-members');
    if (!out) { if (box) box.innerHTML = '<p class="muted sw-pad">This index is not in the last scan. Run <code>algo swing scan</code>.</p>'; return; }
    members = out;
    drawMembers();
    if (out.live && !memberTimer) memberTimer = setInterval(() => loadMembers(name, token), MEMBER_REFRESH_MS);
  }

  function stopTimers() { if (memberTimer) { clearInterval(memberTimer); memberTimer = null; } }

  // ── stock page ──
  // Lightweight Charts has no price bands, so this primitive paints the zones and the volume at
  // price behind the candles.
  class ZoneBands {
    constructor(zones, profile) { this.zones = zones; this.profile = profile; this.showProfile = true; }
    attached({ series, requestUpdate }) { this.series = series; this.requestUpdate = requestUpdate; }
    detached() { this.series = null; }
    updateAllViews() {}
    paneViews() { return [this]; }
    zOrder() { return 'bottom'; }
    renderer() { return this; }
    draw(target) {
      target.useBitmapCoordinateSpace(({ context: ctx, bitmapSize, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
        const y = (p) => { const c = this.series?.priceToCoordinate(p); return c === null || c === undefined ? null : c * vr; };
        if (this.showProfile && this.profile.length) {
          const top = Math.max(...this.profile.map((b) => b.volume)) || 1;
          ctx.fillStyle = 'rgba(100, 116, 139, 0.18)';
          for (const b of this.profile) {
            const y1 = y(b.hi), y2 = y(b.lo);
            if (y1 === null || y2 === null) continue;
            const w = b.volume / top * bitmapSize.width * 0.16;
            ctx.fillRect(bitmapSize.width - w, y1 + vr, w, Math.max(y2 - y1 - vr, vr));
          }
        }
        ctx.font = `${11 * vr}px 'Segoe UI', system-ui, sans-serif`;
        ctx.textBaseline = 'middle';
        for (const z of this.zones) {
          const y1 = y(z.hi), y2 = y(z.lo);
          if (y1 === null || y2 === null) continue;
          const rgb = ZONE_RGB[zoneKey(z)];
          ctx.fillStyle = `rgba(${rgb}, 0.13)`;
          ctx.fillRect(0, y1, bitmapSize.width, Math.max(y2 - y1, vr));
          ctx.fillStyle = `rgba(${rgb}, 0.95)`;
          ctx.fillText(`${App.num(z.lo)}–${App.num(z.hi)}`, 6 * hr, (y1 + y2) / 2);
        }
      });
    }
  }

  function removeChart() { if (chart) { chart.remove(); chart = null; } }

  function drawChart(s, state, bands, keepZoom = false) {
    const { view, pivots: showPivots } = state;
    const zoom = keepZoom && chart ? chart.timeScale().getVisibleLogicalRange() : null;
    removeChart();
    const box = document.getElementById('sw-chart');
    const orderLevels = state.showOrders ? state.levels : [];
    const keepInView = [...(s.held?.average ? [s.held.average] : []), ...orderLevels.map((l) => l.price)];
    chart = LightweightCharts.createChart(box, {
      autoSize: true,
      layout: { background: { color: '#ffffff' }, textColor: '#64748b', fontFamily: "'Segoe UI', system-ui, sans-serif", fontSize: 11 },
      grid: { vertLines: { color: '#f1f5f9' }, horzLines: { color: '#f1f5f9' } },
      rightPriceScale: { borderColor: '#e2e8f0' }, timeScale: { borderColor: '#e2e8f0' },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    });
    const candles = chart.addCandlestickSeries({ upColor: '#10b981', downColor: '#ef4444', borderVisible: false,
      wickUpColor: '#10b981', wickDownColor: '#ef4444',
      autoscaleInfoProvider: (base) => {                     // keep your average and orders on the chart
        const out = base();
        if (out?.priceRange && keepInView.length) {
          out.priceRange.minValue = Math.min(out.priceRange.minValue, ...keepInView);
          out.priceRange.maxValue = Math.max(out.priceRange.maxValue, ...keepInView);
        }
        return out;
      } });
    const rows = s[view];
    candles.setData(rows.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
    candles.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: s.is_index ? 0.05 : 0.2 } });
    if (!s.is_index) {
      const volume = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: '', lastValueVisible: false, priceLineVisible: false });
      volume.priceScale().applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
      volume.setData(rows.map((c) => ({ time: c.time, value: c.volume,
        color: c.close >= c.open ? 'rgba(16, 185, 129, 0.35)' : 'rgba(239, 68, 68, 0.35)' })));
    }
    candles.attachPrimitive(bands);
    if (s.held?.average) {
      candles.createPriceLine({ price: s.held.average, color: '#3b82f6', lineWidth: 2, axisLabelVisible: true, title: 'your average' });
    }
    for (const l of orderLevels) {
      candles.createPriceLine({ price: l.price, color: ORDER_COLOUR[l.kind], lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true, title: l.label });
    }
    if (showPivots) {
      const seen = new Set();
      const marks = [];
      for (const z of s.zones) {
        for (const p of z.pivots) {
          const time = view === 'daily' ? p.time : p.week;
          const id = `${time}${p.kind}`;
          if (!time || seen.has(id)) continue;
          seen.add(id);
          marks.push(p.kind === 'L' ? { time, position: 'belowBar', shape: 'arrowUp', color: '#059669', size: 0.6 }
            : { time, position: 'aboveBar', shape: 'arrowDown', color: '#dc2626', size: 0.6 });
        }
      }
      candles.setMarkers(marks.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0)));
    }
    const legend = document.getElementById('sw-legend');
    const show = (c) => {
      legend.textContent = c ? `${c.time}   O ${App.num(c.open)}   H ${App.num(c.high)}   L ${App.num(c.low)}   C ${App.num(c.close)}` : '';
    };
    const byTime = new Map(rows.map((c) => [c.time, c]));
    chart.subscribeCrosshairMove((p) => show(byTime.get(p.time) || rows[rows.length - 1]));
    show(rows[rows.length - 1]);
    if (zoom) chart.timeScale().setVisibleLogicalRange(zoom);
    else chart.timeScale().fitContent();
  }

  // Active GTT, ATO and alert levels on this symbol, then the switch that shows them.
  async function loadChartOrders(s, state, redraw, token) {
    const box = document.getElementById('sw-orders-toggle');
    let out = null;
    try { out = await getOrders(); } catch { /* the chart works without them */ }
    if (token !== renderId || !box) return;
    if (!out?.available) {
      box.innerHTML = '<span class="muted" title="Log in to Kite to see your orders here">Orders: log in</span>';
      return;
    }
    state.levels = out.rows.filter((r) => r.active && r.symbol === s.symbol)
      .flatMap((r) => r.levels.map((l) => ({ ...l, kind: r.kind })));
    state.showOrders = App.prefs.get('swing.chartOrders', true);
    box.innerHTML = `<label class="sw-check"><input type="checkbox" id="sw-orders"${state.showOrders ? ' checked' : ''}${
      state.levels.length ? '' : ' disabled'} />Orders and alerts (${state.levels.length})</label>`;
    box.querySelector('#sw-orders').addEventListener('change', (e) => {
      state.showOrders = e.target.checked;
      App.prefs.set('swing.chartOrders', state.showOrders);
      redraw(true);
    });
    if (state.showOrders && state.levels.length) redraw();   // no zoom to keep yet: the chart has just opened
  }

  async function renderStock(el, symbol, { embedded = false } = {}) {
    el.innerHTML = '<div class="section-card"><div class="skeleton" style="height:420px"></div></div>';
    const token = renderId;
    let s;
    try { s = await App.api(`swing/stock/${encodeURIComponent(symbol)}`); }
    catch {
      if (token !== renderId) return;
      el.innerHTML = App.emptyState('fa-layer-group', `No data for ${App.esc(symbol)}`,
        'It is not in the swing universe, or has no candles yet.', '<a class="btn-secondary" href="#/swing">Back to the list</a>');
      return;
    }
    if (token !== renderId) return;
    const facts = (s.is_index ? App.esc(s.kind === 'theme' ? 'Theme index' : 'Sector index')
      : `${App.esc(s.industry) || 'Outside the universe'} · ${s.indices.map(App.esc).join(', ') || 'no index'} · ₹${App.num(s.traded_cr, 0)} crore a day`)
      + (s.held ? ` · you hold ${s.held.quantity} at ₹${App.num(s.held.average)}` : '');
    el.innerHTML = `
      <div class="page-header">
        <div>${embedded ? '' : '<a class="sw-back" href="#/swing"><i class="fa-solid fa-arrow-left"></i>Swing review</a>'}
          <div class="page-title">${App.esc(s.symbol)}</div>
          <div class="page-subtitle">${facts}</div></div>
        <div class="sw-price"><div class="sw-close">${App.num(s.close)}</div>
          <div>${change(s.week_pct)} week · ${change(s.month_pct)} month · ATR ${App.num(s.atr)}</div>
          <div>${s.position ? position(s.position) : ''} · candles to ${App.prettyDate(s.candles_to)}</div></div>
      </div>
      <p class="sw-note"><i class="fa-solid fa-circle-info"></i>${App.esc(s.note)}</p>
      ${s.zones_note ? `<p class="sw-note">No zones: ${App.esc(s.zones_note)}.</p>` : ''}
      <div class="section-card">
        <div class="top-bar">
          <div class="top-bar-group" role="group" aria-label="Timeframe">
            <button class="btn-secondary sw-view sw-active" data-view="daily">Daily</button>
            <button class="btn-secondary sw-view" data-view="weekly">Weekly</button>
          </div>
          <div class="top-bar-group">
            <span id="sw-orders-toggle"></span>
            <label class="sw-check"><input type="checkbox" id="sw-pivots" checked />Pivots</label>
            ${s.profile.length ? '<label class="sw-check"><input type="checkbox" id="sw-profile" checked />Volume at price</label>' : ''}
            <span class="sw-key"><i class="sw-sw sw-bg-sup"></i>support<i class="sw-sw sw-bg-res"></i>resistance<i class="sw-sw sw-bg-flip"></i>flipped<i class="sw-sw sw-bg-in"></i>price inside</span>
          </div>
        </div>
        <div class="sw-legend" id="sw-legend"></div>
        <div id="sw-chart" class="sw-chart"></div>
      </div>
      ${s.is_index ? '<div class="section-card" id="sw-members"><div class="skeleton" style="height:120px"></div></div>' : ''}
      ${s.zones.length ? `<div class="section-card">
        <div class="section-title sw-gap">Zones</div>
        <div class="table-wrap"><table class="data-table"><thead><tr><th>Zone</th><th class="sw-left">Role</th><th>Held as support</th>
          <th>Held as resistance</th><th>Pivots</th><th>Last test</th><th class="sw-left">Last break</th><th class="sw-left">Also here</th></tr></thead>
        <tbody>${s.zones.map((z) => {
          const b = z.breaks[z.breaks.length - 1];
          return `<tr><td class="sw-z-${zoneKey(z)}">${App.num(z.lo)}–${App.num(z.hi)}</td><td class="sw-left">${App.esc(zoneName(z))}</td>
            <td>${z.support_touches}</td><td>${z.resistance_touches}</td><td>${z.pivots.length}</td><td>${z.last_test || '—'}</td>
            <td class="sw-left">${b ? `${b.day} ${b.way}` : '—'}</td><td class="sw-left">${App.esc(z.confluence.join(', ')) || '—'}</td></tr>`;
        }).join('')}</tbody></table></div></div>` : ''}`;
    const bands = new ZoneBands(s.zones, s.profile);
    const state = { view: 'daily', pivots: true, showOrders: false, levels: [] };
    const redraw = (keepZoom = false) => drawChart(s, state, bands, keepZoom);
    redraw();
    el.querySelectorAll('.sw-view').forEach((b) => b.addEventListener('click', () => {
      state.view = b.dataset.view;
      el.querySelectorAll('.sw-view').forEach((x) => x.classList.toggle('sw-active', x === b));
      redraw();
    }));
    el.querySelector('#sw-pivots').addEventListener('change', (e) => { state.pivots = e.target.checked; redraw(true); });
    el.querySelector('#sw-profile')?.addEventListener('change', (e) => { bands.showProfile = e.target.checked; bands.requestUpdate?.(); });
    loadChartOrders(s, state, redraw, token);
    if (s.is_index) loadMembers(s.symbol, token);
  }

  const TITLES = { holdings: 'Holdings · Swing', orders: 'Orders · Swing', charts: 'Charts · Swing' };
  App.page('swing', {
    title: (args) => TITLES[args[0]] || (args[0] ? `${args[0]} · Swing` : 'Swing review'),
    render: (el, args) => {
      renderId += 1;
      members = null;
      if (args[0] === 'holdings') return renderHoldings(el);
      if (args[0] === 'orders') return renderOrders(el);
      if (args[0] === 'charts') return renderCharts(el, args[1]);
      return args[0] ? renderStock(el, args[0]) : renderList(el);
    },
    destroy: () => { stopTimers(); removeChart(); },
  });
})();
