# 🔧 Illegear Repair Notifier Bot

Automatically emails customers when their device is ready for pickup — powered by RepairShopr API + Microsoft Outlook SMTP.

## Features
- ✅ Checks RepairShopr every **1 second** for tickets matching a target status
- ✅ Sends branded HTML emails from `support@illegear.com`
- ✅ Prevents duplicate emails (SQLite tracking)
- ✅ Live activity log dashboard
- ✅ Manual ticket fetch & preview
- ✅ Customizable email template

## Setup

### 1. Clone the repo
```bash
git clone https://github.com/YOUR_USERNAME/repairshopr-notifier.git
cd repairshopr-notifier
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Run locally
```bash
streamlit run app.py
```

### 4. Configure in the sidebar
| Field | Value |
|---|---|
| RepairShopr API Key | From RepairShopr → Admin → API Keys |
| Email Password | Your Microsoft 365 App Password |
| Trigger Status | e.g. `Ready for Pickup` |

## Deploy to Streamlit Community Cloud
1. Push this repo to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Connect your repo → set `app.py` as the main file
4. Add secrets in Streamlit Cloud settings (optional — or enter via sidebar)

## Security Note
> Never commit your API key or email password to GitHub.  
> Use Streamlit's **Secrets Management** for production deployments.

## Stack
- [Streamlit](https://streamlit.io) — UI
- [RepairShopr API](https://www.repairshopr.com/api/v1) — ticket data
- Microsoft Outlook SMTP (`smtp.office365.com:587`) — email sending
- SQLite — duplicate prevention & logging
