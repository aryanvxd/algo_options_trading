/* Swing › Picks journal: #/swing/journal. Every Algo pick since the first scan, followed forward on
   daily candles with the setup backtest's rules, next to its random twin (same stock and week, a
   random entry, the same stop and target distances). If the picks don't beat their twins over the
   weeks, the ranking isn't choosing better entries than chance. */

(() => {
  const esc = (v) => App.esc(v);
  const STATUS = { pending: ['Pending', 'tone-slate'], open: ['Open', 'tone-blue'], target: ['Target', 'tone-green'],
    stop: ['Stop', 'tone-red'], time: ['Time exit', 'tone-amber'], expired: ['Not filled', 'tone-slate'] };
  const F = { week: '', status: '' };
  const pill = (s) => `<span class="sw-pill ${(STATUS[s] || ['', 'tone-slate'])[1]}">${esc((STATUS[s] || [s])[0])}</span>`;
  const ret = (v) => (v === null || v === undefined ? '—' : `<span class="${App.tone(v)}">${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%</span>`);
  const val = (v, suffix = '') => (v === null || v === undefined ? '—' : `${v}${suffix}`);

  function kpis(st) {
    const p = st.picks, t = st.twins;
    const pair = (a, b, suffix = '%') => `${val(a, suffix)} <span class="muted swg-small">vs ${val(b, suffix)}</span>`;
    return `<div class="kpi-strip">
      ${App.kpi('Picks', p.picks, '', 'fa-crosshairs', 'tone-blue', `${p.pending} pending · ${p.open} open · ${p.closed} closed`)}
      ${App.kpi('Fill rate', pair(p.fill_rate, t.fill_rate), '', 'fa-arrow-right-to-bracket', 'tone-slate', 'picks vs random twins')}
      ${App.kpi('Win rate', pair(p.win_rate, t.win_rate), '', 'fa-trophy', 'tone-green', 'closed trades above zero')}
      ${App.kpi('Average return', pair(p.avg_return, t.avg_return), App.tone(p.avg_return), 'fa-chart-line', 'tone-violet', 'closed, net of costs')}
      ${App.kpi('Open now', pair(p.avg_open_return, t.avg_open_return), App.tone(p.avg_open_return), 'fa-hourglass-half', 'tone-amber', 'at the last close')}
    </div>`;
  }

  function table(rows) {
    const shown = rows.filter((r) => (!F.week || r.scan_date === F.week) && (!F.status || r.status === F.status));
    if (!shown.length) return '<p class="muted swg-pad">No picks with these filters.</p>';
    return `<div class="table-wrap"><table class="data-table clickable"><thead><tr>
      <th class="sw-left">Week</th><th>#</th><th>Stock</th><th class="sw-left">Sector</th><th>Mode</th><th>Entry</th><th>Stop</th><th>Target</th><th>Score</th>
      <th class="sw-left">Status</th><th>Filled</th><th>Exit</th><th>Last close</th><th>Return</th><th class="sw-left">Twin</th><th>Twin return</th></tr></thead>
      <tbody>${shown.map((r) => `<tr data-symbol="${esc(r.symbol)}">
        <td class="sw-left">${App.prettyDate(r.scan_date)}</td><td>${r.rank || ''}</td>
        <td><a href="#/swing/${encodeURIComponent(r.symbol)}">${esc(r.symbol)}</a></td><td class="sw-left muted">${esc(r.sector || '')}</td>
        <td>${r.mode === 'oco' ? 'OCO' : 'Target only'}</td><td>${App.num(r.entry)}</td><td class="sw-res">${App.num(r.stop)}</td><td class="sw-sup">${App.num(r.target)}</td>
        <td>${r.score === null ? '—' : Math.round(r.score)}</td><td class="sw-left">${pill(r.status)}</td>
        <td>${r.fill_date ? `${App.num(r.fill_price)}<div class="muted swg-small">${esc(r.fill_date)}</div>` : '—'}</td>
        <td>${r.exit_price ? `${App.num(r.exit_price)}<div class="muted swg-small">${esc(r.exit_date)}</div>` : r.exit_date ? `<span class="muted swg-small">${esc(r.exit_date)}</span>` : '—'}</td>
        <td>${App.num(r.last_close)}</td><td>${ret(r.return_pct)}</td>
        <td class="sw-left">${r.twin ? `${pill(r.twin.status)} <span class="muted swg-small">@ ${App.num(r.twin.entry)}</span>` : '—'}</td><td>${ret(r.twin?.return_pct)}</td>
      </tr>`).join('')}</tbody></table></div>`;
  }

  async function render(el) {
    el.innerHTML = '<div class="section-card"><div class="skeleton" style="height:200px"></div></div>';
    let j;
    try { j = await App.api('swing/journal'); }
    catch (err) { el.innerHTML = App.emptyState('fa-book', 'Journal not available', esc(App.swingWatch.why(err))); return; }
    el.innerHTML = `
      <nav class="sw-crumbs" aria-label="Breadcrumb"><a href="#/swing">Swing</a><i class="fa-solid fa-chevron-right"></i><span aria-current="page">Picks journal</span></nav>
      <div class="page-header"><div><div class="page-title"><i class="fa-solid fa-book"></i>Picks journal</div>
        <div class="page-subtitle">Each scan's top picks, followed forward on daily candles, against random twins</div></div>
        <div class="header-meta"><button class="btn-secondary" id="swg-j-update"><i class="fa-solid fa-rotate"></i>Update now</button></div></div>
      <p class="sw-note"><i class="fa-solid fa-circle-info"></i>${esc(j.note)} Updates use the stored candles: run the weekend scan (with Refresh) for the latest.</p>
      ${j.rows.length ? `${kpis(j.stats)}
        <div class="section-card"><div class="top-bar"><div class="section-title">Picks</div><div class="top-bar-group">
          <select class="input-field" id="swg-j-week" aria-label="Week"><option value="">Every week</option>${j.weeks.map((w) => `<option value="${w}"${w === F.week ? ' selected' : ''}>${App.prettyDate(w)}</option>`).join('')}</select>
          <select class="input-field" id="swg-j-status" aria-label="Status"><option value="">Any status</option>${Object.entries(STATUS).map(([k, [label]]) => `<option value="${k}"${k === F.status ? ' selected' : ''}>${label}</option>`).join('')}</select>
        </div></div><div id="swg-j-table">${table(j.rows)}</div></div>`
        : App.emptyState('fa-book', 'No picks yet', 'The weekend scan journals its top picks here.', '<a class="btn-primary" href="#/swing/run"><i class="fa-solid fa-play"></i>Weekend run</a>')}`;
    const redraw = () => {
      const box = el.querySelector('#swg-j-table');
      if (!box) return;
      box.innerHTML = table(j.rows);
      box.querySelectorAll('tbody tr').forEach((tr) => tr.addEventListener('click', (e) => {
        if (!e.target.closest('a, button')) location.hash = `#/swing/${encodeURIComponent(tr.dataset.symbol)}`;
      }));
    };
    redraw();
    el.querySelector('#swg-j-week')?.addEventListener('change', (e) => { F.week = e.target.value; redraw(); });
    el.querySelector('#swg-j-status')?.addEventListener('change', (e) => { F.status = e.target.value; redraw(); });
    el.querySelector('#swg-j-update').addEventListener('click', async () => {
      try {
        const out = await App.api('swing/journal/update', {}, { method: 'POST' });
        App.toast(`${out.updated} picks and twins followed forward`);
        render(el);
      } catch (err) { App.toast(App.swingWatch.why(err), 'error'); }
    });
  }

  App.swingJournal = { render };
})();
