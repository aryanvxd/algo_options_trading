"""Black-76 prices, implied volatility and greeks, vectorised with numpy.

Black-76 prices an option on the *forward*. We take the forward from put-call parity on the
option chain itself (synthetic forward), so no futures history is needed and dividends/carry
are already in it. Conventions:
  delta  per 1 point of the forward          gamma  per 1 point
  theta  in points per calendar day          vega   in points per 1 vol-point (0.01)
"""

from __future__ import annotations

from datetime import date, datetime, time

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

MIN_TTE_YEARS = 1.0 / (365 * 24 * 60)        # one minute
MIN_IV_TTE_YEARS = 15.0 / (365 * 24 * 60)    # with <15 min left IV is noise (e.g. EOD on expiry day)
EXPIRY_CLOSE = time(15, 30)
_SQRT2 = np.sqrt(2.0)
_INV_SQRT_2PI = 1.0 / np.sqrt(2.0 * np.pi)


def norm_cdf(x: np.ndarray) -> np.ndarray:
    """Standard normal CDF via a rational erfc approximation (abs error < 1.2e-7)."""
    z = np.abs(x) / _SQRT2
    t = 1.0 / (1.0 + 0.5 * z)
    erfc = t * np.exp(-z * z - 1.26551223 + t * (1.00002368 + t * (0.37409196 + t * (0.09678418 + t * (
        -0.18628806 + t * (0.27886807 + t * (-1.13520398 + t * (1.48851587 + t * (-0.82215223
        + t * 0.17087277)))))))))
    return np.where(x >= 0, 1.0 - 0.5 * erfc, 0.5 * erfc)


def norm_pdf(x: np.ndarray) -> np.ndarray:
    return _INV_SQRT_2PI * np.exp(-0.5 * x * x)


def _d1d2(f, k, t, sigma):
    vol_t = sigma * np.sqrt(t)
    d1 = (np.log(f / k) + 0.5 * vol_t * vol_t) / vol_t
    return d1, d1 - vol_t


def price(f, k, t, r, sigma, is_call) -> np.ndarray:
    f, k, t, sigma = map(np.asarray, (f, k, t, sigma))
    is_call = np.asarray(is_call, dtype=bool)
    df = np.exp(-r * t)
    d1, d2 = _d1d2(f, k, t, sigma)
    call = df * (f * norm_cdf(d1) - k * norm_cdf(d2))
    put = df * (k * norm_cdf(-d2) - f * norm_cdf(-d1))
    return np.where(is_call, call, put)


def implied_vol(premium, f, k, t, r, is_call, lo: float = 1e-4, hi: float = 5.0,
                newton_steps: int = 8, bisect_steps: int = 50, tol: float = 1e-6,
                min_otm_premium: float = 0.0) -> np.ndarray:
    """Newton from a Brenner–Subrahmanyam start, then vectorised bisection for whatever did not
    converge (deep OTM / near expiry, where vega is tiny). NaN outside the no-arbitrage range."""
    premium, f, k, t = np.broadcast_arrays(*(np.asarray(a, dtype=float) for a in (premium, f, k, t)))
    is_call = np.broadcast_to(np.asarray(is_call, dtype=bool), premium.shape)
    t = np.maximum(t, MIN_TTE_YEARS)
    df = np.exp(-r * t)
    intrinsic = df * np.where(is_call, np.maximum(f - k, 0.0), np.maximum(k - f, 0.0))
    upper = df * np.where(is_call, f, k)
    with np.errstate(invalid="ignore", divide="ignore"):
        valid = np.isfinite(premium) & np.isfinite(f) & (premium > intrinsic) & (premium < upper) & (k > 0) & (f > 0)
    iv = np.full(premium.shape, np.nan)
    if not valid.any():
        return iv
    p, ff, kk, tt, cc, d = (x[valid] for x in (premium, f, k, t, is_call, df))
    # In-the-money prices hardly depend on vol (ill-conditioned). Convert each ITM option to its
    # OTM twin via put-call parity (C - P = df·(F - K)); both share one Black-76 IV.
    itm = np.where(cc, ff > kk, kk > ff)
    p = np.where(itm, p - d * np.abs(ff - kk), p)
    cc = np.where(itm, ~cc, cc)
    ok = (p > 0) & (p >= min_otm_premium)
    if not ok.all():
        valid_idx = np.flatnonzero(valid)
        valid[valid_idx[~ok]] = False
        p, ff, kk, tt, cc, d = (x[ok] for x in (p, ff, kk, tt, cc, d))
        if p.size == 0:
            return iv

    with np.errstate(all="ignore"):
        sigma = np.clip(np.sqrt(2 * np.pi / tt) * p / (d * ff), 0.05, 1.0)
        for _ in range(newton_steps):
            d1, _d2 = _d1d2(ff, kk, tt, sigma)
            vega = d * ff * norm_pdf(d1) * np.sqrt(tt)
            step = (price(ff, kk, tt, r, sigma, cc) - p) / vega
            sigma = np.clip(sigma - np.where(np.isfinite(step), step, 0.0), lo, hi)
        done = np.abs(price(ff, kk, tt, r, sigma, cc) - p) < tol * np.maximum(1.0, p)

    todo = ~done
    if todo.any():
        a = np.full(todo.sum(), lo)
        b = np.full(todo.sum(), hi)
        pt, ft, kt, tt2, ct = p[todo], ff[todo], kk[todo], tt[todo], cc[todo]
        for _ in range(bisect_steps):
            mid = 0.5 * (a + b)
            too_high = price(ft, kt, tt2, r, mid, ct) > pt
            b = np.where(too_high, mid, b)
            a = np.where(too_high, a, mid)
        sigma[todo] = 0.5 * (a + b)
    sigma = np.where(sigma < hi * 0.999, sigma, np.nan)   # pinned at the bound: no solution
    iv[valid] = sigma
    return iv


