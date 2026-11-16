/* Swing › Picks: #/swing/picks. The weekend funnel from picks.json (written by the scan):
   1. sectors: which indices are at support with their trend up, with RSI and relative strength;
   2. stocks: the members of those sectors with a plan (entry at the support zone's top, stop under
      it, target at the next resistance);
   3. ranking: technicals first, a small weight for news (results and macro dates from the market
      calendar) and fundamentals (your uploaded numbers). The top ones fill the Algo picks watchlist.
   A ranking aid, not an edge: every pick is tracked against a random twin in the Picks journal. */

(() => {
  const esc = (v) => App.esc(v);
  const STATE_CLASS = { 'look for longs': 'tone-green', 'at support, downtrend': 'tone-amber', 'near resistance': 'tone-red',
    'mid-range': 'tone-slate', 'no zones': 'tone-slate' };
  const S = { show: 'top', sector: '', sectorsOpen: false };
  let data = null;

  const n1 = (v) => (v === null || v === undefined ? '—' : Number(v).toFixed(1));
  const signed = (v) => (v === null || v === undefined ? '—' : `<span class="${App.tone(v)}">${v > 0 ? '+' : ''}${Number(v).toFixed(1)}</span>`);
  const pill = (state) => `<span class="sw-pill ${STATE_CLASS[state] || 'tone-slate'}">${esc(state)}</span>`;
  const score = (v) => (v === null || v === undefined ? '<span class="muted">—</span>'
    : `<span class="swg-score" style="--w:${Math.max(0, Math.min(100, v))}%">${Math.round(v)}</span>`);

  function sectorsTable() {
    const rows = data.sectors.filter((s) => S.sectorsOpen || s.passed);
    return `<div class="table-wrap"><table class="data-table clickable" id="swg-sectors"><thead><tr>
      <th>Index</th><th class="sw-left">Kind</th><th class="sw-left">State</th><th>Close</th><th>1 week</th><th>1 month</th>
      <th>Support ATR</th><th>Resistance ATR</th><th>Trend</th><th>RSI</th><th>RS 4 wk</th><th>RS 13 wk</th><th>Plans</th></tr></thead>
      <tbody>${rows.map((s) => {
        const plans = data.picks.filter((p) => p.sector === s.name).length;
        return `<tr data-sector="${esc(s.name)}"${S.sector === s.name ? ' class="sw-on"' : ''}>
          <td><a href="#/swing/${encodeURIComponent(s.name)}">${esc(s.name)}</a></td><td class="sw-left muted">${esc(s.kind)}</td>
          <td class="sw-left">${pill(s.state)}</td><td>${App.num(s.close)}</td><td>${signed(s.week_pct)}%</td><td>${signed(s.month_pct)}%</td>
          <td>${n1(s.support_atr)}</td><td>${n1(s.resistance_atr)}</td>
          <td>${s.trend_up ? '<i class="fa-solid fa-arrow-trend-up sw-sup" title="Up"></i>' : '<i class="fa-solid fa-arrow-trend-down sw-res" title="Down"></i>'}</td>
          <td>${n1(s.rsi)}${s.rsi_rising === null ? '' : s.rsi_rising ? ' <i class="fa-solid fa-caret-up sw-sup"></i>' : ' <i class="fa-solid fa-caret-down sw-res"></i>'}</td>
          <td>${signed(s.rs_4w)}</td><td>${signed(s.rs_13w)}</td><td>${plans || '—'}</td></tr>`;
      }).join('')}</tbody></table></div>`;
  }

  function picksTable() {
    let rows = data.picks;
    if (S.show === 'top') rows = rows.filter((p) => p.rank);
    if (S.sector) rows = rows.filter((p) => p.sector === S.sector);
    if (!rows.length) return '<p class="muted swg-pad">No plans with these filters.</p>';
    return `<div class="table-wrap"><table class="data-table clickable" id="swg-picks"><thead><tr>
      <th>#</th><th>Stock</th><th class="sw-left">Sector</th><th>Close</th><th>Entry</th><th>ATR to entry</th><th>Stop</th><th>Target</th>
      <th>Risk</th><th>Reward</th><th>R:R</th><th>Qty</th><th>Score</th><th>Tech</th><th>News</th><th>Fund</th><th class="sw-left">Flags</th></tr></thead>
      <tbody>${rows.map((p) => `<tr data-symbol="${esc(p.symbol)}"${p.rank ? ' class="swg-top"' : ''}>
        <td>${p.rank || ''}</td>
        <td><a href="#/swing/${encodeURIComponent(p.symbol)}">${esc(p.symbol)}</a>${App.swingWatch.addButton(p.symbol)}
          <div class="muted swg-small">${esc(p.industry)}</div></td>
        <td class="sw-left">${esc(p.sector || '—')}${p.sector_ok ? '' : ' <span class="muted swg-small">(not passed)</span>'}</td>
        <td>${App.num(p.close)}</td><td><b>${App.num(p.entry)}</b><div class="muted swg-small">${App.num(p.zone[0])}–${App.num(p.zone[1])}</div></td>
        <td>${n1(p.dist_atr)}</td><td class="sw-res">${App.num(p.stop)}</td><td class="sw-sup">${App.num(p.target)}</td>
        <td class="sw-res">−${n1(p.risk_pct)}%</td><td class="sw-sup">+${n1(p.reward_pct)}%</td><td>${Number(p.rr).toFixed(2)}</td>
        <td title="₹${App.num(p.amount, 0)} ÷ entry">${p.qty}</td>
        <td>${score(p.score)}</td><td title="${esc(Object.entries(p.tech_parts).map(([k, v]) => `${k} ${Math.round(v * 100)}`).join(' · '))}">${n1(p.tech)}</td>
        <td>${n1(p.news)}</td><td title="${esc((p.fund_checks || []).map((c) => `${c.metric} ${c.actual} ${c.op} ${c.value}: ${c.ok ? 'pass' : 'fail'}`).join('\n'))}">${n1(p.fund)}</td>
        <td class="sw-left">${(p.flags || []).map((f) => `<span class="sw-pill tone-amber">${esc(f)}</span>`).join(' ') || '<span class="muted">—</span>'}</td>
      </tr>`).join('')}</tbody></table></div>`;
  }

  function draw(el) {
    el.querySelector('#swg-sectors-box').innerHTML = sectorsTable();
    el.querySelector('#swg-picks-box').innerHTML = picksTable();
    el.querySelectorAll('#swg-sectors tbody tr').forEach((tr) => tr.addEventListener('click', (e) => {
      if (e.target.closest('a, button')) return;
      S.sector = S.sector === tr.dataset.sector ? '' : tr.dataset.sector;
      if (S.sector) S.show = 'all';
      syncFilters(el);
      draw(el);
    }));
    el.querySelectorAll('#swg-picks tbody tr').forEach((tr) => tr.addEventListener('click', (e) => {
      if (!e.target.closest('a, button')) location.hash = `#/swing/${encodeURIComponent(tr.dataset.symbol)}`;
    }));
  }

  function syncFilters(el) {
    el.querySelectorAll('.swg-show').forEach((b) => b.classList.toggle('active', b.dataset.show === S.show));
    el.querySelector('#swg-sector-pick').value = S.sector;
  }

  async function render(el) {
    el.innerHTML = '<div class="section-card"><div class="skeleton" style="height:200px"></div></div>';
    data = await App.api('swing/picks');
    const head = `<nav class="sw-crumbs" aria-label="Breadcrumb"><a href="#/swing">Swing</a><i class="fa-solid fa-chevron-right"></i><span aria-current="page">Picks</span></nav>
      <div class="page-header"><div><div class="page-title"><i class="fa-solid fa-crosshairs"></i>Picks</div>
        <div class="page-subtitle">Sectors at support → their stocks at support → a plan for each, ranked</div></div>
        ${data.available ? `<div class="header-meta">Candles to ${App.prettyDate(data.candles_to)} · scanned ${esc(data.generated_at.slice(0, 16).replace('T', ' '))}</div>` : ''}</div>`;
    if (!data.available) {
      el.innerHTML = head + App.emptyState('fa-crosshairs', 'No picks yet', 'Run the weekend scan: it writes the sectors, the plans and the ranking.',
        '<a class="btn-primary" href="#/swing/run"><i class="fa-solid fa-play"></i>Weekend run</a>');
      return;
    }
    const f = data.funnel;
    const st = data.settings;
    const stale = Object.entries(data.calendar_fresh || {}).filter(([, ok]) => !ok).map(([k]) => k);
    el.innerHTML = `${head}
      <p class="sw-note"><i class="fa-solid fa-circle-info"></i>${esc(data.ranking_note)}</p>
      ${data.macro_flags.length ? `<p class="banner banner-warn"><i class="fa-solid fa-calendar-day"></i>High-impact events in the next days: ${data.macro_flags.map(esc).join(' · ')}</p>` : ''}
      ${stale.length ? `<p class="sw-note swg-small"><i class="fa-solid fa-triangle-exclamation"></i>Calendar not fetched recently: ${stale.map(esc).join(', ')}. News flags may be missing (Calendar › Refresh).</p>` : ''}
      <div class="swg-funnel">
        ${[['Sectors', f.sectors, 'indices scanned'], ['At support', f.sectors_passed, `${st.sector_need_trend ? 'trend up, ' : ''}≤ ${st.sector_near_atr} ATR`],
          ['Stocks', f.considered, st.require_sector ? 'liquid members of those' : 'liquid stocks'], ['Near support', f.near_support, `≤ ${st.near_atr} ATR`],
          ['Plans', f.plans, `R:R ≥ ${st.min_rr}, stop ${st.stop_atr} ATR under`], ['Top picks', f.top, `≤ ${st.max_per_industry} per industry`]]
          .map(([label, n, sub], i) => `${i ? '<i class="fa-solid fa-chevron-right swg-arrow"></i>' : ''}<div class="swg-step"><div class="swg-step-n">${n}</div>
            <div class="swg-step-label">${label}</div><div class="muted swg-small">${esc(sub)}</div></div>`).join('')}
      </div>
      <div class="section-card">
        <div class="top-bar"><div><div class="section-title">1 · Sectors</div>
          <div class="section-subtitle">An index passes when it is in a zone or close above a support${st.sector_need_trend ? ', with its trend up' : ''}. RS = return minus ${esc(data.benchmark || 'the benchmark (not downloaded yet)')}, in points. Click a row to see its plans</div></div>
          <div class="top-bar-group"><label class="sw-check"><input type="checkbox" id="swg-all-sectors"${S.sectorsOpen ? ' checked' : ''} />Show every sector</label></div></div>
        <div id="swg-sectors-box"></div>
      </div>
      <div class="section-card">
        <div class="top-bar"><div><div class="section-title">2 · 3 · Plans, ranked</div>
          <div class="section-subtitle">Score = ${Math.round(st.weights.tech * 100)}% technicals, ${Math.round(st.weights.news * 100)}% news, ${Math.round(st.weights.fund * 100)}% fundamentals (parts a stock has no data for are left out). Hover Tech or Fund for the parts</div></div>
          <div class="top-bar-group">
            <div class="segmented" role="group" aria-label="Show">
              <button class="swg-show" data-show="top">Top ${f.top}</button><button class="swg-show" data-show="all">All ${f.plans}</button></div>
            <select class="input-field" id="swg-sector-pick" aria-label="Sector"><option value="">All sectors</option>${
              data.sectors.filter((s) => data.picks.some((p) => p.sector === s.name)).map((s) => `<option>${esc(s.name)}</option>`).join('')}</select>
          </div></div>
        <div id="swg-picks-box"></div>
      </div>`;
    syncFilters(el);
    draw(el);
    el.querySelectorAll('.swg-show').forEach((b) => b.addEventListener('click', () => { S.show = b.dataset.show; syncFilters(el); draw(el); }));
    el.querySelector('#swg-sector-pick').addEventListener('change', (e) => { S.sector = e.target.value; if (S.sector) S.show = 'all'; syncFilters(el); draw(el); });
    el.querySelector('#swg-all-sectors').addEventListener('change', (e) => { S.sectorsOpen = e.target.checked; draw(el); });
  }

  App.swingPicks = { render };
})();
