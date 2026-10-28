/* Overview: the day's P&L grouped by strategy, intraday P&L, and the last 30 days. */

(() => {
  const REFRESH_MS = 5000;
  const HISTORY_REFRESH_MS = 60000;
  const STATUS = {
    STARTED: ['Running', 'tone-amber', true],
    ENTRY_PENDING: ['Waiting for entry', 'tone-amber', true],
    IN_TRADE: ['In trade', 'tone-green', true],
    EXITING: ['Exiting', 'tone-red', true],
    CLOSED: ['Trade closed', 'tone-blue', true],   // flat, but the process still records ticks until the report
    DONE: ['Done', 'tone-blue', false],
    SKIPPED: ['Skipped', 'tone-slate', false],
    ERROR: ['Error', 'tone-red', false],
  };
  const REASONS = App.REASONS;

  let state = null;
  let timers = [];
  let stopControl = null;
  let lastHistory = null;

  function render(el) {
    state = {
      date: App.meta?.today || App.isoDate(new Date()),
      mode: App.prefs.get('overview.mode', 'paper'),
      source: App.prefs.get('overview.source', 'live'),
    };
    el.innerHTML = `
      <div class="page-header">
        <div>
          <div class="page-title"><i class="fa-solid fa-gauge-high"></i>Overview</div>
          <div class="page-subtitle" id="ov-subtitle">Today's P&amp;L by strategy</div>
        </div>
        <div class="header-meta">
          <span id="ov-live" class="chip live tone-green hidden"><span class="chip-dot"></span>Live</span>
          <span id="ov-updated"></span>
          <button class="btn-icon-box" id="ov-refresh" title="Refresh" aria-label="Refresh"><i class="fa-solid fa-rotate"></i></button>
        </div>
      </div>

      <div id="ov-control" class="control-card"></div>

      <div class="top-bar">
        <div class="top-bar-group">
          <div class="segmented" id="ov-mode" role="group" aria-label="Trading mode">
            <button data-v="paper"><i class="fa-solid fa-file-lines"></i>Paper</button>
            <button data-v="live"><i class="fa-solid fa-bolt"></i>Live</button>
            <button data-v="all">All</button>
          </div>
          <div class="segmented" id="ov-source" role="group" aria-label="Data source">
            <button data-v="live" title="Real trading sessions">Sessions</button>
            <button data-v="replay" title="Offline replays of recorded days">Replays</button>
          </div>
        </div>
        <div class="top-bar-group date-nav">
          <button class="btn-icon-box" id="ov-prev" title="Previous day" aria-label="Previous day"><i class="fa-solid fa-chevron-left"></i></button>
          <input type="date" class="input-field" id="ov-date" aria-label="Date" />
          <button class="btn-icon-box" id="ov-next" title="Next day" aria-label="Next day"><i class="fa-solid fa-chevron-right"></i></button>
          <button class="btn-secondary" id="ov-today">Today</button>
        </div>
      </div>

      <div id="ov-body"></div>`;

    bindSegmented('ov-mode', 'mode');
    bindSegmented('ov-source', 'source');
    const dateInput = document.getElementById('ov-date');
    dateInput.addEventListener('change', () => dateInput.value && setDate(dateInput.value));
    document.getElementById('ov-prev').addEventListener('click', () => setDate(App.shiftDate(state.date, -1)));
    document.getElementById('ov-next').addEventListener('click', () => setDate(App.shiftDate(state.date, 1)));
    document.getElementById('ov-today').addEventListener('click', () => setDate(App.meta?.today || App.isoDate(new Date())));
    document.getElementById('ov-refresh').addEventListener('click', () => load(true));
    stopControl?.();
    stopControl = App.control.mount(document.getElementById('ov-control'));
    load(true);
  }

  function bindSegmented(id, key) {
    const box = document.getElementById(id);
    const paint = () => box.querySelectorAll('button').forEach((b) => b.classList.toggle('active', b.dataset.v === state[key]));
    box.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b || b.dataset.v === state[key]) return;
      state[key] = b.dataset.v;
      App.prefs.set('overview.' + key, state[key]);
      paint();
      load(true);
    });
    paint();
  }

  function setDate(iso) { state.date = iso; load(true); }

  const isToday = () => state.date === (App.meta?.today || App.isoDate(new Date()));
  const isLiveView = () => isToday() && state.source === 'live';

  function schedule() {
    timers.forEach(clearInterval);
    timers = [];
    document.getElementById('ov-live')?.classList.toggle('hidden', !isLiveView());
    if (!isLiveView()) return;
    timers.push(setInterval(() => load(false), REFRESH_MS));
    timers.push(setInterval(() => { lastHistory = null; }, HISTORY_REFRESH_MS));
  }

  async function load(full) {
    const params = { date: state.date, mode: state.mode, source: state.source };
    document.getElementById('ov-date').value = state.date;
    document.getElementById('ov-subtitle').textContent =
      `${App.prettyDate(state.date)} · ${state.source === 'replay' ? 'replays' : 'P&L by strategy'}`;
    if (full) {
      lastHistory = null;
      document.getElementById('ov-body').innerHTML = skeleton();
      schedule();
    }
    try {
      const [day, hist] = await Promise.all([
        App.api('overview', params),
        lastHistory ? Promise.resolve(lastHistory)
          : App.api('pnl-history', { end: state.date, days: 30, mode: state.mode, source: state.source }),
      ]);
      lastHistory = hist;
      draw(day, hist, full);
      document.getElementById('ov-updated').textContent =
        'Updated ' + new Date().toLocaleTimeString('en-IN', { hour12: false });
    } catch (err) {
      if (full) document.getElementById('ov-body').innerHTML = emptyState('fa-plug-circle-xmark',
        'Could not load data', App.esc(err.message));
    }
  }

  // ── drawing ──────────────────────────────────────────────────────────────

  function draw(day, hist, animate) {
    App.destroyCharts();
    const body = document.getElementById('ov-body');
    if (!body) return;
    if (!day.strategies.length) {
      body.innerHTML = noRuns() + historySection(hist);
      bindJump();
      drawHistory(hist, animate);
      return;
    }
    body.innerHTML = kpis(day.totals) + `<div class="strategy-grid">${day.strategies.map(card).join('')}</div>`
      + intradaySection(day) + historySection(hist);
    day.strategies.forEach((s) => sparkline(s, animate));
    drawIntraday(day, animate);
    drawHistory(hist, animate);
  }

  function kpi(label, value, cls, icon, toneCls, foot = '') {
    return `<div class="kpi-tile">
      <div class="kpi-head"><span class="kpi-icon ${toneCls}"><i class="fa-solid ${icon}"></i></span>
        <span class="stat-label">${label}</span></div>
      <div class="stat-value ${cls}">${value}</div>
      ${foot ? `<div class="stat-foot">${foot}</div>` : ''}
    </div>`;
  }

  function kpis(t) {
    return `<div class="kpi-strip">
      ${kpi('Net P&amp;L', App.money(t.net), App.tone(t.net), 'fa-indian-rupee-sign', 'tone-green', 'after charges')}
      ${kpi('Gross P&amp;L', App.money(t.gross), App.tone(t.gross), 'fa-chart-line', 'tone-blue', 'before charges')}
      ${kpi('Charges', App.money(t.charges, { sign: false }), '', 'fa-receipt', 'tone-amber', 'brokerage, STT, GST, fees')}
      ${kpi('Open MTM', t.open_positions ? App.money(t.open_net) : '—', App.tone(t.open_net), 'fa-hourglass-half', 'tone-violet',
            `${t.open_positions} open position${t.open_positions === 1 ? '' : 's'}`)}
      ${kpi('Trades', t.trades, '', 'fa-right-left', 'tone-slate', `${t.traded} of ${t.strategies} strategies traded`)}
      ${kpi('Skipped', t.skipped, '', 'fa-forward', 'tone-slate', t.running ? `${t.running} still running` : 'stood aside today')}
    </div>`;
  }

  function chip(label, cls, live) {
    return `<span class="chip ${cls}${live ? ' live' : ''}"><span class="chip-dot"></span>${label}</span>`;
  }

  function card(s) {
    // The run's own status only changes at the report; the live state knows sooner.
    let key = s.status;
    // ... but only while the run is going: a window closed mid-wait leaves a stale live row.
    if (['STARTED', 'IN_TRADE'].includes(s.status) && ['ENTRY_PENDING', 'EXITING'].includes(s.live_status)) key = s.live_status;
    else if (s.live_status === 'DONE' && s.status === 'IN_TRADE') key = 'CLOSED';
    const st = STATUS[key] || [s.status, 'tone-slate', false];
    const live = st[2] && isLiveView();
    const modeChip = s.mode === 'live' ? chip('Live', 'tone-red', false) : chip('Paper', 'tone-violet', false);
    const traded = s.trades > 0;
    const pnl = traded ? (s.net ?? s.gross) : null;
    const reason = s.exit_reason ? s.exit_reason.split(', ').map((r) => REASONS[r] || r).join(', ') : null;
    let note = '';
    if (s.status === 'SKIPPED') note = `<i class="fa-solid fa-circle-info"></i> ${App.esc(s.skip_reason || 'Skipped')}`;
    else if (traded && (s.max_profit !== null || s.max_drawdown !== null))
      note = `Best ${App.money(s.max_profit)} · worst ${App.money(s.max_drawdown)} during the trade`;
    const sub = state.source === 'replay' ? `Replay run #${s.run_id}` : (s.spot ? `NIFTY ${App.num(s.spot, 1)} · VIX ${App.num(s.vix, 2)}` : `Run #${s.run_id}`);
    const runs = (s.runs || []).length > 1
      ? `<div class="strategy-note"><i class="fa-solid fa-layer-group"></i> ${s.runs.length} runs today: ${s.runs.map((r) =>
        `#${r.run_id} ${App.timeOf(r.started_at, false)} ${App.esc((STATUS[r.status] || [r.status])[0].toLowerCase())}${r.trades ? ` (${r.trades} trade${r.trades === 1 ? '' : 's'})` : ''}`).join(' · ')}</div>` : '';

    return `<a class="strategy-card card-link" href="#/strategy/${App.esc(s.algo_id)}/today" title="Open the strategy page"
      style="--strategy-color:${App.colorFor(s.algo_id)}">
      <div class="strategy-top">
        <div><div class="strategy-name">${App.esc(s.name)}</div><div class="strategy-sub">${sub}</div></div>
        <div class="chips">${modeChip}${(s.runs || []).length > 1 ? chip(`${s.runs.length} runs`, 'tone-slate', false) : ''}${chip(st[0], st[1], live)}</div>
      </div>
      <div class="strategy-pnl ${App.tone(pnl)}">${traded ? App.money(pnl) : '—'}</div>
      <div class="strategy-pnl-label">${traded ? (s.open_positions ? 'net, open position marked to market' : 'net P&L after charges') : 'no trade'}</div>
      ${traded ? `<div class="spark"><canvas id="spark-${s.run_id}" aria-label="Intraday P&L"></canvas></div>` : ''}
      <div class="strategy-facts">
        <div><div class="fact-label">Gross</div><div class="fact-value ${App.tone(s.gross)}">${App.money(s.gross)}</div></div>
        <div><div class="fact-label">Charges</div><div class="fact-value">${App.money(s.charges, { sign: false })}</div></div>
        <div><div class="fact-label">Credit</div><div class="fact-value">${s.entry_credit !== null ? App.num(s.entry_credit) + ' pts' : '—'}</div></div>
      </div>
      ${reason ? `<div class="strategy-note"><i class="fa-solid fa-door-open"></i> Exit: ${App.esc(reason)}</div>` : ''}
      ${note ? `<div class="strategy-note">${note}</div>` : ''}
      ${runs}
    </a>`;
  }

  function sparkline(s, animate) {
    const canvas = document.getElementById(`spark-${s.run_id}`);
    if (!canvas || !s.intraday.length) return;
    const color = App.colorFor(s.algo_id);
    App.chart(canvas, {
      type: 'line',
      data: { labels: s.intraday.map((p) => p[0]),
        datasets: [{ data: s.intraday.map((p) => p[1]), borderColor: color, borderWidth: 1.5, pointRadius: 0,
          fill: { target: { value: 0 }, above: color + '22', below: '#ef444422' }, tension: 0.25 }] },
      options: { responsive: true, maintainAspectRatio: false, animation: animate ? undefined : false,
        scales: { x: { display: false }, y: { display: false } },
        plugins: { tooltip: { enabled: false } }, interaction: { mode: null } },
    });
  }

  function intradaySection(day) {
    const legend = day.strategies.filter((s) => s.intraday.length)
      .map((s) => `<span><i class="swatch" style="background:${App.colorFor(s.algo_id)}"></i>${App.esc(s.name)}</span>`).join('');
    return `<div class="row"><div class="section-card">
      <div class="card-head">
        <div><div class="section-title">Intraday P&amp;L</div>
          <div class="section-subtitle">Net of charges, sampled every minute from position snapshots</div></div>
        <div class="legend">${legend}</div>
      </div>
      <div class="chart-box"><canvas id="ov-intraday"></canvas></div>
    </div></div>`;
  }

  function zeroLineGrid() {
    return { color: (ctx) => (ctx.tick && ctx.tick.value === 0 ? '#cbd5e1' : '#f1f5f9') };
  }
  const moneyTick = (v) => App.money(v);

  function drawIntraday(day, animate) {
    const withData = day.strategies.filter((s) => s.intraday.length);
    const canvas = document.getElementById('ov-intraday');
    if (!canvas) return;
    if (!withData.length) {
      canvas.parentElement.innerHTML = '<div class="chart-empty">No position snapshots for this day yet</div>';
      return;
    }
    const labels = [...new Set(withData.flatMap((s) => s.intraday.map((p) => p[0])))].sort();
    App.chart(canvas, {
      type: 'line',
      data: { labels, datasets: withData.map((s) => {
        const m = new Map(s.intraday);
        const color = App.colorFor(s.algo_id);
        return { label: s.name, data: labels.map((l) => (m.has(l) ? m.get(l) : null)), borderColor: color,
          backgroundColor: color, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0.2, spanGaps: true };
      }) },
      options: { responsive: true, maintainAspectRatio: false, animation: animate ? undefined : false,
        interaction: { mode: 'index', intersect: false },
        scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } },
          y: { grid: zeroLineGrid(), ticks: { callback: moneyTick } } },
        plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${App.money(c.parsed.y)}` } } } },
    });
  }

  function historySection(hist) {
    const rows = hist.series.map((s) => {
      const traded = s.values.filter((v) => v !== 0);
      const wins = traded.filter((v) => v > 0).length;
      const total = s.values.reduce((a, b) => a + b, 0);
      const best = traded.length ? Math.max(...traded) : null;
      const worst = traded.length ? Math.min(...traded) : null;
      return `<tr><td><i class="swatch" style="background:${App.colorFor(s.algo_id)}"></i>${App.esc(s.name)}</td>
        <td>${traded.length}</td><td>${traded.length ? Math.round((wins / traded.length) * 100) + '%' : '—'}</td>
        <td class="${App.tone(best)}">${App.money(best)}</td><td class="${App.tone(worst)}">${App.money(worst)}</td>
        <td class="${App.tone(total)}"><b>${App.money(total)}</b></td></tr>`;
    }).join('');
    const total = hist.cumulative.length ? hist.cumulative[hist.cumulative.length - 1] : null;
    return `<div class="row">
      <div class="section-card">
        <div class="card-head"><div><div class="section-title">Last 30 days</div>
          <div class="section-subtitle">Daily net P&amp;L by strategy (bars) and cumulative (line)</div></div></div>
        <div class="chart-box"><canvas id="ov-history"></canvas></div>
      </div>
    </div>
    <div class="row">
      <div class="section-card">
        <div class="card-head"><div><div class="section-title">Strategy scorecard</div>
          <div class="section-subtitle">Same 30 days, counting only days with a trade</div></div></div>
        ${hist.series.length ? `<div class="table-wrap"><table class="data-table">
          <thead><tr><th>Strategy</th><th>Days traded</th><th>Win rate</th><th>Best day</th><th>Worst day</th><th>Net</th></tr></thead>
          <tbody>${rows}</tbody>
          ${hist.series.length > 1 ? `<tfoot><tr><td>Total</td><td colspan="4"></td><td class="${App.tone(total)}">${App.money(total)}</td></tr></tfoot>` : ''}
        </table></div>` : '<div class="chart-empty" style="height:120px">No completed days yet</div>'}
      </div>
    </div>`;
  }

  function drawHistory(hist, animate) {
    const canvas = document.getElementById('ov-history');
    if (!canvas) return;
    if (!hist.dates.length) {
      canvas.parentElement.innerHTML = '<div class="chart-empty">No completed days in this window</div>';
      return;
    }
    const labels = hist.dates.map((d) => {
      const [y, m, dd] = d.split('-').map(Number);
      return new Date(y, m - 1, dd).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
    });
    App.chart(canvas, {
      data: { labels, datasets: [
        ...hist.series.map((s) => ({ type: 'bar', label: s.name, data: s.values, stack: 'pnl',
          backgroundColor: s.values.map((v) => (v < 0 ? '#ef4444cc' : App.colorFor(s.algo_id) + 'cc')),
          borderRadius: 4, maxBarThickness: 26, yAxisID: 'y' })),
        { type: 'line', label: 'Cumulative', data: hist.cumulative, borderColor: '#0f172a', borderWidth: 1.5,
          pointRadius: 2, tension: 0, yAxisID: 'y' },
      ] },
      options: { responsive: true, maintainAspectRatio: false, animation: animate ? undefined : false,
        interaction: { mode: 'index', intersect: false },
        scales: { x: { stacked: true, grid: { display: false } },
          y: { stacked: false, grid: zeroLineGrid(), ticks: { callback: moneyTick } } },
        plugins: { tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${App.money(c.parsed.y)}` } } } },
    });
  }

  // ── empty / loading ──────────────────────────────────────────────────────

  function emptyState(icon, title, text, extra = '') {
    return `<div class="section-card empty-state"><i class="fa-solid ${icon}"></i>
      <div class="empty-title">${title}</div><p>${text}</p>${extra}</div>`;
  }

  function noRuns() {
    const latest = state.source === 'replay' ? App.meta?.latest_replay_date : App.meta?.latest_live_date;
    const jump = latest && latest !== state.date
      ? `<button class="btn-secondary" id="ov-jump"><i class="fa-solid fa-calendar-day"></i> Go to ${App.prettyDate(latest)}</button>` : '';
    const hint = state.source === 'replay'
      ? 'Replay a recorded day with <code>algo run --algo iron_condor --replay YYYY-MM-DD</code>'
      : 'Start today\'s session with <code>algo run --algo iron_condor</code>';
    return `<div style="margin-bottom:16px">${emptyState('fa-moon', 'No strategy ran on this day', hint, jump)}</div>`;
  }

  function bindJump() {
    const b = document.getElementById('ov-jump');
    const latest = state.source === 'replay' ? App.meta?.latest_replay_date : App.meta?.latest_live_date;
    if (b) b.addEventListener('click', () => setDate(latest));
  }

  function skeleton() {
    const tile = '<div class="kpi-tile"><div class="stat-label skeleton">Loading</div><div class="stat-value skeleton" style="margin-top:8px">₹0,000</div></div>';
    return `<div class="kpi-strip">${tile.repeat(6)}</div>`;
  }

  App.page('overview', {
    title: 'Overview',
    render,
    destroy() { timers.forEach(clearInterval); timers = []; stopControl?.(); stopControl = null; },
  });
})();
