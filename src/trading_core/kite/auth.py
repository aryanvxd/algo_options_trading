"""Daily Kite login.

Kite access tokens expire every day (around 06:00 IST), so once each morning you run
``algo login``: it opens the Kite login page, a tiny local server catches the redirect with the
``request_token``, exchanges it for an access token and stores it in ``broker_sessions``. Every
algo process then reads the token from the DB.

Your Kite app's redirect URL must match ``http://<redirect_host>:<redirect_port><redirect_path>``.
"""

from __future__ import annotations

import logging
import threading
import webbrowser
from datetime import datetime, time, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select

from trading_core.clock import IST, Clock
from trading_core.config import KiteConfig, KiteSecrets
from trading_core.db import models as m
from trading_core.db.engine import Database

log = logging.getLogger(__name__)

TOKEN_EXPIRY = time(6, 0)


class NotLoggedIn(RuntimeError):
    pass


def token_valid_until(login_at: datetime) -> datetime:
    login_at = login_at.astimezone(IST)
    expiry = datetime.combine(login_at.date(), TOKEN_EXPIRY, tzinfo=IST)
    return expiry if login_at < expiry else expiry + timedelta(days=1)


def load_active_session(db: Database, now: datetime) -> m.BrokerSession | None:
    with db.session() as s:
        return s.scalars(
            select(m.BrokerSession).where(m.BrokerSession.valid_until > now)
            .order_by(m.BrokerSession.login_at.desc())
        ).first()


def _wait_for_request_token(cfg: KiteConfig, timeout_s: float) -> str:
    result: dict[str, str] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            url = urlparse(self.path)
            qs = parse_qs(url.query)
            ok = url.path == cfg.redirect_path and qs.get("status", [""])[0] == "success" and "request_token" in qs
            if ok:
                result["request_token"] = qs["request_token"][0]
            body = ("Kite login captured. You can close this tab." if ok
                    else "Login failed or unexpected URL; check the terminal.").encode()
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
            if ok:
                done.set()

        def log_message(self, *_args):  # keep request_token out of the console
            pass

    server = HTTPServer((cfg.redirect_host, cfg.redirect_port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if not done.wait(timeout_s):
            raise TimeoutError("Timed out waiting for the Kite login redirect.")
        return result["request_token"]
    finally:
        server.shutdown()
        server.server_close()


def login(db: Database, cfg: KiteConfig, secrets: KiteSecrets, clock: Clock, open_browser: bool = True,
          timeout_s: float = 300) -> m.BrokerSession:
    from kiteconnect import KiteConnect

    kite = KiteConnect(api_key=secrets.api_key)
    url = kite.login_url()
    print(f"Open this URL and log in to Kite:\n  {url}\n"
          f"Waiting for redirect on http://{cfg.redirect_host}:{cfg.redirect_port}{cfg.redirect_path} ...")
    if open_browser:
        webbrowser.open(url)
    request_token = _wait_for_request_token(cfg, timeout_s)
    data = kite.generate_session(request_token, api_secret=secrets.api_secret)

    now = clock.now()
    with db.session() as s:
        row = m.BrokerSession(broker="zerodha", user_id=data.get("user_id"), access_token=data["access_token"],
                              login_at=now, valid_until=token_valid_until(now))
        s.add(row)
    log.info("Kite login OK for %s, token valid until %s", row.user_id, row.valid_until)
    return row
