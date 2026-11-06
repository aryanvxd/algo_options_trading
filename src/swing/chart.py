"""A self-contained HTML chart of one stock's candles and zones (``algo swing chart``).

Plain inline SVG, no chart library, so the file opens offline from data/reports/swing/. The
dashboard's interactive stock page (W5, Lightweight Charts) replaces it later.
"""

from __future__ import annotations

import html
import math
from datetime import timedelta

from swing.bars import Bars, weekly
from swing.zones import ZoneReport

W, H = 1200, 560                       # plot area
PAD_L, PAD_R, PAD_T, PAD_B = 10, 170, 16, 28

NOTE = ("Zones show past turning points, not a forecast: in the backtests they held no more often "
        "than random price levels of the same width. Information only, not a recommendation.")
THEME = """:root { --bg:#fbfaf7; --fg:#1f2328; --muted:#6b7280; --grid:#e5e2da; --up:#1a7f5a; --dn:#c23b3b;
  --sup:rgba(26,127,90,.16); --res:rgba(194,59,59,.15); --flip:rgba(201,138,20,.20); --in:rgba(90,90,200,.15);
  --supt:#1a7f5a; --rest:#b03434; --flipt:#a8700c; --mark:#2f5fd0; }
@media (prefers-color-scheme: dark) { :root { --bg:#16181c; --fg:#e6e6e6; --muted:#9aa0a6; --grid:#2a2d33;
  --up:#3fbf8a; --dn:#e06666; --sup:rgba(63,191,138,.16); --res:rgba(224,102,102,.16);
  --flip:rgba(230,170,60,.20); --supt:#3fbf8a; --rest:#e06666; --flipt:#e6aa3c; --mark:#7aa2ff; } }
body { margin:0; padding:16px; background:var(--bg); color:var(--fg); font:14px/1.4 system-ui,sans-serif; }
h1 { font-size:18px; margin:0 0 4px; } .sub { color:var(--muted); margin-bottom:10px; }
.note { color:var(--muted); font-size:12px; margin:0 0 10px; }"""


def _nice_step(span: float, ticks: int = 8) -> float:
    raw = span / ticks
    p = 10 ** math.floor(math.log10(raw))
    return next(m * p for m in (1, 2, 2.5, 5, 10) if m * p >= raw)


def _zone_kind(z) -> str:
    return "flip" if z.flipped else "sup" if z.role == "support" else "res" if z.role == "resistance" else "in"


def _svg(b: Bars, rep: ZoneReport, mark: float | None, mark_label: str) -> str:
    lo = min(float(b.low.min()), *(z.lo for z in rep.zones)) if rep.zones else float(b.low.min())
    hi = max(float(b.high.max()), *(z.hi for z in rep.zones)) if rep.zones else float(b.high.max())
    if mark is not None:
        lo, hi = min(lo, mark), max(hi, mark)
    pad = (hi - lo) * 0.03
    lo, hi = lo - pad, hi + pad
    y = lambda p: PAD_T + (hi - p) / (hi - lo) * H  # noqa: E731
    n = len(b)
    step = W / max(n, 1)
    x = lambda i: PAD_L + (i + 0.5) * step  # noqa: E731
    body = max(1.0, step * 0.6)
    out = [f'<svg viewBox="0 0 {PAD_L + W + PAD_R} {PAD_T + H + PAD_B}" role="img">']
    t = _nice_step(hi - lo)
    p = math.ceil(lo / t) * t
    while p <= hi:
        out.append(f'<line class="grid" x1="{PAD_L}" x2="{PAD_L + W}" y1="{y(p):.1f}" y2="{y(p):.1f}"/>'
                   f'<text class="axis" x="{PAD_L + W + 6}" y="{y(p) + 4:.1f}">{p:,.0f}</text>')
        p += t
    for z in rep.zones:
        k = _zone_kind(z)
        top, bot = y(z.hi), y(z.lo)
        role = ("flipped " if z.flipped else "") + z.role
        tip = html.escape(f"{z.lo:,.2f}-{z.hi:,.2f} {role}, score {z.score:.0f}, touches "
                          f"{z.count('support')}S/{z.count('resistance')}R, last test {z.last_test}")
        out.append(f'<rect class="z {k}" x="{PAD_L}" width="{W}" y="{top:.1f}" height="{max(bot - top, 1):.1f}">'
                   f'<title>{tip}</title></rect>'
                   f'<text class="zl {k}" x="{PAD_L + W + 46}" y="{(top + bot) / 2 + 4:.1f}">'
                   f'{z.lo:,.0f}-{z.hi:,.0f} · {z.score:.0f}</text>')
    last_month = None
    for i, d in enumerate(b.dates):
        if (d.year, d.month) != last_month and d.month in (1, 4, 7, 10):
            out.append(f'<line class="grid" x1="{x(i):.1f}" x2="{x(i):.1f}" y1="{PAD_T}" y2="{PAD_T + H}"/>'
                       f'<text class="axis" x="{x(i) + 3:.1f}" y="{PAD_T + H + 18}">{d:%b %Y}</text>')
        last_month = (d.year, d.month)
        o, c = float(b.open[i]), float(b.close[i])
        cls = "up" if c >= o else "dn"
        out.append(f'<line class="w {cls}" x1="{x(i):.1f}" x2="{x(i):.1f}" y1="{y(b.high[i]):.1f}" '
                   f'y2="{y(b.low[i]):.1f}"/>'
                   f'<rect class="{cls}" x="{x(i) - body / 2:.1f}" width="{body:.1f}" y="{y(max(o, c)):.1f}" '
                   f'height="{max(abs(y(o) - y(c)), 0.8):.1f}"><title>{d} O {o:,.2f} H {b.high[i]:,.2f} '
                   f'L {b.low[i]:,.2f} C {c:,.2f}</title></rect>')
    out.append(f'<line class="close" x1="{PAD_L}" x2="{PAD_L + W}" y1="{y(rep.close):.1f}" y2="{y(rep.close):.1f}"/>'
               f'<text class="closel" x="{PAD_L + W + 6}" y="{y(rep.close) - 4:.1f}">close {rep.close:,.2f}</text>')
    if mark is not None:
        out.append(f'<line class="mark" x1="{PAD_L}" x2="{PAD_L + W}" y1="{y(mark):.1f}" y2="{y(mark):.1f}"/>'
                   f'<text class="markl" x="{PAD_L + 6}" y="{y(mark) - 5:.1f}">'
                   f'{html.escape(mark_label)} {mark:,.2f}</text>')
    out.append("</svg>")
    return "".join(out)


