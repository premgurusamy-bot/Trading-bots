"""Unit tests on SYNTHETIC fixtures."""
from datetime import date, timedelta

import pytest

from app.strategy import indicators as ind
from tests.synthetic import c, ts, path_candles


def test_sma_and_volume_sma():
    assert ind.sma([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]
    # missing values reset the window (never fabricated)
    assert ind.sma([1, 2, None, 4, 5, 6], 2) == [None, 1.5, None, None, 4.5, 5.5]


def test_ema_seed_and_recursion():
    v = [1.0, 2, 3, 4, 5, 6]
    e = ind.ema(v, 3)
    assert e[:2] == [None, None]
    assert e[2] == pytest.approx(2.0)
    assert e[3] == pytest.approx(0.5 * 4 + 0.5 * 2.0)
    assert e[5] == pytest.approx(0.5 * 6 + 0.5 * (0.5 * 5 + 0.5 * 3.0))


def test_ema_no_lookahead():
    v = [float(x % 7) for x in range(50)]
    full = ind.ema(v, 9)
    for n in range(10, 50):
        assert ind.ema(v[:n], 9)[-1] == pytest.approx(full[n - 1])


def test_atr_wilder():
    d = date(2026, 1, 5)
    cs = [c(ts(d, 9, 15) + timedelta(minutes=5 * k), 100, 102, 98, 100) for k in range(20)]
    a = ind.atr(cs, 14)
    assert a[12] is None and a[13] == pytest.approx(4.0) and a[-1] == pytest.approx(4.0)


def test_rsi_extremes():
    up = [float(x) for x in range(30)]
    assert ind.rsi(up, 14)[-1] == pytest.approx(100.0)
    down = [float(30 - x) for x in range(30)]
    assert ind.rsi(down, 14)[-1] == pytest.approx(0.0)


def test_adx_trending_vs_flat():
    d = date(2026, 1, 5)
    trend = path_candles(d, [100 + 3 * k for k in range(60)], wick=1)
    a, pdi, mdi = ind.adx(trend, 14)
    assert a[-1] > 40 and pdi[-1] > mdi[-1]
    flat = path_candles(d, [100 + (0, 4, 0, -4)[k % 4] for k in range(60)], wick=1)
    af, _, _ = ind.adx(flat, 14)
    assert af[-1] < 20


def test_session_vwap_resets_and_external_volume():
    d1, d2 = date(2026, 1, 5), date(2026, 1, 6)
    cs = [c(ts(d1, 9, 15), 100, 110, 90, 100, v=10), c(ts(d1, 9, 20), 115, 130, 110, 120, v=30),
          c(ts(d2, 9, 15), 200, 210, 190, 200, v=5)]
    v = ind.session_vwap(cs)
    assert v[0] == pytest.approx(100)
    assert v[1] == pytest.approx((100 * 10 + 120 * 30) / 40)
    assert v[2] == pytest.approx(200)  # reset on new date
    v2 = ind.session_vwap(cs, volumes=[0, 0, 0])
    assert v2 == [None, None, None]     # no volume => None, never fabricated


def test_crosses_and_slope():
    a = [1, 2, 3, 2, 1, 2, 3]
    b = [2] * 7
    assert ind.crosses(a, b, 0, 6) == 3
    assert ind.slope([1.0, 2.0, 3.0, 4.0], 3, 3) == pytest.approx(1.0)
    assert ind.slope([None, 2.0, 3.0], 2, 2) is None
