from swing.chart import NOTE, render_html
from swing.overview import IN_ZONE, MID, NEAR_RESISTANCE, NEAR_SUPPORT, NO_ZONES, place, render, row_for
from swing.universe import SwingIndex, SwingUniverse
from swing.zones import Zone, ZoneConfig, ZoneReport, find_zones
from tests.swing.test_zones import make, saw

B = make(saw(30))


def report(close: float, zones: list[tuple[float, float, str]]) -> ZoneReport:
    zs = [Zone(lo, hi, [], role=role) for lo, hi, role in zones]
    return ZoneReport("TEST", B.dates[-1], close, 2.0, len(B), zs)


def test_position_is_the_nearer_zone_within_near_atr():
    around = [(90.0, 99.0, "support"), (102.5, 110.0, "resistance")]
    assert row_for(B, report(100.0, around), 1.0).position == NEAR_SUPPORT      # 0.5 ATR vs 1.25 ATR
    r = row_for(B, report(102.0, around), 1.0)
    assert r.position == NEAR_RESISTANCE and (r.support_atr, r.resistance_atr) == (1.5, 0.25)
    far = [(80.0, 90.0, "support"), (110.0, 120.0, "resistance")]
    assert row_for(B, report(100.0, far), 1.0).position == MID


def test_position_inside_and_without_zones():
    assert row_for(B, report(100.0, [(99.0, 101.0, "inside")]), 1.0).position == IN_ZONE
    assert row_for(B, report(100.0, []), 1.0).position == NO_ZONES


def test_place_matches_the_scan_row():
    zones = [(90.0, 99.0, "support"), (102.5, 110.0, "resistance")]
    r = row_for(B, report(100.0, zones), 1.0)
    p = place(100.0, [(lo, hi) for lo, hi, _ in zones], 2.0, 1.0)
    assert (p.position, p.support, p.support_atr, p.resistance, p.resistance_atr) == (
        r.position, r.support, r.support_atr, r.resistance, r.resistance_atr)
    assert place(100.0, [], 2.0, 1.0).position == NO_ZONES
    assert place(95.0, [(90.0, 99.0)], 2.0, 1.0).zone == (90.0, 99.0)


def test_overview_page():
    s1, s2 = make(saw(400)), make(saw(400, 200, 220, 30))
    s1.symbol, s2.symbol = "M&M", "POWERGRID"
    ix = make(saw(400, 1000, 1100))
    ix.symbol = "NIFTY AUTO"
    zc = ZoneConfig()
    rows = [row_for(b, find_zones(b, zc), 1.0) for b in (s1, s2)]
    irow = row_for(ix, find_zones(ix, zc), 1.0)
    u = SwingUniverse("t", ["M&M", "POWERGRID"], {"M&M": "Automobile", "POWERGRID": "Power"},
                      [SwingIndex("NIFTY AUTO", "sector", ["M&M"])])
    page = render(u, [irow], rows, {"M&M": 500.0, "POWERGRID": 240.0}, 1.0, 25.0, 3)
    assert "M&amp;M" in page and 'href="M_M.html"' in page                    # escaped; file name is the slug
    assert 'href="NIFTY_AUTO.html"' in page and 'data-indices="NIFTY AUTO"' in page
    assert "3 left out" in page and "<script src" not in page
    assert "held no more often" in page                                       # the no-edge note is on the page
    assert "score" not in page.lower().replace("scoring", "")                 # distances, not scores


def test_chart_carries_the_note():
    import html
    b = make(saw(400))
    assert html.escape(NOTE) in render_html(b, find_zones(b, ZoneConfig()))
