"""
morning_briefing.py — SwingConfluence Morning Briefing Tab
- Real IV Rank from Alpaca options snapshots
- Full Strat analysis: 3-candle combo, FTFC (Daily + Weekly + Monthly)
- Uses plain requests + swing_patterns.StratDetector (no extra SDK)
"""

import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timedelta
import time

DATA_URL = "https://data.alpaca.markets"

from swing_scanner import ALL_TICKERS
from swing_patterns import StratDetector, StratFTFC


def _headers(api_key, api_secret):
    return {
        "APCA-API-KEY-ID":     api_key,
        "APCA-API-SECRET-KEY": api_secret,
        "accept":              "application/json",
    }


# ── Bars fetch (capitalized columns to match StratDetector expectation) ───────
def get_bars(api_key, api_secret, ticker, timeframe="1Day", days=365):
    """
    Fetch OHLCV bars from Alpaca.
    Returns DataFrame with columns: Open, High, Low, Close, Volume
    (capitalized — matches swing_scanner.py and StratDetector requirements).
    """
    for feed in ["sip", "iex"]:
        try:
            end   = datetime.now()
            start = end - timedelta(days=days + 5)
            url   = f"{DATA_URL}/v2/stocks/{ticker}/bars"
            params = {
                "timeframe":  timeframe,
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
                payload    = r.json()
                all_bars.extend(payload.get("bars", []) or [])
                page_token = payload.get("next_page_token")
                if not page_token:
                    break

            if not all_bars:
                continue

            df = pd.DataFrame(all_bars)
            df["t"] = pd.to_datetime(df["t"])
            df = df.set_index("t").sort_index()
            df = df.rename(columns={
                "o": "Open", "h": "High",
                "l": "Low",  "c": "Close", "v": "Volume"
            })
            df = df[~df.index.duplicated(keep="last")]
            return df[["Open", "High", "Low", "Close", "Volume"]]

        except Exception:
            continue

    return None


def get_weekly_bars(api_key, api_secret, ticker):
    """Fetch weekly bars — resample from daily for accuracy."""
    df = get_bars(api_key, api_secret, ticker, timeframe="1Day", days=400)
    if df is None or df.empty:
        return None
    weekly = df.resample("W").agg({
        "Open":   "first",
        "High":   "max",
        "Low":    "min",
        "Close":  "last",
        "Volume": "sum",
    }).dropna()
    return weekly if len(weekly) >= 3 else None


def get_monthly_bars(api_key, api_secret, ticker):
    """Fetch monthly bars — resample from daily."""
    df = get_bars(api_key, api_secret, ticker, timeframe="1Day", days=800)
    if df is None or df.empty:
        return None
    monthly = df.resample("MS").agg({
        "Open":   "first",
        "High":   "max",
        "Low":    "min",
        "Close":  "last",
        "Volume": "sum",
    }).dropna()
    return monthly if len(monthly) >= 3 else None


# ── Real ATM IV from options snapshot ────────────────────────────────────────
def get_atm_iv(api_key, api_secret, ticker, spot_price):
    try:
        today  = datetime.now().date()
        end_dt = today + timedelta(days=14)
        url    = f"{DATA_URL}/v1beta1/options/snapshots/{ticker}"
        params = {
            "limit":               250,
            "feed":                "indicative",
            "expiration_date_gte": today.isoformat(),
            "expiration_date_lte": end_dt.isoformat(),
        }
        r = requests.get(url, headers=_headers(api_key, api_secret),
                         params=params, timeout=15)
        if r.status_code != 200:
            return None

        snapshots = r.json().get("snapshots", {})
        if not snapshots:
            return None

        best_iv, best_dist = None, float("inf")
        for symbol, snap in snapshots.items():
            iv = snap.get("impliedVolatility")
            if not iv or iv <= 0:
                continue
            try:
                strike = int(symbol[-8:]) / 1000
            except Exception:
                continue
            dist = abs(strike - spot_price)
            if dist < best_dist:
                best_dist = dist
                best_iv   = iv
        return best_iv
    except Exception:
        return None


def compute_iv_rank(current_iv, ticker):
    """Builds IV history in session state across runs to compute true IVR."""
    cache_key = f"iv_history_{ticker}"
    history   = st.session_state.get(cache_key, [])
    history.append(current_iv)
    history = history[-252:]
    st.session_state[cache_key] = history
    if len(history) < 2:
        return None
    iv_low, iv_high = min(history), max(history)
    if iv_high == iv_low:
        return 50.0
    return round((current_iv - iv_low) / (iv_high - iv_low) * 100, 1)


def hv_rank_fallback(df):
    try:
        returns    = df["Close"].pct_change().dropna()
        hv_current = returns.tail(20).std() * (252 ** 0.5) * 100
        rolling_hv = returns.rolling(20).std() * (252 ** 0.5) * 100
        hv_high    = rolling_hv.max()
        hv_low     = rolling_hv.min()
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


# ── FTFC across Daily / Weekly / Monthly ─────────────────────────────────────
def compute_3tf_ftfc(df_daily, df_weekly, df_monthly):
    """
    Use StratDetector to get bias on each TF, then compute FTFC.
    Returns (ftfc_label, ftfc_score, ftfc_detail)
    """
    def _bias(df):
        if df is None or len(df) < 2:
            return "—"
        row = df.iloc[-1]
        c, o = float(row["Close"]), float(row["Open"])
        if c > o:  return "🟢"
        if c < o:  return "🔴"
        return "—"

    d_bias = _bias(df_daily)
    w_bias = _bias(df_weekly)
    m_bias = _bias(df_monthly)

    biases  = [d_bias, w_bias, m_bias]
    bull_n  = biases.count("🟢")
    bear_n  = biases.count("🔴")
    score   = max(bull_n, bear_n)
    detail  = f"D:{d_bias} W:{w_bias} M:{m_bias}"

    if bull_n == 3:
        return "✅ BULL", score, detail
    elif bear_n == 3:
        return "✅ BEAR", score, detail
    elif bull_n == 2:
        return "⬆ BULL 2/3", score, detail
    elif bear_n == 2:
        return "⬇ BEAR 2/3", score, detail
    else:
        return "⚪ Mixed", score, detail


# ── Per-ticker analysis ───────────────────────────────────────────────────────
def analyze_ticker(api_key, api_secret, symbol, df_daily, df_weekly, df_monthly):
    try:
        if df_daily is None or len(df_daily) < 10:
            return None

        price = float(df_daily.iloc[-1]["Close"])
        prev  = df_daily.iloc[-2]
        pdh   = float(prev["High"])
        pdl   = float(prev["Low"])
        pwh   = float(df_daily.tail(5)["High"].max())
        pwl   = float(df_daily.tail(5)["Low"].min())
        near  = near_key_level(price, [pdh, pdl, pwh, pwl])

        # ── Full Strat analysis on daily ─────────────────────────────────────
        strat = StratDetector.analyze(df_daily, "1D")
        bar_types = strat.bar_types  # [t0, t1, t2] most-recent first

        # 3-candle sequence display (oldest → newest)
        if len(bar_types) >= 3:
            seq = f"{bar_types[2]}→{bar_types[1]}→{bar_types[0]}"
        elif len(bar_types) >= 1:
            seq = bar_types[0]
        else:
            seq = "—"

        # Combo (the real Strat setup)
        combo     = strat.combo      # e.g. "2-1-2", "3-2-2", ""
        combo_dir = strat.combo_dir  # "CALL", "PUT", ""

        # Actionable signal label
        if combo:
            arrow = "▲" if combo_dir == "CALL" else "▼"
            strat_signal = f"{combo} {arrow}"
        elif strat.is_f2d:
            strat_signal = "F2D 🪤 ▲"
            combo_dir    = "CALL"
        elif strat.is_f2u:
            strat_signal = "F2U 🪤 ▼"
            combo_dir    = "PUT"
        elif strat.is_pmg:
            arrow = "▲" if strat.bias == "BULL" else "▼"
            strat_signal = f"PMG {arrow}"
            combo_dir    = "CALL" if strat.bias == "BULL" else "PUT"
        else:
            strat_signal = f"Seq: {seq}"
            combo_dir    = ""

        # ── FTFC (Daily / Weekly / Monthly) ─────────────────────────────────
        ftfc_label, ftfc_score, ftfc_detail = compute_3tf_ftfc(
            df_daily, df_weekly, df_monthly
        )

        # ── Bias from Strat (most recent bar direction) ───────────────────────
        t0 = bar_types[0] if bar_types else ""
        if combo_dir == "CALL":
            bias = "🟢 Bullish"
        elif combo_dir == "PUT":
            bias = "🔴 Bearish"
        elif t0 == "2U":
            bias = "🟢 Bullish"
        elif t0 == "2D":
            bias = "🔴 Bearish"
        elif t0 == "1":
            bias = "🟡 Neutral"
        else:
            bias = "⚠️ Reversal"

        # ── IV Rank ───────────────────────────────────────────────────────────
        atm_iv    = get_atm_iv(api_key, api_secret, symbol, price)
        iv_source = "IV"

        if atm_iv and atm_iv > 0:
            ivr = compute_iv_rank(atm_iv, symbol)
            if ivr is None:
                ivr       = round(atm_iv * 100, 1)
                iv_source = "IV%"
        else:
            ivr       = hv_rank_fallback(df_daily)
            iv_source = "HV"

        # ── Confluence score ──────────────────────────────────────────────────
        score = 0

        # Strat signal quality
        if combo in ("2-1-2", "3-1-2", "3-2-2", "1-2-2"):
            score += 1.5   # structural combo = highest quality
        elif combo == "2-2":
            score += 1.0   # continuation
        elif strat.is_f2d or strat.is_f2u:
            score += 1.0   # failed 2 trap
        elif strat.is_pmg:
            score += 0.5
        elif t0 in ("2U", "2D"):
            score += 0.5   # bare directional bar, no combo

        # FTFC alignment
        if ftfc_score == 3:
            score += 1.5   # full FTFC
        elif ftfc_score == 2:
            score += 0.75  # 2-of-3

        # Near key level
        if near:
            score += 1

        # IV cheap
        if ivr is not None and ivr < 35:
            score += 0.5

        score = round(min(score, 5.0), 1)  # cap at 5

        return {
            "Symbol":    symbol,
            "Price":     round(price, 2),
            "Bias":      bias,
            "Strat":     strat_signal,
            "Seq":       seq,
            "FTFC":      ftfc_label,
            "FTFC D/W/M": ftfc_detail,
            "PDH":       round(pdh, 2),
            "PDL":       round(pdl, 2),
            "Near Key":  "✅" if near else "—",
            "IV Rank":   ivr if ivr is not None else "—",
            "IV Source": iv_source,
            "Score":     score,
        }
    except Exception:
        return None


# ── Main render ───────────────────────────────────────────────────────────────
def render_morning_briefing(api_key="", api_secret="", base_url="https://api.alpaca.markets"):

    st.markdown("## 🌅 Morning Briefing")
    st.caption(
        f"Ranks all {len(ALL_TICKERS)} tickers — real Strat combos (3 candles), "
        f"FTFC (Daily/Weekly/Monthly), IV Rank. Run at 9:25 AM before open."
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
            # Fetch all three timeframes
            df_d = get_bars(api_key, api_secret, symbol, timeframe="1Day", days=365)
            df_w = get_weekly_bars(api_key, api_secret, symbol)
            df_m = get_monthly_bars(api_key, api_secret, symbol)

            row = analyze_ticker(api_key, api_secret, symbol, df_d, df_w, df_m)
            if row:
                results.append(row)
            time.sleep(0.1)

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
    if sources.get("IV"):  iv_notes.append(f"✅ {sources['IV']} tickers: real IV Rank")
    if sources.get("IV%"): iv_notes.append(f"🔄 {sources.get('IV%',0)} tickers: raw IV% (rank builds over days)")
    if sources.get("HV"):  iv_notes.append(f"⚠️ {sources['HV']} tickers: HV fallback")
    if iv_notes:
        st.caption(" · ".join(iv_notes))

    # ── Market bias summary ───────────────────────────────────────────────────
    total      = len(df_out)
    bullish_n  = len(df_out[df_out["Bias"] == "🟢 Bullish"])
    bearish_n  = len(df_out[df_out["Bias"] == "🔴 Bearish"])
    neutral_n  = len(df_out[df_out["Bias"] == "🟡 Neutral"])
    reversal_n = len(df_out[df_out["Bias"] == "⚠️ Reversal"])
    bull_pct   = bullish_n / total * 100 if total else 0
    bear_pct   = bearish_n / total * 100 if total else 0

    if bull_pct >= 60:
        overall, overall_color = "🟢 BULLISH", "#00ff88"
        overall_note = "Majority showing bullish Strat combos. Favor CALL setups."
    elif bear_pct >= 60:
        overall, overall_color = "🔴 BEARISH", "#ff4444"
        overall_note = "Majority showing bearish Strat combos. Favor PUT setups."
    elif bull_pct >= 45:
        overall, overall_color = "🟡 LEANING BULLISH", "#ffdd00"
        overall_note = "Slight bullish edge. Confirm at key levels."
    elif bear_pct >= 45:
        overall, overall_color = "🟡 LEANING BEARISH", "#ffaa00"
        overall_note = "Slight bearish edge. Confirm at key levels."
    else:
        overall, overall_color = "⚪ MIXED / CHOPPY", "#8888aa"
        overall_note = "No clear directional edge. Wait for confirmation."

    st.markdown("### 📊 Market Bias")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Overall",     overall)
    c2.metric("🟢 Bullish",  f"{bullish_n} ({bull_pct:.0f}%)")
    c3.metric("🔴 Bearish",  f"{bearish_n} ({bear_pct:.0f}%)")
    c4.metric("🟡 Neutral",  neutral_n)
    c5.metric("⚠️ Reversal", reversal_n)

    st.markdown(
        f"<div style='background:#1a1f2e;border-left:4px solid {overall_color};"
        f"border-radius:8px;padding:12px 18px;margin:8px 0 16px 0;"
        f"font-family:monospace;font-size:0.9rem;color:{overall_color};font-weight:700;'>"
        f"{overall} — {overall_note}</div>",
        unsafe_allow_html=True
    )

    st.divider()

    # ── Top picks ─────────────────────────────────────────────────────────────
    top = df_out[df_out["Score"] >= 2.5].head(6)
    st.markdown("### 🎯 Top Setups Today")
    if top.empty:
        st.info("No tickers with score ≥ 2.5 right now.")
    else:
        for _, row in top.iterrows():
            bar      = "█" * int(row["Score"])
            iv_label = (f"IV Rank: {row['IV Rank']}" if row.get("IV Source") == "IV"
                        else f"IV%: {row['IV Rank']}" if row.get("IV Source") == "IV%"
                        else f"HV: {row['IV Rank']}")
            st.markdown(
                f"**{row['Symbol']}** &nbsp; {row['Bias']} &nbsp; "
                f"`{row['Strat']}` &nbsp; FTFC: {row['FTFC']} &nbsp; "
                f"Near Key: {row['Near Key']} &nbsp; {iv_label} &nbsp; "
                f"Score: **{row['Score']}** `{bar}`"
            )

    st.divider()

    # ── Full table ─────────────────────────────────────────────────────────────
    st.markdown("### 📋 Full Watchlist Scan")

    def color_score(val):
        try:
            v = float(val)
            if v >= 3.0:  return "background-color:#1a3a1a;color:#00ff88"
            elif v >= 2.0: return "background-color:#2a2a0a;color:#ffdd00"
            elif v >= 1.0: return "background-color:#1a1a2e;color:#8888ff"
        except Exception:
            pass
        return ""

    display_cols = ["Symbol", "Price", "Bias", "Strat", "FTFC",
                    "FTFC D/W/M", "Near Key", "IV Rank", "IV Source", "Score"]
    st.dataframe(
        df_out[display_cols].style.applymap(color_score, subset=["Score"]),
        use_container_width=True,
        hide_index=True,
    )

    # ── Legend ────────────────────────────────────────────────────────────────
    with st.expander("📖 How scores work"):
        st.markdown("""
**Strat Signal (up to +1.5)**
| Signal | Points |
|---|---|
| Structural combo (2-1-2, 3-1-2, 3-2-2, 1-2-2) | +1.5 |
| Continuation (2-2) or Failed 2 trap (F2D/F2U) | +1.0 |
| PMG (Pivot Machine Gun) | +0.5 |
| Bare 2U or 2D with no combo | +0.5 |

**FTFC — Full Time Frame Continuity (up to +1.5)**
| Alignment | Points |
|---|---|
| Daily + Weekly + Monthly all same direction | +1.5 |
| 2 of 3 timeframes aligned | +0.75 |

**Other signals**
| Signal | Points |
|---|---|
| Price within 0.3% of PDH/PDL/PWH/PWL | +1.0 |
| IV Rank < 35 (cheap premium) | +0.5 |

**Max score: 5.0** — Top setups shown at ≥ 2.5.

**Strat combos:** 2-1-2 = classic setup (down bar → inside bar → up/down break).
3-2-2 = outside bar reversal continuation. F2D/F2U = failed directional break trap.
FTFC = all three timeframes (Daily/Weekly/Monthly) closing in the same direction.
        """)

    csv = df_out.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Export CSV",
        data=csv,
        file_name=f"morning_briefing_{datetime.now().strftime('%Y%m%d')}.csv",
        mime="text/csv",
    )
