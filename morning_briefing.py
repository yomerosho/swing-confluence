"""
morning_briefing.py — SwingConfluence Morning Briefing Tab
Uses plain requests (same pattern as swing_scanner.py) — no extra SDK needed.
"""

import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timedelta
import time

DATA_URL = "https://data.alpaca.markets"

from swing_scanner import ALL_TICKERS


def _headers(api_key, api_secret):
    return {
        "APCA-API-KEY-ID":     api_key,
        "APCA-API-SECRET-KEY": api_secret,
        "accept":              "application/json",
    }


def get_bars(api_key, api_secret, ticker, days=252):
    """Fetch daily bars via raw requests, mirroring swing_scanner.AlpacaClient.get_bars."""
    for feed in ["sip", "iex"]:
        try:
            end   = datetime.now()
            start = end - timedelta(days=days + 5)
            url   = f"{DATA_URL}/v2/stocks/{ticker}/bars"
            params = {
                "timeframe":  "1Day",
                "start":      start.strftime("%Y-%m-%dT00:00:00Z"),
                "end":        end.strftime("%Y-%m-%dT23:59:59Z"),
                "limit":      1000,
                "adjustment": "raw",
                "feed":       feed,
                "sort":       "asc",
            }

            all_bars, page_token = [], None
            for _ in range(10):
                if page_token:
                    params["page_token"] = page_token
                r = requests.get(url, headers=_headers(api_key, api_secret),
                                 params=params, timeout=20)
                if r.status_code in (403, 401):
                    break
                if r.status_code != 200:
                    break
                payload = r.json()
                all_bars.extend(payload.get("bars", []) or [])
                page_token = payload.get("next_page_token")
                if not page_token:
                    break

            if not all_bars:
                continue

            df = pd.DataFrame(all_bars)
            df["t"] = pd.to_datetime(df["t"])
            df = df.set_index("t").sort_index()
            df = df.rename(columns={"o": "open", "h": "high", "l": "low",
                                    "c": "close", "v": "volume"})
            return df

        except Exception:
            continue

    return None


# ── Strat candle classifier ──────────────────────────────────────────────────
def classify_strat(prev_high, prev_low, curr_high, curr_low):
    higher_high = curr_high > prev_high
    lower_low   = curr_low  < prev_low
    if higher_high and lower_low:
        return "3",  "🔴"
    elif higher_high:
        return "2U", "🟢"
    elif lower_low:
        return "2D", "🔴"
    else:
        return "1",  "🟡"


# ── HV Rank (IV proxy) ───────────────────────────────────────────────────────
def hv_rank(df):
    try:
        returns     = df["close"].pct_change().dropna()
        hv_current  = returns.tail(20).std() * (252 ** 0.5) * 100
        rolling_hv  = returns.rolling(20).std() * (252 ** 0.5) * 100
        hv_high     = rolling_hv.max()
        hv_low      = rolling_hv.min()
        if hv_high == hv_low:
            return 50
        return round((hv_current - hv_low) / (hv_high - hv_low) * 100, 1)
    except Exception:
        return None


# ── Key level proximity ───────────────────────────────────────────────────────
def near_key_level(price, levels, threshold_pct=0.003):
    for lvl in levels:
        if lvl and abs(price - lvl) / lvl <= threshold_pct:
            return True
    return False


# ── Per-ticker analysis ───────────────────────────────────────────────────────
def analyze_ticker(symbol, df):
    try:
        if df is None or len(df) < 10:
            return None

        df = df.copy().sort_index()
        prev  = df.iloc[-2]
        curr  = df.iloc[-1]
        price = curr["close"]

        strat_type, strat_emoji = classify_strat(
            prev["high"], prev["low"], curr["high"], curr["low"]
        )

        pdh = prev["high"]
        pdl = prev["low"]
        pwh = df.tail(5)["high"].max()
        pwl = df.tail(5)["low"].min()

        near = near_key_level(price, [pdh, pdl, pwh, pwl])
        hvr  = hv_rank(df)

        # Score
        score = 0
        if strat_type in ("2U", "2D"):
            score += 1
        elif strat_type == "1":
            score += 0.5
        if near:
            score += 1
        if hvr is not None and hvr < 35:
            score += 1

        return {
            "Symbol":    symbol,
            "Price":     round(price, 2),
            "Strat":     f"{strat_emoji} {strat_type}",
            "PDH":       round(pdh, 2),
            "PDL":       round(pdl, 2),
            "Near Key":  "✅" if near else "—",
            "HV Rank":   hvr if hvr is not None else "—",
            "Score":     score,
        }
    except Exception:
        return None


