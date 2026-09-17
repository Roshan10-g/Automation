import asyncio
import logging
import re
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
import pytz

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import config
from keka_client import KekaClient
from state_manager import StateManager

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("KekaAttendanceBot")

state_mgr = StateManager()
keka = KekaClient()
LOCAL_TZ = pytz.timezone(config.TIMEZONE)

def is_authorized(user_id: int) -> bool:
    """Verifies that the incoming message is from the authorized user."""
    if not config.TELEGRAM_CHAT_ID:
        return True  # If not configured yet, allow for setup
    return str(user_id) == str(config.TELEGRAM_CHAT_ID)

# --- TELEGRAM COMMAND HANDLERS ---

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_name = update.effective_user.first_name or "there"

    if not is_authorized(chat_id):
        await update.message.reply_text(
            f"⛔ Unauthorized. Your Chat ID is: `{chat_id}`\n"
            "Please add this ID to your `.env` file under `TELEGRAM_CHAT_ID`.",
            parse_mode="Markdown"
        )
        return

    msg = (
        f"👋 *Hi {user_name}! Welcome to your Keka Attendance Assistant.*\n\n"
        f"🏢 *Portal:* `{config.KEKA_URL}`\n"
        f"⏰ *Morning Alert:* `{config.MORNING_REMINDER_TIME}` (Mon-Fri)\n"
        f"⏱️ *Shift Target:* `{config.SHIFT_DURATION_MINUTES // 60}h {config.SHIFT_DURATION_MINUTES % 60}m`\n\n"
        "💡 *Available Commands:*\n"
        "• `/status` - Check current attendance state & elapsed hours\n"
        "• `/session` - Check Keka login session health & remaining hours\n"
        "• `/clockin` - Trigger Clock-In immediately\n"
        "• `/clockout` - Trigger Clock-Out immediately\n"
        "• `/help` - View instructions"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

def parse_keka_time(time_str: Optional[str]) -> Optional[datetime]:
    """
    Parses a time string from Keka (e.g., '09:15 AM', '9:30 am', 'Clocked in at 10:05 AM')
    into a timezone-aware datetime for today's date in LOCAL_TZ.
    """
    if not time_str:
        return None
    try:
        m = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)?", time_str, re.IGNORECASE)
        if not m:
            return None
        hour = int(m.group(1))
        minute = int(m.group(2))
        meridiem = m.group(3)

        if meridiem:
            meridiem = meridiem.upper()
            if meridiem == "PM" and hour < 12:
                hour += 12
            elif meridiem == "AM" and hour == 12:
                hour = 0

        now = datetime.now(LOCAL_TZ)
        parsed_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return parsed_dt
    except Exception as e:
        logger.warning(f"Could not parse Keka time '{time_str}': {e}")
        return None

