/* Compare: #/backtests/compare?runs=a,b,c (docs/design/backtest.md, section 6.6). 2–6 runs.
   Everything is worked out here from each run's summary.json and recorded config
   (GET /api/backtests/<name>): overlaid equity, KPIs side by side with the best marked, results by
   DTE / weekday / exit / month, the settings that differ, and day-by-day P&L. */

(() => {
  const COLORS = ['#2563eb', '#f59e0b', '#10b981', '#ef4444', '#8b5cf6', '#0891b2'];
  const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'];
  const MAX = 6;

  // KPI rows: [label, value(totals), format, better: 1 higher / -1 lower / 0 none]
  const KPIS = [
    ['Net P&L', (t) => t.net, 'money', 1],
    ['Trades', (t) => t.trades, 'int', 0],
    ['Win rate', (t) => t.win_rate, 'pct', 1],
    ['Profit factor', (t) => t.profit_factor, 'num', 1],
    ['Expectancy / trade', (t) => t.expectancy, 'money', 1],
    ['Max drawdown', (t) => t.max_drawdown, 'money', 1],
    ['Avg win', (t) => t.avg_win, 'money', 1],
    ['Avg loss', (t) => t.avg_loss, 'money', 1],
    ['Best day', (t) => t.best_day, 'money', 1],
    ['Worst day', (t) => t.worst_day, 'money', 1],
    ['Days traded', (t) => t.days_traded, 'int', 0],
    ['Days without a trade', (t) => t.days_skipped, 'int', 0],
    ['Gross', (t) => t.gross, 'money', 0],
    ['Charges', (t) => t.charges, 'money0', 0],
  ];

  const fmt = (v, kind) => (v === null || v === undefined ? '—'
    : kind === 'money' ? App.money(v) : kind === 'money0' ? App.money(v, { sign: false })
      : kind === 'pct' ? `${v}%` : kind === 'num' ? App.num(v) : String(v));
  const short = (iso) => { const [y, m, d] = iso.split('-').map(Number); return `${d}/${m}/${String(y).slice(2)}`; };
  function flatten(obj, prefix = '', out = {}) {
    Object.entries(obj || {}).forEach(([k, v]) => {
      if (v && typeof v === 'object' && !Array.isArray(v) && !/_by_dte$/.test(k)) flatten(v, prefix + k + '.', out);
      else out[prefix + k] = v;
    });
    return out;
  }
  const show = (v) => (v === undefined ? '—' : v === null ? 'none' : typeof v === 'object' ? JSON.stringify(v) : String(v));

  // Index of the best value among runs, or -1 when there's no clear winner.
  function best(values, better) {
    if (!better) return -1;
    const ok = values.map((v, i) => [v, i]).filter(([v]) => v !== null && v !== undefined);
    if (ok.length < 2) return -1;
    ok.sort((a, b) => (b[0] - a[0]) * better);
    return ok[0][0] === ok[1][0] ? -1 : ok[0][1];
  }

  async function render(el, query) {
    let names = (query?.get('runs') || '').split(',').filter(Boolean);
    if (!names.length) names = App.prefs.get('bt.compare', []);
    names = [...new Set(names)].slice(0, MAX);
    el.innerHTML = '<div class="section-card"><div class="skeleton" style="height:200px"></div></div>';
    const [list, ...details] = await Promise.all([App.api('backtests').then((r) => r.runs),
      ...names.map((n) => App.api(`backtests/${encodeURIComponent(n)}`).catch(() => ({ name: n, missing: true })))]);
    const runs = details.map((d, i) => ({ ...d, color: COLORS[i % COLORS.length] }));
    const usable = runs.filter((r) => r.summary);
    const skipped = runs.filter((r) => !r.summary);
    App.prefs.set('bt.compare', names);

    const setRuns = (ns) => { location.hash = '#/backtests/compare' + (ns.length ? '?runs=' + ns.map(encodeURIComponent).join(',') : ''); };
    const done = list.filter((r) => r.state === 'done' && !names.includes(r.name));
    const picker = names.length < MAX ? `<label class="field-label">Add a run<select class="input-field" id="cmp-add">
        <option value="">Pick a finished run…</option>${done.map((r) => `<option value="${App.esc(r.name)}">${App.esc(r.name)} · ${App.esc(r.algo_id)} · ${r.option_prices}</option>`).join('')}</select></label>` : '';

    const head = `<div class="page-header"><div><div class="page-title"><i class="fa-solid fa-code-compare"></i>Compare</div>
        <div class="page-subtitle">Up to ${MAX} runs side by side. Best values are marked <span class="cmp-best-key">like this</span>.</div></div></div>
      <div class="section-card cmp-runs">
        <div class="cmp-chips">${runs.map((r) => `<span class="cmp-chip" style="--c:${r.color}">
          <span class="cmp-swatch"></span><a href="#/backtests/run/${encodeURIComponent(r.name)}">${App.esc(r.name)}</a>
          <span class="muted small">${r.missing ? 'not found' : `${App.esc(r.algo_id)} v${App.esc(r.version || '?')} · ${r.option_prices} · ${r.start === r.end ? short(r.start) : `${short(r.start)}–${short(r.end)}`}`}</span>
          <button class="link-btn" data-remove="${App.esc(r.name)}" title="Remove from compare" aria-label="Remove ${App.esc(r.name)}"><i class="fa-solid fa-xmark"></i></button></span>`).join('')}
        </div>${picker}</div>`;

    if (usable.length < 2) {
      el.innerHTML = head + App.emptyState('fa-code-compare', 'Pick at least two finished runs',
        `Add runs here, or tick 2–6 on <a href="#/backtests/runs">All runs</a>.${skipped.length ? ` Not comparable yet: ${skipped.map((r) => `<code>${App.esc(r.name)}</code> (${r.missing ? 'not found' : App.esc(r.label || r.state)})`).join(', ')}.` : ''}`);
      bindHead(el, names, setRuns);
      return;
    }

    // ── warnings ──
    const warn = [];
    if (new Set(usable.map((r) => r.option_prices)).size > 1) warn.push('These runs mix real and modelled option prices: their rupee results are not comparable.');
    if (new Set(usable.map((r) => `${r.start}|${r.end}`)).size > 1) warn.push('The date ranges differ: totals cover different days. The day-by-day table lines them up by date.');
    if (new Set(usable.map((r) => r.algo_id)).size > 1) warn.push('These are different strategies.');
    if (skipped.length) warn.push(`Left out (no summary yet): ${skipped.map((r) => App.esc(r.name)).join(', ')}.`);

    // ── day by day ──
    const dates = [...new Set(usable.flatMap((r) => (r.summary.equity || []).map((e) => e.date)))].sort();
    const byDay = usable.map((r) => Object.fromEntries((r.summary.equity || []).map((e) => [e.date, e.net])));
    const winsPerRun = usable.map(() => 0);
    const dayRows = dates.map((d) => {
      const vals = byDay.map((m) => (d in m ? m[d] : null));
      const traded = vals.filter((v) => v !== null && v !== 0);
      const b = traded.length ? best(vals, 1) : -1;
      if (b >= 0) winsPerRun[b] += 1;
      return { d, vals, b };
    });

    el.innerHTML = head + `
      ${warn.map((w) => `<div class="banner banner-warn"><i class="fa-solid fa-triangle-exclamation"></i>${w}</div>`).join('')}
      <div class="row"><div class="section-card"><div class="card-head"><div><div class="section-title">Equity curves</div>
        <div class="section-subtitle">Cumulative net P&amp;L by date, one line per run</div></div>
        <div class="segmented" id="cmp-mode"><button data-v="cum" class="active">Cumulative</button><button data-v="dd">Drawdown</button></div></div>
        <div class="chart-box"><canvas id="cmp-equity"></canvas></div></div></div>

      <div class="row"><div class="section-card"><div class="card-head"><div><div class="section-title">Key numbers</div></div></div>
        <div class="table-wrap"><table class="data-table cmp-table"><thead><tr><th></th>${usable.map(colHead).join('')}</tr></thead><tbody>
        ${KPIS.map(([label, fn, kind, better]) => {
          const vals = usable.map((r) => fn(r.summary.totals || {}));
          const b = best(vals, better);
          return `<tr><td>${label}</td>${vals.map((v, i) => `<td class="${kind.startsWith('money') && kind !== 'money0' ? App.tone(v) : ''}${i === b ? ' cmp-best' : ''}">${fmt(v, kind)}</td>`).join('')}</tr>`;
        }).join('')}
        <tr><td>Days best of the group</td>${winsPerRun.map((n) => `<td>${n}</td>`).join('')}</tr>
        <tr><td>Run time</td>${usable.map((r) => `<td class="muted">${App.duration(r.elapsed_s)}</td>`).join('')}</tr>
        </tbody></table></div></div></div>

      <div class="row row-1-1">${groupCard('By days to expiry', usable, 'by_dte', (a, b) => Number(a) - Number(b), (k) => `DTE ${k}`)}
        ${groupCard('By weekday', usable, 'by_weekday', (a, b) => WEEKDAYS.indexOf(a) - WEEKDAYS.indexOf(b))}</div>
      <div class="row row-1-1">${groupCard('By exit reason', usable, 'by_exit', null, App.reason)}
        ${groupCard('By month', usable, 'by_month', (a, b) => a.localeCompare(b))}</div>

      <div class="row"><div class="section-card"><div class="card-head"><div><div class="section-title">Settings that differ</div>
        <div class="section-subtitle">From the config each run recorded (its first day); only keys whose values differ</div></div></div>
        ${settingsTable(usable)}</div></div>

      <div class="row"><div class="section-card"><div class="card-head"><div><div class="section-title">Day by day</div>
        <div class="section-subtitle">Net P&amp;L per day; the best run of each day is marked. A blank cell: the run didn't cover that day</div></div>
        <label class="bt-toggle"><input type="checkbox" id="cmp-traded" ${App.prefs.get('bt.cmp.tradedOnly', true) ? 'checked' : ''} /> Only days with a trade</label></div>
        <div class="table-wrap cmp-days"><table class="data-table cmp-table"><thead><tr><th>Date</th>${usable.map(colHead).join('')}</tr></thead>
          <tbody id="cmp-days-body"></tbody>
          <tfoot><tr><td>Total</td>${usable.map((r, i) => {
            const sum = Object.values(byDay[i]).reduce((a, v) => a + (v || 0), 0);
            return `<td class="${App.tone(sum)}" data-total="${i}">${App.money(sum)}</td>`;
          }).join('')}</tr></tfoot></table></div></div></div>`;

    bindHead(el, names, setRuns);
    const drawDays = () => {
      const only = el.querySelector('#cmp-traded').checked;
      const rows = dayRows.filter((r) => !only || r.vals.some((v) => v !== null && v !== 0)).reverse();
      el.querySelector('#cmp-days-body').innerHTML = rows.map((r) => `<tr><td>${r.d} <span class="muted">${new Date(r.d + 'T12:00:00').toLocaleDateString('en-IN', { weekday: 'short' })}</span></td>
        ${r.vals.map((v, i) => `<td class="${App.tone(v)}${i === r.b ? ' cmp-best' : ''}">${v === null ? '' : v === 0 ? '<span class="muted">0</span>' : App.money(v)}</td>`).join('')}</tr>`).join('')
        || `<tr><td colspan="${usable.length + 1}" class="muted">No day with a trade</td></tr>`;
    };
    el.querySelector('#cmp-traded').addEventListener('change', (e) => { App.prefs.set('bt.cmp.tradedOnly', e.target.checked); drawDays(); });
    drawDays();
    let mode = 'cum';
    el.querySelector('#cmp-mode').addEventListener('click', (e) => {
      const b = e.target.closest('button'); if (!b || b.dataset.v === mode) return;
      mode = b.dataset.v;
      el.querySelectorAll('#cmp-mode button').forEach((x) => x.classList.toggle('active', x === b));
      drawChart(usable, dates, mode);
    });
    drawChart(usable, dates, mode);
  }

  const colHead = (r) => `<th style="--c:${r.color}" class="cmp-col" title="${App.esc(r.name)}"><span class="cmp-swatch"></span>${App.esc(r.name)}</th>`;

  function bindHead(el, names, setRuns) {
    el.querySelectorAll('[data-remove]').forEach((b) => b.addEventListener('click', () => setRuns(names.filter((n) => n !== b.dataset.remove))));
    el.querySelector('#cmp-add')?.addEventListener('change', (e) => { if (e.target.value) setRuns([...names, e.target.value]); });
  }

  function groupCard(title, runs, key, sort, label = (k) => k) {
    const keys = [...new Set(runs.flatMap((r) => Object.keys(r.summary[key] || {})))];
    if (sort) keys.sort(sort);
    if (!keys.length) return `<div class="section-card"><div class="section-title">${title}</div><div class="chart-empty" style="height:80px">Not in these summaries</div></div>`;
    return `<div class="section-card"><div class="card-head"><div><div class="section-title">${title}</div>
      <div class="section-subtitle">Net P&amp;L, with trades and win rate below</div></div></div>
      <div class="table-wrap"><table class="data-table cmp-table"><thead><tr><th></th>${runs.map(colHead).join('')}</tr></thead><tbody>
      ${keys.map((k) => {
        const cells = runs.map((r) => (r.summary[key] || {})[k] || null);
        const b = best(cells.map((c) => (c ? c.net : null)), 1);
        return `<tr><td>${App.esc(String(label(k)))}</td>${cells.map((c, i) => (c
          ? `<td class="${App.tone(c.net)}${i === b ? ' cmp-best' : ''}">${App.money(c.net)}<div class="cmp-sub">${c.trades} tr · ${c.win_rate ?? '—'}%</div></td>`
          : '<td class="muted">—</td>')).join('')}</tr>`;
      }).join('')}</tbody></table></div></div>`;
  }

  function settingsTable(runs) {
    const flats = runs.map((r) => ({ algo: r.algo_id, version: r.version, prices: r.option_prices, dates: `${r.start} → ${r.end}`,
      ...flatten(r.config_snapshot || {}) }));
    const keys = [...new Set(flats.flatMap((f) => Object.keys(f)))];
    const differ = keys.filter((k) => new Set(flats.map((f) => JSON.stringify(f[k]))).size > 1)
      .filter((k) => !['description', 'name', 'algo_id'].includes(k));      // algo_id is the "algo" row
    if (!differ.length) return '<div class="chart-empty" style="height:80px">Same settings in every run</div>';
    const meta = ['algo', 'version', 'prices', 'dates'];
    differ.sort((a, b) => (meta.includes(b) - meta.includes(a)) || a.localeCompare(b));
    return `<div class="table-wrap"><table class="data-table cmp-table cmp-settings"><thead><tr><th>Setting</th>${runs.map(colHead).join('')}</tr></thead><tbody>
      ${differ.map((k) => `<tr><td class="mono">${App.esc(k)}</td>${flats.map((f) => `<td class="mono">${App.esc(show(f[k]))}</td>`).join('')}</tr>`).join('')}
      </tbody></table></div>`;
  }

  function drawChart(runs, dates, mode) {
    App.destroyCharts();
    const canvas = document.getElementById('cmp-equity');
    if (!canvas) return;
    const sets = runs.map((r) => {
      const m = Object.fromEntries((r.summary.equity || []).map((e) => [e.date, mode === 'cum' ? e.cum : e.drawdown]));
      return { label: r.name, data: dates.map((d) => (d in m ? m[d] : null)), borderColor: r.color, backgroundColor: r.color,
        borderWidth: 2, pointRadius: dates.length > 60 ? 0 : 2, tension: 0, spanGaps: true };
    });
    App.chart(canvas, {
      type: 'line', data: { labels: dates, datasets: sets },
      options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
        scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } },
          y: { grid: { color: (c) => (c.tick && c.tick.value === 0 ? '#cbd5e1' : '#f1f5f9') }, ticks: { callback: (v) => App.money(v) } } },
        plugins: { legend: { display: true, position: 'bottom', labels: { boxWidth: 10 } },
          tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${App.money(c.parsed.y)}` } } } },
    });
  }

  App.backtestCompare = { render };
})();
