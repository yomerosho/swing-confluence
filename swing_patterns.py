"""
swing_patterns.py — Technical Pattern Detection for SwingConfluence
====================================================================
Detects 10 high-conviction swing patterns on Daily and 4H timeframes.
Designed for 1-3 day swing trades, not intraday scalps.

Patterns detected:
  1. 9-EMA bounce + confirmation candle
  2. 21-EMA bounce + engulfing
  3. 50-EMA bounce + hammer/pinbar
  4. 100-SMA bounce + rejection
  5. 200-SMA bounce + reversal
  6. Fair Value Gap (FVG) fill + rejection
  7. Break of Structure (BOS) retest
  8. Daily Order Block (OB) tap
  9. Double bottom/top + confirmation
 10. Inside bar breakout

All patterns return (direction, strength, reason) tuples.
direction: "CALL" or "PUT" (bullish or bearish)
strength:  1-5 stars (pattern quality)
reason:    Plain-English description
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class PatternSignal:
    """One detected pattern on a specific timeframe."""
    ticker:     str
    timeframe:  str           # "1D" or "4H"
    pattern:    str           # Pattern name
    direction:  str           # "CALL" or "PUT"
    strength:   int           # 1-5 stars
    reason:     str           # Human-readable description
    price:      float         # Current price at signal
    level:      float         # Key level (EMA, SMA, FVG, etc.)
    candle_date: str          # Date of the signal candle


# ── Technical Indicators ──────────────────────────────────────────────────────

class Indicators:
    @staticmethod
    def sma(series: pd.Series, n: int) -> pd.Series:
        return series.rolling(n).mean()

    @staticmethod
    def ema(series: pd.Series, n: int) -> pd.Series:
        return series.ewm(span=n, adjust=False).mean()

    @staticmethod
    def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low  - close.shift(1)).abs()
        ], axis=1).max(axis=1)
        return tr.rolling(n).mean()


# ── Pattern Detection Engine ──────────────────────────────────────────────────

class PatternDetector:
    """
    Detects all 10 patterns on a given OHLC dataframe.
    Returns a list of PatternSignal objects.
    """

    def __init__(self, ticker: str, timeframe: str):
        self.ticker    = ticker
        self.timeframe = timeframe
        self.ind       = Indicators()

    def detect_all(self, df: pd.DataFrame) -> List[PatternSignal]:
        """Run all pattern detectors and return signals found."""
        if df.empty or len(df) < 200:
            return []

        df = self._enrich(df)
        signals = []

        # Run each detector
        for detector in [
            self._detect_9ema_bounce,
            self._detect_21ema_bounce,
            self._detect_50ema_bounce,
            self._detect_100sma_bounce,
            self._detect_200sma_bounce,
            self._detect_fvg_fill,
            self._detect_bos_retest,
            self._detect_order_block,
            self._detect_double_top_bottom,
            self._detect_inside_bar_breakout,
        ]:
            try:
                sig = detector(df)
                if sig:
                    signals.append(sig)
            except Exception as e:
                logger.debug(f"{self.ticker} {self.timeframe} {detector.__name__}: {e}")

        return signals

    # -- Enrichment --------------------------------------------------------

    def _enrich(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        c, h, l, o, v = df["Close"], df["High"], df["Low"], df["Open"], df["Volume"]

        df["ema9"]    = self.ind.ema(c, 9)
        df["ema21"]   = self.ind.ema(c, 21)
        df["ema50"]   = self.ind.ema(c, 50)
        df["sma100"]  = self.ind.sma(c, 100)
        df["sma200"]  = self.ind.sma(c, 200)
        df["atr"]     = self.ind.atr(h, l, c, 14)
        df["vol_ma"]  = v.rolling(20).mean()
        df["vol_r"]   = v / df["vol_ma"].replace(0, np.nan)

        # Candle anatomy
        df["body"]    = (c - o).abs()
        df["range"]   = h - l
        df["hi_wick"] = h - pd.concat([o, c], axis=1).max(axis=1)
        df["lo_wick"] = pd.concat([o, c], axis=1).min(axis=1) - l
        df["bullish"] = c > o
        df["bearish"] = c < o
        df["cl_pct"]  = (c - l) / df["range"].replace(0, np.nan)

        # Engulfing detection
        df["bull_engulf"] = (
            (df["bullish"]) &
            (df["bearish"].shift(1)) &
            (c > o.shift(1)) &
            (o < c.shift(1))
        )
        df["bear_engulf"] = (
            (df["bearish"]) &
            (df["bullish"].shift(1)) &
            (c < o.shift(1)) &
            (o > c.shift(1))
        )

        # Hammer (long lower wick, small body, close in upper third)
        df["hammer"] = (
            (df["lo_wick"] >= 2 * df["body"]) &
            (df["body"] > 0) &
            (df["cl_pct"] >= 0.6)
        )
        # Shooting star (long upper wick, small body, close in lower third)
        df["shooting_star"] = (
            (df["hi_wick"] >= 2 * df["body"]) &
            (df["body"] > 0) &
            (df["cl_pct"] <= 0.4)
        )

        # Inside bar
        df["inside_bar"] = (h < h.shift(1)) & (l > l.shift(1))

        return df

    # -- Pattern Detectors -------------------------------------------------

    def _detect_9ema_bounce(self, df: pd.DataFrame) -> Optional[PatternSignal]:
        """9-EMA retrace + bullish/bearish confirmation candle (your QCOM signal)."""
        last = df.iloc[-1]
        prev = df.iloc[-2]
        c    = float(last["Close"])
        ema9 = float(last["ema9"])

        if np.isnan(ema9): return None

        buffer = ema9 * 0.005  # 0.5% buffer

        # Bullish: prior candle touched 9-EMA, current closes above prior body
        if (prev["Low"] <= ema9 + buffer and prev["Low"] >= ema9 - buffer and
            last["Close"] > prev["Close"] and last["Close"] > ema9 and
            last["bullish"]):
            return PatternSignal(
                ticker=self.ticker, timeframe=self.timeframe,
                pattern="9-EMA Bounce",
                direction="CALL", strength=4,
                reason=f"Retrace to 9-EMA ${ema9:.2f}, bullish confirmation close",
                price=c, level=ema9, candle_date=str(df.index[-1])[:10],
            )

        # Bearish: prior candle touched 9-EMA from above, current closes below prior
        if (prev["High"] >= ema9 - buffer and prev["High"] <= ema9 + buffer and
            last["Close"] < prev["Close"] and last["Close"] < ema9 and
            last["bearish"]):
            return PatternSignal(
                ticker=self.ticker, timeframe=self.timeframe,
                pattern="9-EMA Rejection",
                direction="PUT", strength=4,
                reason=f"Rejection at 9-EMA ${ema9:.2f}, bearish confirmation close",
                price=c, level=ema9, candle_date=str(df.index[-1])[:10],
            )
        return None

    def _detect_21ema_bounce(self, df: pd.DataFrame) -> Optional[PatternSignal]:
        """21-EMA bounce with engulfing pattern."""
        last = df.iloc[-1]
        c    = float(last["Close"])
        ema21= float(last["ema21"])

        if np.isnan(ema21): return None
        if abs(last["Low"] - ema21) / ema21 > 0.01: return None

        if last["bull_engulf"] and c > ema21:
            return PatternSignal(
                ticker=self.ticker, timeframe=self.timeframe,
                pattern="21-EMA + Bull Engulf",
                direction="CALL", strength=4,
                reason=f"Bullish engulfing at 21-EMA ${ema21:.2f}",
                price=c, level=ema21, candle_date=str(df.index[-1])[:10],
            )
        if last["bear_engulf"] and c < ema21:
            return PatternSignal(
                ticker=self.ticker, timeframe=self.timeframe,
                pattern="21-EMA + Bear Engulf",
                direction="PUT", strength=4,
                reason=f"Bearish engulfing at 21-EMA ${ema21:.2f}",
                price=c, level=ema21, candle_date=str(df.index[-1])[:10],
            )
        return None

    def _detect_50ema_bounce(self, df: pd.DataFrame) -> Optional[PatternSignal]:
        """50-EMA test with hammer/shooting star."""
        last = df.iloc[-1]
        c    = float(last["Close"])
        ema50= float(last["ema50"])

        if np.isnan(ema50): return None

        # Within 1% of 50-EMA
        if abs(last["Low"] - ema50) / ema50 <= 0.01 and last["hammer"] and c > ema50:
            vol_boost = float(last["vol_r"]) if not np.isnan(last["vol_r"]) else 1.0
            strength  = 5 if vol_boost > 1.5 else 4
            return PatternSignal(
                ticker=self.ticker, timeframe=self.timeframe,
                pattern="50-EMA Hammer",
                direction="CALL", strength=strength,
                reason=f"Hammer at 50-EMA ${ema50:.2f} (vol {vol_boost:.1f}x)",
                price=c, level=ema50, candle_date=str(df.index[-1])[:10],
            )

        if abs(last["High"] - ema50) / ema50 <= 0.01 and last["shooting_star"] and c < ema50:
            vol_boost = float(last["vol_r"]) if not np.isnan(last["vol_r"]) else 1.0
            strength  = 5 if vol_boost > 1.5 else 4
            return PatternSignal(
                ticker=self.ticker, timeframe=self.timeframe,
                pattern="50-EMA Shooting Star",
                direction="PUT", strength=strength,
                reason=f"Shooting star at 50-EMA ${ema50:.2f} (vol {vol_boost:.1f}x)",
                price=c, level=ema50, candle_date=str(df.index[-1])[:10],
            )
        return None

    def _detect_100sma_bounce(self, df: pd.DataFrame) -> Optional[PatternSignal]:
        """100-SMA test with rejection wick and confirmation."""
        last = df.iloc[-1]
        c    = float(last["Close"])
        sma  = float(last["sma100"])

        if np.isnan(sma): return None

        # Bullish: low touches 100-SMA, strong lower wick, closes above
        if (abs(last["Low"] - sma) / sma <= 0.012 and
            last["lo_wick"] >= 1.5 * last["body"] and
            c > sma and last["bullish"]):
            return PatternSignal(
                ticker=self.ticker, timeframe=self.timeframe,
                pattern="100-SMA Bounce",
                direction="CALL", strength=5,
                reason=f"Strong rejection wick at 100-SMA ${sma:.2f}",
                price=c, level=sma, candle_date=str(df.index[-1])[:10],
            )

        # Bearish: high touches 100-SMA, strong upper wick, closes below
        if (abs(last["High"] - sma) / sma <= 0.012 and
            last["hi_wick"] >= 1.5 * last["body"] and
            c < sma and last["bearish"]):
            return PatternSignal(
                ticker=self.ticker, timeframe=self.timeframe,
                pattern="100-SMA Rejection",
                direction="PUT", strength=5,
                reason=f"Strong rejection wick at 100-SMA ${sma:.2f}",
                price=c, level=sma, candle_date=str(df.index[-1])[:10],
            )
        return None

    def _detect_200sma_bounce(self, df: pd.DataFrame) -> Optional[PatternSignal]:
        """200-SMA test — the most powerful trend level."""
        last = df.iloc[-1]
        c    = float(last["Close"])
        sma  = float(last["sma200"])

        if np.isnan(sma): return None

        if (abs(last["Low"] - sma) / sma <= 0.015 and
            last["lo_wick"] >= 1.5 * last["body"] and
            c > sma):
            return PatternSignal(
                ticker=self.ticker, timeframe=self.timeframe,
                pattern="200-SMA Reversal",
                direction="CALL", strength=5,
                reason=f"Major reversal at 200-SMA ${sma:.2f} (trend support)",
                price=c, level=sma, candle_date=str(df.index[-1])[:10],
            )

        if (abs(last["High"] - sma) / sma <= 0.015 and
            last["hi_wick"] >= 1.5 * last["body"] and
            c < sma):
            return PatternSignal(
                ticker=self.ticker, timeframe=self.timeframe,
                pattern="200-SMA Breakdown",
                direction="PUT", strength=5,
                reason=f"Major breakdown at 200-SMA ${sma:.2f} (trend resistance)",
                price=c, level=sma, candle_date=str(df.index[-1])[:10],
            )
        return None

    def _detect_fvg_fill(self, df: pd.DataFrame) -> Optional[PatternSignal]:
        """
        Fair Value Gap (FVG): 3-candle pattern.
        Bullish FVG: candle[i-2].high < candle[i].low (gap between candle 1 and 3)
        Detects when current price fills the FVG and rejects.
        """
        if len(df) < 10: return None

        # Look for FVGs in last 5 candles
        for lookback in range(3, 8):
            if len(df) <= lookback: continue

            c1 = df.iloc[-lookback - 2]
            c3 = df.iloc[-lookback]

            # Bullish FVG: gap up
            if c1["High"] < c3["Low"]:
                fvg_top    = c3["Low"]
                fvg_bottom = c1["High"]
                last = df.iloc[-1]
                # Price filled the gap and rejected
                if (last["Low"] <= fvg_top and last["Low"] >= fvg_bottom and
                    last["Close"] > fvg_top and last["bullish"]):
                    return PatternSignal(
                        ticker=self.ticker, timeframe=self.timeframe,
                        pattern="Bullish FVG Fill",
                        direction="CALL", strength=4,
                        reason=f"Filled bull FVG ${fvg_bottom:.2f}-${fvg_top:.2f}, rejected up",
                        price=float(last["Close"]), level=float(fvg_top),
                        candle_date=str(df.index[-1])[:10],
                    )

            # Bearish FVG: gap down
            if c1["Low"] > c3["High"]:
                fvg_top    = c1["Low"]
                fvg_bottom = c3["High"]
                last = df.iloc[-1]
                if (last["High"] <= fvg_top and last["High"] >= fvg_bottom and
                    last["Close"] < fvg_bottom and last["bearish"]):
                    return PatternSignal(
                        ticker=self.ticker, timeframe=self.timeframe,
                        pattern="Bearish FVG Fill",
                        direction="PUT", strength=4,
                        reason=f"Filled bear FVG ${fvg_bottom:.2f}-${fvg_top:.2f}, rejected down",
                        price=float(last["Close"]), level=float(fvg_bottom),
                        candle_date=str(df.index[-1])[:10],
                    )
        return None

    def _detect_bos_retest(self, df: pd.DataFrame) -> Optional[PatternSignal]:
        """
        Break of Structure (BOS) + retest.
        Look for: recent break of a swing high/low, then return to that level.
        """
        if len(df) < 30: return None

        last  = df.iloc[-1]
        c     = float(last["Close"])

        # Find recent swing high/low (highest high / lowest low in last 20 bars before recent 5)
        prior_window = df.iloc[-25:-5]
        if prior_window.empty: return None

        swing_high = prior_window["High"].max()
        swing_low  = prior_window["Low"].min()

        recent = df.iloc[-5:]

        # Bullish BOS: broke swing_high, now retesting from above
        if recent["High"].max() > swing_high and abs(c - swing_high) / swing_high <= 0.015 and c > swing_high and last["bullish"]:
            return PatternSignal(
                ticker=self.ticker, timeframe=self.timeframe,
                pattern="Bullish BOS Retest",
                direction="CALL", strength=5,
                reason=f"Broke swing high ${swing_high:.2f}, retesting + holding",
                price=c, level=float(swing_high), candle_date=str(df.index[-1])[:10],
            )

        # Bearish BOS: broke swing_low, now retesting from below
        if recent["Low"].min() < swing_low and abs(c - swing_low) / swing_low <= 0.015 and c < swing_low and last["bearish"]:
            return PatternSignal(
                ticker=self.ticker, timeframe=self.timeframe,
                pattern="Bearish BOS Retest",
                direction="PUT", strength=5,
                reason=f"Broke swing low ${swing_low:.2f}, retesting + rejecting",
                price=c, level=float(swing_low), candle_date=str(df.index[-1])[:10],
            )
        return None

    def _detect_order_block(self, df: pd.DataFrame) -> Optional[PatternSignal]:
        """
        Order Block: the last opposite-color candle before a strong move.
        Bullish OB: last bearish candle before a strong rally — buying zone.
        Bearish OB: last bullish candle before a strong selloff — selling zone.
        """
        if len(df) < 15: return None

        last = df.iloc[-1]
        c    = float(last["Close"])

        # Look back 5-10 candles for a strong move
        for i in range(5, 12):
            if len(df) <= i: continue

            move_candle = df.iloc[-i]
            ob_candle   = df.iloc[-i - 1]

            move_size = abs(move_candle["Close"] - move_candle["Open"])
            if move_size < move_candle["atr"] * 1.5: continue

            # Bullish OB
            if (move_candle["bullish"] and ob_candle["bearish"]):
                ob_top    = ob_candle["High"]
                ob_bottom = ob_candle["Low"]
                # Price taps the OB from above and holds
                if (last["Low"] <= ob_top and last["Low"] >= ob_bottom and
                    c > ob_top and last["bullish"]):
                    return PatternSignal(
                        ticker=self.ticker, timeframe=self.timeframe,
                        pattern="Bullish Order Block",
                        direction="CALL", strength=4,
                        reason=f"Tap bull OB ${ob_bottom:.2f}-${ob_top:.2f}, holding",
                        price=c, level=float(ob_top), candle_date=str(df.index[-1])[:10],
                    )

            # Bearish OB
            if (move_candle["bearish"] and ob_candle["bullish"]):
                ob_top    = ob_candle["High"]
                ob_bottom = ob_candle["Low"]
                if (last["High"] >= ob_bottom and last["High"] <= ob_top and
                    c < ob_bottom and last["bearish"]):
                    return PatternSignal(
                        ticker=self.ticker, timeframe=self.timeframe,
                        pattern="Bearish Order Block",
                        direction="PUT", strength=4,
                        reason=f"Tap bear OB ${ob_bottom:.2f}-${ob_top:.2f}, rejecting",
                        price=c, level=float(ob_bottom), candle_date=str(df.index[-1])[:10],
                    )
        return None

    def _detect_double_top_bottom(self, df: pd.DataFrame) -> Optional[PatternSignal]:
        """Double bottom / top within last 20 bars."""
        if len(df) < 25: return None

        recent = df.iloc[-20:]
        last   = df.iloc[-1]
        c      = float(last["Close"])

        # Find two lowest lows (double bottom) or two highest highs (double top)
        lows  = recent["Low"].nsmallest(2)
        highs = recent["High"].nlargest(2)

        # Double bottom: two lows within 1%, current candle is bullish breakout
        if len(lows) >= 2:
            low_diff = abs(lows.iloc[0] - lows.iloc[1]) / lows.iloc[0]
            if low_diff < 0.01:
                # Both lows are similar
                # Check if current price is breaking out
                neckline = recent["High"].iloc[recent["Low"].idxmin() if False else 0:].mean()
                if c > neckline and last["bullish"]:
                    return PatternSignal(
                        ticker=self.ticker, timeframe=self.timeframe,
                        pattern="Double Bottom",
                        direction="CALL", strength=4,
                        reason=f"Double bottom ~${lows.iloc[0]:.2f}, breakout confirmation",
                        price=c, level=float(lows.iloc[0]),
                        candle_date=str(df.index[-1])[:10],
                    )

        # Double top
        if len(highs) >= 2:
            high_diff = abs(highs.iloc[0] - highs.iloc[1]) / highs.iloc[0]
            if high_diff < 0.01:
                neckline = recent["Low"].mean()
                if c < neckline and last["bearish"]:
                    return PatternSignal(
                        ticker=self.ticker, timeframe=self.timeframe,
                        pattern="Double Top",
                        direction="PUT", strength=4,
                        reason=f"Double top ~${highs.iloc[0]:.2f}, breakdown confirmation",
                        price=c, level=float(highs.iloc[0]),
                        candle_date=str(df.index[-1])[:10],
                    )
        return None

    def _detect_inside_bar_breakout(self, df: pd.DataFrame) -> Optional[PatternSignal]:
        """
        Inside bar breakout: inside bar following a large momentum candle,
        then a breakout in the direction of the prior trend.
        """
        if len(df) < 5: return None

        last = df.iloc[-1]
        prev = df.iloc[-2]
        prev2= df.iloc[-3]

        # Previous candle must be inside bar
        if not bool(prev["inside_bar"]): return None

        # Two bars ago must be a strong momentum candle
        if prev2["body"] < prev2["atr"] * 1.0: return None

        c = float(last["Close"])

        # Bullish: prior momentum up, current breaks inside bar high
        if prev2["bullish"] and c > prev["High"] and last["bullish"]:
            return PatternSignal(
                ticker=self.ticker, timeframe=self.timeframe,
                pattern="Inside Bar Breakout (Bull)",
                direction="CALL", strength=4,
                reason=f"Inside bar breakout above ${prev['High']:.2f}",
                price=c, level=float(prev["High"]), candle_date=str(df.index[-1])[:10],
            )

        # Bearish: prior momentum down, current breaks inside bar low
        if prev2["bearish"] and c < prev["Low"] and last["bearish"]:
            return PatternSignal(
                ticker=self.ticker, timeframe=self.timeframe,
                pattern="Inside Bar Breakdown (Bear)",
                direction="PUT", strength=4,
                reason=f"Inside bar breakdown below ${prev['Low']:.2f}",
                price=c, level=float(prev["Low"]), candle_date=str(df.index[-1])[:10],
            )
        return None
