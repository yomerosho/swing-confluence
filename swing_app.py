"""
swing_app.py — SwingConfluence Streamlit Dashboard
====================================================
Scan, view, and email 3-of-3 confluence swing setups.
"""

import streamlit as st
import pandas as pd
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from swing_scanner import (
    SwingScanner, ConfluenceSetup,
    ALL_TICKERS, INDICES_ETFS, MEGA_CAPS, SWING_NAMES,
)
from swing_html import build_swing_report, render_setup_card, PALETTE


# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="SwingConfluence",
    page_icon="🎯",
    layout="wide",
)

# ── Secrets ───────────────────────────────────────────────────────────────────

def get_secret(section, key, default=""):
    try: return st.secrets[section][key]
    except: return default

ALPACA_KEY    = get_secret("alpaca", "key", "")
ALPACA_SECRET = get_secret("alpaca", "secret", "")
GMAIL_USER    = get_secret("gmail", "user", "")
GMAIL_PASS    = get_secret("gmail", "password", "")


# ── CSS ───────────────────────────────────────────────────────────────────────

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;600;800&family=JetBrains+Mono:wght@300;400;600&display=swap');

/* Force dark theme globally */
html, body, .stApp, [class*="css"], .main, .block-container {{
  font-family: 'Syne', sans-serif !important;
  background: {PALETTE['bg']} !important;
  color: #ffffff !important;
}}

/* Main content area */
.main .block-container {{ background: {PALETTE['bg']} !important; padding-top: 2rem; }}
[data-testid="stAppViewContainer"] {{ background: {PALETTE['bg']} !important; }}
[data-testid="stHeader"] {{ background: {PALETTE['bg']} !important; }}
[data-testid="stToolbar"] {{ background: transparent !important; }}

