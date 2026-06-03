"""
swing_html.py — HTML Email & Dashboard Renderer for SwingConfluence
====================================================================
Builds dark-themed HTML reports matching GexMetrics aesthetic.
Used by both Streamlit dashboard AND scheduled email runner.
"""

from datetime import datetime
from typing import List
from swing_scanner import ConfluenceSetup


# ── Color palette ─────────────────────────────────────────────────────────────

PALETTE = {
    "bg":         "#06080f",
    "card":       "#2a2f3d",
    "card_dark":  "#21252f",
    "border":     "#525a72",
    "text":       "#f0f4fb",
    "text_dim":   "#a8b3c8",
    "text_muted": "#8090a0",
    "brand":      "#bc8cff",
    "brand_soft": "rgba(188,140,255,0.14)",
    "green":      "#4af0c4",
    "green_soft": "rgba(74,240,196,0.14)",
    "red":        "#f04a6a",
    "red_soft":   "rgba(240,74,106,0.14)",
    "gold":       "#f5c842",
    "gold_soft":  "rgba(245,200,66,0.14)",
}

# ── Conviction labels ─────────────────────────────────────────────────────────

CONVICTION_LABELS = {
    6: ("⭐⭐⭐⭐⭐⭐", "MAX CONVICTION",     PALETTE["brand"]),
    5: ("⭐⭐⭐⭐⭐",   "HIGH CONVICTION",    PALETTE["green"]),
    4: ("⭐⭐⭐⭐",    "MEDIUM-HIGH",         PALETTE["gold"]),
}


# ── Per-setup card ────────────────────────────────────────────────────────────

