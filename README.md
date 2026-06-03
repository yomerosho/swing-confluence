# 🎯 SwingConfluence

**1-3 Day Swing Scanner with Institutional Confluence**

Identifies high-conviction swing setups where ALL THREE factors align:

1. **Technical** — One of 10 patterns on Daily or 4H timeframe
2. **GEX** — Dealer positioning supports the trade direction
3. **Whales** — $500K+ flow agrees with direction

Only 3-of-3 setups are surfaced. Patience is the edge.

---

## 📐 Architecture

```
swing_patterns.py    # 10 pattern detectors (9/21/50 EMA, 100/200 SMA,
                     #   FVG, BOS, Order Block, Double T/B, Inside Bar)
swing_scanner.py     # Alpaca client + GEX + Whale analysis + confluence
swing_html.py        # HTML rendering (email + dashboard)
swing_app.py         # Streamlit dashboard
run_scheduled_email.py  # Email runner for GitHub Actions

.github/workflows/
  email_premarket.yml  # 8 AM CT — Pre-Market Validation
  email_lunch.yml      # 1 PM CT — Mid-Day Check
  email_close.yml      # 4 PM CT — After-Close Scan
```

---

## 🚀 Setup

### 1. Streamlit Cloud secrets

```toml
[alpaca]
key    = "YOUR_ALPACA_KEY"
secret = "YOUR_ALPACA_SECRET"

[gmail]
user     = "yshobowa@gmail.com"
password = "YOUR_GMAIL_APP_PASSWORD"
```

### 2. GitHub repo secrets (for email automation)

Settings → Secrets and variables → Actions:

- `ALPACA_KEY`
- `ALPACA_SECRET`
- `GMAIL_USER`
- `GMAIL_APP_PASSWORD`

### 3. Subscribers

Edit `subscribers.txt`. One email per line.

---

## 📅 Email Schedule

| Slot | Time (CT) | When | Purpose |
|---|---|---|---|
| **Pre-Market** | 8:00 AM | 1.5h before open | Validate overnight setups |
| **Lunch** | 1:00 PM | Mid-session | New 4H setups developed |
| **Close** | 4:00 PM | After close | Tomorrow's swing setups |

Emails are **only sent if setups are found** (using `--skip-if-empty`).

---

## ⭐ Conviction Scoring

| Stars | Tier | Trigger |
|---|---|---|
| ⭐⭐⭐⭐⭐⭐ | MAX | Daily + 4H both confirm + GEX + Whales |
| ⭐⭐⭐⭐⭐ | HIGH | Daily pattern + GEX + Whales |
| ⭐⭐⭐⭐ | MED-HIGH | 4H pattern + GEX + Whales |

Anything less than 3-of-3 is filtered out.

---

## 🔍 Pattern Detection

The 10 patterns scanned on Daily AND 4H:

1. **9-EMA Bounce** — Retrace + confirmation candle
2. **21-EMA Bounce** — With engulfing pattern
3. **50-EMA Bounce** — With hammer/shooting star
4. **100-SMA Bounce** — With strong rejection wick
5. **200-SMA Reversal** — Major trend support/resistance
6. **FVG Fill** — Fair Value Gap fill + rejection
7. **BOS Retest** — Break of Structure retest hold
8. **Order Block** — Bullish/bearish OB tap
9. **Double Bottom/Top** — With breakout confirmation
10. **Inside Bar Breakout** — Momentum continuation

---

## 🧪 Local Testing

```bash
# Set env vars
export ALPACA_KEY="..."
export ALPACA_SECRET="..."
export GMAIL_USER="you@gmail.com"
export GMAIL_APP_PASSWORD="..."

# Dry run (prints HTML, no email)
python run_scheduled_email.py --slot close --dry-run > preview.html

# Real send
python run_scheduled_email.py --slot close

# Skip email if no setups found
python run_scheduled_email.py --slot close --skip-if-empty

# Streamlit dashboard
streamlit run swing_app.py
```

---

## 📊 Output Format

```
⭐⭐⭐⭐⭐⭐  QCOM  $172.50  ▲ CALL  MAX CONVICTION
            Daily + 4H aligned

✅ TECHNICAL
  1D  · 9-EMA Bounce — Retrace to 9-EMA $171.20, bullish confirmation
  4H  · 50-EMA Hammer — Hammer at 50-EMA $171.80 (vol 2.1x)

✅ GEX
  GEX support $170.00 below + magnet $175.00 above

✅ WHALES
  3 whale trade(s), top: CALL $175 5/16 · $1,250,000

📋 TRADE PLAN
  STRIKE   $175       ENTRY  $173.36
  EXPIRY   2026-05-16 STOP   $170.49
  R/R      1:1.7      TARGET $175.00

  Hold: 1-3 days · Support: $170.00 · Resistance: $180.00
```

---

## 🚫 What This Tool Does NOT Do

- ❌ Not an automated trading system — generates signals only
- ❌ Not a guaranteed strategy — confluence improves odds, not certainty
- ❌ Not financial advice — educational tool

---

## 🔧 Architecture Notes

**Why Alpaca?** Real-time options data with Greeks, free with account. Replaces unreliable yfinance options endpoints.

**Why 3-of-3?** Filters noise. Single-factor signals fire constantly; 3-of-3 alignments are rare and meaningful.

**Why 4H + Daily?** Daily-only is too strict (slow signals). Hourly is too noisy. 4H is the swing-trading sweet spot.

**Why skip-if-empty?** Reduces inbox fatigue. No setups = no email. Patience is part of the strategy.
