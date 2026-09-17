import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

import config

def run_login_setup():
    print("=" * 65)
    print("  KEKA ATTENDANCE BOT - ONE-TIME LOGIN SETUP")
    print("=" * 65)
    print(f"\n[INFO] Target Keka URL: {config.KEKA_URL}")
    print(f"[INFO] Profile directory: {config.PROFILE_DIR}")
    
    if "yourcompany.keka.com" in config.KEKA_URL:
        print("\n[WARNING] You haven't configured your company's Keka URL in .env yet!")
        print("Please edit your .env file with your actual Keka URL (e.g., https://myorg.keka.com)")
        url_input = input("\nEnter your Keka URL now (or press Enter to exit): ").strip()
        if not url_input:
            print("Exiting. Please update your .env file and re-run.")
            sys.exit(0)
        target_url = url_input.rstrip("/")
    else:
        target_url = config.KEKA_URL

    print("\n[1/3] Launching Chrome browser...")
    with sync_playwright() as p:
        # Launch visible browser with persistent context to store cookies and SSO tokens
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(config.PROFILE_DIR),
            headless=False,
            viewport={"width": 1366, "height": 768},
            permissions=["geolocation"],
            geolocation={"latitude": config.GEO_LATITUDE, "longitude": config.GEO_LONGITUDE},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
            ]
        )
        
        page = context.pages[0] if context.pages else context.new_page()
        
        print(f"[2/3] Opening {target_url} ...")
        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"[WARN] Navigation note: {e}")
        
        print("\n" + "=" * 65)
        print(" ACTION REQUIRED IN THE OPEN BROWSER WINDOW:")
        print(" 1. Select your SSO provider (Microsoft / Google).")
        print(" 2. Enter your credentials and complete 2FA / Authenticator approval.")
        print(" 3. Wait until your Keka Home / Dashboard has fully loaded.")
        print("=" * 65)
        
        input("\n>>> Press [ENTER] in this terminal AFTER your Keka dashboard is visible... ")
        
        print("\n[3/3] Verifying and capturing dashboard...")
        time.sleep(3)
        screenshot_path = config.SCREENSHOTS_DIR / "initial_login_verification.png"
        try:
            page.screenshot(path=str(screenshot_path))
            print(f"[SUCCESS] Dashboard screenshot saved to: {screenshot_path}")
        except Exception as e:
            print(f"[WARN] Could not capture screenshot: {e}")
            
        print("[SUCCESS] Closing browser and saving session tokens...")
        context.close()
        
    print("\n" + "=" * 65)
    print(" ALL DONE! Your Keka session has been securely saved.")
    print(" The bot can now run in headless mode without asking for credentials.")
    print("=" * 65)

if __name__ == "__main__":
    run_login_setup()
