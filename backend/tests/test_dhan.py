"""Dhan adapter parsing and client behaviour.

Fixtures are SANITIZED/CONSTRUCTED per the official SDK v2.3.0 packet layouts and the documented JSON
shape (see app/data/dhan/parsers.py for provenance). No network access, no real credentials."""
import base64
import json
import struct
from datetime import date, datetime, timezone, timedelta

import httpx
import pytest

from app.core import secrets
from app.core.types import IST, Index, OptionType
from app.data.dhan.client import DhanClient, DhanError
from app.data.dhan.parsers import (parse_option_chain, parse_candles, parse_ws_packet, parse_instrument_master,
                                   parse_expiry_list, ParseError)
from app.execution.dhan_live import DhanLiveBroker, LiveTradingNotPermitted, correlation_id

RX = datetime(2026, 11, 13, 10, 0, tzinfo=IST)


def chain_fixture():
    side = lambda sid, ltp: {"greeks": {"delta": 0.52, "theta": -9.1, "gamma": 0.0011, "vega": 12.0},
                             "implied_volatility": 12.8, "last_price": ltp, "oi": 120000, "previous_close_price": 100.0,
                             "previous_oi": 110000, "previous_volume": 0, "security_id": sid, "top_ask_price": ltp + 0.1,
                             "top_ask_quantity": 650, "top_bid_price": ltp - 0.1, "top_bid_quantity": 650, "volume": 50000}
    return {"data": {"last_price": 24010.5, "oc": {
        "24000.000000": {"ce": side(1001, 110.0), "pe": side(1002, 95.0)},
        "24050.000000": {"ce": side(1003, 85.0), "pe": {**side(1004, 0.0), "greeks": {}, "top_bid_price": 0, "top_ask_price": 0}},
    }}, "status": "success"}


def test_parse_option_chain_fields_and_missing_values():
    snap = parse_option_chain(chain_fixture(), Index.NIFTY, date(2026, 11, 18), RX)
    assert snap.underlying_ltp == 24010.5 and len(snap.rows) == 4
    ce = snap.get(24000.0, OptionType.CE)
    assert ce.security_id == "1001" and ce.delta == 0.52 and ce.greeks_source == "feed" and ce.oi_change == 10000
    pe = snap.get(24050.0, OptionType.PE)
    assert pe.ltp is None and pe.bid is None and pe.delta is None and pe.greeks_source is None   # zeros => missing


@pytest.mark.parametrize("bad", [{}, {"data": {}}, {"data": {"last_price": 1, "oc": {}}},
                                 {"data": {"last_price": 1, "oc": {"abc": {"ce": {}}}}},
                                 {"data": {"last_price": 1, "oc": {"100": {"ce": {"last_price": "x"}}}}}])
def test_parse_option_chain_rejects_malformed(bad):
    with pytest.raises(ParseError):
        parse_option_chain(bad, Index.NIFTY, date(2026, 11, 18), RX)


def test_parse_candles_and_completeness():
    t0 = int(datetime(2026, 11, 13, 9, 15, tzinfo=IST).timestamp())
    raw = {"open": [1, 2], "high": [2, 3], "low": [0.5, 1.5], "close": [1.5, 2.5], "volume": [10, 20],
           "timestamp": [t0, t0 + 300]}
    cs = parse_candles(raw, "dhan", 5, now=datetime(2026, 11, 13, 9, 22, tzinfo=IST))
    assert cs[0].ts.tzinfo is not None and cs[0].ts.hour == 9 and cs[0].ts.minute == 15
    assert cs[0].complete and not cs[1].complete
    with pytest.raises(ParseError):
        parse_candles({**raw, "close": [1]}, "dhan", 5)
    with pytest.raises(ParseError):
        parse_candles({**raw, "timestamp": [t0, t0]}, "dhan", 5)


def test_expiry_list():
    assert parse_expiry_list({"data": ["2026-11-25", "2026-11-18"]})[0] == date(2026, 11, 18)
    with pytest.raises(ParseError):
        parse_expiry_list({"data": ["nope"]})


def test_ws_packets_per_sdk_layout():
    ltt = int(datetime(2026, 11, 13, 4, 30, tzinfo=timezone.utc).timestamp())
    tick = struct.pack("<BHBIfI", 2, 16, 0, 13, 24010.5, ltt)
    p = parse_ws_packet(tick)
    assert p["type"] == "ticker" and p["security_id"] == "13" and p["segment"] == "IDX_I" and p["ltp"] == 24010.5
    depth = b"".join(struct.pack("<IIHHff", 650 * (i + 1), 325, 3, 2, 100.0 - i * 0.05, 100.1 + i * 0.05) for i in range(5))
    full = struct.pack("<BHBIfHIfIIIIIIffff100s", 8, 162, 2, 1001, 100.05, 75, ltt, 100.0, 50000, 1, 2, 120000, 125000,
                       110000, 98.0, 97.0, 102.0, 96.0, depth)
    f = parse_ws_packet(full)
    assert f["type"] == "full" and f["depth"][0]["bid_qty"] == 650 and abs(f["depth"][0]["ask"] - 100.1) < 1e-4
    assert f["oi"] == 120000
    with pytest.raises(ParseError):
        parse_ws_packet(full[:50])


