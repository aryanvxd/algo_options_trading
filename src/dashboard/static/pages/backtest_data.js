/* Market data coverage: #/backtests/data (docs/design/backtest.md, section 6.7).
   What is stored per dataset and its gaps (GET /api/data/coverage), the days real-price backtests
   can and can't trade (GET /api/data/days), and Download buttons that run `algo data ...` in the
   background, one at a time (POST /api/data/<dataset>). The Kite downloads need the day's login: the
   page shows the login state and the existing Log in button (App.control); you sign in yourself. */

(() => {
  const STRIP_DAYS = 80;
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const short = (iso) => { if (!iso) return '—'; const [y, m, d] = iso.split('-').map(Number); return `${d} ${MONTHS[m - 1]} ${String(y).slice(2)}`; };
  const STATE = { collecting: ['Collecting', 'tone-blue'], complete: ['Complete', 'tone-green'], incomplete: ['Expiry day lost', 'tone-red'] };
  let D = null;

  function stop() {
    if (!D) return;
    if (D.jobTimer) clearInterval(D.jobTimer);
    D.stopControl?.();
    D = null;
  }

  async function render(el) {
    stop();
    D = { el, kite: null };
    el.innerHTML = `
      <div class="page-header"><div><div class="page-title"><i class="fa-solid fa-database"></i>Market data coverage</div>
        <div class="page-subtitle">What is stored in data/market for backtests, the gaps, and the days real-price backtests skip</div></div>
        <div class="header-meta"><span id="dc-kite" class="control-kite"></span>
          <button class="btn-icon-box" id="dc-refresh" title="Refresh" aria-label="Refresh"><i class="fa-solid fa-rotate"></i></button></div></div>
      <div class="hidden" id="dc-control" aria-hidden="true"></div>
      <div id="dc-body"><div class="section-card"><div class="skeleton" style="height:200px"></div></div></div>`;
    el.querySelector('#dc-refresh').addEventListener('click', () => load());
    // The login chip and button, from the run-control widget (it polls /api/control every 5 s).
    D.stopControl = App.control.mount(el.querySelector('#dc-control'), { kiteEl: el.querySelector('#dc-kite'),
      onStatus: (s) => { if (!D) return; D.kite = s ? !!s.kite?.logged_in : null; paintButtons(); } });
    await load();
  }

  async function load() {
    if (!D) return;
    const body = D.el.querySelector('#dc-body');
    try {
      const [cov, days, jobs] = await Promise.all([App.api('data/coverage'), App.api('data/days'), App.api('data/jobs')]);
      if (!D) return;
      Object.assign(D, { cov, days, job: jobs.job });
      body.innerHTML = page();
      bind();
      paintJob();
      paintButtons();
    } catch (err) {
      body.innerHTML = App.emptyState('fa-plug-circle-xmark', 'Could not read the market data', App.esc(err.message));
    }
  }

  function page() {
    const { cov, days } = D;
    const real = new Set(days.real_option_days);
    const firstOpt = cov.expiries.map((e) => e.first).filter(Boolean).sort()[0];
    const T = days.trading_days;
    const strip = T.slice(-STRIP_DAYS);
    const since = firstOpt ? T.filter((d) => d >= firstOpt) : [];
    const realSince = since.filter((d) => real.has(d)).length;
    const hhmm = (cov.now || '').slice(11, 16);
    const dow = new Date((cov.now || '').slice(0, 10) + 'T12:00:00').getDay();
    const session = dow >= 1 && dow <= 5 && hhmm >= '09:15' && hhmm < '15:35';
    const ds = cov.downloads;
    const row = (info, key, extra = '') => `<tr>
        <td><b>${App.esc(info.label)}</b>${extra}</td><td>${short(info.first)}</td><td>${short(info.last)}</td><td>${info.days ?? '—'}</td>
        <td>${info.gap_count ? `<span class="neg" title="${App.esc(info.gaps.join(', '))}${info.gap_count > info.gaps.length ? ', …' : ''}">${info.gap_count} missing</span>` : '<span class="pos">none</span>'}</td>
        <td>${info.problems?.length ? `<span class="neg" title="${App.esc(info.problems.join(', '))}">${info.problems.length}</span>` : '—'}</td>
        <td>${key ? dlControls(key, ds[key], info.last) : ''}</td></tr>`;
    return `
      ${cov.reminder ? `<div class="banner banner-warn"><i class="fa-solid fa-bell"></i>${App.esc(cov.reminder)}</div>` : ''}
      ${session ? `<div class="banner"><i class="fa-solid fa-clock"></i>Market is open: a download made now stops at the last finished session, and it shares the Kite API with the live algos. Evening is the usual time.</div>` : ''}
      <div id="dc-job"></div>

      <div class="row"><div class="section-card">
        <div class="card-head"><div><div class="section-title">Real option candles</div>
          <div class="section-subtitle">${firstOpt ? `${realSince} of ${since.length} trading days since ${short(firstOpt)} can run on real prices; real-price backtests skip the red ones (missing_options)` : 'None saved yet'}
            ${cov.next_expiry ? ` · next expiry ${short(cov.next_expiry)}: save it by that evening` : ''}</div></div>
          ${dlControls('options', ds.options)}</div>
        <div class="dc-strip-head muted small"><span>${short(strip[0])}</span><span>last ${strip.length} trading days</span><span>${short(strip[strip.length - 1])}</span></div>
        <div class="bt-strip dc-strip">${strip.map((d) => `<span class="${real.has(d) ? 'ok' : 'miss'}"
          title="${d}: ${real.has(d) ? `real candles (expiry ${days.real_expiry[d]})` : 'no saved candles for the nearest expiry'}"></span>`).join('')}</div>
        <div class="table-wrap" style="margin-top:12px"><table class="data-table">
          <thead><tr><th>Expiry</th><th>State</th><th>Days stored</th><th>First</th><th>Last</th><th>Problems</th></tr></thead>
          <tbody>${cov.expiries.length ? cov.expiries.map((e) => { const s = STATE[e.state] || [e.state, 'tone-slate']; return `<tr>
            <td><b>${short(e.expiry)}</b> <span class="muted small">${new Date(e.expiry + 'T12:00:00').toLocaleDateString('en-IN', { weekday: 'short' })}</span></td>
            <td>${App.chip(s[0], s[1])}</td><td>${e.days}</td><td>${short(e.first)}</td><td>${short(e.last)}</td>
            <td>${e.problems.length ? `<span class="neg" title="${App.esc(e.problems.join(', '))}">${e.problems.length} (re-fetched on the next run)</span>` : '—'}</td></tr>`; }).join('')
            : '<tr><td colspan="6" class="muted">No option candles saved yet: run Download.</td></tr>'}</tbody></table></div>
        <div class="chart-foot">A contract can be downloaded only until its expiry evening; "Expiry day lost" means its last day was never saved (see section 5a).</div>
      </div></div>

      <div class="row"><div class="section-card"><div class="card-head"><div><div class="section-title">Minute history and chains</div>
        <div class="section-subtitle">A gap is a weekday with no file that isn't a holiday. Downloads skip days already stored and start at the last one by default</div></div></div>
        <div class="table-wrap"><table class="data-table dc-table">
          <thead><tr><th>Data</th><th>First</th><th>Last</th><th>Days</th><th>Gaps</th><th>Problems</th><th>Download</th></tr></thead>
          <tbody>
            ${row(cov.index[0], 'index', '<div class="muted small">downloads with India VIX</div>')}
            ${row(cov.index[1], null)}
            ${row(cov.futures, 'futures', '<div class="muted small">Kite serves minute data only for the live contract</div>')}
            <tr><td><b>${App.esc(cov.stocks.label)}</b><div class="muted small">${cov.stocks.count} stocks · ${cov.stocks.days_min === cov.stocks.days_max ? `${cov.stocks.days_max} days each` : `${cov.stocks.days_min}–${cov.stocks.days_max} days`}</div></td>
              <td>${short(cov.stocks.first)}</td><td>${short(cov.stocks.last)}</td><td>${cov.stocks.days_max}</td>
              <td>${cov.stocks.short.length ? `<span class="neg" title="${App.esc(cov.stocks.short.map((x) => `${x.symbol}: ${x.days}`).join(', '))}">${cov.stocks.short.length} stock(s) short</span>` : '<span class="pos">none</span>'}</td>
              <td>—</td><td>${dlControls('constituents-1m', ds['constituents-1m'], cov.stocks.last)}</td></tr>
            ${row(cov.bhavcopy, 'bhavcopy', '<div class="muted small">from NSE: no Kite login needed</div>')}
          </tbody></table></div></div></div>

      <div class="row"><div class="section-card"><div class="card-head"><div><div class="section-title">Recorded by live runs</div>
        <div class="section-subtitle">Ticks the live algos saved; used for the spread model and replays, not by backtests</div></div></div>
        <div class="table-wrap"><table class="data-table"><thead><tr><th>Data</th><th>First</th><th>Last</th><th>Days</th></tr></thead>
          <tbody>${cov.recorded.map((r) => `<tr><td>${App.esc(r.label)}</td><td>${short(r.first)}</td><td>${short(r.last)}</td><td>${r.days}</td></tr>`).join('')}</tbody></table></div></div></div>`;
  }

  function dlControls(key, spec, last) {
    return `<div class="dc-dl" data-dl="${key}" title="${App.esc(spec.help)}">
      ${spec.needs_from ? `<input type="date" class="input-field" data-from aria-label="${App.esc(spec.label)}: download from" value="${last || ''}" />` : ''}
      <button class="${key === 'options' ? 'btn-primary' : 'btn-secondary btn-sm'}" data-download="${key}" data-kite="${spec.kite ? 1 : 0}">
        <i class="fa-solid fa-download"></i>${key === 'options' ? 'Download option candles' : 'Download'}</button></div>`;
  }

  function paintButtons() {
    if (!D?.el.isConnected) return;
    const busy = !!D.job?.running;
    D.el.querySelectorAll("#dc-body [data-download]").forEach((b) => {
      const needsKite = b.dataset.kite === '1';
      const blocked = busy ? 'a download is running' : needsKite && D.kite === false ? 'log in to Kite first' : '';
      b.disabled = !!blocked;
      b.title = blocked ? `Not now: ${blocked}` : '';
    });
  }

  function bind() {
    D.el.querySelectorAll("#dc-body [data-download]").forEach((b) => b.addEventListener('click', async () => {
      const key = b.dataset.download;
      const spec = D.cov.downloads[key];
      const from = b.closest('[data-dl]').querySelector('[data-from]')?.value || null;
      if (!confirm(`Download ${spec.label}?\n\n${spec.help}\n\nIt runs in the background (\`algo data ${key}${from ? ` --from ${from}` : ''}\`); its log shows here.`)) return;
      try {
        D.job = await App.api(`data/${key}`, {}, { method: 'POST', body: from ? { from } : {} });
        App.toast(`${spec.label}: download started`);
        paintJob();
        paintButtons();
      } catch (err) {
        let msg = String(err.message).replace(/^\d+\s*/, '');
        try { msg = JSON.parse(msg).detail; } catch { /* plain */ }
        App.toast('Not started: ' + msg, 'error');
      }
    }));
  }

  async function paintJob() {
    if (!D) return;
    const box = D.el.querySelector('#dc-job');
    if (!box) return;
    if (D.job?.running && !D.jobTimer) {
      D.jobTimer = setInterval(async () => {
        try { D.job = (await App.api('data/jobs')).job; } catch { return; }
        if (!D) return;
        if (!D.job?.running) { clearInterval(D.jobTimer); D.jobTimer = null; load(); return; }
        paintJob();
      }, 2000);
    }
    if (!D.job) { box.innerHTML = ''; return; }
    if (!D.job.lines) {                        // just started: fetch the status with its first log lines
      try { D.job = (await App.api('data/jobs')).job || D.job; } catch { /* keep what we have */ }
    }
    const job = D.job;
    const state = job.running ? App.chip('Downloading', 'tone-blue', true) : job.exit_code === 0 ? App.chip('Done', 'tone-green') : App.chip(`Failed (exit ${job.exit_code})`, 'tone-red');
    const pre = box.querySelector('.bt-log');
    const atEnd = !pre || pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 8;
    box.innerHTML = `<div class="row"><div class="section-card"><div class="card-head"><div><div class="section-title">${App.esc(job.label)} ${state}</div>
      <div class="section-subtitle">Started ${App.esc((job.started_at || '').slice(11, 16))} · <code>algo ${App.esc((job.args || []).join(' '))}</code> · log in <code>${App.esc((job.log || '').split(/[\\/]/).slice(-2).join('/'))}</code></div></div></div>
      <pre class="bt-log dc-log">${App.esc((job.lines || []).join('\n'))}</pre></div></div>`;
    const p = box.querySelector('.bt-log');
    if (atEnd) p.scrollTop = p.scrollHeight;
  }

  App.backtestData = { render, destroy: stop };
})();
