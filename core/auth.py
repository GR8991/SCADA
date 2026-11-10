"""Email-allowlist login backed by Streamlit secrets.

Secrets layout (.streamlit/secrets.toml locally, or App settings -> Secrets on
Streamlit Community Cloud):

    [auth]
    allowed_emails = ["engineer1@company.com", "engineer2@company.com"]
    access_code = ""          # optional shared code; leave "" to disable

Emails are compared case-insensitively after trimming spaces.
"""
from __future__ import annotations

import hmac
import re
import time

import streamlit as st

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 300


def _normalise(email: str) -> str:
    return (email or "").strip().lower()


def _allowed_emails() -> set[str]:
    try:
        raw = st.secrets["auth"]["allowed_emails"]
    except (KeyError, FileNotFoundError):
        return set()
    if isinstance(raw, str):  # tolerate a comma-separated string
        raw = raw.split(",")
    return {_normalise(e) for e in raw if _normalise(e)}


def _access_code() -> str:
    try:
        return str(st.secrets["auth"].get("access_code", "") or "")
    except (KeyError, FileNotFoundError):
        return ""


def is_logged_in() -> bool:
    return bool(st.session_state.get("user_email"))


def current_user() -> str | None:
    return st.session_state.get("user_email")


def logout() -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.rerun()


def login_gate() -> bool:
    """Render the login form. Returns True once the user is authenticated."""
    if is_logged_in():
        return True

    allowed = _allowed_emails()
    code_required = bool(_access_code())

    st.markdown("## BESS SCADA & Protection Design Agent")
    st.caption("Authorised users only. Sign in with your registered email.")

    if not allowed:
        st.error(
            "No users are configured. Add `[auth] allowed_emails = [...]` to the "
            "app's Streamlit secrets."
        )
        return False

    locked_until = st.session_state.get("_locked_until", 0)
    if time.time() < locked_until:
        st.error(f"Too many failed attempts. Try again in {int(locked_until - time.time())} s.")
        return False

    with st.form("login_form", clear_on_submit=False):
        email = st.text_input("Email ID", placeholder="name@company.com")
        code = st.text_input("Access code", type="password") if code_required else ""
        submitted = st.form_submit_button("Sign in", type="primary", width="stretch")

    if submitted:
        e = _normalise(email)
        ok = bool(_EMAIL_RE.match(e)) and e in allowed
        if ok and code_required:
            ok = hmac.compare_digest(code.encode(), _access_code().encode())
        if ok:
            st.session_state["user_email"] = e
            st.session_state.pop("_failed", None)
            st.rerun()
        else:
            n = st.session_state.get("_failed", 0) + 1
            st.session_state["_failed"] = n
            if n >= MAX_ATTEMPTS:
                st.session_state["_locked_until"] = time.time() + LOCKOUT_SECONDS
                st.session_state["_failed"] = 0
            # Same message for unknown email and wrong code (no user enumeration).
            st.error("Access denied. This email is not authorised, or the access code is wrong.")
    return False
