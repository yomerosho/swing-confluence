"""
subscribers.py — Shared Subscriber Management
==============================================
Loads, validates, and manages the SwingConfluence email subscriber list.
Used by both the Streamlit dashboard and the scheduled email runner.

────────────────────────────────────────────────────────────────────────────
HOW TO MANAGE SUBSCRIBERS (the easy way)
────────────────────────────────────────────────────────────────────────────
Just edit the SUBSCRIBERS list right below. Each entry is either:

    "friend@gmail.com"                  # email only
    ("Jane", "jane@example.com")        # name + email

Then commit & push to GitHub. That's it — the change is permanent and works
on Streamlit Cloud (whose filesystem is otherwise wiped on every restart).

You can STILL add/remove people from the Streamlit dashboard's "Manage Email
Subscribers" panel, but those changes write to subscribers.txt and are only
temporary on Streamlit Cloud. The SUBSCRIBERS list here is the durable
source of truth and is always included.
────────────────────────────────────────────────────────────────────────────
"""

import os
import re
from typing import List, Dict, Tuple, Union

# ── EDIT THIS LIST TO MANAGE EMAILS ───────────────────────────────────────────
# Add a plain email string, OR a (name, email) tuple. One entry per line.
# Commit + push after editing.
SUBSCRIBERS: List[Union[str, Tuple[str, str]]] = [
    "yshobowa@gmail.com",
    # ("Jane Doe", "jane@example.com"),
    # "friend@gmail.com",
    # "another.friend@outlook.com",
]
# ──────────────────────────────────────────────────────────────────────────────

SUBSCRIBERS_FILE = "subscribers.txt"

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_REGEX.match(email.strip()))


# ── Hardcoded list (the editable source of truth above) ───────────────────────

def _parse_entry(entry) -> Dict[str, str]:
    """Normalize a SUBSCRIBERS entry (str or (name, email)) into a dict."""
    if isinstance(entry, (tuple, list)):
        # (name, email) in either order — the part with "@" is the email
        parts = [str(p).strip() for p in entry]
        email = next((p for p in parts if "@" in p), "")
        name  = next((p for p in parts if "@" not in p), "")
    else:
        email = str(entry).strip()
        name  = ""
    if not name and email:
        name = email.split("@")[0]
    return {"email": email.lower(), "name": name}


def _hardcoded_subscribers() -> List[Dict[str, str]]:
    """Parsed, validated, de-duped entries from the SUBSCRIBERS list above."""
    out: List[Dict[str, str]] = []
    for entry in SUBSCRIBERS:
        sub = _parse_entry(entry)
        if sub["email"] and is_valid_email(sub["email"]):
            if not any(s["email"] == sub["email"] for s in out):
                out.append(sub)
    return out


# ── File-backed list (dashboard add/remove → subscribers.txt) ─────────────────

def _file_subscribers(path: str = SUBSCRIBERS_FILE) -> List[Dict[str, str]]:
    """
    Parsed entries from subscribers.txt.
    Skips comments and blank lines. Skips invalid emails.
    """
    subs: List[Dict[str, str]] = []
    if not os.path.exists(path):
        return subs

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = [p.strip() for p in line.split(",")]
            email = next((p for p in parts if "@" in p), None)
            if not email:
                continue
            name = next((p for p in parts if "@" not in p), email.split("@")[0])

            if is_valid_email(email):
                if not any(s["email"].lower() == email.lower() for s in subs):
                    subs.append({"email": email.lower(), "name": name})
    return subs


# ── Public API (unchanged signatures) ─────────────────────────────────────────

def load_subscribers(path: str = SUBSCRIBERS_FILE) -> List[Dict[str, str]]:
    """
    Returns list of {"email": str, "name": str} dicts.

    Merges the hardcoded SUBSCRIBERS list (durable source of truth) with any
    entries in subscribers.txt (dashboard-added, temporary on Streamlit Cloud).
    De-duped by email; the hardcoded list takes precedence on conflicts.
    """
    merged = _hardcoded_subscribers()
    for sub in _file_subscribers(path):
        if not any(s["email"] == sub["email"] for s in merged):
            merged.append(sub)
    return merged


def get_emails(path: str = SUBSCRIBERS_FILE) -> List[str]:
    """Convenience: just the email addresses (hardcoded + file, de-duped)."""
    return [s["email"] for s in load_subscribers(path)]


def save_subscribers(subs: List[Dict[str, str]],
                     path: str = SUBSCRIBERS_FILE,
                     header: str = None) -> None:
    """
    Persist a subscriber list back to subscribers.txt.

    NOTE: this only writes the .txt file. The hardcoded SUBSCRIBERS list in
    this module is never written here — edit it directly and push.
    """
    if header is None:
        header = (
            "# SwingConfluence Email Subscribers (dashboard-added)\n"
            "# Format: email OR name,email — one per line.\n"
            "# Lines starting with # are comments.\n"
            "# NOTE: the durable list lives in subscribers.py (SUBSCRIBERS).\n"
            "#       Entries here are temporary on Streamlit Cloud.\n\n"
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
    """Add a subscriber to subscribers.txt. Returns (success, message)."""
    email = email.strip().lower()
    name  = name.strip()

    if not is_valid_email(email):
        return False, f"Invalid email: {email}"

    # Already covered permanently by the hardcoded list?
    if any(s["email"] == email for s in _hardcoded_subscribers()):
        return False, f"Already in the permanent list (subscribers.py): {email}"

    subs = _file_subscribers(path)
    if any(s["email"] == email for s in subs):
        return False, f"Already subscribed: {email}"

    subs.append({"email": email, "name": name or email.split("@")[0]})
    save_subscribers(subs, path)
    return True, f"Added: {email}"


def remove_subscriber(email: str, path: str = SUBSCRIBERS_FILE) -> Tuple[bool, str]:
    """Remove a subscriber from subscribers.txt. Returns (success, message)."""
    email = email.strip().lower()

    # Hardcoded entries can't be removed from the UI — they live in code.
    if any(s["email"] == email for s in _hardcoded_subscribers()):
        return False, (f"{email} is in the SUBSCRIBERS list in subscribers.py — "
                       f"remove it there and push to GitHub.")

    subs = _file_subscribers(path)
    new  = [s for s in subs if s["email"] != email]

    if len(new) == len(subs):
        return False, f"Not found: {email}"

    save_subscribers(new, path)
    return True, f"Removed: {email}"
