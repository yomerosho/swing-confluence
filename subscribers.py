"""
subscribers.py — Shared Subscriber Management
==============================================
Loads, validates, and manages the subscriber list.
Used by both the Streamlit dashboard and the scheduled email runner.

subscribers.txt format:
    # Lines starting with # are comments
    # One subscriber per line. Format: email OR name,email
    yshobowa@gmail.com
    John,john@example.com
"""

import os
import re
from typing import List, Dict, Tuple

SUBSCRIBERS_FILE = "subscribers.txt"

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_REGEX.match(email.strip()))


def load_subscribers(path: str = SUBSCRIBERS_FILE) -> List[Dict[str, str]]:
    """
    Returns list of {"email": str, "name": str} dicts.
    Skips comments and blank lines. Skips invalid emails (logged).
    """
    subs = []
    if not os.path.exists(path):
        return subs

    with open(path) as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = [p.strip() for p in line.split(",")]
            email = next((p for p in parts if "@" in p), None)
            name  = next((p for p in parts if "@" not in p), email.split("@")[0] if email else "")

            if email and is_valid_email(email):
                # Dedupe
                if not any(s["email"].lower() == email.lower() for s in subs):
                    subs.append({"email": email.lower(), "name": name})
    return subs


def get_emails(path: str = SUBSCRIBERS_FILE) -> List[str]:
    """Convenience: just the email addresses."""
    return [s["email"] for s in load_subscribers(path)]


def save_subscribers(subs: List[Dict[str, str]],
                     path: str = SUBSCRIBERS_FILE,
                     header: str = None) -> None:
    """Persist subscriber list back to file (preserves a header comment)."""
    if header is None:
        header = (
            "# SwingConfluence Email Subscribers\n"
            "# Format: email OR name,email — one per line.\n"
            "# Lines starting with # are comments.\n"
            "# To unsubscribe, remove the line and commit/push.\n\n"
        )
    with open(path, "w") as f:
        f.write(header)
        for s in subs:
            if s["name"] and s["name"] != s["email"].split("@")[0]:
                f.write(f"{s['name']},{s['email']}\n")
            else:
                f.write(f"{s['email']}\n")


def add_subscriber(email: str, name: str = "",
                   path: str = SUBSCRIBERS_FILE) -> Tuple[bool, str]:
    """Add a subscriber. Returns (success, message)."""
    email = email.strip().lower()
    name  = name.strip()

    if not is_valid_email(email):
        return False, f"Invalid email: {email}"

    subs = load_subscribers(path)
    if any(s["email"] == email for s in subs):
        return False, f"Already subscribed: {email}"

    subs.append({"email": email, "name": name or email.split("@")[0]})
    save_subscribers(subs, path)
    return True, f"Added: {email}"


def remove_subscriber(email: str, path: str = SUBSCRIBERS_FILE) -> Tuple[bool, str]:
    """Remove a subscriber. Returns (success, message)."""
    email = email.strip().lower()
    subs  = load_subscribers(path)
    new   = [s for s in subs if s["email"] != email]

    if len(new) == len(subs):
        return False, f"Not found: {email}"

    save_subscribers(new, path)
    return True, f"Removed: {email}"
