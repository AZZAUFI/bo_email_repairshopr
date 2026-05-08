#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Illegear Repair Notifier
========================
NO background threads — polling is driven by Streamlit's own rerun loop.
This guarantees exactly ONE API call per cycle, no duplicate threads, no 429 floods.
"""

import re
import streamlit as st
import requests
import smtplib
import sqlite3
import time
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, date, timedelta
import pandas as pd

# ── Config ─────────────────────────────────────────────────────────────────────
SUBDOMAIN     = "illegearticket"
FROM_EMAIL    = "support@illegear.com"
SMTP_HOST     = "mail.illegear.com"
SMTP_PORT     = 587
POLL_INTERVAL = 120   # seconds between API polls (2 min – safely under 180 req/min)

# ── Email validation ───────────────────────────────────────────────────────────
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Exact internal addresses that must NEVER receive customer notifications
EXCLUDED_EMAILS = {
    FROM_EMAIL.lower(),
    "support@illegear.com",
}

# Entire domains whose addresses are always internal — blocks ALL @illegear.com staff
EXCLUDED_DOMAINS = {
    "illegear.com",
}


def _is_valid_customer_email(email: str) -> bool:
    """Return True only if the address is well-formed and NOT an internal/staff address."""
    if not email:
        return False
    e = email.strip().lower()
    # Block known exact internal addresses
    if e in EXCLUDED_EMAILS:
        return False
    # Block entire internal domain — catches every @illegear.com variation
    domain = e.split("@")[-1] if "@" in e else ""
    if domain in EXCLUDED_DOMAINS:
        return False
    # Must match basic email format
    return bool(_EMAIL_RE.match(e))

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Illegear Repair Notifier",
    page_icon="🔧",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Mono:wght@300;400;500&display=swap');
:root {
    --bg:#0a0a0f; --surface:#12121a; --border:#1e1e2e;
    --accent:#ff3c3c; --accent2:#ff8c00;
    --text:#e8e8f0; --muted:#6b6b80; --green:#00e676; --yellow:#ffd600;
}
html,body,[class*="css"]{font-family:'DM Mono',monospace;background-color:var(--bg)!important;color:var(--text)!important;}
h1,h2,h3{font-family:'Syne',sans-serif!important;}
.stApp{background-color:var(--bg)!important;}
section[data-testid="stSidebar"]{background-color:var(--surface)!important;border-right:1px solid var(--border)!important;}
.stTextInput input,.stTextArea textarea,.stDateInput input{
    background-color:var(--surface)!important;border:1px solid var(--border)!important;
    color:var(--text)!important;font-family:'DM Mono',monospace!important;border-radius:4px!important;}
.stButton>button{
    background:var(--accent)!important;color:white!important;border:none!important;
    font-family:'Syne',sans-serif!important;font-weight:700!important;
    letter-spacing:.05em!important;border-radius:4px!important;
    padding:.5rem 1.5rem!important;transition:all .2s!important;}
.stButton>button:hover{opacity:.85!important;transform:translateY(-1px)!important;}
.metric-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:1.2rem 1.5rem;margin-bottom:.5rem;}
.metric-card .label{color:var(--muted);font-size:.75rem;letter-spacing:.1em;text-transform:uppercase;}
.metric-card .value{font-family:'Syne',sans-serif;font-size:2rem;font-weight:800;color:var(--accent);}
.log-row{background:var(--surface);border-left:3px solid var(--accent);padding:.6rem 1rem;margin-bottom:.4rem;border-radius:0 4px 4px 0;font-size:.82rem;}
.header-bar{display:flex;align-items:center;gap:1rem;margin-bottom:2rem;border-bottom:1px solid var(--border);padding-bottom:1rem;}
.bot-status{display:inline-flex;align-items:center;gap:.4rem;padding:4px 12px;border-radius:20px;font-size:.78rem;font-weight:600;}
.bot-on{background:#00e67615;color:var(--green);border:1px solid var(--green);}
.bot-off{background:#ff3c3c15;color:var(--accent);border:1px solid var(--accent);}
.pulse{width:8px;height:8px;border-radius:50%;background:currentColor;animation:pulse 1.5s infinite;}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.filter-pill{display:inline-block;padding:3px 12px;border-radius:20px;font-size:.75rem;font-weight:600;background:#ff3c3c20;color:var(--accent);border:1px solid var(--accent);margin-right:6px;}
.countdown{font-family:'Syne',sans-serif;font-size:1.1rem;color:var(--green);font-weight:700;}
</style>
""",
    unsafe_allow_html=True,
)

