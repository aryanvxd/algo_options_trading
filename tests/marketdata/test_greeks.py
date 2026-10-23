import math
from datetime import date, datetime

import numpy as np
import pyarrow as pa
import pytest

from trading_core.marketdata.greeks import enrich, greeks, implied_vol, norm_cdf, price

R = 0.065
F = 25000.0


def test_norm_cdf_accuracy():
    xs = np.linspace(-7, 7, 1401)
    exact = np.array([0.5 * math.erfc(-x / math.sqrt(2)) for x in xs])
    assert np.abs(norm_cdf(xs) - exact).max() < 2e-7


def test_put_call_parity_gives_the_forward():
    t = 3 / 365
    c, p = price(F, 25200, t, R, 0.13, True), price(F, 25200, t, R, 0.13, False)
    assert 25200 + (c - p) * math.exp(R * t) == pytest.approx(F, abs=1e-6)


@pytest.mark.parametrize("days", [0.5, 2, 7, 30])
def test_iv_round_trip_out_of_the_money(days):
    k = np.arange(23000, 27050, 50.0)
    t = np.full(k.shape, days / 365)
    sigma = 0.12 + 5e-9 * (k - F) ** 2
    for is_call in (True, False):
        prem = np.round(price(F, k, t, R, sigma, is_call), 2)          # quoted to the paisa
        iv = implied_vol(prem, F, k, t, R, is_call)
        otm = (k > F) if is_call else (k < F)
        ok = otm & (prem >= 0.5)
        assert ok.sum() > 0
        assert np.nanmax(np.abs(iv[ok] - sigma[ok])) < 5e-4
        assert not np.isnan(iv[ok]).any()


def test_iv_in_the_money_uses_parity():
    t = 30 / 365
    prem = price(F, 24000, t, R, 0.15, True)                            # deep ITM call
    assert implied_vol(prem, F, 24000, t, R, True)[()] == pytest.approx(0.15, abs=1e-4)


def test_iv_outside_no_arbitrage_is_nan():
    iv = implied_vol([0.0, 1e9, -1.0], F, [25000, 25000, 25000], 3 / 365, R, True)
    assert np.isnan(iv).all()


def test_greeks_match_finite_differences():
    t, k, s = 5 / 365, 25100.0, 0.14
    g = greeks(F, k, t, R, s, True)
    h = 0.5
    assert g["delta"] == pytest.approx((price(F + h, k, t, R, s, True) - price(F - h, k, t, R, s, True)) / (2 * h), rel=1e-5)
    d_up = (price(F + h, k, t, R, s, True) - price(F, k, t, R, s, True)) / h
    d_dn = (price(F, k, t, R, s, True) - price(F - h, k, t, R, s, True)) / h
    assert g["gamma"] == pytest.approx((d_up - d_dn) / h, rel=1e-3)
    assert g["vega"] == pytest.approx((price(F, k, t, R, s + 1e-4, True) - price(F, k, t, R, s - 1e-4, True)) / 2e-4 / 100, rel=1e-4)
    dt = 1e-5
    assert g["theta"] == pytest.approx(-(price(F, k, t, R, s, True) - price(F, k, t - dt, R, s, True)) / dt / 365, rel=1e-3)
    put = greeks(F, k, t, R, s, False)
    assert g["delta"] - put["delta"] == pytest.approx(math.exp(-R * t))   # parity for deltas


def chain(ts, expiry, spot, sigma_fn):
    rows = []
    t = (datetime.combine(expiry, datetime.min.time()).replace(hour=15, minute=30) - ts).total_seconds() / (365 * 86400)
    fwd = spot * math.exp(R * t)
    for k in range(int(spot) - 1000, int(spot) + 1050, 50):
        for opt in ("CE", "PE"):
            p = float(price(fwd, k, t, R, sigma_fn(k), opt == "CE"))
            rows.append(dict(ts=ts, expiry=expiry, strike=float(k), opt_type=opt, close=round(p, 2), spot=spot))
    return rows, fwd


def test_enrich_recovers_forward_and_smile():
    sigma_fn = lambda k: 0.13 + 4e-9 * (k - 25000) ** 2
    rows1, f1 = chain(datetime(2026, 10, 23, 10, 0), date(2026, 10, 27), 25000.0, sigma_fn)
    rows2, f2 = chain(datetime(2026, 10, 23, 10, 1), date(2026, 10, 27), 25040.0, sigma_fn)
    out = enrich(pa.Table.from_pylist(rows1 + rows2), R).to_pylist()
    assert {round(r["forward"], 0) for r in out[:len(rows1)]} == {round(f1, 0)}
    assert {round(r["forward"], 0) for r in out[len(rows1):]} == {round(f2, 0)}
    near = [r for r in out if abs(r["strike"] - r["forward"]) <= 600]
    assert max(abs(r["iv"] - sigma_fn(r["strike"])) for r in near) < 1e-3
    # one IV per strike: CE and PE agree
    by_key = {}
    for r in out:
        by_key.setdefault((r["ts"], r["strike"]), set()).add(round(r["iv"], 6) if r["iv"] == r["iv"] else None)
    assert all(len(v) == 1 for v in by_key.values())
    assert all(r["iv_source"] == "computed" for r in out)


def test_enrich_blanks_iv_at_expiry_close():
    rows, _ = chain(datetime(2026, 10, 27, 15, 25), date(2026, 10, 27), 25000.0, lambda k: 0.13)
    out = enrich(pa.Table.from_pylist(rows), R).to_pylist()
    assert all(r["iv"] is None or r["iv"] != r["iv"] for r in out)