def render_setup_card(s: ConfluenceSetup) -> str:
    """Render one ConfluenceSetup as HTML."""

    stars, conv_label, conv_color = CONVICTION_LABELS.get(
        s.conviction, ("⭐", "STANDARD", PALETTE["text_dim"])
    )

    dir_color = PALETTE["green"] if s.direction == "CALL" else PALETTE["red"]
    dir_arrow = "▲" if s.direction == "CALL" else "▼"
    dir_bg    = PALETTE["green_soft"] if s.direction == "CALL" else PALETTE["red_soft"]

    # Timeframe badge
    tf_badge = ""
    if s.has_daily and s.has_4h:
        tf_badge = "Daily + 4H aligned"
    elif s.has_daily:
        tf_badge = "Daily signal"
    else:
        tf_badge = "4H signal"

    # Pattern list
    pattern_html = ""
    for p in s.patterns:
        tf_color = PALETTE["brand"] if p.timeframe == "1D" else PALETTE["gold"]
        pattern_html += f"""
        <div style='padding:4px 0;font-size:0.78rem;color:{PALETTE["text_dim"]};font-family:monospace;'>
          <span style='color:{tf_color};font-weight:700;'>{p.timeframe}</span>
          <span style='color:{PALETTE["text"]};font-weight:600;'> · {p.pattern}</span>
          <span style='color:{PALETTE["text_muted"]};'> — {p.reason}</span>
        </div>
        """

    # R/R color
    rr_color = PALETTE["green"] if s.risk_reward >= 1.5 else (PALETTE["gold"] if s.risk_reward >= 1.0 else PALETTE["red"])

    return f"""
    <div style='background:{PALETTE["card"]};border:1px solid {PALETTE["border"]};
                border-left:4px solid {conv_color};border-radius:12px;
                padding:20px 24px;margin:14px 0;color:{PALETTE["text"]};
                font-family:Segoe UI,Arial,sans-serif;'>

      <!-- Header -->
      <div style='display:flex;align-items:center;justify-content:space-between;
                  margin-bottom:14px;flex-wrap:wrap;gap:8px;'>
        <div style='display:flex;align-items:center;gap:12px;flex-wrap:wrap;'>
          <span style='font-family:monospace;font-size:1.5rem;font-weight:800;color:{PALETTE["brand"]};'>{s.ticker}</span>
          <span style='font-family:monospace;font-size:1rem;color:{PALETTE["text"]};'>${s.spot:.2f}</span>
          <span style='background:{dir_bg};color:{dir_color};font-family:monospace;font-size:0.85rem;
                       font-weight:700;padding:4px 12px;border-radius:6px;'>{dir_arrow} {s.direction}</span>
        </div>
        <div style='display:flex;align-items:center;gap:8px;'>
          <span style='color:{conv_color};font-size:0.95rem;'>{stars}</span>
          <span style='background:{conv_color}20;color:{conv_color};font-family:monospace;
                       font-size:0.7rem;font-weight:700;padding:4px 10px;border-radius:6px;'>{conv_label}</span>
        </div>
      </div>

      <!-- Confluence factors -->
      <div style='background:{PALETTE["card_dark"]};border-radius:8px;padding:12px 16px;margin-bottom:14px;'>
        <div style='font-family:monospace;font-size:0.7rem;color:{PALETTE["text_muted"]};
                    letter-spacing:0.1em;margin-bottom:8px;'>✅ 3-OF-3 CONFLUENCE · {tf_badge}</div>

        <div style='padding:6px 0;'>
          <span style='color:{PALETTE["green"]};font-weight:700;font-family:monospace;font-size:0.78rem;'>✅ TECHNICAL</span>
          {pattern_html}
        </div>

        <div style='padding:6px 0;border-top:1px solid {PALETTE["border"]};margin-top:4px;'>
          <span style='color:{PALETTE["green"]};font-weight:700;font-family:monospace;font-size:0.78rem;'>✅ GEX</span>
          <div style='font-size:0.78rem;color:{PALETTE["text_dim"]};font-family:monospace;padding:4px 0;'>{s.gex_summary}</div>
        </div>

        <div style='padding:6px 0;border-top:1px solid {PALETTE["border"]};margin-top:4px;'>
          <span style='color:{PALETTE["green"]};font-weight:700;font-family:monospace;font-size:0.78rem;'>✅ WHALES</span>
          <div style='font-size:0.78rem;color:{PALETTE["text_dim"]};font-family:monospace;padding:4px 0;'>{s.whale_summary}</div>
        </div>
      </div>

      <!-- Trade Plan -->
      <div style='background:{PALETTE["card_dark"]};border-radius:8px;padding:14px 18px;'>
        <div style='font-family:monospace;font-size:0.7rem;color:{PALETTE["text_muted"]};
                    letter-spacing:0.1em;margin-bottom:10px;'>📋 TRADE PLAN</div>

        <div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;'>
          <div>
            <div style='font-size:0.68rem;color:{PALETTE["text_muted"]};font-family:monospace;'>STRIKE</div>
            <div style='font-size:1.1rem;color:{PALETTE["text"]};font-weight:700;font-family:monospace;'>${s.strike:.0f}</div>
          </div>
          <div>
            <div style='font-size:0.68rem;color:{PALETTE["text_muted"]};font-family:monospace;'>EXPIRY</div>
            <div style='font-size:0.9rem;color:{PALETTE["text"]};font-weight:600;font-family:monospace;'>{s.expiry or "—"}</div>
          </div>
          <div>
            <div style='font-size:0.68rem;color:{PALETTE["text_muted"]};font-family:monospace;'>ENTRY</div>
            <div style='font-size:0.95rem;color:{PALETTE["green"]};font-weight:700;font-family:monospace;'>${s.entry_above:.2f}</div>
          </div>
          <div>
            <div style='font-size:0.68rem;color:{PALETTE["text_muted"]};font-family:monospace;'>STOP</div>
            <div style='font-size:0.95rem;color:{PALETTE["red"]};font-weight:700;font-family:monospace;'>${s.stop_below:.2f}</div>
          </div>
          <div>
            <div style='font-size:0.68rem;color:{PALETTE["text_muted"]};font-family:monospace;'>TARGET</div>
            <div style='font-size:0.95rem;color:{PALETTE["brand"]};font-weight:700;font-family:monospace;'>${s.target:.2f}</div>
          </div>
          <div>
            <div style='font-size:0.68rem;color:{PALETTE["text_muted"]};font-family:monospace;'>R/R</div>
            <div style='font-size:0.95rem;color:{rr_color};font-weight:700;font-family:monospace;'>1:{s.risk_reward:.1f}</div>
          </div>
        </div>

        <div style='margin-top:12px;padding-top:10px;border-top:1px solid {PALETTE["border"]};
                    font-size:0.74rem;color:{PALETTE["text_dim"]};font-family:monospace;'>
          Hold: <b style='color:{PALETTE["text"]};'>{s.hold_days}</b>
          {f' · Strike OI: <b style="color:{PALETTE["text"]};">{s.strike_oi:,}</b> {s.oi_quality}' if s.strike_oi else ''}
          {f' · Support: <b style="color:{PALETTE["text"]};">${s.support_level:.2f}</b>' if s.support_level else ''}
          {f' · Resistance: <b style="color:{PALETTE["text"]};">${s.resistance_level:.2f}</b>' if s.resistance_level else ''}
        </div>
      </div>
    </div>
    """


# ── Full email/dashboard report ───────────────────────────────────────────────

