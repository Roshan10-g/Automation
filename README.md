# 🚀 Keka Attendance Telegram Bot (24/7 Automation)

An intelligent, cloud-ready Telegram bot that automates your daily Keka attendance workflow with 1-tap clock-in/out, live dashboard screenshots, dynamic 9-hour shift timers, and automated session renewal.

---

## ✨ Key Features

* ⏰ **Automated Morning Prompts**: Alerts you every weekday morning (Mon–Fri) at your designated punch-in time with interactive buttons.
* ⚡ **1-Tap Action via Telegram**: Clock in, clock out, or delay punch by 10/15/30 minutes directly from Telegram inline buttons or natural replies ("yes", "clock in").
* 🎯 **Dynamic 9-Hour Shift Target**: Extracts your exact punch-in timestamp from the live Keka portal and sets an evening countdown timer.
* 🛡️ **Auto-Keepalive Engine**: Pings Keka twice daily in the background so the sliding 72-hour session window stays continuously refreshed.
* ⚠️ **24-Hour Advance Session Alert**: Automatically notifies you on Telegram if your session token is ever within 24 hours of expiring.
* 📸 **Live Dashboard Screenshots**: Visual confirmation showing your Keka attendance card, duration progress bar, and logs.
* 🔐 **Zero-Credential Security**: Your password and email are **never** stored in files, code, or databases. You log in directly in Chrome, and only authenticated session cookies are saved.
* 🖱️ **1-Click Session Sync**: Simple `sync_login.bat` script opens Chrome for you to log in and auto-syncs the session to the cloud.
* 📱 **Drag-and-Drop Session Upload**: Drop an updated `state.json` file directly into your Telegram chat to update the bot instantly.

---

## 📋 Prerequisites

Before running the bot, ensure you have:
1. **Python 3.10+** installed ([Download Python](https://www.python.org/downloads/)).
2. A **Telegram account**.
3. Your company's **Keka Portal URL** (e.g., `https://yourcompany.keka.com`).

---

## 🛠️ Step-by-Step Setup Guide

### Step 1: Clone the Repository
```bash
git clone https://github.com/Roshan10-g/Automation.git
cd Automation
```

### Step 2: Install Dependencies
```bash
pip install -r requirements.txt
playwright install chromium
```

### Step 3: Get Telegram Credentials

1. **Get Bot Token**:
   * Open Telegram and message [@BotFather](https://t.me/BotFather).
   * Send `/newbot`, choose a name (e.g., `My Keka Bot`), and copy your **Bot Token**.
2. **Get Your Chat ID**:
   * Open Telegram and message [@userinfobot](https://t.me/userinfobot).
   * Tap **Start** and copy your numerical **Id** (e.g., `6332007287`).

---

### Step 4: Configure `.env`
Copy `.env.example` to create your `.env` file:
```bash
cp .env.example .env
```
Open `.env` in any text editor and fill in:
```ini
# Company Portal
KEKA_URL=https://yourcompany.keka.com

# Telegram Credentials
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here

# Attendance Schedule
MORNING_REMINDER_TIME=09:30
SHIFT_DURATION_MINUTES=540
TIMEZONE=Asia/Kolkata

# Geolocation (Coordinates used for attendance verification)
GEO_LATITUDE=17.4483
GEO_LONGITUDE=78.3915

# Headless browser mode (true for background execution)
HEADLESS=true
```

---

### Step 5: One-Time Login Setup

Run the 1-click sync helper:
* **On Windows**: Double-click **`sync_login.bat`** (or run `python sync_login_helper.py`).
* **On Mac/Linux**: Run `python sync_login_helper.py`.

A visible Chrome window will open.
1. Enter your **Email**, **Password**, and **Captcha** (or log in via Google/Microsoft SSO).
2. Once your Keka dashboard appears, the tool auto-detects your session, saves `state.json`, and closes.

---

## 🚀 Running the Bot

### Option A: Run 24/7 on AWS EC2 Cloud (Recommended)
Deploy to AWS Free Tier (`t3.micro`) with zero manual server configuration:
```bash
python deploy_cloud_init.py
```
This script will:
* Provision a secure EC2 `t3.micro` instance in `ap-south-1` (Mumbai).
* Install Chromium, Python, and systemd service.
* Keep the bot running 24/7 without needing your personal computer to be on.

### Option B: Run Locally on Your Computer
* **On Windows**: Double-click **`run.bat`**.
* **On Mac/Linux**: Run:
  ```bash
  python bot.py
  ```

---

## 💬 Telegram Commands Cheat Sheet

| Command | Description |
| :--- | :--- |
| `/status` | View current clock-in state, shift progress, and live dashboard screenshot |
| `/session` | Check login token health, last activity, and hours remaining |
| `/clockin` | Trigger Web Clock-In immediately |
| `/clockout` | Trigger Web Clock-Out immediately |
| `/help` | Display instructions and button options |

You can also reply naturally to the bot with messages like:
* `"yes"` or `"clock in"` $\rightarrow$ Triggers clock-in
* `"do it after 10 min"` $\rightarrow$ Sets auto clock-in timer for 10 minutes
* `"clock out"` $\rightarrow$ Triggers clock-out

---

## 🔒 Security & Privacy

* **No Credentials Stored**: Passwords and 2FA tokens are never saved or sent to any server.
* **Session Integrity Safeguard**: The client checks for authentication state before saving. If redirected to a login screen, it never corrupts or overwrites valid sessions.
* **Geofence Emulation**: Mock geolocation coordinates ensure seamless punch-in compliance even when working remotely.

---

## 📄 License
This project is open-source and available under the [MIT License](LICENSE).
