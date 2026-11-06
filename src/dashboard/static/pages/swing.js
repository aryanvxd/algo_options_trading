/* Swing review: #/swing (sectors, then their stocks), #/swing/holdings (your holdings against
   their zones) and #/swing/<SYMBOL> (chart and zones). Information for your own decisions: it makes
   no setups and sends nothing to Kite. The lists come from the last `algo swing scan`; a stock's
   zones are computed when its page opens. */

(() => {
  const POSITIONS = ['near support', 'near resistance', 'in a zone', 'mid-range'];
  const POS_CLASS = { 'near support': 'sw-sup', 'near resistance': 'sw-res', 'in a zone': 'sw-flip' };
  const ZONE_RGB = { support: '16, 185, 129', resistance: '239, 68, 68', flipped: '245, 158, 11', inside: '59, 130, 246' };

  // Kept between pages, so coming back from a chart keeps the filters and the sort.
  const list = { index: '', position: '', q: '', sectorSort: null, stockSort: null, heldSort: null };
  let chart = null;
  let data = null;
  let held = null;

  const tabs = (active) => `<nav class="tabs" aria-label="Swing sections">
    <a class="tab${active === 'list' ? ' active' : ''}" href="#/swing"><i class="fa-solid fa-layer-group"></i>Sectors and stocks</a>
    <a class="tab${active === 'holdings' ? ' active' : ''}" href="#/swing/holdings"><i class="fa-solid fa-briefcase"></i>Holdings</a>
  </nav>`;

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

  function drawChart(s, view, showPivots, bands) {
    removeChart();
    const box = document.getElementById('sw-chart');
    chart = LightweightCharts.createChart(box, {
      autoSize: true,
      layout: { background: { color: '#ffffff' }, textColor: '#64748b', fontFamily: "'Segoe UI', system-ui, sans-serif", fontSize: 11 },
      grid: { vertLines: { color: '#f1f5f9' }, horzLines: { color: '#f1f5f9' } },
      rightPriceScale: { borderColor: '#e2e8f0' }, timeScale: { borderColor: '#e2e8f0' },
      crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    });
    const candles = chart.addCandlestickSeries({ upColor: '#10b981', downColor: '#ef4444', borderVisible: false,
      wickUpColor: '#10b981', wickDownColor: '#ef4444',
      autoscaleInfoProvider: (base) => {                     // keep your average on the chart, however far away
        const out = base();
        if (out?.priceRange && s.held?.average) {
          out.priceRange.minValue = Math.min(out.priceRange.minValue, s.held.average);
          out.priceRange.maxValue = Math.max(out.priceRange.maxValue, s.held.average);
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
    chart.timeScale().fitContent();
  }

  async function renderStock(el, symbol) {
    el.innerHTML = '<div class="section-card"><div class="skeleton" style="height:420px"></div></div>';
    let s;
    try { s = await App.api(`swing/stock/${encodeURIComponent(symbol)}`); }
    catch {
      el.innerHTML = App.emptyState('fa-layer-group', `No data for ${App.esc(symbol)}`,
        'It is not in the swing universe, or has no candles yet.', '<a class="btn-secondary" href="#/swing">Back to the list</a>');
      return;
    }
    const facts = (s.is_index ? App.esc(s.kind === 'theme' ? 'Theme index' : 'Sector index')
      : `${App.esc(s.industry) || 'Outside the universe'} · ${s.indices.map(App.esc).join(', ') || 'no index'} · ₹${App.num(s.traded_cr, 0)} crore a day`)
      + (s.held ? ` · you hold ${s.held.quantity} at ₹${App.num(s.held.average)}` : '');
    el.innerHTML = `
      <div class="page-header">
        <div><a class="sw-back" href="#/swing"><i class="fa-solid fa-arrow-left"></i>Swing review</a>
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
            <label class="sw-check"><input type="checkbox" id="sw-pivots" checked />Pivots</label>
            ${s.profile.length ? '<label class="sw-check"><input type="checkbox" id="sw-profile" checked />Volume at price</label>' : ''}
            <span class="sw-key"><i class="sw-sw sw-bg-sup"></i>support<i class="sw-sw sw-bg-res"></i>resistance<i class="sw-sw sw-bg-flip"></i>flipped<i class="sw-sw sw-bg-in"></i>price inside</span>
          </div>
        </div>
        <div class="sw-legend" id="sw-legend"></div>
        <div id="sw-chart" class="sw-chart"></div>
      </div>
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
    const state = { view: 'daily', pivots: true };
    const redraw = () => drawChart(s, state.view, state.pivots, bands);
    redraw();
    el.querySelectorAll('.sw-view').forEach((b) => b.addEventListener('click', () => {
      state.view = b.dataset.view;
      el.querySelectorAll('.sw-view').forEach((x) => x.classList.toggle('sw-active', x === b));
      redraw();
    }));
    el.querySelector('#sw-pivots').addEventListener('change', (e) => { state.pivots = e.target.checked; redraw(); });
    el.querySelector('#sw-profile')?.addEventListener('change', (e) => { bands.showProfile = e.target.checked; bands.requestUpdate?.(); });
  }

  App.page('swing', {
    title: (args) => (args[0] === 'holdings' ? 'Holdings · Swing' : args[0] ? `${args[0]} · Swing` : 'Swing review'),
    render: (el, args) => (args[0] === 'holdings' ? renderHoldings(el) : args[0] ? renderStock(el, args[0]) : renderList(el)),
    destroy: removeChart,
  });
})();
