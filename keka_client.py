import os
import re
import time
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional
import pytz
from playwright.sync_api import sync_playwright, Page, BrowserContext, TimeoutError as PlaywrightTimeoutError

import config

def get_current_time_str() -> str:
    tz = pytz.timezone(config.TIMEZONE)
    return datetime.now(tz).strftime("%I:%M %p")

class KekaClient:
    def __init__(self, headless: Optional[bool] = None):
        self.headless = config.HEADLESS if headless is None else headless
        self.tz = pytz.timezone(config.TIMEZONE)

    def _create_context(self, playwright_instance) -> BrowserContext:
        """Launches Chromium with user's saved session cookies & mock geolocation."""
        state_file = getattr(config, "SESSION_FILE", Path(__file__).resolve().parent / "state.json")
        chromium_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ]
        if state_file.exists():
            browser = playwright_instance.chromium.launch(
                headless=self.headless,
                args=chromium_args
            )
            context = browser.new_context(
                storage_state=str(state_file),
                viewport={"width": 1366, "height": 768},
                permissions=["geolocation"],
                geolocation={"latitude": config.GEO_LATITUDE, "longitude": config.GEO_LONGITUDE}
            )
            context._keka_browser = browser
            return context

        return playwright_instance.chromium.launch_persistent_context(
            user_data_dir=str(config.PROFILE_DIR),
            headless=self.headless,
            viewport={"width": 1366, "height": 768},
            permissions=["geolocation"],
            geolocation={"latitude": config.GEO_LATITUDE, "longitude": config.GEO_LONGITUDE},
            args=chromium_args
        )

    def _close_context(self, context: BrowserContext, page: Optional[Page] = None):
        """Saves updated storage state ONLY if authenticated, then cleanly closes context/browser."""
        state_file = getattr(config, "SESSION_FILE", Path(__file__).resolve().parent / "state.json")
        try:
            # Safeguard: never overwrite state.json if the browser was redirected to a login/auth page
            should_save = True
            if page is not None:
                if not self._check_logged_in(page):
                    should_save = False
            else:
                for p in getattr(context, "pages", []):
                    if not self._check_logged_in(p):
                        should_save = False
                        break

            if should_save and (state_file.exists() or hasattr(context, "_keka_browser")):
                context.storage_state(path=str(state_file))
        except Exception:
            pass
        try:
            context.close()
        except Exception:
            pass
        if hasattr(context, "_keka_browser") and context._keka_browser:
            try:
                context._keka_browser.close()
            except Exception:
                pass

    def _check_logged_in(self, page: Page) -> bool:
        """Checks whether the user is still authenticated or redirected to login."""
        current_url = page.url.lower()
        if "login" in current_url or "signin" in current_url or "oauth" in current_url:
            return False
        # If login buttons or password input are present
        try:
            if page.locator("input[type='password']").is_visible(timeout=1000):
                return False
            if page.locator("button:has-text('Sign In'), button:has-text('Log In'), button:has-text('Login')").first.is_visible(timeout=1000):
                return False
        except Exception:
            pass
        return True

    def get_session_info(self) -> Dict[str, Any]:
        """
        Inspects session tokens and returns detailed lifetime and expiration status.
        """
        state_file = getattr(config, "SESSION_FILE", Path(__file__).resolve().parent / "state.json")
        if not state_file.exists():
            return {
                "exists": False,
                "is_expired": True,
                "needs_warning": True,
                "hours_remaining": 0.0,
                "status_text": "No session file found. Please run sync_login.bat.",
                "expiry_formatted": "N/A"
            }

        try:
            with open(state_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            now_ts = datetime.now(self.tz).timestamp()
            last_activity_ts = None

            # Check localStorage timestamps
            for origin in data.get("origins", []):
                for item in origin.get("localStorage", []):
                    name = item.get("name")
                    val = item.get("value")
                    if name in ["access_token_stored_at", "id_token_stored_at"] and val:
                        try:
                            ts = float(val)
                            if ts > 1e11:
                                ts = ts / 1000.0
                            if last_activity_ts is None or ts > last_activity_ts:
                                last_activity_ts = ts
                        except Exception:
                            pass

            # Fallback to file modification time
            if last_activity_ts is None:
                last_activity_ts = state_file.stat().st_mtime

            # Keka sliding session is 72 hours from last activity
            sliding_limit_ts = last_activity_ts + (72 * 3600)
            diff_seconds = sliding_limit_ts - now_ts
            hours_remaining = max(0.0, diff_seconds / 3600.0)

            last_active_dt = datetime.fromtimestamp(last_activity_ts, tz=self.tz)
            expiry_dt = datetime.fromtimestamp(sliding_limit_ts, tz=self.tz)

            is_expired = (diff_seconds <= 0)
            needs_warning = (0 < hours_remaining <= 24)

            return {
                "exists": True,
                "is_expired": is_expired,
                "needs_warning": needs_warning,
                "hours_remaining": round(hours_remaining, 1),
                "last_active_formatted": last_active_dt.strftime("%d %b, %I:%M %p"),
                "expiry_formatted": expiry_dt.strftime("%d %b, %I:%M %p"),
                "status_text": "Expired" if is_expired else f"Active ({round(hours_remaining, 1)}h remaining)"
            }
        except Exception as e:
            return {
                "exists": True,
                "is_expired": True,
                "needs_warning": True,
                "hours_remaining": 0.0,
                "status_text": f"Error parsing session: {e}",
                "expiry_formatted": "Unknown"
            }

    def keepalive(self) -> Dict[str, Any]:
        """
        Silent background visit to Keka to refresh the sliding 72-hour session tokens.
        """
        screenshot_name = f"keepalive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        screenshot_path = config.SCREENSHOTS_DIR / screenshot_name

        with sync_playwright() as p:
            try:
                context = self._create_context(p)
                page = context.pages[0] if context.pages else context.new_page()

                page.goto(config.KEKA_URL, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                page.wait_for_timeout(4000)

                logged_in = self._check_logged_in(page)
                page.screenshot(path=str(screenshot_path))
                self._close_context(context, page=page)

                if logged_in:
                    return {
                        "success": True,
                        "is_logged_in": True,
                        "message": "Session refreshed successfully.",
                        "screenshot_path": str(screenshot_path)
                    }
                else:
                    return {
                        "success": False,
                        "is_logged_in": False,
                        "message": "Session has expired and requires re-login.",
                        "screenshot_path": str(screenshot_path)
                    }
            except Exception as e:
                return {
                    "success": False,
                    "is_logged_in": False,
                    "message": f"Keepalive failed: {e}",
                    "screenshot_path": None
                }

    def _extract_clock_in_time(self, page) -> Optional[str]:
        """Extracts the actual clock-in punch timestamp displayed on Keka."""
        for query in [
            r"text=/Clocked in at [0-9]{1,2}:[0-9]{2}\s*(?:AM|PM)?/i",
            r"text=/(?:First\s+In|In\s+Time)[:\s]+[0-9]{1,2}:[0-9]{2}\s*(?:AM|PM)?/i",
        ]:
            try:
                match_elem = page.locator(query).first
                if match_elem.is_visible():
                    text = match_elem.inner_text().strip()
                    m = re.search(r"([0-9]{1,2}:[0-9]{2}\s*(?:AM|PM)?)", text, re.IGNORECASE)
                    if m:
                        return m.group(1).strip()
            except Exception:
                pass

        try:
            body_text = page.inner_text("body")

            # Check for 'Xh:Ym Since Last Login'
            m_since = re.search(r"(\d+)\s*h\s*:\s*(\d+)\s*m\s*Since\s+Last\s+Login", body_text, re.IGNORECASE)
            if m_since:
                h = int(m_since.group(1))
                mins = int(m_since.group(2))
                now_dt = datetime.now(self.tz)
                punch_dt = now_dt - timedelta(hours=h, minutes=mins)
                return punch_dt.strftime("%I:%M %p")

            # Check for 'Effective: Xh Ym'
            m_eff = re.search(r"Effective:\s*(\d+)\s*h(?:\s*:\s*|\s+)(\d+)\s*m", body_text, re.IGNORECASE)
            if m_eff:
                h = int(m_eff.group(1))
                mins = int(m_eff.group(2))
                now_dt = datetime.now(self.tz)
                punch_dt = now_dt - timedelta(hours=h, minutes=mins)
                return punch_dt.strftime("%I:%M %p")

            # Check for direct punch labels
            for pat in [
                r"Clocked\s+in\s+at\s+([0-9]{1,2}:[0-9]{2}\s*(?:AM|PM)?)",
                r"(?:First\s+In|In\s+Time|In)\s*[:\-]?\s*([0-9]{1,2}:[0-9]{2}\s*(?:AM|PM)?)",
                r"([0-9]{1,2}:[0-9]{2}\s*(?:AM|PM))\s*(?:to\s*Present|till\s*now)",
            ]:
                m = re.search(pat, body_text, re.IGNORECASE)
                if m:
                    return m.group(1).strip()
        except Exception:
            pass
        return None

    def get_status(self) -> Dict[str, Any]:
        """
        Navigates to Keka and inspects current attendance state.
        Returns:
            {
                "success": bool,
                "is_clocked_in": bool,
                "status_text": str,
                "clock_in_time": Optional[str],
                "screenshot_path": Optional[str],
                "error": Optional[str]
            }
        """
        screenshot_name = f"status_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        screenshot_path = config.SCREENSHOTS_DIR / screenshot_name

        with sync_playwright() as p:
            try:
                context = self._create_context(p)
                page = context.pages[0] if context.pages else context.new_page()

                page.goto(config.KEKA_URL, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                page.wait_for_timeout(3000)  # Allow dynamic SPA components to settle

                if not self._check_logged_in(page):
                    page.screenshot(path=str(screenshot_path))
                    self._close_context(context, page=page)
                    return {
                        "success": False,
                        "is_clocked_in": False,
                        "status_text": "Session expired or not logged in.",
                        "clock_in_time": None,
                        "screenshot_path": str(screenshot_path),
                        "error": "Not logged in. Please run setup_login.py to authenticate."
                    }

                # Navigate to Me -> Attendance to get rich attendance card & punch time
                try:
                    att_url = f"{config.KEKA_URL.rstrip('/')}/#/me/attendance"
                    page.goto(att_url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(3500)
                except Exception:
                    pass

                # Wait up to 5s for attendance button to mount in DOM if still loading
                try:
                    page.locator("button:has-text('Web Clock-In'), button:has-text('Web Clock-Out')").first.wait_for(state="visible", timeout=6000)
                except Exception:
                    pass

                # Check for Web Clock-Out button (indicates currently clocked in)
                clock_out_btn = page.locator("button:has-text('Web Clock-Out'), button:has-text('Clock-Out'), a:has-text('Web Clock-Out')").first
                is_clocked_in = clock_out_btn.is_visible()

                # Check for Web Clock-In button
                clock_in_btn = page.locator("button:has-text('Web Clock-In'), button:has-text('Clock-In'), a:has-text('Web Clock-In')").first
                can_clock_in = clock_in_btn.is_visible()

                # Extract time info if available on dashboard
                clock_in_time = self._extract_clock_in_time(page)

                page.screenshot(path=str(screenshot_path))
                self._close_context(context, page=page)

                if is_clocked_in:
                    status_text = f"Currently Clocked In ({clock_in_time if clock_in_time else 'Active'})"
                elif can_clock_in:
                    status_text = "Currently Clocked Out (Ready to Clock In)"
                else:
                    status_text = "Attendance status unclear (Dashboard loaded)"

                return {
                    "success": True,
                    "is_clocked_in": is_clocked_in,
                    "can_clock_in": can_clock_in,
                    "status_text": status_text,
                    "clock_in_time": clock_in_time,
                    "screenshot_path": str(screenshot_path),
                    "error": None
                }

            except Exception as e:
                return {
                    "success": False,
                    "is_clocked_in": False,
                    "status_text": "Failed to check status",
                    "clock_in_time": None,
                    "screenshot_path": None,
                    "error": str(e)
                }

    def clock_in(self) -> Dict[str, Any]:
        """
        Clicks the Web Clock-In button on Keka.
        Returns:
            {
                "success": bool,
                "already_done": bool,
                "message": str,
                "clock_in_time": Optional[str],
                "time": str,
                "screenshot_path": Optional[str]
            }
        """
        now_str = get_current_time_str()
        screenshot_name = f"clockin_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        screenshot_path = config.SCREENSHOTS_DIR / screenshot_name

        with sync_playwright() as p:
            try:
                context = self._create_context(p)
                page = context.pages[0] if context.pages else context.new_page()

                print(f"[KekaClient] Navigating to {config.KEKA_URL} ...")
                page.goto(config.KEKA_URL, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                page.wait_for_timeout(3000)

                if not self._check_logged_in(page):
                    page.screenshot(path=str(screenshot_path))
                    self._close_context(context, page=page)
                    return {
                        "success": False,
                        "already_done": False,
                        "message": "Login session expired. Run setup_login.py on your machine.",
                        "clock_in_time": None,
                        "time": now_str,
                        "screenshot_path": str(screenshot_path)
                    }

                # Check if already clocked in
                clock_out_btn = page.locator("button:has-text('Web Clock-Out'), button:has-text('Clock-Out')").first
                if clock_out_btn.is_visible():
                    existing_time = self._extract_clock_in_time(page)
                    page.screenshot(path=str(screenshot_path))
                    self._close_context(context, page=page)
                    return {
                        "success": True,
                        "already_done": True,
                        "message": f"You are ALREADY clocked in ({existing_time or 'Active'})!",
                        "clock_in_time": existing_time or now_str,
                        "time": existing_time or now_str,
                        "screenshot_path": str(screenshot_path)
                    }

                # Locate Clock-In button
                clock_in_btn = page.locator("button:has-text('Web Clock-In'), button:has-text('Clock-In'), a:has-text('Web Clock-In')").first
                if not clock_in_btn.is_visible():
                    page.screenshot(path=str(screenshot_path))
                    self._close_context(context, page=page)
                    return {
                        "success": False,
                        "already_done": False,
                        "message": "Could not find 'Web Clock-In' button on Keka page.",
                        "clock_in_time": None,
                        "time": now_str,
                        "screenshot_path": str(screenshot_path)
                    }

                # Click Clock-In
                print("[KekaClient] Clicking Web Clock-In button...")
                clock_in_btn.click()
                page.wait_for_timeout(2000)

                # Check if confirmation dialog / modal appeared
                confirm_btn = page.locator("button:has-text('Confirm'), button:has-text('Clock In'), button:has-text('Clock-In')").last
                if confirm_btn.is_visible():
                    print("[KekaClient] Confirming in popup modal...")
                    confirm_btn.click()
                    page.wait_for_timeout(3000)

                # Wait for state change / animation to settle
                page.wait_for_timeout(3000)

                # Extract the actual confirmed clock-in time directly from Keka
                actual_clock_in_time = self._extract_clock_in_time(page) or now_str

                page.screenshot(path=str(screenshot_path))
                self._close_context(context, page=page)

                return {
                    "success": True,
                    "already_done": False,
                    "message": f"Successfully clocked in at {actual_clock_in_time}!",
                    "clock_in_time": actual_clock_in_time,
                    "time": actual_clock_in_time,
                    "screenshot_path": str(screenshot_path)
                }

            except Exception as e:
                return {
                    "success": False,
                    "already_done": False,
                    "message": f"Error executing Clock-In: {str(e)}",
                    "clock_in_time": None,
                    "time": now_str,
                    "screenshot_path": None
                }

    def clock_out(self) -> Dict[str, Any]:
        """
        Clicks the Web Clock-Out button on Keka.
        Returns:
            {
                "success": bool,
                "message": str,
                "time": str,
                "screenshot_path": Optional[str]
            }
        """
        now_str = get_current_time_str()
        screenshot_name = f"clockout_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        screenshot_path = config.SCREENSHOTS_DIR / screenshot_name

        with sync_playwright() as p:
            try:
                context = self._create_context(p)
                page = context.pages[0] if context.pages else context.new_page()

                print(f"[KekaClient] Navigating to {config.KEKA_URL} ...")
                page.goto(config.KEKA_URL, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                page.wait_for_timeout(3000)

                if not self._check_logged_in(page):
                    page.screenshot(path=str(screenshot_path))
                    self._close_context(context, page=page)
                    return {
                        "success": False,
                        "message": "Login session expired. Run setup_login.py on your machine.",
                        "time": now_str,
                        "screenshot_path": str(screenshot_path)
                    }

                # Locate Clock-Out button
                clock_out_btn = page.locator("button:has-text('Web Clock-Out'), button:has-text('Clock-Out'), a:has-text('Web Clock-Out')").first
                if not clock_out_btn.is_visible():
                    # Check if already clocked out (Clock-In button is visible instead)
                    clock_in_btn = page.locator("button:has-text('Web Clock-In'), button:has-text('Clock-In')").first
                    page.screenshot(path=str(screenshot_path))
                    self._close_context(context, page=page)
                    if clock_in_btn.is_visible():
                        return {
                            "success": True,
                            "already_done": True,
                            "message": "You are ALREADY clocked out!",
                            "time": now_str,
                            "screenshot_path": str(screenshot_path)
                        }
                    return {
                        "success": False,
                        "message": "Could not find 'Web Clock-Out' button on Keka page.",
                        "time": now_str,
                        "screenshot_path": str(screenshot_path)
                    }

                # Click Clock-Out
                print("[KekaClient] Clicking Web Clock-Out button...")
                clock_out_btn.click()
                page.wait_for_timeout(2000)

                # Check if confirmation modal / reason prompt appeared
                confirm_btn = page.locator("button:has-text('Confirm'), button:has-text('Clock Out'), button:has-text('Clock-Out')").last
                if confirm_btn.is_visible():
                    print("[KekaClient] Confirming clock out in modal...")
                    confirm_btn.click()
                    page.wait_for_timeout(3000)

                page.wait_for_timeout(3000)
                page.screenshot(path=str(screenshot_path))
                self._close_context(context, page=page)

                return {
                    "success": True,
                    "already_done": False,
                    "message": f"Successfully clocked out at {now_str}!",
                    "time": now_str,
                    "screenshot_path": str(screenshot_path)
                }

            except Exception as e:
                return {
                    "success": False,
                    "message": f"Error executing Clock-Out: {str(e)}",
                    "time": now_str,
                    "screenshot_path": None
                }
