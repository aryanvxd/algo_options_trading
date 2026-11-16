/* Swing › Watchlists: #/swing/watch/<id>. Five lists: "Algo picks" is replaced by every weekend scan
   with its top picks and their plans (read-only here); the other four are yours to rename and fill.
   Prices refresh from Kite every 30 seconds while the page is open and you are logged in (the last
   close otherwise). Also here, for every Swing page: the sidebar's list of watchlists, the
   "add to watchlist" button and menu, and the stock page's Fundamentals card. */

(() => {
  const REFRESH_MS = 30000;
  const POS_CLASS = { 'near support': 'sw-sup', 'near resistance': 'sw-res', 'in a zone': 'sw-flip' };
  let lists = [];
  let timer = null;
  let token = 0;
  let menu = null;

  const esc = (v) => App.esc(v);
  // "409 {"detail": "..."}" -> the server's own message.
  const why = (err) => {
    const m = /\{[\s\S]*\}$/.exec(err.message || '');
    try {
      const d = JSON.parse(m[0]).detail;
      return Array.isArray(d) ? d.map((x) => x.msg).join('; ') : d || err.message;
    } catch { return err.message; }
  };
  const range = (z) => (z ? `${App.num(z[0])}–${App.num(z[1])}` : '—');
  const atr = (v) => (v === null || v === undefined ? '—' : v.toFixed(1));
  const change = (v) => (v === null || v === undefined ? '—' : `<span class="${App.tone(v)}">${App.pct(v, 1)}</span>`);

  async function loadNav() {
    try { lists = (await App.api('swing/watchlists')).lists; }
    catch { lists = []; }
    App.setWatchlists(lists);
    return lists;
  }

  // ── "add to watchlist": a small button anywhere, one shared menu ──
  function addButton(symbol, big = false) {
    return `<button class="swg-add${big ? ' swg-add-big' : ''}" data-watch-add="${esc(symbol)}" title="Add ${esc(symbol)} to a watchlist" aria-label="Add ${esc(symbol)} to a watchlist"><i class="fa-regular fa-star"></i></button>`;
  }

  function closeMenu() { menu?.remove(); menu = null; }

  async function openMenu(btn) {
    closeMenu();
    const symbol = btn.dataset.watchAdd;
    const own = (lists.length ? lists : await loadNav()).filter((w) => w.kind === 'manual');
    menu = document.createElement('div');
    menu.className = 'swg-menu';
    menu.innerHTML = `<div class="swg-menu-head">Add ${esc(symbol)} to</div>${own.map((w) =>
      `<button data-list="${w.id}"><i class="fa-solid fa-star"></i>${esc(w.name)}<span class="muted">${w.count}</span></button>`).join('')
      || '<div class="muted swg-pad">No watchlists</div>'}`;
    document.body.appendChild(menu);
    const r = btn.getBoundingClientRect();
    menu.style.top = `${Math.min(window.innerHeight - menu.offsetHeight - 8, r.bottom + 4)}px`;
    menu.style.left = `${Math.min(window.innerWidth - menu.offsetWidth - 8, r.left)}px`;
    menu.querySelectorAll('button[data-list]').forEach((b) => b.addEventListener('click', async () => {
      closeMenu();
      try {
        const out = await App.api(`swing/watchlists/${b.dataset.list}/items`, {}, { method: 'POST', body: { symbol } });
        const name = own.find((w) => String(w.id) === b.dataset.list)?.name;
        App.toast(out.added ? `${symbol} added to ${name}` : `${symbol} is already in ${name}`);
        loadNav();
      } catch (err) { App.toast(why(err), 'error'); }
    }));
  }

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-watch-add]');
    if (btn) { e.preventDefault(); e.stopPropagation(); openMenu(btn); return; }
    if (menu && !e.target.closest('.swg-menu')) closeMenu();
  }, true);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeMenu(); });
  window.addEventListener('hashchange', closeMenu);

  // ── the stock page's Fundamentals card ──
  function fundamentalsCard(values) {
    const keys = Object.keys(values || {}).sort();
    return `<div class="section-card"><div class="card-head"><div><div class="section-title">Fundamentals</div>
      <div class="section-subtitle">From your uploaded data (Swing › Fundamentals data)</div></div></div>
      ${keys.length ? `<div class="swg-funds">${keys.map((k) => `<div class="swg-fund"><span class="stat-label">${esc(k.replace(/_/g, ' '))}</span>
        <span class="swg-fund-v">${App.num(values[k], 2)}</span></div>`).join('')}</div>`
        : '<p class="muted swg-pad">No numbers for this stock yet. <a href="#/swing/fundamentals">Upload a CSV</a>.</p>'}</div>`;
  }

  // ── the watchlist page ──
  function stop() { if (timer) { clearInterval(timer); timer = null; } }

  function table(w) {
    const auto = w.kind === 'auto';
    if (!w.rows.length) {
      return auto ? '<p class="muted swg-pad">Empty until the next weekend scan. <a href="#/swing/run">Weekend run</a></p>'
        : '<p class="muted swg-pad">No stocks yet. Add one above, or with the <i class="fa-regular fa-star"></i> button on Sectors, Picks or a stock page.</p>';
    }
    return `<div class="table-wrap"><table class="data-table clickable"><thead><tr>
      <th>Stock</th><th class="sw-left">Industry</th><th>Price</th><th>Since added</th><th class="sw-left">Position</th>
      <th>Support</th><th>ATR away</th><th>Resistance</th><th>ATR away</th>
      ${auto ? '<th>Entry</th><th>To entry</th><th>Stop</th><th>Target</th>' : ''}<th class="sw-left">Note</th><th></th></tr></thead>
      <tbody>${w.rows.map((r) => `<tr data-symbol="${esc(r.symbol)}">
        <td><a href="#/swing/${encodeURIComponent(r.symbol)}">${esc(r.symbol)}</a></td>
        <td class="sw-left muted">${esc(r.industry) || '—'}</td><td>${App.num(r.price)}</td><td>${change(r.since_pct)}</td>
        <td class="sw-left"><span class="${POS_CLASS[r.position] || 'muted'}">${esc(r.position)}</span></td>
        <td>${range(r.support)}</td><td>${atr(r.support_atr)}</td><td>${range(r.resistance)}</td><td>${atr(r.resistance_atr)}</td>
        ${auto ? `<td>${App.num(r.entry)}</td><td>${change(r.to_entry_pct)}</td><td class="sw-res">${App.num(r.stop)}</td><td class="sw-sup">${App.num(r.target)}</td>` : ''}
        <td class="sw-left muted swg-note">${esc(r.note || '')}</td>
        <td>${auto ? addButton(r.symbol) : `<button class="btn-icon-box swg-remove" data-remove="${esc(r.symbol)}" title="Remove ${esc(r.symbol)}" aria-label="Remove ${esc(r.symbol)}"><i class="fa-solid fa-xmark"></i></button>`}</td>
      </tr>`).join('')}</tbody></table></div>`;
  }

  async function render(el, args) {
    stop();
    const id = Number(args[0]);
    const my = ++token;
    el.innerHTML = '<div class="section-card"><div class="skeleton" style="height:200px"></div></div>';
    if (!id) {
      const ls = await loadNav();
      if (ls.length) { location.replace(`#/swing/watch/${ls[0].id}`); return; }
    }
    let w;
    try { w = await App.api(`swing/watchlists/${id}`); }
    catch (err) {
      if (my !== token) return;
      el.innerHTML = App.emptyState('fa-star', 'Watchlist not available', esc(why(err)));
      return;
    }
    if (my !== token) return;
    const auto = w.kind === 'auto';
    el.innerHTML = `
      <nav class="sw-crumbs" aria-label="Breadcrumb"><a href="#/swing">Swing</a><i class="fa-solid fa-chevron-right"></i><span>Watchlists</span>
        <i class="fa-solid fa-chevron-right"></i><span aria-current="page">${esc(w.name)}</span></nav>
      <div class="page-header">
        <div><div class="page-title"><i class="fa-solid ${auto ? 'fa-robot' : 'fa-star'}"></i><span id="swg-wl-name">${esc(w.name)}</span>
          ${auto ? '' : '<button class="btn-icon-box" id="swg-rename" title="Rename" aria-label="Rename"><i class="fa-solid fa-pen"></i></button>'}</div>
          <div class="page-subtitle">${auto ? 'Filled by each weekend scan with its top picks and their plans. The Picks journal keeps the earlier ones'
            : 'Your list: stocks you are watching, placed against their zones'}</div></div>
        <div class="header-meta" id="swg-wl-meta"></div>
      </div>
      <p class="sw-note"><i class="fa-solid fa-circle-info"></i>${esc(w.note)}</p>
      ${auto ? '' : `<div class="section-card"><form class="top-bar" id="swg-add-form">
        <div class="top-bar-group"><input class="input-field" id="swg-add-symbol" placeholder="Symbol, e.g. POWERGRID" aria-label="Symbol" autocomplete="off" />
          <input class="input-field swg-note-in" id="swg-add-note" placeholder="Note (optional)" aria-label="Note" maxlength="500" />
          <button class="btn-primary" type="submit"><i class="fa-solid fa-plus"></i>Add</button></div></form></div>`}
      <div class="section-card" id="swg-wl-body"></div>`;
    const paint = (data) => {
      el.querySelector('#swg-wl-meta').innerHTML = data.live ? `<span class="sw-sup">Kite last price</span> at ${App.timeOf(data.as_of)}`
        : 'Last close · log in to Kite for live prices';
      const body = el.querySelector('#swg-wl-body');
      body.innerHTML = table(data);
      body.querySelectorAll('tbody tr').forEach((tr) => tr.addEventListener('click', (e) => {
        if (!e.target.closest('a, button')) location.hash = `#/swing/${encodeURIComponent(tr.dataset.symbol)}`;
      }));
      body.querySelectorAll('[data-remove]').forEach((b) => b.addEventListener('click', async () => {
        try {
          await App.api(`swing/watchlists/${id}/items/${encodeURIComponent(b.dataset.remove)}`, {}, { method: 'DELETE' });
          App.toast(`${b.dataset.remove} removed`);
          loadNav();
          reload();
        } catch (err) { App.toast(why(err), 'error'); }
      }));
    };
    const reload = async () => {
      try { const d = await App.api(`swing/watchlists/${id}`); if (my === token) { w = d; paint(d); } } catch { /* keep the last */ }
    };
    paint(w);
    if (w.live) timer = setInterval(reload, REFRESH_MS);
    el.querySelector('#swg-add-form')?.addEventListener('submit', async (e) => {
      e.preventDefault();
      const sym = el.querySelector('#swg-add-symbol').value.trim().toUpperCase();
      const note = el.querySelector('#swg-add-note').value.trim() || null;
      if (!sym) return;
      try {
        const out = await App.api(`swing/watchlists/${id}/items`, {}, { method: 'POST', body: { symbol: sym, note } });
        App.toast(out.added ? `${sym} added` : `${sym} is already here`);
        el.querySelector('#swg-add-symbol').value = '';
        el.querySelector('#swg-add-note').value = '';
        loadNav();
        reload();
      } catch (err) { App.toast(why(err), 'error'); }
    });
    // Rename in place: Enter or leaving the box saves, Escape cancels.
    el.querySelector('#swg-rename')?.addEventListener('click', () => {
      const span = el.querySelector('#swg-wl-name');
      if (span.querySelector('input')) return;
      span.innerHTML = `<input class="input-field swg-rename-in" maxlength="60" aria-label="Watchlist name" value="${esc(w.name)}" />`;
      const inp = span.querySelector('input');
      inp.focus();
      inp.select();
      let done = false;
      const finish = async (save) => {
        if (done) return;
        done = true;
        const name = inp.value.trim();
        if (save && name && name !== w.name) {
          try {
            const out = await App.api(`swing/watchlists/${id}`, {}, { method: 'PUT', body: { name } });
            w.name = out.name;
            loadNav();
          } catch (err) { App.toast(why(err), 'error'); }
        }
        span.textContent = w.name;
      };
      inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') finish(true); if (e.key === 'Escape') finish(false); });
      inp.addEventListener('blur', () => finish(true));
    });
  }

  App.swingWatch = { render, destroy: () => { stop(); token += 1; closeMenu(); }, loadNav, addButton, fundamentalsCard, why };
})();
