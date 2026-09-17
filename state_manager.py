import json
from datetime import datetime, date
from pathlib import Path
from typing import Dict, Any, Optional
import pytz

import config

class StateManager:
    """Persists attendance state (clock-in time, dynamic shift end time) across bot reboots."""
    
    def __init__(self):
        self.state_file: Path = getattr(config, "ATTENDANCE_STATE_FILE", config.STATE_FILE)
        self.tz = pytz.timezone(config.TIMEZONE)

    def _load_raw(self) -> Dict[str, Any]:
        if not self.state_file.exists():
            return {}
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_raw(self, data: Dict[str, Any]):
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            print(f"[StateManager] Failed to save state: {e}")

    def get_today_state(self) -> Dict[str, Any]:
        data = self._load_raw()
        today_str = datetime.now(self.tz).strftime("%Y-%m-%d")
        today_data = data.get(today_str, {
            "date": today_str,
            "is_clocked_in": False,
            "is_clocked_out": False,
            "clock_in_iso": None,
            "clock_in_formatted": None,
            "target_clock_out_iso": None,
            "target_clock_out_formatted": None,
            "clock_out_iso": None,
            "clock_out_formatted": None,
        })
        return today_data

    def record_clock_in(self, dt: Optional[datetime] = None) -> Dict[str, Any]:
        if dt:
            if dt.tzinfo is None:
                now_dt = self.tz.localize(dt)
            else:
                now_dt = dt.astimezone(self.tz)
        else:
            now_dt = datetime.now(self.tz)

        today_str = now_dt.strftime("%Y-%m-%d")
        
        # Calculate target dynamic clock out time (+ SHIFT_DURATION_MINUTES)
        target_timestamp = now_dt.timestamp() + (config.SHIFT_DURATION_MINUTES * 60)
        target_dt = datetime.fromtimestamp(target_timestamp, tz=self.tz)

        data = self._load_raw()
        today_data = {
            "date": today_str,
            "is_clocked_in": True,
            "is_clocked_out": False,
            "clock_in_iso": now_dt.isoformat(),
            "clock_in_formatted": now_dt.strftime("%I:%M %p"),
            "target_clock_out_iso": target_dt.isoformat(),
            "target_clock_out_formatted": target_dt.strftime("%I:%M %p"),
            "clock_out_iso": None,
            "clock_out_formatted": None,
        }
        data[today_str] = today_data
        self._save_raw(data)
        return today_data

    def record_clock_out(self, dt: Optional[datetime] = None) -> Dict[str, Any]:
        now_dt = dt or datetime.now(self.tz)
        today_str = now_dt.strftime("%Y-%m-%d")

        data = self._load_raw()
        today_data = data.get(today_str, {
            "date": today_str,
            "is_clocked_in": False,
            "clock_in_iso": None,
            "clock_in_formatted": None,
            "target_clock_out_iso": None,
            "target_clock_out_formatted": None,
        })

        today_data["is_clocked_in"] = False
        today_data["is_clocked_out"] = True
        today_data["clock_out_iso"] = now_dt.isoformat()
        today_data["clock_out_formatted"] = now_dt.strftime("%I:%M %p")

        data[today_str] = today_data
        self._save_raw(data)
        return today_data

    def record_skip(self, dt: Optional[datetime] = None) -> Dict[str, Any]:
        now_dt = dt or datetime.now(self.tz)
        today_str = now_dt.strftime("%Y-%m-%d")

        data = self._load_raw()
        today_data = data.get(today_str, {
            "date": today_str,
            "is_clocked_in": False,
            "is_clocked_out": False,
            "clock_in_iso": None,
            "clock_in_formatted": None,
            "target_clock_out_iso": None,
            "target_clock_out_formatted": None,
        })
        today_data["is_skipped"] = True
        data[today_str] = today_data
        self._save_raw(data)
        return today_data
