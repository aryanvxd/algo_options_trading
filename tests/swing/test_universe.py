from datetime import date

import pytest

from swing.settings import SwingConfig, load_swing_config
from swing.universe import build_universe, load_swing_universe, parse_constituents, write_universe
from trading_core.config import load_universe

ENERGY = ("﻿Company Name,Industry,Symbol,Series,ISIN Code\n"
          "Power Grid Corporation of India Ltd.,Power,POWERGRID,EQ,INE752E01010\n"
          "NTPC Ltd.,Power,NTPC,EQ,INE733E01010\n"
          "Some Partly Paid Ltd.,Power,SPPL,E1,INE000000000\n")
PSE = ("Company Name,Industry,Symbol,Series,ISIN Code\n"
       "Power Grid Corporation of India Ltd.,Power,POWERGRID,EQ,INE752E01010\n"
       "State Bank of India,Financial Services,SBIN,EQ,INE062A01020\n")


def cfg(**kw) -> SwingConfig:
    return SwingConfig(indices=[{"name": "NIFTY ENERGY", "kind": "theme", "csv": "energy.csv"},
                                {"name": "NIFTY PSE", "kind": "theme", "csv": "pse.csv"}], **kw)


def test_parse_keeps_eq_series_and_industry():
    assert parse_constituents(ENERGY) == [("POWERGRID", "Power"), ("NTPC", "Power")]


def test_parse_rejects_html():
    with pytest.raises(ValueError):
        parse_constituents("<!DOCTYPE html><html></html>")


def test_build_merges_members_and_tags():
    files = {"energy.csv": ENERGY, "pse.csv": PSE}
    u = build_universe(cfg(), files.__getitem__, date(2026, 11, 1), progress=lambda _: None, pause_s=0)
    assert u.symbols == ["NTPC", "POWERGRID", "SBIN"]
    assert u.industry["SBIN"] == "Financial Services"
    assert u.indices_of("POWERGRID") == ["NIFTY ENERGY", "NIFTY PSE"]
    assert u.index("NIFTY PSE").members == ["POWERGRID", "SBIN"]


def test_failed_index_is_left_out():
    seen = []
    u = build_universe(cfg(), {"energy.csv": ENERGY}.__getitem__, date(2026, 11, 1), progress=seen.append,
                       pause_s=0)
    assert [ix.name for ix in u.indices] == ["NIFTY ENERGY"]
    assert any("FAILED" in s for s in seen)


def test_round_trip_and_platform_universe(platform, tmp_path):
    platform = platform.model_copy(update={"root": tmp_path})
    (tmp_path / "config" / "universes").mkdir(parents=True)
    files = {"energy.csv": ENERGY, "pse.csv": PSE}
    u = build_universe(cfg(universe="swing_t"), files.__getitem__, date(2026, 11, 1),
                       progress=lambda _: None, pause_s=0)
    write_universe(platform, u)
    back = load_swing_universe(platform, "swing_t")
    assert back.symbols == u.symbols and back.as_of == date(2026, 11, 1)
    assert back.indices_of("SBIN") == ["NIFTY PSE"]
    assert load_universe(platform, "swing_t") == ["NSE:NTPC", "NSE:POWERGRID", "NSE:SBIN"]


def test_shipped_config_loads():
    c = load_swing_config()
    assert c.alerts_enabled is False
    names = [ix.name for ix in c.indices]
    assert len(names) == len(set(names)) and "NIFTY ENERGY" in names and "NIFTY PSE" in names
