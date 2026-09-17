import sys
import time
import json
import threading
from pathlib import Path
from playwright.sync_api import sync_playwright
import paramiko

import config

BASE_DIR = Path(__file__).resolve().parent
KEY_FILE = BASE_DIR / "keka-attendance-key.pem"
EC2_HOST = "15.207.254.33"
EC2_USER = "ubuntu"
REMOTE_DIR = "/home/ubuntu/keka-attendance-bot"

# Global flag to allow pressing Enter to trigger save
user_pressed_enter = False

def listen_for_enter():
    global user_pressed_enter
    try:
        input()
        user_pressed_enter = True
    except Exception:
        pass

def is_on_dashboard(page) -> bool:
    """Checks if the user has reached the authenticated Keka dashboard."""
    try:
        url = page.url.lower()
        # Still on login / auth pages
        if any(w in url for w in ["/account/kekalogin", "/login", "/signin", "/oauth2", "login.microsoftonline.com", "accounts.google.com"]):
            return False

        # If on home, attendance, or dashboard URL
        if any(w in url for w in ["#/home", "#/me", "dashboard", "/attendance"]):
            return True

        # Check for presence of attendance buttons or employee profile
        if page.locator("button:has-text('Web Clock-In'), button:has-text('Web Clock-Out')").first.is_visible(timeout=500):
            return True

        if page.locator(".profile-name, .user-name, a:has-text('Home')").first.is_visible(timeout=500):
            return True

    except Exception:
        pass
    return False

def upload_to_ec2(local_state_file: Path):
    """Uploads state.json to AWS EC2 via SFTP and restarts keka-bot.service."""
    print(f"\n[1/3] Connecting to EC2 ({EC2_HOST}) via SSH...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    key = paramiko.Ed25519Key.from_private_key_file(str(KEY_FILE))
    
    ssh.connect(EC2_HOST, username=EC2_USER, pkey=key, timeout=15)
    print("      Connected successfully!")

    print("[2/3] Uploading updated session (state.json) to EC2...")
    sftp = ssh.open_sftp()
    
    # Backup remote state.json if it exists
    try:
        ssh.exec_command(f"cp {REMOTE_DIR}/state.json {REMOTE_DIR}/state.json.bak")
    except Exception:
        pass

    remote_state_path = f"{REMOTE_DIR}/state.json"
    sftp.put(str(local_state_file), remote_state_path)
    sftp.close()
    print("      Session uploaded successfully!")

    print("[3/3] Restarting Keka Attendance Bot on EC2...")
    stdin, stdout, stderr = ssh.exec_command("sudo systemctl restart keka-bot.service && systemctl is-active keka-bot.service")
    status = stdout.read().decode().strip()
    ssh.close()

    if status == "active":
        print("      Bot service restarted and is ACTIVE!")
        return True
    else:
        print(f"      Warning: Bot status is '{status}'")
        return False

def run_sync():
    print("=" * 68)
    print("              KEKA ATTENDANCE - 1-CLICK SESSION SYNC")
    print("=" * 68)
    print("\n  This tool opens a browser window for you to log in.")
    print("  The bot will automatically capture your session and upload it to EC2.")
    print("  No passwords or emails are stored in any code or files.\n")
    print("=" * 68)

    state_file = BASE_DIR / "state.json"
    target_url = config.KEKA_URL

    print("\n[Step 1] Launching Chrome browser...")
    
    # Start thread listening for Enter key in case user wants to manually trigger
    t = threading.Thread(target=listen_for_enter, daemon=True)
    t.start()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
            ]
        )
        context = browser.new_context(
            viewport=None,
            permissions=["geolocation"],
            geolocation={"latitude": config.GEO_LATITUDE, "longitude": config.GEO_LONGITUDE}
        )
        page = context.new_page()

        print(f"[Step 2] Navigating to {target_url} ...")
        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"         Navigation note: {e}")

        print("\n" + "=" * 68)
        print("  ACTION: Please log in in the opened browser window.")
        print("  (Enter your email, password, and captcha / SSO)")
        print("  --> Once you reach your Keka dashboard, we will auto-detect it.")
        print("  --> (Or press [ENTER] in this terminal once logged in)")
        print("=" * 68 + "\n")

        # Wait loop for login completion
        logged_in = False
        start_wait = time.time()
        max_wait = 300  # 5 minutes max wait time

        while time.time() - start_wait < max_wait:
            if user_pressed_enter:
                print("\n[Enter detected] Capturing session now...")
                logged_in = True
                break

            try:
                # If user manually closed browser window
                if page.is_closed():
                    print("\nBrowser closed. Proceeding to save...")
                    break
            except Exception:
                break

            if is_on_dashboard(page):
                print("\n[Auto-detected] Dashboard reached! Capturing session...")
                time.sleep(2)  # Allow tokens to finish saving in localStorage
                logged_in = True
                break

            time.sleep(1.5)

        if not logged_in and not user_pressed_enter:
            print("[Note] Attempting to save current browser session...")

        # Save session to state.json
        try:
            context.storage_state(path=str(state_file))
            print(f"[Step 3] Session tokens saved locally ({state_file.stat().st_size} bytes).")
        except Exception as e:
            print(f"[ERROR] Could not save storage state: {e}")
            return

        try:
            context.close()
            browser.close()
        except Exception:
            pass

    # Validate state.json
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        has_tokens = any("token" in item.get("name", "").lower() 
                         for origin in data.get("origins", []) 
                         for item in origin.get("localStorage", []))
        if not has_tokens and not data.get("cookies"):
            print("\n[WARNING] It looks like the login was not completed. Please try again.")
            return
    except Exception as e:
        print(f"[ERROR] Invalid state.json: {e}")
        return

    # Upload to EC2
    print("\n[Step 4] Syncing session with AWS EC2 Server...")
    success = upload_to_ec2(state_file)

    if success:
        print("\n" + "=" * 68)
        print("  🎉 ALL SET! YOUR KEKA SESSION IS FULLY SYNCED!")
        print("  • Your bot on AWS EC2 is running 24x7.")
        print("  • Keepalive will automatically ping Keka daily to keep this active.")
        print("  • Telegram will alert you 24h in advance if a re-login is ever needed.")
        print("=" * 68)
    else:
        print("\n" + "=" * 68)
        print("  ⚠️ Session saved locally, but EC2 upload had a warning.")
        print("  You can also drag-and-drop state.json directly into your Telegram bot!")
        print("=" * 68)

if __name__ == "__main__":
    run_sync()
