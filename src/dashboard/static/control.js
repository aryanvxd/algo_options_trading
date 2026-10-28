/* Run control: Kite login state and a Start / Stop button per strategy.
   Start opens `algo run --algo <id>` in its own window (it keeps running if the dashboard is
   closed); Stop sends KILL, the same as `algo kill`: square off, then stop.
   App.control.mount(el, {algoId}) renders it into `el` and keeps it fresh; it returns a stop function. */

(() => {
  const POLL_MS = 5000;

  function strategyName(id) {
    return (App.meta?.strategies || []).find((a) => a.algo_id === id)?.name || id;
  }

  function kiteChip(k) {
    if (k.logged_in) {
      const until = k.valid_until ? `${k.valid_until.slice(11, 16)} ${k.valid_until.slice(8, 10)}/${k.valid_until.slice(5, 7)}` : '';
      return `<span class="chip tone-green" title="Kite session valid until ${until}"><span class="chip-dot"></span>Kite logged in</span>`;
    }
    return `<span class="chip tone-amber"><span class="chip-dot"></span>Kite: not logged in</span>
      <button class="btn-secondary btn-sm" data-login><i class="fa-solid fa-right-to-bracket"></i>Log in</button>`;
  }

  function algoRow(id, a, compact) {
    const name = App.esc(compact ? 'Run' : strategyName(id).replace(/^NIFTY\s+/, ''));
    let state;
    if (a.running) state = `<span class="chip tone-green live" title="pid ${a.pid}"><span class="chip-dot"></span>Running since ${App.timeOf(a.started_at, false)}</span>`;
    else if (a.today_run) state = `<span class="chip tone-slate"><span class="chip-dot"></span>${App.esc(a.today_run.status.toLowerCase())} · run #${a.today_run.run_id}</span>`;
    else state = '<span class="chip tone-slate"><span class="chip-dot"></span>Not started today</span>';
    const btn = a.running
      ? `<button class="btn-danger btn-sm" data-stop="${App.esc(id)}" title="Square off and stop"><i class="fa-solid fa-stop"></i>Stop</button>`
      : `<button class="btn-primary btn-sm" data-start="${App.esc(id)}" ${a.can_start ? '' : 'disabled'}
           title="${App.esc(a.can_start ? (a.resume ? 'Resume today\'s run' : 'Start today\'s run') : a.start_blocked)}">
           <i class="fa-solid fa-play"></i>${a.resume ? 'Resume' : 'Start'}</button>`;
    const why = !a.running && !a.can_start && a.start_blocked ? `<span class="muted small">${App.esc(a.start_blocked)}</span>` : '';
    return `<div class="control-row">${compact ? '' : `<span class="control-name">${name}</span>`}${state}${btn}${why}</div>`;
  }

  async function act(el, opts, fn) {
    try { await fn(); }
    catch (err) {
      const msg = String(err.message).replace(/^\d+ /, '');
      let detail = msg;
      try { detail = JSON.parse(msg).detail || msg; } catch { /* plain text */ }
      App.toast(detail, 'error');
    }
    refresh(el, opts);
  }

  function bind(el, opts, data) {
    el.querySelector('[data-login]')?.addEventListener('click', () => act(el, opts, async () => {
      await App.api('kite/login', {}, { method: 'POST' });
      App.toast('Kite login opened in a new window: sign in in your browser');
    }));
    el.querySelectorAll('[data-start]').forEach((b) => b.addEventListener('click', () => {
      const id = b.dataset.start;
      const a = data.algos[id];
      const late = a.entry_closed ? `\n\nNote: entries close at ${a.entry_deadline}. It's past that, so it will not take a new trade today.` : '';
      if (!confirm(`${a.resume ? 'Resume' : 'Start'} ${strategyName(id)} (paper)?\n\nIt opens in its own window and keeps running if you close the dashboard.${late}`)) return;
      act(el, opts, async () => {
        await App.api(`strategies/${id}/start`, {}, { method: 'POST' });
        App.toast(`${strategyName(id)} starting in a new window`);
      });
    }));
    el.querySelectorAll('[data-stop]').forEach((b) => b.addEventListener('click', () => {
      const id = b.dataset.stop;
      if (!confirm(`Stop ${strategyName(id)}?\n\nAny open position is squared off first, then the run ends for today.`)) return;
      act(el, opts, async () => {
        await App.api(`strategies/${id}/stop`, {}, { method: 'POST' });
        App.toast(`Stop sent to ${strategyName(id)}: squaring off`);
      });
    }));
  }

  async function refresh(el, opts) {
    if (!el.isConnected) return;
    let data;
    try { data = await App.api('control'); }
    catch { el.innerHTML = '<span class="muted small">Run control unavailable</span>'; return; }
    const ids = opts.algoId ? [opts.algoId] : Object.keys(data.algos);
    el.innerHTML = `<div class="control-bar${opts.algoId ? ' compact' : ''}">
      <div class="control-kite">${kiteChip(data.kite)}</div>
      ${ids.filter((id) => data.algos[id]).map((id) => algoRow(id, data.algos[id], !!opts.algoId)).join('')}</div>`;
    bind(el, opts, data);
  }

  function mount(el, opts = {}) {
    refresh(el, opts);
    const t = setInterval(() => refresh(el, opts), POLL_MS);
    return () => clearInterval(t);
  }

  App.control = { mount };
})();
