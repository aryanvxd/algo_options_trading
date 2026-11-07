"""The weekend overview (spec section 10, R1): where each sector and stock sits against its zones.

One self-contained HTML page: sector and theme indices first, then the liquid stocks, each linked
to its chart. Distances, not scores: the backtests found the score adds nothing (spec 9a). This is
information for the user's own decisions; it makes no setups and suggests no trades.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date
from typing import Iterable, NamedTuple

from swing.bars import Bars
from swing.chart import NOTE, THEME
from swing.universe import SwingUniverse
from swing.zones import ZoneReport
from trading_core.marketdata.store import slug

NEAR_SUPPORT, NEAR_RESISTANCE, IN_ZONE, MID, NO_ZONES = ("near support", "near resistance", "in a zone",
                                                         "mid-range", "no zones")


class Band(NamedTuple):
    lo: float
    hi: float


@dataclass
class Place:
    """Where a price sits against a set of zones."""
    position: str
    zone: Band | None            # the zone the price is inside, if any
    support: Band | None
    support_atr: float | None
    resistance: Band | None
    resistance_atr: float | None


def place(price: float, zones: Iterable[Band], atr: float, near_atr: float) -> Place:
    """Position: inside a zone; else within ``near_atr`` ATR of the nearer of the zones around it.
    The scan uses this with the close, an index page with the live price."""
    bands = [Band(*z) for z in zones]
    sup = max((z for z in bands if z.hi < price), key=lambda z: z.hi, default=None)
    res = min((z for z in bands if z.lo > price), key=lambda z: z.lo, default=None)
    inside = next((z for z in bands if z.lo <= price <= z.hi), None)
    d_sup = round((price - sup.hi) / atr, 2) if sup and atr else None
    d_res = round((res.lo - price) / atr, 2) if res and atr else None
    if not bands:
        position = NO_ZONES
    elif inside:
        position = IN_ZONE
    else:
        near = [(d, label) for d, label in ((d_sup, NEAR_SUPPORT), (d_res, NEAR_RESISTANCE))
                if d is not None and d <= near_atr]
        position = min(near)[1] if near else MID
    return Place(position, inside, sup, d_sup, res, d_res)


@dataclass
class Row:
    symbol: str
    day: date
    close: float
    week_pct: float | None
    month_pct: float | None
    position: str
    zone: Band | None
    support: Band | None
    support_atr: float | None
    resistance: Band | None
    resistance_atr: float | None
    atr: float
    zones: list[Band]


def _change(b: Bars, sessions: int) -> float | None:
    return round((b.close[-1] / b.close[-1 - sessions] - 1) * 100, 2) if len(b) > sessions else None


def row_for(b: Bars, rep: ZoneReport, near_atr: float) -> Row:
    bands = [Band(z.lo, z.hi) for z in rep.zones]
    p = place(rep.close, bands, rep.atr, near_atr)
    return Row(b.symbol, b.dates[-1], float(b.close[-1]), _change(b, 5), _change(b, 21), p.position, p.zone,
               p.support, p.support_atr, p.resistance, p.resistance_atr, rep.atr, bands)


def _pct(v: float | None) -> str:
    return "" if v is None else f'<td data-v="{v}" class="{"up" if v >= 0 else "dn"}">{v:+.1f}%</td>'


def _zone_cells(z: Band | None, dist: float | None) -> str:
    if z is None:
        return '<td data-v=""></td><td data-v="9999"></td>'
    return f'<td data-v="{z.lo}">{z.lo:,.2f}–{z.hi:,.2f}</td><td data-v="{dist}">{dist:.1f}</td>'


def _pos_class(position: str) -> str:
    return {NEAR_SUPPORT: "sup", NEAR_RESISTANCE: "res", IN_ZONE: "flip"}.get(position, "")


def _name(symbol: str, linked: bool) -> str:
    text = html.escape(symbol)
    return f'<a href="{slug(symbol)}.html">{text}</a>' if linked else text


def _member_counts(u: SwingUniverse, index: str, by_sym: dict[str, Row]) -> tuple[int, int, int]:
    """An index's liquid stocks: how many, how many near support, how many near resistance."""
    members = [by_sym[s] for s in u.index(index).members if s in by_sym]
    return (len(members), sum(m.position == NEAR_SUPPORT for m in members),
            sum(m.position == NEAR_RESISTANCE for m in members))