def greeks(f, k, t, r, sigma, is_call) -> dict[str, np.ndarray]:
    f, k, t, sigma = (np.asarray(a, dtype=float) for a in (f, k, t, sigma))
    is_call = np.asarray(is_call, dtype=bool)
    t = np.maximum(t, MIN_TTE_YEARS)
    df = np.exp(-r * t)
    d1, d2 = _d1d2(f, k, t, sigma)
    pdf = norm_pdf(d1)
    sqrt_t = np.sqrt(t)
    prem = price(f, k, t, r, sigma, is_call)
    delta = np.where(is_call, df * norm_cdf(d1), -df * norm_cdf(-d1))
    gamma = df * pdf / (f * sigma * sqrt_t)
    vega = df * f * pdf * sqrt_t / 100.0
    theta = (-df * f * pdf * sigma / (2.0 * sqrt_t) + r * prem) / 365.0
    return dict(delta=delta, gamma=gamma, theta=theta, vega=vega)


def tte_years(ts: np.ndarray, expiry: np.ndarray) -> np.ndarray:
    """Years from each timestamp to 15:30 on its expiry date (numpy datetime64 inputs)."""
    expiry_close = expiry.astype("datetime64[m]") + np.timedelta64(15 * 60 + 30, "m")
    minutes = (expiry_close - ts.astype("datetime64[m]")).astype("float64")
    return np.maximum(minutes / (365.0 * 24 * 60), MIN_TTE_YEARS)


def synthetic_forward(strike: np.ndarray, call: np.ndarray, put: np.ndarray, t: np.ndarray, r: float,
                      spot: float | None) -> float:
    """Median of K + (C - P)·e^{rT} over the (up to) 3 strikes nearest the spot/ATM with both legs priced."""
    ok = np.isfinite(call) & np.isfinite(put)
    if not ok.any():
        return np.nan
    strike, call, put, t = strike[ok], call[ok], put[ok], t[ok]
    centre = spot if spot is not None and np.isfinite(spot) else strike[np.argmin(np.abs(call - put))]
    nearest = np.argsort(np.abs(strike - centre))[:3]
    return float(np.median(strike[nearest] + (call[nearest] - put[nearest]) * np.exp(r * t[nearest])))


