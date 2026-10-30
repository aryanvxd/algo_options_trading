/* Directional buying scalp: bias panel on Today (score, its three parts, breadth, Renko bricks),
   break-even line on Performance. Reads the algo's live levels (live_state.levels). */

(() => {
  // Typical loss ≈ ₹1,200 (8-pt hard stop at 2 lots + charges + slippage) vs typical win ≈ ₹1,270
  // (lot A +12, lot B ~+10): the rough break-even win rate from spec section 7, until the backtest replaces it.
  const BREAK_EVEN_WIN_RATE = 49;

  // A signed part (−1..+1) drawn from the centre: right/green = bullish, left/red = bearish.
  function part(label, value, raw) {
    const v = value === null || value === undefined ? null : Number(value);
    const width = v === null ? 0 : Math.min(50, Math.abs(v) * 50);
    const left = v !== null && v < 0 ? 50 - width : 50;
    return `<div class="meter"><div class="meter-head"><span>${label}</span>
        <span class="${v > 0 ? 'pos' : v < 0 ? 'neg' : ''}">${v === null ? '—' : (v > 0 ? '+' : '') + v.toFixed(2)}${raw ? ` · ${raw}` : ''}</span></div>
      <div class="meter-bar signed"><div class="meter-mid"></div>
        <div class="meter-fill ${v < 0 ? 'bear' : 'ok'}" style="margin-left:${left}%;width:${width}%"></div></div></div>`;
  }

  function bricks(list) {
    if (!list?.length) return '<div class="muted small">No bricks yet</div>';
    return `<div class="bricks">${list.map((b) => `<span class="brick ${b.dir}" title="${App.esc(b.ts)} · ${App.num(b.open, 0)} → ${App.num(b.close, 0)}"></span>`).join('')}</div>`;
  }

  function today(day) {
    const lv = day.live?.levels;
    const running = ['STARTED', 'IN_TRADE'].includes(day.run?.status) && day.date === App.meta?.today;
    if (!lv) {
      return `<div class="section-card muted-card" style="margin-bottom:16px"><i class="fa-solid fa-compass"></i>
        The bias panel shows the score, breadth and the Renko chart while the algo runs.</div>`;
    }
    const b = lv.bias || {};
    const br = lv.breadth;
    const side = lv.bias_ready || lv.bias_side;
    const sideChip = lv.bias_ready ? App.chip(`bias ${lv.bias_ready} ready`, lv.bias_ready === 'CE' ? 'tone-green' : 'tone-red')
      : lv.bias_side ? App.chip(`${lv.bias_side} for ${lv.bias_held_s}s / ${lv.hold_s}s`, 'tone-amber')
        : App.chip('no side', 'tone-slate');
    const losses = lv.losses_in_row ?? 0;
    const last = lv.bricks?.length ? lv.bricks[lv.bricks.length - 1] : null;
    return `<div class="section-card" style="margin-bottom:16px">
      <div class="card-head"><div><div class="section-title"><i class="fa-solid fa-compass"></i> Bias and trigger</div>
        <div class="section-subtitle">CE at score ≥ +${lv.threshold}, PE at ≤ −${lv.threshold}, breadth agreeing, held ${lv.hold_s}s; then
          two ${App.num(lv.brick_pts, 0)}-pt Renko bricks that way${lv.dte !== null ? ` · DTE ${lv.dte}` : ''}</div></div>
        <div class="chips">${sideChip}
          ${App.chip(`trades ${lv.trades_used} / ${lv.max_trades}`, lv.trades_used >= lv.max_trades ? 'tone-slate' : 'tone-blue')}
          ${App.chip(`losses in a row ${losses} / ${lv.max_consecutive_losses}`, losses ? 'tone-amber' : 'tone-slate')}
          ${App.chip(`direction changes ${lv.direction_changes} / ${lv.max_direction_changes}`, 'tone-slate')}
          ${App.chip(`day net ${App.money(lv.realized_net)} (limit −₹${lv.max_day_loss})`, App.tone(lv.realized_net) === 'neg' ? 'tone-red' : 'tone-slate')}</div></div>
      <div class="row row-1-1" style="margin:0">
        <div>
          <div class="pair">
            <div><div class="fact-label">Score</div><div class="fact-value ${b.score > 0 ? 'pos' : b.score < 0 ? 'neg' : ''}">${b.score === null || b.score === undefined ? '—' : (b.score > 0 ? '+' : '') + Number(b.score).toFixed(2)}</div>
              <div class="muted small">${side ? App.esc(side) : 'neutral'}</div></div>
            <div><div class="fact-label">Breadth</div><div class="fact-value">${br ? `${br.advances} ↑ ${br.declines} ↓` : '—'}</div>
              <div class="muted small">${br ? `weighted ${br.weighted > 0 ? '+' : ''}${App.num(br.weighted, 1)}` : 'waiting for prices'}</div></div>
            <div><div class="fact-label">Futures − VWAP</div><div class="fact-value">${b.fut_minus_vwap === null || b.fut_minus_vwap === undefined ? '—' : `${b.fut_minus_vwap > 0 ? '+' : ''}${App.num(b.fut_minus_vwap, 1)}`}</div>
              <div class="muted small">VWAP ${App.num(lv.vwap)}</div></div>
          </div>
          <div class="meters">
            ${part('B1 breadth (40%)', b.b1, br ? `A/D ${br.weighted > 0 ? '+' : ''}${App.num(br.weighted, 1)}` : '')}
            ${part('B2 premium skew (30%)', b.b2, b.skew_ratio ? `PE/CE ${App.num(b.skew_ratio, 2)}` : '')}
            ${part('B3 futures vs VWAP (30%)', b.b3, '')}
          </div>
          ${br?.leaders?.length ? `<div class="muted small" style="margin-top:8px">Heaviest: ${br.leaders.map(App.esc).join(' · ')}</div>` : ''}
        </div>
        <div><div class="mini-title">Renko, NIFTY ${App.num(lv.brick_pts, 0)}-pt bricks (1-minute closes)</div>
          ${bricks(lv.bricks)}
          <div class="chart-foot">${last ? `Last ${App.esc(last.dir)} brick at ${App.esc(last.ts)} · reversal at ${App.num(lv.reversal_level, 0)}` : ''}
            ${lv.side ? ` · in a ${App.esc(lv.side)} at ${App.num(lv.entry_price)}${lv.lot_a_booked ? ', lot A booked' : ''}` : ''}</div>
          <div class="chart-foot">Hard stop −${lv.hard_stop_pts} pts (${App.money(lv.hard_stop_rupees)}) · lot A +${lv.lot_a_target_pts} · lot B +${lv.lot_b_target_pts}
            ${running ? '' : ' · last values before the run ended'}</div></div>
      </div></div>`;
  }

  function performanceNote(p) {
    const wr = p.stats.win_rate;
    if (wr === null) return '';
    const ok = wr > BREAK_EVEN_WIN_RATE;
    return `<div class="banner ${ok ? 'banner-ok' : ''}"><i class="fa-solid fa-scale-balanced"></i>
      Win rate ${wr}% vs the ~${BREAK_EVEN_WIN_RATE}% this scalp needs to break even (rough estimate from the spec;
      the backtest will replace it). ${p.stats.trades < 30 ? 'Too few trades to judge yet.' : ok ? 'Above the line.' : 'Below the line.'}</div>`;
  }

  App.strategyPanels('directional_scalp', { today, performanceNote });
})();