def _row_dict(r: Row) -> dict:
    zone = lambda z: [z.lo, z.hi] if z else None  # noqa: E731
    return {"symbol": r.symbol, "close": r.close, "week_pct": r.week_pct, "month_pct": r.month_pct,
            "position": r.position, "zone": zone(r.zone), "support": zone(r.support), "support_atr": r.support_atr,
            "resistance": zone(r.resistance), "resistance_atr": r.resistance_atr, "atr": r.atr,
            "zones": [[z.lo, z.hi] for z in r.zones]}


def payload(u: SwingUniverse, indices: list[Row], stocks: list[Row], traded: dict[str, float], near_atr: float,
            min_traded_value_cr: float, left_out: int, sparks: dict[str, list[float]]) -> dict:
    """What the dashboard's Sectors page shows (overview.json, written by ``algo swing scan``)."""
    by_sym = {r.symbol: r for r in stocks}
    kinds = {ix.name: ix.kind for ix in u.indices}
    sectors = []
    for r in sorted(indices, key=lambda r: (kinds.get(r.symbol, ""), r.symbol)):
        n, n_sup, n_res = _member_counts(u, r.symbol, by_sym)
        sectors.append({**_row_dict(r), "kind": kinds.get(r.symbol, ""), "stocks": n, "near_support": n_sup,
                        "near_resistance": n_res, "spark": sparks.get(r.symbol, [])})
    rows = [{**_row_dict(r), "industry": u.industry.get(r.symbol, ""), "indices": u.indices_of(r.symbol),
             "traded_cr": round(traded.get(r.symbol, 0.0), 1)} for r in sorted(stocks, key=lambda r: r.symbol)]
    day = max((r.day for r in indices + stocks), default=None)
    return {"available": True, "candles_to": day.isoformat() if day else None, "near_atr": near_atr,
            "min_traded_value_cr": min_traded_value_cr, "left_out": left_out, "note": NOTE,
            "sectors": sectors, "stocks": rows}


def render(u: SwingUniverse, indices: list[Row], stocks: list[Row], traded: dict[str, float], near_atr: float,
           min_traded_value_cr: float, left_out: int) -> str:
    by_sym = {r.symbol: r for r in stocks}
    kinds = {ix.name: ix.kind for ix in u.indices}
    sector_rows = []
    for r in sorted(indices, key=lambda r: (kinds.get(r.symbol, ""), r.symbol)):
        n_members, n_sup, n_res = _member_counts(u, r.symbol, by_sym)
        sector_rows.append(
            f'<tr data-index="{html.escape(r.symbol)}"><td>{_name(r.symbol, r.position != NO_ZONES)}</td>'
            f'<td>{kinds.get(r.symbol, "")}</td><td data-v="{r.close}">{r.close:,.2f}</td>'
            f'{_pct(r.week_pct) or "<td></td>"}{_pct(r.month_pct) or "<td></td>"}'
            f'<td class="{_pos_class(r.position)}">{r.position}</td>'
            f'{_zone_cells(r.support, r.support_atr)}{_zone_cells(r.resistance, r.resistance_atr)}'
            f'<td data-v="{n_members}">{n_members}</td><td data-v="{n_sup}">{n_sup}</td>'
            f'<td data-v="{n_res}">{n_res}</td></tr>')
    stock_rows = []
    for r in sorted(stocks, key=lambda r: r.symbol):
        tags = html.escape("|".join(u.indices_of(r.symbol)))
        stock_rows.append(
            f'<tr data-indices="{tags}"><td>{_name(r.symbol, r.position != NO_ZONES)}</td>'
            f'<td>{html.escape(u.industry.get(r.symbol, ""))}</td><td data-v="{r.close}">{r.close:,.2f}</td>'
            f'{_pct(r.week_pct) or "<td></td>"}{_pct(r.month_pct) or "<td></td>"}'
            f'<td class="{_pos_class(r.position)}">{r.position}</td>'
            f'{_zone_cells(r.support, r.support_atr)}{_zone_cells(r.resistance, r.resistance_atr)}'
            f'<td data-v="{traded.get(r.symbol, 0):.1f}">{traded.get(r.symbol, 0):,.0f}</td></tr>')
    options = "".join(f'<option>{html.escape(ix.name)}</option>' for ix in u.indices)
    day = max((r.day for r in indices + stocks), default=None)
    dropped = f" · {left_out} left out (under ₹{min_traded_value_cr:g} crore a day, or no recent candles)"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Swing overview</title>
