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
            ticket_id,
            name,
            email,
            ticket_number,
            device,
            status,
            updated_at,
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
    return DB.execute(
        "SELECT ts, level, message FROM logs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()

def get_notified_list():
    return DB.execute(
        """
        SELECT ticket_number, customer_name, customer_email, device, status,
                 updated_at, notified_at
        FROM notified
        ORDER BY notified_at DESC
        """
    ).fetchall()

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
                add_log("ERROR", f"Rate limited (attempt {attempt}); waiting {delay}s")
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise RuntimeError(f"Exceeded {MAX_RETRIES} retries")

def fetch_tickets_by_status(api_key, subdomain, status_filter):
    url = f"https://{subdomain}.repairshopr.com/api/v1/tickets"
    headers = {"Authorization": f"Bearer {api_key}"}
    tickets, page = [], 1
    while True:
        try:
            resp = _request_with_backoff(
                requests.get, url, headers=headers,
                params={"status": status_filter, "per_page": 100, "page": page},
                timeout=10
            )
            batch = resp.json().get("tickets", [])
            if not batch: break
            tickets.extend(batch)
            page += 1
            time.sleep(PAGE_DELAY)
        except Exception as e:
            add_log("ERROR", f"API fetch failed: {e}")
            break
    return tickets

def fetch_tickets_by_date(api_key, subdomain, date_from, date_to):
    url = f"https://{subdomain}.repairshopr.com/api/v1/tickets"
    headers = {"Authorization": f"Bearer {api_key}"}
    tickets, page = [], 1
    since = date_from.strftime("%Y-%m-%dT00:00:00Z")
    while True:
        try:
            resp = _request_with_backoff(
                requests.get, url, headers=headers,
                params={"since_updated_at": since, "per_page": 100, "page": page},
                timeout=10
            )
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
            add_log("ERROR", f"API date-fetch failed: {e}")
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

def send_email(smtp_user, smtp_pass, to_email, customer_name, ticket_number, device, template):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Your device is ready for collection! Ticket #{ticket_number}"
    msg["From"] = f"Illegear Support <{smtp_user}>"
    msg["To"] = to_email
    body = template.replace("{name}", customer_name).replace("{ticket}", ticket_number).replace("{device}", device or "your device")
    html = f"""<div style="background:#0a0a0f;color:#e8e8f0;padding:20px;"><h2>Hi {customer_name}</h2><p>Ticket <b>#{ticket_number}</b> is ready.</p></div>"""
    msg.attach(MIMEText(body, "plain"))
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP("mail.illegear.com", 587) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, to_email, msg.as_string())
        return True
    except Exception as e:
        add_log("ERROR", f"Email failed: {e}")
        return False

# ── Bot Logic ──────────────────────────────────────────────────────────────────
_bot_running = False

def bot_loop(api_key, subdomain, smtp_user, smtp_pass, mode, status_filter, d_from, d_to, template):
    global _bot_running
    add_log("INFO", "Bot sequence initiated")
    while _bot_running:
        tickets = fetch_tickets_by_status(api_key, subdomain, status_filter) if mode == "status" else fetch_tickets_by_date(api_key, subdomain, d_from, d_to)
        for t in tickets:
            tid = str(t.get("id"))
            if already_notified(tid): continue
            name = t.get("customer", {}).get("fullname", "Customer")
            email = t.get("customer", {}).get("email", "")
            number = t.get("number", tid)
            device = t.get("subject", "")
            if email and send_email(smtp_user, smtp_pass, email, name, str(number), device, template):
                mark_notified(tid, name, email, str(number), device, status_filter, str(t.get("updated_at", "")))
                add_log("OK", f"Sent to {name} (#{number})")
        time.sleep(30) # Increased to 30s to prevent rate limits

def start_bot(*args):
    global _bot_running
    _bot_running = True
    threading.Thread(target=bot_loop, args=args, daemon=True).start()

def stop_bot():
    global _bot_running
    _bot_running = False

# ── Sidebar & UI ────────────────────────────────────────────────────────────────
if "bot_on" not in st.session_state: st.session_state.bot_on = False
if "api_key" not in st.session_state: st.session_state.api_key = ""
if "smtp_pass" not in st.session_state: st.session_state.smtp_pass = ""
if "status_filter" not in st.session_state: st.session_state.status_filter = "Device is Ready for Collection"
if "filter_mode" not in st.session_state: st.session_state.filter_mode = "Latest Status (Live)"
if "date_from" not in st.session_state: st.session_state.date_from = date.today() - timedelta(days=7)
if "date_to" not in st.session_state: st.session_state.date_to = date.today()
if "email_template" not in st.session_state: st.session_state.email_template = "Hi {name}, your device {device} is ready. Ticket: #{ticket}"

with st.sidebar:
    st.markdown("### ⚙️ Configuration")
    st.session_state.api_key = st.text_input("API Key", value=st.session_state.api_key, type="password")
    st.session_state.smtp_pass = st.text_input("SMTP Password", value=st.session_state.smtp_pass, type="password")
    st.session_state.status_filter = st.selectbox("Status", ["Device is Ready for Collection", "Ready for Pickup", "Resolved"])
    st.session_state.filter_mode = st.radio("Mode", ["Latest Status (Live)", "Date Range"])
    
    if st.button("▶ Start Bot", disabled=st.session_state.bot_on):
        mode = "status" if st.session_state.filter_mode == "Latest Status (Live)" else "date"
        start_bot(st.session_state.api_key, "illegearticket", "support@illegear.com", st.session_state.smtp_pass, mode, st.session_state.status_filter, st.session_state.date_from, st.session_state.date_to, st.session_state.email_template)
        st.session_state.bot_on = True
        st.rerun()
    if st.button("⏹ Stop Bot", disabled=not st.session_state.bot_on):
        stop_bot()
        st.session_state.bot_on = False
        st.rerun()

# ── Header & Tabs ─────────────────────────────────────────────────────────────
st.markdown(f"<h1>🔧 ILLEGEAR <span style='color:red'>REPAIR NOTIFIER</span></h1>", unsafe_allow_html=True)
tab1, tab2, tab3, tab4 = st.tabs(["📋 Logs", "✅ Notified", "🔍 Manual", "🧪 Diagnostics"])

with tab1:
    for ts, level, msg in get_logs(50):
        st.markdown(f"`{ts}` **[{level}]** {msg}")

with tab4:
    st.markdown("##### 🧪 Connection Diagnostics")
    if st.button("🔑 Test API"):
        resp = requests.get("https://illegearticket.repairshopr.com/api/v1/tickets", headers={"Authorization": f"Bearer {st.session_state.api_key}"}, params={"per_page":1})
        st.write("Success!" if resp.status_code == 200 else f"Failed: {resp.status_code}")

if st.session_state.bot_on:
    time.sleep(5)
    st.rerun()
