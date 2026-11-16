/* Parameter sweep: #/backtests/sweep (form) and #/backtests/sweep/<sweep> (results)
   (docs/design/backtest.md, section 6.5). The page picks 1–2 settings and their values, builds the
   grid here (at most 20 runs), checks every combination with a dry run, then queues them as
   <sweep>_01 … <sweep>_NN. The server's queue runs them one after another; each run's request.json
   records its point in the grid, which is all the results page needs. */

(() => {
  const MAX_RUNS = 20;
  const KPIS = {
    net: ['Net P&L', (t) => t.net, (v) => App.money(v)],
    profit_factor: ['Profit factor', (t) => t.profit_factor, (v) => App.num(v)],
    expectancy: ['Expectancy / trade', (t) => t.expectancy, (v) => App.money(v)],
    win_rate: ['Win rate', (t) => t.win_rate, (v) => (v === null || v === undefined ? '—' : v + '%')],
    max_drawdown: ['Max drawdown', (t) => t.max_drawdown, (v) => App.money(v)],
  };
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const shortDate = (iso) => { const [y, m, d] = iso.split('-').map(Number); return `${d} ${MONTHS[m - 1]} ${String(y).slice(2)}`; };
  const active = (r) => r.state === 'running' || r.state === 'queued';
  const H = () => App.backtestForm;
  let W = null;
  let timer = null;
  const stopPoll = () => { if (timer) clearInterval(timer); timer = null; };

  async function render(el, args, query) {
    stopPoll();
    if (args[0]) return results(el, args[0]);
    return form(el, query);
  }

  // ── which settings can be swept ──
  function sweepable(cfg, ndte) {
    const out = [];
    H().buildGroups(cfg).forEach((g) => g.fields.forEach((f) => {
      const group = g.title === 'General' ? '' : g.title + ' · ';
      if (['int', 'num', 'time', 'select', 'bool'].includes(f.kind)) out.push({ path: f.path, kind: f.kind, enum: f.s.enum, text: group + H().label(f.key), inYaml: f.inYaml });
      else if (f.kind === 'dte') for (let d = 0; d < ndte; d++) out.push({ path: `${f.path}[${d}]`, kind: 'num', text: `${group}${H().label(f.key)} at DTE ${d}`, inYaml: f.inYaml });
    }));
    return out.sort((a, b) => b.inYaml - a.inYaml);
  }
  const splitDte = (path) => { const m = path.match(/^(.*)\[(\d+)\]$/); return m ? [m[1], Number(m[2])] : [path, null]; };
  function valueAt(values, path) {
    const [p, d] = splitDte(path);
    const v = H().get(values, p);
    return d === null ? v : H().forDte(v, d);
  }
  function parseValues(text, opt) {
    const parts = String(text || '').split(/[,\s]+/).map((s) => s.trim()).filter(Boolean);
    const out = [], bad = [];
    parts.forEach((s) => {
      let v;
      if (opt.kind === 'int') v = /^-?\d+$/.test(s) ? Number(s) : NaN;
      else if (opt.kind === 'num') v = Number(s);
      else if (opt.kind === 'time') v = /^\d{1,2}:\d{2}$/.test(s) ? s.padStart(5, '0') : NaN;
      else if (opt.kind === 'bool') v = /^(true|on|yes|1)$/i.test(s) ? true : /^(false|off|no|0)$/i.test(s) ? false : NaN;
      else if (opt.kind === 'select') v = opt.enum.includes(s) ? s : NaN;
      if (typeof v === 'number' && Number.isNaN(v)) bad.push(s);
      else if (!out.some((x) => x === v)) out.push(v);
    });
    return { values: out, bad };
  }
  const showVal = (v) => (v === null || v === undefined ? '—' : typeof v === 'boolean' ? (v ? 'on' : 'off') : String(v));

  // ── form ─────────────────────────────────────────────────────────────────

  async function form(el, query) {
    el.innerHTML = '<div class="section-card"><div class="skeleton" style="height:200px"></div></div>';
    const [days, runs] = await Promise.all([App.api('data/days'), App.api('backtests').then((r) => r.runs)]);
    const strategies = (App.meta?.strategies || []).filter((a) => !a.retired && !a.error);
    const algo = query?.get('algo') || App.prefs.get('bt.new.algo', 'iron_condor_balanced');
    const real = days.real_option_days;
    W = { el, days, runs, strategies, real: new Set(real), trading: days.trading_days,
      algo: strategies.some((a) => a.algo_id === algo) ? algo : strategies[0]?.algo_id,
      prices: 'real', start: real[0] || days.last_day, end: real[real.length - 1] || days.last_day,
      base: '', axes: [{ path: '', text: '' }], nameEdited: false, name: '', startNow: false };
    await loadAlgo();
  }

  async function loadAlgo() {
    W.cfg = await App.api(`algos/${W.algo}/config`);
    const twd = Number(W.cfg.values.trade_window_days);
    W.ndte = twd > 0 ? Math.min(twd, 7) : 5;
    W.options = sweepable(W.cfg, W.ndte);
    W.axes = [{ path: '', text: '' }];
    W.base = '';
    draw();
  }

  const baseOverrides = () => (W.base ? (W.runs.find((r) => r.name === W.base)?.overrides || {}) : {});
  function effective() {                                // live config with the base run's changes
    const v = H().clone(W.cfg.values);
    const walk = (o, prefix) => Object.entries(o).forEach(([k, x]) => {
      const path = prefix + k;
      if (x && typeof x === 'object' && !Array.isArray(x) && !H().isDte(k)) walk(x, path + '.');
      else if (H().get(W.cfg.values, path) !== undefined) H().set(v, path, H().isDte(k) ? { ...(H().get(v, path) || {}), ...x } : x);
    });
    walk(baseOverrides(), '');
    return v;
  }

  function draw() {
    const el = W.el;
    const T = W.trading;
    const last = W.days.last_day || W.end;
    const bases = W.runs.filter((r) => r.algo_id === W.algo && r.state === 'done' && Object.keys(r.overrides || {}).length);
    const sweeps = groupSweeps(W.runs);
    el.innerHTML = `
      <div class="page-header"><div><div class="page-title"><i class="fa-solid fa-table-cells"></i>Parameter sweep</div>
        <div class="page-subtitle">Pick 1–2 settings and the values to try. The page builds the grid (up to ${MAX_RUNS} runs) and the queue runs them one after another.</div></div></div>
      <div class="bt-new">
        <div class="bt-new-main">
          <div class="section-card bt-new-card">
            <div class="bt-row">
              <label class="field-label">Strategy<select class="input-field" id="sw-algo">${W.strategies.map((s) =>
                `<option value="${App.esc(s.algo_id)}"${s.algo_id === W.algo ? ' selected' : ''}>${App.esc(s.name)}</option>`).join('')}</select></label>
              <label class="field-label">Start from<select class="input-field" id="sw-base"><option value="">Today's live config</option>
                ${bases.map((r) => `<option value="${App.esc(r.name)}"${r.name === W.base ? ' selected' : ''}>${App.esc(r.name)} (its changes)</option>`).join('')}</select></label>
            </div>
            <div class="bt-row" style="margin-top:12px">
              <label class="field-label">From<input type="date" class="input-field" id="sw-start" value="${W.start}" min="${T[0] || ''}" max="${last}" /></label>
              <label class="field-label">To<input type="date" class="input-field" id="sw-end" value="${W.end}" min="${T[0] || ''}" max="${last}" /></label>
              <div class="segmented" id="sw-prices" role="group" aria-label="Option prices">
                <button data-v="real" class="${W.prices === 'real' ? 'active' : ''}">Real candles</button>
                <button data-v="model" class="${W.prices === 'model' ? 'active' : ''}">Model</button></div>
              ${W.days.real_option_days.length ? '<button class="btn-secondary btn-sm" id="sw-realdays">Real-price days</button>' : ''}
            </div>
            <div class="small" id="sw-days" style="margin-top:8px"></div>
          </div>

          <div class="section-card bt-new-card">
            <div class="section-title" style="margin-bottom:10px">Settings to sweep</div>
            <div id="sw-axes"></div>
          </div>

          <div class="section-card bt-new-card">
            <div class="card-head"><div><div class="section-title">The grid</div>
              <div class="section-subtitle" id="sw-grid-sub"></div></div></div>
            <div id="sw-grid"></div>
          </div>
        </div>

        <aside class="bt-new-side">
          <div class="section-card bt-new-card">
            <label class="field-label">Sweep name<input class="input-field" id="sw-name" maxlength="70" spellcheck="false" /></label>
            <div class="small" id="sw-name-msg"></div>
            <div id="sw-when"></div>
            <button class="btn-primary bt-run-btn" id="sw-run"><i class="fa-solid fa-play"></i><span>Queue the sweep</span></button>
            <div class="muted small" id="sw-est"></div>
          </div>
          <div class="section-card bt-new-card">
            <div class="section-title" style="margin-bottom:8px">Earlier sweeps</div>
            ${sweeps.length ? `<ul class="sw-list">${sweeps.slice(0, 12).map((s) => `<li><a href="#/backtests/sweep/${encodeURIComponent(s.name)}">${App.esc(s.name)}</a>
              <span class="muted small">${s.done}/${s.total} done${s.active ? ' · running' : ''}</span></li>`).join('')}</ul>` : '<div class="muted small">None yet.</div>'}
          </div>
        </aside>
      </div>`;
    el.querySelector('#sw-algo').addEventListener('change', (e) => { W.algo = e.target.value; W.nameEdited = false; loadAlgo(); });
    el.querySelector('#sw-base').addEventListener('change', (e) => { W.base = e.target.value; drawAxes(); refresh(); });
    el.querySelector('#sw-start').addEventListener('change', (e) => { if (e.target.value) { W.start = e.target.value; refresh(); } });
    el.querySelector('#sw-end').addEventListener('change', (e) => { if (e.target.value) { W.end = e.target.value; refresh(); } });
    el.querySelector('#sw-realdays')?.addEventListener('click', () => {
      const r = W.days.real_option_days; W.start = r[0]; W.end = r[r.length - 1];
      el.querySelector('#sw-start').value = W.start; el.querySelector('#sw-end').value = W.end; refresh();
    });
    el.querySelector('#sw-prices').addEventListener('click', (e) => {
      const b = e.target.closest('button'); if (!b) return;
      W.prices = b.dataset.v;
      el.querySelectorAll('#sw-prices button').forEach((x) => x.classList.toggle('active', x === b));
      refresh();
    });
    const name = el.querySelector('#sw-name');
    name.addEventListener('input', () => { W.nameEdited = !!name.value; W.name = name.value; refresh(true); });
    el.querySelector('#sw-run').addEventListener('click', run);
    drawAxes();
    refresh();
  }

  function drawAxes() {
    const box = W.el.querySelector('#sw-axes');
    const eff = effective();
    box.innerHTML = W.axes.map((a, i) => {
      const opt = W.options.find((o) => o.path === a.path);
      const cur = opt ? H().normTime(valueAt(eff, opt.path)) : undefined;
      const numeric = opt && (opt.kind === 'int' || opt.kind === 'num');
      return `<div class="sw-axis" data-i="${i}">
        <div class="bt-row">
          <label class="field-label">Setting ${i + 1}<select class="input-field" data-axis-path>
            <option value="">Pick a setting…</option>
            ${W.options.map((o) => `<option value="${App.esc(o.path)}"${o.path === a.path ? ' selected' : ''}${W.axes.some((x, j) => j !== i && x.path === o.path) ? ' disabled' : ''}>${App.esc(o.text)}</option>`).join('')}
          </select></label>
          <label class="field-label sw-values">Values<input class="input-field" data-axis-values value="${App.esc(a.text)}"
            placeholder="${opt ? (opt.kind === 'time' ? '09:45, 09:50, 09:53' : opt.kind === 'select' ? opt.enum.join(', ') : opt.kind === 'bool' ? 'on, off' : 'e.g. 10, 12, 15') : 'pick a setting first'}" ${opt ? '' : 'disabled'} /></label>
          ${i > 0 ? `<button class="btn-icon-box" data-axis-remove title="Remove this setting" aria-label="Remove setting ${i + 1}"><i class="fa-solid fa-xmark"></i></button>` : ''}
        </div>
        ${opt ? `<div class="small muted sw-axis-foot">Now <b>${App.esc(showVal(cur))}</b> <span class="mono">${App.esc(opt.path)}</span>
          ${numeric ? `<span class="sw-range">· fill from <input type="number" step="any" class="input-field" data-r="from" aria-label="From" />
            to <input type="number" step="any" class="input-field" data-r="to" aria-label="To" /> step <input type="number" step="any" class="input-field" data-r="step" aria-label="Step" />
            <button class="btn-secondary btn-sm" data-fill>Fill</button></span>` : ''}
          ${W.cfg.help[splitDte(opt.path)[0]] ? `<div class="bt-help">${App.esc(W.cfg.help[splitDte(opt.path)[0]])}</div>` : ''}</div>
          <div class="small neg" data-axis-err></div>` : ''}
      </div>`;
    }).join('') + (W.axes.length < 2 ? '<button class="btn-secondary btn-sm" id="sw-add-axis"><i class="fa-solid fa-plus"></i>Add a second setting (for a heatmap)</button>' : '');
    box.querySelectorAll('.sw-axis').forEach((row) => {
      const i = Number(row.dataset.i);
      row.querySelector('[data-axis-path]').addEventListener('change', (e) => { W.axes[i] = { path: e.target.value, text: '' }; drawAxes(); refresh(); });
      row.querySelector('[data-axis-values]').addEventListener('input', (e) => { W.axes[i].text = e.target.value; refresh(); });
      row.querySelector('[data-axis-remove]')?.addEventListener('click', () => { W.axes.splice(i, 1); drawAxes(); refresh(); });
      row.querySelector('[data-fill]')?.addEventListener('click', () => {
        const n = (k) => Number(row.querySelector(`[data-r="${k}"]`).value);
        const [from, to, step] = [n('from'), n('to'), n('step')];
        if (!(step > 0) || Number.isNaN(from) || Number.isNaN(to) || to < from) { App.toast('From, to and a positive step, please', 'error'); return; }
        const vals = [];
        for (let x = from; x <= to + 1e-9 && vals.length <= MAX_RUNS; x += step) vals.push(Math.round(x * 1000) / 1000);
        W.axes[i].text = vals.join(', ');
        row.querySelector('[data-axis-values]').value = W.axes[i].text;
        refresh();
      });
    });
    box.querySelector('#sw-add-axis')?.addEventListener('click', () => { W.axes.push({ path: '', text: '' }); drawAxes(); refresh(); });
  }

  // The grid: every combination, with the overrides each run gets.
  function grid() {
    const axes = [];
    for (const a of W.axes) {
      const opt = W.options.find((o) => o.path === a.path);
      if (!opt) continue;
      const { values, bad } = parseValues(a.text, opt);
      axes.push({ path: opt.path, text: opt.text, values, bad, opt });
    }
    let points = [{}];
    axes.forEach((a) => { points = points.flatMap((p) => a.values.map((v) => ({ ...p, [a.path]: v }))); });
    if (!axes.length || axes.some((a) => !a.values.length)) points = [];
    const base = baseOverrides();
    const eff = effective();
    const combos = points.map((pt) => {
      const ov = H().clone(base);
      Object.entries(pt).forEach(([path, v]) => {
        const [p, d] = splitDte(path);
        if (d === null) { H().set(ov, p, v); return; }
        const table = H().get(ov, p) || H().get(eff, p) || {};
        const whole = {};
        for (let k = 0; k < W.ndte; k++) whole[String(k)] = H().forDte(table, k);   // every DTE key, sent whole
        whole[String(d)] = v;
        H().set(ov, p, whole);
      });
      return { point: pt, overrides: ov };
    });
    return { axes, combos };
  }

  function sweepName(axes) {
    const leaf = (p) => { const [x, d] = splitDte(p); return x.split('.').pop().replace(/_by_dte$/, '').replace(/_/g, '') + (d === null ? '' : d); };
    return `${H().SHORT[W.algo] || W.algo}_${W.prices}_${H().mmdd(W.start)}_${H().mmdd(W.end)}_sw_${axes.map((a) => leaf(a.path)).join('x') || 'grid'}`
      .replace(/[^A-Za-z0-9_.-]/g, '').slice(0, 66);
  }
  const taken = (name) => W.runs.some((r) => r.name === name || r.name.startsWith(name + '_') || r.sweep?.name === name);

  function refresh(fromName = false) {
    const el = W.el;
    if (!el.isConnected) return;
    const inRange = W.trading.filter((d) => d >= W.start && d <= W.end);
    const realN = inRange.filter((d) => W.real.has(d)).length;
    W.count = inRange.length;
    el.querySelector('#sw-days').innerHTML = `${inRange.length} trading day${inRange.length === 1 ? '' : 's'} (${shortDate(W.start)} → ${shortDate(W.end)})`
      + (W.prices === 'real' ? ` · <span class="${realN === inRange.length ? 'pos' : 'neg'}">${realN} with real option prices</span>`
        : ` · <span class="neg">modelled prices${H().SELLERS.test(W.algo) ? ': not reliable for a condor' : ''}</span>`);
    const { axes, combos } = grid();
    W.axes.forEach((a, i) => {
      const err = el.querySelector(`.sw-axis[data-i="${i}"] [data-axis-err]`);
      const ax = axes.find((x) => x.path === a.path);
      if (err) err.textContent = ax?.bad.length ? `Not understood: ${ax.bad.join(', ')}` : '';
    });
    const eff = effective();
    el.querySelector('#sw-grid-sub').textContent = combos.length
      ? `${combos.length} run${combos.length === 1 ? '' : 's'}${combos.length > MAX_RUNS ? ` — more than ${MAX_RUNS}: use fewer values` : ''}. Values equal to the current setting are marked.`
      : 'Pick a setting and type its values.';
    if (!fromName && !W.nameEdited) { W.name = sweepName(axes); el.querySelector('#sw-name').value = W.name; }
    el.querySelector('#sw-grid').innerHTML = combos.length ? `<div class="table-wrap" style="max-height:360px;overflow:auto"><table class="data-table">
      <thead><tr><th>Run</th>${axes.map((a) => `<th>${App.esc(a.text)}</th>`).join('')}</tr></thead>
      <tbody>${combos.slice(0, 60).map((c, i) => `<tr><td class="mono">${App.esc(W.name)}_${String(i + 1).padStart(2, '0')}</td>
        ${axes.map((a) => { const v = c.point[a.path]; const now = H().normTime(valueAt(eff, a.path)) === H().normTime(v);
          return `<td class="${now ? 'sw-now' : ''}" ${now ? 'title="the current value"' : ''}>${App.esc(showVal(v))}</td>`; }).join('')}</tr>`).join('')}</tbody></table></div>`
      : '<div class="chart-empty" style="height:80px">No combinations yet</div>';

    // name, estimate, when, button
    const okName = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,69}$/.test(W.name || '');
    const clash = okName && taken(W.name);
    el.querySelector('#sw-name-msg').innerHTML = !okName ? '<span class="neg">Letters, digits, _ - . only.</span>'
      : clash ? '<span class="neg">A sweep or run with this name exists.</span>' : `<span class="muted">Runs are named ${App.esc(W.name)}_01 …</span>`;
    const same = W.runs.filter((r) => r.algo_id === W.algo && r.option_prices === W.prices && r.state === 'done' && r.elapsed_s && r.days)
      .map((r) => r.elapsed_s / r.days).sort((a, b) => a - b);
    const spd = same.length ? same[Math.floor(same.length / 2)] : 1.5;
    const secs = spd * W.count * combos.length;
    el.querySelector('#sw-est').textContent = combos.length ? `About ${secs < 90 ? Math.max(1, Math.round(secs)) + ' s' : Math.round(secs / 60) + ' min'} in all, from earlier runs of this algo (${spd.toFixed(1)} s per day).` : '';
    const now = W.days.now || new Date().toISOString();
    const hhmm = now.slice(11, 16);
    const dow = new Date(now.slice(0, 10) + 'T12:00:00').getDay();
    W.marketHours = dow >= 1 && dow <= 5 && hhmm >= '09:15' && hhmm < '15:30';
    const when = el.querySelector('#sw-when');
    when.innerHTML = W.marketHours ? `<div class="banner banner-warn" style="margin:8px 0"><i class="fa-solid fa-clock"></i>
        Market hours: the sweep waits until 15:30. <label class="bt-toggle" style="margin-top:6px"><input type="checkbox" id="sw-now" ${W.startNow ? 'checked' : ''} /> Start now instead</label></div>` : '';
    when.querySelector('#sw-now')?.addEventListener('change', (e) => { W.startNow = e.target.checked; refresh(); });
    const btn = el.querySelector('#sw-run');
    const blockers = [!combos.length && 'pick a setting and its values', combos.length > MAX_RUNS && `at most ${MAX_RUNS} runs`,
      axes.some((a) => a.bad.length) && 'some values are not understood', !W.count && 'no trading days in this range',
      W.prices === 'real' && !realN && 'no day with real option prices', (!okName || clash) && 'pick another sweep name'].filter(Boolean);
    btn.disabled = blockers.length > 0;
    btn.title = blockers.join('; ');
    btn.querySelector('span').textContent = combos.length ? `Queue ${combos.length} run${combos.length === 1 ? '' : 's'}${W.marketHours && !W.startNow ? ' after 15:30' : ''}` : 'Queue the sweep';
    W.grid = { axes, combos };
  }

  async function run() {
    const btn = W.el.querySelector('#sw-run');
    btn.disabled = true;
    const { axes, combos } = W.grid;
    const name = W.name;
    const bodies = combos.map((c, i) => ({
      algo_id: W.algo, start: W.start, end: W.end, name: `${name}_${String(i + 1).padStart(2, '0')}`, option_prices: W.prices,
      overrides: c.overrides, days: W.count,
      not_before: W.marketHours && !W.startNow ? `${(W.days.now || '').slice(0, 10)}T15:30:00+05:30` : null,
      sweep: { name, base: W.base || null, axes: axes.map((a) => ({ path: a.path, label: a.text })), point: c.point, index: i + 1, total: combos.length },
    }));
    const detail = (err) => { const m = String(err.message).replace(/^\d+\s*/, ''); try { return JSON.parse(m).detail; } catch { return m; } };
    try {                                                  // check every combination before queueing any
      for (const b of bodies) {
        try { await App.api('backtests', {}, { method: 'POST', body: { ...b, dry_run: true } }); }
        catch (err) { throw new Error(`${b.name}: ${detail(err)}`); }
      }
      for (const b of bodies) await App.api('backtests', {}, { method: 'POST', body: b });
      App.toast(`Queued ${bodies.length} runs`);
      location.hash = `#/backtests/sweep/${encodeURIComponent(name)}`;
    } catch (err) {
      App.toast('Not queued: ' + (err.message.includes(':') ? err.message : detail(err)), 'error');
      btn.disabled = false;
    }
  }

  function groupSweeps(runs) {
    const by = {};
    runs.filter((r) => r.sweep?.name).forEach((r) => {
      const s = by[r.sweep.name] = by[r.sweep.name] || { name: r.sweep.name, total: r.sweep.total || 0, done: 0, active: false, at: r.started_at };
      if (r.state === 'done') s.done += 1;
      if (active(r)) s.active = true;
      s.at = s.at > r.started_at ? s.at : r.started_at;
    });
    return Object.values(by).sort((a, b) => (b.at || '').localeCompare(a.at || ''));
  }

  // ── results ──────────────────────────────────────────────────────────────

  async function results(el, name) {
    const draw = async () => {
      const all = (await App.api('backtests')).runs;
      const runs = all.filter((r) => r.sweep?.name === name).sort((a, b) => (a.sweep.index || 0) - (b.sweep.index || 0));
      if (!runs.length) {
        el.innerHTML = App.emptyState('fa-table-cells', 'No such sweep', `Nothing called <code>${App.esc(name)}</code>. <a href="#/backtests/sweep">Start a sweep</a>`);
        return false;
      }
      App.setRecent(all.slice(0, 5).map((r) => ({ name: r.name, state: r.state, label: r.label })));
      const axes = runs[0].sweep.axes || [];
      const kpiKey = App.prefs.get('bt.sweep.kpi', 'net');
      const [kLabel, kFn, kFmt] = KPIS[kpiKey];
      const done = runs.filter((r) => r.state === 'done' && r.totals);
      const val = (r) => (r.totals ? kFn(r.totals) : null);
      const ranked = done.filter((r) => val(r) !== null && val(r) !== undefined).sort((a, b) => val(b) - val(a));
      const top = ranked.length ? val(ranked[0]) : null;
      const bests = new Set(ranked.filter((r) => val(r) === top).map((r) => r.name));      // ties are all best
      const bestName = bests;
      const r0 = runs[0];
      const sel = new Set(App.prefs.get('bt.compare', []));
      const queued = runs.filter((r) => r.state === 'queued').length;
      el.innerHTML = `
        <div class="page-header bt-head"><div><div class="page-title"><i class="fa-solid fa-table-cells"></i>${App.esc(name)}</div>
          <div class="page-subtitle">${App.esc(r0.algo_id)} v${App.esc(r0.version || '?')} · ${r0.start === r0.end ? shortDate(r0.start) : `${shortDate(r0.start)} → ${shortDate(r0.end)}`}
            · ${r0.option_prices} prices${r0.sweep.base ? ` · starting from ${App.esc(r0.sweep.base)}` : ''} · sweeps ${axes.map((a) => `<code>${App.esc(a.path)}</code>`).join(' × ')}</div>
          <div class="chips chips-left" style="margin-top:8px">${App.chip(`${done.length} of ${runs.length} done`, done.length === runs.length ? 'tone-green' : 'tone-blue', runs.some(active))}
            ${runs.filter((r) => r.state === 'failed' || r.state === 'stale').length ? App.chip(`${runs.filter((r) => r.state === 'failed' || r.state === 'stale').length} failed`, 'tone-red') : ''}</div></div>
          <div class="header-meta">
            <label class="field-label">Best by<select class="input-field" id="sw-kpi">${Object.entries(KPIS).map(([k, [l]]) => `<option value="${k}"${k === kpiKey ? ' selected' : ''}>${l}</option>`).join('')}</select></label>
            ${queued ? `<button class="btn-secondary" id="sw-cancel"><i class="fa-solid fa-xmark"></i>Cancel ${queued} queued</button>` : ''}
            <button class="btn-primary" id="sw-compare" disabled><i class="fa-solid fa-code-compare"></i><span>Compare</span></button>
          </div></div>
        ${r0.option_prices !== 'real' && H().SELLERS.test(r0.algo_id || '') ? `<div class="banner banner-warn"><i class="fa-solid fa-triangle-exclamation"></i>Modelled prices: rank the settings, don't trust the rupees for a condor.</div>` : ''}
        ${axes.length === 2 ? heatmap(runs, axes, val, kFmt, kLabel, bestName) : axes.length === 1 ? '<div class="row"><div class="section-card"><div class="section-title">' + App.esc(kLabel) + ' by ' + App.esc(axes[0].label || axes[0].path) + '</div><div class="chart-box chart-sm"><canvas id="sw-chart"></canvas></div></div></div>' : ''}
        <div class="row"><div class="section-card bt-table-card"><div class="table-wrap"><table class="data-table bt-runs sw-table">
          <thead><tr><th class="bt-check"><i class="fa-solid fa-code-compare"></i></th><th>Run</th>${axes.map((a) => `<th>${App.esc(a.label || a.path)}</th>`).join('')}
            <th>Status</th><th>Trades</th><th>Net</th><th>Win %</th><th>PF</th><th>Expectancy</th><th>Max DD</th></tr></thead>
          <tbody>${runs.map((r) => { const t = r.totals || {}; return `<tr class="${bestName.has(r.name) ? 'sw-best' : ''}">
            <td class="bt-check"><input type="checkbox" data-pick="${App.esc(r.name)}" ${sel.has(r.name) ? 'checked' : ''} ${r.state !== 'done' ? 'disabled' : ''} aria-label="Compare ${App.esc(r.name)}" /></td>
            <td><a class="bt-name" href="#/backtests/run/${encodeURIComponent(r.name)}">${App.esc(r.name)}</a>${bestName.has(r.name) ? ' <span class="tag">best</span>' : ''}</td>
            ${axes.map((a) => `<td><b>${App.esc(showVal(r.sweep.point?.[a.path]))}</b></td>`).join('')}
            <td>${r.state === 'done' ? App.chip('Done', 'tone-green') : App.chip(r.state, r.state === 'running' ? 'tone-blue' : r.state === 'queued' ? 'tone-slate' : 'tone-red', r.state === 'running')}${active(r) ? `<div class="muted small">${App.esc(r.label)}</div>` : ''}</td>
            <td>${t.trades ?? '—'}</td><td class="${App.tone(t.net)}"><b>${r.totals ? App.money(t.net) : '—'}</b></td>
            <td>${t.win_rate ?? '—'}${t.win_rate !== null && t.win_rate !== undefined ? '%' : ''}</td><td>${App.num(t.profit_factor)}</td>
            <td class="${App.tone(t.expectancy)}">${r.totals ? App.money(t.expectancy) : '—'}</td><td class="${App.tone(t.max_drawdown)}">${r.totals ? App.money(t.max_drawdown) : '—'}</td></tr>`; }).join('')}</tbody>
        </table></div></div></div>`;
      el.querySelector('#sw-kpi').addEventListener('change', (e) => { App.prefs.set('bt.sweep.kpi', e.target.value); draw(); });
      const cmp = el.querySelector('#sw-compare');
      const paint = () => {
        const picked = [...new Set(App.prefs.get('bt.compare', []))].filter((n) => runs.some((r) => r.name === n));
        cmp.disabled = picked.length < 2;
        cmp.querySelector('span').textContent = picked.length ? `Compare ${picked.length}` : 'Compare';
        cmp.onclick = () => { location.hash = '#/backtests/compare?runs=' + picked.map(encodeURIComponent).join(','); };
      };
      el.querySelectorAll('[data-pick]').forEach((cb) => cb.addEventListener('change', () => {
        const s = new Set(App.prefs.get('bt.compare', []).filter((n) => runs.some((r) => r.name === n)));
        if (cb.checked) { if (s.size >= 6) { cb.checked = false; App.toast('At most 6 runs', 'error'); return; } s.add(cb.dataset.pick); } else s.delete(cb.dataset.pick);
        App.prefs.set('bt.compare', [...s]);
        paint();
      }));
      paint();
      el.querySelector('#sw-cancel')?.addEventListener('click', async () => {
        if (!confirm(`Cancel the ${queued} runs of this sweep that haven't started?`)) return;
        for (const r of runs.filter((x) => x.state === 'queued')) await App.api(`backtests/${encodeURIComponent(r.name)}/stop`, {}, { method: 'POST' }).catch(() => {});
        App.toast('Cancelled the queued runs');
        draw();
      });
      if (axes.length === 1) drawChart(runs, axes[0], val, kLabel, bestName);
      return runs.some(active);
    };
    el.innerHTML = '<div class="section-card"><div class="skeleton" style="height:200px"></div></div>';
    const going = await draw();
    if (going) timer = setInterval(async () => { if (!(await draw())) stopPoll(); }, 3000);
  }

  function heatmap(runs, axes, val, fmt, label, bestName) {
    const [a, b] = axes;
    const uniq = (ax) => [...new Map(runs.map((r) => [JSON.stringify(r.sweep.point?.[ax.path]), r.sweep.point?.[ax.path]])).values()];
    const rows = uniq(a), cols = uniq(b);
    const vals = runs.map(val).filter((v) => v !== null && v !== undefined);
    const lo = Math.min(...vals), hi = Math.max(...vals);
    const cell = (x, y) => {
      const r = runs.find((q) => JSON.stringify(q.sweep.point?.[a.path]) === JSON.stringify(x) && JSON.stringify(q.sweep.point?.[b.path]) === JSON.stringify(y));
      if (!r) return '<td class="hm-empty"></td>';
      const v = val(r);
      if (v === null || v === undefined) return `<td class="hm-empty" title="${App.esc(r.name)}: ${App.esc(r.label)}"><span class="muted small">${App.esc(r.state)}</span></td>`;
      const t = hi > lo ? (v - lo) / (hi - lo) : 1;                // 0 = worst, 1 = best
      const bg = `rgba(${t >= 0.5 ? '16,185,129' : '239,68,68'},${(0.1 + 0.55 * Math.abs(t - 0.5) * 2).toFixed(2)})`;
      return `<td style="background:${bg}" class="${bestName.has(r.name) ? 'sw-best-cell' : ''}" title="${App.esc(r.name)}">
        <a href="#/backtests/run/${encodeURIComponent(r.name)}">${fmt(v)}</a></td>`;
    };
    return `<div class="row"><div class="section-card"><div class="card-head"><div><div class="section-title">${App.esc(label)}: heatmap</div>
      <div class="section-subtitle">Rows: ${App.esc(a.label || a.path)} · columns: ${App.esc(b.label || b.path)} · green is better, the best is outlined</div></div></div>
      <div class="table-wrap"><table class="data-table bt-heatmap sw-heatmap"><thead><tr><th>${App.esc(a.label || a.path)} ↓ / ${App.esc(b.label || b.path)} →</th>
        ${cols.map((y) => `<th>${App.esc(showVal(y))}</th>`).join('')}</tr></thead>
        <tbody>${rows.map((x) => `<tr><td><b>${App.esc(showVal(x))}</b></td>${cols.map((y) => cell(x, y)).join('')}</tr>`).join('')}</tbody></table></div></div></div>`;
  }

  function drawChart(runs, axis, val, label, bestName) {
    App.destroyCharts();
    const canvas = document.getElementById('sw-chart');
    if (!canvas) return;
    App.chart(canvas, {
      type: 'bar',
      data: { labels: runs.map((r) => showVal(r.sweep.point?.[axis.path])), datasets: [{ label, data: runs.map(val),
        backgroundColor: runs.map((r) => (bestName.has(r.name) ? '#10b981' : (val(r) ?? 0) < 0 ? '#ef4444aa' : '#3b82f6aa')), borderRadius: 4, maxBarThickness: 48 }] },
      options: { responsive: true, maintainAspectRatio: false,
        scales: { x: { grid: { display: false }, title: { display: true, text: axis.label || axis.path } }, y: { grid: { color: '#f1f5f9' } } },
        plugins: { tooltip: { callbacks: { label: (c) => `${label}: ${c.parsed.y}` } } } },
    });
  }

  App.backtestSweep = { render, destroy: stopPoll };
})();
