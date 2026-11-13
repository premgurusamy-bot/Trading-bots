"""Runtime container: database, settings, market-data provider, trading service and the background loop."""
from __future__ import annotations

import logging
import secrets as pysecrets
import threading
from datetime import datetime, timedelta
from typing import Optional

from app import db as dbm
from app.config import settings, APP_VERSION
from app.core import secrets
from app.core.service import TradingService
from app.core.types import IST
from app.data.provider import ReplayProvider, MarketDataProvider, FeedStatus
from app.risk.charges import ChargeSchedule
from app.risk.secure_exit import SecureExitConfig
from app.strategy.params import StrategyParams

log = logging.getLogger("runtime")


class DisconnectedProvider(ReplayProvider):
    """Used when Dhan is not configured/available: holds NO data and reports a clear fault."""
    source = "none"

    def __init__(self, reason: str):
        super().__init__()
        self._status = FeedStatus(connected=False, source="none", token_status="missing", faults=[reason])

    def status(self):
        return self._status


class Runtime:
    def __init__(self, database_url: Optional[str] = None, start_feed: bool = True):
        dbm.run_migrations(database_url)
        self.engine = dbm.make_engine(database_url)
        self.Session = dbm.make_session_factory(self.engine)
        self.params = StrategyParams.from_dict(self.get_setting("strategy_params", {}))
        sec = self.get_setting("secure_exit", None)
        self.secure_cfg = SecureExitConfig(**sec) if sec else SecureExitConfig()
        ch = self.get_setting("charges", None)
        self.schedule = ChargeSchedule.from_dict(ch) if ch else ChargeSchedule()
        pa = self.get_setting("paper_account", {"capital": 100000.0})
        self.provider: MarketDataProvider = DisconnectedProvider("Dhan credentials not configured")
        self.dhan_client = None
        if start_feed:
            self.start_provider()
        self.svc = TradingService(self.provider, self.Session, self.params, self.secure_cfg, self.schedule,
                                  paper_capital=pa.get("capital", 100000.0))
        self.svc.load_state()
        self._live_enable_token: Optional[str] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_loop_error: Optional[str] = None
        self.last_loop_at: Optional[datetime] = None
        self.started_at = datetime.now(IST)

    # ---------- settings ----------
    def get_setting(self, key, default):
        with self.Session() as s:
            row = s.get(dbm.SettingRow, key)
            return row.value if row else default

    def put_setting(self, key, value, actor: str, action: str):
        with self.Session() as s:
            row = s.get(dbm.SettingRow, key)
            old = row.value if row else None
            if row is None:
                row = dbm.SettingRow(key=key, value=value)
                s.add(row)
            row.value = value
            row.updated_at = datetime.now(IST)
            s.add(dbm.AuditLog(actor=actor, action=action, detail={"key": key, "old": old, "new": value}))
            s.commit()

    # ---------- provider ----------
    def start_provider(self) -> str:
        if settings.market_data_mode != "dhan":
            self.provider = DisconnectedProvider(f"market_data_mode={settings.market_data_mode}")
            return "disabled"
        cid, tok = secrets.get("dhan_client_id"), secrets.get("dhan_access_token")
        if not cid or not tok:
            self.provider = DisconnectedProvider("Dhan credentials not configured — open Settings → Dhan credentials")
            return "no-credentials"
        from app.data.dhan.client import DhanClient
        from app.data.dhan.feed import DhanMarketData, load_instrument_master
        try:
            self.dhan_client = DhanClient(cid, tok, settings.dhan_api_base)
            load_instrument_master(self.Session, settings.instrument_master_url)
            prov = DhanMarketData(self.dhan_client, self.Session, tok, cid, settings.chain_poll_seconds)
            prov.start()
            self.provider = prov
            return "started"
        except Exception as e:
            msg = f"Dhan feed failed to start: {type(e).__name__}: {str(e)[:200]}"
            log.error(msg)
            self.provider = DisconnectedProvider(msg)
            return "failed"

    def restart_provider(self):
        try:
            self.provider.stop()
        except Exception:
            pass
        st = self.start_provider()
        self.svc.provider = self.provider
        return st

    # ---------- live enablement ----------
    def enable_live(self, actor: str, acknowledgement: str) -> tuple[bool, str]:
        ACK = "I UNDERSTAND LIVE TRADING PLACES REAL ORDERS WITH REAL MONEY"
        if acknowledgement.strip() != ACK:
            return False, f"Type exactly: {ACK}"
        if not settings.live_trading_feature_flag:
            return False, "LIVE_TRADING_FEATURE_FLAG is false in .env (feature flag)"
        if self.dhan_client is None or not isinstance(self.provider, __import__("app.data.dhan.feed", fromlist=["DhanMarketData"]).DhanMarketData):
            return False, "Dhan market data is not connected"
        st = secrets.status()
        if st["token_expired"]:
            return False, "Dhan access token expired"
        if any(t.state not in ("CLOSED", "ENTRY_FAILED") for t in self.svc.trades.values()):
            return False, "Close all paper positions first"
        from app.execution.dhan_live import DhanLiveBroker, LiveTradingNotPermitted
        self._live_enable_token = pysecrets.token_hex(16)
        try:
            lb = DhanLiveBroker(self.dhan_client, self._live_enable_token, self._live_enable_token)
            bal = lb.available_balance()
            if bal is None:
                return False, "Could not read available balance from Dhan"
        except LiveTradingNotPermitted as e:
            return False, str(e)
        self.svc.live_broker = lb
        self.svc.live_enabled = True
        self.svc.mode = "LIVE"
        self.svc.load_state()
        self.svc.audit(actor, "live_trading_enabled", {"balance": bal})
        self.svc.sys_event("WARN", "mode", "LIVE TRADING ENABLED (per-order confirmation required)")
        return True, "Live trading enabled. Every order still needs your confirmation of the pre-trade summary."

    def disable_live(self, actor: str) -> tuple[bool, str]:
        if any(t.mode == "LIVE" and t.state in ("OPEN", "CLOSING", "PENDING_ENTRY") for t in self.svc.trades.values()):
            return False, "Live positions/orders open — exit them first (or use the kill switch)"
        self.svc.mode = "PAPER"
        self.svc.live_enabled = False
        self.svc.live_broker = None
        self._live_enable_token = None
        self.svc.load_state()
        self.svc.audit(actor, "live_trading_disabled", {})
        return True, "Paper mode"

    # ---------- loop ----------
    def _loop(self):
        last_reconcile = datetime.now(IST)
        while not self._stop.is_set():
            try:
                self.svc.step()
                self.last_loop_at = datetime.now(IST)
                if self.svc.mode == "LIVE" and datetime.now(IST) - last_reconcile > timedelta(seconds=30):
                    self.reconcile()
                    last_reconcile = datetime.now(IST)
                self.last_loop_error = None
            except Exception as e:  # keep running; surface the error
                self.last_loop_error = f"{type(e).__name__}: {e}"
                log.exception("service step failed")
            self._stop.wait(1.0)

    def reconcile(self):
        """Compare broker net positions with local open quantity; any mismatch blocks new entries."""
        lb = self.svc.live_broker
        if lb is None:
            return
        for t in self.svc.trades.values():
            if t.mode != "LIVE" or t.state not in ("OPEN", "CLOSING"):
                continue
            net = lb.net_position(t.option["security_id"])
            if net is None or net != t.exit_state.qty_open:
                self.svc.flags.order_state_uncertain = True
                self.svc.sys_event("ERROR", "reconcile", f"Position mismatch for {t.id}: broker {net} vs local {t.exit_state.qty_open}")

    def start(self):
        self._thread = threading.Thread(target=self._loop, name="service-loop", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        try:
            self.provider.stop()
        except Exception:
            pass
