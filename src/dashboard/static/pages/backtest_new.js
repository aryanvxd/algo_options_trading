/* New backtest: #/backtests/new[?from=<run>][&algo=<id>] (docs/design/backtest.md, section 6.3).
   The form is built here from the algo's config schema (GET /api/algos/<id>/config); the page works
   out what changed and sends only that as `overrides`. A by-DTE table that changed is sent whole, every
   DTE key filled, so the run's record shows the full table. The server checks the settings with the
   same config load the engine uses, and queues the run. */

(() => {
  const FIXED = new Set(['algo_id', 'name', 'version', 'description', 'mode']);
  const SHORT = { iron_condor: 'ic', iron_condor_balanced: 'icb', strangle_scalp: 'ss', weekend_condor: 'wc', directional_scalp: 'ds' };
  const SELLERS = /condor/;
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  let F = null;            // form state

  // ── small helpers ──
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const get = (obj, path) => path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
  function set(obj, path, value) {
    const keys = path.split('.');
    let o = obj;
    keys.slice(0, -1).forEach((k) => { if (o[k] === undefined || o[k] === null || typeof o[k] !== 'object') o[k] = {}; o = o[k]; });
    o[keys[keys.length - 1]] = value;
  }
  const isDte = (path) => /_by_dte$/.test(path);
  const normTime = (v) => (typeof v === 'string' && /^\d{1,2}:\d{2}(:00)?$/.test(v) ? v.padStart(5, '0').slice(0, 5) : v);
  const same = (a, b) => JSON.stringify(normTime(a)) === JSON.stringify(normTime(b));
  const label = (k) => k.replace(/_by_dte$/, '').replace(/_/g, ' ').replace(/\bpts\b/, '(pts)').replace(/\bs$/, '(s)');
  function forDte(table, dte) {                        // the algo's rule: largest key at or below, else the smallest
    const keys = Object.keys(table || {}).map(Number).sort((a, b) => a - b);
    if (!keys.length) return null;
    const below = keys.filter((k) => k <= dte);
    return table[String(below.length ? below[below.length - 1] : keys[0])];
  }
  const mmdd = (iso) => iso.slice(5, 7) + iso.slice(8, 10);
  const shortDate = (iso) => { const [y, m, d] = iso.split('-').map(Number); return `${d} ${MONTHS[m - 1]} ${String(y).slice(2)}`; };

  // ── schema ──
  function resolve(s, root) {
    if (!s) return {};
    if (s.$ref) return resolve(root.$defs?.[s.$ref.split('/').pop()], root);
    if (s.allOf?.length === 1) return { ...resolve(s.allOf[0], root), default: s.default };
    if (s.anyOf) {
      const non = s.anyOf.filter((x) => x.type !== 'null');
      if (!non.length) return { type: 'null' };
      return { ...resolve(non[0], root), nullable: non.length < s.anyOf.length, default: s.default };
    }
    return s;
  }
  function kindOf(path, s) {
    const key = path.split('.').pop();
    if (s.type === 'null') return null;                        // a setting this algo doesn't use
    if (isDte(key) && (s.type === 'object' || s.additionalProperties)) return 'dte';
    if (s.enum) return 'select';
    if (s.type === 'boolean') return 'bool';
    if (s.type === 'integer') return 'int';
    if (s.type === 'number') return 'num';
    if (s.type === 'string' && s.format === 'time') return 'time';
    if (s.type === 'string') return 'text';
    if (s.type === 'object' && s.properties) return 'group';
    return 'json';
  }

  // Groups like config.yaml: top-level settings first, then each section, in the file's order.
  function buildGroups(cfg = F.cfg) {
    const { schema, order } = cfg;
    const pos = (p) => { const i = order.indexOf(p); return i < 0 ? 1e6 : i; };
    const fieldsOf = (props, prefix) => Object.entries(props || {}).map(([k, raw]) => {
      const path = prefix + k;
      const s = resolve(raw, schema);
      return { path, key: k, s, kind: kindOf(path, s), inYaml: order.includes(path) };
    }).filter((f) => f.kind && !FIXED.has(f.path));
    const top = fieldsOf(schema.properties, '');
    const groups = [{ id: 'general', title: 'General', fields: top.filter((f) => f.kind !== 'group') }];
    top.filter((f) => f.kind === 'group').forEach((g) => groups.push({ id: g.path, title: label(g.key), fields: fieldsOf(g.s.properties, g.path + '.') }));
    groups.forEach((g) => {
      g.fields.sort((a, b) => pos(a.path) - pos(b.path));
      g.first = Math.min(...g.fields.map((f) => pos(f.path)), 1e6);
    });
    return groups.filter((g) => g.fields.length).sort((a, b) => (a.id === 'general' ? -1 : b.id === 'general' ? 1 : a.first - b.first));
  }

  function dteCount() {
    const twd = Number(F.cur.trade_window_days);
    if (twd > 0) return Math.min(twd, 7);
    let max = 0;
    const walk = (o) => Object.entries(o || {}).forEach(([k, v]) => {
      if (isDte(k) && v && typeof v === 'object') Object.keys(v).forEach((x) => { max = Math.max(max, Number(x) + 1); });
      else if (v && typeof v === 'object' && !Array.isArray(v)) walk(v);
    });
    walk(F.cur);
    return Math.max(max, 1);
  }

  // ── changes ──
  function changes() {
    const out = [];
    F.groups.forEach((g) => g.fields.forEach((f) => {
      const was = get(F.live, f.path), now = get(F.cur, f.path);
      if (f.kind === 'dte') {
        for (let d = 0; d < F.ndte; d++) {
          const a = forDte(was, d), b = forDte(now, d);
          if (!same(a, b)) out.push({ path: f.path, dte: d, from: a, to: b });
        }
      } else if (!same(was, now)) out.push({ path: f.path, from: was, to: now });
    }));
    return out;
  }
  function overrides() {
    const o = {};
    const done = new Set();
    changes().forEach((c) => {
      if (c.dte !== undefined) {
        if (done.has(c.path)) return;
        done.add(c.path);
        const t = get(F.cur, c.path);
        const whole = {};
        for (let d = 0; d < F.ndte; d++) whole[String(d)] = forDte(t, d);   // every DTE key, so nothing is merged in
        set(o, c.path, whole);
      } else set(o, c.path, normTime(get(F.cur, c.path)));
    });
    return o;
  }
  function slugOf(c) {
    const leaf = c.path.split('.').pop().replace(/_by_dte$/, '').replace(/_(points|pts|max|min)$/, '').replace(/_/g, '');
    const v = String(normTime(c.to)).replace(/:/g, '').replace(/[^A-Za-z0-9.-]/g, '');
    return c.dte !== undefined ? `${leaf}${c.dte}-${v}` : `${leaf}${v}`;
  }
  function autoName() {
    const cs = changes();
    let n = `${SHORT[F.algo] || F.algo}_${F.prices}_${mmdd(F.start)}_${mmdd(F.end)}`;
    if (cs.length) n += '_' + slugOf(cs[0]) + (cs.length > 1 ? `_${cs.length}chg` : '');
    n = n.replace(/[^A-Za-z0-9_.-]/g, '').slice(0, 76);
    const taken = new Set((F.runs || []).map((r) => r.name));
    let out = n, i = 2;
    while (taken.has(out)) out = `${n}_${i++}`;
    return out;
  }

  // ── render ──
  async function render(el, query) {
    el.innerHTML = '<div class="section-card"><div class="skeleton" style="height:200px"></div></div>';
    const [days, runs] = await Promise.all([App.api('data/days'), App.api('backtests').then((r) => r.runs)]);
    let from = null;
    if (query?.get('from')) from = await App.api(`backtests/${encodeURIComponent(query.get('from'))}`).catch(() => null);
    const strategies = (App.meta?.strategies || []).filter((a) => !a.retired && !a.error);
    const algo = from?.algo_id || query?.get('algo') || App.prefs.get('bt.new.algo', 'iron_condor_balanced');
    F = { el, days, runs, from, strategies, real: new Set(days.real_option_days), trading: days.trading_days,
      algo: strategies.some((a) => a.algo_id === algo) ? algo : strategies[0]?.algo_id,
      prices: from?.option_prices || 'real', nameEdited: false, startNow: false, invalid: new Set() };
    const last = days.last_day || App.isoDate(new Date());
    if (from) { F.start = from.start; F.end = from.end; }
    else {                                     // default: every day with real option prices, else the last 20
      const real = days.real_option_days;
      F.start = real.length ? real[0] : preset(20, last); F.end = real.length ? real[real.length - 1] : last;
    }
    await loadAlgo();
  }

  function preset(n, end) {
    const T = F.trading.filter((d) => d <= end);
    return T.length ? T[Math.max(0, T.length - n)] : end;
  }
  function presetYears(y, end) {
    const [Y, M, D] = end.split('-').map(Number);
    const target = App.isoDate(new Date(Y - y, M - 1, D + 1));
    return F.trading.find((d) => d >= target) || target;
  }

  async function loadAlgo() {
    F.cfg = await App.api(`algos/${F.algo}/config`);
    F.live = F.cfg.values;
    F.cur = clone(F.live);
    F.dropped = [];
    if (F.from && F.from.algo_id === F.algo) applyOverrides(F.from.overrides || {});
    F.groups = buildGroups();
    F.ndte = dteCount();
    F.invalid.clear();
    if (!F.nameEdited) F.name = null;
    draw();
  }

  function applyOverrides(o, prefix = '') {
    Object.entries(o).forEach(([k, v]) => {
      const path = prefix + k;
      const live = get(F.live, path);
      if (live === undefined) { F.dropped.push(path); return; }
      if (v && typeof v === 'object' && !Array.isArray(v) && !isDte(k)) applyOverrides(v, path + '.');
      else set(F.cur, path, isDte(k) ? { ...(live || {}), ...v } : v);
    });
  }

  function draw() {
    const el = F.el;
    const a = F.strategies.find((s) => s.algo_id === F.algo) || {};
    const T = F.trading;
    const last = F.days.last_day || F.end;
    const presets = [['5d', 'Last 5 days', () => preset(5, last)], ['20d', '20 days', () => preset(20, last)], ['60d', '60 days', () => preset(60, last)],
      ['1y', '1 year', () => presetYears(1, last)], ['2y', '2 years', () => presetYears(2, last)]];
    const realDays = F.days.real_option_days;
    el.innerHTML = `
      <div class="page-header"><div><div class="page-title"><i class="fa-solid fa-play"></i>New backtest</div>
        <div class="page-subtitle">${F.from ? `Starting from <a href="#/backtests/run/${encodeURIComponent(F.from.name)}">${App.esc(F.from.name)}</a>: its dates, prices and changes are filled in` : 'Pick a strategy, change its settings, and run it on minute history. Nothing here touches a live algo.'}</div></div></div>
      <div class="bt-new">
        <div class="bt-new-main">
          <div class="section-card bt-new-card">
            <div class="bt-row">
              <label class="field-label">Strategy<select class="input-field" id="bn-algo">${F.strategies.map((s) =>
                `<option value="${App.esc(s.algo_id)}"${s.algo_id === F.algo ? ' selected' : ''}>${App.esc(s.name)}</option>`).join('')}</select></label>
              <div class="chips chips-left">${App.chip(`v${App.esc(F.cfg.version)}`, 'tone-slate')}${App.chip(`${F.cur.lots} lot${F.cur.lots === 1 ? '' : 's'}`, 'tone-slate')}${App.chip('Paper', 'tone-violet')}</div>
            </div>
            ${F.dropped.length ? `<div class="banner banner-warn" style="margin-top:12px"><i class="fa-solid fa-triangle-exclamation"></i>
              Not in today's config, so left out: ${F.dropped.map((p) => `<code>${App.esc(p)}</code>`).join(', ')}</div>` : ''}
          </div>

          <div class="section-card bt-new-card">
            <div class="section-title" style="margin-bottom:10px">Dates and prices</div>
            <div class="bt-row">
              <label class="field-label">From<input type="date" class="input-field" id="bn-start" value="${F.start}" min="${T[0] || ''}" max="${last}" /></label>
              <label class="field-label">To<input type="date" class="input-field" id="bn-end" value="${F.end}" min="${T[0] || ''}" max="${last}" /></label>
              <div class="bt-presets">${presets.map(([k, l]) => `<button class="btn-secondary btn-sm" data-preset="${k}">${l}</button>`).join('')}
                ${realDays.length ? `<button class="btn-secondary btn-sm" data-preset="real" title="${realDays.length} days with saved option candles">Real-price days</button>` : ''}</div>
            </div>
            <div class="bt-row" style="margin-top:12px">
              <div class="segmented" id="bn-prices" role="group" aria-label="Option prices">
                <button data-v="real" class="${F.prices === 'real' ? 'active' : ''}">Real candles</button>
                <button data-v="model" class="${F.prices === 'model' ? 'active' : ''}">Model</button></div>
              <span class="muted small" id="bn-daycount"></span>
            </div>
            <div id="bn-coverage"></div>
          </div>

          ${F.groups.map(groupHtml).join('')}
        </div>

        <aside class="bt-new-side">
          <div class="section-card bt-new-card">
            <div class="card-head"><div><div class="section-title">Changes from the live config</div>
              <div class="section-subtitle">Only these are sent; everything else is today's <code>config.yaml</code></div></div>
              <button class="link-btn" id="bn-reset">Reset all</button></div>
            <div id="bn-changes"></div>
            ${Object.keys(F.cfg.backtest_overrides || {}).length ? `<div class="mini-title" style="margin-top:12px">Always applied in backtests</div>
              <div class="muted small mono">${App.esc(JSON.stringify(F.cfg.backtest_overrides))}</div>` : ''}
          </div>
          <div class="section-card bt-new-card">
            <label class="field-label">Name<input class="input-field" id="bn-name" maxlength="80" spellcheck="false" /></label>
            <div class="small" id="bn-name-msg"></div>
            <div id="bn-when"></div>
            <button class="btn-primary bt-run-btn" id="bn-run"><i class="fa-solid fa-play"></i><span>Run backtest</span></button>
            <div class="muted small" id="bn-queue"></div>
          </div>
        </aside>
      </div>`;
    bind();
    refresh();
  }

  function groupHtml(g) {
    const main = g.fields.filter((f) => f.inYaml), extra = g.fields.filter((f) => !f.inYaml);
    const open = App.prefs.get(`bt.new.group.${g.id}`, g.id !== 'general' || true);
    return `<details class="section-card bt-new-card bt-group" data-group="${App.esc(g.id)}" ${open ? 'open' : ''}>
      <summary><span class="section-title">${App.esc(g.title)}</span><span class="muted small bt-group-count"></span></summary>
      <div class="bt-fields">${main.map(fieldHtml).join('')}</div>
      ${extra.length ? `<details class="bt-defaults"><summary class="muted small">${extra.length} more setting${extra.length === 1 ? '' : 's'} not in config.yaml (defaults)</summary>
        <div class="bt-fields">${extra.map(fieldHtml).join('')}</div></details>` : ''}
    </details>`;
  }

  function fieldHtml(f) {
    const v = get(F.cur, f.path);
    const help = F.cfg.help[f.path];
    const id = 'bn-f-' + f.path.replace(/\./g, '-');
    let input;
    const attrs = `id="${id}" data-path="${App.esc(f.path)}" data-kind="${f.kind}"`;
    if (f.kind === 'bool') input = `<label class="bt-switch"><input type="checkbox" ${attrs} ${v ? 'checked' : ''} /><span>${v ? 'on' : 'off'}</span></label>`;
    else if (f.kind === 'select') input = `<select class="input-field" ${attrs}>${f.s.enum.map((o) => `<option${o === v ? ' selected' : ''}>${App.esc(o)}</option>`).join('')}</select>`;
    else if (f.kind === 'int' || f.kind === 'num') input = `<input type="number" class="input-field" ${attrs} value="${v ?? ''}" step="${f.kind === 'int' ? 1 : 'any'}" ${f.s.nullable ? 'placeholder="none"' : ''} />`;
    else if (f.kind === 'time') input = `<input type="time" class="input-field" ${attrs} value="${normTime(v) || ''}" />`;
    else if (f.kind === 'text') input = `<input class="input-field" ${attrs} value="${App.esc(v ?? '')}" />`;
    else if (f.kind === 'dte') {
      input = `<div class="bt-dte" ${attrs}>${Array.from({ length: F.ndte }, (_, d) => {
        const own = v && Object.prototype.hasOwnProperty.call(v, String(d));
        return `<label><span>DTE ${d}</span><input type="number" step="any" class="input-field${own ? '' : ' inherited'}" data-dte="${d}"
          value="${own ? v[String(d)] : ''}" placeholder="${forDte(v, d) ?? ''}" aria-label="${App.esc(label(f.key))} at DTE ${d}" /></label>`;
      }).join('')}</div>`;
    } else input = `<textarea class="input-field mono" rows="2" ${attrs}>${App.esc(JSON.stringify(v))}</textarea>`;
    return `<div class="bt-field${f.kind === 'dte' || f.kind === 'json' ? ' wide' : ''}" data-field="${App.esc(f.path)}">
      <label class="bt-field-label" for="${id}">${App.esc(label(f.key))} <span class="mono muted">${App.esc(f.path)}</span></label>
      ${input}${help ? `<div class="bt-help">${App.esc(help)}</div>` : ''}</div>`;
  }

  function readInput(inp) {
    const path = inp.dataset.path || inp.closest('[data-path]')?.dataset.path;
    const kind = inp.dataset.kind || 'dte';
    const f = F.groups.flatMap((g) => g.fields).find((x) => x.path === path);
    F.invalid.delete(path);
    if (kind === 'bool') { set(F.cur, path, inp.checked); inp.nextElementSibling.textContent = inp.checked ? 'on' : 'off'; }
    else if (kind === 'int' || kind === 'num') {
      if (inp.value === '') { if (f?.s.nullable) set(F.cur, path, null); else F.invalid.add(path); }
      else set(F.cur, path, kind === 'int' ? Math.round(Number(inp.value)) : Number(inp.value));
    } else if (kind === 'time') { if (inp.value) set(F.cur, path, inp.value); else F.invalid.add(path); }
    else if (kind === 'select' || kind === 'text') set(F.cur, path, inp.value);
    else if (kind === 'json') { try { set(F.cur, path, JSON.parse(inp.value)); } catch { F.invalid.add(path); } }
    else if (kind === 'dte') {
      const t = { ...(get(F.cur, path) || {}) };
      const d = inp.dataset.dte;
      if (inp.value === '') delete t[d]; else t[d] = Number(inp.value);
      inp.classList.toggle('inherited', inp.value === '');
      set(F.cur, path, t);
      inp.closest('.bt-dte').querySelectorAll('input[data-dte]').forEach((x) => { x.placeholder = forDte(t, Number(x.dataset.dte)) ?? ''; });
    }
    inp.classList.toggle('invalid', F.invalid.has(path));
    refresh();
  }

  function bind() {
    const el = F.el;
    el.querySelector('#bn-algo').addEventListener('change', (e) => { F.algo = e.target.value; App.prefs.set('bt.new.algo', F.algo); F.from = null; loadAlgo(); });
    el.querySelector('#bn-start').addEventListener('change', (e) => { if (e.target.value) { F.start = e.target.value; refresh(); } });
    el.querySelector('#bn-end').addEventListener('change', (e) => { if (e.target.value) { F.end = e.target.value; refresh(); } });
    el.querySelectorAll('[data-preset]').forEach((b) => b.addEventListener('click', () => {
      const last = F.days.last_day || F.end;
      const p = b.dataset.preset;
      if (p === 'real') { const r = F.days.real_option_days; F.start = r[0]; F.end = r[r.length - 1]; }
      else { F.end = last; F.start = { '5d': () => preset(5, last), '20d': () => preset(20, last), '60d': () => preset(60, last),
        '1y': () => presetYears(1, last), '2y': () => presetYears(2, last) }[p](); }
      el.querySelector('#bn-start').value = F.start; el.querySelector('#bn-end').value = F.end;
      refresh();
    }));
    el.querySelector('#bn-prices').addEventListener('click', (e) => {
      const b = e.target.closest('button'); if (!b) return;
      F.prices = b.dataset.v;
      el.querySelectorAll('#bn-prices button').forEach((x) => x.classList.toggle('active', x === b));
      refresh();
    });
    el.querySelectorAll('[data-path] , .bt-dte input').forEach((inp) => {
      if (inp.classList.contains('bt-dte')) return;
      inp.addEventListener(inp.type === 'checkbox' || inp.tagName === 'SELECT' ? 'change' : 'input', () => readInput(inp));
    });
    el.querySelectorAll('details.bt-group').forEach((d) => d.addEventListener('toggle', () => App.prefs.set(`bt.new.group.${d.dataset.group}`, d.open)));
    el.querySelector('#bn-reset').addEventListener('click', () => { F.cur = clone(F.live); F.from = null; F.dropped = []; F.invalid.clear(); draw(); });
    const name = el.querySelector('#bn-name');
    name.addEventListener('input', () => { F.nameEdited = !!name.value; F.name = name.value || null; refresh(true); });
    el.querySelector('#bn-run').addEventListener('click', run);
  }

  // Everything that depends on the current values: changes list, field marks, coverage, name, button.
  function refresh(fromName = false) {
    const el = F.el;
    if (!el.isConnected) return;
    const cs = changes();
    const changed = new Set(cs.map((c) => c.path));
    el.querySelectorAll('.bt-field').forEach((x) => x.classList.toggle('changed', changed.has(x.dataset.field)));
    el.querySelectorAll('details.bt-group').forEach((d) => {
      const n = cs.filter((c) => c.path === d.dataset.group || c.path.startsWith(d.dataset.group + '.') || (d.dataset.group === 'general' && !c.path.includes('.'))).length;
      d.querySelector('.bt-group-count').textContent = n ? `${n} changed` : '';
    });
    const show = (v) => (v === null || v === undefined ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(normTime(v)));
    el.querySelector('#bn-changes').innerHTML = cs.length ? `<ul class="bt-changes">${cs.map((c, i) => `<li>
        <span class="mono">${App.esc(c.path)}${c.dte !== undefined ? `[${c.dte}]` : ''}</span>
        <span><span class="muted">${App.esc(show(c.from))}</span> → <b>${App.esc(show(c.to))}</b></span>
        <button class="link-btn" data-undo="${i}" title="Back to the live value">undo</button></li>`).join('')}</ul>`
      : '<div class="muted small">None yet: the run uses the live config.</div>';
    el.querySelectorAll('[data-undo]').forEach((b) => b.addEventListener('click', () => undo(cs[Number(b.dataset.undo)])));

    // dates and coverage
    const inRange = F.trading.filter((d) => d >= F.start && d <= F.end);
    const realN = inRange.filter((d) => F.real.has(d)).length;
    F.count = inRange.length;
    el.querySelector('#bn-daycount').textContent = `${inRange.length} trading day${inRange.length === 1 ? '' : 's'} (${shortDate(F.start)} → ${shortDate(F.end)})`;
    const cov = el.querySelector('#bn-coverage');
    if (F.prices === 'real') {
      cov.innerHTML = `<div class="bt-strip" aria-label="Days with real option prices">${inRange.map((d) =>
          `<span class="${F.real.has(d) ? 'ok' : 'miss'}" title="${d}: ${F.real.has(d) ? `real candles (expiry ${F.days.real_expiry[d]})` : 'no saved option candles: not traded'}"></span>`).join('')}</div>
        <div class="small ${realN === inRange.length ? 'pos' : realN ? '' : 'neg'}">${realN === inRange.length && realN
          ? `<i class="fa-solid fa-check"></i> ${realN === 1 ? 'This day has' : `All ${realN} days have`} real option prices.`
          : `<i class="fa-solid fa-circle-info"></i> Only ${realN} of ${inRange.length} days have real option prices; the other ${inRange.length - realN} will be skipped (listed as missing_options).`}</div>`;
    } else {
      cov.innerHTML = `<div class="banner ${SELLERS.test(F.algo) ? 'banner-warn' : ''}" style="margin:10px 0 0"><i class="fa-solid fa-triangle-exclamation"></i>
        Modelled prices (Black-76 on yesterday's smile) are biased against option sellers${SELLERS.test(F.algo) ? ': don\'t trust rupee results for a condor' : ''}.
        Fine for ranking settings, not a promise of rupees.</div>`;
    }

    // name
    const nameEl = el.querySelector('#bn-name');
    if (!F.nameEdited) { F.name = autoName(); if (!fromName) nameEl.value = F.name; }
    else if (!fromName && nameEl.value !== F.name) nameEl.value = F.name;
    const taken = (F.runs || []).some((r) => r.name === F.name);
    const okName = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/.test(F.name || '');
    el.querySelector('#bn-name-msg').innerHTML = !okName ? '<span class="neg">Letters, digits, _ - . only; start with a letter or digit.</span>'
      : taken ? '<span class="neg">A backtest with this name exists.</span>' : F.nameEdited ? '' : '<span class="muted">Made from the settings; edit it if you like.</span>';

    // when
    const now = F.days.now || new Date().toISOString();
    const hhmm = now.slice(11, 16);
    const dow = new Date(now.slice(0, 10) + 'T12:00:00').getDay();
    F.marketHours = dow >= 1 && dow <= 5 && hhmm >= '09:15' && hhmm < '15:30';
    const when = el.querySelector('#bn-when');
    when.innerHTML = F.marketHours ? `<div class="banner banner-warn" style="margin:8px 0"><i class="fa-solid fa-clock"></i>
        Market hours: live algos are using this laptop. The run waits until 15:30.
        <label class="bt-toggle" style="margin-top:6px"><input type="checkbox" id="bn-now" ${F.startNow ? 'checked' : ''} /> Start now instead</label></div>` : '';
    when.querySelector('#bn-now')?.addEventListener('change', (e) => { F.startNow = e.target.checked; refresh(); });
    const ahead = (F.runs || []).filter((r) => r.state === 'running' || r.state === 'queued').length;
    el.querySelector('#bn-queue').textContent = ahead ? `${ahead} backtest${ahead === 1 ? '' : 's'} ahead in the queue; this one starts after.` : '';

    const btn = el.querySelector('#bn-run');
    const blockers = [!F.count && 'no trading days in this range', F.prices === 'real' && !realN && 'no day in this range has real option prices',
      F.invalid.size && `fix ${[...F.invalid].join(', ')}`, (!okName || taken) && 'pick another name', F.start > F.end && 'From is after To'].filter(Boolean);
    btn.disabled = blockers.length > 0;
    btn.title = blockers.join('; ');
    btn.querySelector('span').textContent = F.marketHours && !F.startNow ? 'Run after 15:30' : 'Run backtest';
  }

  function undo(c) {
    if (c.dte !== undefined) {
      const t = { ...(get(F.cur, c.path) || {}) };
      const live = get(F.live, c.path) || {};
      if (Object.prototype.hasOwnProperty.call(live, String(c.dte))) t[String(c.dte)] = live[String(c.dte)]; else delete t[String(c.dte)];
      set(F.cur, c.path, t);
    } else set(F.cur, c.path, clone(get(F.live, c.path) ?? null));
    F.invalid.delete(c.path);
    const box = F.el.querySelector(`[data-field="${CSS.escape(c.path)}"]`);
    const f = F.groups.flatMap((g) => g.fields).find((x) => x.path === c.path);
    if (box && f) {
      const tmp = document.createElement('div');
      tmp.innerHTML = fieldHtml(f);
      box.replaceWith(tmp.firstElementChild);
      const fresh = F.el.querySelector(`[data-field="${CSS.escape(c.path)}"]`);
      fresh.querySelectorAll('[data-path], .bt-dte input').forEach((inp) => {
        if (inp.classList.contains('bt-dte')) return;
        inp.addEventListener(inp.type === 'checkbox' || inp.tagName === 'SELECT' ? 'change' : 'input', () => readInput(inp));
      });
    }
    refresh();
  }

  async function run() {
    const btn = F.el.querySelector('#bn-run');
    btn.disabled = true;
    const body = { algo_id: F.algo, start: F.start, end: F.end, name: F.name, option_prices: F.prices, overrides: overrides(), days: F.count };
    if (F.marketHours && !F.startNow) body.not_before = `${(F.days.now || '').slice(0, 10)}T15:30:00+05:30`;
    try {
      await App.api('backtests', {}, { method: 'POST', body });
      App.toast(body.not_before ? `Queued: ${F.name} starts after 15:30` : `Queued: ${F.name}`);
      location.hash = `#/backtests/run/${encodeURIComponent(F.name)}/log`;
    } catch (err) {
      const msg = String(err.message).replace(/^\d+\s*/, '');
      let detail = msg;
      try { detail = JSON.parse(msg).detail; } catch { /* plain text */ }
      App.toast('Not started: ' + detail, 'error');
      btn.disabled = false;
    }
  }

  App.backtestNew = { render };
  // Shared with the Parameter sweep page (pages/backtest_sweep.js).
  App.backtestForm = { buildGroups, forDte, get, set, clone, normTime, isDte, label, mmdd, SHORT, SELLERS };
})();
