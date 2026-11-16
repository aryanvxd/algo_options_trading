/* Swing › Weekend run: #/swing/run. Starts the weekend scan (`algo swing scan`) in the background
   and shows its stages as they finish: refresh candles from Kite (optional), sectors, stocks,
   picks, then the Algo picks watchlist and the journal. Any day, not only at the weekend; during
   market hours it asks first, because the scan shares the Kite API key with the algos. */

(() => {
  const POLL_MS = 2000;
  const STALE_DAYS = 7;
  let R = null;
  const esc = (v) => App.esc(v);
  const ICON = { waiting: 'fa-regular fa-circle', running: 'fa-solid fa-spinner fa-spin', done: 'fa-solid fa-circle-check',
    skipped: 'fa-solid fa-circle-minus', failed: 'fa-solid fa-circle-xmark' };

  function stop() {
    if (!R) return;
    if (R.timer) clearInterval(R.timer);
    R.stopControl?.();
    R = null;
  }

  const marketHours = (d = new Date()) => {
    // IST, whatever the browser's zone
    const ist = new Date(d.getTime() + (d.getTimezoneOffset() + 330) * 60000);
    const mins = ist.getHours() * 60 + ist.getMinutes();
    return ist.getDay() >= 1 && ist.getDay() <= 5 && mins >= 9 * 60 && mins <= 15 * 60 + 30;
  };

  async function render(el) {
    stop();
    R = { el, kite: null, state: null, timer: null };
    const refresh = App.prefs.get('swing.run.refresh', true);
    el.innerHTML = `
      <nav class="sw-crumbs" aria-label="Breadcrumb"><a href="#/swing">Swing</a><i class="fa-solid fa-chevron-right"></i><span aria-current="page">Weekend run</span></nav>
      <div class="page-header">
        <div><div class="page-title"><i class="fa-solid fa-play"></i>Weekend run</div>
          <div class="page-subtitle">Sectors at support, then their stocks at support, then a plan for each: entry, stop and target</div></div>
        <div class="header-meta"><span id="swg-kite" class="control-kite"></span></div>
      </div>
      <div class="hidden" id="swg-control" aria-hidden="true"></div>
      <div id="swg-stale"></div>
      <div class="row row-2-1">
        <div class="section-card">
          <div class="card-head"><div><div class="section-title">Run the scan</div>
            <div class="section-subtitle">Run it any day, outside market hours. It only reads from Kite: candles, holdings and your GTTs and alerts.</div></div></div>
          <div class="swg-run-opts">
            <label class="sw-check"><input type="checkbox" id="swg-refresh"${refresh ? ' checked' : ''} />Refresh candles and holdings from Kite first (needs today's login)</label>
            <button class="btn-primary" id="swg-start"><i class="fa-solid fa-play"></i>Run scan</button>
            <span class="muted swg-small" id="swg-why"></span>
          </div>
          <ol class="swg-stages" id="swg-stages"></ol>
        </div>
        <div class="section-card">
          <div class="section-title sw-gap">After the scan</div>
          <ul class="swg-links">
            <li><a href="#/swing"><i class="fa-solid fa-industry"></i>Sectors: where each index and stock sits</a></li>
            <li><a href="#/swing/picks"><i class="fa-solid fa-crosshairs"></i>Picks: the funnel and the ranked plans</a></li>
            <li><a href="#/swing/watch"><i class="fa-solid fa-robot"></i>Algo picks: the top plans as a watchlist</a></li>
            <li><a href="#/swing/journal"><i class="fa-solid fa-book"></i>Picks journal: how earlier picks did, against random twins</a></li>
            <li><a href="#/swing/settings"><i class="fa-solid fa-sliders"></i>Settings: the rules of each stage</a></li>
          </ul>
        </div>
      </div>
      <div id="swg-log"></div>`;
    el.querySelector('#swg-refresh').addEventListener('change', (e) => { App.prefs.set('swing.run.refresh', e.target.checked); paintButton(); });
    el.querySelector('#swg-start').addEventListener('click', start);
    R.stopControl = App.control.mount(el.querySelector('#swg-control'), { kiteEl: el.querySelector('#swg-kite'),
      onStatus: (s) => { if (!R) return; R.kite = s ? !!s.kite?.logged_in : null; paintButton(); } });
    await poll();
  }

  function paintButton() {
    if (!R) return;
    const btn = R.el.querySelector('#swg-start');
    const why = R.el.querySelector('#swg-why');
    const running = !!R.state?.job?.running;
    const refresh = R.el.querySelector('#swg-refresh').checked;
    let reason = '';
    if (running) reason = 'A scan is running.';
    else if (refresh && R.kite === false) reason = 'Log in to Kite, or untick Refresh to use the stored candles.';
    btn.disabled = !!reason;
    why.textContent = reason || (refresh && marketHours() ? 'Market is open: you will be asked to confirm.' : '');
  }

  async function poll() {
    if (!R) return;
    try { R.state = await App.api('swing/scan'); } catch { return; }
    if (!R) return;
    paint();
    const running = !!R.state.job?.running;
    if (running && !R.timer) R.timer = setInterval(poll, POLL_MS);
    if (!running && R.timer) { clearInterval(R.timer); R.timer = null; App.swingWatch.loadNav(); }
  }

  function paint() {
    const { job, scan } = R.state;
    const stale = R.el.querySelector('#swg-stale');
    const last = scan?.finished_at ? new Date(scan.finished_at) : null;
    const days = last ? (Date.now() - last.getTime()) / 86400000 : null;
    stale.innerHTML = !scan ? '<p class="banner banner-warn"><i class="fa-solid fa-triangle-exclamation"></i>No weekend scan from the dashboard yet.</p>'
      : days !== null && days > STALE_DAYS ? `<p class="banner banner-warn"><i class="fa-solid fa-triangle-exclamation"></i>The last scan finished ${Math.floor(days)} days ago: the picks are stale.</p>` : '';
    const stages = scan?.stages || [];
    R.el.querySelector('#swg-stages').innerHTML = stages.length ? stages.map((s, i) => `
      <li class="swg-stage swg-${s.state}"><i class="${ICON[s.state] || ICON.waiting}"></i>
        <div><div class="swg-stage-name">${i + 1}. ${esc(s.label)}</div>
        <div class="muted swg-small">${esc(s.detail || (s.state === 'waiting' ? 'waiting' : s.state))}</div></div></li>`).join('')
      + `<li class="swg-stage-foot muted swg-small">${scan.finished_at ? `${scan.ok ? 'Finished' : 'Stopped'} ${App.prettyDate(scan.finished_at.slice(0, 10))} ${scan.finished_at.slice(11, 16)}`
        : `Started ${scan.started_at.slice(11, 16)}`}${scan.error ? ` · ${esc(scan.error)}` : ''}</li>`
      : '<li class="muted swg-small">No scan yet.</li>';
    const box = R.el.querySelector('#swg-log');
    if (!job) { box.innerHTML = ''; paintButton(); return; }
    const pre = box.querySelector('.bt-log');
    const atEnd = !pre || pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 8;
    const chip = job.running ? App.chip('Running', 'tone-blue', true) : job.exit_code === 0 ? App.chip('Done', 'tone-green') : App.chip(`Failed (exit ${job.exit_code})`, 'tone-red');
    box.innerHTML = `<div class="section-card"><div class="card-head"><div><div class="section-title">Log ${chip}</div>
      <div class="section-subtitle">Started ${esc((job.started_at || '').slice(11, 16))} · <code>algo ${esc(job.args.join(' '))}</code></div></div></div>
      <pre class="bt-log">${esc((job.lines || []).join('\n'))}</pre></div>`;
    const p = box.querySelector('.bt-log');
    if (atEnd) p.scrollTop = p.scrollHeight;
    paintButton();
  }

  async function start() {
    const refresh = R.el.querySelector('#swg-refresh').checked;
    let confirm = false;
    if (refresh && marketHours()) {
      if (!window.confirm('The market is open. The scan shares the Kite API key with the algos and makes several hundred candle requests. Run it anyway?')) return;
      confirm = true;
    }
    try {
      await App.api('swing/scan', {}, { method: 'POST', body: { refresh, confirm_market_hours: confirm } });
      App.toast('Scan started');
      await poll();
    } catch (err) { App.toast(App.swingWatch.why(err), 'error'); }
  }

  App.swingRun = { render, destroy: stop };
})();
