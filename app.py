mport streamlit as st

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

              device         TEXT,

              status         TEXT,

              updated_at     TEXT,

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

      # migrate old schema

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

  # ---- Rate‑limit helper -------------------------------------------------------

  MAX_RETRIES = 5          # how many times we retry a 429

  BASE_DELAY = 5           # seconds before the first retry (exponential)

  PAGE_DELAY = 0.2         # short pause after each successful page fetch





  def _request_with_backoff(method, url, **kwargs):

      """

      Perform a ``requests`` call with exponential back‑off on HTTP 429.

      Logs the wait so you can see it in the activity log.

      """

      delay = BASE_DELAY

      for attempt in range(1, MAX_RETRIES + 1):

          try:

              resp = method(url, **kwargs)

              resp.raise_for_status()

              return resp

          except requests.exceptions.HTTPError as e:

              if resp.status_code == 429:

                  add_log(

                      "ERROR",

                      f"Rate limited (attempt {attempt}); waiting {delay}s before retry",

                  )

                  time.sleep(delay)

                  delay *= 2  # exponential back‑off

                  continue

              # any other HTTP error – let it bubble up

              raise

      raise RuntimeError(f"Exceeded {MAX_RETRIES} retries for {url}")





  def fetch_tickets_by_status(api_key, subdomain, status_filter):

      """Fetch all tickets currently matching a specific status."""

      url = f"https://{subdomain}.repairshopr.com/api/v1/tickets"

      headers = {"Authorization": f"Bearer {api_key}"}

      tickets, page = [], 1



      while True:

          try:

              resp = _request_with_backoff(

                  requests.get,

                  url,

                  headers=headers,

                  params={"status": status_filter, "per_page": 100, "page": page},

                  timeout=10,

              )

              batch = resp.json().get("tickets", [])

              if not batch:

                  break

              tickets.extend(batch)

              page += 1

              time.sleep(PAGE_DELAY)  # avoid hammering the endpoint

          except Exception as e:

              add_log("ERROR", f"API fetch failed (page {page}): {e}")

              break

      return tickets





  def fetch_tickets_by_date(api_key, subdomain, date_from, date_to):

      """Fetch all tickets updated within the given date range."""

      url = f"https://{subdomain}.repairshopr.com/api/v1/tickets"

      headers = {"Authorization": f"Bearer {api_key}"}

      tickets, page = [], 1

      since = date_from.strftime("%Y-%m-%dT00:00:00Z")



      while True:

          try:

              resp = _request_with_backoff(

                  requests.get,

                  url,

                  headers=headers,

                  params={

                      "since_updated_at": since,

                      "per_page": 100,

                      "page": page,

                  },

                  timeout=10,

              )

              batch = resp.json().get("tickets", [])

              if not batch:

                  break

              for t in batch:

                  raw = t.get("updated_at") or t.get("created_at") or ""

                  try:

                      ts = datetime.fromisoformat(raw.replace("Z", "+00:00")).date()

                      if ts <= date_to:

                          tickets.append(t)

                  except Exception:

                      tickets.append(t)

              page += 1

              time.sleep(PAGE_DELAY)

          except Exception as e:

              add_log("ERROR", f"API date-fetch failed (page {page}): {e}")

              break

      return tickets





  def get_ticket_latest(api_key, subdomain, ticket_id):

      """Re‑fetch a single ticket to get its absolute latest status."""

      url = f"https://{subdomain}.repairshopr.com/api/v1/tickets/{ticket_id}"

      headers = {"Authorization": f"Bearer {api_key}"}

      try:

          resp = requests.get(url, headers=headers, timeout=10)

          resp.raise_for_status()

          return resp.json().get("ticket", {})

      except Exception:

          return {}





  # ── Email sender ───────────────────────────────────────────────────────────────

  def send_email(smtp_user, smtp_pass, to_email, customer_name, ticket_number, device, template):

      msg = MIMEMultipart("alternative")

      msg["Subject"] = f"Your device is ready for collection! Ticket #{ticket_number}"

      msg["From"] = f"Illegear Support <{smtp_user}>"

      msg["To"] = to_email



      body = (

          template.replace("{name}", customer_name)

          .replace("{ticket}", ticket_number)

          .replace("{device}", device or "your device")

      )



      html = f"""

      <div style="font-family:Arial,sans-serif;max-width:520px;margin:auto;

                  background:#0a0a0f;color:#e8e8f0;border-radius:10px;overflow:hidden;">

        <div style="background:#ff3c3c;padding:28px 32px;">

          <h1 style="margin:0;font-size:22px;color:white;letter-spacing:0.05em;">ILLEGEAR REPAIR</h1>

          <p style="margin:4px 0 0;color:#ffaaaa;font-size:13px;">Device Ready for Collection</p>

        </div>

        <div style="padding:32px;">

          <p style="font-size:16px;">Hi <strong>{customer_name}</strong>,</p>

          <p>Great news! Your device is <strong style="color:#00e676;">ready for collection</strong>.</p>

          <div style="background:#12121a;border:1px solid #1e1e2e;border-radius:6px;padding:16px;margin:20px 0;">

            <p style="margin:0 0 6px;color:#6b6b80;font-size:12px;text-transform:uppercase;letter-spacing:0.1em;">Ticket Details</p>

            <p style="margin:0;font-size:20px;font-weight:bold;color:#ff3c3c;">#{ticket_number}</p>

            <p style="margin:4px 0 0;color:#aaa;">{device or 'Your device'}</p>

          </div>

          <p>Please visit us during operating hours to collect your device. Bring this ticket number as reference.</p>

          <p style="margin-top:24px;color:#6b6b80;font-size:12px;">— Illegear Support Team<br>support@illegear.com</p>

        </div>

      </div>"""



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





  def bot_loop(

      api_key,

      subdomain,

      smtp_user,

      smtp_pass,

      mode,

      status_filter,

      date_from,

      date_to,

      template,

  ):

      global _bot_running

      add_log(

          "INFO",

          f"Bot started · mode={mode} · trigger='{status_filter}' · checking every 1 sec",

      )



      while _bot_running:

          # ------------------------------------------------------------------ fetch tickets

          if mode == "status":

              tickets = fetch_tickets_by_status(api_key, subdomain, status_filter)

          else:

              raw = fetch_tickets_by_date(api_key, subdomain, date_from, date_to)

              tickets = []

              for t in raw:

                  latest = get_ticket_latest(api_key, subdomain, t["id"])

                  if latest.get("status") == status_filter:

                      t["status"] = latest.get("status", "")

                      t["updated_at"] = latest.get(

                          "updated_at", t.get("updated_at", "")

                      )

                      tickets.append(t)



          # --------------------------------------------------------------- process tickets

          for t in tickets:

              tid = str(t.get("id"))

              number = str(t.get("number", tid))

              name = t.get("customer", {}).get("fullname", "Customer")

              email = t.get("customer", {}).get("email", "")

              device = t.get("subject", "")

              status = t.get("status", "")

              updated_at = (t.get("updated_at") or "")[:19]



              if not email or already_notified(tid):

                  continue



              ok = send_email(smtp_user, smtp_pass, email, name, number, device, template)

              if ok:

                  mark_notified(tid, name, email, number, device, status, updated_at)

                  add_log(

                      "OK",

                      f"Notified {name} ({email}) — Ticket #{number} [{status}]",

                  )

              else:

                  add_log(

                      "ERROR",

                      f"Failed to notify {name} ({email}) — Ticket #{number}",

                  )



          time.sleep(1)



      add_log("INFO", "Bot stopped")





  def start_bot(

      api_key,

      subdomain,

      smtp_user,

      smtp_pass,

      mode,

      status_filter,

      date_from,

      date_to,

      template,

  ):

      global _bot_running, _bot_thread

      _bot_running = True

      _bot_thread = threading.Thread(

          target=bot_loop,

          args=(

              api_key,

              subdomain,

              smtp_user,

              smtp_pass,

              mode,

              status_filter,

              date_from,

              date_to,

              template,

          ),

          daemon=True,

      )

      _bot_thread.start()





  def stop_bot():

      global _bot_running

      _bot_running = False





  # ── Session state ──────────────────────────────────────────────────────────────

  defaults = {

      "bot_on": False,

      "api_key": "",

      "smtp_pass": "",

      "status_filter": "Device is Ready for Collection",

      "filter_mode": "Latest Status (Live)",

      "date_from": date.today() - timedelta(days=7),

      "date_to": date.today(),

      "email_template": (

          "Hi {name},\n\n"

          "Your device ({device}) is ready for collection at our store.\n"

          "Ticket number: #{ticket}\n\n"

          "Thank you for choosing Illegear!\n\n"

          "Best regards,\nIllegear Support Team"

      ),

  }

  for k, v in defaults.items():

      if k not in st.session_state:

          st.session_state[k] = v





  # ── Sidebar ────────────────────────────────────────────────────────────────────

  with st.sidebar:

      st.markdown("### ⚙️ Configuration")

      st.markdown("---")



      st.session_state.api_key = st.text_input(

          "RepairShopr API Key",

          value=st.session_state.api_key,

          type="password",

          placeholder="your-api-key-here",

      )

      st.text_input("Subdomain", value="illegearticket", disabled=True)

      st.text_input("From Email", value="support@illegear.com", disabled=True)

      st.text_input("SMTP Server", value="mail.illegear.com:587", disabled=True)



      st.session_state.smtp_pass = st.text_input(

          "Email Password / App Password",

          value=st.session_state.smtp_pass,

          type="password",

          placeholder="••••••••",

      )



      st.markdown("---")

      st.markdown("### 🎯 Trigger Status")

      st.session_state.status_filter = st.selectbox(

          "Notify when ticket status is",

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

      st.markdown("### 📅 Detection Mode")

      st.session_state.filter_mode = st.radio(

          "How to detect tickets",

          ["Latest Status (Live)", "Date Range"],

          index=0,

          help=(

              "**Latest Status (Live):** Watches for tickets whose current status "

              "matches the trigger — checks every second in real time.\n\n"

              "**Date Range:** Scans tickets updated within a date window and "

              "verifies their latest status before sending."

          ),

      )



      if st.session_state.filter_mode == "Date Range":

          st.session_state.date_from = st.date_input(

              "From date", value=st.session_state.date_from

          )

          st.session_state.date_to = st.date_input(

              "To date",

              value=st.session_state.date_to,

              min_value=st.session_state.date_from,

          )

          st.caption(

              f"Tickets updated: **{st.session_state.date_from}** → **{st.session_state.date_to}**"

          )



      st.markdown("---")

      st.markdown("### 📧 Email Template")

      st.caption("Placeholders: `{name}` · `{ticket}` · `{device}`")

      st.session_state.email_template = st.text_area(

          "Template",

          value=st.session_state.email_template,

          height=160,

          label_visibility="collapsed",

      )



      st.markdown("---")

      c1, c2 = st.columns(2)

      with c1:

          if st.button("▶ Start", disabled=st.session_state.bot_on):

              if st.session_state.api_key and st.session_state.smtp_pass:

                  mode = (

                      "status"

                      if st.session_state.filter_mode == "Latest Status (Live)"

                      else "date"

                  )

                  start_bot(

                      st.session_state.api_key,

                      "illegearticket",

                      "support@illegear.com",

                      st.session_state.smtp_pass,

                      mode,

                      st.session_state.status_filter,

                      st.session_state.date_from,

                      st.session_state.date_to,

                      st.session_state.email_template,

                  )

                  st.session_state.bot_on = True

                  st.rerun()

              else:

                  st.error("Fill in API key & password first.")

      with c2:

          if st.button("⏹ Stop", disabled=not st.session_state.bot_on):

              stop_bot()

              st.session_state.bot_on = False

              st.rerun()





  # ── Header ─────────────────────────────────────────────────────────────────────

  status_html = (

      '<span class="bot-status bot-on"><span class="pulse"></span>BOT RUNNING</span>'

      if st.session_state.bot_on

      else '<span class="bot-status bot-off"><span class="pulse"></span>BOT STOPPED</span>'

  )

  mode_label = (

      f"Date Range: {st.session_state.date_from} → {st.session_state.date_to}"

      if st.session_state.filter_mode == "Date Range"

      else "Latest Status (Live)"

  )

  st.markdown(

      f"""

  <div class="header-bar">

    <div>

      <h1 style="margin:0;font-family:Syne,sans-serif;font-size:1.8rem;font-weight:800;letter-spacing:0.04em;">

        🔧 ILLEGEAR <span style="color:#ff3c3c;">REPAIR NOTIFIER</span>

      </h1>

      <p style="margin:4px 0 0;color:#6b6b80;font-size:0.8rem;">

        illegearticket.repairshopr.com &nbsp;·&nbsp;

        <span class="filter-pill">{st.session_state.status_filter}</span>

        <span class="filter-pill" style="background:#ff8c0020;color:#ff8c00;border-color:#ff8c00;">

          {mode_label}

        </span>

      </p>

    </div>

    <div style="margin-left:auto">{status_html}</div>

  </div>

  """,

      unsafe_allow_html=True,

  )



  # ── Metrics ────────────────────────────────────────────────────────────────────

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

      st.markdown(

          '<div class="metric-card"><div class="label">Check Interval</div>'

          '<div class="value" style="color:#00e676;font-size:1.4rem">1 sec</div></div>',

          unsafe_allow_html=True,

      )



  st.markdown("---")



  tab1, tab2, tab3, tab4 = st.tabs(

      ["📋 Live Logs", "✅ Notified Customers", "🔍 Manual Check", "🧪 Diagnostics"]

  )



  # ── Tab 1 ──────────────────────────────────────────────────────────────────────

  with tab1:

      ca, cb = st.columns([3, 1])

      with ca:

          st.markdown("#### Activity Log")

      with cb:

          if st.button("🔄 Refresh"):

              st.rerun()

      fresh = get_logs(100)

      if not fresh:

          st.info("No activity yet. Start the bot to begin.")

      else:

          for ts, level, msg in fresh:

              color = {

                  "OK": "#00e676",

                  "ERROR": "#ff3c3c",

                  "INFO": "#ff8c00",

              }.get(level, "#6b6b80")

              st.markdown(

                  f'<div class="log-row" style="border-left-color:{color}">'

                  f'<span style="color:#6b6b80">{ts}</span> '

                  f'<span style="color:{color};font-weight:600">[{level}]</span> {msg}'

                  f"</div>",

                  unsafe_allow_html=True,

              )



  # ── Tab 2 ──────────────────────────────────────────────────────────────────────

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

          st.dataframe(df, use_container_width=True, hide_index=True)



  # ── Tab 3 ──────────────────────────────────────────────────────────────────────

  with tab3:

      st.markdown("#### Manual Ticket Check")

      check_mode = st.radio(

          "Fetch mode",

          ["Latest Status (Live)", "Date Range"],

          horizontal=True,

          key="manual_check_mode",

      )

      if check_mode == "Date Range":

          mc1, mc2 = st.columns(2)

          with mc1:

              manual_from = st.date_input(

                  "From", value=date.today() - timedelta(days=7), key="mf"

              )

          with mc2:

              manual_to = st.date_input("To", value=date.today(), key="mt")

      else:

          manual_from = manual_to = None



      manual_status = st.selectbox(

          "Status to filter",

          [

              "Device is Ready for Collection",

              "Ready for Pickup",

              "Customer Notified",

              "Waiting for Parts",

              "Resolved",

              "— Show All —",

          ],

          key="manual_status",

      )



      if st.button("🔍 Fetch Tickets Now"):

          if not st.session_state.api_key:

              st.error("Enter your API key in the sidebar first.")

          else:

              with st.spinner("Fetching from RepairShopr..."):

                  if check_mode == "Latest Status (Live)":

                      s = "" if manual_status == "— Show All —" else manual_status

                      tickets = fetch_tickets_by_status(

                          st.session_state.api_key, "illegearticket", s

                      )

                  else:

                      raw = fetch_tickets_by_date(

                          st.session_state.api_key,

                          "illegearticket",

                          manual_from,

                          manual_to,

                      )

                      tickets = (

                          raw

                          if manual_status == "— Show All —"

                          else [t for t in raw if t.get("status") == manual_status]

                      )



              if tickets:

                  rows = []

                  for t in tickets:

                      tid = str(t.get("id"))

                      number = t.get("number", tid)

                      name = t.get("customer", {}).get("fullname", "—")

                      email = t.get("customer", {}).get("email", "—")

                      status = t.get("status", "—")

                      device = t.get("subject", "—")

                      upd = (t.get("updated_at") or "")[:10]

                      sent = "✅ Yes" if already_notified(tid) else "❌ No"

                      rows.append(

                          [

                              f"#{number}",

                              name,

                              email,

                              device,

                              status,

                              upd,

                              sent,

                          ]

                      )

                  df2 = pd.DataFrame(

                      rows,

                      columns=[

                          "Ticket #",

                          "Customer",

                          "Email",

                          "Device",

                          "Status",

                          "Updated",

                          "Notified?",

                      ],

                  )

                  st.dataframe(df2, use_container_width=True, hide_index=True)

                  st.success(f"Found **{len(tickets)}** ticket(s).")

              else:

                  st.warning("No tickets found with those filters.")



  # ── Tab 4: Diagnostics ────────────────────────────────────────────────────────

  with tab4:

      st.markdown("#### 🧪 Connection Diagnostics")

      st.caption("Use these tests to pinpoint why emails are not being sent.")



      st.markdown("---")

      # ── Test 1: API connection ──────────────────────────────────────────────

      st.markdown("##### 1️⃣ Test RepairShopr API Key")

      if st.button("🔑 Test API Connection"):

          if not st.session_state.api_key:

              st.error("Enter your API key in the sidebar first.")

          else:

              with st.spinner("Connecting to RepairShopr..."):

                  try:

                      url = "https://illegearticket.repairshopr.com/api/v1/tickets"

                      resp = requests.get(

                          url,

                          headers={

                              "Authorization": f"Bearer {st.session_state.api_key}"

                          },

                          params={"per_page": 5},

                          timeout=10,

                      )

                      if resp.status_code == 200:

                          data = resp.json()

                          total = data.get("meta", {}).get("total_count", "?")

                          tickets = data.get("tickets", [])

                          st.success(

                              f"✅ API connected! Total tickets in system: **{total}**"

                          )

                          if tickets:

                              st.markdown("**Sample tickets returned:**")

                              rows = [

                                  [

                                      t.get("number", "—"),

                                      t.get("subject", "—")[:60],

                                      t.get("status", "—"),

                                      t.get("customer", {}).get("email", "—"),

                                  ]

                                  for t in tickets

                              ]

                              st.dataframe(

                                  pd.DataFrame(

                                      rows,

                                      columns=["#", "Subject", "Status", "Email"],

                                  ),

                                  use_container_width=True,

                                  hide_index=True,

                              )

                          else:

                              st.warning(

                                  "API connected but returned 0 tickets. Check if tickets exist."

                              )

                      elif resp.status_code == 401:

                          st.error(

                              "❌ Invalid API key — 401 Unauthorized. Please check your key."

                          )

                      else:

                          st.error(

                              f"❌ API returned status {resp.status_code}: {resp.text[:200]}"

                          )

                  except Exception as e:

                      st.error(f"❌ Could not reach RepairShopr: {e}")



      st.markdown("---")

      # ── Test 2: Status filter ───────────────────────────────────────────────

      st.markdo