def build_swing_report(setups: List[ConfluenceSetup],
                        slot_label: str = "Swing Scan") -> str:
    """Build the full HTML report with all setups."""

    timestamp = datetime.now().strftime("%A, %B %d, %Y · %I:%M %p CT")

    # Header
    header = f"""
    <div style='background:linear-gradient(90deg,#0a0518,#1a0d3a);
                border:1px solid #2a1a4a;border-radius:14px;padding:24px;margin-bottom:20px;'>
      <h1 style='margin:0;font-family:Syne,sans-serif;font-size:2rem;font-weight:800;
                 background:linear-gradient(90deg,{PALETTE["brand"]},#6a5aff,{PALETTE["green"]});
                 -webkit-background-clip:text;-webkit-text-fill-color:transparent;'>
        🎯 SwingConfluence
      </h1>
      <div style='color:{PALETTE["text_dim"]};font-family:monospace;font-size:0.85rem;margin-top:6px;'>
        {slot_label} · {timestamp}
      </div>
      <div style='color:{PALETTE["text_muted"]};font-family:monospace;font-size:0.78rem;margin-top:4px;'>
        3-of-3 confluence setups · Technical + GEX + Whale Flow · 1-3 day swings
      </div>
    </div>
    """

    # Summary
    if not setups:
        body = f"""
        <div style='background:{PALETTE["card"]};border:1px solid {PALETTE["border"]};
                    border-radius:12px;padding:40px;text-align:center;'>
          <div style='font-size:3rem;'>🔍</div>
          <div style='color:{PALETTE["text"]};font-family:monospace;font-size:1.1rem;margin-top:12px;'>
            No confluence setups detected
          </div>
          <div style='color:{PALETTE["text_dim"]};font-family:monospace;font-size:0.85rem;margin-top:8px;'>
            All 36 tickers scanned. None meet the 3-of-3 confluence threshold today.
          </div>
          <div style='color:{PALETTE["text_muted"]};font-family:monospace;font-size:0.75rem;margin-top:14px;'>
            This is normal — patience is part of the edge.
          </div>
        </div>
        """
    else:
        max_count    = sum(1 for s in setups if s.conviction == 6)
        high_count   = sum(1 for s in setups if s.conviction == 5)
        medium_count = sum(1 for s in setups if s.conviction == 4)
        call_count   = sum(1 for s in setups if s.direction == "CALL")
        put_count    = sum(1 for s in setups if s.direction == "PUT")

        summary = f"""
        <div style='background:{PALETTE["card"]};border:1px solid {PALETTE["border"]};
                    border-radius:12px;padding:18px 22px;margin-bottom:18px;'>
          <div style='font-family:monospace;font-size:0.7rem;color:{PALETTE["text_muted"]};
                      letter-spacing:0.1em;margin-bottom:10px;'>📊 SCAN SUMMARY</div>
          <div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px;'>
            <div>
              <div style='font-size:0.7rem;color:{PALETTE["text_muted"]};font-family:monospace;'>TOTAL SETUPS</div>
              <div style='font-size:1.5rem;color:{PALETTE["text"]};font-weight:800;font-family:monospace;'>{len(setups)}</div>
            </div>
            <div>
              <div style='font-size:0.7rem;color:{PALETTE["text_muted"]};font-family:monospace;'>MAX (6★)</div>
              <div style='font-size:1.5rem;color:{PALETTE["brand"]};font-weight:800;font-family:monospace;'>{max_count}</div>
            </div>
            <div>
              <div style='font-size:0.7rem;color:{PALETTE["text_muted"]};font-family:monospace;'>HIGH (5★)</div>
              <div style='font-size:1.5rem;color:{PALETTE["green"]};font-weight:800;font-family:monospace;'>{high_count}</div>
            </div>
            <div>
              <div style='font-size:0.7rem;color:{PALETTE["text_muted"]};font-family:monospace;'>MED (4★)</div>
              <div style='font-size:1.5rem;color:{PALETTE["gold"]};font-weight:800;font-family:monospace;'>{medium_count}</div>
            </div>
            <div>
              <div style='font-size:0.7rem;color:{PALETTE["text_muted"]};font-family:monospace;'>CALLS</div>
              <div style='font-size:1.5rem;color:{PALETTE["green"]};font-weight:800;font-family:monospace;'>{call_count}</div>
            </div>
            <div>
              <div style='font-size:0.7rem;color:{PALETTE["text_muted"]};font-family:monospace;'>PUTS</div>
              <div style='font-size:1.5rem;color:{PALETTE["red"]};font-weight:800;font-family:monospace;'>{put_count}</div>
            </div>
          </div>
        </div>
        """

        cards = "".join(render_setup_card(s) for s in setups)
        body  = summary + cards

    # Footer
    footer = f"""
    <div style='margin-top:24px;padding:18px;background:{PALETTE["card_dark"]};
                border-radius:8px;text-align:center;'>
      <div style='color:{PALETTE["text_muted"]};font-family:monospace;font-size:0.72rem;line-height:1.6;'>
        SwingConfluence · Alpaca real-time data · 3-of-3 confluence required<br>
        Educational use only · Not financial advice
      </div>
    </div>
    """

    full = f"""
    <!DOCTYPE html>
    <html><body style='background:{PALETTE["bg"]};margin:0;padding:0;'>
      <div style='max-width:900px;margin:0 auto;padding:24px;'>
        {header}
        {body}
        {footer}
      </div>
    </body></html>
    """
    return full
