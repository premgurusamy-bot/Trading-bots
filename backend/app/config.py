"""Application configuration from environment / .env (never from source code).

Secrets (Dhan client id / access token) are NOT read here. See app/core/secrets.py, which uses
the OS credential store (Windows Credential Manager via `keyring`) with an env-file fallback.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"

APP_VERSION = "1.0.0"


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BACKEND_DIR.parent / ".env"), env_file_encoding="utf-8", extra="ignore")

    app_host: str = "127.0.0.1"          # localhost only by default; set 0.0.0.0 for LAN/phone (auth enforced)
    app_port: int = 8000
    database_url: str = f"sqlite:///{(DATA_DIR / 'trading.db').as_posix()}"
    data_dir: str = str(DATA_DIR)
    log_level: str = "INFO"
    # Market data source: "dhan" (live) or "replay" (offline replay of stored/CSV candles; no network)
    market_data_mode: str = "dhan"
    # Live trading feature flag. MUST be explicitly "true" in .env AND enabled in the UI with risk acknowledgement.
    live_trading_feature_flag: bool = False
    # Dhan market-data polling cadence (seconds). Option chain endpoint is rate-limited (see docs/DHAN_INTEGRATION.md).
    chain_poll_seconds: float = 5.0
    quote_poll_seconds: float = 2.0
    instrument_master_url: str = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
    dhan_api_base: str = "https://api.dhan.co/v2"
    dhan_feed_url: str = "wss://api-feed.dhan.co"
    session_secret_ttl_hours: int = 12


settings = AppSettings()
