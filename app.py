import streamlit as st
import requests
import smtplib
import sqlite3
import time
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, date, timedelta
import pandas as pd

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Illegear Repair Notifier",
    page_icon="🔧",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ─────────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Mono:wght@300;400;500&display=swap');

    :root {
        --bg: #0a0a0f;
        --surface: #12121a;
        --border: #1e1e2e;
        --accent: #ff3c3c;
        --accent2: #ff8c00;
        --text: #e8e8f0;
        --muted: #6b6b80;
        --green: #00e676;
        --yellow: #ffd600;
    }
    html, body, [class*="css"] {
        font-family: 'DM Mono', monospace;
        background-color: var(--bg) !important;
        color: var(--text) !important;
    }
    h1, h2, h3 { font-family: 'Syne', sans-serif !important; }
    .stApp { background-color: var(--bg) !important; }
    section[data-testid="stSidebar"] {
        background-color: var(--surface) !important;
        border-right: 1px solid var(--border) !important;
    }
    .stTextInput input, .stTextArea textarea, .stSelectbox select,
    .stDateInput input {
        background-color: var(--surface) !important;
        border: 1px solid var(--border) !important;
        color: var(--text) !important;
        font-family: 'DM Mono', monospace !important;
        border-radius: 4px !important;
    }
    .stButton > button {
        background: var(--accent) !important;
        color: white !important;
        border: none !important;
        font-family: 'Syne', sans-serif !important;
        font-weight: 700 !important;
        letter-spacing: 0.05em !important;
        border-radius: 4px !important;
        padding: 0.5rem 1.5rem !important;
        transition: all 0.2s !important;
    }
    .stButton > button:hover { opacity: 0.85 !important; transform: translateY(-1px) !important; }
    .metric-card {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 1.2rem 1.5rem;
        margin-bottom: 0.5rem;
    }
    .metric-card .label { color: var(--muted); font-size: 0.75rem; letter-spacing: 0.1em; text-transform: uppercase; }
    .metric-card .value { font-family: 'Syne', sans-serif; font-size: 2rem; font-weight: 800; color: var(--accent); }
    .log-row {
        background: var(--surface);
        border-left: 3px solid var(--accent);
        padding: 0.6rem 1rem;
        margin-bottom: 0.4rem;
        border-radius: 0 4px 4px 0;
        font-size: 0.82rem;
    }
    .header-bar {
        display: flex;
        align-items: center;
        gap: 1rem;
        margin-bottom: 2rem;
        border-bottom: 1px solid var(--border);
        padding-bottom: 1rem;
    }
    .bot-status {
        display: inline-flex;
        align-items: center;
        gap: 0.4rem;
        padding: 4px 12px;
        border-radius: 20px;
        font-size: 0.78rem;
        font-weight: 600;
    }
    .bot-on  { background: #00e67615; color: var(--green); border: 1px solid var(--green); }
    .bot-off { background: #ff3c3c15; color: var(--accent); border: 1px solid var(--accent); }
    .pulse { width: 8px; height: 8px; border-radius: 50%; background: currentColor; animation: pulse 1.5s infinite; }
    @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.3} }
    .filter-pill {
        display: inline-block;
        padding: 3px 12px;
        border-radius: 20px;
        font-size: 0.75rem;
        font-weight: 600;
        background: #ff3c3c20;
        color: var(--accent);
        border: 1px solid var(--accent);
        margin-right: 6px;
    }
    div[data-testid="stDataFrame"] { background: var(--surface) !important; border-radius: 8px; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── Database ───────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect("notifier.db", check_same_thread=False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notified (
            ticket_id      TEXT PRIMARY KEY,
            customer_name  TEXT,
            customer_email TEXT,
            ticket_number  TEXT,
            device          TEXT,
            status          TEXT,
            updated_at      TEXT,
            notified_at    TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS logs (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ts      TEXT,
            level   TEXT,
            message TEXT
        )
        """
    )
    cols = [r[1] for r in conn.execute("PRAGMA table_info(notified)").fetchall()]
    for col in ["device", "status", "updated_at"]:
        if col not in cols:
            conn.execute(f"ALTER TABLE notified ADD COLUMN {col} TEXT DEFAULT ''")
    conn.commit()
    return conn

DB = init_db()

def already_notified(ticket_id):
    return DB.execute("SELECT 1 FROM notified WHERE ticket_id=?", (ticket_id,)).fetchone() is not None

def mark_notified(ticket_id, name, email, ticket_number, device, status, updated_at):
    DB.execute(
        "INSERT OR IGNORE INTO notified VALUES (?,?,?,?,?,?,?,?)",
        (
            ticket_id, name, email, ticket_number, device, status, updated_at,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ),
    )
    DB.commit()

def add_log(level, message):
    DB.execute(
        "INSERT INTO logs (ts, level, message) VALUES (?,?,?)",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), level, message),
    )
    DB.commit()

def get_logs(limit=100):
    return DB.execute("SELECT ts, level, message FROM logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

def get_notified_list():
    return DB.execute("SELECT ticket_number, customer_name, customer_email, device, status, updated_at, notified_at FROM notified ORDER BY notified_at DESC").fetchall()

# ── RepairShopr API ────────────────────────────────────────────────────────────
MAX_RETRIES = 5
BASE_DELAY = 5
PAGE_DELAY = 0.2

def _request_with_backoff(method, url, **kwargs):
    delay = BASE_DELAY
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = method(url, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.exceptions.HTTPError as e:
            if resp.status_code == 429:
                add_log("ERROR", f"Rate limited (attempt {attempt}); waiting {delay}s before retry")
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise RuntimeError(f"Exceeded {MAX_RETRIES} retries for {url}")

def fetch_tickets_by_status(api_key, subdomain, status_filter):
    url = f"https://{subdomain}.repairshopr.com/api/v1/tickets"
    headers = {"Authorization": f"Bearer {api_key}"}
    tickets, page = [], 1
    while True:
        try:
            resp = _request_with_backoff(requests.get, url, headers=headers, params={"status": status_filter, "per_page": 100, "page": page}, timeout=10)
            batch = resp.json().get("tickets", [])
            if not batch: break
            tickets.extend(batch)
            page += 1
            time.sleep(PAGE_DELAY)
        except Exception as e:
            add_log("ERROR", f"API fetch failed (page {page}): {e}")
            break
    return tickets

def fetch_tickets_by_date(api_key, subdomain, date_from, date_to):
    url = f"https://{subdomain}.repairshopr.com/api/v1/tickets"
    headers = {"Authorization": f"Bearer {api_key}"}
    tickets, page = [], 1
    since = date_from.strftime("%Y-%m-%dT00:00:00Z")
    while True:
        try:
            resp = _request_with_backoff(requests.get, url, headers=headers, params={"since_updated_at": since, "per_page": 100, "page": page}, timeout=10)
            batch = resp.json().get("tickets", [])
            if not batch: break
            for t in batch:
                raw = t.get("updated_at") or t.get("created_at") or ""
                try:
                    ts = datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
                    if ts <= date_to: tickets.append(t)
                except: tickets.append(t)
            page += 1
            time.sleep(PAGE_DELAY)
        except Exception as e:
            add_log("ERROR", f"API date-fetch failed (page {page}): {e}")
            break
    return tickets

def get_ticket_latest(api_key, subdomain, ticket_id):
    url = f"https://{subdomain}.repairshopr.com/api/v1/tickets/{ticket_id}"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json().get("ticket", {})
    except: return {}

# ── Email sender ───────────────────────────────────────────────────────────────
def send_email(smtp_user, smtp_pass, to_email, customer_name, ticket_number, device, template):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Your device is ready for collection! Ticket #{ticket_number}"
    msg["From"] = f"Illegear Support <{smtp_user}>"
    msg["To"] = to_email
    body = template.replace("{name}", customer_name).replace("{ticket}", ticket_number).replace("{device}", device or "your device")
    html = f"""<div style="font-family:Arial,sans-serif;max-width:520px;margin:auto;background:#0a0a0f;color:#e8e8f0;border-radius:10px;overflow:hidden;"><div style="background:#ff3c3c;padding:28px 32px;"><h1 style="margin:0;font-size:22px;color:white;letter-spacing:0.05em;">ILLEGEAR REPAIR</h1><p style="margin:4px 0 0;color:#ffaaaa;font-size:13px;">Device Ready for Collection</p></div><div style="padding:32px;"><p style="font-size:16px;">Hi <strong>{customer_name}</strong>,</p><p>Great news! Your device is <strong style="color:#00e676;">ready for collection</strong>.</p><div style="background:#12121a;border:1px solid #1e1e2e;border-radius:6px;padding:16px;margin:20px 0;"><p style="margin:0 0 6px;color:#6b6b80;font-size:12px;text-transform:uppercase;letter-spacing:0.1em;">Ticket Details</p><p style="margin:0;font-size:20px;font-weight:bold;color:#ff3c3c;">#{ticket_number}</p><p style="margin:4px 0 0;color:#aaa;">{device or 'Your device'}</p></div><p>Please visit us during operating hours to collect your device. Bring this ticket number as reference.</p><p style="margin-top:24px;color:#6b6b80;font-size:12px;">— Illegear Support Team<br>support@illegear.com</p></div></div>"""
    msg.attach(MIMEText(body, "plain"))
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP("mail.illegear.com", 587) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, to_email, msg.as_string())
        return True
    except Exception as e:
        add_log("ERROR", f"Email to {to_email} failed: {e}")
        return False

# ── Bot loop ───────────────────────────────────────────────────────────────────
_bot_running = False
_bot_thread = None

def bot_loop(api_key, subdomain, smtp_user, smtp_pass, mode, status_filter, date_from, date_to, template):
    global _bot_running
    add_log("INFO", f"Bot started · mode={mode} · trigger='{status_filter}'")
    while _bot_running:
        if mode == "status":
            tickets = fetch_tickets_by_status(api_key, subdomain, status_filter)
        else:
            raw = fetch_tickets_by_date(api_key, subdomain, date_from, date_to)
            tickets = []
            for t in raw:
                latest = get_ticket_latest(api_key, subdomain, t["id"])
                if latest.get("status") == status_filter:
                    t["status"] = latest.get("status", "")
                    t["updated_at"] = latest.get("updated_at", t.get("updated_at", ""))
                    tickets.append(t)
        for t in tickets:
            tid = str(t.get("id"))
            number = str(t.get("number", tid))
            name = t.get("customer", {}).get("fullname", "Customer")
            email = t.get("customer", {}).get("email", "")
            device = t.get("subject", "")
            status = t.get("status", "")
            updated_at = (t.get("updated_at") or "")[:19]
            if not email or already_notified(tid): continue
            if send_email(smtp_user, smtp_pass, email, name, number, device, template):
                mark_notified(tid, name, email, number, device, status, updated_at)
                add_log("OK", f"Notified {name} ({email}) — Ticket #{number}")
            else:
                add_log("ERROR", f"Failed to notify {name} — Ticket #{number}")
        time.sleep(1)
    add_log("INFO", "Bot stopped")

def start_bot(*args):
    global _bot_running, _bot_thread
    _bot_running = True
    _bot_thread = threading.Thread(target=bot_loop, args=args, daemon=True)
    _bot_thread.start()

def stop_bot():
    global _bot_running
    _bot_running = False

# ── Session State & UI ─────────────────────────────────────────────────────────
if "bot_on" not in st.session_state: st.session_state.bot_on = False
if "api_key" not in st.session_state: st.session_state.api_key = ""
if "smtp_pass" not in st.session_state: st.session_state.smtp_pass = ""
if "status_filter" not in st.session_state: st.session_state.status_filter = "Device is Ready for Collection"
if "filter_mode" not in st.session_state: st.session_state.filter_mode = "Latest Status (Live)"
if "date_from" not in st.session_state: st.session_state.date_from = date.today() - timedelta(days=7)
if "date_to" not in st.session_state: st.session_state.date_to = date.today()
if "email_template" not in st.session_state: st.session_state.email_template = "Hi {name},\n\nYour device ({device}) is ready for collection.\nTicket: #{ticket}\n\nRegards,\nIllegear Support"

with st.sidebar:
    st.markdown("### ⚙️ Configuration")
    st.session_state.api_key = st.text_input("RepairShopr API Key", value=st.session_state.api_key, type="password")
    st.text_input("Subdomain", value="illegearticket", disabled=True)
    st.session_state.smtp_pass = st.text_input("Email Password", value=st.session_state.smtp_pass, type="password")
    st.markdown("---")
    st.session_state.status_filter = st.selectbox("Trigger Status", ["Device is Ready for Collection", "Ready for Pickup", "Customer Notified", "Resolved"])
    st.session_state.filter_mode = st.radio("Mode", ["Latest Status (Live)", "Date Range"])
    if st.session_state.filter_mode == "Date Range":
        st.session_state.date_from = st.date_input("From", st.session_state.date_from)
        st.session_state.date_to = st.date_input("To", st.session_state.date_to)
    st.markdown("---")
    st.session_state.email_template = st.text_area("Template", st.session_state.email_template, height=150)
    c1, c2 = st.columns(2)
    if c1.button("▶ Start", disabled=st.session_state.bot_on):
        if st.session_state.api_key and st.session_state.smtp_pass:
            mode = "status" if st.session_state.filter_mode == "Latest Status (Live)" else "date"
            start_bot(st.session_state.api_key, "illegearticket", "support@illegear.com", st.session_state.smtp_pass, mode, st.session_state.status_filter, st.session_state.date_from, st.session_state.date_to, st.session_state.email_template)
            st.session_state.bot_on = True
            st.rerun()
    if c2.button("⏹ Stop", disabled=not st.session_state.bot_on):
        stop_bot()
        st.session_state.bot_on = False
        st.rerun()

# ── Main Content ──────────────────────────────────────────────────────────────
status_html = '<span class="bot-status bot-on"><span class="pulse"></span>BOT RUNNING</span>' if st.session_state.bot_on else '<span class="bot-status bot-off"><span class="pulse"></span>BOT STOPPED</span>'
st.markdown(f'<div class="header-bar"><div><h1>🔧 ILLEGEAR <span style="color:#ff3c3c;">REPAIR NOTIFIER</span></h1></div><div style="margin-left:auto">{status_html}</div></div>', unsafe_allow_html=True)

notified_list = get_notified_list()
logs = get_logs(100)
c1, c2, c3, c4 = st.columns(4)
c1.markdown(f'<div class="metric-card"><div class="label">Notified</div><div class="value">{len(notified_list)}</div></div>', unsafe_allow_html=True)
c2.markdown(f'<div class="metric-card"><div class="label">Logs</div><div class="value" style="color:#ff8c00">{len(logs)}</div></div>', unsafe_allow_html=True)
c3.markdown(f'<div class="metric-card"><div class="label">Errors</div><div class="value" style="color:{"#ff3c3c" if any(l[1]=="ERROR" for l in logs) else "#00e676"}">{len([l for l in logs if l[1]=="ERROR"])}</div></div>', unsafe_allow_html=True)
c4.markdown('<div class="metric-card"><div class="label">Interval</div><div class="value" style="color:#00e676;font-size:1.4rem">1 sec</div></div>', unsafe_allow_html=True)

tab1, tab2, tab3, tab4 = st.tabs(["📋 Live Logs", "✅ Notified", "🔍 Manual Check", "🧪 Diagnostics"])

with tab1:
    for ts, level, msg in logs:
        color = {"OK": "#00e676", "ERROR": "#ff3c3c", "INFO": "#ff8c00"}.get(level, "#6b6b80")
        st.markdown(f'<div class="log-row" style="border-left-color:{color}"><span style="color:#6b6b80">{ts}</span> <span style="color:{color}">[{level}]</span> {msg}</div>', unsafe_allow_html=True)

with tab2:
    if notified_list:
        st.dataframe(pd.DataFrame(notified_list, columns=["Ticket #", "Name", "Email", "Device", "Status", "Updated", "Notified"]), use_container_width=True, hide_index=True)
    else: st.info("No notifications sent yet.")

with tab3:
    if st.button("🔍 Fetch Current Tickets"):
        tickets = fetch_tickets_by_status(st.session_state.api_key, "illegearticket", st.session_state.status_filter)
        st.write(f"Found {len(tickets)} tickets.")
        if tickets: st.json(tickets[:3])

with tab4:
    st.markdown("##### 1️⃣ Test API Connection")
    if st.button("🔑 Run API Test"):
        try:
            resp = requests.get("https://illegearticket.repairshopr.com/api/v1/tickets", headers={"Authorization": f"Bearer {st.session_state.api_key}"}, params={"per_page": 1})
            if resp.status_code == 200: st.success("API Connection Successful!")
            else: st.error(f"API Failed: {resp.status_code}")
        except Exception as e: st.error(f"Error: {e}")

    st.markdown("---")
    st.markdown("##### 2️⃣ Test SMTP Email")
    test_email = st.text_input("Receiver Email", value="support@illegear.com")
    if st.button("📧 Send Test Email"):
        if send_email("support@illegear.com", st.session_state.smtp_pass, test_email, "Test User", "0000", "Diagnostic Device", "Test Successful."):
            st.success("Test email sent!")
        else: st.error("Email failed. Check logs.")
