"""
Illegear Repair Notifier
========================
NO background threads — polling is driven by Streamlit's own rerun loop.
This guarantees exactly ONE API call per cycle, no duplicate threads, no 429 floods.
"""

import streamlit as st
import requests
import smtplib
import sqlite3
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, date, timedelta
import pandas as pd

# ── Config ─────────────────────────────────────────────────────────────────────
SUBDOMAIN      = "illegearticket"
FROM_EMAIL     = "support@illegear.com"
SMTP_HOST      = "mail.illegear.com"
SMTP_PORT      = 587
POLL_INTERVAL  = 120   # seconds between API polls (2 min — safely under 180 req/min limit)

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Illegear Repair Notifier",
    page_icon="🔧",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
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
""", unsafe_allow_html=True)

# ── Database ───────────────────────────────────────────────────────────────────
@st.cache_resource
def get_db():
    conn = sqlite3.connect("notifier.db", check_same_thread=False)
    conn.execute("""CREATE TABLE IF NOT EXISTS notified (
        ticket_id TEXT PRIMARY KEY, customer_name TEXT, customer_email TEXT,
        ticket_number TEXT, device TEXT, status TEXT, updated_at TEXT, notified_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, level TEXT, message TEXT)""")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(notified)").fetchall()]
    for col in ["device","status","updated_at"]:
        if col not in cols:
            conn.execute(f"ALTER TABLE notified ADD COLUMN {col} TEXT DEFAULT ''")
    conn.commit()
    return conn

DB = get_db()

def already_notified(ticket_id):
    return DB.execute("SELECT 1 FROM notified WHERE ticket_id=?", (str(ticket_id),)).fetchone() is not None

def mark_notified(ticket_id, name, email, number, device, status, updated_at):
    DB.execute("INSERT OR IGNORE INTO notified VALUES (?,?,?,?,?,?,?,?)",
        (str(ticket_id), name, email, str(number), device, status, updated_at,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    DB.commit()

def add_log(level, message):
    DB.execute("INSERT INTO logs (ts,level,message) VALUES (?,?,?)",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), level, message))
    DB.commit()

def get_logs(limit=100):
    return DB.execute("SELECT ts,level,message FROM logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

def get_notified_list():
    return DB.execute(
        "SELECT ticket_number,customer_name,customer_email,device,status,updated_at,notified_at "
        "FROM notified ORDER BY notified_at DESC").fetchall()

# ── API ────────────────────────────────────────────────────────────────────────
def api_get(api_key, path, params=None):
    """Single safe API call. Returns (data_dict | None, error_str | None)."""
    url     = f"https://{SUBDOMAIN}.repairshopr.com/api/v1/{path}"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        resp = requests.get(url, headers=headers, params=params or {}, timeout=15)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 60))
            return None, f"429 rate limited — retry after {wait}s"
        resp.raise_for_status()
        return resp.json(), None
    except Exception as e:
        return None, str(e)

def fetch_ready_tickets(api_key, status_filter):
    """Fetch page 1 only — enough to catch newly ready tickets each cycle."""
    data, err = api_get(api_key, "tickets", {"status": status_filter, "per_page": 100, "page": 1})
    if err:
        return [], err
    return data.get("tickets", []), None

# ── Email ──────────────────────────────────────────────────────────────────────
def send_email(smtp_pass, to_email, customer_name, ticket_number, device, template):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Your device is ready for collection! Ticket #{ticket_number}"
    msg["From"]    = f"Illegear Support <{FROM_EMAIL}>"
    msg["To"]      = to_email
    body = (template.replace("{name}", customer_name)
                    .replace("{ticket}", str(ticket_number))
                    .replace("{device}", device or "your device"))
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:auto;
                background:#0a0a0f;color:#e8e8f0;border-radius:10px;overflow:hidden;">
      <div style="background:#ff3c3c;padding:28px 32px;">
        <h1 style="margin:0;font-size:22px;color:white;">ILLEGEAR REPAIR</h1>
        <p style="margin:4px 0 0;color:#ffaaaa;font-size:13px;">Device Ready for Collection</p>
      </div>
      <div style="padding:32px;">
        <p>Hi <strong>{customer_name}</strong>,</p>
        <p>Your device is <strong style="color:#00e676;">ready for collection</strong>.</p>
        <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:6px;padding:16px;margin:20px 0;">
          <p style="margin:0 0 4px;color:#6b6b80;font-size:11px;text-transform:uppercase;">Ticket</p>
          <p style="margin:0;font-size:20px;font-weight:bold;color:#ff3c3c;">#{ticket_number}</p>
          <p style="margin:4px 0 0;color:#aaa;">{device or 'Your device'}</p>
        </div>
        <p>Please bring this ticket number when collecting your device.</p>
        <p style="color:#6b6b80;font-size:12px;">— Illegear Support<br>{FROM_EMAIL}</p>
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

# ── Core poll function (called once per cycle, NO threads) ─────────────────────
def run_poll(api_key, smtp_pass, status_filter, template):
    """
    Called directly from the Streamlit main thread.
    One function, one API call, fully synchronous — zero thread issues.
    """
    tickets, err = fetch_ready_tickets(api_key, status_filter)

    if err:
        add_log("ERROR", f"API error: {err}")
        return

    new_count = 0
    for t in tickets:
        tid    = str(t.get("id"))
        number = str(t.get("number", tid))
        name   = t.get("customer", {}).get("fullname", "Customer")
        email  = t.get("customer", {}).get("email", "")
        device = t.get("subject", "")
        status = t.get("status", "")
        upd    = (t.get("updated_at") or "")[:19]

        if not email or already_notified(tid):
            continue

        ok, err2 = send_email(smtp_pass, email, name, number, device, template)
        if ok:
            mark_notified(tid, name, email, number, device, status, upd)
            add_log("OK", f"Notified {name} ({email}) — Ticket #{number}")
            new_count += 1
        else:
            add_log("ERROR", f"Email failed for {name} ({email}): {err2}")

    msg = f"Scan done — {len(tickets)} ticket(s) matched, {new_count} new notification(s)"
    add_log("INFO", msg)

# ── Session state ──────────────────────────────────────────────────────────────
def ss(key, default):
    if key not in st.session_state:
        st.session_state[key] = default

ss("bot_on",        False)
ss("api_key",       "")
ss("smtp_pass",     "")
ss("status_filter", "Device is Ready for Collection")
ss("filter_mode",   "Latest Status (Live)")
ss("date_from",     date.today() - timedelta(days=7))
ss("date_to",       date.today())
ss("last_poll",     0.0)   # unix timestamp of last poll
ss("email_template",
    "Hi {name},\n\nYour device ({device}) is ready for collection.\n"
    "Ticket: #{ticket}\n\nThank you!\nIllegear Support Team")

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ Configuration")
    st.markdown("---")
    st.session_state.api_key   = st.text_input("RepairShopr API Key",        value=st.session_state.api_key,   type="password", placeholder="your-api-key")
    st.text_input("Subdomain",   value=SUBDOMAIN,   disabled=True)
    st.text_input("From Email",  value=FROM_EMAIL,  disabled=True)
    st.text_input("SMTP Server", value=f"{SMTP_HOST}:{SMTP_PORT}", disabled=True)
    st.session_state.smtp_pass = st.text_input("Email Password", value=st.session_state.smtp_pass, type="password", placeholder="••••••••")

    st.markdown("---")
    st.markdown("### 🎯 Trigger Status")
    st.session_state.status_filter = st.selectbox("Notify when status is", [
        "Device is Ready for Collection", "Ready for Pickup",
        "Customer Notified", "Waiting for Parts", "Resolved"], index=0)

    st.markdown("---")
    st.markdown("### 📧 Email Template")
    st.caption("Placeholders: `{name}` · `{ticket}` · `{device}`")
    st.session_state.email_template = st.text_area(
        "Template", value=st.session_state.email_template, height=140, label_visibility="collapsed")

    st.markdown("---")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("▶ Start", disabled=st.session_state.bot_on):
            if not st.session_state.api_key or not st.session_state.smtp_pass:
                st.error("Fill API key & password first.")
            else:
                st.session_state.bot_on   = True
                st.session_state.last_poll = 0.0   # poll immediately on next rerun
                add_log("INFO", f"Bot enabled | status='{st.session_state.status_filter}' | interval={POLL_INTERVAL}s")
                st.rerun()
    with c2:
        if st.button("⏹ Stop", disabled=not st.session_state.bot_on):
            st.session_state.bot_on = False
            add_log("INFO", "Bot disabled by user")
            st.rerun()

# ── Poll logic (runs in main thread, gated by timer) ──────────────────────────
now = time.time()
seconds_since_last = now - st.session_state.last_poll
seconds_until_next = max(0, POLL_INTERVAL - seconds_since_last)

if st.session_state.bot_on:
    if seconds_since_last >= POLL_INTERVAL:
        # Time to poll — do it right now in the main thread
        with st.spinner("🔍 Checking RepairShopr..."):
            run_poll(
                st.session_state.api_key,
                st.session_state.smtp_pass,
                st.session_state.status_filter,
                st.session_state.email_template,
            )
        st.session_state.last_poll = time.time()
        seconds_until_next = POLL_INTERVAL

# ── Header ─────────────────────────────────────────────────────────────────────
status_html = (
    '<span class="bot-status bot-on"><span class="pulse"></span>BOT RUNNING</span>'
    if st.session_state.bot_on else
    '<span class="bot-status bot-off"><span class="pulse"></span>BOT STOPPED</span>'
)
st.markdown(f"""
<div class="header-bar">
  <div>
    <h1 style="margin:0;font-family:Syne,sans-serif;font-size:1.8rem;font-weight:800;letter-spacing:.04em;">
      🔧 ILLEGEAR <span style="color:#ff3c3c;">REPAIR NOTIFIER</span>
    </h1>
    <p style="margin:4px 0 0;color:#6b6b80;font-size:.8rem;">
      {SUBDOMAIN}.repairshopr.com &nbsp;·&nbsp;
      <span class="filter-pill">{st.session_state.status_filter}</span>
    </p>
  </div>
  <div style="margin-left:auto">{status_html}</div>
