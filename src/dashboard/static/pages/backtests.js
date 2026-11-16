/* Backtests: #/backtests/<view>/... (docs/design/backtest.md, section 6). One page, several views:
     runs                  All runs (default): every folder in data/backtests
     run/<name>/<tab>      one run: summary, trades, days, settings, log
     new, sweep, compare?runs=a,b, data   later stages (placeholders for now)
   The server only lists and reads folders; sorting, filtering, diffs and charts happen here. The
   Trades tab and trade detail are the strategy page's own panels (App.strategyEmbed). */

(() => {
  const POLL_MS = 2000;
  const VIEWS = {
    runs: { title: 'All runs', icon: 'fa-list', sub: 'Every backtest in data/backtests, newest first' },
    run: { title: 'Backtest', icon: 'fa-flask', sub: '' },
    new: { title: 'New backtest', icon: 'fa-play', sub: 'Pick a strategy, change its settings and run it on minute history', stage: 'F-B3' },
    compare: { title: 'Compare', icon: 'fa-code-compare', sub: 'Equity curves, KPIs, settings and day-by-day P&L of 2–6 runs', stage: 'F-B4' },
    sweep: { title: 'Parameter sweep', icon: 'fa-table-cells', sub: 'Run a grid of 1–2 settings, one backtest per combination', stage: 'F-B5' },
    data: { title: 'Market data coverage', icon: 'fa-database', sub: 'What is stored per dataset, the gaps, and the days real-price backtests skip', stage: 'F-B6' },
  };
  const RUN_TABS = [
    ['summary', 'Summary', 'fa-chart-pie'],
    ['trades', 'Trades', 'fa-table-list'],
    ['days', 'Days', 'fa-calendar-days'],
    ['settings', 'Settings', 'fa-sliders'],
    ['log', 'Log', 'fa-terminal'],
  ];
  const STATE = {
    done: ['Done', 'tone-green', false], running: ['Running', 'tone-blue', true], queued: ['Queued', 'tone-slate', false],
    failed: ['Failed', 'tone-red', false], stale: ['Stale', 'tone-amber', false],
  };
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const SELLERS = /condor/;      // algos that sell options: modelled prices are biased against them

  let S = { runs: null, runsAt: 0, timer: null };
  const active = (r) => r.state === 'running' || r.state === 'queued';
  const viewOf = (args) => (VIEWS[args[0]] ? args[0] : 'runs');
  const stopPoll = () => { if (S.timer) clearInterval(S.timer); S.timer = null; };

  // ── shared bits ──────────────────────────────────────────────────────────
  const algoName = (id) => ((App.meta?.strategies || []).find((a) => a.algo_id === id)?.name || id || '?').replace(/^NIFTY\s+/, '');
  function shortDate(iso, year = true) {
    if (!iso) return '—';
    const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
    return `${d} ${MONTHS[m - 1]}${year ? ' ' + String(y).slice(2) : ''}`;
  }
  const range = (r) => (r.start === r.end ? shortDate(r.start) : `${shortDate(r.start, r.start.slice(0, 4) !== r.end.slice(0, 4))} → ${shortDate(r.end)}`);
  const stamp = (iso) => (iso ? `${shortDate(iso, false)} ${iso.slice(11, 16)}` : '—');
  const stateChip = (r) => { const s = STATE[r.state] || [r.state, 'tone-slate', false]; return App.chip(s[0], s[1], s[2]); };
  const pricesChip = (p) => (p === 'real' ? App.chip('Real', 'tone-green') : App.chip('Model', 'tone-amber'));
  function progress(r) {
    if (!active(r) || !r.days) return '';
    const pct = Math.round((100 * (r.done || 0)) / r.days);
    return `<div class="bt-progress" title="${App.esc(r.label)}"><div style="width:${pct}%"></div></div>`;
  }
  function compareSet() { return new Set(App.prefs.get('bt.compare', [])); }
  function saveCompare(set) { App.prefs.set('bt.compare', [...set].slice(-6)); }

  function flatten(obj, prefix = '', out = {}) {
    Object.entries(obj || {}).forEach(([k, v]) => {
      const key = prefix + k;
      if (v && typeof v === 'object' && !Array.isArray(v) && !/_by_dte$/.test(k)) flatten(v, key + '.', out);
      else out[key] = v;
    });
    return out;
  }
  const show = (v) => (v === null || v === undefined ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v));
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

  async function loadRuns(force = false) {
    if (force || !S.runs || Date.now() - S.runsAt > 10000) {
      S.runs = (await App.api('backtests')).runs;
      S.runsAt = Date.now();
      App.setRecent(S.runs.slice(0, 5).map((r) => ({ name: r.name, state: r.state, label: r.label })));
    }
    return S.runs;
  }

  function header(title, sub, meta = '', icon = 'fa-flask') {
    return `<div class="page-header">
      <div><div class="page-title"><i class="fa-solid ${icon}"></i>${title}</div>${sub ? `<div class="page-subtitle">${sub}</div>` : ''}</div>
      <div class="header-meta">${meta}</div></div>`;
  }

  // ── page ─────────────────────────────────────────────────────────────────

  function title(args) {
    const v = viewOf(args);
    return v === 'run' && args[1] ? args[1] : VIEWS[v].title;
  }

  async function render(el, args, query) {
    stopPoll();
    App.strategyEmbed?.close();
    if (!args.length) { location.replace('#/backtests/runs'); return; }   // one URL per view
    const v = viewOf(args);
    if (v === 'runs') return renderRuns(el);
    if (v === 'run') return renderRun(el, args[1], RUN_TABS.some((t) => t[0] === args[2]) ? args[2] : 'summary');
    loadRuns().catch(() => {});                                            // keeps Recent current
    if (v === 'new') return App.backtestNew.render(el, query);
    if (v === 'compare') return App.backtestCompare.render(el, query);
    if (v === 'sweep') return App.backtestSweep.render(el, args.slice(1), query);
    if (v === 'data') return App.backtestData.render(el);
    const def = VIEWS[v];
    const runs = v === 'compare' ? (query?.get('runs') || '').split(',').filter(Boolean) : [];
    el.innerHTML = header(def.title, def.sub, '', def.icon) + App.emptyState('fa-person-digging', `${def.title}: coming in stage ${def.stage}`,
      'This page is built in a later stage of the plan in docs/design/backtest.md, section 6.11.',
      runs.length ? `<p class="bt-placeholder-arg">Runs: ${runs.map((r) => `<code>${App.esc(r)}</code>`).join(', ')}</p>` : '');
  }

  // ── All runs ─────────────────────────────────────────────────────────────

  const COLS = [
    ['name', 'Name', (r) => r.name],
    ['algo', 'Algo', (r) => r.algo_id],
    ['version', 'Ver', (r) => r.version],
    ['start', 'Dates', (r) => r.start + r.end],
    ['days', 'Days', (r) => r.days],
    ['prices', 'Prices', (r) => r.option_prices],
    ['trades', 'Trades', (r) => r.totals?.trades],
    ['net', 'Net', (r) => r.totals?.net],
    ['win', 'Win %', (r) => r.totals?.win_rate],
    ['pf', 'PF', (r) => r.totals?.profit_factor],
    ['dd', 'Max DD', (r) => r.totals?.max_drawdown],
    ['time', 'Run time', (r) => r.elapsed_s],
    ['started', 'Started', (r) => r.started_at],
    ['state', 'Status', (r) => r.state],
  ];

  async function renderRuns(el) {
    const f = { q: '', algo: App.prefs.get('bt.filter.algo', ''), prices: App.prefs.get('bt.filter.prices', ''),
      state: '', sort: App.prefs.get('bt.sort', { key: 'started', dir: -1 }) };
    el.innerHTML = header('All runs', VIEWS.runs.sub,
      `<span class="muted small" id="bt-count"></span>
       <button class="btn-icon-box" id="bt-refresh" title="Refresh" aria-label="Refresh"><i class="fa-solid fa-rotate"></i></button>`, 'fa-list')
      + `<div class="filter-row" id="bt-filters"></div><div class="section-card bt-table-card" id="bt-list">
         <div class="skeleton" style="height:160px"></div></div>`;
    el.querySelector('#bt-refresh').addEventListener('click', () => draw(true));

    async function draw(force) {
      let runs;
      try { runs = await loadRuns(force); }
      catch (err) { el.querySelector('#bt-list').innerHTML = App.emptyState('fa-plug-circle-xmark', 'Could not load backtests', App.esc(err.message)); return; }
      const algos = [...new Set(runs.map((r) => r.algo_id).filter(Boolean))].sort();
      const sel = compareSet();
      const q = f.q.toLowerCase();
      const rows = runs.filter((r) => (!q || r.name.toLowerCase().includes(q)) && (!f.algo || r.algo_id === f.algo)
        && (!f.prices || r.option_prices === f.prices) && (!f.state || r.state === f.state));
      const col = COLS.find((c) => c[0] === f.sort.key) || COLS[12];
      rows.sort((a, b) => {
        const x = col[2](a), y = col[2](b);
        if (x === y) return 0;
        if (x === null || x === undefined) return 1;            // blanks last either way
        if (y === null || y === undefined) return -1;
        return (x < y ? -1 : 1) * f.sort.dir;
      });
      const picked = [...sel].filter((n) => runs.some((r) => r.name === n));
      const opt = (v, l, cur) => `<option value="${App.esc(v)}"${v === cur ? ' selected' : ''}>${App.esc(l)}</option>`;
      const filters = el.querySelector('#bt-filters');
      if (!filters.dataset.built) {
        filters.dataset.built = '1';
        filters.innerHTML = `
          <label class="field-label">Search<input class="input-field" id="bt-q" placeholder="name…" /></label>
          <label class="field-label">Algo<select class="input-field" id="bt-algo">${opt('', 'All algos', f.algo)}${algos.map((a) => opt(a, algoName(a), f.algo)).join('')}</select></label>
          <label class="field-label">Prices<select class="input-field" id="bt-prices">${opt('', 'Real and model', f.prices)}${opt('real', 'Real', f.prices)}${opt('model', 'Model', f.prices)}</select></label>
          <label class="field-label">Status<select class="input-field" id="bt-state">${opt('', 'Any', '')}${Object.entries(STATE).map(([k, s]) => opt(k, s[0], '')).join('')}</select></label>
          <span class="bt-filter-gap"></span>
          <button class="btn-secondary" id="bt-clear" title="Clear the compare selection">Clear</button>
          <button class="btn-primary" id="bt-compare"><i class="fa-solid fa-code-compare"></i><span></span></button>`;
        filters.querySelector('#bt-q').addEventListener('input', (e) => { f.q = e.target.value; draw(false); });
        [['algo', 'bt-algo'], ['prices', 'bt-prices'], ['state', 'bt-state']].forEach(([k, id]) =>
          filters.querySelector('#' + id).addEventListener('change', (e) => {
            f[k] = e.target.value;
            if (k !== 'state') App.prefs.set('bt.filter.' + k, f[k]);
            draw(false);
          }));
        filters.querySelector('#bt-compare').addEventListener('click', () => {
          const names = [...compareSet()];
          if (names.length >= 2) location.hash = '#/backtests/compare?runs=' + names.map(encodeURIComponent).join(',');
        });
        filters.querySelector('#bt-clear').addEventListener('click', () => { saveCompare(new Set()); draw(false); });
      }
      const cmp = filters.querySelector('#bt-compare');
      cmp.disabled = picked.length < 2;
      cmp.querySelector('span').textContent = picked.length ? `Compare ${picked.length}` : 'Compare';
      cmp.title = picked.length < 2 ? 'Tick 2–6 runs to compare' : picked.join(', ');
      filters.querySelector('#bt-clear').classList.toggle('hidden', !picked.length);
      el.querySelector('#bt-count').textContent = `${rows.length} of ${runs.length} run${runs.length === 1 ? '' : 's'}`;

      const t = (r) => r.totals || {};
      const arrow = (k) => (f.sort.key === k ? `<i class="fa-solid fa-caret-${f.sort.dir > 0 ? 'up' : 'down'}"></i>` : '');
      el.querySelector('#bt-list').innerHTML = rows.length ? `<div class="table-wrap"><table class="data-table bt-runs">
        <thead><tr><th class="bt-check" title="Tick 2–6 to compare"><i class="fa-solid fa-code-compare"></i></th>
          ${COLS.map(([k, label]) => `<th class="sortable" data-sort="${k}" tabindex="0" aria-sort="${f.sort.key === k ? (f.sort.dir > 0 ? 'ascending' : 'descending') : 'none'}">${label} ${arrow(k)}</th>`).join('')}
          <th></th></tr></thead>
        <tbody>${rows.map((r) => `<tr class="${sel.has(r.name) ? 'picked' : ''}">
          <td class="bt-check"><input type="checkbox" data-pick="${App.esc(r.name)}" aria-label="Compare ${App.esc(r.name)}"
            ${sel.has(r.name) ? 'checked' : ''} ${!sel.has(r.name) && picked.length >= 6 ? 'disabled' : ''} /></td>
          <td><a class="bt-name" href="#/backtests/run/${encodeURIComponent(r.name)}">${App.esc(r.name)}</a>${r.sweep ? ` <a class="tag" href="#/backtests/sweep/${encodeURIComponent(r.sweep.name || '')}" title="Open the sweep">${App.esc(r.sweep.name || 'sweep')}</a>` : ''}</td>
          <td title="${App.esc(algoName(r.algo_id))}"><span class="bt-algo"><i class="fa-solid fa-circle" style="color:${App.colorFor(r.algo_id)}"></i>${App.esc(algoName(r.algo_id))}</span></td>
          <td class="muted">${App.esc(r.version || '—')}</td>
          <td>${App.esc(range(r))}</td><td>${r.days ?? '—'}</td>
          <td>${pricesChip(r.option_prices)}${r.missing_options ? ` <span class="muted small" title="days not run: no saved option candles">−${r.missing_options}</span>` : ''}</td>
          <td>${t(r).trades ?? '—'}</td>
          <td class="${App.tone(t(r).net)}"><b>${r.totals ? App.money(t(r).net) : '—'}</b></td>
          <td>${t(r).win_rate !== null && t(r).win_rate !== undefined ? t(r).win_rate + '%' : '—'}</td>
          <td>${App.num(t(r).profit_factor)}</td>
          <td class="${App.tone(t(r).max_drawdown)}">${r.totals ? App.money(t(r).max_drawdown) : '—'}</td>
          <td>${App.duration(r.elapsed_s)}</td><td class="muted">${stamp(r.started_at)}</td>
          <td><div class="bt-state" title="${App.esc(r.label)}">${stateChip(r)}${active(r) ? `<span class="muted small">${App.esc(r.label)}</span>` : ''}</div>${progress(r)}</td>
          <td class="bt-actions">
            <a class="btn-icon-box" href="#/backtests/new?from=${encodeURIComponent(r.name)}" title="Run again with changes" aria-label="Run ${App.esc(r.name)} again with changes"><i class="fa-solid fa-rotate-right"></i></a>
            <button class="btn-icon-box" data-trash="${App.esc(r.name)}" title="Move to trash" aria-label="Move ${App.esc(r.name)} to trash" ${active(r) ? 'disabled' : ''}><i class="fa-solid fa-trash-can"></i></button>
          </td></tr>`).join('')}</tbody></table></div>`
        : App.emptyState('fa-flask', runs.length ? 'No run matches these filters' : 'No backtests yet',
          runs.length ? 'Clear the search or pick another algo.' : 'Backtests run with <code>algo backtest</code> appear here.');

      const box = el.querySelector('#bt-list');
      box.querySelectorAll('th[data-sort]').forEach((th) => {
        const go = () => {
          const k = th.dataset.sort;
          f.sort = { key: k, dir: f.sort.key === k ? -f.sort.dir : (['name', 'algo', 'version', 'prices', 'state'].includes(k) ? 1 : -1) };
          App.prefs.set('bt.sort', f.sort);
          draw(false);
        };
        th.addEventListener('click', go);
        th.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
      });
      box.querySelectorAll('[data-pick]').forEach((cb) => cb.addEventListener('change', () => {
        const s = compareSet();
        if (cb.checked) s.add(cb.dataset.pick); else s.delete(cb.dataset.pick);
        saveCompare(s);
        draw(false);
      }));
      box.querySelectorAll('[data-trash]').forEach((b) => b.addEventListener('click', () => trash(b.dataset.trash, () => draw(true))));

      stopPoll();
      if (runs.some(active)) S.timer = setInterval(() => draw(true), POLL_MS);
    }
    await draw(true);
  }

  async function trash(name, after) {
    if (!confirm(`Move "${name}" to the trash?\n\nThe folder goes to data/backtests/.trash/ and can be moved back by hand. Nothing is deleted.`)) return;
    try {
      const out = await App.api(`backtests/${encodeURIComponent(name)}/trash`, {}, { method: 'POST' });
      const s = compareSet(); s.delete(name); saveCompare(s);
      App.toast(`Moved to ${out.to}`);
      S.runs = null;
      after();
    } catch (err) { App.toast('Could not move it: ' + err.message, 'error'); }
  }

  // ── one run ──────────────────────────────────────────────────────────────

  async function renderRun(el, name, tab) {
    el.innerHTML = '<div class="section-card"><div class="skeleton" style="height:160px"></div></div>';
    let d;
    try { d = await App.api(`backtests/${encodeURIComponent(name)}`); }
    catch (err) {
      el.innerHTML = header(App.esc(name), '') + App.emptyState('fa-circle-question', 'No such backtest',
        `Nothing called <code>${App.esc(name)}</code> in data/backtests. <a href="#/backtests/runs">All runs</a>`);
      return;
    }
    loadRuns().catch(() => {});
    const base = `#/backtests/run/${encodeURIComponent(name)}`;
    const inCompare = compareSet().has(name);
    const algo = algoName(d.algo_id);
    el.innerHTML = `
      <div class="page-header bt-head">
        <div>
          <div class="page-title"><i class="fa-solid fa-circle" style="color:${App.colorFor(d.algo_id)};font-size:0.8rem"></i>${App.esc(name)}</div>
          <div class="page-subtitle">${App.esc(algo)} · v${App.esc(d.version || '?')} · ${App.esc(range(d))} (${d.days ?? '?'} trading days)
            · started ${stamp(d.started_at)}${d.elapsed_s ? ` · took ${App.duration(d.elapsed_s)}` : ''}</div>
          <div class="chips chips-left" style="margin-top:8px" id="bt-run-chips">${stateChip(d)}${pricesChip(d.option_prices)}
            ${Object.keys(flatten(d.overrides)).length ? App.chip(`${Object.keys(flatten(d.overrides)).length} setting(s) changed`, 'tone-violet') : App.chip('live config', 'tone-slate')}
            ${d.sweep ? `<a href="#/backtests/sweep/${encodeURIComponent(d.sweep.name || '')}" class="chip-link">${App.chip(`sweep ${App.esc(d.sweep.name || '')}`, 'tone-violet')}</a>` : ''}</div>
          <div id="bt-run-progress" style="margin-top:8px">${active(d) ? `${progress(d)}<span class="muted small">${App.esc(d.label)}</span>` : ''}</div>
        </div>
        <div class="header-meta">
          <a class="btn-secondary" href="#/backtests/new?from=${encodeURIComponent(name)}"><i class="fa-solid fa-rotate-right"></i>Run again with changes</a>
          ${d.state === 'queued' ? '<button class="btn-secondary" id="bt-stop"><i class="fa-solid fa-xmark"></i>Cancel</button>' : ''}
          ${d.state === 'running' && d.request ? '<button class="btn-danger" id="bt-stop"><i class="fa-solid fa-stop"></i>Stop</button>' : ''}
          <button class="btn-secondary" id="bt-cmp"><i class="fa-solid fa-code-compare"></i>${inCompare ? 'In compare' : 'Add to compare'}</button>
          <button class="btn-icon-box" id="bt-trash" title="Move to trash" aria-label="Move to trash" ${active(d) ? 'disabled' : ''}><i class="fa-solid fa-trash-can"></i></button>
        </div>
      </div>
      <nav class="tabs" aria-label="Backtest sections">
        ${RUN_TABS.map(([k, label, icon]) => `<a class="tab${k === tab ? ' active' : ''}" href="${base}/${k}"><i class="fa-solid ${icon}"></i>${label}</a>`).join('')}
      </nav>
      <div id="bt-run-body"></div>`;
    el.querySelector('#bt-cmp').addEventListener('click', (e) => {
      const s = compareSet();
      if (s.has(name)) s.delete(name); else s.add(name);
      saveCompare(s);
      const n = s.size;
      e.currentTarget.innerHTML = `<i class="fa-solid fa-code-compare"></i>${s.has(name) ? 'In compare' : 'Add to compare'}`;
      App.toast(s.has(name) ? `Added to compare (${n} picked${n >= 2 ? ': open Compare from All runs' : ''})` : 'Removed from compare');
    });
    el.querySelector('#bt-trash').addEventListener('click', () => trash(name, () => { location.hash = '#/backtests/runs'; }));
    el.querySelector('#bt-stop')?.addEventListener('click', async () => {
      const queued = d.state === 'queued';
      if (!confirm(queued ? `Cancel "${name}" before it starts?` : `Stop "${name}"?\n\nThis ends the backtest process only (never an algo). The days run so far stay in the folder.`)) return;
      try {
        await App.api(`backtests/${encodeURIComponent(name)}/stop`, {}, { method: 'POST' });
        App.toast(queued ? 'Cancelled' : 'Stopped');
        S.runs = null;
        stopPoll();
        App.navigate(location.hash);
      } catch (err) { App.toast('Could not stop it: ' + err.message, 'error'); }
    });

    const body = el.querySelector('#bt-run-body');
    const fn = { summary: runSummary, trades: runTrades, days: runDays, settings: runSettings, log: runLog }[tab];
    try { await fn(body, d); }
    catch (err) { console.error(err); body.innerHTML = App.emptyState('fa-plug-circle-xmark', 'Could not load this tab', App.esc(err.message)); }

    if (active(d)) {                       // follow the run; re-render once it finishes
      S.timer = setInterval(async () => {
        let now;
        try { now = await App.api(`backtests/${encodeURIComponent(name)}`); } catch { return; }
        if (now.state !== d.state) { stopPoll(); S.runs = null; App.navigate(location.hash); return; }
        const p = document.getElementById('bt-run-progress');
        if (p) p.innerHTML = `${progress(now)}<span class="muted small">${App.esc(now.label)}</span>`;
        if (tab === 'log') runLog(body, now).catch(() => {});        // follow the log while it runs
      }, POLL_MS);
    }
  }

  function notReady(d) {
    if (d.state === 'failed') return App.emptyState('fa-triangle-exclamation', 'This backtest failed', App.esc(d.status?.error || d.label),
      '<p><a href="#" data-tab-log>See its log</a></p>');
    if (d.state === 'stale') return App.emptyState('fa-hourglass-end', 'This backtest stopped before the end',
      `It ${App.esc(d.label)} and never wrote its summary. The days it ran are in the Trades and Days tabs.`);
    return App.emptyState('fa-hourglass-half', active(d) ? 'Still running' : 'No summary', active(d)
      ? `${App.esc(d.label)}. The summary appears when the last day is done.` : 'This folder has no summary.json.');
  }

  // ── Summary tab ──

  async function runSummary(body, d) {
    const sm = d.summary;
    if (!sm) { body.innerHTML = notReady(d); bindLogLink(body, d); return; }
    const perf = await App.api(`backtests/${encodeURIComponent(d.name)}/performance`).catch(() => null);
    App.destroyCharts();
    const t = sm.totals || {};
    const cov = sm.coverage || {};
    const warn = [];
    if (d.option_prices !== 'real' && SELLERS.test(d.algo_id || '')) warn.push(`<div class="banner banner-warn"><i class="fa-solid fa-triangle-exclamation"></i>
      Modelled option prices are biased against option sellers. Don't trust these rupee results for a condor: run it on real prices.</div>`);
    if ((cov.missing_options || []).length) warn.push(`<div class="banner"><i class="fa-solid fa-circle-info"></i>
      ${cov.missing_options.length} day(s) not run: the nearest expiry's option candles weren't saved (${cov.missing_options.slice(0, 6).map((x) => shortDate(x)).join(', ')}${cov.missing_options.length > 6 ? ', …' : ''}).</div>`);
    const gt = App.strategyEmbed.groupTable;
    const sideRows = Object.entries(sm.by_side || {}).map(([key, v]) => ({ key, ...v }));
    body.innerHTML = `${warn.join('')}
      <div class="kpi-strip">
        ${App.kpi('Net P&amp;L', App.money(t.net), App.tone(t.net), 'fa-indian-rupee-sign', 'tone-green',
          t.gross !== undefined ? `gross ${App.money(t.gross)} · charges ${App.money(t.charges, { sign: false })}` : 'after charges')}
        ${App.kpi('Trades', t.trades ?? '—', '', 'fa-right-left', 'tone-slate', `${t.days_traded ?? '?'} days traded · ${t.days_skipped ?? '?'} skipped`)}
        ${App.kpi('Win rate', t.win_rate !== null && t.win_rate !== undefined ? t.win_rate + '%' : '—', '', 'fa-bullseye', 'tone-blue', `${t.wins ?? 0} won · ${t.losses ?? 0} lost`)}
        ${App.kpi('Profit factor', App.num(t.profit_factor), '', 'fa-divide', 'tone-amber', 'gross wins ÷ gross losses')}
        ${App.kpi('Expectancy', App.money(t.expectancy), App.tone(t.expectancy), 'fa-scale-balanced', 'tone-violet',
          `avg win ${App.money(t.avg_win)} · avg loss ${App.money(t.avg_loss)}`)}
        ${App.kpi('Max drawdown', App.money(t.max_drawdown), App.tone(t.max_drawdown), 'fa-arrow-trend-down', 'tone-red',
          `best day ${App.money(t.best_day)} · worst ${App.money(t.worst_day)}`)}
      </div>
      <div class="row"><div class="section-card"><div class="card-head"><div><div class="section-title">Equity curve</div>
        <div class="section-subtitle">Cumulative net (line), each day's net (bars) and drawdown from the running peak (shaded)</div></div></div>
        <div class="chart-box"><canvas id="bt-equity"></canvas></div></div></div>
      <div class="row"><div class="section-card"><div class="card-head"><div><div class="section-title">By month</div>
        <div class="section-subtitle">Net P&amp;L per month; hover for trades and win rate</div></div></div>${heatmap(sm.by_month || {})}</div></div>
      ${perf ? `<div class="row row-1-1">
        ${gt('By days to expiry', 'Which DTE days pay', perf.by_dte, 'DTE')}
        ${gt('By weekday', '', perf.by_weekday, 'Day')}
      </div>
      <div class="row row-1-1">
        ${gt('By exit reason', '', perf.by_exit, 'Exit', App.reason)}
        ${gt('By entry time', '15-minute buckets', perf.by_entry_time, 'Entry')}
      </div>` : ''}
      <div class="row row-1-1">
        ${sideRows.length > 1 ? gt('By side', 'CE / PE, or the structure', sideRows, 'Side') : ''}
        ${skipsCard(sm.skips || {})}
      </div>
      <div class="row"><div class="section-card"><div class="card-head"><div><div class="section-title">Data and limits</div>
        <div class="section-subtitle">What this backtest could and couldn't see (docs/design/backtest.md, section 4)</div></div></div>
        <div class="bt-facts">
          ${fact('Option prices', d.option_prices === 'real' ? 'real 1-minute candles' : 'modelled (Black-76)')}
          ${fact('Days with data', `${cov.days ?? '?'}`)}${fact('Not run: no option candles', (cov.missing_options || []).length)}
          ${fact('Not run: no index data', (cov.missing_index || []).length)}
          ${cov.with_futures !== undefined ? fact('Days with futures', cov.with_futures) : ''}
          ${cov.with_vix !== undefined ? fact('Days with VIX', cov.with_vix) : ''}
          ${cov.stocks_max ? fact('Stocks per day', `${cov.stocks_min}–${cov.stocks_max}`) : ''}
        </div>
        <ul class="bt-limits">${(sm.limits || []).map((l) => `<li>${App.esc(l)}</li>`).join('')}</ul></div></div>`;
    drawEquity(sm.equity || [], d.algo_id);
  }

  const fact = (label, value) => `<div><div class="fact-label">${label}</div><div class="fact-value">${App.esc(String(value))}</div></div>`;

  function skipsCard(skips) {
    const rows = Object.entries(skips);
    return `<div class="section-card"><div class="card-head"><div><div class="section-title">Days without a trade</div>
      <div class="section-subtitle">Why the algo stayed out</div></div></div>
      ${rows.length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>Reason</th><th>Days</th></tr></thead>
        <tbody>${rows.map(([k, n]) => `<tr><td class="bt-wrap">${App.esc(k)}</td><td>${n}</td></tr>`).join('')}</tbody></table></div>`
        : '<div class="chart-empty" style="height:80px">Every day traded</div>'}</div>`;
  }

  function heatmap(byMonth) {
    const months = Object.keys(byMonth).sort();
    if (!months.length) return '<div class="chart-empty" style="height:80px">No closed trades</div>';
    const years = [...new Set(months.map((mo) => mo.slice(0, 4)))];
    const max = Math.max(...months.map((mo) => Math.abs(byMonth[mo].net || 0)), 1);
    const cell = (mo) => {
      const v = byMonth[mo];
      if (!v) return '<td class="hm-empty"></td>';
      const a = 0.12 + (0.6 * Math.abs(v.net || 0)) / max;
      const bg = v.net > 0 ? `rgba(16,185,129,${a.toFixed(2)})` : v.net < 0 ? `rgba(239,68,68,${a.toFixed(2)})` : 'transparent';
      return `<td style="background:${bg}" title="${mo}: ${v.trades} trades · ${v.win_rate ?? '—'}% won · ${App.money(v.net)}">${App.money(v.net)}</td>`;
    };
    const total = (y) => months.filter((mo) => mo.startsWith(y)).reduce((a, mo) => a + (byMonth[mo].net || 0), 0);
    return `<div class="table-wrap"><table class="data-table bt-heatmap"><thead><tr><th>Year</th>${MONTHS.map((m) => `<th>${m}</th>`).join('')}<th>Year</th></tr></thead>
      <tbody>${years.map((y) => `<tr><td><b>${y}</b></td>${MONTHS.map((_, i) => cell(`${y}-${String(i + 1).padStart(2, '0')}`)).join('')}
        <td class="${App.tone(total(y))}"><b>${App.money(total(y))}</b></td></tr>`).join('')}</tbody></table></div>`;
  }

  function drawEquity(equity, algoId) {
    const canvas = document.getElementById('bt-equity');
    if (!canvas) return;
    if (!equity.length) { canvas.parentElement.innerHTML = '<div class="chart-empty">No days run</div>'; return; }
    const color = App.colorFor(algoId);
    App.chart(canvas, {
      data: { labels: equity.map((e) => e.date), datasets: [
        { type: 'bar', label: 'Day net', data: equity.map((e) => e.net), backgroundColor: equity.map((e) => (e.net < 0 ? '#ef4444aa' : color + 'aa')),
          borderRadius: 2, maxBarThickness: 14 },
        { type: 'line', label: 'Cumulative', data: equity.map((e) => e.cum), borderColor: '#0f172a', borderWidth: 2,
          pointRadius: equity.length > 60 ? 0 : 2, tension: 0 },
        { type: 'line', label: 'Drawdown', data: equity.map((e) => e.drawdown), borderColor: 'rgba(239,68,68,0.6)', borderWidth: 1, pointRadius: 0,
          fill: { target: { value: 0 }, below: 'rgba(239,68,68,0.12)' } },
      ] },
      options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
        scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } },
          y: { grid: { color: (c) => (c.tick && c.tick.value === 0 ? '#cbd5e1' : '#f1f5f9') }, ticks: { callback: (v) => App.money(v) } } },
        plugins: { legend: { display: true, position: 'bottom', labels: { boxWidth: 10 } },
          tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${App.money(c.parsed.y)}` } } } },
    });
  }

  // ── Trades tab: the strategy page's trade log and trade detail on this run's database ──

  async function runTrades(body, d) {
    if (!d.config_snapshot && !d.summary) { body.innerHTML = notReady(d); bindLogLink(body, d); return; }
    body.innerHTML = `<div class="top-bar"><div class="top-bar-group muted small">All trades of this backtest. Click one for its legs, fills and payoff.</div>
      <div class="top-bar-group" id="sp-controls"></div></div><div id="sp-body"></div>`;
    const api = `backtests/${encodeURIComponent(d.name)}/`;
    App.strategyEmbed.use({ algoId: d.algo_id, name: `${algoName(d.algo_id)} · ${d.name}`, apiBase: api, posBase: api,
      csvName: d.name, start: d.start, end: d.end });
    App.strategyEmbed.show('trades');
  }

  // ── Days tab ──

  async function runDays(body, d) {
    const data = await App.api(`backtests/${encodeURIComponent(d.name)}/days`);
    const all = data.days;
    if (!all.length) { body.innerHTML = notReady(d); bindLogLink(body, d); return; }
    const api = `backtests/${encodeURIComponent(d.name)}/`;
    App.strategyEmbed.use({ algoId: d.algo_id, name: `${algoName(d.algo_id)} · ${d.name}`, apiBase: api, posBase: api,
      csvName: d.name, start: d.start, end: d.end });
    let filter = 'all';
    const count = (fn) => all.filter(fn).length;
    const kinds = {
      all: ['All', () => true], traded: ['Traded', (x) => x.trades > 0], skipped: ['Skipped', (x) => !x.trades && x.status !== 'NOT_RUN'],
      not_run: ['Not run', (x) => x.status === 'NOT_RUN'],
    };
    const draw = () => {
      const rows = all.filter(kinds[filter][1]);
      body.innerHTML = `<div class="filter-row"><div class="segmented" id="bt-days-filter">${Object.entries(kinds).map(([k, [l, fn]]) =>
          `<button data-v="${k}" class="${k === filter ? 'active' : ''}">${l} <span class="muted">${count(fn)}</span></button>`).join('')}</div>
        <span class="muted small">Click a day for its checks, events and trades.</span></div>
        <div class="section-card"><div class="table-wrap"><table class="data-table clickable bt-days">
          <thead><tr><th>Date</th><th>DTE</th><th>Result</th><th>Trades</th><th>Exit</th><th>Net</th><th>Why no trade</th></tr></thead>
          <tbody>${rows.map((x) => `<tr data-day="${x.date}" tabindex="0" ${x.run_id ? '' : 'class="bt-notrun"'}>
            <td>${x.date} <span class="muted">${x.weekday}</span></td><td>${x.dte ?? '—'}</td>
            <td>${x.status === 'NOT_RUN' ? App.chip('Not run', 'tone-slate') : x.trades ? App.chip(x.net > 0 ? 'Win' : 'Loss', x.net > 0 ? 'tone-green' : 'tone-red') : App.chip('Skipped', 'tone-slate')}</td>
            <td>${x.trades || '—'}</td><td>${x.exit ? App.esc(App.reason(x.exit)) : '—'}</td>
            <td class="${App.tone(x.net)}">${x.net !== null ? `<b>${App.money(x.net)}</b>` : '—'}</td>
            <td class="bt-wrap muted">${App.esc(x.skip_reason || '')}</td></tr>`).join('')}</tbody></table></div></div>`;
      body.querySelector('#bt-days-filter').addEventListener('click', (e) => {
        const b = e.target.closest('button'); if (b) { filter = b.dataset.v; draw(); }
      });
      body.querySelectorAll('tr[data-day]').forEach((tr) => {
        const open = () => toggleDay(tr, d);
        tr.addEventListener('click', open);
        tr.addEventListener('keydown', (e) => { if (e.key === 'Enter') open(); });
      });
    };
    draw();
  }

  async function toggleDay(tr, d) {
    const next = tr.nextElementSibling;
    if (next?.classList.contains('bt-day-detail')) { next.remove(); tr.classList.remove('open'); return; }
    if (tr.classList.contains('bt-notrun')) return;
    tr.classList.add('open');
    const row = document.createElement('tr');
    row.className = 'bt-day-detail';
    row.innerHTML = `<td colspan="7"><div class="skeleton" style="height:80px"></div></td>`;
    tr.after(row);
    try {
      const j = await App.api(`backtests/${encodeURIComponent(d.name)}/journal`, { start: tr.dataset.day, end: tr.dataset.day });
      const cell = row.querySelector('td');
      cell.innerHTML = j.days.length ? App.strategyEmbed.journalDay(j.days[0]) : '<div class="muted small">Nothing recorded on this day.</div>';
      App.strategyEmbed.bindDetails(cell);
    } catch (err) { row.querySelector('td').innerHTML = `<div class="neg small">Could not load the day: ${App.esc(err.message)}</div>`; }
  }

  // ── Settings tab ──

  function runSettings(body, d) {
    const used = flatten(d.config_snapshot || {});
    const live = flatten(d.live_config || {});
    const changed = flatten(d.overrides || {});
    const fixed = flatten(d.backtest_overrides || {});
    const keys = [...new Set([...Object.keys(used), ...Object.keys(live)])].sort();
    const diff = keys.filter((k) => !same(used[k], live[k]));
    let onlyDiff = App.prefs.get('bt.settings.onlyDiff', true);
    const list = (obj, empty) => (Object.keys(obj).length ? `<table class="data-table"><tbody>${Object.entries(obj).map(([k, v]) =>
      `<tr><td class="mono">${App.esc(k)}</td><td class="mono">${App.esc(show(v))}</td></tr>`).join('')}</tbody></table>` : `<div class="muted small">${empty}</div>`);
    const draw = () => {
      const rows = (onlyDiff ? diff : keys);
      body.innerHTML = `<div class="row row-1-1">
        <div class="section-card"><div class="card-head"><div><div class="section-title">Changed for this run</div>
          <div class="section-subtitle">The overrides it was started with, on top of config.yaml</div></div></div>${list(changed, 'None: the config as it was that day.')}</div>
        <div class="section-card"><div class="card-head"><div><div class="section-title">Always applied in backtests</div>
          <div class="section-subtitle">The algo's BACKTEST_OVERRIDES: signals history can't show</div></div></div>${list(fixed, 'None for this algo.')}</div>
      </div>
      <div class="section-card"><div class="card-head"><div><div class="section-title">Config used vs today's live config</div>
        <div class="section-subtitle">${d.config_snapshot ? `${diff.length} setting(s) differ from <code>src/algos/${App.esc(d.algo_id)}/config.yaml</code> today` : 'This run has no recorded config (no day was run).'}
          ${d.live_config_error ? ` · live config can't be read: ${App.esc(d.live_config_error)}` : ''}</div></div>
        <label class="bt-toggle"><input type="checkbox" id="bt-only-diff" ${onlyDiff ? 'checked' : ''} /> Only differences</label></div>
        ${rows.length ? `<div class="table-wrap"><table class="data-table rules-table"><thead><tr><th>Setting</th><th>This run</th><th>Live now</th></tr></thead>
          <tbody>${rows.map((k) => `<tr class="${same(used[k], live[k]) ? '' : 'changed'}"><td class="mono">${App.esc(k)}${k in changed || Object.keys(changed).some((c) => c.startsWith(k + '.')) ? ' <span class="tag">override</span>' : ''}</td>
            <td class="mono">${k in used ? App.esc(show(used[k])) : '<i class="muted">not set</i>'}</td><td class="mono muted">${k in live ? App.esc(show(live[k])) : '<i>not set</i>'}</td></tr>`).join('')}</tbody></table></div>`
          : '<div class="chart-empty" style="height:80px">Same as the live config</div>'}</div>`;
      body.querySelector('#bt-only-diff').addEventListener('change', (e) => { onlyDiff = e.target.checked; App.prefs.set('bt.settings.onlyDiff', onlyDiff); draw(); });
    };
    draw();
  }

  // ── Log tab ──

  async function runLog(body, d) {
    const log = await App.api(`backtests/${encodeURIComponent(d.name)}/log`, { lines: 400 });
    const err = d.status?.error ? `<div class="banner banner-warn"><i class="fa-solid fa-triangle-exclamation"></i>${App.esc(d.status.error)}</div>` : '';
    if (!log.available) {
      body.innerHTML = err + (d.state === 'queued' ? App.emptyState('fa-hourglass-start', 'Waiting to start', `${App.esc(d.label)}. Its output appears here once it starts.`)
        : d.request ? App.emptyState('fa-terminal', 'No log for this run', 'It never started, so nothing was written.')
          : App.emptyState('fa-terminal', 'No log for this run',
            'It was started from the terminal, so its output went there. Runs started from the dashboard keep a <code>run.log</code> in their folder.'));
      return;
    }
    const old = body.querySelector('.bt-log');
    const atEnd = !old || old.scrollTop + old.clientHeight >= old.scrollHeight - 8;
    body.innerHTML = `${err}<div class="section-card"><div class="card-head"><div><div class="section-title">run.log</div>
      <div class="section-subtitle">Last ${log.lines.length} lines${active(d) ? ' · updating every 2 s' : ''}</div></div></div>
      <pre class="bt-log">${App.esc(log.lines.join('\n'))}</pre></div>`;
    const pre = body.querySelector('.bt-log');
    if (atEnd) pre.scrollTop = pre.scrollHeight;
  }

  function bindLogLink(body, d) {
    body.querySelector('[data-tab-log]')?.addEventListener('click', (e) => {
      e.preventDefault(); location.hash = `#/backtests/run/${encodeURIComponent(d.name)}/log`;
    });
  }

  App.page('backtests', { title, render, destroy() { stopPoll(); App.backtestSweep?.destroy(); App.backtestData?.destroy(); App.strategyEmbed?.close(); } });
})();
