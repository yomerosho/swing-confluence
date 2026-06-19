"""
morning_briefing.py — SwingConfluence Morning Briefing Tab
Drop this file into your SwingConfluence project root.
In your main app.py, add:
    from morning_briefing import render_morning_briefing
    # then in your tab/page structure:
    render_morning_briefing()

Requirements (already in your stack):
    pip install alpaca-trade-api streamlit pandas
"""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import time

# ── Try importing Alpaca ─────────────────────────────────────────────────────
try:
    from alpaca.data.historical import StockHistoricalDataClient, OptionHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest, OptionChainRequest
    from alpaca.data.timeframe import TimeFrame
    ALPACA_V2 = True
except ImportError:
    try:
        import alpaca_trade_api as tradeapi
        ALPACA_V2 = False
    except ImportError:
        ALPACA_V2 = None


# ── Watchlist ────────────────────────────────────────────────────────────────
CORE_20 = [
    "AMD", "NVDA", "TSLA", "AMZN", "META",
    "GOOGL", "MSFT", "AAPL", "NFLX", "CRM",
    "COIN", "HOOD", "MSTR", "PLTR", "ARM",
    "UBER", "SHOP", "SQ", "SOFI", "PYPL",
]

INDICES_ETFS = ["SPY", "QQQ", "IWM"]
ALL_TICKERS  = INDICES_ETFS + CORE_20


# ── Strat candle classifier ──────────────────────────────────────────────────
def classify_strat(prev_high, prev_low, curr_high, curr_low):
    """Return Strat candle type: 1 (inside), 2U (up), 2D (down), 3 (outside)."""
    higher_high = curr_high > prev_high
    lower_low   = curr_low  < prev_low

    if higher_high and lower_low:
        return "3", "🔴"          # outside bar — trend exhaustion / reversal risk
    elif higher_high and not lower_low:
        return "2U", "🟢"         # up — bullish continuation candidate
    elif lower_low and not higher_high:
        return "2D", "🔴"         # down — bearish continuation candidate
    else:
        return "1", "🟡"          # inside bar — compression / coiling


# ── IVR calculation ──────────────────────────────────────────────────────────
def calculate_ivr(current_iv, iv_52w_low, iv_52w_high):
    """IV Rank: where current IV sits in its 52-week range (0–100)."""
    if iv_52w_high == iv_52w_low:
        return 50
    return round((current_iv - iv_52w_low) / (iv_52w_high - iv_52w_low) * 100, 1)


def ivr_label(ivr):
    if ivr is None:
        return "—", "⬜"
    if ivr < 30:
        return f"{ivr}", "🟢"   # cheap premium
    elif ivr < 60:
        return f"{ivr}", "🟡"   # normal
    else:
        return f"{ivr}", "🔴"   # expensive


# ── Key level proximity ───────────────────────────────────────────────────────
def proximity_score(price, levels, threshold_pct=0.003):
    """Return 1 if price is within threshold_pct of ANY key level."""
    for lvl in levels:
        if lvl and abs(price - lvl) / lvl <= threshold_pct:
            return 1
    return 0


# ── Confluence score ─────────────────────────────────────────────────────────
def confluence_score(strat_type, near_key_level, ivr):
    score = 0
    # Strat signal quality
    if strat_type in ("2U", "2D"):
        score += 1
    elif strat_type == "1":
        score += 0.5   # inside bar still tradeable at key level
    # Near key level
    score += near_key_level   # 0 or 1
    # IVR (cheap premium = better risk/reward for buying)
    if ivr is not None and ivr < 35:
        score += 1
    return round(score, 1)


# ── Alpaca data fetch ─────────────────────────────────────────────────────────
@st.cache_data(ttl=300)   # cache 5 min
def fetch_bars_v2(api_key, api_secret, tickers, days_back=252):
    """Fetch daily bars using alpaca-py (v2 SDK)."""
    client = StockHistoricalDataClient(api_key, api_secret)
    end   = datetime.now()
    start = end - timedelta(days=days_back + 5)
    req   = StockBarsRequest(
        symbol_or_symbols=tickers,
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
    )
    bars = client.get_stock_bars(req).df
    return bars


@st.cache_data(ttl=300)
def fetch_bars_v1(api_key, api_secret, base_url, tickers, days_back=252):
    """Fetch daily bars using legacy alpaca_trade_api (v1 SDK)."""
    api  = tradeapi.REST(api_key, api_secret, base_url, api_version='v2')
    end  = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=days_back + 5)).strftime('%Y-%m-%d')
    result = {}
    for t in tickers:
        try:
            barset = api.get_bars(t, tradeapi.rest.TimeFrame.Day, start, end, limit=days_back+5).df
            result[t] = barset
        except Exception:
            result[t] = None
    return result


