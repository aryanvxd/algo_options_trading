/* Swing › Settings (#/swing/settings): the rules of each stage of the weekend workflow, saved to
   data/swing/overrides.json over src/swing/config.yaml. The next scan uses them.
   Swing › Fundamentals data (#/swing/fundamentals): upload a CSV (e.g. a Screener export), pick the
   symbol column and the numbers to keep. They feed the fundamentals rules and the stock pages. */

(() => {
  const esc = (v) => App.esc(v);
  const why = (err) => App.swingWatch.why(err);

  // [path, label, help, kind]   kind: num | int | bool | mode
  const GROUPS = [
    ['Universe', [
      ['min_traded_value_cr', 'Liquid stocks only, ₹ crore a day', '20-day average traded value', 'num'],
      ['overview_near_atr', 'Sectors page: "near" within, ATR', 'how close counts as near support or resistance', 'num'],
    ]],
    ['1 · Sectors', [
      ['funnel.sector_near_atr', 'At support within, ATR', 'an index inside a zone, or this close above a support', 'num'],
      ['funnel.sector_need_trend', 'Trend must be up', 'close above the 200-day average, or the weekly 50-EMA rising', 'bool'],
      ['funnel.require_sector', 'Only stocks of passing sectors', 'off: every liquid stock (the sector still counts in the score)', 'bool'],
      ['funnel.rsi_days', 'RSI days', '', 'int'],
    ]],
    ['2 · Stocks and plans', [
      ['setups.near_atr', 'Near support within, ATR', 'close this far above a support zone\'s top, or inside it', 'num'],
      ['setups.min_rr', 'Minimum reward:risk', '(target − entry) ÷ (entry − stop)', 'num'],
      ['funnel.stop_atr', 'Stop under the zone, ATR', 'stop = zone bottom − this. The backtest\'s 0.5 was hit in 2 days 3 times in 4', 'num'],
      ['setups.knife_atr', 'Skip after a fall of, ATR', 'no plan for a stock still falling hard', 'num'],
      ['setups.knife_days', '… within days', '', 'int'],
      ['funnel.default_mode', 'Exit mode', 'OCO = stop and target; target only = no stop', 'mode'],
      ['setups.amount_high', '₹ per entry, strong zone', 'quantity = amount ÷ entry', 'num'],
      ['setups.amount_low', '₹ per entry, other zones', '', 'num'],
      ['setups.high_score', 'Strong zone: score at least', '', 'num'],
    ]],
    ['3 · Ranking', [
      ['funnel.top_n', 'Top picks (Algo picks list)', '', 'int'],
      ['setups.max_per_sector', 'At most per industry', '', 'int'],
      ['funnel.w_tech', 'Weight: technicals', 'the parts a stock has are averaged with these weights', 'num'],
      ['funnel.w_news', 'Weight: news', '', 'num'],
      ['funnel.w_fund', 'Weight: fundamentals', '', 'num'],
      ['funnel.news_days', 'Results within, days', 'results inside this window: flagged', 'int'],
      ['funnel.results_penalty', '… news score minus', '', 'num'],
      ['funnel.macro_days', 'High-impact Indian events within, days', 'RBI, CPI, GDP, Budget …', 'int'],
      ['funnel.macro_penalty', '… news score minus', '', 'num'],
    ]],
    ['Tracking (journal)', [
      ['setups.fill_weeks', 'Entry must fill within, weeks', 'else the pick expires', 'int'],
      ['setups.max_hold_days', 'Sell after, sessions', '', 'int'],
      ['setups.cost_pct', 'Costs per round trip, %', '', 'num'],
    ]],
  ];

  const get = (o, path) => path.split('.').reduce((a, k) => (a === undefined || a === null ? a : a[k]), o);
  function set(o, path, v) {
    const keys = path.split('.');
    let cur = o;
    keys.slice(0, -1).forEach((k) => { cur[k] = cur[k] && typeof cur[k] === 'object' ? cur[k] : {}; cur = cur[k]; });
    cur[keys[keys.length - 1]] = v;
  }
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

  function field([path, label, help, kind], values, defaults) {
    const v = get(values, path);
    const changed = !same(v, get(defaults, path));
    const id = `swg-f-${path.replace(/\./g, '-')}`;
    let input;
    if (kind === 'bool') input = `<label class="sw-check"><input type="checkbox" id="${id}" data-path="${path}" data-kind="bool"${v ? ' checked' : ''} />${v ? 'on' : 'off'}</label>`;
    else if (kind === 'mode') input = `<select class="input-field" id="${id}" data-path="${path}" data-kind="mode"><option value="oco"${v === 'oco' ? ' selected' : ''}>OCO</option><option value="target"${v === 'target' ? ' selected' : ''}>Target only</option></select>`;
    else input = `<input class="input-field" type="number" step="${kind === 'int' ? 1 : 'any'}" id="${id}" data-path="${path}" data-kind="${kind}" value="${esc(v)}" />`;
    return `<div class="bt-field swg-field${changed ? ' changed' : ''}"><label for="${id}">${esc(label)}</label>${input}
      <div class="muted swg-small">${esc(help)}${changed ? ` · default ${esc(String(get(defaults, path)))}` : ''}</div></div>`;
  }

  function rulesEditor(rules, metrics) {
    return `<div id="swg-rules">${rules.map((r, i) => `<div class="swg-rule" data-i="${i}">
        <input class="input-field" list="swg-metrics" value="${esc(r.metric)}" data-k="metric" aria-label="Metric" />
        <select class="input-field" data-k="op" aria-label="Test"><option${r.op === '>=' ? ' selected' : ''}>&gt;=</option><option${r.op === '<=' ? ' selected' : ''}>&lt;=</option></select>
        <input class="input-field" type="number" step="any" value="${esc(r.value)}" data-k="value" aria-label="Value" />
        <button class="btn-icon-box" data-del="${i}" title="Remove rule" aria-label="Remove rule"><i class="fa-solid fa-xmark"></i></button></div>`).join('')}
      <datalist id="swg-metrics">${metrics.map((m) => `<option value="${esc(m)}">`).join('')}</datalist>
      <button class="btn-secondary" id="swg-rule-add"><i class="fa-solid fa-plus"></i>Add rule</button>
      ${metrics.length ? `<div class="muted swg-small">Uploaded metrics: ${metrics.map(esc).join(', ')}</div>`
        : '<div class="muted swg-small">No fundamentals uploaded yet: <a href="#/swing/fundamentals">upload a CSV</a>. Until then the score is technicals and news only.</div>'}</div>`;
  }

  async function render(el) {
    el.innerHTML = '<div class="section-card"><div class="skeleton" style="height:200px"></div></div>';
    let s;
    try { s = await App.api('swing/settings'); }
    catch (err) { el.innerHTML = App.emptyState('fa-sliders', 'Settings not available', esc(why(err))); return; }
    let rules = (s.values.funnel.fund_rules || []).map((r) => ({ ...r }));
    el.innerHTML = `
      <nav class="sw-crumbs" aria-label="Breadcrumb"><a href="#/swing">Swing</a><i class="fa-solid fa-chevron-right"></i><span aria-current="page">Settings</span></nav>
      <div class="page-header"><div><div class="page-title"><i class="fa-solid fa-sliders"></i>Settings</div>
        <div class="page-subtitle">The rules of each stage. Saved in data/swing/overrides.json; the next weekend scan uses them</div></div>
        <div class="header-meta"><button class="btn-secondary" id="swg-reset"><i class="fa-solid fa-rotate-left"></i>Reset to defaults</button>
          <button class="btn-primary" id="swg-save"><i class="fa-solid fa-floppy-disk"></i>Save</button></div></div>
      ${GROUPS.map(([title, fields]) => `<div class="section-card"><div class="section-title sw-gap">${esc(title)}</div>
        <div class="bt-fields swg-fields">${fields.map((f) => field(f, s.values, s.defaults)).join('')}</div></div>`).join('')}
      <div class="section-card"><div class="section-title sw-gap">Fundamentals rules</div>
        <div class="section-subtitle sw-gap">The fundamentals score is the share of these a stock passes, over the ones it has numbers for</div>
        <div id="swg-rules-box"></div></div>`;
    const drawRules = () => {
      const box = el.querySelector('#swg-rules-box');
      box.innerHTML = rulesEditor(rules, s.metrics);
      box.querySelectorAll('.swg-rule').forEach((row) => row.querySelectorAll('[data-k]').forEach((inp) => inp.addEventListener('change', () => {
        const r = rules[Number(row.dataset.i)];
        r[inp.dataset.k] = inp.dataset.k === 'value' ? Number(inp.value) : inp.value.trim();
      })));
      box.querySelectorAll('[data-del]').forEach((b) => b.addEventListener('click', () => { rules.splice(Number(b.dataset.del), 1); drawRules(); }));
      box.querySelector('#swg-rule-add').addEventListener('click', () => { rules.push({ metric: s.metrics[0] || 'roce', op: '>=', value: 0 }); drawRules(); });
    };
    drawRules();
    el.querySelectorAll('input[data-kind="bool"]').forEach((c) => c.addEventListener('change', () => {
      c.parentElement.lastChild.textContent = c.checked ? 'on' : 'off';
    }));
    el.querySelector('#swg-save').addEventListener('click', async () => {
      const out = {};
      el.querySelectorAll('[data-path]').forEach((inp) => {
        const kind = inp.dataset.kind;
        const v = kind === 'bool' ? inp.checked : kind === 'mode' ? inp.value : kind === 'int' ? parseInt(inp.value, 10) : parseFloat(inp.value);
        if (!same(v, get(s.defaults, inp.dataset.path))) set(out, inp.dataset.path, v);
      });
      const cleaned = rules.filter((r) => r.metric).map((r) => ({ metric: r.metric, op: r.op, value: Number(r.value) }));
      if (!same(cleaned, s.defaults.funnel.fund_rules)) set(out, 'funnel.fund_rules', cleaned);
      try {
        await App.api('swing/settings', {}, { method: 'PUT', body: { overrides: out } });
        App.toast('Saved: the next scan uses these');
        render(el);
      } catch (err) { App.toast(why(err), 'error'); }
    });
    el.querySelector('#swg-reset').addEventListener('click', async () => {
      if (!window.confirm('Reset every Swing setting to config.yaml?')) return;
      try { await App.api('swing/settings', {}, { method: 'DELETE' }); App.toast('Back to the defaults'); render(el); }
      catch (err) { App.toast(why(err), 'error'); }
    });
  }

  // ── Fundamentals data ──
  // A small CSV reader: quoted fields, commas and new lines inside quotes.
  function parseCsv(text) {
    const rows = [];
    let row = [], cell = '', q = false;
    for (let i = 0; i < text.length; i += 1) {
      const c = text[i];
      if (q) {
        if (c === '"' && text[i + 1] === '"') { cell += '"'; i += 1; } else if (c === '"') q = false; else cell += c;
      } else if (c === '"') q = true;
      else if (c === ',') { row.push(cell); cell = ''; }
      else if (c === '\n' || c === '\r') {
        if (c === '\r' && text[i + 1] === '\n') i += 1;
        row.push(cell); cell = '';
        if (row.some((x) => x.trim() !== '')) rows.push(row);
        row = [];
      } else cell += c;
    }
    row.push(cell);
    if (row.some((x) => x.trim() !== '')) rows.push(row);
    return rows;
  }
  const number = (v) => { const n = parseFloat(String(v).replace(/[,₹%\s]/g, '')); return Number.isFinite(n) ? n : null; };

  async function renderFundamentals(el) {
    el.innerHTML = '<div class="section-card"><div class="skeleton" style="height:200px"></div></div>';
    let sum;
    try { sum = await App.api('swing/fundamentals'); }
    catch (err) { el.innerHTML = App.emptyState('fa-file-csv', 'Fundamentals not available', esc(why(err))); return; }
    const today = new Date().toISOString().slice(0, 10);
    el.innerHTML = `
      <nav class="sw-crumbs" aria-label="Breadcrumb"><a href="#/swing">Swing</a><i class="fa-solid fa-chevron-right"></i><span aria-current="page">Fundamentals data</span></nav>
      <div class="page-header"><div><div class="page-title"><i class="fa-solid fa-file-csv"></i>Fundamentals data</div>
        <div class="page-subtitle">Upload numbers per stock (ROCE, P/B, debt to equity …) from a CSV, e.g. a Screener export. Kite has none</div></div>
        <div class="header-meta">${sum.stocks ? '<button class="btn-secondary" id="swg-fd-clear"><i class="fa-solid fa-trash"></i>Clear all</button>' : ''}</div></div>
      <div class="row row-1-1">
        <div class="section-card"><div class="section-title sw-gap">Upload</div>
          <div class="swg-upload">
            <input type="file" id="swg-fd-file" accept=".csv,text/csv" class="input-field" />
            <label class="muted swg-small">As of <input type="date" class="input-field" id="swg-fd-asof" value="${today}" /></label>
          </div>
          <div id="swg-fd-map"><p class="muted swg-small">Choose a file: its first row must be the column names. You then pick the symbol column (the NSE code) and the numbers to keep.</p></div>
        </div>
        <div class="section-card"><div class="section-title sw-gap">Stored</div>
          ${sum.metrics.length ? `<p class="muted swg-small">${sum.stocks} stocks</p><div class="table-wrap"><table class="data-table"><thead><tr><th class="sw-left">Metric</th><th>Stocks</th><th class="sw-left">As of</th></tr></thead>
            <tbody>${sum.metrics.map((m) => `<tr><td class="sw-left"><code>${esc(m.metric)}</code></td><td>${m.stocks}</td><td class="sw-left">${m.as_of ? App.prettyDate(m.as_of) : '—'}</td></tr>`).join('')}</tbody></table></div>
            <p class="muted swg-small">Use these keys in Settings › Fundamentals rules.</p>`
            : '<p class="muted swg-small">Nothing yet.</p>'}
        </div>
      </div>`;
    el.querySelector('#swg-fd-clear')?.addEventListener('click', async () => {
      if (!window.confirm('Delete every uploaded fundamentals number? This cannot be undone.')) return;
      try { await App.api('swing/fundamentals', {}, { method: 'DELETE' }); App.toast('Cleared'); renderFundamentals(el); }
      catch (err) { App.toast(why(err), 'error'); }
    });
    el.querySelector('#swg-fd-file').addEventListener('change', async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      const rows = parseCsv(await file.text());
      const box = el.querySelector('#swg-fd-map');
      if (rows.length < 2) { box.innerHTML = '<p class="sw-res swg-small">No data rows in this file.</p>'; return; }
      const head = rows[0].map((h) => h.trim());
      const body = rows.slice(1);
      const guess = head.findIndex((h) => /^(nse ?code|symbol|nse symbol|ticker)$/i.test(h));
      const numeric = head.map((_, i) => body.filter((r) => number(r[i]) !== null).length >= body.length * 0.6);
      box.innerHTML = `<p class="muted swg-small">${body.length} rows, ${head.length} columns</p>
        <label class="swg-small">Symbol column <select class="input-field" id="swg-fd-sym">${head.map((h, i) => `<option value="${i}"${i === (guess >= 0 ? guess : 0) ? ' selected' : ''}>${esc(h)}</option>`).join('')}</select></label>
        <div class="swg-cols">${head.map((h, i) => (numeric[i] ? `<label class="sw-check"><input type="checkbox" data-col="${i}" checked />${esc(h)}</label>` : '')).join('')}</div>
        <button class="btn-primary" id="swg-fd-upload"><i class="fa-solid fa-upload"></i>Upload</button>`;
      box.querySelector('#swg-fd-upload').addEventListener('click', async () => {
        const symCol = Number(box.querySelector('#swg-fd-sym').value);
        const cols = [...box.querySelectorAll('[data-col]:checked')].map((c) => Number(c.dataset.col)).filter((i) => i !== symCol);
        if (!cols.length) { App.toast('Pick at least one number column', 'error'); return; }
        const payload = body.map((r) => ({ symbol: (r[symCol] || '').trim().toUpperCase(),
          metrics: Object.fromEntries(cols.map((i) => [head[i], number(r[i])]).filter(([, v]) => v !== null)) }))
          .filter((r) => r.symbol);
        try {
          const out = await App.api('swing/fundamentals', {}, { method: 'POST', body: { as_of: el.querySelector('#swg-fd-asof').value || null, rows: payload } });
          App.toast(`${out.values} numbers for ${out.stocks} stocks saved`);
          renderFundamentals(el);
        } catch (err) { App.toast(why(err), 'error'); }
      });
    });
  }

  App.swingSettings = { render, fundamentals: { render: renderFundamentals } };
})();
