import json
from pathlib import Path
from keka_client import KekaClient
import config

def test():
    print("=" * 60)
    print("  TESTING KEKA AUTOMATION CLIENT")
    print("=" * 60)
    print(f"Keka URL: {config.KEKA_URL}")
    print(f"Browser Profile Directory: {config.PROFILE_DIR}")
    print(f"Headless Mode: {config.HEADLESS}")
    print("\nNavigating to Keka and querying status...")

    client = KekaClient()
    result = client.get_status()

    print("\n--- STATUS RESULT ---")
    print(f"Success: {result.get('success')}")
    print(f"Status Text: {result.get('status_text')}")
    print(f"Is Clocked In: {result.get('is_clocked_in')}")
    print(f"Clock-in Time: {result.get('clock_in_time')}")
    if result.get("screenshot_path"):
        print(f"Screenshot: {result.get('screenshot_path')}")
    if result.get("error"):
        print(f"Error Note: {result.get('error')}")
    print("=" * 60)

if __name__ == "__main__":
    test()