# ── Database (singleton) ───────────────────────────────────────────────────────
if hasattr(st, "cache_resource"):
    _cache_res = st.cache_resource
else:
    _cache_res = st.experimental_singleton


@_cache_res
def get_db():
    """Create (or reuse) a single SQLite connection for the app lifetime."""
    conn = sqlite3.connect("notifier.db", check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS notified (
            ticket_id TEXT PRIMARY KEY,
            customer_name TEXT,
            customer_email TEXT,
            ticket_number TEXT,
            device TEXT,
            status TEXT,
            updated_at TEXT,
            notified_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            level TEXT,
            message TEXT
        )"""
    )
    cols = [r[1] for r in conn.execute("PRAGMA table_info(notified)").fetchall()]
    for col in ["device", "status", "updated_at"]:
        if col not in cols:
            conn.execute(f"ALTER TABLE notified ADD COLUMN {col} TEXT DEFAULT ''")
    conn.commit()
    return conn


DB = get_db()

# ── DB helper wrappers ─────────────────────────────────────────────────────────
def already_notified(ticket_id: str) -> bool:
    return (
        DB.execute("SELECT 1 FROM notified WHERE ticket_id=?", (ticket_id,)).fetchone()
        is not None
    )


def mark_notified(ticket_id, name, email, number, device, status, updated_at):
    DB.execute(
        """INSERT OR IGNORE INTO notified (
            ticket_id, customer_name, customer_email, ticket_number,
            device, status, updated_at, notified_at
        ) VALUES (?,?,?,?,?,?,?,?)""",
        (
            str(ticket_id),
            name,
            email,
            str(number),
            device,
            status,
            updated_at,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    DB.commit()


def add_log(level, message):
    """Write a log entry — always coerces to str so SQLite never rejects it."""
    try:
        DB.execute(
            "INSERT INTO logs (ts, level, message) VALUES (?,?,?)",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(level), str(message)),
        )
        DB.commit()
    except Exception as log_err:
        print(f"[add_log failed] {log_err} | original: {level} {message}")


def get_logs(limit=100):
    return DB.execute(
        "SELECT ts, level, message FROM logs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def get_notified_list():
    return DB.execute(
        """SELECT ticket_number, customer_name, customer_email,
                  device, status, updated_at, notified_at
           FROM notified ORDER BY notified_at DESC"""
    ).fetchall()


MAX_LOG_ROWS = 10_000


def prune_logs():
    cur = DB.execute("SELECT COUNT(*) FROM logs")
    total = cur.fetchone()[0]
    if total > MAX_LOG_ROWS:
        DB.execute(
            f"""DELETE FROM logs WHERE id <= (
                SELECT id FROM logs ORDER BY id DESC LIMIT 1 OFFSET {MAX_LOG_ROWS}
            )"""
        )
        DB.commit()


# ── API ────────────────────────────────────────────────────────────────────────
def api_get(api_key, path, params=None):
    url = f"https://{SUBDOMAIN}.repairshopr.com/api/v1/{path}"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        resp = requests.get(url, headers=headers, params=params or {}, timeout=15)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 60))
            st.session_state.last_poll = time.time() + wait
            return None, f"429 – rate limited, retry after {wait}s"
        resp.raise_for_status()
        return resp.json(), None
    except Exception as e:
        return None, str(e)


def fetch_ready_tickets(api_key, status_filter, date_from=None, date_to=None):
    """Return tickets matching status. Optionally filter by updated_at date range."""
    params = {"status": status_filter, "per_page": 100, "page": 1}
    # RepairShopr supports since/until as ISO date strings
    if date_from:
        params["since"] = date_from.strftime("%Y-%m-%d")
    if date_to:
        params["until"] = date_to.strftime("%Y-%m-%d")
    data, err = api_get(api_key, "tickets", params)
    if err:
        return [], err
    tickets = data.get("tickets", [])
    # Client-side guard: also filter updated_at locally in case API ignores the params
    if date_from or date_to:
        filtered = []
        for t in tickets:
            upd_str = (t.get("updated_at") or "")[:10]
            try:
                upd = date.fromisoformat(upd_str)
            except ValueError:
                filtered.append(t)
                continue
            if date_from and upd < date_from:
                continue
            if date_to and upd > date_to:
                continue
            filtered.append(t)
        return filtered, None
    return tickets, None


# ── Email ──────────────────────────────────────────────────────────────────────
def send_email(smtp_pass, to_email, customer_name, ticket_number, device, template):
    """Send a multipart (plain + HTML) email via the configured SMTP server."""
    # ── Hard safety gate — NEVER send to internal/sender addresses ──────────
    if not _is_valid_customer_email(to_email):
        return False, f"Blocked: '{to_email}' is an internal or invalid address — not sent"
    # ────────────────────────────────────────────────────────────────────────
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Your device is ready for collection! Ticket #{ticket_number}"
    msg["From"] = f"Illegear Support <{FROM_EMAIL}>"
    msg["To"] = to_email

    # Plain-text fallback
    body = (
        template.replace("{name}", customer_name)
        .replace("{ticket}", str(ticket_number))
        .replace("{device}", device or "your device")
    )

    # Clean HTML version
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;background:#ffffff;
                border:1px solid #e0e0e0;border-radius:8px;overflow:hidden;">

      <!-- Header -->
      <div style="background:#cc0000;padding:24px 32px;">
        <h1 style="margin:0;font-size:20px;color:#ffffff;letter-spacing:0.5px;">Illegear Repair</h1>
        <p style="margin:4px 0 0;color:#ffcccc;font-size:13px;">Device Ready for Collection</p>
      </div>

      <!-- Body -->
      <div style="padding:32px;color:#333333;">
        <p style="font-size:15px;">Hi <strong>{customer_name}</strong>,</p>
        <p style="font-size:15px;line-height:1.6;">
          Good news! Your device has been repaired and is now
          <strong style="color:#cc0000;">ready for collection</strong>.
        </p>
                <p style="font-size:15px;line-height:1.6;">
         Please ignore this email if your device already collected.
        </p>

        <!-- Ticket Info Box -->
        <div style="background:#f7f7f7;border-left:4px solid #cc0000;
                    border-radius:4px;padding:16px 20px;margin:24px 0;">
          <p style="margin:0 0 4px;font-size:11px;color:#888888;text-transform:uppercase;
                    letter-spacing:0.8px;">Repair Ticket</p>
          <p style="margin:0;font-size:22px;font-weight:bold;color:#cc0000;">#{ticket_number}</p>
          <p style="margin:6px 0 0;font-size:13px;color:#555555;">{device or 'Your device'}</p>
        </div>

        <p style="font-size:14px;color:#444444;line-height:1.6;">
          Please bring this ticket number when collecting your device from our service centre.
          If you have any questions, feel free to reply to this email.
        </p>

        <p style="font-size:14px;color:#444444;margin-top:28px;">
          Thank you for choosing Illegear.<br>
          <strong>Illegear Support Team</strong>
        </p>
      </div>

      <!-- Footer -->
      <div style="background:#f0f0f0;padding:14px 32px;text-align:center;
                  border-top:1px solid #e0e0e0;">
        <p style="margin:0;font-size:11px;color:#999999;">
          {FROM_EMAIL} &nbsp;|&nbsp; This is an automated message, please do not reply directly.
        </p>
      </div>

    </div>"""

    msg.attach(MIMEText(body, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(FROM_EMAIL, smtp_pass)
            s.sendmail(FROM_EMAIL, to_email, msg.as_string())
        return True, None
    except Exception as e:
        return False, str(e)


# ── Helper: robust extraction of name & e‑mail ────────────────────────────────
def _extract_contact(ticket: dict) -> tuple[str, str]:
    """
    Try every known place where RepairShopr stores a contact name / e-mail.
    Returns (name, email). email may be empty → the bot will skip that ticket.
    Internal/staff addresses are always rejected.
    """
    # 1️⃣ Customer object (most common)
    cust = ticket.get("customer") or {}
    name = (
        cust.get("fullname")
        or cust.get("name")
        or f"{cust.get('firstname','')} {cust.get('lastname','')}".strip()
    )
    email = cust.get("email")
    if email and not _is_valid_customer_email(email):
        email = None  # reject internal address, keep looking

    # 2️⃣ Contact object
    if not email:
        contact = ticket.get("contact") or {}
        candidate = contact.get("email")
        if candidate and _is_valid_customer_email(candidate):
            email = candidate
        if not name:
            name = f"{contact.get('firstname','')} {contact.get('lastname','')}".strip()

    # 3️⃣ Legacy "requester" field
    if not email:
        requester = ticket.get("requester") or {}
        candidate = requester.get("email")
        if candidate and _is_valid_customer_email(candidate):
            email = candidate
        if not name:
            name = requester.get("name")

    # 4️⃣ Root-level shortcuts
    if not email:
        for key in ("email", "contact_email"):
            candidate = ticket.get(key)
            if candidate and _is_valid_customer_email(candidate):
                email = candidate
                break
    if not name:
        name = ticket.get("name") or ticket.get("customer_name")

    # 5️⃣ Comments → destination_emails
    if not email:
        comments = ticket.get("comments", [])
        if comments:
            dest = comments[0].get("destination_emails", "")
            for candidate in [e.strip() for e in dest.split(",") if e.strip()]:
                if _is_valid_customer_email(candidate):
                    email = candidate
                    break

    # 6️⃣ Custom fields
    if not email:
        cf = ticket.get("custom_fields", {})
        for key in ("client_email",):
            candidate = cf.get(key)
            if candidate and _is_valid_customer_email(candidate):
                email = candidate
                break

    # 7️⃣ Staff user fallback — BLOCKED intentionally to prevent sending to
    #    internal addresses or ex-staff Gmail accounts that no longer exist.
    #    Do NOT re-enable without an explicit allow-list check.

    # 8️⃣ Normalise
    name = (name or "").strip() or "Customer"
    email = (email or "").strip()

    add_log(
        "DEBUG",
        f"Extracted contact → ticket #{ticket.get('number','?')}: name='{name}' email='{email or '—'}'",
    )
    return name, email


# ── Core poll function (no threads) ───────────────────────────────────────────
def run_poll(api_key, smtp_pass, status_filter, template, date_from=None, date_to=None):
    """One full scan: fetch tickets → send emails → log results."""
    tickets, err = fetch_ready_tickets(api_key, status_filter, date_from, date_to)

    if err:
        add_log("ERROR", f"API error: {err}")
        return

    new_count = 0
    for idx, t in enumerate(tickets):
        tid = str(t.get("id"))

        if idx < 2:
            add_log(
                "DEBUG",
                f"TICKET RAW {tid}: {json.dumps(t, default=str)[:500]}…",
            )

        number = str(t.get("number", tid))
        device = t.get("subject", "")
        status = t.get("status", "")
        upd = (t.get("updated_at") or "")[:19]

        name, email = _extract_contact(t)

        # Skip: no email, invalid/internal email, or already notified
        if already_notified(tid):
            continue
        if not email:
            add_log("WARN", f"Skipped ticket #{number} — no customer email found")
            continue
        if not _is_valid_customer_email(email):
            add_log("WARN", f"Skipped ticket #{number} — blocked internal/invalid email: '{email}'")
            continue

        ok, err2 = send_email(smtp_pass, email, name, number, device, template)
        if ok:
            mark_notified(tid, name, email, number, device, status, upd)
            add_log("OK", f"Notified {name} ({email}) — Ticket #{number}")
            new_count += 1
        else:
            add_log("ERROR", f"Email failed for {name} ({email}): {str(err2)}")

    add_log(
        "INFO",
        f"Scan done — {len(tickets)} ticket(s) matched, {new_count} new notification(s)",
    )
    prune_logs()


# ── Session‑state helpers ──────────────────────────────────────────────────────
def _ss(key, default):
    if key not in st.session_state:
        st.session_state[key] = default


_ss("bot_on", False)
_ss("api_key", "")
_ss("smtp_pass", "")
_ss("status_filter", "Device is Ready for Collection")
_ss("filter_mode", "Latest Status (Live)")
_ss("date_from", date.today() - timedelta(days=7))
_ss("date_to", date.today())
_ss("use_date_filter", False)
_ss("last_poll", 0.0)
_ss(
    "email_template",
    "Hi {name},\n\nYour device ({device}) is ready for collection.\nTicket: #{ticket}\n\nThank you!\nIllegear Support Team",
)

# ── Sidebar UI ─────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ Configuration")
    st.markdown("---")
    st.session_state.api_key = st.text_input(
        "RepairShopr API Key",
        value=st.session_state.api_key,
        type="password",
        placeholder="your-api-key",
    )
    st.text_input("Subdomain", value=SUBDOMAIN, disabled=True)
    st.text_input("From Email", value=FROM_EMAIL, disabled=True)
    st.text_input("SMTP Server", value=f"{SMTP_HOST}:{SMTP_PORT}", disabled=True)
    st.session_state.smtp_pass = st.text_input(
        "Email Password",
        value=st.session_state.smtp_pass,
        type="password",
        placeholder="••••••••",
    )

    st.markdown("---")
    st.markdown("### 🎯 Trigger Status")
    st.session_state.status_filter = st.selectbox(
        "Notify when status is",
        [
            "Device is Ready for Collection",
            "Ready for Pickup",
            "Customer Notified",
            "Waiting for Parts",
            "Resolved",
        ],
        index=0,
    )

    st.markdown("---")
    st.markdown("### 📧 Email Template")
    st.caption("Placeholders: `{name}` · `{ticket}` · `{device}`")
    st.session_state.email_template = st.text_area(
        "Template",
        value=st.session_state.email_template,
        height=140,
        label_visibility="collapsed",
    )

    st.markdown("---")
    st.markdown("### 📅 Date Range Filter")
    st.session_state.use_date_filter = st.toggle(
        "Filter by ticket updated date", value=st.session_state.use_date_filter
    )
    if st.session_state.use_date_filter:
        st.session_state.date_from = st.date_input(
            "From", value=st.session_state.date_from, key="df_from"
        )
        st.session_state.date_to = st.date_input(
            "To", value=st.session_state.date_to, key="df_to"
        )
        if st.session_state.date_from > st.session_state.date_to:
            st.error("'From' must be before 'To'.")
        else:
            st.caption(
                f"Only tickets updated {st.session_state.date_from} → {st.session_state.date_to} will trigger emails."
            )

    st.markdown("---")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("▶ Start", disabled=st.session_state.bot_on):
            if not st.session_state.api_key or not st.session_state.smtp_pass:
                st.error("Fill API key & password first.")
            else:
                st.session_state.bot_on = True
                st.session_state.last_poll = 0.0
                add_log(
                    "INFO",
                    f"Bot enabled | status='{st.session_state.status_filter}' | interval={POLL_INTERVAL}s",
                )
                st.rerun()
    with c2:
        if st.button("⏹ Stop", disabled=not st.session_state.bot_on):
            st.session_state.bot_on = False
            add_log("INFO", "Bot disabled by user")
            st.rerun()

# ── Poll timer logic ───────────────────────────────────────────────────────────
now = time.time()
seconds_since_last = now - st.session_state.last_poll
seconds_until_next = max(0, POLL_INTERVAL - seconds_since_last)

if st.session_state.bot_on:
    if seconds_since_last >= POLL_INTERVAL:
        with st.spinner("🔍 Checking RepairShopr..."):
            _df = st.session_state.date_from if st.session_state.use_date_filter else None
            _dt = st.session_state.date_to   if st.session_state.use_date_filter else None
            run_poll(
                st.session_state.api_key,
                st.session_state.smtp_pass,
                st.session_state.status_filter,
                st.session_state.email_template,
                date_from=_df,
                date_to=_dt,
            )
        st.session_state.last_poll = time.time()
        seconds_until_next = POLL_INTERVAL

# ── Header UI ─────────────────────────────────────────────────────────────────
status_html = (
    '<span class="bot-status bot-on"><span class="pulse"></span>BOT RUNNING</span>'
    if st.session_state.bot_on
    else '<span class="bot-status bot-off"><span class="pulse"></span>BOT STOPPED</span>'
)

st.markdown(
    f"""
<div class="header-bar">
  <div>
    <h1 style="margin:0;font-family:Syne,sans-serif;font-size:1.8rem;font-weight:800;letter-spacing:.04em;">
      🔧 ILLEGEAR <span style="color:#ff3c3c;">REPAIR NOTIFIER</span>
    </h1>
    <p style="margin:4px 0 0;color:#6b6b80;font-size:.8rem;">
      {SUBDOMAIN}.repairshopr.com&nbsp;·&nbsp;
      <span class="filter-pill">{st.session_state.status_filter}</span>
    </p>
  </div>
  <div style="margin-left:auto">{status_html}</div>
</div>
""",
    unsafe_allow_html=True,
)

# ── Metrics cards ──────────────────────────────────────────────────────────────
notified_list = get_notified_list()
logs = get_logs(200)
errors = [l for l in logs if l[1] == "ERROR"]

c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown(
        f'<div class="metric-card"><div class="label">Customers Notified</div>'
        f'<div class="value">{len(notified_list)}</div></div>',
        unsafe_allow_html=True,
    )
with c2:
    st.markdown(
        f'<div class="metric-card"><div class="label">Log Entries</div>'
        f'<div class="value" style="color:#ff8c00">{len(logs)}</div></div>',
        unsafe_allow_html=True,
    )
with c3:
    ec = "ff3c3c" if errors else "00e676"
    st.markdown(
        f'<div class="metric-card"><div class="label">Errors</div>'
        f'<div class="value" style="color:#{ec}">{len(errors)}</div></div>',
        unsafe_allow_html=True,
    )
with c4:
    if st.session_state.bot_on:
        val = f'<span class="countdown">{int(seconds_until_next)}s</span>'
        lbl = "Next Poll In"
    else:
        val = '<span style="color:#6b6b80">—</span>'
        lbl = "Next Poll In"
    st.markdown(
        f'<div class="metric-card"><div class="label">{lbl}</div>'
        f'<div class="value" style="font-size:1.4rem">{val}</div></div>',
        unsafe_allow_html=True,
    )

st.markdown("---")

# ── Tabs ───────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(
    ["📋 Live Logs", "✅ Notified Customers", "🔍 Manual Check", "🧪 Diagnostics"]
)

# ── Tab 1: Live Logs ───────────────────────────────────────────────────────────
with tab1:
    ca, cb = st.columns([3, 1])
    with ca:
        st.markdown("#### Activity Log")
    with cb:
        if st.button("🔄 Refresh"):
            st.rerun()
    fresh = get_logs(100)
    if not fresh:
        st.info("No activity yet. Start the bot or run a manual check.")
    else:
        for ts, level, msg in fresh:
            color = {"OK": "#00e676", "ERROR": "#ff3c3c", "INFO": "#ff8c00"}.get(
                level, "#6b6b80"
            )
            st.markdown(
                f'<div class="log-row" style="border-left-color:{color}">'
                f'<span style="color:#6b6b80">{ts}</span> '
                f'<span style="color:{color};font-weight:600">[{level}]</span> {msg}'
                f"</div>",
                unsafe_allow_html=True,
            )

# ── Tab 2: Notified Customers ──────────────────────────────────────────────────
with tab2:
    st.markdown("#### Customers Successfully Notified")
    if not notified_list:
        st.info("No customers notified yet.")
    else:
        df = pd.DataFrame(
            notified_list,
            columns=[
                "Ticket #",
                "Customer Name",
                "Email",
                "Device",
                "Status",
                "Updated At",
                "Notified At",
            ],
        )
        # ── Date range filter for notified list ──
        t2c1, t2c2 = st.columns(2)
        with t2c1:
            t2_from = st.date_input("Notified from", value=date.today() - timedelta(days=30), key="t2_from")
        with t2c2:
            t2_to = st.date_input("Notified to", value=date.today(), key="t2_to")
        t2_use = st.checkbox("Filter by notified date", value=False, key="t2_use")
        if t2_use:
            mask = pd.to_datetime(df["Notified At"], errors="coerce").dt.date
            df = df[(mask >= t2_from) & (mask <= t2_to)]
            st.caption(f"Showing {len(df)} record(s) between {t2_from} → {t2_to}")
        st.dataframe(df, use_container_width=True, hide_index=True)

# ── Tab 3: Manual Ticket Check ─────────────────────────────────────────────────
with tab3:
    st.markdown("#### Manual Ticket Check")
    manual_status = st.selectbox(
        "Filter by status",
        [
            "Device is Ready for Collection",
            "Ready for Pickup",
            "Customer Notified",
            "Waiting for Parts",
            "Resolved",
            "— Show All —",
        ],
        key="ms",
    )

    mc1, mc2 = st.columns(2)
    with mc1:
        manual_date_from = st.date_input(
            "Updated from", value=date.today() - timedelta(days=7), key="mc_from"
        )
    with mc2:
        manual_date_to = st.date_input(
            "Updated to", value=date.today(), key="mc_to"
        )
    manual_use_dates = st.checkbox("Apply date range filter", value=False, key="mc_use_dates")

    if st.button("🔍 Fetch Tickets Now"):
        if not st.session_state.api_key:
            st.error("Enter API key in sidebar first.")
        elif manual_use_dates and manual_date_from > manual_date_to:
            st.error("'From' date must be before 'To' date.")
        else:
            with st.spinner("Fetching..."):
                s = "" if manual_status == "— Show All —" else manual_status
                _mdf = manual_date_from if manual_use_dates else None
                _mdt = manual_date_to   if manual_use_dates else None
                tickets, err = fetch_ready_tickets(
                    st.session_state.api_key, s, _mdf, _mdt
                ) if s else ([], None)
                # for "Show All" we still need to call api_get directly
                if not s:
                    data, err = api_get(
                        st.session_state.api_key,
                        "tickets",
                        {"per_page": 100, "page": 1},
                    )
                    tickets = data.get("tickets", []) if data else []
                    # apply client-side date filter for "Show All"
                    if manual_use_dates and tickets:
                        def _in_range(t):
                            upd_str = (t.get("updated_at") or "")[:10]
                            try:
                                upd = date.fromisoformat(upd_str)
                                return _mdf <= upd <= _mdt
                            except ValueError:
                                return True
                        tickets = [t for t in tickets if _in_range(t)]
            if err:
                st.error(f"API error: {err}")
            elif tickets is not None:
                if tickets:
                    with st.expander("🔬 Full JSON of the first ticket"):
                        st.json(tickets[0])

                    rows = []
                    for t in tickets:
                        number = f"#{t.get('number','')}"
                        device = t.get("subject", "—")
                        status = t.get("status", "—")
                        updated = (t.get("updated_at") or "")[:10]

                        name, email = _extract_contact(t)

                        rows.append(
                            [
                                number,
                                name,
                                email or "—",
                                device[:50],
                                status,
                                updated,
                                "✅" if already_notified(str(t.get("id"))) else "❌",
                            ]
                        )
                    st.dataframe(
                        pd.DataFrame(
                            rows,
                            columns=[
                                "#",
                                "Customer",
                                "Email",
                                "Device",
                                "Status",
                                "Updated",
                                "Notified?",
                            ],
                        ),
                        use_container_width=True,
                        hide_index=True,
                    )
                    st.success(f"Found **{len(tickets)}** ticket(s).")
                else:
                    st.warning("No tickets found with that status.")

# ── Tab 4: Diagnostics ────────────────────────────────────────────────────────
with tab4:
    st.markdown("#### 🧪 Connection Diagnostics")
    st.markdown("---")

    st.markdown("##### 1️⃣ Test API Key")
    if st.button("🔑 Test API Connection"):
        if not st.session_state.api_key:
            st.error("Enter API key in sidebar first.")
        else:
            with st.spinner("Connecting..."):
                data, err = api_get(
                    st.session_state.api_key, "tickets", {"per_page": 5, "page": 1}
                )
            if err:
                st.error(f"❌ {err}")
            else:
                tickets = data.get("tickets", [])
                total = data.get("meta", {}).get("total_count", "?")
                st.success(f"✅ API connected! Total tickets: **{total}**")
                if tickets:
                    data2, _ = api_get(
                        st.session_state.api_key, "tickets", {"per_page": 100, "page": 1}
                    )
                    all_t = data2.get("tickets", []) if data2 else []
                    statuses = sorted(
                        {t.get("status", "") for t in all_t if t.get("status")}
                    )
                    st.markdown("**Statuses in recent tickets:**")
                    for s in statuses:
                        note = " ← ✅ MATCHES trigger" if s == st.session_state.status_filter else ""
                        st.code(f"{s}{note}")

    st.markdown("---")

    st.markdown("##### 2️⃣ Test Email (SMTP)")
    test_to = st.text_input("Send test to", placeholder="youremail@example.com")
    if st.button("📧 Send Test Email"):
        if not st.session_state.smtp_pass:
            st.error("Enter email password in sidebar first.")
        elif not test_to:
            st.error("Enter a recipient e‑mail.")
        else:
            with st.spinner(f"Sending to {test_to}…"):
                ok, err = send_email(
                    st.session_state.smtp_pass,
                    test_to,
                    "Test Customer",
                    "0000",
                    "Test Device",
                    "Hi {name}, this is a test email from Illegear Notifier. Ticket #{ticket}.",
                )
            if ok:
                st.success(f"✅ Test e‑mail sent to **{test_to}**! Check your inbox.")
                add_log("OK", f"SMTP test sent to {test_to}")
            else:
                st.error(f"❌ Failed: {err}")
                add_log("ERROR", f"SMTP test failed: {str(err)}")

    st.markdown("---")

    st.markdown("##### 3️⃣ Run One Poll Now")
    st.caption("Manually trigger one scan without waiting for the timer.")
    if st.button("▶ Run Poll Now"):
        if not st.session_state.api_key or not st.session_state.smtp_pass:
            st.error("Fill in API key and password first.")
        else:
            with st.spinner("Polling…"):
                _df = st.session_state.date_from if st.session_state.use_date_filter else None
                _dt = st.session_state.date_to   if st.session_state.use_date_filter else None
                run_poll(
                    st.session_state.api_key,
                    st.session_state.smtp_pass,
                    st.session_state.status_filter,
                    st.session_state.email_template,
                    date_from=_df,
                    date_to=_dt,
                )
            st.session_state.last_poll = time.time()
            st.success("Poll complete — check Live Logs tab.")
            st.rerun()

# ── Auto‑refresh while bot is running ─────────────────────────────────────────
if st.session_state.bot_on:
    time.sleep(10)
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()
