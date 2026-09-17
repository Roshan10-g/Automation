import os
from pathlib import Path
from dotenv import load_dotenv

# Base paths
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

# Load environment variables
load_dotenv(dotenv_path=ENV_PATH)

# State and Session configurations
PROFILE_DIR = BASE_DIR / "browser_profile"
SCREENSHOTS_DIR = BASE_DIR / "screenshots"
SESSION_FILE = BASE_DIR / "state.json"  # Playwright browser cookies / storage_state
ATTENDANCE_STATE_FILE = BASE_DIR / "attendance_state.json"  # Bot daily attendance tracking
STATE_FILE = ATTENDANCE_STATE_FILE  # Backwards-compatible alias

PROFILE_DIR.mkdir(exist_ok=True)
SCREENSHOTS_DIR.mkdir(exist_ok=True)

# Keka configuration
KEKA_URL = os.getenv("KEKA_URL", "https://yourcompany.keka.com").strip().rstrip("/")

# Telegram Bot configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Scheduling configuration
MORNING_REMINDER_TIME = os.getenv("MORNING_REMINDER_TIME", "09:30").strip()
try:
    SHIFT_DURATION_MINUTES = int(os.getenv("SHIFT_DURATION_MINUTES", "545"))
except ValueError:
    SHIFT_DURATION_MINUTES = 545  # 9 hours 5 minutes

TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata").strip()

# Geolocation configuration (default fallback coords)
try:
    GEO_LATITUDE = float(os.getenv("GEO_LATITUDE", "17.4483"))
    GEO_LONGITUDE = float(os.getenv("GEO_LONGITUDE", "78.3915"))
except ValueError:
    GEO_LATITUDE = 17.4483
    GEO_LONGITUDE = 78.3915

# Browser headless mode
HEADLESS = os.getenv("HEADLESS", "True").strip().lower() in ("true", "1", "yes")

# Automation modes
# False = Sends message first and waits for your confirmation (button or 'yes') before clocking in
AUTO_CLOCK_IN = os.getenv("AUTO_CLOCK_IN", "False").strip().lower() in ("true", "1", "yes")
# False = Sends message after 9 hours and waits for your confirmation before clocking out
AUTO_CLOCK_OUT = os.getenv("AUTO_CLOCK_OUT", "False").strip().lower() in ("true", "1", "yes")