/* TOP-RIGHT TOOLBAR BUTTONS (Share, edit, github, ⋮) — force light icons */
[data-testid="stToolbar"] button,
[data-testid="stToolbar"] a,
[data-testid="stToolbar"] svg {{
  color: #e0e8f0 !important;
  fill: #e0e8f0 !important;
  opacity: 1 !important;
}}
[data-testid="stToolbar"] button:hover,
[data-testid="stToolbar"] a:hover {{
  background: rgba(255,255,255,0.08) !important;
  color: #ffffff !important;
}}
header [data-testid="baseButton-headerNoPadding"] svg {{ color: #e0e8f0 !important; }}

/* Sidebar */
section[data-testid="stSidebar"] {{ background: #090c14 !important; border-right: 1px solid #1a2030; }}
section[data-testid="stSidebar"] * {{ color: #ffffff !important; }}
section[data-testid="stSidebar"] .stCheckbox label,
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span {{ color: #e0e8f0 !important; }}

/* Status messages */
.stStatus, [data-testid="stStatusWidget"] {{
  background: {PALETTE['card']} !important;
  color: #ffffff !important;
  border: 1px solid {PALETTE['border']} !important;
}}
.stStatus *, [data-testid="stStatusWidget"] * {{ color: #ffffff !important; }}
.stAlert {{ background: {PALETTE['card']} !important; color: #ffffff !important; }}
.stAlert * {{ color: #ffffff !important; }}

/* Progress bar messages */
.stMarkdown code, code {{
  background: {PALETTE['card_dark']} !important;
  color: #4af0c4 !important;
  padding: 4px 10px !important;
  border-radius: 6px !important;
  font-family: 'JetBrains Mono', monospace !important;
}}

/* Headings & text — high contrast */
h1, h2, h3, h4, h5, h6 {{ color: #ffffff !important; }}
p, span, label, div {{ color: #d4dce8; }}

/* Metric widgets */
[data-testid="metric-container"] {{
  background: {PALETTE['card']} !important;
  border: 1px solid {PALETTE['border']} !important;
  border-radius: 10px;
  padding: 14px 18px;
}}
[data-testid="metric-container"] label {{
  color: #a8b3c8 !important;
  font-family: 'JetBrains Mono', monospace !important;
  font-size: 0.7rem !important;
  text-transform: uppercase;
  letter-spacing: 0.1em;
}}
[data-testid="metric-container"] [data-testid="stMetricValue"] {{
  color: {PALETTE['brand']} !important;
  font-family: 'JetBrains Mono', monospace !important;
  font-weight: 700 !important;
}}

/* Text inputs — fix placeholder visibility */
.stTextInput input, .stTextArea textarea {{
  background: {PALETTE['card_dark']} !important;
  color: #ffffff !important;
  border: 1px solid {PALETTE['border']} !important;
  font-family: 'JetBrains Mono', monospace !important;
}}
.stTextInput input::placeholder,
.stTextArea textarea::placeholder {{
  color: #8090a0 !important;
  opacity: 1 !important;
}}
.stTextInput label, .stTextArea label {{ color: #d4dce8 !important; font-weight: 600; }}

/* Help tooltips (?) icon */
[data-testid="stTooltipIcon"] svg {{ color: #6a7a90 !important; fill: #6a7a90 !important; }}

/* Buttons */
.stButton > button {{
  background: linear-gradient(135deg, #3a1a6b, #1a0d4a) !important;
  color: {PALETTE['brand']} !important;
  border: none !important;
  border-radius: 6px;
  padding: 10px 20px;
  font-family: 'JetBrains Mono', monospace;
  font-weight: 600;
}}
.stButton > button:hover {{
  background: linear-gradient(135deg, #4a2a80, #2a0f60) !important;
  color: #ffffff !important;
}}

/* Captions */
.stCaption, small {{ color: #8a99b0 !important; }}

/* Horizontal divider */
hr {{ border-color: #1a2030 !important; }}

/* Checkbox color when checked */
.stCheckbox input:checked + div {{ background: {PALETTE['brand']} !important; }}
</style>
""", unsafe_allow_html=True)

# ── Session state ─────────────────────────────────────────────────────────────

for k, v in {
    "setups":         [],
    "last_scan":      None,
    "scan_log":       [],
    "diagnostics":    [],
    "last_diagnostic": None,
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## 🎯 SwingConfluence")
    st.markdown("*3-of-3 swing setups · 1-3 day holds*")
    st.markdown("---")

    st.markdown("### 🎯 Scope")

    use_indices = st.checkbox("Indices/ETFs (6)", value=True)
    use_mega    = st.checkbox("Mega Caps (7)", value=True)
    use_swing   = st.checkbox("Swing Names (42)", value=True)

    selected = []
    if use_indices: selected += INDICES_ETFS
    if use_mega:    selected += MEGA_CAPS
    if use_swing:   selected += SWING_NAMES

    # Custom tickers
    custom_raw = st.text_input(
        "➕ Additional tickers",
        value="",
        placeholder="e.g. QCOM, SMCI, AVGO",
        help="Comma or space separated. Added on top of selected categories.",
    )
    custom_tickers = []
    if custom_raw.strip():
        custom_tickers = [
            t.strip().upper() for t in custom_raw.replace(",", " ").split()
            if t.strip().isalpha() and 1 <= len(t.strip()) <= 6
        ]
        # Dedupe while preserving order
        custom_tickers = [t for t in custom_tickers if t not in selected]
        selected += custom_tickers

    if custom_tickers:
        st.caption(f"Will scan {len(selected)} tickers (+{len(custom_tickers)} custom)")
    else:
        st.caption(f"Will scan {len(selected)} tickers")

    st.markdown("---")
    scan_btn  = st.button("🎯 Run Confluence Scan", type="primary")
    email_btn = st.button("📧 Email Results")

    if st.session_state.last_scan:
        st.caption(f"Last: {st.session_state.last_scan}")

    if ALPACA_KEY and ALPACA_SECRET:
        st.success("✅ Alpaca connected")
    else:
        st.error("❌ Alpaca keys missing")

    st.markdown(f"""
    <div style='margin-top:20px;font-size:0.7rem;color:#5a3a80;line-height:1.7;'>
    <b style='color:{PALETTE["brand"]};'>Conviction Tiers</b><br>
    ⭐⭐⭐⭐⭐⭐ Daily + 4H aligned<br>
    ⭐⭐⭐⭐⭐ Daily signal<br>
    ⭐⭐⭐⭐ 4H signal only<br><br>
    <b style='color:{PALETTE["brand"]};'>3 Factors</b><br>
    Technical pattern (10 types)<br>
    GEX positioning supports<br>
    Whale flow ($500K+) agrees<br><br>
    <i style='color:#3a2a50;'>Educational only<br>Not financial advice</i>
    </div>""", unsafe_allow_html=True)


# ── Header ────────────────────────────────────────────────────────────────────

st.markdown(f"""
<div style='padding:8px 0 16px 0;'>
  <h1 style='font-family:Syne,sans-serif;font-size:2.2rem;font-weight:800;
             background:linear-gradient(90deg,{PALETTE["brand"]},#6a5aff,{PALETTE["green"]});
             -webkit-background-clip:text;-webkit-text-fill-color:transparent;
             margin:0;letter-spacing:-0.02em;'>🎯 SwingConfluence</h1>
  <p style='color:#5a3a80;font-family:JetBrains Mono,monospace;font-size:0.78rem;margin:4px 0 0 0;'>
    Daily + 4H pattern scan · GEX positioning · Whale flow · 1-3 day swings
  </p>
</div>
""", unsafe_allow_html=True)


# ── Scan ──────────────────────────────────────────────────────────────────────

from subscribers import get_emails as load_subscribers


def send_email(html, subject):
    if not GMAIL_USER or not GMAIL_PASS:
        return False, "Gmail credentials missing"
    subs = load_subscribers()
    if not subs:
        return False, "No subscribers"

    sent = 0
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_USER, GMAIL_PASS)
            for email in subs:
                msg = MIMEMultipart("alternative")
                msg["Subject"] = subject
                msg["From"]    = GMAIL_USER
                msg["To"]      = email
                msg.attach(MIMEText(html, "html"))
                s.send_message(msg)
                sent += 1
        return True, f"Sent to {sent} subscriber(s)"
    except Exception as e:
        return False, str(e)


if scan_btn:
    if not ALPACA_KEY or not ALPACA_SECRET:
        st.error("⚠️ Configure Alpaca keys in Streamlit secrets first")
    else:
        scanner = SwingScanner(ALPACA_KEY, ALPACA_SECRET)
        with st.status("🎯 Running confluence scan...", expanded=True) as status:
            pb  = st.progress(0)
            stx = st.empty()

            def cb(pct, msg):
                pb.progress(pct)
                stx.markdown(f"`{msg}`")

            setups = scanner.scan_all(tickers=selected, progress_cb=cb)
            pb.empty(); stx.empty()

            st.session_state.setups    = setups
            st.session_state.last_scan = datetime.now().strftime("%H:%M:%S CT")

            if setups:
                status.update(label=f"✅ Found {len(setups)} confluence setup(s)!", state="complete")
            else:
                status.update(label="✅ Scan complete · No setups today", state="complete")


if email_btn:
    if not st.session_state.setups:
        st.warning("⚠️ Run a scan first")
    else:
        html = build_swing_report(st.session_state.setups, "Manual Scan")
        subj = f"🎯 SwingConfluence · {len(st.session_state.setups)} Setup(s) · {datetime.now().strftime('%b %d %H:%M CT')}"
        ok, msg = send_email(html, subj)
        if ok: st.success(f"✅ {msg}")
        else:  st.error(f"❌ {msg}")


# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_scanner, tab_diagnostic = st.tabs([
    "🎯 Confluence Scanner",
    "🔬 Diagnostic Scan",
])


# ════════════════════════════════════════════════
#  TAB 1 — CONFLUENCE SCANNER (main feature)
# ════════════════════════════════════════════════
with tab_scanner:
    if not st.session_state.setups and st.session_state.last_scan is None:
        st.markdown(f"""
        <div style='text-align:center;padding:80px 0;'>
          <div style='font-size:4rem;'>🎯</div>
          <h2 style='font-family:Syne,sans-serif;color:#5a3a80;margin-top:16px;'>3-of-3 Swing Confluence Scanner</h2>
          <p style='font-family:JetBrains Mono,monospace;color:#3a2a50;font-size:0.9rem;'>
            Click <b style='color:{PALETTE["brand"]};'>Run Confluence Scan</b> in the sidebar
          </p>
          <p style='font-family:JetBrains Mono,monospace;color:#3a2a50;font-size:0.78rem;margin-top:24px;line-height:1.8;'>
            Scans Daily + 4H timeframes<br>
            Returns ONLY setups where ALL 3 factors align<br>
            Patterns · GEX positioning · Whale flow ≥ $500K
          </p>
          <p style='font-family:JetBrains Mono,monospace;color:#5a3a80;font-size:0.72rem;margin-top:30px;'>
            Not finding anything? Try the <b style='color:{PALETTE["brand"]};'>🔬 Diagnostic Scan</b> tab
          </p>
        </div>""", unsafe_allow_html=True)

    elif not st.session_state.setups:
        st.markdown(f"""
        <div style='background:{PALETTE["card"]};border:1px solid {PALETTE["border"]};
                    border-radius:12px;padding:40px;text-align:center;margin:20px 0;'>
          <div style='font-size:3rem;'>🔍</div>
          <h3 style='color:{PALETTE["text"]};font-family:monospace;margin-top:12px;'>No confluence setups today</h3>
          <p style='color:{PALETTE["text_dim"]};font-family:monospace;font-size:0.85rem;'>
            All tickers scanned. None meet the 3-of-3 threshold.
          </p>
          <p style='color:{PALETTE["text_muted"]};font-family:monospace;font-size:0.78rem;margin-top:14px;'>
            Run the <b>🔬 Diagnostic Scan</b> tab to see which gate is filtering setups out.
          </p>
        </div>""", unsafe_allow_html=True)

    else:
        setups       = st.session_state.setups
        max_count    = sum(1 for s in setups if s.conviction == 6)
        high_count   = sum(1 for s in setups if s.conviction == 5)
        medium_count = sum(1 for s in setups if s.conviction == 4)
        call_count   = sum(1 for s in setups if s.direction == "CALL")
        put_count    = sum(1 for s in setups if s.direction == "PUT")

        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Total",   len(setups))
        c2.metric("MAX 6★",  max_count)
        c3.metric("HIGH 5★", high_count)
        c4.metric("MED 4★",  medium_count)
        c5.metric("Calls",   call_count)
        c6.metric("Puts",    put_count)

        st.markdown("---")

        for setup in setups:
            html = render_setup_card(setup)
            st.markdown(html, unsafe_allow_html=True)


# ════════════════════════════════════════════════
#  TAB 2 — DIAGNOSTIC SCAN
# ════════════════════════════════════════════════
with tab_diagnostic:
    st.markdown(f"""
    <div style='background:{PALETTE["card_dark"]};border:1px solid {PALETTE["border"]};
                border-left:3px solid {PALETTE["brand"]};border-radius:10px;
                padding:16px 20px;margin-bottom:20px;'>
      <div style='color:{PALETTE["brand"]};font-family:monospace;font-size:0.85rem;font-weight:700;margin-bottom:6px;'>
        🔬 Diagnostic Scanner
      </div>
      <div style='color:{PALETTE["text_dim"]};font-family:monospace;font-size:0.78rem;line-height:1.6;'>
        Shows where each ticker is being filtered out. Useful when the main scanner returns nothing.<br>
        For each ticker: <b>Patterns → GEX → Whales</b>. The "Blocked At" column tells you which gate fails.
      </div>
    </div>
    """, unsafe_allow_html=True)

    diag_btn = st.button("🔬 Run Diagnostic Scan", type="primary", key="diag_btn_main")

    health_btn = st.button("🩺 Quick Alpaca Health Check", key="health_btn")

    if health_btn:
        if not ALPACA_KEY or not ALPACA_SECRET:
            st.error("⚠️ Alpaca keys missing")
        else:
            import requests as _rq
            st.markdown("### 🩺 Alpaca Data Quality Check")
            st.caption("Verifying which feeds work and whether prices are sane.")

            headers = {
                "APCA-API-KEY-ID":     ALPACA_KEY,
                "APCA-API-SECRET-KEY": ALPACA_SECRET,
                "accept":              "application/json",
            }

            test_tickers = ["SPY", "AAPL", "NVDA"]
            results_rows = []

            with st.spinner("Probing Alpaca endpoints..."):
                for ticker in test_tickers:
                    for feed in ["sip", "iex"]:
                        # Quote test
                        try:
                            r = _rq.get(
                                f"https://data.alpaca.markets/v2/stocks/{ticker}/quotes/latest",
                                headers=headers,
                                params={"feed": feed},
                                timeout=10,
                            )
                            status = r.status_code
                            if status == 200:
                                q = r.json().get("quote", {})
                                bid, ask = q.get("bp", 0), q.get("ap", 0)
                                mid_val = f"${(bid + ask) / 2:.2f}" if (bid and ask) else "—"
                                results_rows.append({
                                    "Ticker": ticker, "Feed": feed.upper(),
                                    "Endpoint": "quote", "Status": status,
                                    "Bid": f"${bid:.2f}" if bid else "—",
                                    "Ask": f"${ask:.2f}" if ask else "—",
                                    "Mid": mid_val,
                                })
                            elif status == 403:
                                results_rows.append({
                                    "Ticker": ticker, "Feed": feed.upper(),
                                    "Endpoint": "quote", "Status": "❌ 403 no access",
                                    "Bid": "—", "Ask": "—", "Mid": "—",
                                })
                            else:
                                results_rows.append({
                                    "Ticker": ticker, "Feed": feed.upper(),
                                    "Endpoint": "quote", "Status": f"❌ {status}",
                                    "Bid": "—", "Ask": "—", "Mid": "—",
                                })
                        except Exception as e:
                            results_rows.append({
                                "Ticker": ticker, "Feed": feed.upper(),
                                "Endpoint": "quote", "Status": f"❌ {type(e).__name__}",
                                "Bid": "—", "Ask": "—", "Mid": "—",
                            })

            st.dataframe(pd.DataFrame(results_rows), use_container_width=True, hide_index=True)
            st.caption(
                "**What to look for:** SPY around $570-580, AAPL around $200-280, NVDA around $130-200. "
                "If SIP rows show ❌ 403, your account doesn't have real-time SIP access — we'll need to handle that. "
                "If prices look wildly wrong (10x or 0.1x of expected), there's a parsing bug."
            )

    if diag_btn:
        if not ALPACA_KEY or not ALPACA_SECRET:
            st.error("⚠️ Configure Alpaca keys in Streamlit secrets first")
        else:
            scanner = SwingScanner(ALPACA_KEY, ALPACA_SECRET)
            with st.status("🔬 Running diagnostic scan...", expanded=True) as status:
                pb  = st.progress(0)
                stx = st.empty()

                def diag_cb(pct, msg):
                    pb.progress(pct)
                    stx.markdown(f"`{msg}`")

                diags = scanner.diagnose_all(tickers=selected, progress_cb=diag_cb)
                pb.empty(); stx.empty()

                st.session_state.diagnostics    = diags
                st.session_state.last_diagnostic = datetime.now().strftime("%H:%M:%S CT")
                status.update(label=f"✅ Diagnosed {len(diags)} tickers", state="complete")

    # Display diagnostic results
    if st.session_state.diagnostics:
        diags = st.session_state.diagnostics

        # ── Aggregate stats ─────────────────────────────────────────────
        no_data       = sum(1 for d in diags if d.get("error") or not d.get("spot"))
        no_patterns   = sum(1 for d in diags if not d.get("daily_patterns") and not d.get("h4_patterns") and not d.get("error"))
        had_patterns  = len(diags) - no_data - no_patterns

        # Count where setups blocked
        blocked_gex     = 0
        blocked_whales  = 0
        passed_all      = 0
        no_chain        = 0
        for d in diags:
            for r in d.get("results", []):
                if r["blocked_at"] == "gex":      blocked_gex    += 1
                elif r["blocked_at"] == "whales": blocked_whales += 1
                elif r["blocked_at"] == "passed": passed_all     += 1
                elif r["blocked_at"] == "no_chain": no_chain     += 1

        st.markdown("### 📊 Gate Analysis Summary")

        c1, c2, c3, c4, c5, c6 = st.columns(6)
        c1.metric("Tickers", len(diags))
        c2.metric("Had Patterns", had_patterns)
        c3.metric("No Patterns", no_patterns)
        c4.metric("Blocked: GEX", blocked_gex)
        c5.metric("Blocked: Whales", blocked_whales)
        c6.metric("✅ Passed", passed_all)

        # Insight
        total_attempts = blocked_gex + blocked_whales + passed_all + no_chain
        if total_attempts > 0:
            primary_blocker = max(
                [("GEX", blocked_gex), ("Whales", blocked_whales),
                 ("Chain unavailable", no_chain), ("All passed", passed_all)],
                key=lambda x: x[1]
            )
            pct_blocked = primary_blocker[1] / total_attempts * 100

            color = PALETTE["red"] if primary_blocker[0] != "All passed" else PALETTE["green"]
            st.markdown(f"""
            <div style='background:{PALETTE["card"]};border-left:3px solid {color};
                        border-radius:8px;padding:14px 18px;margin:16px 0;'>
              <div style='color:{color};font-family:monospace;font-size:0.95rem;font-weight:700;'>
                🔍 Primary blocker: {primary_blocker[0]} ({primary_blocker[1]}/{total_attempts} = {pct_blocked:.0f}%)
              </div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("---")

        # ── Per-ticker breakdown ────────────────────────────────────────
        st.markdown("### 🎯 Per-Ticker Breakdown")

        filter_opt = st.radio(
            "Show:",
            ["All tickers", "Only blocked at whales", "Only blocked at GEX", "Only with patterns", "Only passed all"],
            horizontal=True,
        )

        rows = []
        for d in diags:
            ticker = d["ticker"]
            spot   = d.get("spot", 0) or 0

            if d.get("error"):
                rows.append({
                    "Ticker": ticker, "Spot": "—",
                    "Daily Patterns": "—", "4H Patterns": "—",
                    "Direction": "—", "GEX": "—", "Whales": "—",
                    "Blocked At": f"❌ {d['error']}",
                    "Whale $": "—",
                })
                continue

            daily_count = len(d.get("daily_patterns", []))
            h4_count    = len(d.get("h4_patterns",    []))

            if not d.get("results"):
                # No patterns at all
                rows.append({
                    "Ticker": ticker, "Spot": f"${spot:.2f}",
                    "Daily Patterns": daily_count, "4H Patterns": h4_count,
                    "Direction": "—", "GEX": "—", "Whales": "—",
                    "Blocked At": "🚫 No patterns",
                    "Whale $": "—",
                })
                continue

            for r in d["results"]:
                rows.append({
                    "Ticker": ticker, "Spot": f"${spot:.2f}",
                    "Daily Patterns": daily_count, "4H Patterns": h4_count,
                    "Direction": r["direction"],
                    "GEX":    "✅" if r["gex_supports"] else "❌",
                    "Whales": "✅" if r["whale_supports"] else "❌",
                    "Blocked At": {
                        "patterns":  "🚫 No patterns",
                        "gex":       "❌ GEX",
                        "whales":    "❌ Whales",
                        "no_chain":  "⚠️ No chain",
                        "passed":    "✅ PASSED",
                    }.get(r["blocked_at"], r["blocked_at"]),
                    "Whale $": f"${r['whale_top_premium']:,.0f}" if r['whale_top_premium'] else "—",
                })

        df_diag = pd.DataFrame(rows)

        # Apply filter
        if filter_opt == "Only blocked at whales":
            df_diag = df_diag[df_diag["Blocked At"].str.contains("Whales", na=False)]
        elif filter_opt == "Only blocked at GEX":
            df_diag = df_diag[df_diag["Blocked At"].str.contains("GEX", na=False)]
        elif filter_opt == "Only with patterns":
            df_diag = df_diag[(df_diag["Daily Patterns"] != "—") &
                              ((df_diag["Daily Patterns"] > 0) | (df_diag["4H Patterns"] > 0))]
        elif filter_opt == "Only passed all":
            df_diag = df_diag[df_diag["Blocked At"].str.contains("PASSED", na=False)]

        st.dataframe(df_diag, use_container_width=True, hide_index=True)

        # ── Detailed pattern + summary breakdown ─────────────────────────
        st.markdown("---")
        st.markdown("### 📋 Detailed Pattern Detections")
        st.caption("Expand each ticker to see exactly what patterns fired and why GEX/whales blocked it.")

        tickers_with_patterns = [d for d in diags
                                  if d.get("daily_patterns") or d.get("h4_patterns")]

        if not tickers_with_patterns:
            st.info("No patterns detected on any ticker. This means Gate 1 (pattern detection) is the bottleneck.")
        else:
            for d in tickers_with_patterns:
                ticker = d["ticker"]
                spot   = d.get("spot", 0)

                summary_emoji = "✅" if any(r["blocked_at"] == "passed" for r in d.get("results", [])) else "❌"

                with st.expander(f"{summary_emoji} {ticker} · ${spot:.2f} · {len(d.get('daily_patterns', []))}d + {len(d.get('h4_patterns', []))}h4 patterns"):
                    # Patterns
                    if d.get("daily_patterns"):
                        st.markdown("**Daily patterns:**")
                        for p in d["daily_patterns"]:
                            color = PALETTE["green"] if p.direction == "CALL" else PALETTE["red"]
                            st.markdown(f"<div style='font-family:monospace;font-size:0.78rem;color:#d4dce8;padding:2px 0;'>"
                                        f"<b style='color:{color};'>{p.direction}</b> · "
                                        f"{p.pattern} · {p.reason}</div>",
                                        unsafe_allow_html=True)

                    if d.get("h4_patterns"):
                        st.markdown("**4H patterns:**")
                        for p in d["h4_patterns"]:
                            color = PALETTE["green"] if p.direction == "CALL" else PALETTE["red"]
                            st.markdown(f"<div style='font-family:monospace;font-size:0.78rem;color:#d4dce8;padding:2px 0;'>"
                                        f"<b style='color:{color};'>{p.direction}</b> · "
                                        f"{p.pattern} · {p.reason}</div>",
                                        unsafe_allow_html=True)

                    # Gate results per direction
                    if d.get("results"):
                        st.markdown("**Gate analysis:**")
                        for r in d["results"]:
                            dir_color = PALETTE["green"] if r["direction"] == "CALL" else PALETTE["red"]
                            st.markdown(f"""
                            <div style='background:#1a1f2e;border-radius:6px;padding:10px 14px;margin:6px 0;
                                        font-family:monospace;font-size:0.78rem;'>
                              <b style='color:{dir_color};'>{r["direction"]}</b> direction:<br>
                              • GEX: {"✅" if r["gex_supports"] else "❌"} {r["gex_summary"]}<br>
                              • Whales: {"✅" if r["whale_supports"] else "❌"} {r["whale_summary"]}<br>
                              <b style='color:#bc8cff;'>→ {r["blocked_at"]}</b>
                            </div>
                            """, unsafe_allow_html=True)

    elif st.session_state.last_diagnostic is None:
        st.markdown(f"""
        <div style='text-align:center;padding:60px 0;'>
          <div style='font-size:3rem;'>🔬</div>
          <h3 style='color:{PALETTE["text"]};font-family:monospace;margin-top:12px;'>Diagnostic Mode</h3>
          <p style='color:{PALETTE["text_dim"]};font-family:monospace;font-size:0.85rem;'>
            Click <b style='color:{PALETTE["brand"]};'>Run Diagnostic Scan</b> to see where setups are getting filtered
          </p>
        </div>""", unsafe_allow_html=True)

# ── Subscriber Management ─────────────────────────────────────────────────────

st.markdown("---")

with st.expander("📧 Manage Email Subscribers", expanded=False):
    from subscribers import (
        load_subscribers as _load_subs,
        add_subscriber as _add_sub,
        remove_subscriber as _rm_sub,
        is_valid_email,
    )

    current_subs = _load_subs()

    st.markdown(f"**Current subscribers: {len(current_subs)}**")
    st.caption("Emails are sent automatically at 8 AM / 1 PM / 4 PM CT when 3-of-3 setups are found.")

    if current_subs:
        for sub in current_subs:
            cols = st.columns([5, 1])
            with cols[0]:
                if sub["name"] != sub["email"].split("@")[0]:
                    st.markdown(
                        f"<div style='font-family:monospace;color:#d4dce8;padding:4px 0;'>"
                        f"<b style='color:#bc8cff'>{sub['name']}</b> · "
                        f"<span style='color:#a8b3c8'>{sub['email']}</span></div>",
                        unsafe_allow_html=True
                    )
                else:
                    st.markdown(
                        f"<div style='font-family:monospace;color:#d4dce8;padding:4px 0;'>"
                        f"{sub['email']}</div>",
                        unsafe_allow_html=True
                    )
            with cols[1]:
                if st.button("🗑️", key=f"rm_{sub['email']}", help="Remove"):
                    ok, msg = _rm_sub(sub["email"])
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
    else:
        st.info("No subscribers yet. Add one below.")

    st.markdown("##### ➕ Add Subscriber")
    add_cols = st.columns([2, 3, 1])
    with add_cols[0]:
        new_name = st.text_input("Name (optional)", key="new_sub_name",
                                   placeholder="John", label_visibility="collapsed")
    with add_cols[1]:
        new_email = st.text_input("Email", key="new_sub_email",
                                    placeholder="john@example.com",
                                    label_visibility="collapsed")
    with add_cols[2]:
        if st.button("Add", key="add_sub_btn"):
            if not new_email:
                st.error("Email required")
            elif not is_valid_email(new_email):
                st.error("Invalid email format")
            else:
                ok, msg = _add_sub(new_email, new_name)
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.warning(msg)

    st.caption(
        "⚠️ Changes are written to subscribers.txt on the deployed instance. "
        "For permanent persistence, edit subscribers.txt in GitHub and push."
    )

# Footer
st.markdown("---")
st.markdown(f"""
<div style='text-align:center;font-family:JetBrains Mono,monospace;font-size:0.68rem;color:#1a0a30;'>
  SwingConfluence · Alpaca real-time data · 3-of-3 confluence required
</div>""", unsafe_allow_html=True)
