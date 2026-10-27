/* Long strangle scalp: live signal panel on Today, break-even line on Performance.
   Reads the algo's live levels (live_state.levels): pair, signal progress, trades used, day limits. */

(() => {
  // Net win ≈ ₹374 and net loss ≈ ₹426 at 1 lot (₹500 / ₹300 gross, ~₹100 charges, ~₹26 slippage):
  // the strategy needs to win more than this share of trades to break even (spec section 6).
  const BREAK_EVEN_WIN_RATE = 53;

  // ``progress`` is how far towards ``need`` the value counts (e.g. a fall in V counts as none).
  function meter(label, value, need, fmt, ok, progress = Math.abs(value ?? 0)) {
    const pctFill = need ? Math.max(0, Math.min(100, (progress / need) * 100)) : 0;
    return `<div class="meter"><div class="meter-head"><span>${label}</span>
        <span class="${ok ? 'pos' : ''}">${value === null || value === undefined ? '—' : fmt(value)} / ${fmt(need)}</span></div>
      <div class="meter-bar"><div class="meter-fill${ok ? ' ok' : ''}" style="width:${pctFill}%"></div></div></div>`;
  }

  function today(day) {
    const lv = day.live?.levels;
    const running = ['STARTED', 'IN_TRADE'].includes(day.run?.status) && day.date === App.meta?.today;
    if (!lv) {
      return `<div class="section-card muted-card" style="margin-bottom:16px"><i class="fa-solid fa-wave-square"></i>
        The signal panel shows the pair being watched and how close the entry signal is, while the algo runs.</div>`;
    }
    const pair = lv.pair;
    const sig = lv.signal || {};
    const losses = lv.losses_in_row ?? 0;
    const pairHtml = pair ? `<div class="pair">
        <div><div class="fact-label">Call</div><div class="fact-value">${App.esc(pair.ce)}</div>
          <div class="muted small">₹${App.num(pair.ce_mid)}${pair.ce_delta !== null ? ` · Δ ${App.num(pair.ce_delta, 2)}` : ''}</div></div>
        <div><div class="fact-label">Put</div><div class="fact-value">${App.esc(pair.pe)}</div>
          <div class="muted small">₹${App.num(pair.pe_mid)}${pair.pe_delta !== null ? ` · Δ ${App.num(pair.pe_delta, 2)}` : ''}</div></div>
        <div><div class="fact-label">Pair value V</div><div class="fact-value">₹${App.num(sig.value ?? pair.value)}</div>
          <div class="muted small">ratio ${App.num(pair.ratio, 2)}</div></div></div>`
      : `<div class="muted small">${App.esc(lv.pair_reason || 'No pair selected yet')}</div>`;
    return `<div class="section-card" style="margin-bottom:16px">
      <div class="card-head"><div><div class="section-title"><i class="fa-solid fa-wave-square"></i> Entry signal</div>
        <div class="section-subtitle">V = mid(CE) + mid(PE) must rise in ${lv.windows_needed} windows in a row, by ${lv.min_rise_pct}%,
          with NIFTY futures moving ${lv.min_fut_move} pts${lv.dte !== null ? ` (DTE ${lv.dte})` : ''}</div></div>
        <div class="chips">${App.chip(`trades ${lv.trades_used} / ${lv.max_trades}`, lv.trades_used >= lv.max_trades ? 'tone-slate' : 'tone-blue')}
          ${App.chip(`losses in a row ${losses} / ${lv.max_consecutive_losses}`, losses ? 'tone-amber' : 'tone-slate')}
          ${App.chip(`day net ${App.money(lv.realized_net)} (limit −₹${lv.max_day_loss})`, App.tone(lv.realized_net) === 'neg' ? 'tone-red' : 'tone-slate')}</div></div>
      <div class="row row-1-1" style="margin:0">
        <div>${pairHtml}
          <div class="meters">
            ${meter('Rising windows', sig.windows_up ?? 0, lv.windows_needed, (v) => String(v), (sig.windows_up ?? 0) >= lv.windows_needed)}
            ${meter('Rise in V', sig.rise_pct, lv.min_rise_pct, (v) => `${Number(v).toFixed(1)}%`, (sig.rise_pct ?? 0) >= lv.min_rise_pct,
              Math.max(0, sig.rise_pct ?? 0))}
            ${meter('Futures move', sig.fut_move, lv.min_fut_move, (v) => `${Math.abs(Number(v)).toFixed(1)} pts`, Math.abs(sig.fut_move ?? 0) >= lv.min_fut_move)}
          </div></div>
        <div><div class="mini-title">V, last 60 seconds</div><div class="chart-box chart-xs"><canvas id="scalp-v"></canvas></div>
          <div class="chart-foot">Stop ${App.money(lv.stop_rupees)} · target ${App.money(lv.target_rupees)} · trailing from ${App.money(lv.trail_activate_rupees)}
            ${lv.trail_peak !== null && lv.trail_peak !== undefined ? ` · peak ${App.money(lv.trail_peak)}` : ''}${running ? '' : ' · last values before the run ended'}</div></div>
      </div></div>`;
  }

  function afterToday(day, animate) {
    const values = day.live?.levels?.values || [];
    const canvas = document.getElementById('scalp-v');
    if (!canvas) return;
    if (values.length < 2) { canvas.parentElement.innerHTML = '<div class="chart-empty">No samples yet</div>'; return; }
    App.chart(canvas, {
      type: 'line',
      data: { labels: values.map((_, i) => i - values.length + 1),
        datasets: [{ data: values, borderColor: App.colorFor('strangle_scalp'), borderWidth: 2, pointRadius: 0, tension: 0.2 }] },
      options: { responsive: true, maintainAspectRatio: false, animation: animate ? undefined : false,
        scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 4, callback(v) { return `${this.getLabelForValue(v)}s`; } } },
          y: { grid: { color: '#f1f5f9' }, ticks: { maxTicksLimit: 5, callback: (v) => '₹' + App.num(v, 2) } } },
        plugins: { tooltip: { callbacks: { label: (c) => `V ₹${App.num(c.parsed.y)}` } } } },
    });
  }

  function performanceNote(p) {
    const wr = p.stats.win_rate;
    if (wr === null) return '';
    const ok = wr > BREAK_EVEN_WIN_RATE;
    return `<div class="banner ${ok ? 'banner-ok' : ''}"><i class="fa-solid fa-scale-balanced"></i>
      Win rate ${wr}% vs the ~${BREAK_EVEN_WIN_RATE}% this scalp needs to break even after charges and slippage
      (₹500 target / ₹300 stop at 1 lot). ${p.stats.trades < 30 ? 'Too few trades to judge yet.' : ok ? 'Above the line.' : 'Below the line.'}</div>`;
  }

  App.strategyPanels('strangle_scalp', { today, afterToday, performanceNote });
})();
