"""
run_scheduled_email.py — SwingConfluence Scheduled Email Runner
=================================================================
Run by GitHub Actions at 3 daily slots: 4 PM / 8 AM / 1 PM CT.

Usage:
  python run_scheduled_email.py --slot close       (4 PM CT)
  python run_scheduled_email.py --slot premarket   (8 AM CT)
  python run_scheduled_email.py --slot lunch       (1 PM CT)
  python run_scheduled_email.py --slot close --dry-run  (preview HTML)
"""

import os
import sys
import argparse
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import logging

from swing_scanner import SwingScanner, ALL_TICKERS
from swing_html import build_swing_report
from subscribers import load_subscribers as load_subs

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


SLOT_LABELS = {
    "close":     "After-Close Swing Scan (4 PM CT)",
    "premarket": "Pre-Market Validation (8 AM CT)",
    "lunch":     "Mid-Day Confluence Check (1 PM CT)",
}


def load_subscribers(path="subscribers.txt"):
    """Wrapper that returns just email addresses for backward compat."""
    return [s["email"] for s in load_subs(path)]


def send_email(html: str, subject: str, recipients: list,
                gmail_user: str, gmail_pass: str) -> int:
    sent = 0
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(gmail_user, gmail_pass)
        for email in recipients:
            try:
                msg = MIMEMultipart("alternative")
                msg["Subject"] = subject
                msg["From"]    = gmail_user
                msg["To"]      = email
                msg.attach(MIMEText(html, "html"))
                smtp.send_message(msg)
                sent += 1
                logger.info(f"  ✅ sent to {email}")
            except Exception as e:
                logger.error(f"  ❌ failed for {email}: {e}")
    return sent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot", required=True, choices=SLOT_LABELS.keys())
    parser.add_argument("--dry-run", action="store_true",
                         help="Print HTML to stdout instead of sending")
    parser.add_argument("--skip-if-empty", action="store_true",
                         help="Don't send email if no setups found")
    args = parser.parse_args()

    # Validate env vars
    alpaca_key    = os.environ.get("ALPACA_KEY")
    alpaca_secret = os.environ.get("ALPACA_SECRET")
    gmail_user    = os.environ.get("GMAIL_USER")
    gmail_pass    = os.environ.get("GMAIL_APP_PASSWORD")

    if not alpaca_key or not alpaca_secret:
        logger.error("ALPACA_KEY/SECRET missing")
        sys.exit(1)

    if not args.dry_run and (not gmail_user or not gmail_pass):
        logger.error("Gmail credentials missing")
        sys.exit(1)

    # Run scan
    logger.info(f"=== SwingConfluence: {SLOT_LABELS[args.slot]} ===")
    scanner = SwingScanner(alpaca_key, alpaca_secret)

    def progress(pct, msg):
        logger.info(f"  [{int(pct*100):3d}%] {msg}")

    setups = scanner.scan_all(progress_cb=progress)
    logger.info(f"Found {len(setups)} confluence setup(s)")

    # Skip email if requested and no setups
    if args.skip_if_empty and not setups:
        logger.info("No setups + --skip-if-empty → exiting without email")
        return

    # Build HTML
    html = build_swing_report(setups, SLOT_LABELS[args.slot])

    if args.dry_run:
        print(html)
        return

    # Send
    recipients = load_subscribers()
    if not recipients:
        logger.error("No subscribers in subscribers.txt")
        sys.exit(1)

    subject = (f"🎯 SwingConfluence · {len(setups)} Setup(s) · "
               f"{SLOT_LABELS[args.slot]}")

    logger.info(f"Sending to {len(recipients)} recipient(s)...")
    sent = send_email(html, subject, recipients, gmail_user, gmail_pass)
    logger.info(f"✅ Sent to {sent}/{len(recipients)} recipient(s)")


if __name__ == "__main__":
    main()
