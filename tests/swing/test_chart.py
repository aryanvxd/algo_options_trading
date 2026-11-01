from swing.chart import render_html
from swing.zones import ZoneConfig, find_zones
from tests.swing.test_zones import make, saw


def test_chart_has_both_views_zones_and_mark():
    b = make(saw(400) + [(104.5, 105.5, 105.0)])
    rep = find_zones(b, ZoneConfig())
    page = render_html(b, rep, mark=101.5, mark_label="entry")
    assert page.count("<svg") == 2                       # daily and weekly
    assert page.count('class="z ') == 2 * len(rep.zones)
    assert "entry 101.50" in page and "close 105.00" in page
    assert "<script src" not in page                     # self-contained: opens offline