def schedule_dynamic_reminder(chat_id: int, clock_in_dt: datetime, clock_in_str: str, context: ContextTypes.DEFAULT_TYPE):
    """
    Cancels any previous dynamic evening reminders and schedules a new one based on the
    actual remaining time until clock_in_dt + SHIFT_DURATION_MINUTES.
    """
    if context and context.job_queue:
        # Cancel any existing dynamic evening reminder to prevent duplicates
        for job in context.job_queue.get_jobs_by_name("dynamic_evening_reminder"):
            job.schedule_removal()

    # Ensure clock_in_dt is timezone-aware
    if clock_in_dt.tzinfo is None:
        clock_in_dt = LOCAL_TZ.localize(clock_in_dt)
    else:
        clock_in_dt = clock_in_dt.astimezone(LOCAL_TZ)

    target_dt = clock_in_dt + timedelta(minutes=config.SHIFT_DURATION_MINUTES)
    now_dt = datetime.now(LOCAL_TZ)
    remaining_seconds = (target_dt - now_dt).total_seconds()

    if context and context.job_queue and chat_id:
        if remaining_seconds > 0:
            context.job_queue.run_once(
                send_evening_prompt_job,
                when=remaining_seconds,
                chat_id=chat_id,
                name="dynamic_evening_reminder",
                data={"clock_in_time": clock_in_str}
            )
            logger.info(f"Dynamic evening reminder scheduled in {int(remaining_seconds // 60)} minutes (at {target_dt.strftime('%I:%M %p')}).")
        else:
            # Shift duration has already passed! Trigger prompt immediately
            context.job_queue.run_once(
                send_evening_prompt_job,
                when=1,
                chat_id=chat_id,
                name="dynamic_evening_reminder",
                data={"clock_in_time": clock_in_str}
            )
            logger.info("Dynamic evening reminder triggered immediately (shift duration already completed).")

    return target_dt, max(0.0, remaining_seconds)

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_chat.id):
        return

    status_msg = await update.message.reply_text("🔍 Checking your Keka attendance status... please wait.")
    
    # Run Playwright check in background thread to avoid blocking asyncio loop
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, keka.get_status)

    today_state = state_mgr.get_today_state()

    # If Keka shows clocked in, but local state wasn't updated (e.g. user clocked in manually on laptop)
    if result.get("is_clocked_in") and not today_state.get("is_clocked_in"):
        logger.info("Manual clock-in detected during status check. Syncing dynamic reminder...")
        keka_time_str = result.get("clock_in_time")
        clock_in_dt = parse_keka_time(keka_time_str) or datetime.now(LOCAL_TZ)
        today_state = state_mgr.record_clock_in(clock_in_dt)
        schedule_dynamic_reminder(
            chat_id=update.effective_chat.id,
            clock_in_dt=clock_in_dt,
            clock_in_str=today_state["clock_in_formatted"],
            context=context
        )

    clock_in_formatted = today_state.get("clock_in_formatted") or result.get("clock_in_time") or "Not recorded"
    target_formatted = today_state.get("target_clock_out_formatted") or "N/A"

    progress_line = ""
    if result.get("is_clocked_in") and today_state.get("clock_in_iso"):
        try:
            c_in = datetime.fromisoformat(today_state["clock_in_iso"])
            now_dt = datetime.now(LOCAL_TZ)
            elapsed_mins = max(0, int((now_dt - c_in).total_seconds() // 60))
            remain_mins = max(0, config.SHIFT_DURATION_MINUTES - elapsed_mins)
            progress_line = f"• *Shift Progress:* `{elapsed_mins // 60}h {elapsed_mins % 60:02d}m` completed (`{remain_mins // 60}h {remain_mins % 60:02d}m` remaining)\n"
        except Exception:
            pass

    msg = (
        f"📊 *Current Attendance Status:*\n\n"
        f"• *State:* {result.get('status_text')}\n"
        f"• *Clock-In Recorded:* `{clock_in_formatted}`\n"
        f"• *Target 9h Completion:* `{target_formatted}`\n"
        f"{progress_line}"
    )

    if result.get("is_clocked_in"):
        keyboard = [
            [InlineKeyboardButton("🚪 Clock Out Now", callback_data="do_clock_out")]
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("✅ Clock In Now", callback_data="do_clock_in")]
        ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    screenshot_path = result.get("screenshot_path")
    if screenshot_path and Path(screenshot_path).exists():
        try:
            with open(screenshot_path, "rb") as photo:
                await update.message.reply_photo(
                    photo=photo,
                    caption=msg,
                    parse_mode="Markdown",
                    reply_markup=reply_markup
                )
            await status_msg.delete()
            return
        except Exception as e:
            logger.warning(f"Could not send photo: {e}")

    await status_msg.edit_text(msg, parse_mode="Markdown", reply_markup=reply_markup)

async def cmd_clockin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_chat.id):
        return
    await execute_clock_in(update.effective_chat.id, context)

async def cmd_clockout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_chat.id):
        return
    await execute_clock_out(update.effective_chat.id, context)