# ── Main render ───────────────────────────────────────────────────────────────
def render_morning_briefing(api_key="", api_secret="", base_url="https://api.alpaca.markets"):

    st.markdown("## 🌅 Morning Briefing")
    st.caption(f"Ranks all {len(ALL_TICKERS)} tickers (INDICES + MEGA_CAPS + SWING_NAMES) by confluence score. Run at 9:25 AM before open.")

    run_col, time_col = st.columns([1, 3])
    with run_col:
        run_scan = st.button("🔍 Run Scan", type="primary", use_container_width=True)
    with time_col:
        st.caption(f"Last run: {st.session_state.get('mb_last_run', 'never')}")

    if run_scan:
        if not api_key or not api_secret:
            st.warning("⚠️ Alpaca keys not configured. Add them to your Streamlit secrets under [alpaca].")
            return

        results  = []
        progress = st.progress(0, text="Fetching data…")

        for i, symbol in enumerate(ALL_TICKERS):
            progress.progress((i + 1) / len(ALL_TICKERS), text=f"Analyzing {symbol}…")
            df  = get_bars(api_key, api_secret, symbol, days=60)
            row = analyze_ticker(symbol, df)
            if row:
                results.append(row)
            time.sleep(0.05)

        progress.empty()
        st.session_state["mb_last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.session_state["mb_data"]     = results

    results = st.session_state.get("mb_data", [])
    if not results:
        st.info("Hit **Run Scan** to generate today's briefing.")
        return

    df_out = pd.DataFrame(results).sort_values("Score", ascending=False).reset_index(drop=True)

    # ── Top picks ─────────────────────────────────────────────────────────────
    top = df_out[df_out["Score"] >= 2].head(5)
    st.markdown("### 🎯 Top Setups Today")
    if top.empty:
        st.info("No tickers with confluence score ≥ 2 right now.")
    else:
        for _, row in top.iterrows():
            bar = "█" * int(row["Score"] * 2)
            st.markdown(
                f"**{row['Symbol']}** &nbsp; `{row['Strat']}` &nbsp; "
                f"Near Key: {row['Near Key']} &nbsp; "
                f"HV Rank: {row['HV Rank']} &nbsp; "
                f"Score: **{row['Score']}** `{bar}`"
            )

    st.divider()

    # ── Full table ─────────────────────────────────────────────────────────────
    st.markdown("### 📋 Full Watchlist Scan")

    def color_score(val):
        try:
            if float(val) >= 2:   return "background-color:#1a3a1a;color:#00ff88"
            elif float(val) >= 1: return "background-color:#2a2a0a;color:#ffdd00"
        except: pass
        return ""

    st.dataframe(
        df_out.style.applymap(color_score, subset=["Score"]),
        use_container_width=True,
        hide_index=True,
    )

    # ── Score legend ──────────────────────────────────────────────────────────
    with st.expander("📖 How scores work"):
        st.markdown("""
| Signal | Points |
|---|---|
| Strat 2U or 2D (directional) | +1 |
| Strat 1 (inside/coiling) | +0.5 |
| Price within 0.3% of PDH/PDL/PWH/PWL | +1 |
| HV Rank < 35 (cheap premium) | +1 |

**Max: 3.0** — focus on scores ≥ 2.
**1** = inside bar, **2U** = bullish, **2D** = bearish, **3** = outside/reversal risk.
        """)

    csv = df_out.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Export CSV",
        data=csv,
        file_name=f"morning_briefing_{datetime.now().strftime('%Y%m%d')}.csv",
        mime="text/csv",
    )
