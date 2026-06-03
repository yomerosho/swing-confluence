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
    "setups":    [],
    "last_scan": None,
    "scan_log":  [],
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

def load_subscribers():
    subs = []
    try:
        with open("subscribers.txt") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"): continue
                parts = line.split(",")
                email = next((p.strip() for p in parts if "@" in p), None)
                if email: subs.append(email)
    except FileNotFoundError:
        pass
    return subs


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


# ── Display setups ────────────────────────────────────────────────────────────

if not st.session_state.setups and st.session_state.last_scan is None:
    st.markdown(f"""
    <div style='text-align:center;padding:80px 0;'>
      <div style='font-size:4rem;'>🎯</div>
      <h2 style='font-family:Syne,sans-serif;color:#5a3a80;margin-top:16px;'>3-of-3 Swing Confluence Scanner</h2>
      <p style='font-family:JetBrains Mono,monospace;color:#3a2a50;font-size:0.9rem;'>
        Click <b style='color:{PALETTE["brand"]};'>Run Confluence Scan</b> in the sidebar
      </p>
      <p style='font-family:JetBrains Mono,monospace;color:#3a2a50;font-size:0.78rem;margin-top:24px;line-height:1.8;'>
        Scans Daily + 4H timeframes across 36 tickers<br>
        Returns ONLY setups where ALL 3 factors align<br>
        Patterns · GEX positioning · Whale flow ≥ $500K
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
        Patience is part of the edge. Check again on the next scheduled slot.
      </p>
    </div>""", unsafe_allow_html=True)

else:
    # Summary
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

    # Render each setup
    for setup in setups:
        html = render_setup_card(setup)
        st.markdown(html, unsafe_allow_html=True)

# Footer
st.markdown("---")
st.markdown(f"""
<div style='text-align:center;font-family:JetBrains Mono,monospace;font-size:0.68rem;color:#1a0a30;'>
  SwingConfluence · Alpaca real-time data · 3-of-3 confluence required
</div>""", unsafe_allow_html=True)