def test_instrument_master_compact():
    csv_text = ("SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,SEM_EXPIRY_CODE,SEM_TRADING_SYMBOL,"
                "SEM_LOT_UNITS,SEM_CUSTOM_SYMBOL,SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_TICK_SIZE,SEM_EXPIRY_FLAG\n"
                "NSE,I,13,INDEX,0,NIFTY,1,Nifty 50,,0,XX,0.05,NA\n"
                "NSE,D,55555,OPTIDX,0,NIFTY-Nov2026-24000-CE,65,NIFTY 18 NOV 24000 CALL,2026-11-18 14:30:00,24000,CE,5,W\n"
                "NSE,D,55556,FUTIDX,0,NIFTY-Nov2026-FUT,65,NIFTY NOV FUT,2026-11-24 14:30:00,0,XX,10,M\n"
                "NSE,D,77777,OPTIDX,0,NIFTYNXT50-Nov2026-70000-CE,25,x,2026-11-24 14:30:00,70000,CE,5,M\n"
                "BSE,D,1,OPTIDX,0,SENSEX-Nov2026-80000-CE,20,x,2026-11-20 14:30:00,80000,CE,5,W\n")
    rows, meta = parse_instrument_master(csv_text.encode())
    syms = {r["trading_symbol"]: r for r in rows}
    assert set(syms) == {"NIFTY", "NIFTY-Nov2026-24000-CE", "NIFTY-Nov2026-FUT"}
    o = syms["NIFTY-Nov2026-24000-CE"]
    assert o["lot_size"] == 65 and o["expiry"] == date(2026, 11, 18) and o["option_type"] == "CE" and o["strike"] == 24000
    assert syms["NIFTY"]["exchange_segment"] == "IDX_I" and len(meta["sha256"]) == 64
    with pytest.raises(ParseError):
        parse_instrument_master(b"A,B\n1,2\n")


# ---------------- client ----------------
def make_client(handler):
    return DhanClient("1100000001", "SECRET-TOKEN-VALUE-XYZ", transport=httpx.MockTransport(handler))


def test_client_headers_and_no_token_in_repr():
    seen = {}

    def h(req):
        seen.update(req.headers)
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"data": ["2026-11-18"]})
    c = make_client(h)
    c.expiry_list(13, "IDX_I")
    assert seen["access-token"] == "SECRET-TOKEN-VALUE-XYZ" and seen["client-id"] == "1100000001"
    assert seen["path"] == "/v2/optionchain/expirylist" and seen["body"] == {"UnderlyingScrip": 13, "UnderlyingSeg": "IDX_I"}
    assert "SECRET" not in repr(c)


def test_client_auth_error_stops_requests():
    calls = {"n": 0}

    def h(req):
        calls["n"] += 1
        return httpx.Response(401, json={"errorType": "Invalid_Authentication", "errorCode": "DH-901", "errorMessage": "x"})
    c = make_client(h)
    with pytest.raises(DhanError) as e:
        c.expiry_list(13, "IDX_I")
    assert e.value.kind == "AUTH" and c.token_invalid
    with pytest.raises(DhanError):
        c.expiry_list(13, "IDX_I")
    assert calls["n"] == 1                      # no further network calls with a bad token


def test_client_rate_limit_backs_off():
    calls = {"n": 0}

    def h(req):
        calls["n"] += 1
        return httpx.Response(429, json={"errorCode": "DH-904", "errorMessage": "Too many requests"})
    c = make_client(h)
    with pytest.raises(DhanError):
        c.ltp({"IDX_I": [13]})
    with pytest.raises(DhanError) as e:
        c.ltp({"IDX_I": [13]})
    assert e.value.kind == "RATE_LIMIT" and calls["n"] == 1


def test_client_invalid_json():
    c = make_client(lambda req: httpx.Response(200, content=b"<html>"))
    with pytest.raises(DhanError) as e:
        c.profile()
    assert e.value.kind == "INVALID_RESPONSE"


# ---------------- live safety ----------------
def test_live_broker_cannot_be_constructed_in_tests():
    c = make_client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(LiveTradingNotPermitted):
        DhanLiveBroker(c, "tok", "tok")


def test_correlation_id_deterministic_and_short():
    a = correlation_id("T20261113-NIFTY-abc-ENTRY")
    assert a == correlation_id("T20261113-NIFTY-abc-ENTRY") and len(a) == 20
    assert a != correlation_id("T20261113-NIFTY-abc-EXIT-1")


def test_token_expiry_decode_and_mask():
    exp = int((datetime.now(timezone.utc) + timedelta(hours=5)).timestamp())
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    tok = f"xx.{payload}.yy"
    assert secrets.token_expiry(tok).timestamp() == exp
    assert secrets.token_expiry("not-a-jwt") is None
    assert secrets.mask("1100000001") == "******0001"
