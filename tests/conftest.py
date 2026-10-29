from __future__ import annotations

import pytest

from trading_core.config import PlatformConfig, project_root
from trading_core.db.engine import Database


@pytest.fixture
def platform(tmp_path) -> PlatformConfig:
    return PlatformConfig(root=project_root(), db_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
                          data_dir=tmp_path / "data", logs_dir=tmp_path / "logs",
                          marketdata={"dir": tmp_path / "market"}, calendar_autofetch=False)   # no network


@pytest.fixture
def db(platform) -> Database:
    d = Database(platform.db_url)
    d.create_all()
    return d
