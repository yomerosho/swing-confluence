"""
swing_patterns.py — Technical Pattern Detection for SwingConfluence
====================================================================
Detects 10 high-conviction swing patterns on Daily and 4H timeframes.
Also detects Rob Smith's "The Strat" candle sequences, combos, and
3-TF Full Time Frame Continuity (1H / 4H / Daily).

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

Strat detections:
  - Candle type: 1 (inside), 2U (up), 2D (down), 3 (outside)
  - Combos: 2-1-2, 1-2-2 (Rev Strat), 3-2-2, 3-1-2, 2-2 Continuation
  - Failed 2: F2U (bearish trap), F2D (bullish trap)
  - PMG: Pivot Machine Gun (6+ consecutive 2-bars)
  - 3-TF FTFC: 1H + 4H + Daily bias continuity

All patterns return PatternSignal objects.
StratResult is a separate dataclass returned per-timeframe.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class PatternSignal:
    """One detected ICT/SMC pattern on a specific timeframe."""
    ticker:      str
    timeframe:   str        # "1D" or "4H"
    pattern:     str
    direction:   str        # "CALL" or "PUT"
    strength:    int        # 1-5 stars
    reason:      str
    price:       float
    level:       float
    candle_date: str


@dataclass
class StratResult:
    """
    Strat analysis result for a single timeframe's bar series.
    Populated by StratDetector.analyze().
    """
    timeframe:    str           # "1D", "4H", "1H"

    # Candle types for last 3 bars (index 0 = most recent)
    bar_types:    List[str] = field(default_factory=list)   # e.g. ["2U", "1", "2D"]

    # Active combo
    combo:        str  = ""     # e.g. "2-1-2", "1-2-2", "3-2-2", "3-1-2", "2-2", ""
    combo_dir:    str  = ""     # "CALL", "PUT", or ""

    # Failed 2
    is_f2u:       bool = False  # bearish trap (prev 2U failed)
    is_f2d:       bool = False  # bullish trap (prev 2D failed)

    # PMG
    is_pmg:       bool = False
    pmg_count:    int  = 0      # consecutive 2-bars detected

    # TF bias: close > open on the most recent confirmed bar
    bias:         str  = ""     # "BULL", "BEAR", or "NEUTRAL"

    # Human-readable summary
    summary:      str  = ""


@dataclass
class StratFTFC:
    """
    3-TF Full Time Frame Continuity result.
    True FTFC = all three TFs have the same directional bias.
    """
    bias_1h:      str   = ""    # "BULL", "BEAR", "NEUTRAL"
    bias_4h:      str   = ""
    bias_1d:      str   = ""
    ftfc:         bool  = False
    ftfc_dir:     str   = ""    # "BULL" or "BEAR" when ftfc=True
    score:        int   = 0     # 0-3 — how many TFs agree
    summary:      str   = ""


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


# ── Strat Detector ────────────────────────────────────────────────────────────

class StratDetector:
    """
    Ports Rob Smith's "The Strat" candle relationship logic to pandas.

    Candle type definitions (mirrors Pine Script):
      1  = Inside bar  : high < prev_high AND low > prev_low
      2U = Up bar      : high > prev_high AND low >= prev_low
      2D = Down bar    : low < prev_low   AND high <= prev_high
      3  = Outside bar : high > prev_high AND low < prev_low

    Combos detected on the last 3 completed bars (t2 → t1 → t0):
      2-1-2  Bull: 2D → 1 → 2U
      2-1-2  Bear: 2U → 1 → 2D
      1-2-2  Bull (Rev Strat): 1 → 2D → 2U  (close > open)
      1-2-2  Bear (Rev Strat): 1 → 2U → 2D  (close < open)
      3-2-2  Bull: 3 → 2D → 2U  (close > open)
      3-2-2  Bear: 3 → 2U → 2D  (close < open)
      3-1-2  Bull: 3 → 1 → 2U
      3-1-2  Bear: 3 → 1 → 2D
      2-2    Bull: 2U → 2U
      2-2    Bear: 2D → 2D

    Failed 2:
      F2U: prev bar was 2U (broke prev_high), current close < high[2]  → bearish trap
      F2D: prev bar was 2D (broke prev_low),  current close > low[2]   → bullish trap

    PMG (Pivot Machine Gun):
      6+ consecutive bars that are all 2U or 2D (any directional 2-bar)
    """

    PMG_MIN = 6  # consecutive 2-bars to qualify as PMG

    @staticmethod
    def _bar_type(df: pd.DataFrame, i: int) -> str:
        """
        Return candle type for bar at position i (negative index from end).
        i=0 → most recent bar, i=-1 → one before that, etc.
        Uses .iloc so i should be negative (e.g. -1 = last, -2 = second-to-last).
        """
        if abs(i) >= len(df):
            return ""
        row  = df.iloc[i]
        prev = df.iloc[i - 1]
        h, l   = float(row["High"]),  float(row["Low"])
        ph, pl = float(prev["High"]), float(prev["Low"])

        if h < ph and l > pl:
            return "1"
        elif h > ph and l >= pl:
            return "2U"
        elif l < pl and h <= ph:
            return "2D"
        elif h > ph and l < pl:
            return "3"
        return "1"  # flat / equal treated as inside

    @classmethod
    def analyze(cls, df: pd.DataFrame, timeframe: str) -> StratResult:
        """
        Run full Strat analysis on a bar DataFrame.
        Requires at least 10 bars. Returns empty StratResult if insufficient.
        """
        result = StratResult(timeframe=timeframe)

        if df is None or len(df) < 10:
            return result

        try:
            # Last 3 bar types (t0=most recent, t1=prev, t2=two bars ago)
            t0 = cls._bar_type(df, -1)
            t1 = cls._bar_type(df, -2)
            t2 = cls._bar_type(df, -3)
            result.bar_types = [t0, t1, t2]

            last  = df.iloc[-1]
            prev  = df.iloc[-2]
            prev2 = df.iloc[-3]

            c0, o0 = float(last["Close"]),  float(last["Open"])
            c1, o1 = float(prev["Close"]),  float(prev["Open"])

            # ── Failed 2 ──────────────────────────────────────────────
            # F2U: prev bar was 2U, current close falls back below prev2's high
            # F2D: prev bar was 2D, current close recovers above prev2's low
            prev_high2 = float(prev2["High"])
            prev_low2  = float(prev2["Low"])

            result.is_f2u = (t1 == "2U") and (c0 < prev_high2)
            result.is_f2d = (t1 == "2D") and (c0 > prev_low2)

            # ── Combo detection ───────────────────────────────────────
            combo, combo_dir = cls._detect_combo(t0, t1, t2, c0, o0)
            result.combo     = combo
            result.combo_dir = combo_dir

            # ── PMG ───────────────────────────────────────────────────
            pmg_count = 0
            for k in range(1, min(cls.PMG_MIN + 4, len(df))):
                bt = cls._bar_type(df, -(k))
                if bt in ("2U", "2D"):
                    pmg_count += 1
                else:
                    break
            result.pmg_count = pmg_count
            result.is_pmg    = pmg_count >= cls.PMG_MIN

            # ── TF Bias ───────────────────────────────────────────────
            if c0 > o0:
                result.bias = "BULL"
            elif c0 < o0:
                result.bias = "BEAR"
            else:
                result.bias = "NEUTRAL"

            # ── Summary string ────────────────────────────────────────
            parts = []
            if result.is_f2u:
                parts.append("F2U 🪤 (bear trap)")
            if result.is_f2d:
                parts.append("F2D 🪤 (bull trap)")
            if combo:
                arrow = "▲" if combo_dir == "CALL" else "▼"
                parts.append(f"{combo} {arrow}")
            if result.is_pmg:
                parts.append(f"⚡ PMG ({pmg_count} bars)")
            if not parts:
                seq = f"{t2}→{t1}→{t0}"
                parts.append(f"Seq: {seq} · bias {result.bias}")

            result.summary = " · ".join(parts)

        except Exception as e:
            logger.debug(f"StratDetector.analyze [{timeframe}]: {e}")

        return result

    @staticmethod
    def _detect_combo(t0: str, t1: str, t2: str,
                      close0: float, open0: float) -> Tuple[str, str]:
        """
        Returns (combo_name, direction) or ("", "").
        direction: "CALL" (bullish) or "PUT" (bearish)
        """
        bull = close0 > open0
        bear = close0 < open0

        # 2-1-2
        if t2 == "2D" and t1 == "1" and t0 == "2U":
            return "2-1-2", "CALL"
        if t2 == "2U" and t1 == "1" and t0 == "2D":
            return "2-1-2", "PUT"

        # 1-2-2 Rev Strat
        if t2 == "1" and t1 == "2D" and t0 == "2U" and bull:
            return "1-2-2", "CALL"
        if t2 == "1" and t1 == "2U" and t0 == "2D" and bear:
            return "1-2-2", "PUT"

        # 3-2-2
        if t2 == "3" and t1 == "2D" and t0 == "2U" and bull:
            return "3-2-2", "CALL"
        if t2 == "3" and t1 == "2U" and t0 == "2D" and bear:
            return "3-2-2", "PUT"

        # 3-1-2
        if t2 == "3" and t1 == "1" and t0 == "2U":
            return "3-1-2", "CALL"
        if t2 == "3" and t1 == "1" and t0 == "2D":
            return "3-1-2", "PUT"

        # 2-2 Continuation
        if t1 == "2U" and t0 == "2U":
            return "2-2", "CALL"
        if t1 == "2D" and t0 == "2D":
            return "2-2", "PUT"

        return "", ""

    @classmethod
    def compute_ftfc(cls,
                     df_1h: Optional[pd.DataFrame],
                     df_4h: Optional[pd.DataFrame],
                     df_1d: Optional[pd.DataFrame]) -> StratFTFC:
        """
        Compute 3-TF Full Time Frame Continuity.
        1H bars are derived from the 4H dataframe (resample to 1H).
        Each TF bias = close > open on the most recent completed bar.
        """
        ftfc = StratFTFC()

        def _bias(df: Optional[pd.DataFrame]) -> str:
            if df is None or df.empty:
                return "NEUTRAL"
            row = df.iloc[-1]
            c, o = float(row["Close"]), float(row["Open"])
            if c > o:   return "BULL"
            if c < o:   return "BEAR"
            return "NEUTRAL"

        # 1H: resample 4H bars down to 1H if we don't have a dedicated feed
        df_1h_derived = None
        if df_4h is not None and not df_4h.empty:
            try:
                df_1h_derived = (
                    df_4h.resample("1h")
                    .agg({"Open": "first", "High": "max",
                          "Low": "min", "Close": "last", "Volume": "sum"})
                    .dropna()
                )
            except Exception:
                df_1h_derived = None

        use_1h = df_1h if (df_1h is not None and not df_1h.empty) else df_1h_derived

        ftfc.bias_1h = _bias(use_1h)
        ftfc.bias_4h = _bias(df_4h)
        ftfc.bias_1d = _bias(df_1d)

        biases = [ftfc.bias_1h, ftfc.bias_4h, ftfc.bias_1d]
        bull_n = biases.count("BULL")
        bear_n = biases.count("BEAR")

        ftfc.score = max(bull_n, bear_n)

        if bull_n == 3:
            ftfc.ftfc     = True
            ftfc.ftfc_dir = "BULL"
            ftfc.summary  = "FTFC ▲ — 1H + 4H + Daily all bullish"
        elif bear_n == 3:
            ftfc.ftfc     = True
            ftfc.ftfc_dir = "BEAR"
            ftfc.summary  = "FTFC ▼ — 1H + 4H + Daily all bearish"
        else:
            ftfc.ftfc     = False
            ftfc.ftfc_dir = ""
            score_dir     = "▲" if bull_n >= bear_n else "▼"
            ftfc.summary  = (f"Mixed {score_dir} · {ftfc.score}/3 TFs aligned "
                             f"(1H:{ftfc.bias_1h} 4H:{ftfc.bias_4h} 1D:{ftfc.bias_1d})")

        return ftfc


# ── Pattern Detection Engine ──────────────────────────────────────────────────

class PatternDetector:
    """
    Detects all 10 ICT/SMC patterns on a given OHLC dataframe.
    Returns a list of PatternSignal objects.
    """

    def __init__(self, ticker: str, timeframe: str):
        self.ticker    = ticker
        self.timeframe = timeframe
        self.ind       = Indicators()

    def detect_all(self, df: pd.DataFrame) -> List[PatternSignal]:
        min_bars = 100
        if df.empty or len(df) < min_bars:
            logger.debug(f"{self.ticker} {self.timeframe}: only {len(df)} bars, need {min_bars}")
            return []

        df = self._enrich(df)
        signals = []

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

        df["body"]    = (c - o).abs()
        df["range"]   = h - l
        df["hi_wick"] = h - pd.concat([o, c], axis=1).max(axis=1)
        df["lo_wick"] = pd.concat([o, c], axis=1).min(axis=1) - l
        df["bullish"] = c > o
        df["bearish"] = c < o
        df["cl_pct"]  = (c - l) / df["range"].replace(0, np.nan)

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

        df["hammer"] = (
            (df["lo_wick"] >= 2 * df["body"]) &
            (df["body"] > 0) &
            (df["cl_pct"] >= 0.6)
        )
        df["shooting_star"] = (
            (df["hi_wick"] >= 2 * df["body"]) &
            (df["body"] > 0) &
            (df["cl_pct"] <= 0.4)
        )

        df["inside_bar"] = (h < h.shift(1)) & (l > l.shift(1))

        return df

    # -- Pattern Detectors -------------------------------------------------

    def _detect_9ema_bounce(self, df: pd.DataFrame) -> Optional[PatternSignal]:
        last = df.iloc[-1]
        prev = df.iloc[-2]
        c    = float(last["Close"])
        ema9 = float(last["ema9"])

        if np.isnan(ema9): return None

        buffer = ema9 * 0.005

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
        last  = df.iloc[-1]
        c     = float(last["Close"])
        ema21 = float(last["ema21"])

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
        last  = df.iloc[-1]
        c     = float(last["Close"])
        ema50 = float(last["ema50"])

        if np.isnan(ema50): return None

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
        last = df.iloc[-1]
        c    = float(last["Close"])
        sma  = float(last["sma100"])

        if np.isnan(sma): return None

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
        if len(df) < 10: return None

        for lookback in range(3, 8):
            if len(df) <= lookback: continue

            c1 = df.iloc[-lookback - 2]
            c3 = df.iloc[-lookback]

            if c1["High"] < c3["Low"]:
                fvg_top    = c3["Low"]
                fvg_bottom = c1["High"]
                last = df.iloc[-1]
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
        if len(df) < 30: return None

        last  = df.iloc[-1]
        c     = float(last["Close"])

        prior_window = df.iloc[-25:-5]
        if prior_window.empty: return None

        swing_high = prior_window["High"].max()
        swing_low  = prior_window["Low"].min()

        recent = df.iloc[-5:]

        if (recent["High"].max() > swing_high and
            abs(c - swing_high) / swing_high <= 0.015 and
            c > swing_high and last["bullish"]):
            return PatternSignal(
                ticker=self.ticker, timeframe=self.timeframe,
                pattern="Bullish BOS Retest",
                direction="CALL", strength=5,
                reason=f"Broke swing high ${swing_high:.2f}, retesting + holding",
                price=c, level=float(swing_high), candle_date=str(df.index[-1])[:10],
            )

        if (recent["Low"].min() < swing_low and
            abs(c - swing_low) / swing_low <= 0.015 and
            c < swing_low and last["bearish"]):
            return PatternSignal(
                ticker=self.ticker, timeframe=self.timeframe,
                pattern="Bearish BOS Retest",
                direction="PUT", strength=5,
                reason=f"Broke swing low ${swing_low:.2f}, retesting + rejecting",
                price=c, level=float(swing_low), candle_date=str(df.index[-1])[:10],
            )
        return None

    def _detect_order_block(self, df: pd.DataFrame) -> Optional[PatternSignal]:
        if len(df) < 15: return None

        last = df.iloc[-1]
        c    = float(last["Close"])

        for i in range(5, 12):
            if len(df) <= i: continue

            move_candle = df.iloc[-i]
            ob_candle   = df.iloc[-i - 1]

            move_size = abs(move_candle["Close"] - move_candle["Open"])
            if move_size < move_candle["atr"] * 1.5: continue

            if (move_candle["bullish"] and ob_candle["bearish"]):
                ob_top    = ob_candle["High"]
                ob_bottom = ob_candle["Low"]
                if (last["Low"] <= ob_top and last["Low"] >= ob_bottom and
                    c > ob_top and last["bullish"]):
                    return PatternSignal(
                        ticker=self.ticker, timeframe=self.timeframe,
                        pattern="Bullish Order Block",
                        direction="CALL", strength=4,
                        reason=f"Tap bull OB ${ob_bottom:.2f}-${ob_top:.2f}, holding",
                        price=c, level=float(ob_top), candle_date=str(df.index[-1])[:10],
                    )

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
        if len(df) < 25: return None

        recent = df.iloc[-20:]
        last   = df.iloc[-1]
        c      = float(last["Close"])

        lows  = recent["Low"].nsmallest(2)
        highs = recent["High"].nlargest(2)

        if len(lows) >= 2:
            low_diff = abs(lows.iloc[0] - lows.iloc[1]) / lows.iloc[0]
            if low_diff < 0.01:
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
        if len(df) < 5: return None

        last  = df.iloc[-1]
        prev  = df.iloc[-2]
        prev2 = df.iloc[-3]

        if not bool(prev["inside_bar"]): return None
        if prev2["body"] < prev2["atr"] * 1.0: return None

        c = float(last["Close"])

        if prev2["bullish"] and c > prev["High"] and last["bullish"]:
            return PatternSignal(
                ticker=self.ticker, timeframe=self.timeframe,
                pattern="Inside Bar Breakout (Bull)",
                direction="CALL", strength=4,
                reason=f"Inside bar breakout above ${prev['High']:.2f}",
                price=c, level=float(prev["High"]), candle_date=str(df.index[-1])[:10],
            )

        if prev2["bearish"] and c < prev["Low"] and last["bearish"]:
            return PatternSignal(
                ticker=self.ticker, timeframe=self.timeframe,
                pattern="Inside Bar Breakdown (Bear)",
                direction="PUT", strength=4,
                reason=f"Inside bar breakdown below ${prev['Low']:.2f}",
                price=c, level=float(prev["Low"]), candle_date=str(df.index[-1])[:10],
            )
        return None