</div>
""", unsafe_allow_html=True)

# ── Metrics ────────────────────────────────────────────────────────────────────
notified_list = get_notified_list()
logs          = get_logs(200)
errors        = [l for l in logs if l[1] == "ERROR"]

c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown(f'<div class="metric-card"><div class="label">Customers Notified</div><div class="value">{len(notified_list)}</div></div>', unsafe_allow_html=True)
with c2:
    st.markdown(f'<div class="metric-card"><div class="label">Log Entries</div><div class="value" style="color:#ff8c00">{len(logs)}</div></div>', unsafe_allow_html=True)
with c3:
    ec = "ff3c3c" if errors else "00e676"
    st.markdown(f'<div class="metric-card"><div class="label">Errors</div><div class="value" style="color:#{ec}">{len(errors)}</div></div>', unsafe_allow_html=True)
with c4:
    if st.session_state.bot_on:
        val = f'<span class="countdown">{int(seconds_until_next)}s</span>'
        lbl = "Next Poll In"
    else:
        val = f'<span style="color:#6b6b80">—</span>'
        lbl = "Next Poll In"
    st.markdown(f'<div class="metric-card"><div class="label">{lbl}</div><div class="value" style="font-size:1.4rem">{val}</div></div>', unsafe_allow_html=True)

st.markdown("---")

tab1, tab2, tab3, tab4 = st.tabs(["📋 Live Logs", "✅ Notified Customers", "🔍 Manual Check", "🧪 Diagnostics"])

# ── Tab 1: Logs ────────────────────────────────────────────────────────────────
with tab1:
    ca, cb = st.columns([3,1])
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
            color = {"OK":"#00e676","ERROR":"#ff3c3c","INFO":"#ff8c00"}.get(level,"#6b6b80")
            st.markdown(
                f'<div class="log-row" style="border-left-color:{color}">'
                f'<span style="color:#6b6b80">{ts}</span> '
                f'<span style="color:{color};font-weight:600">[{level}]</span> {msg}'
                f'</div>', unsafe_allow_html=True)

# ── Tab 2: Notified ────────────────────────────────────────────────────────────
with tab2:
    st.markdown("#### Customers Successfully Notified")
    if not notified_list:
        st.info("No customers notified yet.")
    else:
        df = pd.DataFrame(notified_list,
            columns=["Ticket #","Customer Name","Email","Device","Status","Updated At","Notified At"])
        st.dataframe(df, use_container_width=True, hide_index=True)

# ── Tab 3: Manual check ────────────────────────────────────────────────────────
with tab3:
    st.markdown("#### Manual Ticket Check")
    manual_status = st.selectbox("Filter by status", [
        "Device is Ready for Collection","Ready for Pickup",
        "Customer Notified","Waiting for Parts","Resolved","— Show All —"], key="ms")

    if st.button("🔍 Fetch Tickets Now"):
        if not st.session_state.api_key:
            st.error("Enter API key in sidebar first.")
        else:
            with st.spinner("Fetching..."):
                s = "" if manual_status == "— Show All —" else manual_status
                data, err = api_get(st.session_state.api_key, "tickets",
                                    {"status": s, "per_page": 100, "page": 1})
            if err:
                st.error(f"API error: {err}")
            elif data:
                tickets = data.get("tickets", [])
                if tickets:
                    rows = []
                    for t in tickets:
                        rows.append([
                            f"#{t.get('number','')}",
                            t.get("customer",{}).get("fullname","—"),
                            t.get("customer",{}).get("email","—"),
                            t.get("subject","—")[:50],
                            t.get("status","—"),
                            (t.get("updated_at") or "")[:10],
                            "✅" if already_notified(str(t.get("id"))) else "❌"
                        ])
                    st.dataframe(pd.DataFrame(rows,
                        columns=["#","Customer","Email","Device","Status","Updated","Notified?"]),
                        use_container_width=True, hide_index=True)
                    st.success(f"Found **{len(tickets)}** ticket(s).")
                else:
                    st.warning("No tickets found with that status.")

# ── Tab 4: Diagnostics ─────────────────────────────────────────────────────────
with tab4:
    st.markdown("#### 🧪 Connection Diagnostics")
    st.markdown("---")

    st.markdown("##### 1️⃣ Test API Key")
    if st.button("🔑 Test API Connection"):
        if not st.session_state.api_key:
            st.error("Enter API key in sidebar first.")
        else:
            with st.spinner("Connecting..."):
                data, err = api_get(st.session_state.api_key, "tickets", {"per_page": 5, "page": 1})
            if err:
                st.error(f"❌ {err}")
            else:
                tickets = data.get("tickets", [])
                total   = data.get("meta", {}).get("total_count", "?")
                st.success(f"✅ API connected! Total tickets: **{total}**")
                if tickets:
                    # Show what statuses exist
                    data2, _ = api_get(st.session_state.api_key, "tickets", {"per_page": 100, "page": 1})
                    all_t = data2.get("tickets", []) if data2 else []
                    statuses = sorted(set(t.get("status","") for t in all_t if t.get("status")))
                    st.markdown("**Statuses in your recent tickets:**")
                    for s in statuses:
                        match = " ← ✅ MATCHES trigger" if s == st.session_state.status_filter else ""
                        st.code(f"{s}{match}")

    st.markdown("---")
    st.markdown("##### 2️⃣ Test Email (SMTP)")
    test_to = st.text_input("Send test to", placeholder="youremail@example.com")
    if st.button("📧 Send Test Email"):
        if not st.session_state.smtp_pass:
            st.error("Enter email password in sidebar first.")
        elif not test_to:
            st.error("Enter a recipient email.")
        else:
            with st.spinner(f"Sending to {test_to}..."):
                ok, err = send_email(
                    st.session_state.smtp_pass, test_to,
                    "Test Customer", "0000", "Test Device",
                    "Hi {name}, this is a test email from Illegear Notifier. Ticket #{ticket}."
                )
            if ok:
                st.success(f"✅ Test email sent to **{test_to}**! Check your inbox.")
                add_log("OK", f"SMTP test sent to {test_to}")
            else:
                st.error(f"❌ Failed: {err}")
                add_log("ERROR", f"SMTP test failed: {err}")

    st.markdown("---")
    st.markdown("##### 3️⃣ Run One Poll Now")
    st.caption("Manually trigger one scan without waiting for the timer.")
    if st.button("▶ Run Poll Now"):
        if not st.session_state.api_key or not st.session_state.smtp_pass:
            st.error("Fill in API key and password first.")
        else:
            with st.spinner("Polling..."):
                run_poll(
                    st.session_state.api_key, st.session_state.smtp_pass,
                    st.session_state.status_filter, st.session_state.email_template
                )
            st.session_state.last_poll = time.time()
            st.success("Poll complete — check Live Logs tab.")
            st.rerun()

# ── Auto-rerun to count down timer (only when bot is on) ──────────────────────
if st.session_state.bot_on:
    # Rerun every 10s to update the countdown and trigger polls when due
    time.sleep(10)
    st.rerun()