# ── Per-ticker analysis ───────────────────────────────────────────────────────
def analyze_ticker(symbol, bars_df):
    """
    bars_df: DataFrame with columns [open, high, low, close, volume]
             indexed by datetime. Must have at least 2 rows.
    Returns dict of metrics.
    """
    try:
        df = bars_df.copy().sort_index()
        if len(df) < 2:
            return None

        # Last two daily candles
        prev  = df.iloc[-2]
        curr  = df.iloc[-1]
        price = curr["close"]

        # Strat candle type (based on yesterday vs day before)
        strat_type, strat_emoji = classify_strat(
            prev["high"], prev["low"],
            curr["high"], curr["low"]
        )

        # Key levels
        pdh = prev["high"]
        pdl = prev["low"]
        week_slice = df.last("5B") if len(df) >= 5 else df
        pwh = week_slice["high"].max()
        pwl = week_slice["low"].min()

        near_key = proximity_score(price, [pdh, pdl, pwh, pwl])

        # IVR from close price proxy (we use 52-week high/low of IV as proxy)
        # Real IV needs options chain — use a simplified historical vol proxy here
        returns      = df["close"].pct_change().dropna()
        hv_current   = returns.tail(20).std() * (252 ** 0.5) * 100   # annualised %
        hv_52w_high  = returns.rolling(20).std().max() * (252 ** 0.5) * 100
        hv_52w_low   = returns.rolling(20).std().min() * (252 ** 0.5) * 100
        ivr          = calculate_ivr(hv_current, hv_52w_low, hv_52w_high)

        score = confluence_score(strat_type, near_key, ivr)

        return {
            "Symbol":         symbol,
            "Price":          round(price, 2),
            "Strat":          f"{strat_emoji} {strat_type}",
            "PDH":            round(pdh, 2),
            "PDL":            round(pdl, 2),
            "Near Key Level": "✅" if near_key else "—",
            "HV20 Rank":      ivr,
            "_ivr_label":     ivr_label(ivr),
            "Score":          score,
        }
    except Exception as e:
        return {"Symbol": symbol, "Price": "—", "Strat": "err", "Score": 0, "_err": str(e)}


