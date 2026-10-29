/* Market calendar: #/calendar/<tab> with Economic, Results and Holidays & expiries.
   Events come from the DB (fetched daily: Forex Factory weekly feed, NSE results calendar, our
   India list in config/calendar/); Refresh fetches the web sources now. */

(() => {
  const TABS = [
    ['economic', 'Economic', 'fa-globe'],
    ['results', 'Results', 'fa-building'],
    ['days', 'Holidays & expiries', 'fa-calendar-days'],
  ];
  const IMPACT = { High: 'tone-red', Medium: 'tone-amber', Low: 'tone-slate', Holiday: 'tone-violet' };
  const SOURCE = { forexfactory: 'Forex Factory', nse_results: 'NSE results', india: 'India list' };

  let st = null;

  const today = () => App.meta?.today || App.isoDate(new Date());
  const pref = (k, d) => App.prefs.get('calendar.' + k, d);
  const setPref = (k, v) => App.prefs.set('calendar.' + k, v);

  function render(el, args) {
    const tab = TABS.some((t) => t[0] === args[0]) ? args[0] : 'economic';
    st = { tab, start: st?.start || today(), days: pref('days', 21), impact: pref('impact', 'all'),
      countries: pref('countries', []), q: '', nifty: pref('nifty', true), data: null };
    el.innerHTML = `
      <div class="page-header">
        <div><div class="page-title"><i class="fa-solid fa-calendar-days"></i>Market calendar</div>
          <div class="page-subtitle">Economic releases, company results, holidays and expiries — and the days each algo trades</div></div>
        <div class="header-meta"><span id="cal-sources"></span>
          <button class="btn-secondary" id="cal-refresh" title="Fetch the web sources now"><i class="fa-solid fa-rotate"></i>Refresh</button></div>
      </div>
      <nav class="tabs" aria-label="Calendar sections">
        ${TABS.map(([k, label, icon]) => `<a class="tab${k === tab ? ' active' : ''}" href="#/calendar/${k}"><i class="fa-solid ${icon}"></i>${label}</a>`).join('')}
      </nav>
      <div class="top-bar">
        <div class="top-bar-group" id="cal-filters"></div>
        <div class="top-bar-group date-nav">
          <button class="btn-icon-box" id="cal-prev" title="Earlier" aria-label="Earlier"><i class="fa-solid fa-chevron-left"></i></button>
          <input type="date" class="input-field" id="cal-start" aria-label="From" value="${st.start}" />
          <select class="input-field" id="cal-days" aria-label="Range">${[[7, '1 week'], [21, '3 weeks'], [42, '6 weeks'], [90, '3 months']]
            .map(([d, l]) => `<option value="${d}"${d === st.days ? ' selected' : ''}>${l}</option>`).join('')}</select>
          <button class="btn-icon-box" id="cal-next" title="Later" aria-label="Later"><i class="fa-solid fa-chevron-right"></i></button>
          <button class="btn-secondary" id="cal-today">Today</button>
        </div>
      </div>
      <div id="cal-body"></div>`;
    const shift = (n) => { st.start = App.shiftDate(st.start, n * st.days); load(); };
    el.querySelector('#cal-prev').addEventListener('click', () => shift(-1));
    el.querySelector('#cal-next').addEventListener('click', () => shift(1));
    el.querySelector('#cal-today').addEventListener('click', () => { st.start = today(); load(); });
    el.querySelector('#cal-start').addEventListener('change', (e) => { if (e.target.value) { st.start = e.target.value; load(); } });
    el.querySelector('#cal-days').addEventListener('change', (e) => { st.days = Number(e.target.value); setPref('days', st.days); load(); });
    el.querySelector('#cal-refresh').addEventListener('click', refreshSources);
    load();
  }

  async function refreshSources() {
    const b = document.getElementById('cal-refresh');
    b.disabled = true;
    try {
      const out = await App.api('calendar/refresh', {}, { method: 'POST' });
      const failed = Object.entries(out).filter(([, v]) => String(v).startsWith('failed'));
      App.toast(failed.length ? `Refreshed, but ${failed.map(([k]) => SOURCE[k] || k).join(', ')} failed` : 'Calendar refreshed',
        failed.length ? 'error' : 'info');
    } catch (err) { App.toast('Refresh failed: ' + err.message, 'error'); }
    b.disabled = false;
    load();
  }

  async function load() {
    const body = document.getElementById('cal-body');
    if (!body) return;
    document.getElementById('cal-start').value = st.start;
    body.innerHTML = '<div class="section-card"><div class="skeleton" style="height:120px"></div></div>';
    try {
      st.data = await App.api('calendar', { start: st.start, days: st.days });
    } catch (err) {
      body.innerHTML = App.emptyState('fa-plug-circle-xmark', 'Could not load the calendar', App.esc(err.message));
      return;
    }
    sourcesLine();
    draw();
  }

  function sourcesLine() {
    const src = st.data.sources || {};
    document.getElementById('cal-sources').innerHTML = Object.keys(SOURCE).map((k) => {
      const s = src[k];
      if (!s) return `<span class="chip tone-slate" title="never fetched"><span class="chip-dot"></span>${SOURCE[k]}: —</span>`;
      const tone = s.status === 'OK' ? 'tone-green' : 'tone-red';
      return `<span class="chip ${tone}" title="${App.esc(s.note || `${s.rows} events`)}"><span class="chip-dot"></span>${SOURCE[k]} ${s.fetched_at.slice(8, 10)}/${s.fetched_at.slice(5, 7)} ${s.fetched_at.slice(11, 16)}</span>`;
    }).join('');
  }

  function draw() {
    const body = document.getElementById('cal-body');
    if (st.tab === 'economic') economic(body);
    else if (st.tab === 'results') results(body);
    else days(body);
  }

  // ── filters ──

  function filterBar(html, bind) {
    const box = document.getElementById('cal-filters');
    box.innerHTML = html;
    bind(box);
  }

  // Day header: "Fri 30 Oct" plus Today / Weekend marks and which algos trade that day.
  function dayHead(iso) {
    const [y, mo, d] = iso.split('-').map(Number);
    const dt = new Date(y, mo - 1, d);
    const wd = dt.getDay();
    const marks = [];
    if (iso === today()) marks.push(App.chip('Today', 'tone-green'));
    if (wd === 0 || wd === 6) marks.push(App.chip('Weekend', 'tone-violet'));
    const hol = (st.data.holidays || []).find((h) => h.date === iso);
    if (hol) marks.push(App.chip(`Market holiday${hol.description ? ': ' + App.esc(hol.description) : ''}`, 'tone-red'));
    const exp = (st.data.expiries || []).find((x) => x.date === iso);
    if (exp) marks.push(App.chip(`${exp.monthly ? 'Monthly' : 'Weekly'} expiry`, 'tone-amber'));
    return `<div class="cal-day${wd === 0 || wd === 6 ? ' weekend' : ''}"><span class="cal-day-name">${dt.toLocaleDateString('en-IN',
      { weekday: 'short', day: 'numeric', month: 'short' })}</span>${marks.join('')}</div>`;
  }

  function grouped(items, keyFn, rowFn) {
    const groups = {};
    items.forEach((x) => { (groups[keyFn(x)] = groups[keyFn(x)] || []).push(x); });
    return Object.keys(groups).sort().map((k) => dayHead(k) + groups[k].map(rowFn).join('')).join('');
  }

  // ── Economic ──

  function economic(body) {
    const all = st.data.economic;
    const countries = [...new Set(all.map((e) => e.country).filter(Boolean))].sort((a, b) => (a === 'IN' ? -1 : b === 'IN' ? 1 : a.localeCompare(b)));
    filterBar(`
      <div class="segmented" id="f-impact">${[['all', 'All'], ['Medium', 'Medium+'], ['High', 'High']]
        .map(([v, l]) => `<button data-v="${v}" class="${st.impact === v ? 'active' : ''}">${l}</button>`).join('')}</div>
      <div class="country-chips">${countries.map((c) => `<button class="country${st.countries.length && !st.countries.includes(c) ? ' off' : ''}" data-c="${c}">${c}</button>`).join('')}</div>
      <input class="input-field" id="f-q" placeholder="Search" value="${App.esc(st.q)}" aria-label="Search events" style="width:150px" />`, (box) => {
      box.querySelector('#f-impact').addEventListener('click', (e) => {
        const b = e.target.closest('button'); if (!b) return; st.impact = b.dataset.v; setPref('impact', st.impact); economic(body);
      });
      box.querySelectorAll('[data-c]').forEach((b) => b.addEventListener('click', () => {
        const c = b.dataset.c;
        // first click narrows to one country; later clicks add/remove; emptying shows all again
        st.countries = !st.countries.length ? [c] : st.countries.includes(c) ? st.countries.filter((x) => x !== c) : [...st.countries, c];
        setPref('countries', st.countries); economic(body);
      }));
      box.querySelector('#f-q').addEventListener('input', (e) => { st.q = e.target.value; drawEconomicList(body); });
    });
    drawEconomicList(body);
  }

  function drawEconomicList(body) {
    const rank = { High: 3, Medium: 2, Low: 1, Holiday: 0 };
    const need = st.impact === 'all' ? -1 : rank[st.impact];
    const q = st.q.trim().toLowerCase();
    const rows = st.data.economic.filter((e) => (rank[e.impact] ?? 0) >= need
      && (!st.countries.length || st.countries.includes(e.country)) && (!q || e.title.toLowerCase().includes(q)));
    const src = st.data.sources?.forexfactory;
    const note = `<div class="chart-foot" style="margin:-6px 0 12px">Global events come from Forex Factory (current week only, no actual values);
      India's from <code>config/calendar/</code> (RBI, MoSPI). ${src ? '' : 'Press <b>Refresh</b> to fetch them.'}</div>`;
    body.innerHTML = note + (rows.length ? `<div class="section-card cal-list">${grouped(rows, (e) => e.ts.slice(0, 10), econRow)}</div>`
      : App.emptyState('fa-calendar-xmark', 'No events', 'Nothing matches these filters in this range.'));
  }

  function econRow(e) {
    const time = e.all_day ? 'All day' : e.ts.slice(11, 16) + ' IST';
    const planned = e.detail?.planned ? ` <span class="muted small" title="moved from ${e.detail.planned}: MoSPI releases on the next working day">(planned ${e.detail.planned.slice(8, 10)}/${e.detail.planned.slice(5, 7)})</span>` : '';
    return `<div class="cal-row">
      <div class="cal-main"><span class="country-badge c-${App.esc(e.country || 'XX')}">${App.esc(e.country || '—')}</span>
        <div><div class="cal-title">${App.esc(e.title)}${planned}</div>
          <div class="muted small">${time}${e.detail?.note ? ' · ' + App.esc(e.detail.note) : ''}</div></div></div>
      <div>${e.impact ? App.chip(e.impact, IMPACT[e.impact] || 'tone-slate') : ''}</div>
      <div class="cal-values"><div><span class="fact-label">Expected</span>${App.esc(e.forecast || '—')}</div>
        <div><span class="fact-label">Actual</span>${App.esc(e.actual || '—')}</div>
        <div><span class="fact-label">Previous</span>${App.esc(e.previous || '—')}</div></div>
    </div>`;
  }

  // ── Results ──

  function results(body) {
    filterBar(`<label class="toggle"><input type="checkbox" id="f-nifty" ${st.nifty ? 'checked' : ''}/> NIFTY 50 only</label>
      <input class="input-field" id="f-q" placeholder="Company or symbol" value="${App.esc(st.q)}" aria-label="Search companies" style="width:180px" />`, (box) => {
      box.querySelector('#f-nifty').addEventListener('change', (e) => { st.nifty = e.target.checked; setPref('nifty', st.nifty); drawResults(body); });
      box.querySelector('#f-q').addEventListener('input', (e) => { st.q = e.target.value; drawResults(body); });
    });
    drawResults(body);
  }

  function drawResults(body) {
    const q = st.q.trim().toLowerCase();
    const rows = st.data.results.filter((e) => (!st.nifty || e.detail?.nifty50)
      && (!q || e.title.toLowerCase().includes(q) || (e.symbol || '').toLowerCase().includes(q)));
    const n50 = st.data.results.filter((e) => e.detail?.nifty50).length;
    const head = `<div class="chart-foot" style="margin:-6px 0 12px">Board meetings for results from NSE's event calendar ·
      ${st.data.results.length} in range, ${n50} of them NIFTY 50 companies. Results are often announced after the close.</div>`;
    body.innerHTML = head + (rows.length ? `<div class="section-card cal-list">${grouped(rows, (e) => e.ts.slice(0, 10), (e) => `
      <div class="cal-row results">
        <div class="cal-main"><span class="country-badge">${App.esc(e.symbol || '')}</span>
          <div><div class="cal-title">${App.esc(e.detail?.company || e.title)}</div>
            <div class="muted small" title="${App.esc(e.detail?.description || '')}">${App.esc(e.detail?.purpose || '')}</div></div></div>
        <div>${e.detail?.nifty50 ? App.chip('NIFTY 50', 'tone-blue') : ''}</div>
      </div>`)}</div>` : App.emptyState('fa-building', 'No results dates', st.nifty
      ? 'No NIFTY 50 company has a results meeting in this range.' : 'Nothing in this range yet: press Refresh.'));
  }

  // ── Holidays & expiries (with the days each algo trades) ──

  function days(body) {
    filterBar('<span class="muted small">Trading days, holidays and expiries in the range, with the algos that trade each day</span>', () => {});
    const start = st.data.start, end = st.data.end;
    const out = [];
    for (let d = start; d <= end; d = App.shiftDate(d, 1)) {
      const [y, mo, dd] = d.split('-').map(Number);
      const wd = new Date(y, mo - 1, dd).getDay();
      const hol = st.data.holidays.find((h) => h.date === d);
      const exp = st.data.expiries.find((x) => x.date === d);
      const algos = st.data.algo_days[d] || [];
      const closed = wd === 0 || wd === 6 || hol;
      out.push(`<div class="cal-row day-row${closed ? ' closed' : ''}">
        <div class="cal-main"><span class="cal-date">${new Date(y, mo - 1, dd).toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short' })}</span>
          <div>${d === today() ? App.chip('Today', 'tone-green') : ''}
            ${hol ? App.chip(`Holiday${hol.description ? ': ' + App.esc(hol.description) : ''}`, 'tone-red') : closed ? '<span class="muted small">Weekend</span>' : ''}
            ${exp ? App.chip(`NIFTY ${exp.monthly ? 'monthly' : 'weekly'} expiry`, 'tone-amber') : ''}</div></div>
        <div class="algo-chips">${algos.map((a) => `<span class="algo-chip" style="--c:${App.colorFor(a.algo_id)}" title="${App.esc(a.name)}">
          ${App.esc(a.name.replace(/^NIFTY\s+/, ''))}${a.role === 'entry' ? ' · entry' : a.role === 'exit' ? ' · exit' : ''}</span>`).join('')}</div>
      </div>`);
    }
    const upcoming = st.data.expiries.slice(0, 8).map((x) => `${x.date.slice(8, 10)}/${x.date.slice(5, 7)}${x.monthly ? ' (M)' : ''}`).join(' · ');
    body.innerHTML = `<div class="chart-foot" style="margin:-6px 0 12px">Expiries from Kite's instrument list${upcoming ? ': ' + upcoming : ' (none stored yet)'} ·
      holidays from <code>config/holidays/</code> · algo days from each config, before their entry checks.</div>
      <div class="section-card cal-list">${out.join('')}</div>`;
  }

  App.page('calendar', { title: 'Market calendar', render, destroy() {} });
})();