<style>
{THEME}
h2 {{ font-size:15px; margin:18px 0 6px; }}
.wrap {{ overflow-x:auto; }}
table {{ border-collapse:collapse; font-size:13px; white-space:nowrap; }}
th {{ cursor:pointer; user-select:none; color:var(--muted); font-weight:600; }}
td,th {{ padding:3px 10px; border-bottom:1px solid var(--grid); text-align:left; }}
tbody tr[data-index] {{ cursor:pointer; }} tbody tr.on {{ background:var(--grid); }}
a {{ color:var(--mark); text-decoration:none; }} a:hover {{ text-decoration:underline; }}
.up {{ color:var(--up); }} .dn {{ color:var(--dn); }} .sup {{ color:var(--supt); }} .res {{ color:var(--rest); }}
.flip {{ color:var(--flipt); }}
input,select {{ font:inherit; padding:3px 8px; margin-right:6px; background:transparent; color:var(--fg);
  border:1px solid var(--grid); border-radius:6px; }} select option {{ color:#1f2328; }}
#count {{ color:var(--muted); }}
</style></head><body>
<h1>Swing overview</h1>
<div class="sub">candles to {day} · {len(indices)} indices, {len(stocks)} liquid stocks{dropped} ·
"near" = within {near_atr:g} ATR of the nearest zone · "ATR away" counts from the close · click a column to sort</div>
<p class="note">{html.escape(NOTE)}</p>
<h2>Sectors and themes</h2>
<div class="wrap"><table id="sectors"><thead><tr><th>Index</th><th>Kind</th><th>Close</th><th>1 week</th><th>1 month</th>
<th>Position</th><th>Support</th><th>ATR away</th><th>Resistance</th><th>ATR away</th><th>Liquid stocks</th>
<th>Near support</th><th>Near resistance</th></tr></thead><tbody>{"".join(sector_rows)}</tbody></table></div>
<h2>Stocks</h2>
<div><select id="pick"><option value="">All indices</option>{options}</select>
<select id="pos"><option value="">Any position</option><option>{NEAR_SUPPORT}</option><option>{NEAR_RESISTANCE}</option>
<option>{IN_ZONE}</option><option>{MID}</option></select>
<input id="find" placeholder="Search symbol or industry"><span id="count"></span></div>
<div class="wrap"><table id="stocks"><thead><tr><th>Stock</th><th>Industry</th><th>Close</th><th>1 week</th><th>1 month</th>
<th>Position</th><th>Support</th><th>ATR away</th><th>Resistance</th><th>ATR away</th><th>₹ crore a day</th></tr></thead>
<tbody>{"".join(stock_rows)}</tbody></table></div>
<script>
const $ = (id) => document.getElementById(id);
const stocks = [...$("stocks").tBodies[0].rows];
function filter() {{
  const ix = $("pick").value, pos = $("pos").value, q = $("find").value.trim().toLowerCase();
  let n = 0;
  for (const r of stocks) {{
    const show = (!ix || r.dataset.indices.split("|").includes(ix)) && (!pos || r.cells[5].textContent === pos)
      && (!q || (r.cells[0].textContent + " " + r.cells[1].textContent).toLowerCase().includes(q));
    r.hidden = !show; n += show;
  }}
  $("count").textContent = n + " stocks";
  for (const r of $("sectors").tBodies[0].rows) r.classList.toggle("on", r.dataset.index === ix);
}}
for (const id of ["pick", "pos"]) $(id).onchange = filter;
$("find").oninput = filter;
for (const r of $("sectors").tBodies[0].rows) r.onclick = (e) => {{
  if (e.target.tagName === "A") return;
  $("pick").value = $("pick").value === r.dataset.index ? "" : r.dataset.index;
  filter(); $("stocks").scrollIntoView({{ behavior: "smooth" }});
}};
for (const t of [$("sectors"), $("stocks")]) [...t.tHead.rows[0].cells].forEach((th, i) => {{
  let up = true;
  th.onclick = () => {{
    const val = (r) => r.cells[i].dataset.v ?? r.cells[i].textContent;
    const rows = [...t.tBodies[0].rows].sort((a, b) => {{
      const x = val(a), y = val(b), nx = parseFloat(x), ny = parseFloat(y);
      const c = isNaN(nx) || isNaN(ny) ? String(x).localeCompare(String(y)) : nx - ny;
      return up ? c : -c;
    }});
    up = !up;
    t.tBodies[0].append(...rows);
  }};
}});
filter();
</script>
</body></html>
"""