# ── Main render function ──────────────────────────────────────────────────────
def render_morning_briefing():
    st.markdown("## 🌅 Morning Briefing")
    st.caption("Ranks your CORE_20 + ETFs by confluence score. Run at 9:25 AM before open.")

    # ── Credentials ─────────────────────────────────────────────────────────
    with st.expander("⚙️ Alpaca API Keys", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            api_key = st.text_input("API Key", type="password",
                                    value=st.session_state.get("alpaca_key", ""))
        with col2:
            api_secret = st.text_input("API Secret", type="password",
                                       value=st.session_state.get("alpaca_secret", ""))
        base_url = st.text_input("Base URL", value="https://paper-api.alpaca.markets",
                                 help="Use https://api.alpaca.markets for live")

        if st.button("Save Keys"):
            st.session_state["alpaca_key"]    = api_key
            st.session_state["alpaca_secret"] = api_secret
            st.session_state["alpaca_url"]    = base_url
            st.success("Keys saved for this session.")

    api_key    = st.session_state.get("alpaca_key", "")
    api_secret = st.session_state.get("alpaca_secret", "")
    base_url   = st.session_state.get("alpaca_url", "https://paper-api.alpaca.markets")

    # ── Run scan ─────────────────────────────────────────────────────────────
    run_col, time_col = st.columns([1, 3])
    with run_col:
        run_scan = st.button("🔍 Run Scan", type="primary", use_container_width=True)
    with time_col:
        st.caption(f"Last run: {st.session_state.get('last_run', 'never')}")

    if run_scan:
        if not api_key or not api_secret:
            st.warning("Enter your Alpaca API keys above first.")
            return

        results = []
        progress = st.progress(0, text="Fetching data…")

        try:
            # ── Fetch bars ───────────────────────────────────────────────────
            if ALPACA_V2 is True:
                with st.spinner("Pulling daily bars from Alpaca…"):
                    bars = fetch_bars_v2(api_key, api_secret, ALL_TICKERS)

                for i, symbol in enumerate(ALL_TICKERS):
                    progress.progress((i + 1) / len(ALL_TICKERS), text=f"Analyzing {symbol}…")
                    try:
                        # alpaca-py returns MultiIndex (symbol, timestamp)
                        sym_bars = bars.xs(symbol, level=0) if symbol in bars.index.get_level_values(0) else None
                        if sym_bars is not None and len(sym_bars) >= 2:
                            row = analyze_ticker(symbol, sym_bars)
                            if row:
                                results.append(row)
                    except Exception as e:
                        results.append({"Symbol": symbol, "Score": 0, "Strat": "—",
                                        "Price": "—", "Near Key Level": "—",
                                        "HV20 Rank": None, "_err": str(e)})
                    time.sleep(0.05)

            elif ALPACA_V2 is False:
                with st.spinner("Pulling daily bars from Alpaca (v1 SDK)…"):
                    bars_dict = fetch_bars_v1(api_key, api_secret, base_url, ALL_TICKERS)

                for i, symbol in enumerate(ALL_TICKERS):
                    progress.progress((i + 1) / len(ALL_TICKERS), text=f"Analyzing {symbol}…")
                    sym_bars = bars_dict.get(symbol)
                    if sym_bars is not None and len(sym_bars) >= 2:
                        row = analyze_ticker(symbol, sym_bars)
                        if row:
                            results.append(row)
                    time.sleep(0.05)

            else:
                st.error("Alpaca SDK not installed. Run: pip install alpaca-py  OR  pip install alpaca-trade-api")
                return

        except Exception as e:
            st.error(f"Data fetch failed: {e}")
            return

        progress.empty()
        st.session_state["last_run"]      = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.session_state["briefing_data"] = results

    # ── Display results ───────────────────────────────────────────────────────
    results = st.session_state.get("briefing_data", [])
    if not results:
        st.info("Hit **Run Scan** to generate today's briefing.")
        return

    df = pd.DataFrame(results)

    # Clean up internal columns
    df = df.drop(columns=[c for c in ["_ivr_label", "_err"] if c in df.columns], errors="ignore")

    # Sort by Score desc
    df = df.sort_values("Score", ascending=False).reset_index(drop=True)

    # ── Top picks ────────────────────────────────────────────────────────────
    top = df[df["Score"] >= 2].head(5)

    st.markdown("### 🎯 Top Setups Today")
    if top.empty:
        st.info("No tickers with confluence score ≥ 2 right now. Check back closer to open.")
    else:
        for _, row in top.iterrows():
            score_bar = "█" * int(row["Score"] * 2)
            st.markdown(
                f"**{row['Symbol']}** &nbsp; `{row.get('Strat','—')}` &nbsp; "
                f"Near Key: {row.get('Near Key Level','—')} &nbsp; "
                f"HV Rank: {row.get('HV20 Rank','—')} &nbsp; "
                f"Score: **{row['Score']}** `{score_bar}`"
            )

    st.divider()

    # ── Full table ────────────────────────────────────────────────────────────
    st.markdown("### 📋 Full Watchlist Scan")

    # Color score column
    def color_score(val):
        if val >= 2:
            return "background-color: #1a3a1a; color: #00ff88"
        elif val >= 1:
            return "background-color: #2a2a0a; color: #ffdd00"
        return ""

    display_cols = ["Symbol", "Price", "Strat", "PDH", "PDL", "Near Key Level", "HV20 Rank", "Score"]
    display_df   = df[[c for c in display_cols if c in df.columns]]

    st.dataframe(
        display_df.style.applymap(color_score, subset=["Score"]),
        use_container_width=True,
        hide_index=True,
    )

    # ── Score legend ─────────────────────────────────────────────────────────
    with st.expander("📖 How scores are calculated"):
        st.markdown("""
| Signal | Points |
|---|---|
| Strat 2U or 2D (directional candle) | +1 |
| Strat 1 (inside/coiling candle) | +0.5 |
| Price within 0.3% of PDH, PDL, PWH, or PWL | +1 |
| HV20 Rank < 35 (cheap premium for buyers) | +1 |

**Max score: 3.0** — trade only names scoring ≥ 2.

**Strat types:** 1 = Inside bar (compression), 2U = Higher high only (bullish), 2D = Lower low only (bearish), 3 = Outside bar (reversal risk).

**HV20 Rank** = where current 20-day historical volatility sits in its 52-week range. Low rank = relatively cheap options premium.
        """)

    # ── Export ───────────────────────────────────────────────────────────────
    csv = display_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Export to CSV",
        data=csv,
        file_name=f"morning_briefing_{datetime.now().strftime('%Y%m%d')}.csv",
        mime="text/csv",
    )
