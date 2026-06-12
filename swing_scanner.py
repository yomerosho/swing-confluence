"""
swing_scanner.py — SwingConfluence Engine
==========================================
Combines:
  • Technical patterns (10 types, Daily + 4H)
  • GEX positioning (Alpaca real-time)
  • Whale flow ($500K+ trades, last 2-3 days)
  • The Strat (candle sequences, combos, F2, PMG, 3-TF FTFC)

Output: Only 3-of-3 confluence setups for 1-3 day swings.
Strat alignment boosts conviction to 7★ ELITE.
"""

import os
import requests
import pandas as pd
import numpy as np
import logging
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional

from swing_patterns import (
    PatternDetector, PatternSignal, Indicators,
    StratDetector, StratResult, StratFTFC,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── Tickers  ────────────────────────────────────────────────────

INDICES_ETFS = ["SPY", "QQQ", "IWM"]

MEGA_CAPS    = ["AAPL", "MSFT", "NVDA", "META", "GOOGL", "AMZN", "TSLA"]

CORE_20 = [
    "MSTR", "AMD", "PLTR", "COIN", "AVGO",
    "NFLX", "MU", "INTC", "BAC", "UBER",
    "CRWD", "MRVL", "CVNA", "HOOD", "SOFI",
    "QCOM", "DIS", "C", "ABNB", "AMAT",
]

BENCH = [
    "ARM", "LRCX", "TSM", "KLAC", "ON",
    "ADBE", "CRM", "ORCL", "SHOP", "SNOW", "ZS", "PANW",
    "GS", "JPM", "PYPL", "V", "MS", "WFC", "SCHW",
    "ISRG", "UNH",
    "CAT", "CVX", "DE", "FSLR", "GE", "OXY", "XOM",
    "BABA", "JD", "PINS", "RBLX", "RDDT", "WMT", "DKNG", "SBUX", "NKE", "GM",
    "HIMS", "LMND", "OKLO", "RKLB", "AFRM",
]

SWING_NAMES = CORE_20 + BENCH

ALL_TICKERS = INDICES_ETFS + MEGA_CAPS + SWING_NAMES

WHALE_THRESHOLD        = 500_000
WHALE_OVERRIDE         = 5_000_000
MIN_OI_THRESHOLD       = 500
PREFERRED_OI_THRESHOLD = 1000
MIN_RISK_REWARD        = 1.0

ATR_FALLBACK_PCT   = 0.015
STOP_LEVEL_BUFFER  = 0.30
RISK_MIN_ATR       = 0.60
RISK_MAX_ATR       = 2.50
TARGET_MIN_ATR     = 1.00
ATR_TARGET_MULT    = 1.50
MAX_LEVEL_DIST_PCT = 0.25

# Strat combo/signal names that are strong enough to count as
# "directional agreement" and boost conviction.
STRAT_BULLISH_COMBOS = {"2-1-2", "1-2-2", "3-2-2", "3-1-2", "2-2"}
STRAT_BEARISH_COMBOS = {"2-1-2", "1-2-2", "3-2-2", "3-1-2", "2-2"}


# ── Confluence Setup Output ───────────────────────────────────────────────────

@dataclass
class ConfluenceSetup:
    """A 3-of-3 swing setup ready to email."""
    ticker:           str
    direction:        str           # "CALL" or "PUT"
    conviction:       int           # 4, 5, 6, or 7 stars
    spot:             float

    # Factor 1: Technical (ICT/SMC)
    patterns:         List[PatternSignal] = field(default_factory=list)
    has_daily:        bool = False
    has_4h:           bool = False

    # Factor 2: GEX
    gex_summary:      str = ""
    nearest_magnet:   Optional[float] = None
    support_level:    Optional[float] = None
    resistance_level: Optional[float] = None

    # Factor 3: Whales
    whale_summary:    str = ""
    whale_count:      int = 0
    whale_premium:    float = 0

    # Factor 4: The Strat (optional enhancer)
    strat_daily:      Optional[StratResult] = None
    strat_4h:         Optional[StratResult] = None
    strat_ftfc:       Optional[StratFTFC]   = None
    strat_active:     bool  = False   # True when Strat boosts conviction
    strat_summary:    str   = ""      # human-readable Strat summary for the card

    # Trade plan
    strike:           float = 0
    expiry:           str = ""
    entry_above:      float = 0
    stop_below:       float = 0
    target:           float = 0
    risk_reward:      float = 0
    hold_days:        str = "1-3 days"
    strike_oi:        int = 0
    oi_quality:       str = ""


# ── Alpaca Client ─────────────────────────────────────────────────────────────

class AlpacaClient:
    DATA_URL = "https://data.alpaca.markets"

    def __init__(self, key=None, secret=None):
        self.key    = key    or os.environ.get("ALPACA_KEY", "")
        self.secret = secret or os.environ.get("ALPACA_SECRET", "")

    def _headers(self):
        return {
            "APCA-API-KEY-ID":     self.key,
            "APCA-API-SECRET-KEY": self.secret,
            "accept":              "application/json",
        }

    def get_spot(self, ticker: str, verbose: bool = False) -> float:
        for feed in ["sip", "iex"]:
            try:
                url = f"{self.DATA_URL}/v2/stocks/{ticker}/quotes/latest"
                r   = requests.get(url, headers=self._headers(),
                                   params={"feed": feed}, timeout=10)
                if r.status_code == 403:
                    continue
                if r.status_code != 200:
                    continue

                q = r.json().get("quote", {})
                bid, ask = q.get("bp", 0) or 0, q.get("ap", 0) or 0

                if bid == 0 and ask == 0:
                    continue

                mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else (ask or bid)

                if bid > 0 and ask > 0:
                    spread_pct = (ask - bid) / mid * 100
                    if spread_pct > 30:
                        continue

                return mid

            except Exception:
                pass

        for feed in ["sip", "iex"]:
            try:
                url = f"{self.DATA_URL}/v2/stocks/{ticker}/trades/latest"
                r   = requests.get(url, headers=self._headers(),
                                   params={"feed": feed}, timeout=10)
                if r.status_code == 200:
                    p = r.json().get("trade", {}).get("p", 0)
                    if p and p > 0:
                        return p
            except:
                pass

        return 0

    def get_bars(self, ticker: str, timeframe: str = "1Day",
                 days: int = 250) -> pd.DataFrame:
        for feed in ["sip", "iex"]:
            try:
                end   = datetime.now()
                start = end - timedelta(days=days)

                url = f"{self.DATA_URL}/v2/stocks/{ticker}/bars"
                base_params = {
                    "timeframe":  timeframe,
                    "start":      start.strftime("%Y-%m-%dT00:00:00Z"),
                    "end":        end.strftime("%Y-%m-%dT23:59:59Z"),
                    "limit":      10000,
                    "adjustment": "raw",
                    "feed":       feed,
                    "sort":       "asc",
                }

                all_bars, page_token, feed_ok = [], None, True
                for _ in range(25):
                    params = dict(base_params)
                    if page_token:
                        params["page_token"] = page_token
                    r = requests.get(url, headers=self._headers(),
                                     params=params, timeout=20)

                    if r.status_code == 403:
                        feed_ok = False
                        break
                    if r.status_code != 200:
                        feed_ok = False
                        break

                    payload = r.json()
                    all_bars.extend(payload.get("bars", []) or [])
                    page_token = payload.get("next_page_token")
                    if not page_token:
                        break

                if not feed_ok or not all_bars:
                    continue

                df = pd.DataFrame(all_bars)
                df["t"] = pd.to_datetime(df["t"])
                df.set_index("t", inplace=True)
                df.rename(columns={"o": "Open", "h": "High", "l": "Low",
                                   "c": "Close", "v": "Volume"}, inplace=True)
                df = df[~df.index.duplicated(keep="last")].sort_index()
                logger.info(f"{ticker} {timeframe} feed={feed}: {len(df)} bars "
                            f"(last {df.index[-1].date()})")
                return df[["Open", "High", "Low", "Close", "Volume"]]
            except Exception as e:
                logger.error(f"{ticker} {timeframe} feed={feed} exception: {e}")
                continue

        return pd.DataFrame()

    def get_open_interest(self, ticker: str) -> dict:
        oi_map = {}
        try:
            url = "https://paper-api.alpaca.markets/v2/options/contracts"
            params = {
                "underlying_symbols": ticker,
                "status":             "active",
                "limit":              1000,
            }
            page_token = None
            pages      = 0

            while pages < 10:
                if page_token: params["page_token"] = page_token
                r = requests.get(url, headers=self._headers(),
                                 params=params, timeout=15)
                if r.status_code != 200:
                    break

                data = r.json()
                for c in data.get("option_contracts", []):
                    sym = c.get("symbol")
                    oi  = c.get("open_interest")
                    if sym and oi is not None:
                        try: oi_map[sym] = int(oi)
                        except: pass

                page_token = data.get("next_page_token")
                if not page_token: break
                pages += 1
                time.sleep(0.1)
        except Exception as e:
            logger.debug(f"{ticker} OI fetch: {e}")
        return oi_map

    def get_options_chain(self, ticker: str, days_out: int = 14) -> pd.DataFrame:
        try:
            today  = datetime.now().date()
            end_dt = today + timedelta(days=days_out)
            url    = f"{self.DATA_URL}/v1beta1/options/snapshots/{ticker}"
            params = {
                "limit": 1000,
                "feed":  "indicative",
                "expiration_date_gte": today.isoformat(),
                "expiration_date_lte": end_dt.isoformat(),
            }

            all_rows   = []
            page_token = None
            pages      = 0

            while pages < 5:
                if page_token: params["page_token"] = page_token
                r = requests.get(url, headers=self._headers(),
                                 params=params, timeout=20)
                if r.status_code != 200: break

                data = r.json()
                for symbol, snap in data.get("snapshots", {}).items():
                    parsed = self._parse_option_symbol(symbol)
                    if not parsed: continue

                    q      = snap.get("latestQuote") or {}
                    t      = snap.get("latestTrade") or {}
                    g      = snap.get("greeks") or {}
                    daily  = snap.get("dailyBar") or {}
                    prev   = snap.get("prevDailyBar") or {}

                    bid, ask = q.get("bp", 0) or 0, q.get("ap", 0) or 0
                    mid = (bid + ask) / 2 if bid and ask else 0

                    day_vol  = daily.get("v", 0) or 0
                    prev_vol = prev.get("v", 0) or 0
                    last_sz  = t.get("s", 0) or 0
                    volume   = day_vol if day_vol > 0 else (prev_vol if prev_vol > 0 else last_sz)
                    vwap     = daily.get("vw", 0) or mid

                    all_rows.append({
                        "symbol":      symbol,
                        "option_type": parsed["type"],
                        "strike":      parsed["strike"],
                        "expiry":      parsed["expiration"],
                        "bid":         bid, "ask": ask, "mid": mid,
                        "last":        t.get("p") or 0,
                        "volume":      volume,
                        "vwap":        vwap,
                        "day_volume":  day_vol,
                        "prev_volume": prev_vol,
                        "gamma":       g.get("gamma", 0),
                        "delta":       g.get("delta", 0),
                        "iv":          snap.get("impliedVolatility") or 0,
                        "open_interest": 0,
                    })

                page_token = data.get("next_page_token")
                if not page_token: break
                pages += 1
                time.sleep(0.1)

            if not all_rows:
                return pd.DataFrame()

            df = pd.DataFrame(all_rows)

            oi_map = self.get_open_interest(ticker)
            if oi_map:
                df["open_interest"] = df["symbol"].map(oi_map).fillna(0).astype(int)

            df["spread"]     = df["ask"] - df["bid"]
            df["spread_pct"] = np.where(df["mid"] > 0,
                                        df["spread"] / df["mid"] * 100, 999)
            return df

        except Exception as e:
            logger.error(f"{ticker} chain: {e}")
            return pd.DataFrame()

    @staticmethod
    def _parse_option_symbol(sym: str) -> Optional[dict]:
        try:
            for i, ch in enumerate(sym):
                if ch.isdigit():
                    rest = sym[i:]
                    break
            else: return None
            if len(rest) < 15: return None
            return {
                "expiration": f"20{rest[:2]}-{rest[2:4]}-{rest[4:6]}",
                "type":       "call" if rest[6] == "C" else "put",
                "strike":     int(rest[7:15]) / 1000.0,
            }
        except: return None


# ── GEX Analyzer ──────────────────────────────────────────────────────────────

class GEXAnalyzer:

    def analyze(self, chain: pd.DataFrame, spot: float, direction: str) -> dict:
        if chain.empty:
            return {"supports": False, "summary": "No options data",
                    "magnet": None, "support": None, "resistance": None}

        df = chain.copy()
        df["volume"] = df["volume"].fillna(0)

        df["gex"] = np.where(
            df["option_type"] == "call",
             df["gamma"].abs() * df["volume"] * 100 * spot**2 * 0.01,
            -df["gamma"].abs() * df["volume"] * 100 * spot**2 * 0.01
        )

        by_strike = df.groupby("strike").agg(
            net_gex=("gex", "sum"),
            total_vol=("volume", "sum"),
        ).reset_index().sort_values("strike")

        if by_strike.empty:
            return {"supports": False, "summary": "Empty GEX",
                    "magnet": None, "support": None, "resistance": None}

        below = by_strike[by_strike["strike"] < spot]
        above = by_strike[by_strike["strike"] > spot]

        support    = float(below.nlargest(1, "net_gex")["strike"].iloc[0]) if not below.empty else None
        resistance = float(above.nlargest(1, "net_gex")["strike"].iloc[0]) if not above.empty else None

        df["dist_pct"] = abs(df["strike"] - spot) / spot
        df["magnet_score"] = (df["gamma"].abs() * df["volume"].clip(lower=1)
                              * np.exp(-df["dist_pct"] * 5))
        magnet_row = (df.groupby("strike")["magnet_score"]
                      .sum().sort_values(ascending=False).head(1))
        magnet = float(magnet_row.index[0]) if not magnet_row.empty else None

        if direction == "CALL":
            has_support_below = support is not None and spot > support
            has_magnet_above  = magnet  is not None and magnet > spot
            supports = has_support_below or has_magnet_above

            if has_support_below and has_magnet_above:
                summary = f"GEX support ${support:.2f} below + magnet ${magnet:.2f} above (strong)"
            elif has_support_below:
                summary = f"GEX support ${support:.2f} below"
            elif has_magnet_above:
                summary = f"Magnet pulling toward ${magnet:.2f} above"
            else:
                summary = f"GEX positioning unfavorable for bullish"
        else:
            has_resistance_above = resistance is not None and spot < resistance
            has_magnet_below     = magnet     is not None and magnet < spot
            supports = has_resistance_above or has_magnet_below

            if has_resistance_above and has_magnet_below:
                summary = f"GEX resistance ${resistance:.2f} above + magnet ${magnet:.2f} below (strong)"
            elif has_resistance_above:
                summary = f"GEX resistance ${resistance:.2f} above"
            elif has_magnet_below:
                summary = f"Magnet pulling toward ${magnet:.2f} below"
            else:
                summary = f"GEX positioning unfavorable for bearish"

        return {
            "supports":   supports,
            "summary":    summary,
            "magnet":     magnet,
            "support":    support,
            "resistance": resistance,
        }


# ── Whale Flow Analyzer ───────────────────────────────────────────────────────

class WhaleAnalyzer:

    def analyze(self, chain: pd.DataFrame, spot: float, direction: str,
                threshold: float = WHALE_THRESHOLD) -> dict:
        if chain.empty:
            return {"supports": False, "summary": "No flow data",
                    "count": 0, "premium": 0}

        df = chain.copy()
        df["volume"] = df["volume"].fillna(0)
        df["mid"]    = df["mid"].fillna(0)
        df["vwap"]   = df.get("vwap", df["mid"]).fillna(df["mid"])

        price_for_prem = df["vwap"].where(df["vwap"] > 0, df["mid"])
        df["premium"]  = price_for_prem * df["volume"] * 100

        df = df[abs(df["strike"] - spot) / spot <= 0.10]

        target_type = "call" if direction == "CALL" else "put"
        directional = df[(df["option_type"] == target_type) & (df["volume"] > 0)].copy()

        if directional.empty:
            return {
                "supports": False,
                "summary":  f"No {direction.lower()} flow detected near spot",
                "count":    0, "premium": 0,
            }

        big_strikes  = directional[directional["premium"] >= threshold]
        agg_threshold = threshold * 2
        total_prem    = float(directional["premium"].sum())

        if not big_strikes.empty:
            top = big_strikes.nlargest(1, "premium").iloc[0]
            return {
                "supports": True,
                "summary":  (f"🐋 BLOCK: ${top['premium']:,.0f} on "
                             f"{target_type.upper()} ${top['strike']:.0f} "
                             f"{top['expiry']} ({len(big_strikes)} block strike(s))"),
                "count":    len(big_strikes),
                "premium":  float(big_strikes["premium"].sum()),
            }
        elif total_prem >= agg_threshold:
            top = directional.nlargest(1, "premium").iloc[0]
            return {
                "supports": True,
                "summary":  (f"💪 PRESSURE: ${total_prem:,.0f} aggregate "
                             f"{direction.lower()} flow near spot · "
                             f"largest strike: ${top['strike']:.0f} ${top['premium']:,.0f}"),
                "count":    int((directional["premium"] > 0).sum()),
                "premium":  total_prem,
            }
        else:
            top_prem = float(directional["premium"].max()) if not directional.empty else 0
            return {
                "supports": False,
                "summary":  (f"No whale signal · top strike ${top_prem:,.0f} "
                             f"(need $500K block or $1M aggregate)"),
                "count":    0,
                "premium":  total_prem,
            }


# ── Strat Confluence Evaluator ────────────────────────────────────────────────

def _evaluate_strat(
    direction: str,
    strat_daily: StratResult,
    strat_4h: StratResult,
    ftfc: StratFTFC,
) -> tuple:
    """
    Returns (strat_active: bool, strat_summary: str, conviction_bonus: int).
    conviction_bonus is 1 when Strat adds a meaningful signal.

    Strat signals that count toward conviction:
    - Directionally aligned combo on Daily or 4H
    - F2D (for CALL) or F2U (for PUT) on Daily or 4H
    - PMG on either timeframe
    - FTFC aligned with direction (all 3 TFs same bias)
    """
    signals = []
    bonus   = 0

    call = direction == "CALL"

    # Check F2 traps — these are the highest-quality reversal signals
    for strat, tf_label in [(strat_daily, "Daily"), (strat_4h, "4H")]:
        if call and strat.is_f2d:
            signals.append(f"F2D 🪤 on {tf_label} (bears trapped)")
            bonus = 1
        if not call and strat.is_f2u:
            signals.append(f"F2U 🪤 on {tf_label} (bulls trapped)")
            bonus = 1

    # Check combos aligned with direction
    for strat, tf_label in [(strat_daily, "Daily"), (strat_4h, "4H")]:
        if strat.combo and strat.combo_dir == direction:
            signals.append(f"{strat.combo} ▲ on {tf_label}" if call
                           else f"{strat.combo} ▼ on {tf_label}")
            bonus = 1

    # PMG — reversal brewing regardless of direction label
    for strat, tf_label in [(strat_daily, "Daily"), (strat_4h, "4H")]:
        if strat.is_pmg:
            signals.append(f"⚡ PMG on {tf_label} ({strat.pmg_count} bars)")
            bonus = 1

    # FTFC aligned with trade direction
    ftfc_dir_map = {"BULL": "CALL", "BEAR": "PUT"}
    if ftfc.ftfc and ftfc_dir_map.get(ftfc.ftfc_dir) == direction:
        signals.append(f"FTFC {ftfc.summary}")
        bonus = 1

    strat_active = bool(signals)
    summary      = " · ".join(signals) if signals else ""

    return strat_active, summary, bonus


# ── SwingConfluence Scanner (Main Engine) ─────────────────────────────────────

class SwingScanner:

    def __init__(self, alpaca_key=None, alpaca_secret=None):
        self.alpaca = AlpacaClient(alpaca_key, alpaca_secret)
        self.gex    = GEXAnalyzer()
        self.whale  = WhaleAnalyzer()

    # ── Single-ticker diagnostic (unchanged logic, Strat data added) ──────

    def diagnose_ticker(self, ticker: str) -> dict:
        diag = {
            "ticker":           ticker,
            "spot":             None,
            "daily_patterns":   [],
            "h4_patterns":      [],
            "strat_daily":      None,
            "strat_4h":         None,
            "strat_ftfc":       None,
            "directions_tried": [],
            "results":          [],
            "error":            None,
        }

        try:
            spot = self.alpaca.get_spot(ticker)
            diag["spot"] = spot
            if spot == 0:
                diag["error"] = "No spot price"
                return diag

            df_daily = self.alpaca.get_bars(ticker, "1Day", days=250)
            df_4h    = self.alpaca.get_bars(ticker, "4Hour", days=180)

            if df_daily.empty:
                diag["error"] = "No daily bars"
                return diag

            diag["daily_patterns"] = PatternDetector(ticker, "1D").detect_all(df_daily)
            diag["h4_patterns"]    = (PatternDetector(ticker, "4H").detect_all(df_4h)
                                      if not df_4h.empty else [])

            # Strat analysis
            diag["strat_daily"] = StratDetector.analyze(df_daily, "1D")
            diag["strat_4h"]    = StratDetector.analyze(df_4h, "4H") if not df_4h.empty else StratResult("4H")
            diag["strat_ftfc"]  = StratDetector.compute_ftfc(None, df_4h, df_daily)

            all_patterns = diag["daily_patterns"] + diag["h4_patterns"]
            calls = [p for p in all_patterns if p.direction == "CALL"]
            puts  = [p for p in all_patterns if p.direction == "PUT"]

            chain = self.alpaca.get_options_chain(ticker, days_out=14)

            for direction, patterns in [("CALL", calls), ("PUT", puts)]:
                if not patterns:
                    continue

                diag["directions_tried"].append(direction)
                result = {
                    "direction":         direction,
                    "pattern_count":     len(patterns),
                    "patterns":          patterns,
                    "gex_supports":      False,
                    "gex_summary":       "",
                    "whale_supports":    False,
                    "whale_summary":     "",
                    "whale_count":       0,
                    "whale_top_premium": 0,
                    "blocked_at":        "patterns",
                }

                if chain.empty:
                    result["blocked_at"] = "no_chain"
                    diag["results"].append(result)
                    continue

                gex_result   = self.gex.analyze(chain, spot, direction)
                whale_result = self.whale.analyze(chain, spot, direction)

                result["gex_supports"]      = gex_result["supports"]
                result["gex_summary"]       = gex_result["summary"]
                result["whale_supports"]    = whale_result["supports"]
                result["whale_summary"]     = whale_result["summary"]
                result["whale_count"]       = whale_result["count"]
                result["whale_top_premium"] = whale_result["premium"]

                whale_override = whale_result["premium"] >= WHALE_OVERRIDE
                result["whale_override"] = whale_override

                gex_passes = gex_result["supports"] or whale_override

                if not gex_passes:
                    result["blocked_at"] = "gex"
                elif not whale_result["supports"]:
                    result["blocked_at"] = "whales"
                else:
                    result["blocked_at"] = (
                        "passed_with_override"
                        if whale_override and not gex_result["supports"]
                        else "passed"
                    )

                diag["results"].append(result)

        except Exception as e:
            diag["error"] = f"{type(e).__name__}: {e}"
            logger.error(f"{ticker} diagnose: {diag['error']}")

        return diag

    def diagnose_all(self, tickers: List[str] = None,
                     progress_cb=None) -> List[dict]:
        if tickers is None:
            tickers = ALL_TICKERS

        results = []
        total   = len(tickers)

        for i, ticker in enumerate(tickers):
            if progress_cb:
                progress_cb(i / total, f"Diagnosing {ticker} ({i+1}/{total})...")
            results.append(self.diagnose_ticker(ticker))

        return results

    # ── Single-ticker scan ────────────────────────────────────────────────

    def scan_ticker(self, ticker: str) -> List[ConfluenceSetup]:
        try:
            spot = self.alpaca.get_spot(ticker)
            if spot == 0:
                logger.warning(f"{ticker}: no spot price")
                return []

            df_daily = self.alpaca.get_bars(ticker, "1Day", days=250)
            df_4h    = self.alpaca.get_bars(ticker, "4Hour", days=180)

            if df_daily.empty:
                return []

            # Freshness guard
            now_naive = pd.Timestamp.now("UTC").tz_convert(None)

            def _age_days(d) -> float:
                if d is None or d.empty: return 1e9
                ts = pd.to_datetime(d.index[-1])
                if ts.tzinfo is not None:
                    ts = ts.tz_convert(None)
                return (now_naive - ts).days

            if _age_days(df_daily) > 5:
                logger.warning(f"{ticker}: daily bars stale, skipping")
                return []
            if _age_days(df_4h) > 5:
                logger.warning(f"{ticker}: 4H bars stale, ignoring 4H")
                df_4h = pd.DataFrame()

            # Daily ATR
            try:
                atr_series = Indicators.atr(
                    df_daily["High"], df_daily["Low"], df_daily["Close"]
                )
                atr_d = float(atr_series.iloc[-1]) if not atr_series.empty else 0.0
                if not np.isfinite(atr_d): atr_d = 0.0
            except Exception:
                atr_d = 0.0

            # ICT/SMC patterns
            daily_patterns = PatternDetector(ticker, "1D").detect_all(df_daily)
            h4_patterns    = (PatternDetector(ticker, "4H").detect_all(df_4h)
                              if not df_4h.empty else [])

            all_patterns = daily_patterns + h4_patterns

            # Level-sanity filter
            all_patterns = [
                p for p in all_patterns
                if p.level and p.level > 0
                and abs(p.level - spot) / spot <= MAX_LEVEL_DIST_PCT
            ]
            if not all_patterns:
                return []

            # Strat analysis — run once, reuse for both CALL/PUT builds
            strat_daily = StratDetector.analyze(df_daily, "1D")
            strat_4h    = (StratDetector.analyze(df_4h, "4H")
                           if not df_4h.empty else StratResult("4H"))
            ftfc        = StratDetector.compute_ftfc(None, df_4h, df_daily)

            calls = [p for p in all_patterns if p.direction == "CALL"]
            puts  = [p for p in all_patterns if p.direction == "PUT"]

            setups = []

            if calls:
                setup = self._build_setup(
                    ticker, spot, "CALL", calls, atr_d,
                    strat_daily, strat_4h, ftfc,
                )
                if setup: setups.append(setup)

            if puts:
                setup = self._build_setup(
                    ticker, spot, "PUT", puts, atr_d,
                    strat_daily, strat_4h, ftfc,
                )
                if setup: setups.append(setup)

            return setups

        except Exception as e:
            logger.error(f"{ticker} scan error: {e}")
            return []

    def _build_setup(
        self,
        ticker: str,
        spot: float,
        direction: str,
        patterns: List[PatternSignal],
        atr: float = 0.0,
        strat_daily: Optional[StratResult] = None,
        strat_4h:    Optional[StratResult] = None,
        ftfc:        Optional[StratFTFC]   = None,
    ) -> Optional[ConfluenceSetup]:

        has_daily = any(p.timeframe == "1D" for p in patterns)
        has_4h    = any(p.timeframe == "4H" for p in patterns)

        chain = self.alpaca.get_options_chain(ticker, days_out=14)
        if chain.empty:
            return None

        gex_result   = self.gex.analyze(chain, spot, direction)
        whale_result = self.whale.analyze(chain, spot, direction)

        whale_override_active = whale_result["premium"] >= WHALE_OVERRIDE

        gex_passes   = gex_result["supports"] or whale_override_active
        whale_passes = whale_result["supports"]

        if not gex_passes or not whale_passes:
            return None

        # ── Base conviction (ICT/SMC) ──
        if has_daily and has_4h:
            conviction = 6
        elif has_daily:
            conviction = 5
        else:
            conviction = 4

        # ── Strat evaluation ──
        _sd = strat_daily if strat_daily is not None else StratResult("1D")
        _s4 = strat_4h    if strat_4h    is not None else StratResult("4H")
        _ft = ftfc        if ftfc        is not None else StratFTFC()

        strat_active, strat_summary, strat_bonus = _evaluate_strat(
            direction, _sd, _s4, _ft
        )

        # Boost to 7★ ELITE when Strat adds signal on top of 6★ or 5★+
        if strat_active and strat_bonus:
            conviction = min(conviction + 1, 7)

        # GEX summary (whale override tag)
        if whale_override_active and not gex_result["supports"]:
            gex_summary = (f"⚠️ GEX neutral but "
                           f"${whale_result['premium']/1_000_000:.1f}M whale flow overrides · "
                           f"{gex_result['summary']}")
        else:
            gex_summary = gex_result["summary"]

        plan = self._build_trade_plan(
            spot, direction, patterns, gex_result, chain, atr
        )

        if plan["risk_reward"] < MIN_RISK_REWARD:
            logger.info(
                f"{ticker} {direction} rejected: R/R {plan['risk_reward']} < {MIN_RISK_REWARD}"
            )
            return None

        return ConfluenceSetup(
            ticker=ticker, direction=direction,
            conviction=conviction, spot=spot,
            patterns=patterns, has_daily=has_daily, has_4h=has_4h,
            gex_summary=gex_summary,
            nearest_magnet=gex_result["magnet"],
            support_level=gex_result["support"],
            resistance_level=gex_result["resistance"],
            whale_summary=whale_result["summary"],
            whale_count=whale_result["count"],
            whale_premium=whale_result["premium"],
            strat_daily=_sd,
            strat_4h=_s4,
            strat_ftfc=_ft,
            strat_active=strat_active,
            strat_summary=strat_summary,
            **plan,
        )

    def _build_trade_plan(
        self,
        spot: float,
        direction: str,
        patterns: List[PatternSignal],
        gex_result: dict,
        chain: pd.DataFrame,
        atr: float = 0.0,
    ) -> dict:

        target_dte_min, target_dte_max = 3, 14
        today = datetime.now().date()

        df = chain.copy()
        df["dte"] = pd.to_datetime(df["expiry"]).dt.date.apply(
            lambda d: (d - today).days
        )
        df = df[(df["dte"] >= target_dte_min) & (df["dte"] <= target_dte_max)]
        df = df[df["option_type"] == direction.lower()]

        if df.empty:
            df = chain[chain["option_type"] == direction.lower()]

        if direction == "CALL":
            df_otm = df[df["strike"] > spot].copy()
        else:
            df_otm = df[df["strike"] < spot].copy()

        df_otm = df_otm[
            (df_otm["bid"] > 0.05) &
            (df_otm["spread_pct"] < 15)
        ]

        strike, expiry, picked_oi, oi_quality = spot, "", 0, "unknown"

        if not df_otm.empty:
            tier1 = df_otm[
                (df_otm["open_interest"] >= PREFERRED_OI_THRESHOLD) &
                (df_otm["spread_pct"] <= 5)
            ]
            tier2 = df_otm[
                (df_otm["open_interest"] >= MIN_OI_THRESHOLD) &
                (df_otm["spread_pct"] <= 10)
            ]

            if not tier1.empty:
                pick = (tier1.nsmallest(1, "strike") if direction == "CALL"
                        else tier1.nlargest(1, "strike"))
                oi_quality = "✅ Liquid"
            elif not tier2.empty:
                pick = (tier2.nsmallest(1, "strike") if direction == "CALL"
                        else tier2.nlargest(1, "strike"))
                oi_quality = "🟡 Adequate"
            else:
                pick = (df_otm.nsmallest(1, "strike") if direction == "CALL"
                        else df_otm.nlargest(1, "strike"))
                oi_quality = "⚠️ Illiquid — wide spread"

            if not pick.empty:
                strike    = float(pick.iloc[0]["strike"])
                expiry    = str(pick.iloc[0]["expiry"])
                picked_oi = int(pick.iloc[0]["open_interest"])

        atr_val        = float(atr) if (atr and atr > 0) else spot * ATR_FALLBACK_PCT
        pattern_levels = [float(p.level) for p in patterns if p.level and p.level > 0]
        min_dist       = max(TARGET_MIN_ATR * atr_val, spot * 0.01)

        if direction == "CALL":
            entry = spot
            below = [lv for lv in pattern_levels if lv < spot]
            gsup  = gex_result.get("support")
            if gsup and gsup < spot:
                below.append(float(gsup))
            struct   = max(below) if below else None
            raw_stop = ((struct - STOP_LEVEL_BUFFER * atr_val) if struct is not None
                        else (spot - 1.5 * atr_val))
            risk   = min(max(spot - raw_stop, RISK_MIN_ATR * atr_val),
                         RISK_MAX_ATR * atr_val)
            stop   = spot - risk
            ups    = sorted(
                lv for lv in (gex_result.get("magnet"), gex_result.get("resistance"))
                if lv and lv > entry + min_dist
            )
            target = ups[0] if ups else entry + ATR_TARGET_MULT * atr_val
        else:
            entry = spot
            above = [lv for lv in pattern_levels if lv > spot]
            gres  = gex_result.get("resistance")
            if gres and gres > spot:
                above.append(float(gres))
            struct   = min(above) if above else None
            raw_stop = ((struct + STOP_LEVEL_BUFFER * atr_val) if struct is not None
                        else (spot + 1.5 * atr_val))
            risk   = min(max(raw_stop - spot, RISK_MIN_ATR * atr_val),
                         RISK_MAX_ATR * atr_val)
            stop   = spot + risk
            downs  = sorted(
                (lv for lv in (gex_result.get("magnet"), gex_result.get("support"))
                 if lv and lv < entry - min_dist),
                reverse=True,
            )
            target = downs[0] if downs else entry - ATR_TARGET_MULT * atr_val

        entry_above = float(entry)
        stop_below  = float(stop)
        target      = float(target)

        risk   = abs(entry_above - stop_below)
        reward = abs(target - entry_above)
        rr     = round(reward / risk, 2) if risk > 0 else 0

        return {
            "strike":      round(strike, 2),
            "expiry":      expiry,
            "entry_above": round(entry_above, 2),
            "stop_below":  round(stop_below, 2),
            "target":      round(target, 2),
            "risk_reward": rr,
            "strike_oi":   picked_oi,
            "oi_quality":  oi_quality,
        }

    def scan_all(self, tickers: List[str] = None,
                 progress_cb=None) -> List[ConfluenceSetup]:
        if tickers is None:
            tickers = ALL_TICKERS

        all_setups = []
        total      = len(tickers)

        for i, ticker in enumerate(tickers):
            if progress_cb:
                progress_cb(i / total, f"Scanning {ticker} ({i+1}/{total})...")

            setups = self.scan_ticker(ticker)
            if setups:
                logger.info(f"✅ {ticker}: {len(setups)} confluence setup(s)")
                all_setups.extend(setups)

        # Sort: conviction descending (7→4), then ticker
        all_setups.sort(key=lambda s: (-s.conviction, s.ticker))
        return all_setups