def render_html(daily: Bars, rep: ZoneReport, days: int = 730, mark: float | None = None,
                mark_label: str = "mark") -> str:
    """Daily and weekly views of the last ``days`` days, with ``rep``'s zones."""
    d = daily.since(rep.asof - timedelta(days=days))
    w = weekly(d)
    rows = "".join(
        f'<tr class="{_zone_kind(z)}"><td>{z.lo:,.2f}-{z.hi:,.2f}</td>'
        f'<td>{"flipped " if z.flipped else ""}{z.role}</td><td>{z.score:.0f}</td>'
        f'<td>{z.count("support")}/{z.count("resistance")}</td><td>{z.last_test or "-"}</td>'
        f'<td>{html.escape(", ".join(z.confluence))}</td></tr>'
        for z in sorted(rep.zones, key=lambda z: -z.hi))
    title = html.escape(f"{rep.symbol} zones")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
{THEME}
button {{ font:inherit; padding:4px 12px; margin-right:6px; border:1px solid var(--grid); background:transparent;
  color:var(--fg); border-radius:6px; cursor:pointer; }} button.on {{ background:var(--grid); }}
svg {{ width:100%; height:auto; display:block; }} .hide {{ display:none; }}
.grid {{ stroke:var(--grid); stroke-width:1; }} .axis {{ fill:var(--muted); font-size:11px; }}
.up {{ fill:var(--up); stroke:var(--up); }} .dn {{ fill:var(--dn); stroke:var(--dn); }} .w {{ stroke-width:1; }}
.z.sup {{ fill:var(--sup); }} .z.res {{ fill:var(--res); }} .z.flip {{ fill:var(--flip); }} .z.in {{ fill:var(--in); }}
.zl {{ font-size:11px; }} .zl.sup {{ fill:var(--supt); }} .zl.res {{ fill:var(--rest); }} .zl.flip {{ fill:var(--flipt); }}
.close {{ stroke:var(--fg); stroke-dasharray:4 3; stroke-width:1; }} .closel {{ fill:var(--fg); font-size:11px; }}
.mark {{ stroke:var(--mark); stroke-width:1.5; }} .markl {{ fill:var(--mark); font-size:12px; font-weight:600; }}
table {{ border-collapse:collapse; margin-top:14px; font-size:13px; }}
td,th {{ padding:3px 10px; border-bottom:1px solid var(--grid); text-align:left; }}
tr.sup td:first-child {{ color:var(--supt); }} tr.res td:first-child {{ color:var(--rest); }}
tr.flip td:first-child {{ color:var(--flipt); }}
</style></head><body>
<h1>{title}</h1>
<div class="sub">as of {rep.asof} · close {rep.close:,.2f} · ATR(14) {rep.atr:,.2f} · zones from the last two years ·
green support, red resistance, amber flipped · hover a candle or zone for details</div>
<p class="note">{html.escape(NOTE)}</p>
<div><button id="bd" class="on">Daily</button><button id="bw">Weekly</button></div>
<div id="vd">{_svg(d, rep, mark, mark_label)}</div>
<div id="vw" class="hide">{_svg(w, rep, mark, mark_label)}</div>
<table><tr><th>Zone</th><th>Role</th><th>Score</th><th>Touches S/R</th><th>Last test</th><th>Confluence</th></tr>
{rows}</table>
<script>
const show = (k) => {{ for (const v of ["d","w"]) {{
  document.getElementById("v"+v).classList.toggle("hide", v !== k);
  document.getElementById("b"+v).classList.toggle("on", v === k); }} }};
document.getElementById("bd").onclick = () => show("d");
document.getElementById("bw").onclick = () => show("w");
</script>
</body></html>
"""