def enrich(table: pa.Table, r: float, price_col: str = "close", ts_col: str = "ts",
           iv_source: str = "computed", min_otm_premium: float = 0.05) -> pa.Table:
    """Add forward, tte_years, iv and greeks to an option-chain table.

    Needs columns: ``expiry, strike, opt_type, <price_col>`` and either ``ts`` (intraday) or
    ``trade_date`` (EOD, valued at 15:30). ``spot`` is used to centre the synthetic forward when
    present. The forward is solved per (timestamp, expiry) group."""
    if table.num_rows == 0:
        return table
    cols = table.column_names
    if ts_col in cols:
        ts = table[ts_col].to_numpy(zero_copy_only=False).astype("datetime64[m]")
    else:
        day = table["trade_date"].to_numpy(zero_copy_only=False).astype("datetime64[D]")
        ts = day.astype("datetime64[m]") + np.timedelta64(15 * 60 + 30, "m")
    expiry = table["expiry"].to_numpy(zero_copy_only=False).astype("datetime64[D]")
    strike = table["strike"].to_numpy(zero_copy_only=False).astype(float)
    is_call = np.asarray(pc.equal(table["opt_type"], "CE").to_numpy(zero_copy_only=False), dtype=bool)
    prem = np.asarray(table[price_col].to_numpy(zero_copy_only=False), dtype=float)
    spot = (np.asarray(table["spot"].to_numpy(zero_copy_only=False), dtype=float)
            if "spot" in cols else np.full(len(strike), np.nan))
    t = tte_years(ts, expiry)

    forward = np.full(len(strike), np.nan)
    group = ts.astype("int64") * 100_000 + expiry.astype("int64")
    order = np.argsort(group, kind="stable")
    bounds = np.flatnonzero(np.diff(group[order])) + 1
    for idx in np.split(order, bounds):
        k = strike[idx]
        calls = {kk: p for kk, p, c in zip(k, prem[idx], is_call[idx]) if c}
        puts = {kk: p for kk, p, c in zip(k, prem[idx], is_call[idx]) if not c}
        both = np.array(sorted(set(calls) & set(puts)), dtype=float)
        if both.size == 0:
            continue
        s = spot[idx]
        f = synthetic_forward(both, np.array([calls[x] for x in both]), np.array([puts[x] for x in both]),
                              np.full(both.size, t[idx][0]), r, float(np.nanmedian(s)) if np.isfinite(s).any() else None)
        forward[idx] = f

    iv = implied_vol(prem, forward, strike, t, r, is_call, min_otm_premium=min_otm_premium)
    iv = np.where(t >= MIN_IV_TTE_YEARS, iv, np.nan)
    # One IV per (minute, expiry, strike): take it from the OTM side, which is well conditioned.
    with np.errstate(invalid="ignore"):
        otm = np.where(is_call, strike >= forward, strike <= forward) & np.isfinite(iv)
    _, inv = np.unique(np.stack([group.astype(float), strike]), axis=1, return_inverse=True)
    inv = inv.ravel()
    otm_iv = np.full(inv.max() + 1, np.nan)
    otm_iv[inv[otm]] = iv[otm]
    iv = np.where(np.isfinite(otm_iv[inv]), otm_iv[inv], iv)
    g = greeks(forward, strike, t, r, np.where(np.isfinite(iv), iv, 0.2), is_call)
    nan = ~np.isfinite(iv)
    out = table
    for name, values, typ in [("forward", forward, pa.float64()), ("tte_years", t, pa.float64()),
                              ("iv", iv, pa.float32()), ("delta", np.where(nan, np.nan, g["delta"]), pa.float32()),
                              ("gamma", np.where(nan, np.nan, g["gamma"]), pa.float32()),
                              ("theta", np.where(nan, np.nan, g["theta"]), pa.float32()),
                              ("vega", np.where(nan, np.nan, g["vega"]), pa.float32())]:
        arr = pa.array(values, type=pa.float64(), from_pandas=True).cast(typ)
        out = out.set_column(out.column_names.index(name), name, arr) if name in out.column_names \
            else out.append_column(name, arr)
    src = pa.array([iv_source] * out.num_rows, type=pa.string())
    out = out.set_column(out.column_names.index("iv_source"), "iv_source", src) if "iv_source" in out.column_names \
        else out.append_column("iv_source", src)
    return out


def years_between(start: datetime, expiry: date) -> float:
    end = datetime.combine(expiry, EXPIRY_CLOSE)
    return max((end - start.replace(tzinfo=None)).total_seconds() / (365 * 24 * 3600), MIN_TTE_YEARS)
