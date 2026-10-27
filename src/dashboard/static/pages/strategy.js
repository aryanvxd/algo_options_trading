/* Strategy pages: #/strategy/<algo_id>/<tab>. One generic page with a tab bar (Today, Trade log,
   Journal, Performance, Rules); a strategy can add panels with App.strategyPanels(algoId, {...})
   from pages/strategies/<algo_id>.js. */

(() => {
  const REFRESH_MS = 5000;
  const TABS = [
    ['today', 'Today', 'fa-calendar-day'],
    ['trades', 'Trade log', 'fa-table-list'],
    ['journal', 'Journal', 'fa-book'],
    ['performance', 'Performance', 'fa-chart-line'],
    ['rules', 'Rules', 'fa-sliders'],
  ];
  const STATUS = {
    STARTED: ['Running', 'tone-amber', true], IDLE: ['Starting', 'tone-amber', true], READY: ['Ready', 'tone-amber', true],
    ENTRY_PENDING: ['Watching for entry', 'tone-amber', true], IN_TRADE: ['In trade', 'tone-green', true],
    EXITING: ['Exiting', 'tone-red', true], DONE: ['Done', 'tone-blue', false],
    SKIPPED: ['Skipped', 'tone-slate', false], ERROR: ['Error', 'tone-red', false],
  };
  const CHECK_LABELS = {
    trade_day: 'Trade day', entry_vix: 'India VIX', premium_balance: 'CE/PE premium balance', pcr: 'PCR (log only)',
    vix: 'India VIX (log only)', pair: 'Pair selected', signal: 'Entry signal',
  };
  const panels = {};      // algo_id -> { today(day) -> html, afterToday(day), performanceNote(perf) -> html }
  App.strategyPanels = (algoId, def) => { panels[algoId] = def; };

  let st = null;          // page state
  let timers = [];

  const today = () => App.meta?.today || App.isoDate(new Date());
  const info = (id) => (App.meta?.strategies || []).find((a) => a.algo_id === id);
  const params = () => ({ mode: st.mode, source: st.source });
  const isLiveView = () => st.date === today() && st.source === 'live';

  function clearTimers() { timers.forEach(clearInterval); timers = []; }

  // ── page shell ───────────────────────────────────────────────────────────

  function render(el, args) {
    clearTimers();
    const algoId = args[0];
    const a = info(algoId);
    if (!a) {
      el.innerHTML = App.emptyState('fa-circle-question', 'Unknown strategy',
        `No strategy called <code>${App.esc(algoId || '')}</code>.`);
      return;
    }
    const tab = TABS.some((t) => t[0] === args[1]) ? args[1] : 'today';
    st = {
      algoId, info: a, tab,
      date: st && st.algoId === algoId ? st.date : today(),
      mode: App.prefs.get(`strategy.${algoId}.mode`, 'paper'),
      source: App.prefs.get(`strategy.${algoId}.source`, 'live'),
    };
    const facts = [a.mode === 'live' ? App.chip('Live', 'tone-red') : App.chip('Paper', 'tone-violet'),
      App.chip(`v${App.esc(a.version || '?')}`, 'tone-slate'),
      App.chip(`${a.lots} lot${a.lots === 1 ? '' : 's'}`, 'tone-slate'),
      a.max_trades > 1 ? App.chip(`up to ${a.max_trades} trades`, 'tone-slate') : '',
      App.chip(a.capital ? `capital ${App.money(a.capital, { sign: false })}` : 'capital not set', a.capital ? 'tone-slate' : 'tone-amber'),
    ].join('');
    el.innerHTML = `
      <div class="page-header">
        <div>
          <div class="page-title"><i class="fa-solid fa-circle" style="color:${App.colorFor(algoId)};font-size:0.8rem"></i>${App.esc(a.name)}</div>
          <div class="page-subtitle">${App.esc(a.description || '')}</div>
          <div class="chips chips-left" style="margin-top:8px">${facts}</div>
        </div>
        <div class="header-meta">
          <span id="sp-live" class="chip live tone-green hidden"><span class="chip-dot"></span>Live</span>
          <span id="sp-updated"></span>
          <button class="btn-icon-box" id="sp-refresh" title="Refresh" aria-label="Refresh"><i class="fa-solid fa-rotate"></i></button>
        </div>
      </div>
      <nav class="tabs" aria-label="Strategy sections">
        ${TABS.map(([k, label, icon]) => `<a class="tab${k === tab ? ' active' : ''}" href="#/strategy/${App.esc(algoId)}/${k}">
          <i class="fa-solid ${icon}"></i>${label}</a>`).join('')}
      </nav>
      <div class="top-bar">
        <div class="top-bar-group">
          <div class="segmented" id="sp-mode" role="group" aria-label="Trading mode">
            <button data-v="paper"><i class="fa-solid fa-file-lines"></i>Paper</button>
            <button data-v="live"><i class="fa-solid fa-bolt"></i>Live</button>
            <button data-v="all">All</button>
          </div>
          <div class="segmented" id="sp-source" role="group" aria-label="Data source">
            <button data-v="live" title="Real trading sessions">Sessions</button>
            <button data-v="replay" title="Offline replays of recorded days">Replays</button>
          </div>
        </div>
        <div class="top-bar-group" id="sp-controls"></div>
      </div>
      <div id="sp-body"></div>`;
    bindSegmented('sp-mode', 'mode');
    bindSegmented('sp-source', 'source');
    document.getElementById('sp-refresh').addEventListener('click', () => load(true));
    load(true);
  }

  function bindSegmented(id, key) {
    const box = document.getElementById(id);
    const paint = () => box.querySelectorAll('button').forEach((b) => b.classList.toggle('active', b.dataset.v === st[key]));
    box.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b || b.dataset.v === st[key]) return;
      st[key] = b.dataset.v;
      App.prefs.set(`strategy.${st.algoId}.${key}`, st[key]);
      paint();
      load(true);
    });
    paint();
  }

  async function load(full) {
    const body = document.getElementById('sp-body');
    if (!body) return;
    const fn = { today: loadToday, trades: loadTrades, journal: loadJournal, performance: loadPerformance, rules: loadRules }[st.tab];
    if (full) {
      clearTimers();
      body.innerHTML = '<div class="section-card"><div class="skeleton" style="height:120px"></div></div>';
    }
    try {
      await fn(body, full);
      const up = document.getElementById('sp-updated');
      if (up) up.textContent = 'Updated ' + new Date().toLocaleTimeString('en-IN', { hour12: false });
    } catch (err) {
      console.error(err);
      if (full) body.innerHTML = App.emptyState('fa-plug-circle-xmark', 'Could not load data', App.esc(err.message));
    }
  }

  function setControls(html, bind) {
    const box = document.getElementById('sp-controls');
    if (box) { box.innerHTML = html; bind?.(box); }
  }

  function dateNav() {
    setControls(`<div class="date-nav">
        <button class="btn-icon-box" data-step="-1" title="Previous day" aria-label="Previous day"><i class="fa-solid fa-chevron-left"></i></button>
        <input type="date" class="input-field" id="sp-date" aria-label="Date" value="${st.date}" />
        <button class="btn-icon-box" data-step="1" title="Next day" aria-label="Next day"><i class="fa-solid fa-chevron-right"></i></button>
        <button class="btn-secondary" id="sp-today">Today</button></div>`, (box) => {
      box.querySelectorAll('[data-step]').forEach((b) => b.addEventListener('click', () => {
        st.date = App.shiftDate(st.date, Number(b.dataset.step)); load(true);
      }));
      box.querySelector('#sp-date').addEventListener('change', (e) => { if (e.target.value) { st.date = e.target.value; load(true); } });
      box.querySelector('#sp-today').addEventListener('click', () => { st.date = today(); load(true); });
    });
  }

  function rangeControls(key, defDays) {
    const end = st[key + 'End'] || today();
    const start = st[key + 'Start'] || App.shiftDate(end, -(defDays - 1));
    st[key + 'Start'] = start; st[key + 'End'] = end;
    return { start, end };
  }

  function rangeNav(key, extra = '') {
    setControls(`<div class="date-nav"><label class="field-label" for="sp-from">From</label>
        <input type="date" class="input-field" id="sp-from" value="${st[key + 'Start']}" />
        <label class="field-label" for="sp-to">to</label>
        <input type="date" class="input-field" id="sp-to" value="${st[key + 'End']}" />${extra}</div>`, (box) => {
      box.querySelector('#sp-from').addEventListener('change', (e) => { if (e.target.value) { st[key + 'Start'] = e.target.value; load(true); } });
      box.querySelector('#sp-to').addEventListener('change', (e) => { if (e.target.value) { st[key + 'End'] = e.target.value; load(true); } });
    });
  }

  function statusOf(day) {
    const live = day.live?.status;
    let key = day.run?.status;
    if (day.run && ['STARTED', 'IN_TRADE'].includes(key) && live) key = live;
    return STATUS[key] || [key || 'No run', 'tone-slate', false];
  }

  // ── Today ────────────────────────────────────────────────────────────────

  async function loadToday(body, full) {
    dateNav();
    const day = await App.api(`strategies/${st.algoId}/day`, { date: st.date, ...params() });
    const payoffs = await Promise.all(day.trades.map((t) =>
      App.api(`positions/${t.position_id}/payoff`).catch(() => null)));
    App.destroyCharts();
    if (!day.run) {
      body.innerHTML = App.emptyState('fa-moon', `${App.esc(st.info.name)} did not run on ${App.prettyDate(st.date)}`,
        st.source === 'replay'
          ? `Replay a recorded day with <code>algo run --algo ${App.esc(st.algoId)} --replay ${st.date}</code>`
          : `Start it with <code>algo run --algo ${App.esc(st.algoId)}</code>`) + notesSection(day.notes, st.date);
      bindNotes(body, st.date);
      scheduleRefresh(false);
      return;
    }
    const s = statusOf(day);
    const running = s[2] && isLiveView();
    const traded = day.trades;
    const net = traded.reduce((a, t) => a + (t.net ?? 0), 0);
    const gross = traded.reduce((a, t) => a + (t.gross ?? 0), 0);
    const charges = traded.reduce((a, t) => a + (t.charges ?? 0), 0);
    const capital = traded.length ? Math.max(...traded.map((t) => t.capital || 0)) : null;
    const roi = capital ? (net / capital) * 100 : null;
    const maxTrades = day.run.max_trades || 1;
    const lastEvent = [...day.events].reverse().find((e) => !['RESUME'].includes(e.type));
    const banner = day.run.skip_reason ? `<i class="fa-solid fa-circle-info"></i> Skipped: ${App.esc(day.run.skip_reason)}`
      : lastEvent ? `<i class="fa-solid fa-clock-rotate-left"></i> ${App.timeOf(lastEvent.time)} · ${App.esc(lastEvent.message)}` : '';
    const extra = panels[st.algoId]?.today?.(day) || '';

    body.innerHTML = `
      <div class="status-line">${App.chip(s[0], s[1], running)}
        <span class="muted">Run #${day.run.run_id} · v${App.esc(day.run.version || '?')} · ${day.run.lots ?? '?'} lot(s)
        ${day.live?.spot ? ` · NIFTY ${App.num(day.live.spot, 1)}` : ''}${day.live?.vix ? ` · VIX ${App.num(day.live.vix, 2)}` : ''}
        ${day.live && !day.live.feed_ok && running ? ' · <span class="neg">feed stale</span>' : ''}</span></div>
      ${banner ? `<div class="banner">${banner}</div>` : ''}
      <div class="kpi-strip">
        ${App.kpi('Net P&amp;L', traded.length ? App.money(net) : '—', App.tone(net), 'fa-indian-rupee-sign', 'tone-green',
          traded.some((t) => t.open) ? 'open trade marked to market' : 'after charges')}
        ${App.kpi('Gross P&amp;L', traded.length ? App.money(gross) : '—', App.tone(gross), 'fa-chart-line', 'tone-blue', 'before charges')}
        ${App.kpi('Charges', traded.length ? App.money(charges, { sign: false }) : '—', '', 'fa-receipt', 'tone-amber', 'brokerage, STT, GST, fees')}
        ${App.kpi('Capital used', capital ? App.money(capital, { sign: false }) : '—', '', 'fa-vault', 'tone-violet',
          traded[0]?.capital_source ? capitalLabel(traded[0].capital_source) : 'margin or premium')}
        ${App.kpi('Return on capital', App.pct(roi), App.tone(roi), 'fa-percent', 'tone-green', 'net ÷ capital used')}
        ${App.kpi('Trades', `${traded.length} / ${maxTrades}`, '', 'fa-right-left', 'tone-slate',
          day.summary?.max_profit !== null && day.summary?.max_profit !== undefined
            ? `day best ${App.money(day.summary.max_profit)} · worst ${App.money(day.summary.max_drawdown)}` : 'today')}
      </div>
      ${extra}
      ${traded.map((t, i) => tradeCard(t, i, payoffs[i])).join('') || `<div class="section-card muted-card" style="margin-bottom:16px">
        <i class="fa-solid fa-hourglass-half"></i> No trade ${running ? 'yet' : 'on this day'}.</div>`}
      <div class="row row-2-1">
        <div class="section-card">${checksTable(day.checks)}</div>
        <div class="section-card">${eventsList(day.events)}</div>
      </div>
      ${notesSection(day.notes, st.date)}`;
    traded.forEach((t, i) => { drawPayoff(`pay-${t.position_id}`, payoffs[i], full); drawCurve(`curve-${t.position_id}`, t, payoffs[i], full); });
    panels[st.algoId]?.afterToday?.(day, full);
    bindNotes(body, st.date);
    scheduleRefresh(running);
  }

  function scheduleRefresh(on) {
    clearTimers();
    document.getElementById('sp-live')?.classList.toggle('hidden', !on);
    if (on) timers.push(setInterval(() => load(false), REFRESH_MS));
  }

  const capitalLabel = (src) => ({ kite_margin: 'Kite basket margin', estimate: 'estimate: worst loss at expiry',
    premium: 'premium paid + entry charges' }[src] || src);

  function tradeCard(t, i, pay) {
    const head = `Trade ${t.trade_no || i + 1} · ${App.timeOf(t.entry_time)} → ${t.exit_time ? App.timeOf(t.exit_time) : 'open'}`;
    const legs = t.legs.map((l) => `<tr><td>${App.esc(l.symbol)}</td><td>${l.side === 'BUY' ? App.chip('Buy', 'tone-green') : App.chip('Sell', 'tone-red')}</td>
      <td>${l.qty}</td><td>${App.num(l.entry)}</td><td>${l.exit !== null ? App.num(l.exit) : '—'}</td>
      <td class="${App.tone(l.pnl)}">${App.money(l.pnl)}</td></tr>`).join('');
    const credit = t.entry_credit;
    return `<div class="section-card trade-card" style="margin-bottom:16px">
      <div class="card-head">
        <div><div class="section-title">${head}</div>
          <div class="section-subtitle">${App.esc(t.strikes)} · ${credit !== null ? (credit >= 0 ? `credit ${App.num(credit)} pts` : `cost ${App.num(-credit)} pts`) : ''}
            ${t.dte !== null && t.dte !== undefined ? ` · DTE ${t.dte}` : ''}${t.driver ? ` · driven by ${t.driver}` : ''}</div></div>
        <div class="chips">${t.open ? App.chip('Open', 'tone-green', isLiveView()) : App.chip(App.reason(t.exit_reason), exitTone(t))}
          <button class="btn-secondary btn-sm" data-detail="${t.position_id}"><i class="fa-solid fa-magnifying-glass"></i>Details</button></div>
      </div>
      <div class="trade-facts">
        ${fact('Net', App.money(t.net), App.tone(t.net))}${fact('Gross', App.money(t.gross), App.tone(t.gross))}
        ${fact('Charges', App.money(t.charges, { sign: false }))}${fact('Capital', App.money(t.capital, { sign: false }))}
        ${fact('ROI', App.pct(t.roi_pct), App.tone(t.roi_pct))}${fact('Held', App.duration(t.hold_s))}
      </div>
      <div class="row row-1-1" style="margin:14px 0 0">
        <div><div class="mini-title">Payoff</div><div class="chart-box chart-sm"><canvas id="pay-${t.position_id}"></canvas></div>
          ${payoffFoot(pay)}</div>
        <div><div class="mini-title">P&amp;L during the trade</div><div class="chart-box chart-sm"><canvas id="curve-${t.position_id}"></canvas></div></div>
      </div>
      <div class="table-wrap" style="margin-top:12px"><table class="data-table">
        <thead><tr><th>Leg</th><th>Side</th><th>Qty</th><th>Entry</th><th>Exit</th><th>P&amp;L</th></tr></thead>
        <tbody>${legs}</tbody></table></div>
    </div>`;
  }

  const fact = (label, value, cls = '') => `<div><div class="fact-label">${label}</div><div class="fact-value ${cls}">${value}</div></div>`;
  const exitTone = (t) => (t.net > 0 ? 'tone-green' : t.net < 0 ? 'tone-red' : 'tone-slate');

  function payoffFoot(pay) {
    if (!pay || !pay.spots?.length) return '';
    const be = pay.breakevens.length ? pay.breakevens.map((b) => App.num(b, 0)).join(' / ') : '—';
    return `<div class="chart-foot">Breakeven ${be} · max loss ${pay.max_loss !== null ? App.money(-pay.max_loss) : 'unlimited'}
      · max profit ${App.money(pay.max_profit)}${pay.now ? ` · dashed: value ${pay.open ? 'now' : 'at entry'} (Black-76)` : ''}</div>`;
  }

  // ── charts ──

  function drawPayoff(id, pay, animate) {
    const canvas = document.getElementById(id);
    if (!canvas) return;
    if (!pay || !pay.spots?.length) { canvas.parentElement.innerHTML = '<div class="chart-empty">No payoff</div>'; return; }
    const xy = (ys) => pay.spots.map((x, i) => ({ x, y: ys[i] }));
    const lo = Math.min(...pay.expiry, ...(pay.now?.values || [])), hi = Math.max(...pay.expiry, ...(pay.now?.values || []));
    const vline = (x, color, label) => ({ type: 'line', label, data: [{ x, y: lo }, { x, y: hi }], borderColor: color,
      borderWidth: 1, borderDash: [3, 3], pointRadius: 0 });
    const hline = (y, color, label) => ({ type: 'line', label, data: [{ x: pay.spots[0], y }, { x: pay.spots[pay.spots.length - 1], y }],
      borderColor: color, borderWidth: 1, borderDash: [6, 4], pointRadius: 0 });
    const sets = [
      { type: 'line', label: 'At expiry', data: xy(pay.expiry), borderColor: '#0f172a', borderWidth: 2, pointRadius: 0,
        fill: { target: { value: 0 }, above: 'rgba(16,185,129,0.15)', below: 'rgba(239,68,68,0.12)' } },
    ];
    if (pay.now) sets.push({ type: 'line', label: pay.open ? 'Now' : 'At entry', data: xy(pay.now.values), borderColor: '#3b82f6',
      borderWidth: 1.5, borderDash: [5, 3], pointRadius: 0 });
    if (pay.spot_entry) sets.push(vline(pay.spot_entry, '#94a3b8', 'Spot at entry'));
    if (pay.spot_now) sets.push(vline(pay.spot_now, '#f59e0b', pay.open ? 'Spot now' : 'Spot at exit'));
    if (pay.stop !== null) sets.push(hline(pay.stop, '#ef4444', 'Stop'));
    if (pay.target !== null) sets.push(hline(pay.target, '#10b981', 'Target'));
    App.chart(canvas, {
      data: { datasets: sets },
      options: { responsive: true, maintainAspectRatio: false, animation: animate ? undefined : false, parsing: false,
        interaction: { mode: 'nearest', axis: 'x', intersect: false },
        scales: { x: { type: 'linear', grid: { display: false }, ticks: { maxTicksLimit: 7, callback: (v) => App.num(v, 0) } },
          y: { grid: zeroGrid(), ticks: { callback: (v) => App.money(v) } } },
        plugins: { legend: { display: true, position: 'bottom', labels: { boxWidth: 10, font: { size: 10 } } },
          tooltip: { callbacks: { title: (c) => `NIFTY ${App.num(c[0].parsed.x, 0)}`,
            label: (c) => `${c.dataset.label}: ${App.money(c.parsed.y)}` } } } },
    });
  }

  function drawCurve(id, t, pay, animate) {
    const canvas = document.getElementById(id);
    if (!canvas) return;
    if (!t.curve?.length) { canvas.parentElement.innerHTML = '<div class="chart-empty">No snapshots</div>'; return; }
    const labels = t.curve.map((p) => p[0]);
    const sets = [
      { label: 'Net', data: t.curve.map((p) => p[1]), borderColor: App.colorFor(st.algoId), borderWidth: 2, pointRadius: 0, tension: 0.15,
        fill: { target: { value: 0 }, above: 'rgba(16,185,129,0.12)', below: 'rgba(239,68,68,0.10)' } },
      { label: 'Gross', data: t.curve.map((p) => p[2]), borderColor: '#94a3b8', borderWidth: 1, pointRadius: 0, tension: 0.15 },
    ];
    if (pay?.stop !== null && pay?.stop !== undefined) sets.push({ label: 'Stop', data: labels.map(() => pay.stop), borderColor: '#ef4444', borderWidth: 1, borderDash: [6, 4], pointRadius: 0 });
    if (pay?.target !== null && pay?.target !== undefined) sets.push({ label: 'Target', data: labels.map(() => pay.target), borderColor: '#10b981', borderWidth: 1, borderDash: [6, 4], pointRadius: 0 });
    App.chart(canvas, {
      type: 'line', data: { labels, datasets: sets },
      options: { responsive: true, maintainAspectRatio: false, animation: animate ? undefined : false,
        interaction: { mode: 'index', intersect: false },
        scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 6 } }, y: { grid: zeroGrid(), ticks: { callback: (v) => App.money(v) } } },
        plugins: { legend: { display: true, position: 'bottom', labels: { boxWidth: 10, font: { size: 10 } } },
          tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${App.money(c.parsed.y)}` } } } },
    });
  }

  const zeroGrid = () => ({ color: (ctx) => (ctx.tick && ctx.tick.value === 0 ? '#cbd5e1' : '#f1f5f9') });

  // ── checks / events ──

  function detailText(d) {
    if (!d) return '';
    const flat = [];
    const walk = (o, p = '') => Object.entries(o).forEach(([k, v]) => {
      if (v === null || v === undefined || k === 'values') return;
      if (typeof v === 'object' && !Array.isArray(v)) walk(v, p + k + '.');
      else flat.push(`${p}${k}: ${Array.isArray(v) ? v.join(', ') : v}`);
    });
    walk(d);
    return flat.join(' · ');
  }

  function checksTable(checks) {
    if (!checks.length) return '<div class="section-title">Checks</div><div class="chart-empty" style="height:80px">No checks recorded</div>';
    const rows = checks.map((c) => `<tr>
      <td>${App.timeOf(c.time)}</td><td>${App.esc(CHECK_LABELS[c.name] || c.name)}</td>
      <td>${c.passed ? App.chip('Pass', 'tone-green') : App.chip('Fail', 'tone-red')}</td>
      <td>${c.value !== null ? App.num(c.value, 3).replace(/\.?0+$/, '') : '—'}</td>
      <td>${c.threshold !== null ? App.num(c.threshold, 3).replace(/\.?0+$/, '') : '—'}</td>
      <td class="detail-cell" title="${App.esc(detailText(c.detail))}">${App.esc(detailText(c.detail))}</td></tr>`).join('');
    return `<div class="card-head"><div><div class="section-title">Checks</div>
      <div class="section-subtitle">Every entry condition the algo tested, with its value</div></div></div>
      <div class="table-wrap scroll-y"><table class="data-table"><thead><tr><th>Time</th><th>Check</th><th>Result</th><th>Value</th><th>Limit</th><th>Detail</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
  }

  const EVENT_ICON = { ENTRY: 'fa-right-to-bracket tone-green', EXIT: 'fa-right-from-bracket tone-blue', EXIT_TRIGGER: 'fa-bell tone-amber',
    SKIP: 'fa-forward tone-slate', KILL_SWITCH: 'fa-power-off tone-red', ERROR: 'fa-triangle-exclamation tone-red',
    ORDER_REJECTED: 'fa-ban tone-red', REENTRY: 'fa-rotate tone-violet', ENTRIES_STOPPED: 'fa-hand tone-slate', RESUME: 'fa-play tone-amber' };

  function eventsList(events) {
    const items = [...events].reverse().map((e) => `<li><i class="fa-solid ${EVENT_ICON[e.type] || 'fa-circle-dot tone-slate'} ev-icon"></i>
      <div><div class="ev-time">${App.timeOf(e.time)} · ${App.esc(e.type)}</div><div class="ev-msg">${App.esc(e.message)}</div></div></li>`).join('');
    return `<div class="card-head"><div><div class="section-title">Timeline</div><div class="section-subtitle">Newest first</div></div></div>
      ${items ? `<ul class="timeline scroll-y">${items}</ul>` : '<div class="chart-empty" style="height:80px">No events</div>'}`;
  }

  // ── notes ────────────────────────────────────────────────────────────────

  function notesSection(notes, date, positionId = null) {
    const list = notes.filter((n) => positionId === null || n.position_id === positionId).map(noteItem).join('');
    return `<div class="section-card notes-card" data-date="${date}" data-position="${positionId ?? ''}" style="margin-top:16px">
      <div class="card-head"><div><div class="section-title"><i class="fa-solid fa-pen-to-square"></i> Your notes</div>
        <div class="section-subtitle">${positionId ? 'On this trade' : `On ${App.prettyDate(date)}`} — kept in the journal, never read by the algo</div></div></div>
      <div class="notes-list">${list || '<div class="muted small">No notes yet.</div>'}</div>
      <form class="note-form">
        <textarea class="input-field note-text" rows="2" maxlength="5000" placeholder="What happened, what you'd change…" aria-label="Note"></textarea>
        <div class="note-row"><input class="input-field note-tags" placeholder="tags, comma separated (e.g. news day, manual kill)" aria-label="Tags" />
          <button class="btn-primary" type="submit"><i class="fa-solid fa-plus"></i>Add note</button></div>
      </form></div>`;
  }

  function noteItem(n) {
    return `<div class="note" data-id="${n.id}">
      <div class="note-body">${App.esc(n.text).replace(/\n/g, '<br>')}</div>
      <div class="note-meta">${(n.tags || []).map((t) => `<span class="tag">${App.esc(t)}</span>`).join('')}
        <span>${n.updated_at ? App.timeOf(n.updated_at, false) + ' · ' + n.updated_at.slice(0, 10) : ''}</span>
        <button class="link-btn" data-edit="${n.id}">Edit</button><button class="link-btn danger" data-del="${n.id}">Delete</button></div></div>`;
  }

  function bindNotes(root, date, onChange) {
    const reload = onChange || (() => load(false));
    root.querySelectorAll('.notes-card').forEach((card) => {
      const d = card.dataset.date || date;
      const pid = card.dataset.position ? Number(card.dataset.position) : null;
      card.querySelector('.note-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const text = card.querySelector('.note-text').value.trim();
        if (!text) return;
        const tags = card.querySelector('.note-tags').value.split(',').map((t) => t.trim()).filter(Boolean);
        try {
          await App.api('notes', {}, { method: 'POST', body: { algo_id: st.algoId, trade_date: d, position_id: pid, text, tags } });
          App.toast('Note saved');
          reload();
        } catch (err) { App.toast('Could not save the note: ' + err.message, 'error'); }
      });
      card.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', async () => {
        if (!confirm('Delete this note?')) return;
        try { await App.api(`notes/${b.dataset.del}`, {}, { method: 'DELETE' }); reload(); }
        catch (err) { App.toast('Could not delete: ' + err.message, 'error'); }
      }));
      card.querySelectorAll('[data-edit]').forEach((b) => b.addEventListener('click', () => {
        const el = card.querySelector(`.note[data-id="${b.dataset.edit}"]`);
        const text = el.querySelector('.note-body').innerText;
        const tags = [...el.querySelectorAll('.tag')].map((t) => t.textContent).join(', ');
        el.innerHTML = `<form class="note-edit"><textarea class="input-field note-text" rows="3" aria-label="Edit note">${App.esc(text)}</textarea>
          <div class="note-row"><input class="input-field note-tags" value="${App.esc(tags)}" aria-label="Tags" />
          <button class="btn-secondary" type="button" data-cancel>Cancel</button><button class="btn-primary" type="submit">Save</button></div></form>`;
        el.querySelector('[data-cancel]').addEventListener('click', reload);
        el.querySelector('form').addEventListener('submit', async (e) => {
          e.preventDefault();
          const t = el.querySelector('.note-text').value.trim();
          if (!t) return;
          const tg = el.querySelector('.note-tags').value.split(',').map((x) => x.trim()).filter(Boolean);
          try { await App.api(`notes/${b.dataset.edit}`, {}, { method: 'PUT', body: { text: t, tags: tg } }); reload(); }
          catch (err) { App.toast('Could not save: ' + err.message, 'error'); }
        });
      }));
    });
    root.querySelectorAll('[data-detail]').forEach((b) => b.addEventListener('click', () => openDetail(Number(b.dataset.detail))));
  }

  // ── Trade log ────────────────────────────────────────────────────────────

  async function loadTrades(body) {
    const { start, end } = rangeControls('log', 90);
    rangeNav('log', `<button class="btn-secondary" id="sp-csv"><i class="fa-solid fa-file-csv"></i>CSV</button>`);
    const data = await App.api(`strategies/${st.algoId}/trades`, { start, end, ...params() });
    const all = data.trades;
    const reasons = [...new Set(all.map((t) => t.exit_reason).filter(Boolean))];
    const dtes = [...new Set(all.map((t) => t.dte).filter((d) => d !== null && d !== undefined))].sort((a, b) => a - b);
    st.logFilter = st.logFilter || { reason: '', result: '', dte: '' };
    const f = st.logFilter;
    const rows = all.filter((t) => (!f.reason || t.exit_reason === f.reason)
      && (!f.result || (f.result === 'win' ? t.net > 0 : t.net <= 0)) && (f.dte === '' || String(t.dte) === f.dte));
    document.getElementById('sp-csv').addEventListener('click', () => downloadCsv(rows));
    const sum = (k) => rows.reduce((a, t) => a + (t[k] ?? 0), 0);
    const wins = rows.filter((t) => t.net > 0).length;
    const sel = (id, label, opts, val) => `<label class="field-label">${label}
      <select class="input-field" id="${id}">${opts.map(([v, l]) => `<option value="${App.esc(v)}"${String(v) === String(val) ? ' selected' : ''}>${App.esc(l)}</option>`).join('')}</select></label>`;
    body.innerHTML = `
      <div class="filter-row">
        ${sel('f-reason', 'Exit', [['', 'All exits'], ...reasons.map((r) => [r, App.reason(r)])], f.reason)}
        ${sel('f-result', 'Result', [['', 'Wins and losses'], ['win', 'Wins'], ['loss', 'Losses']], f.result)}
        ${sel('f-dte', 'DTE', [['', 'All'], ...dtes.map((d) => [String(d), String(d)])], f.dte)}
        <span class="muted small">${rows.length} trade${rows.length === 1 ? '' : 's'} · ${rows.length ? Math.round((wins / rows.length) * 100) : 0}% won · net
          <b class="${App.tone(sum('net'))}">${App.money(sum('net'))}</b></span>
      </div>
      <div class="section-card">${rows.length ? `<div class="table-wrap"><table class="data-table clickable">
        <thead><tr><th>Date</th><th>#</th><th>Entry</th><th>Exit</th><th>Held</th><th>DTE</th><th>Strikes</th><th title="Points received per unit; negative = premium paid">Net credit</th>
          <th>Capital</th><th>Gross</th><th>Charges</th><th>Net</th><th>ROI</th><th>Exit reason</th><th></th></tr></thead>
        <tbody>${rows.map((t) => `<tr data-detail="${t.position_id}" tabindex="0">
          <td>${t.trade_date} <span class="muted">${t.weekday}</span></td><td>${t.trade_no ?? 1}</td>
          <td>${App.timeOf(t.entry_time)}</td><td>${App.timeOf(t.exit_time)}</td><td>${App.duration(t.hold_s)}</td>
          <td>${t.dte ?? '—'}</td><td class="mono">${App.esc(t.strikes)}</td><td>${App.num(t.entry_credit)}</td>
          <td title="${App.esc(capitalLabel(t.capital_source))}">${App.money(t.capital, { sign: false })}${t.capital_source === 'estimate' ? '<sup>e</sup>' : ''}</td>
          <td class="${App.tone(t.gross)}">${App.money(t.gross)}</td><td>${App.money(t.charges, { sign: false })}</td>
          <td class="${App.tone(t.net)}"><b>${App.money(t.net)}</b></td><td class="${App.tone(t.roi_pct)}">${App.pct(t.roi_pct)}</td>
          <td>${App.esc(App.reason(t.exit_reason))}</td><td>${t.notes ? '<i class="fa-solid fa-note-sticky" title="Has notes"></i>' : ''}</td></tr>`).join('')}</tbody>
        <tfoot><tr><td>Total</td><td colspan="8"></td><td class="${App.tone(sum('gross'))}">${App.money(sum('gross'))}</td>
          <td>${App.money(sum('charges'), { sign: false })}</td><td class="${App.tone(sum('net'))}">${App.money(sum('net'))}</td><td colspan="3"></td></tr></tfoot>
        </table></div><div class="chart-foot"><sup>e</sup> estimated capital (worst loss at expiry) where Kite's margin wasn't recorded</div>`
        : '<div class="chart-empty" style="height:120px">No trades in this range</div>'}</div>`;
    ['reason', 'result', 'dte'].forEach((k) => document.getElementById('f-' + k).addEventListener('change', (e) => { f[k] = e.target.value; load(false); }));
    body.querySelectorAll('tr[data-detail]').forEach((tr) => {
      tr.addEventListener('click', () => openDetail(Number(tr.dataset.detail)));
      tr.addEventListener('keydown', (e) => { if (e.key === 'Enter') openDetail(Number(tr.dataset.detail)); });
    });
  }

  function downloadCsv(rows) {
    const cols = ['trade_date', 'weekday', 'trade_no', 'entry_time', 'exit_time', 'hold_s', 'dte', 'strikes', 'entry_credit',
      'exit_debit', 'capital', 'capital_source', 'gross', 'charges', 'net', 'roi_pct', 'exit_reason', 'spot_entry', 'spot_exit', 'lots', 'mode', 'source'];
    const cell = (v) => (v === null || v === undefined ? '' : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
    const csv = [cols.join(','), ...rows.map((r) => cols.map((c) => cell(r[c])).join(','))].join('\r\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv' }));
    a.download = `${st.algoId}_trades_${st.logStart}_${st.logEnd}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  // ── trade detail (modal) ─────────────────────────────────────────────────

  async function openDetail(positionId) {
    closeDetail();
    const wrap = document.createElement('div');
    wrap.className = 'modal-backdrop';
    wrap.id = 'trade-modal';
    wrap.innerHTML = `<div class="modal" role="dialog" aria-modal="true" aria-label="Trade detail"><div class="modal-body">
      <div class="skeleton" style="height:200px"></div></div></div>`;
    document.body.appendChild(wrap);
    wrap.addEventListener('click', (e) => { if (e.target === wrap) closeDetail(); });
    document.addEventListener('keydown', escClose);
    try {
      const [d, pay] = await Promise.all([App.api(`positions/${positionId}`), App.api(`positions/${positionId}/payoff`).catch(() => null)]);
      const t = d.trade;
      const fills = d.fills.map((f) => `<tr><td>${App.timeOf(f.time)}</td><td>${f.purpose}</td><td>${App.esc(f.symbol)}</td><td>${f.side}</td>
        <td>${f.qty}</td><td>${f.status === 'COMPLETE' ? App.num(f.price) : App.chip(f.status, 'tone-red')}</td>
        <td>${App.num(f.bid)} / ${App.num(f.ask)}</td><td>${App.num(f.slippage)}</td><td>${App.money(f.charges, { sign: false, decimals: 2 })}</td></tr>`).join('');
      const modal = wrap.querySelector('.modal-body');
      modal.innerHTML = `
        <div class="card-head"><div><div class="section-title">${App.esc(st.info.name)} · ${t.trade_date} · trade ${t.trade_no ?? 1}</div>
          <div class="section-subtitle">${App.timeOf(t.entry_time)} → ${App.timeOf(t.exit_time)} (${App.duration(t.hold_s)}) · ${App.esc(t.strikes)}
            · ${App.esc(App.reason(t.exit_reason))}</div></div>
          <button class="btn-icon-box" id="modal-close" aria-label="Close"><i class="fa-solid fa-xmark"></i></button></div>
        <div class="trade-facts">
          ${fact('Net', App.money(t.net), App.tone(t.net))}${fact('Gross', App.money(t.gross), App.tone(t.gross))}
          ${fact('Charges', App.money(t.charges, { sign: false, decimals: 2 }))}
          ${fact('Capital', `${App.money(t.capital, { sign: false })}`)}${fact('ROI', App.pct(t.roi_pct, 3), App.tone(t.roi_pct))}
          ${fact('Spot', `${App.num(t.spot_entry, 1)} → ${App.num(t.spot_exit, 1)}`)}
        </div>
        <div class="chart-foot">Capital: ${App.esc(capitalLabel(t.capital_source))}</div>
        <div class="row row-1-1" style="margin-top:12px">
          <div><div class="mini-title">Payoff</div><div class="chart-box chart-sm"><canvas id="m-pay"></canvas></div>${payoffFoot(pay)}</div>
          <div><div class="mini-title">P&amp;L during the trade</div><div class="chart-box chart-sm"><canvas id="m-curve"></canvas></div></div>
        </div>
        <div class="mini-title" style="margin-top:14px">Orders and fills</div>
        <div class="table-wrap"><table class="data-table"><thead><tr><th>Time</th><th>Purpose</th><th>Symbol</th><th>Side</th><th>Qty</th>
          <th>Price</th><th>Bid / ask</th><th>Slippage</th><th>Charges</th></tr></thead><tbody>${fills}</tbody></table></div>
        <div class="row row-1-1" style="margin-top:14px">
          <div>${checksTable(d.checks)}</div><div>${eventsList(d.events)}</div>
        </div>
        ${notesSection(d.notes, t.trade_date, t.position_id)}`;
      modal.querySelector('#modal-close').addEventListener('click', closeDetail);
      drawPayoff('m-pay', pay, true);
      drawCurve('m-curve', { curve: d.curve }, pay, true);
      bindNotes(modal, t.trade_date, () => openDetail(positionId));
    } catch (err) {
      wrap.querySelector('.modal-body').innerHTML = App.emptyState('fa-plug-circle-xmark', 'Could not load the trade', App.esc(err.message));
    }
  }

  function escClose(e) { if (e.key === 'Escape') closeDetail(); }
  function closeDetail() {
    document.getElementById('trade-modal')?.remove();
    document.removeEventListener('keydown', escClose);
  }

  // ── Journal ──────────────────────────────────────────────────────────────

  async function loadJournal(body) {
    const { start, end } = rangeControls('journal', 30);
    rangeNav('journal');
    const data = await App.api(`strategies/${st.algoId}/journal`, { start, end, ...params() });
    if (!data.days.length) {
      body.innerHTML = App.emptyState('fa-book-open', 'Nothing in this range', 'No runs or notes between these dates.')
        + notesSection([], today());
      bindNotes(body, today());
      return;
    }
    body.innerHTML = data.days.map(journalDay).join('');
    bindNotes(body, null);
  }

  function journalDay(d) {
    const s = STATUS[d.status] || [d.status || 'Not run', 'tone-slate', false];
    const groups = {};
    d.checks.forEach((c) => { (groups[c.name] = groups[c.name] || []).push(c); });
    const checkSummary = Object.entries(groups).map(([name, cs]) => {
      const passed = cs.filter((c) => c.passed).length;
      const last = cs[cs.length - 1];
      return `<li>${passed === cs.length ? '<i class="fa-solid fa-check pos"></i>' : passed ? '<i class="fa-solid fa-circle-half-stroke" style="color:var(--warn)"></i>' : '<i class="fa-solid fa-xmark neg"></i>'}
        <b>${App.esc(CHECK_LABELS[name] || name)}</b> ${cs.length > 1 ? `<span class="muted">×${cs.length} (${passed} passed)</span>` : ''}
        <span class="muted">last ${last.value !== null ? App.num(last.value, 3).replace(/\.?0+$/, '') : '—'}${last.threshold !== null ? ` vs ${App.num(last.threshold, 3).replace(/\.?0+$/, '')}` : ''}</span></li>`;
    }).join('');
    const trades = d.trades.map((t) => `<tr data-detail="${t.position_id}" tabindex="0"><td>${t.trade_no ?? 1}</td><td>${App.timeOf(t.entry_time)}</td>
      <td>${App.timeOf(t.exit_time)}</td><td class="mono">${App.esc(t.strikes)}</td><td>${App.esc(App.reason(t.exit_reason))}</td>
      <td class="${App.tone(t.net)}">${App.money(t.net)}</td><td class="${App.tone(t.roi_pct)}">${App.pct(t.roi_pct)}</td></tr>`).join('');
    const important = d.events.filter((e) => ['ENTRY', 'EXIT', 'EXIT_TRIGGER', 'SKIP', 'KILL_SWITCH', 'ERROR', 'ENTRIES_STOPPED', 'ENTRY_FAILED'].includes(e.type));
    return `<div class="section-card journal-day">
      <div class="card-head"><div><div class="section-title">${App.prettyDate(d.date)}</div>
        <div class="section-subtitle">${d.run_id ? `Run #${d.run_id}` : 'No run'}${d.summary_notes ? ' · ' + App.esc(d.summary_notes) : ''}</div></div>
        <div class="chips">${d.status ? App.chip(s[0], s[1]) : ''}${d.net !== null ? `<span class="journal-net ${App.tone(d.net)}">${App.money(d.net)}</span>` : ''}</div></div>
      ${d.skip_reason ? `<div class="banner"><i class="fa-solid fa-circle-info"></i> ${App.esc(d.skip_reason)}</div>` : ''}
      <div class="row row-1-1">
        <div><div class="mini-title">Decisions</div>${checkSummary ? `<ul class="check-list">${checkSummary}</ul>` : '<div class="muted small">No checks</div>'}</div>
        <div><div class="mini-title">What happened</div>${important.length ? `<ul class="timeline">${important.map((e) => `<li>
          <i class="fa-solid ${EVENT_ICON[e.type] || 'fa-circle-dot tone-slate'} ev-icon"></i><div><div class="ev-time">${App.timeOf(e.time)}</div>
          <div class="ev-msg">${App.esc(e.message)}</div></div></li>`).join('')}</ul>` : '<div class="muted small">No events</div>'}</div>
      </div>
      ${trades ? `<div class="table-wrap" style="margin-top:12px"><table class="data-table clickable"><thead><tr><th>#</th><th>Entry</th><th>Exit</th>
        <th>Strikes</th><th>Exit reason</th><th>Net</th><th>ROI</th></tr></thead><tbody>${trades}</tbody></table></div>` : ''}
      ${notesSection(d.notes, d.date).replace('style="margin-top:16px"', 'style="margin-top:12px;box-shadow:none;background:#fafbfc"')}
    </div>`;
  }

  // ── Performance ──────────────────────────────────────────────────────────

  async function loadPerformance(body, full) {
    const opts = [[30, '30 days'], [90, '90 days'], [365, '1 year'], [3650, 'All']];
    st.perfDays = st.perfDays || 365;
    setControls(`<div class="segmented" id="perf-range">${opts.map(([d, l]) => `<button data-v="${d}" class="${d === st.perfDays ? 'active' : ''}">${l}</button>`).join('')}</div>`,
      (box) => box.querySelector('#perf-range').addEventListener('click', (e) => {
        const b = e.target.closest('button'); if (!b) return; st.perfDays = Number(b.dataset.v); load(true);
      }));
    const p = await App.api(`strategies/${st.algoId}/performance`, { days: st.perfDays, ...params() });
    App.destroyCharts();
    const s = p.stats;
    const extra = panels[st.algoId]?.performanceNote?.(p) || '';
    body.innerHTML = `
      <div class="kpi-strip">
        ${App.kpi('Net P&amp;L', App.money(s.net), App.tone(s.net), 'fa-indian-rupee-sign', 'tone-green', `${s.trades} trades over ${s.days} days`)}
        ${App.kpi('Win rate', s.win_rate !== null ? s.win_rate + '%' : '—', '', 'fa-bullseye', 'tone-blue', `${s.wins} won · ${s.losses} lost`)}
        ${App.kpi('Expectancy', App.money(s.expectancy), App.tone(s.expectancy), 'fa-scale-balanced', 'tone-violet', 'average net per trade')}
        ${App.kpi('Profit factor', s.profit_factor !== null ? App.num(s.profit_factor) : '—', '', 'fa-divide', 'tone-amber', 'gross wins ÷ gross losses')}
        ${App.kpi('Max drawdown', App.money(s.max_drawdown), App.tone(s.max_drawdown), 'fa-arrow-trend-down', 'tone-red', 'from a running peak')}
        ${App.kpi('Return on capital', App.pct(s.return_pct), App.tone(s.return_pct), 'fa-percent', 'tone-green',
          s.capital ? `on ${App.money(s.capital, { sign: false })} · avg ${App.pct(s.avg_roi_pct)}/trade` : `avg ${App.pct(s.avg_roi_pct)} per trade`)}
      </div>
      ${extra}
      <div class="row"><div class="section-card"><div class="card-head"><div><div class="section-title">Equity curve</div>
        <div class="section-subtitle">Cumulative net P&amp;L (line), drawdown (shaded) and each day's net (bars)</div></div></div>
        <div class="chart-box"><canvas id="perf-equity"></canvas></div></div></div>
      <div class="row row-1-1">
        ${groupTable('By days to expiry', 'Which DTE days pay — the basis for choosing live days', p.by_dte, 'DTE')}
        ${groupTable('By weekday', '', p.by_weekday, 'Day')}
      </div>
      <div class="row row-1-1">
        ${groupTable('By exit reason', '', p.by_exit, 'Exit', App.reason)}
        ${groupTable('By entry time', '15-minute buckets', p.by_entry_time, 'Entry')}
      </div>`;
    drawEquity(p, full);
  }

  function groupTable(title, sub, rows, keyLabel, fmt = (k) => k) {
    return `<div class="section-card"><div class="card-head"><div><div class="section-title">${title}</div>
      ${sub ? `<div class="section-subtitle">${sub}</div>` : ''}</div></div>
      ${rows.length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>${keyLabel}</th><th>Trades</th><th>Win rate</th>
        <th>Avg win</th><th>Avg loss</th><th>Expectancy</th><th>Net</th></tr></thead><tbody>
        ${rows.map((r) => `<tr><td>${App.esc(String(fmt(r.key)))}</td><td>${r.trades}</td><td>${r.win_rate !== null ? r.win_rate + '%' : '—'}</td>
          <td class="pos">${App.money(r.avg_win)}</td><td class="neg">${App.money(r.avg_loss)}</td>
          <td class="${App.tone(r.expectancy)}">${App.money(r.expectancy)}</td><td class="${App.tone(r.net)}"><b>${App.money(r.net)}</b></td></tr>`).join('')}
      </tbody></table></div>` : '<div class="chart-empty" style="height:80px">No closed trades</div>'}</div>`;
  }

  function drawEquity(p, animate) {
    const canvas = document.getElementById('perf-equity');
    if (!canvas) return;
    if (!p.equity.length) { canvas.parentElement.innerHTML = '<div class="chart-empty">No closed trades in this range</div>'; return; }
    const labels = p.equity.map((e) => e[0]);
    App.chart(canvas, {
      data: { labels, datasets: [
        { type: 'bar', label: 'Day net', data: p.daily.map((d) => d[1]), backgroundColor: p.daily.map((d) => (d[1] < 0 ? '#ef4444aa' : App.colorFor(st.algoId) + 'aa')),
          borderRadius: 3, maxBarThickness: 18 },
        { type: 'line', label: 'Cumulative', data: p.equity.map((e) => e[1]), borderColor: '#0f172a', borderWidth: 2, pointRadius: 2, tension: 0 },
        { type: 'line', label: 'Drawdown', data: p.drawdown.map((e) => e[1]), borderColor: 'rgba(239,68,68,0.6)', borderWidth: 1, pointRadius: 0,
          fill: { target: { value: 0 }, below: 'rgba(239,68,68,0.12)' } },
      ] },
      options: { responsive: true, maintainAspectRatio: false, animation: animate ? undefined : false,
        interaction: { mode: 'index', intersect: false },
        scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } }, y: { grid: zeroGrid(), ticks: { callback: (v) => App.money(v) } } },
        plugins: { legend: { display: true, position: 'bottom', labels: { boxWidth: 10 } },
          tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${App.money(c.parsed.y)}` } } } },
    });
  }

  // ── Rules ────────────────────────────────────────────────────────────────

  async function loadRules(body) {
    setControls('');
    const r = await App.api(`strategies/${st.algoId}/rules`);
    const changed = r.rows.filter((x) => x.changed).length;
    const val = (v) => (v === null || v === undefined ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v));
    body.innerHTML = `
      <div class="section-card" style="margin-bottom:16px">
        <div class="card-head"><div><div class="section-title">Current configuration · v${App.esc(r.version)}</div>
          <div class="section-subtitle">From <code>src/algos/${App.esc(st.algoId)}/config.yaml</code>${r.last_run
            ? ` · last live run #${r.last_run.run_id} on ${r.last_run.trade_date} used v${App.esc(r.last_version || '?')}` : ' · no live run yet'}</div></div>
          ${changed ? App.chip(`${changed} changed since the last run`, 'tone-amber') : r.last_run ? App.chip('same as last run', 'tone-green') : ''}</div>
        <div class="table-wrap"><table class="data-table rules-table"><thead><tr><th>Setting</th><th>Value</th><th>Last run</th></tr></thead>
          <tbody>${r.rows.map((x) => `<tr class="${x.changed ? 'changed' : ''}"><td class="mono">${App.esc(x.key)}</td>
            <td>${App.esc(val(x.value))}</td><td class="muted">${x.new ? '<i>new setting</i>' : r.last_run ? App.esc(val(x.last_run)) : ''}</td></tr>`).join('')}</tbody></table></div>
      </div>
      ${r.spec_text ? `<details class="section-card spec"><summary><b>Strategy spec</b> <span class="muted">${App.esc(r.spec)}</span>
        ${r.spec_text.includes('not final') || r.spec_text.includes('proposed') ? App.chip('thresholds not final', 'tone-amber') : ''}</summary>
        <pre class="spec-text">${App.esc(r.spec_text)}</pre></details>` : ''}`;
  }

  App.page('strategy', {
    title: (args) => info(args[0])?.name || 'Strategy',
    render,
    destroy() { clearTimers(); closeDetail(); },
  });
})();