async def cmd_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_chat.id):
        return

    msg = await update.message.reply_text("🔍 Checking session health... please wait.")
    info = await asyncio.to_thread(keka.get_session_info)

    if not info.get("exists"):
        await msg.edit_text(
            "❌ *No active Keka session found!*\n\n"
            "Please run `sync_login.bat` on your computer or send your `state.json` file here.",
            parse_mode="Markdown"
        )
        return

    hours = info.get("hours_remaining", 0.0)
    last_act = info.get("last_active_formatted", "Unknown")
    exp_time = info.get("expiry_formatted", "Unknown")

    if info.get("is_expired"):
        status_line = "🔴 *Status:* Expired (Re-login required)"
    elif info.get("needs_warning"):
        status_line = f"🟡 *Status:* Expiring Soon ({hours}h remaining - 24h Advance Warning!)"
    else:
        status_line = f"🟢 *Status:* Healthy ({hours}h remaining)"

    text = (
        "🔐 *Keka Session Health Check*\n\n"
        f"{status_line}\n"
        f"• *Last Activity:* `{last_act}`\n"
        f"• *Sliding Expiry:* `{exp_time}`\n\n"
        "💡 *Auto-Keepalive:* The bot pings Keka automatically in the background to renew the 72h sliding window.\n\n"
        "If you ever need to refresh your session:\n"
        "1. Double-click `sync_login.bat` on your PC, OR\n"
        "2. Drag-and-drop your `state.json` file directly into this chat."
    )
    await msg.edit_text(text, parse_mode="Markdown")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if not is_authorized(chat_id):
        return

    doc = update.message.document
    if not doc:
        return

    file_name = (doc.file_name or "").lower()
    if not file_name.endswith(".json"):
        await update.message.reply_text("⚠️ Please send a valid `.json` session file (e.g. `state.json`).")
        return

    status_msg = await update.message.reply_text("📥 Receiving session file... validating...")

    try:
        tg_file = await doc.get_file()
        import shutil
        import json

        temp_path = config.BASE_DIR / f"temp_{doc.file_name}"
        await tg_file.download_to_drive(custom_path=str(temp_path))

        with open(temp_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Validate Playwright storage state schema
        if "cookies" not in data and "origins" not in data:
            if temp_path.exists():
                temp_path.unlink()
            await status_msg.edit_text("❌ Invalid session file. It does not contain Playwright cookies or origins.")
            return

        # Backup existing state.json
        state_file = getattr(config, "SESSION_FILE", config.BASE_DIR / "state.json")
        if state_file.exists():
            backup_file = config.BASE_DIR / "state.json.bak"
            shutil.copy2(state_file, backup_file)

        # Move new file into place
        shutil.move(str(temp_path), str(state_file))

        # Check session info
        info = keka.get_session_info()
        hours = info.get("hours_remaining", 0.0)

        await status_msg.edit_text(
            "✅ *New session file installed successfully!*\n\n"
            f"• *Status:* {'🟢 Active' if not info.get('is_expired') else '🔴 Expired'}\n"
            f"• *Hours Remaining:* `{hours}h`\n"
            f"• *Expires Around:* `{info.get('expiry_formatted', 'N/A')}`\n\n"
            "The bot is ready for automated attendance! 🚀",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error installing uploaded session file: {e}")
        await status_msg.edit_text(f"❌ Error processing session file: {str(e)}")

async def daily_keepalive_and_expiry_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Runs daily in the background.
    Pings Keka to refresh the 72h sliding window.
    Sends an advance warning to Telegram if <= 24 hours remain.
    """
    logger.info("[KeepaliveJob] Running scheduled keepalive & session expiry check...")
    chat_id = config.TELEGRAM_CHAT_ID

    # 1. Run keepalive
    res = await asyncio.to_thread(keka.keepalive)
    logger.info(f"[KeepaliveJob] Keepalive result: {res.get('message')}")

    # 2. Check session info
    info = await asyncio.to_thread(keka.get_session_info)
    hours = info.get("hours_remaining", 0.0)
    is_expired = info.get("is_expired", False)
    needs_warning = info.get("needs_warning", False)

    if not chat_id:
        return

    if is_expired or not res.get("is_logged_in"):
        msg = (
            "🚨 *URGENT: Keka Login Session Has Expired!*\n\n"
            "The bot cannot clock you in or out until the session is renewed.\n\n"
            "👉 *Quick Fix Options:*\n"
            "1. Double-click `sync_login.bat` on your computer.\n"
            "2. OR send your updated `state.json` file directly into this chat."
        )
        try:
            await context.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Failed to send session expired alert: {e}")

    elif needs_warning:
        msg = (
            "⚠️ *ADVANCE NOTICE: Keka Session Expiring Soon!*\n\n"
            f"Your session has *{hours} hours* remaining (approx. `{info.get('expiry_formatted')}`).\n\n"
            "To keep automated attendance uninterrupted, please refresh your session today:\n"
            "• Double-click `sync_login.bat` on your computer, OR\n"
            "• Send your updated `state.json` file directly into this chat."
        )
        try:
            await context.bot.send_message(chat_id=int(chat_id), text=msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Failed to send 24h advance warning: {e}")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_chat.id):
        return
    help_text = (
        "🤖 *Keka Attendance Assistant Help*\n\n"
        "• Every weekday morning at your configured time, you'll receive a prompt asking if you want to clock in.\n"
        "• When you tap *[Clock In Now]*, it logs in via persistent session and clocks in.\n"
        "• It computes your exact 9h shift time and sets a dynamic timer.\n"
        "• When the 9 hours are up, it alerts you with a *[Clock Out Now]* prompt.\n\n"
        "*Commands:*\n"
        "/status - View current status and live dashboard screenshot\n"
        "/session - Check Keka login session health & remaining hours\n"
        "/clockin - Clock in immediately\n"
        "/clockout - Clock out immediately\n\n"
        "💡 *Session renewal:* If your session ever expires, double-click `sync_login.bat` on your PC, or send your `state.json` file directly into this chat!"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

# --- CORE ATTENDANCE EXECUTORS ---

async def execute_clock_in(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    msg = await context.bot.send_message(chat_id=chat_id, text="⏳ Processing Clock-In on Keka... please wait.")

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, keka.clock_in)

    if result.get("success"):
        # Extract actual clock-in punch time from Keka
        keka_time_str = result.get("clock_in_time")
        clock_in_dt = parse_keka_time(keka_time_str) or datetime.now(LOCAL_TZ)
        today_data = state_mgr.record_clock_in(clock_in_dt)

        clock_in_time = today_data["clock_in_formatted"]
        target_time = today_data["target_clock_out_formatted"]

        # Schedule or sync dynamic evening reminder for remaining shift duration
        schedule_dynamic_reminder(chat_id, clock_in_dt, clock_in_time, context)

        if result.get("already_done"):
            response_text = (
                f"ℹ️ *You are already clocked in on Keka!*\n\n"
                f"🕒 *Clocked In At:* `{clock_in_time}`\n"
                f"🎯 *Shift Target (9h 05m):* `{target_time}`\n\n"
                f"🔔 *Dynamic reminder active!* I will notify you promptly at `{target_time}` to clock out."
            )
        else:
            response_text = (
                f"✅ *Clock-In Successful!*\n\n"
                f"🕒 *Clocked In At:* `{clock_in_time}`\n"
                f"🎯 *Shift Target (9h 05m):* `{target_time}`\n\n"
                f"🔔 *Dynamic reminder scheduled!* I will notify you promptly at `{target_time}` to clock out."
            )
    else:
        response_text = f"❌ *Clock-In Failed!*\n\n`{result.get('message')}`"

    screenshot_path = result.get("screenshot_path")
    if screenshot_path and Path(screenshot_path).exists():
        try:
            with open(screenshot_path, "rb") as photo:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=response_text,
                    parse_mode="Markdown"
                )
            await msg.delete()
            return
        except Exception as e:
            logger.warning(f"Could not send photo: {e}")

    await msg.edit_text(response_text, parse_mode="Markdown")

async def execute_clock_out(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    msg = await context.bot.send_message(chat_id=chat_id, text="⏳ Processing Clock-Out on Keka... please wait.")

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, keka.clock_out)

    if result.get("success"):
        now_dt = datetime.now(LOCAL_TZ)
        today_data = state_mgr.record_clock_out(now_dt)
        clock_out_time = today_data["clock_out_formatted"]

        response_text = (
            f"🚪 *Clock-Out Successful!*\n\n"
            f"🕒 *Clocked Out At:* `{clock_out_time}`\n\n"
            f"🎉 Great job today! Have a wonderful evening."
        )
    else:
        response_text = f"❌ *Clock-Out Failed!*\n\n`{result.get('message')}`"

    screenshot_path = result.get("screenshot_path")
    if screenshot_path and Path(screenshot_path).exists():
        try:
            with open(screenshot_path, "rb") as photo:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=response_text,
                    parse_mode="Markdown"
                )
            await msg.delete()
            return
        except Exception as e:
            logger.warning(f"Could not send photo: {e}")

    await msg.edit_text(response_text, parse_mode="Markdown")

# --- CALLBACK QUERY HANDLER (BUTTON CLICKS) ---

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = update.effective_chat.id

    if not is_authorized(chat_id):
        await query.edit_message_text("⛔ Unauthorized action.")
        return

    if data == "do_clock_in":
        await query.edit_message_text("👍 Confirmed! Initiating Clock-In...")
        await execute_clock_in(chat_id, context)

    elif data == "do_clock_out":
        await query.edit_message_text("👍 Confirmed! Initiating Clock-Out...")
        await execute_clock_out(chat_id, context)

    elif data.startswith("snooze_in_"):
        minutes = int(data.split("_")[-1])
        remind_time = (datetime.now(LOCAL_TZ) + timedelta(minutes=minutes)).strftime("%I:%M %p")
        context.job_queue.run_once(
            send_morning_prompt_job,
            when=minutes * 60,
            chat_id=chat_id,
            name=f"snooze_in_{minutes}"
        )
        await query.edit_message_text(f"⏰ Snoozed! I'll remind you again in {minutes} minutes (around `{remind_time}`).", parse_mode="Markdown")

    elif data.startswith("snooze_out_"):
        minutes = int(data.split("_")[-1])
        remind_time = (datetime.now(LOCAL_TZ) + timedelta(minutes=minutes)).strftime("%I:%M %p")
        context.job_queue.run_once(
            send_evening_prompt_job,
            when=minutes * 60,
            chat_id=chat_id,
            name=f"snooze_out_{minutes}",
            data={"clock_in_time": "earlier today"}
        )
        await query.edit_message_text(f"⏰ Got it! Staying for {minutes} more minutes. Next reminder at `{remind_time}`.", parse_mode="Markdown")

    elif data == "skip_morning":
        state_mgr.record_skip()
        await query.edit_message_text("❌ Skipped Clock-In for today. Enjoy your day off! 🌴")

    elif data == "dismiss_evening":
        await query.edit_message_text("👌 Evening reminder dismissed. Don't forget to clock out if needed!")

# --- SCHEDULED JOBS ---

async def send_morning_prompt_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id or config.TELEGRAM_CHAT_ID
    if not chat_id:
        return

    # Check if weekday (0 = Monday, 4 = Friday)
    now = datetime.now(LOCAL_TZ)
    if now.weekday() > 4:
        logger.info("Weekend detected. Skipping morning clock-in.")
        return

    # Check if already clocked in locally or marked as leave/skipped today
    today_state = state_mgr.get_today_state()
    if today_state.get("is_skipped"):
        logger.info("Today is marked as leave/skipped. Skipping morning prompt.")
        return

    if today_state.get("is_clocked_in"):
        logger.info("User already clocked in today in state. Skipping morning action.")
        return

    # Check live Keka portal to see if user already clocked in manually
    loop = asyncio.get_running_loop()
    live_status = await loop.run_in_executor(None, keka.get_status)
    if live_status.get("is_clocked_in"):
        logger.info("Manual clock-in detected on Keka portal. Auto-scheduling dynamic evening reminder...")
        keka_time_str = live_status.get("clock_in_time")
        clock_in_dt = parse_keka_time(keka_time_str) or datetime.now(LOCAL_TZ)
        today_data = state_mgr.record_clock_in(clock_in_dt)
        clock_in_time = today_data["clock_in_formatted"]
        target_time = today_data["target_clock_out_formatted"]

        schedule_dynamic_reminder(chat_id, clock_in_dt, clock_in_time, context)

        notice_msg = (
            f"ℹ️ *Good morning!*\n\n"
            f"I checked Keka and saw you **already clocked in manually** (`{clock_in_time}`).\n\n"
            f"🎯 *Shift Target (9h 05m):* `{target_time}`\n"
            f"🔔 *Dynamic reminder scheduled!* I will notify you promptly at `{target_time}` to clock out."
        )
        await context.bot.send_message(chat_id=chat_id, text=notice_msg, parse_mode="Markdown")
        return

    # If Automatic Clock-In is enabled, directly execute clock-in
    if config.AUTO_CLOCK_IN:
        logger.info("[AUTO_CLOCK_IN] Executing automatic morning clock-in...")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⏰ *09:30 AM reached!* Starting automatic Clock-In on Keka...",
            parse_mode="Markdown"
        )
        await execute_clock_in(chat_id, context)
        return

    # Otherwise, send interactive confirmation prompt
    now_str = now.strftime("%I:%M %p")
    msg = (
        f"🌅 *Good morning!*\n\n"
        f"It's *{now_str}*. Ready to clock in to Keka?\n\n"
        "Tap below to approve:"
    )

    keyboard = [
        [InlineKeyboardButton("✅ Clock In Now", callback_data="do_clock_in")],
        [
            InlineKeyboardButton("⏰ 10m", callback_data="snooze_in_10"),
            InlineKeyboardButton("⏰ 15m", callback_data="snooze_in_15"),
            InlineKeyboardButton("⏰ 30m", callback_data="snooze_in_30"),
        ],
        [InlineKeyboardButton("❌ Skip Today", callback_data="skip_morning")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_message(
        chat_id=chat_id,
        text=msg,
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def send_evening_prompt_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id or config.TELEGRAM_CHAT_ID
    if not chat_id:
        return

    job_data = context.job.data or {}
    clock_in_time = job_data.get("clock_in_time", "9 hours ago")
    now_str = datetime.now(LOCAL_TZ).strftime("%I:%M %p")

    # If Automatic Clock-Out is enabled, directly execute clock-out
    if config.AUTO_CLOCK_OUT:
        logger.info("[AUTO_CLOCK_OUT] Executing automatic evening clock-out...")
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🎉 *9 Hours Completed!* Automatically clocking you out of Keka...",
            parse_mode="Markdown"
        )
        await execute_clock_out(chat_id, context)
        return

    # Otherwise, send interactive confirmation prompt
    msg = (
        f"🎉 *Shift Completed! (9 Hours)*\n\n"
        f"• *Clocked In At:* `{clock_in_time}`\n"
        f"• *Current Time:* `{now_str}`\n\n"
        "You have completed your required hours. Should I clock you out now?"
    )

    keyboard = [
        [InlineKeyboardButton("🚪 Clock Out Now", callback_data="do_clock_out")],
        [
            InlineKeyboardButton("⏰ Stay 15m", callback_data="snooze_out_15"),
            InlineKeyboardButton("⏰ Stay 30m", callback_data="snooze_out_30"),
        ],
        [InlineKeyboardButton("❌ Dismiss", callback_data="dismiss_evening")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await context.bot.send_message(
        chat_id=chat_id,
        text=msg,
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

# --- DELAYED ACTION JOBS ---

async def execute_delayed_clock_in_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    await context.bot.send_message(chat_id=chat_id, text="⏰ Scheduled time reached! Starting your Clock-In on Keka now...")
    await execute_clock_in(chat_id, context)

async def execute_delayed_clock_out_job(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    await context.bot.send_message(chat_id=chat_id, text="⏰ Scheduled time reached! Starting your Clock-Out on Keka now...")
    await execute_clock_out(chat_id, context)

# --- NATURAL LANGUAGE TEXT HANDLER ---

async def handle_user_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    if not is_authorized(chat_id):
        return

    raw_text = update.message.text.strip().lower()
    today_state = state_mgr.get_today_state()

    # Check for delay phrases like "do it after 10 min", "after 10 mins", "wait 10 min", "in 15 min"
    delay_match = re.search(r'(?:after|in|wait|snooze|delay)\s+(\d+)\s*(?:min|minute|m)?', raw_text)
    if delay_match:
        minutes = int(delay_match.group(1))
        target_str = (datetime.now(LOCAL_TZ) + timedelta(minutes=minutes)).strftime("%I:%M %p")

        if today_state.get("is_clocked_in"):
            # Schedule delayed clock out
            context.job_queue.run_once(
                execute_delayed_clock_out_job,
                when=minutes * 60,
                chat_id=chat_id,
                name=f"delayed_clock_out_{minutes}"
            )
            await update.message.reply_text(
                f"⏰ Got it! I will automatically clock you out in {minutes} minutes (at `{target_str}`). Finish up your tasks!",
                parse_mode="Markdown"
            )
        else:
            # Schedule delayed clock in
            context.job_queue.run_once(
                execute_delayed_clock_in_job,
                when=minutes * 60,
                chat_id=chat_id,
                name=f"delayed_clock_in_{minutes}"
            )
            await update.message.reply_text(
                f"⏰ Got it! I will automatically clock you in on Keka in {minutes} minutes (at `{target_str}`). Sit back and relax!",
                parse_mode="Markdown"
            )
        return

    # Rejection / Leave / Skip phrases (e.g. "don't", "no", "on leave", "holiday", "leave")
    if any(phrase in raw_text for phrase in [
        "don't", "dont", "no", "nope", "skip", "leave", "on leave",
        "holiday", "off today", "day off", "cancel", "not today",
        "don't do it", "dont do it", "don't clock in", "dont clock in"
    ]):
        state_mgr.record_skip()
        await update.message.reply_text(
            "🌴 *Got it! Clock-In cancelled for today.*\n\n"
            "I have marked today as your day off. Enjoy your leave and relax! ✨",
            parse_mode="Markdown"
        )
        return

    # Affirmative / Clock-In phrases
    if any(phrase in raw_text for phrase in ["clock in", "clockin", "login", "clock me in"]):
        await update.message.reply_text("👍 Received! Proceeding to Clock In on Keka...")
        await execute_clock_in(chat_id, context)
        return

    # Clock-Out phrases
    if any(phrase in raw_text for phrase in ["clock out", "clockout", "logout", "log out", "leave", "clock me out"]):
        await update.message.reply_text("👍 Received! Proceeding to Clock Out on Keka...")
        await execute_clock_out(chat_id, context)
        return

    # Contextual 'yes' / 'ok' / 'approve'
    if any(raw_text == affirmative for affirmative in ["yes", "yep", "yeah", "ok", "okay", "sure", "approve", "do it"]):
        # If user is currently clocked in and shift is done or ongoing, assume clock-out
        if today_state.get("is_clocked_in"):
            await update.message.reply_text("👍 Received 'Yes'! Proceeding to Clock Out on Keka...")
            await execute_clock_out(chat_id, context)
        else:
            await update.message.reply_text("👍 Received 'Yes'! Proceeding to Clock In on Keka...")
            await execute_clock_in(chat_id, context)
        return

    # Status inquiries
    if any(phrase in raw_text for phrase in ["status", "hours", "check"]):
        await cmd_status(update, context)
        return

    # Session health check phrases
    if any(phrase in raw_text for phrase in ["session", "token", "expiry", "login health"]):
        await cmd_session(update, context)
        return

    # Friendly fallback
    await update.message.reply_text(
        "👋 I am your Keka Attendance Assistant.\n\n"
        "You can tap the buttons on my messages, or reply directly with:\n"
        "• *'clock in'* or *'yes'* to clock in immediately\n"
        "• *'do it after 10 min'* to auto clock-in in 10 minutes\n"
        "• *'clock out'* to clock out\n"
        "• *'session'* to check login token health\n"
        "• *'status'* to see your hours and screenshot",
        parse_mode="Markdown"
    )

# --- APPLICATION INITIALIZATION ---

def main():
    if not config.TELEGRAM_BOT_TOKEN:
        print("\n[ERROR] TELEGRAM_BOT_TOKEN is not set in your .env file!")
        print("Please edit .env and insert your Bot Token from @BotFather on Telegram.")
        return

    print("=" * 65)
    print("  KEKA ATTENDANCE TELEGRAM BOT - STARTING")
    print("=" * 65)
    print(f"• Keka URL: {config.KEKA_URL}")
    print(f"• Morning Daily Alert: {config.MORNING_REMINDER_TIME} (Mon-Fri)")
    print(f"• Dynamic Shift Length: {config.SHIFT_DURATION_MINUTES} minutes")
    print(f"• Timezone: {config.TIMEZONE}")
    print(f"• Headless: {config.HEADLESS}")
    print("=" * 65)

    # Initialize Telegram Application
    app = ApplicationBuilder().token(config.TELEGRAM_BOT_TOKEN).build()

    # Register Command Handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("session", cmd_session))
    app.add_handler(CommandHandler("clockin", cmd_clockin))
    app.add_handler(CommandHandler("clockout", cmd_clockout))
    app.add_handler(CommandHandler("help", cmd_help))

    # Register Inline Button Callback Handler
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Register Document / File Upload Handler (for dropping state.json directly into Telegram)
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Register Natural Text Message Handler (e.g. user replies 'yes', 'clock in', 'clock out')
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_text))

    # Schedule daily keepalive and session expiry checks at 11:00 AM and 19:00 PM
    try:
        app.job_queue.run_daily(
            daily_keepalive_and_expiry_job,
            time=dtime(hour=11, minute=0, tzinfo=LOCAL_TZ),
            name="daily_keepalive_morning"
        )
        app.job_queue.run_daily(
            daily_keepalive_and_expiry_job,
            time=dtime(hour=19, minute=0, tzinfo=LOCAL_TZ),
            name="daily_keepalive_evening"
        )
        # Also run a background check 20 seconds after startup to verify session health
        app.job_queue.run_once(
            daily_keepalive_and_expiry_job,
            when=20,
            name="startup_session_check"
        )
        print(f"[Scheduler] Daily keepalive & expiry checks scheduled at 11:00 and 19:00 {config.TIMEZONE}")
    except Exception as e:
        logger.error(f"Failed to schedule keepalive jobs: {e}")

    # Parse morning reminder time (HH:MM)
    try:
        hour, minute = map(int, config.MORNING_REMINDER_TIME.split(":"))
        morning_time = dtime(hour=hour, minute=minute, tzinfo=LOCAL_TZ)
        app.job_queue.run_daily(
            send_morning_prompt_job,
            time=morning_time,
            days=(0, 1, 2, 3, 4),  # Monday through Friday
            name="daily_morning_reminder"
        )
        print(f"[Scheduler] Daily morning reminder scheduled for {config.MORNING_REMINDER_TIME} {config.TIMEZONE}")
    except Exception as e:
        logger.error(f"Failed to schedule morning reminder: {e}")

    # Resume any active dynamic shift from today if bot was restarted
    today_state = state_mgr.get_today_state()
    if today_state.get("is_clocked_in") and not today_state.get("is_clocked_out"):
        target_iso = today_state.get("target_clock_out_iso")
        if target_iso:
            try:
                target_dt = datetime.fromisoformat(target_iso)
                now_dt = datetime.now(LOCAL_TZ)
                remaining_seconds = (target_dt - now_dt).total_seconds()
                if config.TELEGRAM_CHAT_ID:
                    if remaining_seconds > 0:
                        app.job_queue.run_once(
                            send_evening_prompt_job,
                            when=remaining_seconds,
                            chat_id=int(config.TELEGRAM_CHAT_ID),
                            name="dynamic_evening_reminder",
                            data={"clock_in_time": today_state.get("clock_in_formatted")}
                        )
                        print(f"[Scheduler] Resumed dynamic shift timer! Next alert in {int(remaining_seconds // 60)} minutes (at {target_dt.strftime('%I:%M %p')}).")
                    else:
                        app.job_queue.run_once(
                            send_evening_prompt_job,
                            when=1,
                            chat_id=int(config.TELEGRAM_CHAT_ID),
                            name="dynamic_evening_reminder",
                            data={"clock_in_time": today_state.get("clock_in_formatted")}
                        )
                        print("[Scheduler] Active shift has already passed 9 hours! Alerting immediately.")
            except Exception as ex:
                logger.warning(f"Could not resume dynamic shift: {ex}")

    print("\n[SUCCESS] Bot is now polling for Telegram commands...")
    print("Press Ctrl+C to stop.\n")
    app.run_polling()

if __name__ == "__main__":
    main()
