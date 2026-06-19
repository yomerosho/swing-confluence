"""
morning_briefing.py — SwingConfluence Morning Briefing Tab
Uses plain requests (same pattern as swing_scanner.py) — no extra SDK needed.
Real IV Rank pulled from Alpaca options snapshots (indicative feed).
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


# ── Daily bars ────────────────────────────────────────────────────────────────
def get_bars(api_key, api_secret, ticker, days=252):
    """Fetch daily bars — mirrors swing_scanner.AlpacaClient.get_bars."""
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
                if r.status_code in (401, 403):
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
            df = df.rename(columns={"o": "open", "h": "high",
                                    "l": "low",  "c": "close", "v": "volume"})
            return df
        except Exception:
            continue
    return None


# ── Real ATM IV from options snapshot ────────────────────────────────────────
def get_atm_iv(api_key, api_secret, ticker, spot_price):
    """
    Pull the nearest-expiry ATM option IV from Alpaca's options snapshot.
    Returns IV as a decimal (e.g. 0.35 = 35%) or None on failure.
    Mirrors the approach in swing_scanner.AlpacaClient.get_options_chain.
    """
    try:
        today  = datetime.now().date()
        end_dt = today + timedelta(days=14)
        url    = f"{DATA_URL}/v1beta1/options/snapshots/{ticker}"
        params = {
            "limit":                 250,
            "feed":                  "indicative",
            "expiration_date_gte":   today.isoformat(),
            "expiration_date_lte":   end_dt.isoformat(),
        }

        r = requests.get(url, headers=_headers(api_key, api_secret),
                         params=params, timeout=15)
        if r.status_code != 200:
            return None

        snapshots = r.json().get("snapshots", {})
        if not snapshots:
            return None

        # Find ATM contract — closest strike to spot across calls & puts
        best_iv   = None
        best_dist = float("inf")

        for symbol, snap in snapshots.items():
            iv = snap.get("impliedVolatility")
            if not iv or iv <= 0:
                continue
            # Parse strike from OCC symbol (e.g. AAPL250620C00200000)
            try:
                strike = int(symbol[-8:]) / 1000
            except Exception:
                continue
            dist = abs(strike - spot_price)
            if dist < best_dist:
                best_dist = dist
                best_iv   = iv

        return best_iv  # decimal form, e.g. 0.32
    except Exception:
        return None


# ── IV Rank from 52-week HV as baseline (session-cached per ticker) ──────────
def compute_iv_rank(current_iv, ticker):
    """
    IV Rank = (current_iv - 52w_low) / (52w_high - 52w_low) * 100
    We cache IV samples in session state across runs to build the range.
    On first run we only have today's IV, so we fall back to HV rank.
    """
    cache_key = f"iv_history_{ticker}"
    history   = st.session_state.get(cache_key, [])

    # Add today's reading
    history.append(current_iv)
    # Keep last 252 daily readings max
    history = history[-252:]
    st.session_state[cache_key] = history

    if len(history) < 2:
        return None  # not enough history yet

    iv_low  = min(history)
    iv_high = max(history)
    if iv_high == iv_low:
        return 50.0
    return round((current_iv - iv_low) / (iv_high - iv_low) * 100, 1)


# ── HV Rank fallback ──────────────────────────────────────────────────────────
def hv_rank(df):
    try:
        returns    = df["close"].pct_change().dropna()
        hv_current = returns.tail(20).std() * (252 ** 0.5) * 100
        rolling_hv = returns.rolling(20).std() * (252 ** 0.5) * 100
        hv_high    = rolling_hv.max()
        hv_low     = rolling_hv.min()
        if hv_high == hv_low:
            return 50
        return round((hv_current - hv_low) / (hv_high - hv_low) * 100, 1)
    except Exception:
        return None


# ── Strat candle classifier ───────────────────────────────────────────────────
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


# ── Key level proximity ───────────────────────────────────────────────────────
def near_key_level(price, levels, threshold_pct=0.003):
    for lvl in levels:
        if lvl and abs(price - lvl) / lvl <= threshold_pct:
            return True
    return False


# ── Per-ticker analysis ───────────────────────────────────────────────────────
def analyze_ticker(api_key, api_secret, symbol, df):
    try:
        if df is None or len(df) < 10:
            return None

        df    = df.copy().sort_index()
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

        # ── Real IV Rank ──────────────────────────────────────────────────────
        atm_iv   = get_atm_iv(api_key, api_secret, symbol, price)
        iv_source = "IV"

        if atm_iv and atm_iv > 0:
            ivr = compute_iv_rank(atm_iv, symbol)
            if ivr is None:
                # First run — show raw IV % instead of rank
                ivr       = round(atm_iv * 100, 1)
                iv_source = "IV%"   # flag that this is raw IV, not rank yet
        else:
            # Fallback to HV rank if options data unavailable
            ivr       = hv_rank(df)
            iv_source = "HV"

        # ── Score ─────────────────────────────────────────────────────────────
        score = 0
        if strat_type in ("2U", "2D"):
            score += 1
        elif strat_type == "1":
            score += 0.5
        if near:
            score += 1
        # Use the rank for scoring regardless of source
        if ivr is not None and ivr < 35:
            score += 1

        # ── Bias ──────────────────────────────────────────────────────────────
        if strat_type == "2U":
            bias = "🟢 Bullish"
        elif strat_type == "2D":
            bias = "🔴 Bearish"
        elif strat_type == "1":
            bias = "🟡 Neutral"
        else:
            bias = "⚠️ Reversal"

        iv_display = f"{ivr}" if ivr is not None else "—"

        return {
            "Symbol":    symbol,
            "Price":     round(price, 2),
            "Bias":      bias,
            "Strat":     f"{strat_emoji} {strat_type}",
            "PDH":       round(pdh, 2),
            "PDL":       round(pdl, 2),
            "Near Key":  "✅" if near else "—",
            "IV Rank":   iv_display,
            "IV Source": iv_source,
            "Score":     score,
        }
    except Exception:
        return None


# ── Main render ───────────────────────────────────────────────────────────────
def render_morning_briefing(api_key="", api_secret="", base_url="https://api.alpaca.markets"):

    st.markdown("## 🌅 Morning Briefing")
    st.caption(
        f"Ranks all {len(ALL_TICKERS)} tickers (INDICES + MEGA_CAPS + SWING_NAMES) "
        f"by confluence score. Run at 9:25 AM before open."
    )

    run_col, time_col = st.columns([1, 3])
    with run_col:
        run_scan = st.button("🔍 Run Scan", type="primary", use_container_width=True)
    with time_col:
        st.caption(f"Last run: {st.session_state.get('mb_last_run', 'never')}")

    if run_scan:
        if not api_key or not api_secret:
            st.warning("⚠️ Alpaca keys not configured. Add them to Streamlit secrets under [alpaca].")
            return

        results  = []
        progress = st.progress(0, text="Fetching data…")

        for i, symbol in enumerate(ALL_TICKERS):
            progress.progress((i + 1) / len(ALL_TICKERS),
                              text=f"Analyzing {symbol}…")
            df  = get_bars(api_key, api_secret, symbol, days=60)
            row = analyze_ticker(api_key, api_secret, symbol, df)
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

    # ── IV source note ────────────────────────────────────────────────────────
    sources = df_out["IV Source"].value_counts().to_dict() if "IV Source" in df_out.columns else {}
    iv_notes = []
    if sources.get("IV"):
        iv_notes.append(f"✅ {sources['IV']} tickers: real IV Rank from Alpaca options")
    if sources.get("IV%"):
        iv_notes.append(f"🔄 {sources.get('IV%', 0)} tickers: raw IV% shown (rank builds after multiple runs)")
    if sources.get("HV"):
        iv_notes.append(f"⚠️ {sources['HV']} tickers: HV Rank fallback (no options data)")
    if iv_notes:
        st.caption(" · ".join(iv_notes))

    # ── Market bias summary ───────────────────────────────────────────────────
    total      = len(df_out)
    bullish_n  = len(df_out[df_out["Bias"] == "🟢 Bullish"])
    bearish_n  = len(df_out[df_out["Bias"] == "🔴 Bearish"])
    neutral_n  = len(df_out[df_out["Bias"] == "🟡 Neutral"])
    reversal_n = len(df_out[df_out["Bias"] == "⚠️ Reversal"])

    bull_pct = bullish_n / total * 100 if total else 0
    bear_pct = bearish_n / total * 100 if total else 0

    if bull_pct >= 60:
        overall       = "🟢 BULLISH"
        overall_color = "#00ff88"
        overall_note  = "Majority of watchlist showing higher highs. Favor CALL setups."
    elif bear_pct >= 60:
        overall       = "🔴 BEARISH"
        overall_color = "#ff4444"
        overall_note  = "Majority of watchlist showing lower lows. Favor PUT setups."
    elif bull_pct >= 45:
        overall       = "🟡 LEANING BULLISH"
        overall_color = "#ffdd00"
        overall_note  = "Slight bullish edge. Be selective — confirm at key levels."
    elif bear_pct >= 45:
        overall       = "🟡 LEANING BEARISH"
        overall_color = "#ffaa00"
        overall_note  = "Slight bearish edge. Be selective — confirm at key levels."
    else:
        overall       = "⚪ MIXED / CHOPPY"
        overall_color = "#8888aa"
        overall_note  = "No clear directional edge. Wait for confirmation at key levels."

    st.markdown("### 📊 Market Bias")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Overall Bias", overall)
    c2.metric("🟢 Bullish",   f"{bullish_n} ({bull_pct:.0f}%)")
    c3.metric("🔴 Bearish",   f"{bearish_n} ({bear_pct:.0f}%)")
    c4.metric("🟡 Neutral",   neutral_n)
    c5.metric("⚠️ Reversal",  reversal_n)

    st.markdown(
        f"<div style='background:#1a1f2e;border-left:4px solid {overall_color};"
        f"border-radius:8px;padding:12px 18px;margin:8px 0 16px 0;"
        f"font-family:monospace;font-size:0.9rem;color:{overall_color};font-weight:700;'>"
        f"{overall} — {overall_note}</div>",
        unsafe_allow_html=True
    )

    st.divider()

    # ── Top picks ─────────────────────────────────────────────────────────────
    top = df_out[df_out["Score"] >= 2].head(5)
    st.markdown("### 🎯 Top Setups Today")
    if top.empty:
        st.info("No tickers with confluence score ≥ 2 right now.")
    else:
        for _, row in top.iterrows():
            bar       = "█" * int(row["Score"] * 2)
            iv_label  = f"IV Rank: {row['IV Rank']}" if row.get("IV Source") == "IV" else \
                        f"IV%: {row['IV Rank']}"     if row.get("IV Source") == "IV%" else \
                        f"HV Rank: {row['IV Rank']}"
            st.markdown(
                f"**{row['Symbol']}** &nbsp; {row['Bias']} &nbsp; `{row['Strat']}` &nbsp; "
                f"Near Key: {row['Near Key']} &nbsp; "
                f"{iv_label} &nbsp; Score: **{row['Score']}** `{bar}`"
            )

    st.divider()

    # ── Full table ─────────────────────────────────────────────────────────────
    st.markdown("### 📋 Full Watchlist Scan")

    def color_score(val):
        try:
            if float(val) >= 2:   return "background-color:#1a3a1a;color:#00ff88"
            elif float(val) >= 1: return "background-color:#2a2a0a;color:#ffdd00"
        except Exception:
            pass
        return ""

    display_cols = ["Symbol", "Price", "Bias", "Strat", "PDH", "PDL",
                    "Near Key", "IV Rank", "IV Source", "Score"]
    st.dataframe(
        df_out[display_cols].style.applymap(color_score, subset=["Score"]),
        use_container_width=True,
        hide_index=True,
    )

    # ── Legend ────────────────────────────────────────────────────────────────
    with st.expander("📖 How scores work"):
        st.markdown("""
| Signal | Points |
|---|---|
| Strat 2U or 2D (directional candle) | +1 |
| Strat 1 (inside/coiling candle) | +0.5 |
| Price within 0.3% of PDH/PDL/PWH/PWL | +1 |
| IV Rank < 35 (cheap premium to buy) | +1 |

**Max: 3.0** — focus on scores ≥ 2.

**IV Source column:**
- `IV` = real IV Rank from Alpaca options snapshot ✅
- `IV%` = raw implied volatility % (rank builds after multiple daily runs)
- `HV` = historical volatility rank fallback (options data unavailable)

**Strat types:** 1 = inside bar, 2U = bullish, 2D = bearish, 3 = outside/reversal risk.
        """)

    csv = df_out.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Export CSV",
        data=csv,
        file_name=f"morning_briefing_{datetime.now().strftime('%Y%m%d')}.csv",
        mime="text/csv",
    )
