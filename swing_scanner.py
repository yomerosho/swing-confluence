"""
swing_scanner.py — SwingConfluence Engine
==========================================
Combines:
  • Technical patterns (10 types, Daily + 4H)
  • GEX positioning (Alpaca real-time)
  • Whale flow ($500K+ trades, last 2-3 days)

Output: Only 3-of-3 confluence setups for 1-3 day swings.
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

from swing_patterns import PatternDetector, PatternSignal

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── Tickers (36 confirmed) ────────────────────────────────────────────────────

INDICES_ETFS = ["SPY", "QQQ", "IWM", "DIA", "XLF", "EEM"]
MEGA_CAPS    = ["AAPL", "MSFT", "NVDA", "META", "GOOGL", "AMZN", "TSLA"]
SWING_NAMES  = [
    # Semis & Hardware
    "AMD", "ARM", "AVGO", "INTC", "LRCX", "MU", "TSM",
    # Software & Cloud
    "ADBE", "CRM", "ORCL", "SHOP", "SNOW", "ZS",
    # Financials
    "COIN", "GS", "HOOD", "JPM", "PYPL", "SOFI", "V",
    # Healthcare
    "ISRG", "UNH",
    # Energy & Industrials
    "CAT", "CVX", "DE", "FSLR", "GE", "OXY", "XOM",
    # Consumer & Internet
    "BABA", "JD", "NFLX", "PINS", "RBLX", "RDDT", "UBER", "WMT",
    # Speculative / High-Beta
    "HIMS", "LMND", "OKLO", "PLTR", "RKLB",
]
ALL_TICKERS = INDICES_ETFS + MEGA_CAPS + SWING_NAMES

WHALE_THRESHOLD       = 500_000
MIN_OI_THRESHOLD      = 500       # minimum OI for a tradeable strike
PREFERRED_OI_THRESHOLD = 1000     # preferred OI for full-confidence pick


# ── Confluence Setup Output ───────────────────────────────────────────────────

@dataclass
class ConfluenceSetup:
    """A 3-of-3 swing setup ready to email."""
    ticker:           str
    direction:        str           # "CALL" or "PUT"
    conviction:       int           # 4, 5, or 6 stars
    spot:             float

    # Factor 1: Technical
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
    """Alpaca REST client for options + bars."""

    DATA_URL    = "https://data.alpaca.markets"

    def __init__(self, key=None, secret=None):
        self.key    = key    or os.environ.get("ALPACA_KEY", "")
        self.secret = secret or os.environ.get("ALPACA_SECRET", "")

    def _headers(self):
        return {
            "APCA-API-KEY-ID":     self.key,
            "APCA-API-SECRET-KEY": self.secret,
            "accept":              "application/json",
        }

    def get_spot(self, ticker: str) -> float:
        try:
            url = f"{self.DATA_URL}/v2/stocks/{ticker}/quotes/latest"
            r = requests.get(url, headers=self._headers(), timeout=10)
            if r.status_code == 200:
                q = r.json().get("quote", {})
                bid, ask = q.get("bp", 0), q.get("ap", 0)
                if bid > 0 and ask > 0: return (bid + ask) / 2
                return ask or bid
        except Exception as e:
            logger.debug(f"{ticker} spot error: {e}")
        return 0

    def get_bars(self, ticker: str, timeframe: str = "1Day", days: int = 250) -> pd.DataFrame:
        """
        Get OHLC bars from Alpaca.
        timeframe: "1Day", "4Hour", "1Hour", etc.
        """
        try:
            end   = datetime.now()
            start = end - timedelta(days=days)

            url = f"{self.DATA_URL}/v2/stocks/{ticker}/bars"
            params = {
                "timeframe":  timeframe,
                "start":      start.strftime("%Y-%m-%dT00:00:00Z"),
                "end":        end.strftime("%Y-%m-%dT23:59:59Z"),
                "limit":      10000,
                "adjustment": "raw",
                "feed":       "iex",
            }
            r = requests.get(url, headers=self._headers(), params=params, timeout=20)
            if r.status_code != 200:
                logger.warning(f"{ticker} bars {r.status_code}: {r.text[:200]}")
                return pd.DataFrame()

            bars = r.json().get("bars", [])
            if not bars: return pd.DataFrame()

            df = pd.DataFrame(bars)
            df["t"] = pd.to_datetime(df["t"])
            df.set_index("t", inplace=True)
            df.rename(columns={"o": "Open", "h": "High", "l": "Low",
                               "c": "Close", "v": "Volume"}, inplace=True)
            return df[["Open", "High", "Low", "Close", "Volume"]]
        except Exception as e:
            logger.error(f"{ticker} bars error: {e}")
            return pd.DataFrame()

    def get_open_interest(self, ticker: str) -> dict:
        """
        Fetch Open Interest for all active option contracts.
        Returns: {option_symbol: open_interest_int}
        Uses the contracts endpoint which provides current OI.
        """
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
                r = requests.get(url, headers=self._headers(), params=params, timeout=15)
                if r.status_code != 200:
                    logger.debug(f"{ticker} OI {r.status_code}")
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
        """Fetch options chain with Greeks AND open interest."""
        try:
            today   = datetime.now().date()
            end_dt  = today + timedelta(days=days_out)
            url     = f"{self.DATA_URL}/v1beta1/options/snapshots/{ticker}"
            params  = {
                "limit": 1000,
                "feed":  "indicative",
                "expiration_date_gte": today.isoformat(),
                "expiration_date_lte": end_dt.isoformat(),
            }

            all_rows  = []
            page_token = None
            pages      = 0

            while pages < 5:
                if page_token: params["page_token"] = page_token
                r = requests.get(url, headers=self._headers(), params=params, timeout=20)
                if r.status_code != 200: break

                data = r.json()
                for symbol, snap in data.get("snapshots", {}).items():
                    parsed = self._parse_option_symbol(symbol)
                    if not parsed: continue

                    q  = snap.get("latestQuote") or {}
                    t  = snap.get("latestTrade") or {}
                    g  = snap.get("greeks") or {}
                    iv = snap.get("impliedVolatility") or 0

                    bid, ask = q.get("bp", 0) or 0, q.get("ap", 0) or 0
                    mid = (bid + ask) / 2 if bid and ask else 0

                    all_rows.append({
                        "symbol":      symbol,
                        "option_type": parsed["type"],
                        "strike":      parsed["strike"],
                        "expiry":      parsed["expiration"],
                        "bid":         bid, "ask": ask, "mid": mid,
                        "last":        t.get("p") or 0,
                        "volume":      t.get("s") or 0,
                        "gamma":       g.get("gamma", 0),
                        "delta":       g.get("delta", 0),
                        "iv":          iv,
                        "open_interest": 0,  # filled in below
                    })

                page_token = data.get("next_page_token")
                if not page_token: break
                pages += 1
                time.sleep(0.1)

            if not all_rows:
                return pd.DataFrame()

            df = pd.DataFrame(all_rows)

            # Enrich with OI
            oi_map = self.get_open_interest(ticker)
            if oi_map:
                df["open_interest"] = df["symbol"].map(oi_map).fillna(0).astype(int)

            # Spread metrics for liquidity scoring
            df["spread"]     = df["ask"] - df["bid"]
            df["spread_pct"] = np.where(df["mid"] > 0, df["spread"] / df["mid"] * 100, 999)

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
    """
    Analyzes whether dealer positioning supports a directional trade.
    Returns confluence verdict (supports/neutral/opposes) + level info.
    """

    def analyze(self, chain: pd.DataFrame, spot: float, direction: str) -> dict:
        """
        direction: "CALL" or "PUT"
        Returns: {
          "supports":  bool,         # True if GEX supports the trade
          "summary":   str,          # Plain-English explanation
          "magnet":    float,        # Nearest magnetic level
          "support":   float,        # Key GEX support
          "resistance": float,        # Key GEX resistance
        }
        """
        if chain.empty:
            return {"supports": False, "summary": "No options data", "magnet": None, "support": None, "resistance": None}

        df = chain.copy()
        df["volume"] = df["volume"].fillna(0)

        # GEX per strike (using gamma × volume as proxy when no OI)
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
            return {"supports": False, "summary": "Empty GEX", "magnet": None, "support": None, "resistance": None}

        # Find nearby support (highest pos GEX below) and resistance (highest pos GEX above)
        below = by_strike[by_strike["strike"] < spot]
        above = by_strike[by_strike["strike"] > spot]

        support    = float(below.nlargest(1, "net_gex")["strike"].iloc[0]) if not below.empty else None
        resistance = float(above.nlargest(1, "net_gex")["strike"].iloc[0]) if not above.empty else None

        # Magnetic level: highest gamma × vol weighted by proximity
        df["dist_pct"] = abs(df["strike"] - spot) / spot
        df["magnet_score"] = df["gamma"].abs() * df["volume"].clip(lower=1) * np.exp(-df["dist_pct"] * 5)
        magnet_row = df.groupby("strike")["magnet_score"].sum().sort_values(ascending=False).head(1)
        magnet = float(magnet_row.index[0]) if not magnet_row.empty else None

        # Confluence verdict
        if direction == "CALL":
            # Bullish needs: support BELOW spot (floor) + magnet ABOVE (target)
            supports = (
                support is not None and spot > support and
                magnet is not None and magnet > spot
            )
            summary = (
                f"GEX support ${support:.2f} below + magnet ${magnet:.2f} above"
                if supports else
                f"GEX positioning doesn't favor bullish (support: {support}, magnet: {magnet})"
            )
        else:  # PUT
            supports = (
                resistance is not None and spot < resistance and
                magnet is not None and magnet < spot
            )
            summary = (
                f"GEX resistance ${resistance:.2f} above + magnet ${magnet:.2f} below"
                if supports else
                f"GEX positioning doesn't favor bearish (resistance: {resistance}, magnet: {magnet})"
            )

        return {
            "supports":   supports,
            "summary":    summary,
            "magnet":     magnet,
            "support":    support,
            "resistance": resistance,
        }


# ── Whale Flow Analyzer ───────────────────────────────────────────────────────

class WhaleAnalyzer:
    """
    Checks for $500K+ whale flow in the direction of the trade.
    Looks at current chain (today's volume + premium).
    """

    def analyze(self, chain: pd.DataFrame, spot: float, direction: str,
                threshold: float = WHALE_THRESHOLD) -> dict:
        """
        Returns: {
          "supports":  bool,
          "summary":   str,
          "count":     int,
          "premium":   float,  # total whale premium in direction
        }
        """
        if chain.empty:
            return {"supports": False, "summary": "No flow data", "count": 0, "premium": 0}

        df = chain.copy()
        df["volume"] = df["volume"].fillna(0)
        df["mid"]    = df["mid"].fillna(0)
        df["premium"] = df["mid"] * df["volume"] * 100

        # Filter to strikes within 10% of spot
        df = df[abs(df["strike"] - spot) / spot <= 0.10]

        # Direction-aligned whales
        target_type = "call" if direction == "CALL" else "put"
        whales = df[
            (df["option_type"] == target_type) &
            (df["premium"] >= threshold) &
            (df["volume"] > 0)
        ]

        if whales.empty:
            return {
                "supports": False,
                "summary":  f"No ${threshold/1000:.0f}K+ {direction.lower()} flow detected",
                "count":    0,
                "premium":  0,
            }

        total_prem = float(whales["premium"].sum())
        top_whale  = whales.nlargest(1, "premium").iloc[0]

        summary = (
            f"{len(whales)} whale trade(s), "
            f"top: {target_type.upper()} ${top_whale['strike']:.0f} "
            f"{top_whale['expiry']} · ${top_whale['premium']:,.0f}"
        )

        return {
            "supports": True,
            "summary":  summary,
            "count":    len(whales),
            "premium":  total_prem,
        }


# ── SwingConfluence Scanner (Main Engine) ─────────────────────────────────────

class SwingScanner:
    """The main scanner. Finds 3-of-3 confluence setups."""

    def __init__(self, alpaca_key=None, alpaca_secret=None):
        self.alpaca = AlpacaClient(alpaca_key, alpaca_secret)
        self.gex    = GEXAnalyzer()
        self.whale  = WhaleAnalyzer()

    def diagnose_ticker(self, ticker: str) -> dict:
        """
        Diagnostic scan — returns gate-by-gate results without 3-of-3 filter.
        Used to identify which gate is blocking setups.

        Returns: {
          "ticker":            str,
          "spot":              float | None,
          "daily_patterns":    List[PatternSignal],  # raw detections
          "h4_patterns":       List[PatternSignal],
          "directions_tried":  List[str],            # ["CALL", "PUT"] or subset
          "results":           List[dict]            # per direction: gate results
        }

        Each result has:
          "direction":        "CALL" or "PUT"
          "pattern_count":    int
          "gex_supports":     bool
          "gex_summary":      str
          "whale_supports":   bool
          "whale_summary":    str
          "whale_count":      int
          "whale_top_premium": float
          "blocked_at":       "patterns" | "gex" | "whales" | "passed"
        """
        diag = {
            "ticker":           ticker,
            "spot":             None,
            "daily_patterns":   [],
            "h4_patterns":      [],
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
            df_4h    = self.alpaca.get_bars(ticker, "4Hour", days=60)

            if df_daily.empty:
                diag["error"] = "No daily bars"
                return diag

            diag["daily_patterns"] = PatternDetector(ticker, "1D").detect_all(df_daily)
            diag["h4_patterns"]    = PatternDetector(ticker, "4H").detect_all(df_4h) if not df_4h.empty else []

            all_patterns = diag["daily_patterns"] + diag["h4_patterns"]
            calls = [p for p in all_patterns if p.direction == "CALL"]
            puts  = [p for p in all_patterns if p.direction == "PUT"]

            # Fetch chain once for both directions
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

                # Gate 2: GEX
                gex_result = self.gex.analyze(chain, spot, direction)
                result["gex_supports"] = gex_result["supports"]
                result["gex_summary"]  = gex_result["summary"]

                # Gate 3: Whales — compute regardless of GEX for diagnostic purposes
                whale_result = self.whale.analyze(chain, spot, direction)
                result["whale_supports"]    = whale_result["supports"]
                result["whale_summary"]     = whale_result["summary"]
                result["whale_count"]       = whale_result["count"]
                result["whale_top_premium"] = whale_result["premium"]

                # Determine where it blocked
                if not gex_result["supports"]:
                    result["blocked_at"] = "gex"
                elif not whale_result["supports"]:
                    result["blocked_at"] = "whales"
                else:
                    result["blocked_at"] = "passed"

                diag["results"].append(result)

        except Exception as e:
            diag["error"] = f"{type(e).__name__}: {e}"
            logger.error(f"{ticker} diagnose: {diag['error']}")

        return diag

    def diagnose_all(self, tickers: List[str] = None,
                     progress_cb=None) -> List[dict]:
        """Run diagnostic scan on all tickers, return raw gate results."""
        if tickers is None:
            tickers = ALL_TICKERS

        results = []
        total   = len(tickers)

        for i, ticker in enumerate(tickers):
            if progress_cb:
                progress_cb(i / total, f"Diagnosing {ticker} ({i+1}/{total})...")

            diag = self.diagnose_ticker(ticker)
            results.append(diag)

        return results

    def scan_ticker(self, ticker: str) -> List[ConfluenceSetup]:
        """Scan one ticker for confluence setups. Returns 0+ setups."""
        try:
            # Get spot
            spot = self.alpaca.get_spot(ticker)
            if spot == 0:
                logger.warning(f"{ticker}: no spot price")
                return []

            # Get daily + 4H bars
            df_daily = self.alpaca.get_bars(ticker, "1Day", days=250)
            df_4h    = self.alpaca.get_bars(ticker, "4Hour", days=60)

            if df_daily.empty:
                return []

            # Detect patterns on both timeframes
            daily_patterns = PatternDetector(ticker, "1D").detect_all(df_daily)
            h4_patterns    = PatternDetector(ticker, "4H").detect_all(df_4h) if not df_4h.empty else []

            all_patterns = daily_patterns + h4_patterns
            if not all_patterns:
                return []

            # Group patterns by direction
            calls = [p for p in all_patterns if p.direction == "CALL"]
            puts  = [p for p in all_patterns if p.direction == "PUT"]

            setups = []

            # Try CALL direction if we have call patterns
            if calls:
                setup = self._build_setup(ticker, spot, "CALL", calls)
                if setup: setups.append(setup)

            # Try PUT direction
            if puts:
                setup = self._build_setup(ticker, spot, "PUT", puts)
                if setup: setups.append(setup)

            return setups

        except Exception as e:
            logger.error(f"{ticker} scan error: {e}")
            return []

    def _build_setup(self, ticker: str, spot: float, direction: str,
                      patterns: List[PatternSignal]) -> Optional[ConfluenceSetup]:
        """Build a setup if all 3 confluence factors align."""

        # Factor 1: Technical patterns (already validated)
        has_daily = any(p.timeframe == "1D" for p in patterns)
        has_4h    = any(p.timeframe == "4H" for p in patterns)

        # Factor 2 & 3 need options chain
        chain = self.alpaca.get_options_chain(ticker, days_out=14)
        if chain.empty:
            return None

        # Factor 2: GEX
        gex_result = self.gex.analyze(chain, spot, direction)
        if not gex_result["supports"]:
            return None  # 3-of-3 not met

        # Factor 3: Whales
        whale_result = self.whale.analyze(chain, spot, direction)
        if not whale_result["supports"]:
            return None  # 3-of-3 not met

        # ✅ All 3 factors align — build the setup

        # Conviction scoring
        if has_daily and has_4h:
            conviction = 6  # MAX
        elif has_daily:
            conviction = 5  # HIGH
        else:
            conviction = 4  # MEDIUM-HIGH (4H only)

        # Trade plan
        plan = self._build_trade_plan(spot, direction, patterns, gex_result, chain)

        return ConfluenceSetup(
            ticker=ticker, direction=direction, conviction=conviction, spot=spot,
            patterns=patterns, has_daily=has_daily, has_4h=has_4h,
            gex_summary=gex_result["summary"],
            nearest_magnet=gex_result["magnet"],
            support_level=gex_result["support"],
            resistance_level=gex_result["resistance"],
            whale_summary=whale_result["summary"],
            whale_count=whale_result["count"],
            whale_premium=whale_result["premium"],
            **plan,
        )

    def _build_trade_plan(self, spot: float, direction: str,
                          patterns: List[PatternSignal],
                          gex_result: dict, chain: pd.DataFrame) -> dict:
        """Build entry/stop/target/strike based on technical + GEX + OI liquidity."""

        # Find liquid OTM strike — prefer 5-10 DTE, require OI for tight spreads
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

        # Filter to OTM only
        if direction == "CALL":
            df_otm = df[df["strike"] > spot].copy()
        else:
            df_otm = df[df["strike"] < spot].copy()

        # Tradeable basics: positive bid, sane spread
        df_otm = df_otm[
            (df_otm["bid"] > 0.05) &
            (df_otm["spread_pct"] < 15)  # reject spreads > 15% of mid
        ]

        strike, expiry, picked_oi, oi_quality = spot, "", 0, "unknown"

        if not df_otm.empty:
            # Tier 1: PREFERRED — OI >= 1000 + spread <= 5%
            tier1 = df_otm[
                (df_otm["open_interest"] >= PREFERRED_OI_THRESHOLD) &
                (df_otm["spread_pct"] <= 5)
            ]
            # Tier 2: ACCEPTABLE — OI >= 500 + spread <= 10%
            tier2 = df_otm[
                (df_otm["open_interest"] >= MIN_OI_THRESHOLD) &
                (df_otm["spread_pct"] <= 10)
            ]
            # Tier 3: ANY — fall back to bid > 0.05 (already filtered)

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

        # Entry/stop/target from technical levels
        primary_pattern = patterns[0]
        level           = primary_pattern.level

        if direction == "CALL":
            entry_above = float(spot + 0.005 * spot)
            stop_below  = float(min(level * 0.995, spot * 0.985))
            target      = float(gex_result["magnet"]) if gex_result["magnet"] and gex_result["magnet"] > spot else float(spot * 1.03)
        else:
            entry_above = float(spot - 0.005 * spot)
            stop_below  = float(max(level * 1.005, spot * 1.015))
            target      = float(gex_result["magnet"]) if gex_result["magnet"] and gex_result["magnet"] < spot else float(spot * 0.97)

        # Risk/reward
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
        """Scan all tickers and return setups (sorted by conviction)."""
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

        # Sort: conviction first (6→4), then ticker
        all_setups.sort(key=lambda s: (-s.conviction, s.ticker))
        return all_setups
