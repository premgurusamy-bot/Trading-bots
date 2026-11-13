# Candle and price-action pattern definitions

Code: `backend/app/strategy/patterns.py`. Only completed candles are used. Thresholds are fractions of the candle's range
unless stated. A pattern never generates a trade on its own.

| Pattern | Definition |
|---|---|
| Doji | body ≤ 10% of range |
| Hammer | lower wick ≥ 2 × max(body, 5% range); upper wick ≤ 25% range; close location ≥ 0.60 |
| Shooting star | mirror of hammer (close location ≤ 0.40) |
| Bullish / bearish pin bar | rejecting wick ≥ 66% of range and body ≤ 30% of range |
| Bullish engulfing | previous candle bearish, current bullish, current open ≤ previous close, current close ≥ previous open, current body > previous body |
| Bearish engulfing | mirror |
| Morning star | c1 bearish with body ≥ 50% of its range; c2 body ≤ 30% of c1 body and c2 top ≤ c1 close + ½ c1 body; c3 bullish and closes above c1's body midpoint |
| Evening star | mirror |
| Three white soldiers | 3 bullish candles, body ≥ 50% of range, each opening inside the prior body, each closing higher, upper wick ≤ 30% |
| Three black crows | mirror |
| Inside-bar breakout/breakdown | c2 inside c1 (high ≤, low ≥); c3 closes above c2's high (bull) / below c2's low (bear) |
| Outside bar | high > previous high and low < previous low |
| Bull flag | pole: net move ≥ 2 ATR within ≤ 4 bars; flag: 2–6 bars, retracing ≤ 50% of the pole and staying below the pole top; breakout: close above the flag high |
| Bear flag | mirror |
| Double bottom / top | last two confirmed swing lows (highs) within 0.25 ATR, with a confirmed swing high (low) between them as neckline; confirmed on the first close through the neckline |
| Ascending triangle | last two confirmed swing highs within 0.25 ATR (flat top) and rising swing lows |
| Descending triangle | flat bottom and falling swing highs |
| Head and shoulders | last three confirmed swing highs with the middle one highest and shoulders within 0.5 ATR of each other; confirmed on a close below the neckline (lowest swing low between the shoulders) |
| Inverse H&S | mirror |

Rejection patterns (score #9 and mandatory): hammer, bullish pin bar, bullish engulfing, morning star, and their bearish mirrors.
Formations (score #12): three soldiers/crows, flags, inside-bar breaks, double bottom/top, triangles, H&S.
These are two separate lists, so the same evidence is never counted twice.
