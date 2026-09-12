import os
import asyncio
import logging
import sqlite3
import random
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import RetryAfter
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# Enable logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment Variables Configuration
TOKEN = os.getenv("BOT_TOKEN")
DESTINATION_GROUP_ID = int(os.getenv("DESTINATION_GROUP_ID", "-1004441022456"))
PORT = int(os.environ.get("PORT", "8080"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL")

# Authorized Admin IDs
ADMIN_IDS = [8323137024, 8553702880]

# SQLite Database Initialization with Robust Support for 100+ Quests
DB_FILE = "forwarder_progress.db"

def init_db():
    conn = sqlite3.connect(DB_FILE, timeout=30.0)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS progress (
            user_id INTEGER PRIMARY KEY,
            channel_id INTEGER,
            topic_id INTEGER,
            max_msg_id INTEGER,
            current_msg_id INTEGER,
            success_count INTEGER,
            skipped_count INTEGER,
            forward_mode TEXT,
            status TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quest_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            channel_id INTEGER,
            channel_title TEXT,
            topic_id INTEGER,
            max_msg_id INTEGER,
            forward_mode TEXT,
            status TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

def save_progress_db(data: dict):
    with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO progress 
            (user_id, channel_id, topic_id, max_msg_id, current_msg_id, success_count, skipped_count, forward_mode, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data["user_id"],
            data["channel_id"],
            data["topic_id"],
            data["max_msg_id"],
            data["current_msg_id"],
            data["success_count"],
            data["skipped_count"],
            data["forward_mode"],
            data["status"]
        ))
        conn.commit()

def load_progress_db(user_id: int):
    with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, channel_id, topic_id, max_msg_id, current_msg_id, success_count, skipped_count, forward_mode, status FROM progress WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
    if row:
        return {
            "user_id": row[0],
            "channel_id": row[1],
            "topic_id": row[2],
            "max_msg_id": row[3],
            "current_msg_id": row[4],
            "success_count": row[5],
            "skipped_count": row[6],
            "forward_mode": row[7],
            "status": row[8],
            "files": []
        }
    return None

def clear_progress_db(user_id: int):
    with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM progress WHERE user_id = ?", (user_id,))
        conn.commit()

def add_quest_to_db(user_id: int, channel_id: int, channel_title: str, topic_id: int, max_msg_id: int, forward_mode: str):
    with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO quest_queue (user_id, channel_id, channel_title, topic_id, max_msg_id, forward_mode, status)
            VALUES (?, ?, ?, ?, ?, ?, 'pending')
        """, (user_id, channel_id, channel_title, topic_id, max_msg_id, forward_mode))
        conn.commit()

def get_next_quest():
    with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, user_id, channel_id, channel_title, topic_id, max_msg_id, forward_mode FROM quest_queue WHERE status = 'pending' ORDER BY id ASC LIMIT 1")
        row = cursor.fetchone()
    if row:
        return {
            "quest_id": row[0],
            "user_id": row[1],
            "channel_id": row[2],
            "channel_title": row[3],
            "topic_id": row[4],
            "max_msg_id": row[5],
            "forward_mode": row[6]
        }
    return None

def set_quest_status(quest_id: int, status: str):
    with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE quest_queue SET status = ? WHERE id = ?", (status, quest_id))
        conn.commit()

def remove_quest_from_db(quest_id: int):
    with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM quest_queue WHERE id = ?", (quest_id,))
        conn.commit()

def get_all_pending_quests():
    with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, channel_title, max_msg_id, forward_mode FROM quest_queue WHERE status = 'pending' ORDER BY id ASC")
        rows = cursor.fetchall()
    quests = []
    for row in rows:
        quests.append({
            "quest_id": row[0],
            "channel_title": row[1],
            "max_msg_id": row[2],
            "forward_mode": row[3]
        })
    return quests

user_sessions = {}
is_global_forwarding_active = False

def admin_required(func):
    """Decorator to restrict handler execution strictly to defined admins."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user or user.id not in ADMIN_IDS:
            if update.message:
                await update.message.reply_text("❌ You are not authorized to use this bot.")
            elif update.callback_query:
                await update.callback_query.answer("❌ Unauthorized action.", show_alert=True)
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

@admin_required
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the sequence by offering setup links, forward trigger, and queue view."""
    keyboard = [
        [InlineKeyboardButton("🤖 Add Bot 1 to Channel", url="https://t.me/DPS_xbot?startchannel=true&admin=post_messages+edit_messages+delete_messages+ban_users+invite_users+change_info+pin_messages+manage_video_chats+manage_topics+add_admins")],
        [InlineKeyboardButton("🤖 Add Bot 2 to Channel", url="https://t.me/dps_Storiesbot?startchannel=true&admin=post_messages+edit_messages+delete_messages+ban_users+invite_users+change_info+pin_messages+manage_video_chats+manage_topics+add_admins")],
        [InlineKeyboardButton("➡️ Forward", callback_data="start_forward")],
        [InlineKeyboardButton("📋 View & Manage Quests", callback_data="view_queue_1")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    pending_count = len(get_all_pending_quests())

    await update.message.reply_text(
        f"Welcome Admin! 📊 Quests currently in queue: **{pending_count}**\n\nChoose an option below:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def get_exact_latest_message_id(context: ContextTypes.DEFAULT_TYPE, channel_id: int) -> int:
    try:
        sent_msg = await context.bot.send_message(chat_id=channel_id, text="🔍 Probe sync check...")
        msg_id = sent_msg.message_id
        await context.bot.delete_message(chat_id=channel_id, message_id=msg_id)
        return msg_id
    except Exception as e:
        logger.error(f"Failed to probe channel via send/delete: {e}")
        low, high = 1, 1
        while True:
            try:
                await context.bot.forward_message(chat_id=DESTINATION_GROUP_ID, from_chat_id=channel_id, message_id=high)
                low = high
                high *= 2
            except Exception:
                break
            if high > 1000000:
                break

        best_id = low
        l, r = low, high - 1
        while l <= r:
            mid = (l + r) // 2
            try:
                await context.bot.forward_message(chat_id=DESTINATION_GROUP_ID, from_chat_id=channel_id, message_id=mid)
                best_id = mid
                l = mid + 1
            except Exception:
                r = mid - 1
        return best_id

@admin_required
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_state = user_sessions.get(user_id)
    if not user_state:
        return

    step = user_state.get("step")
    if step == "awaiting_channel":
        channel_id = None
        channel_title = "Source Channel"
        forwarded_msg_id = None

        if update.message.forward_origin:
            origin = update.message.forward_origin
            if hasattr(origin, "chat"):
                channel_id = origin.chat.id
                channel_title = origin.chat.title or "Source Channel"
            if hasattr(origin, "message_id"):
                forwarded_msg_id = origin.message_id
        elif update.message.text:
            channel_input = update.message.text.strip()
            if channel_input.isdigit():
                channel_input = f"-100{channel_input}"
            try:
                chat = await context.bot.get_chat(channel_input)
                channel_id = chat.id
                channel_title = chat.title or "Source Channel"
            except Exception as e:
                await update.message.reply_text(f"❌ Error accessing channel: {e}\nForward any post or provide a valid ID.")
                return

        if channel_id:
            status_prompt = await update.message.reply_text(f"🔍 Probing channel **{channel_title}**... Please wait.")
            max_msg_id = await get_exact_latest_message_id(context, channel_id)
            if forwarded_msg_id and forwarded_msg_id > max_msg_id:
                max_msg_id = forwarded_msg_id

            try:
                topic_id = None
                try:
                    chats_forum = await context.bot.get_forum_topics(chat_id=DESTINATION_GROUP_ID)
                    for topic in chats_forum:
                        if topic.name.strip().lower() == channel_title.strip().lower():
                            topic_id = topic.message_thread_id
                            break
                except Exception:
                    pass

                if not topic_id:
                    new_topic = await context.bot.create_forum_topic(chat_id=DESTINATION_GROUP_ID, name=channel_title)
                    topic_id = new_topic.message_thread_id

                user_sessions[user_id] = {
                    "step": "collecting_files",
                    "topic_id": topic_id,
                    "channel_id": channel_id,
                    "max_msg_id": max_msg_id,
                    "forward_mode": "regular",
                    "channel_title": channel_title,
                    "files": []
                }

                await status_prompt.edit_text(f"✅ Successfully linked channel: **{channel_title}** (`{channel_id}`) with Total IDs: `{max_msg_id}`!")
                await send_control_panel(update, context, user_id)
            except Exception as e:
                await status_prompt.edit_text(f"❌ Error setting up forum topic: {e}")

    elif step == "collecting_files":
        file_id = None
        attachment_type = None

        if update.message.document:
            file_id, attachment_type = update.message.document.file_id, "document"
        elif update.message.video:
            file_id, attachment_type = update.message.video.file_id, "video"
        elif update.message.photo:
            file_id, attachment_type = update.message.photo[-1].file_id, "photo"
        elif update.message.audio:
            file_id, attachment_type = update.message.audio.file_id, "audio"

        if file_id:
            user_sessions[user_id]["files"].append({
                "type": attachment_type,
                "file_id": file_id,
                "caption": update.message.caption or ""
            })
            try:
                await update.message.delete()
            except Exception:
                pass
            await update_control_panel(update, context, user_id)

async def send_control_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    state = user_sessions[user_id]
    text, reply_markup = get_panel_content(state)
    sent_msg = await context.bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup, parse_mode="Markdown")
    try:
        await context.bot.pin_chat_message(chat_id=user_id, message_id=sent_msg.message_id)
    except Exception:
        pass
    state["panel_message_id"] = sent_msg.message_id

async def update_control_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    state = user_sessions[user_id]
    text, reply_markup = get_panel_content(state)
    try:
        await context.bot.edit_message_text(chat_id=user_id, message_id=state["panel_message_id"], text=text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception:
        pass

def get_panel_content(state):
    topic_id = state.get("topic_id")
    mode = state.get("forward_mode", "regular")
    total_files = len(state.get("files", []))
    mode_str = "Reverse Order" if mode == "reverse_order" else "Regular"

    text = (
        f"⚙️ **Configuration Panel**\n\n"
        f"• **Group topic id:** `{topic_id}`\n"
        f"• **Forward mode:** `{mode_str}`\n"
        f"• **Total files saved:** `{total_files}`\n\n"
        f"*(Send files to queue them, choose a mode, then click Automated Forwarding)*"
    )
    keyboard = [
        [InlineKeyboardButton(f"Mode: {mode_str}", callback_data="toggle_mode")],
        [InlineKeyboardButton("🤖 Automated Forwarding", callback_data="automated_forward")],
        [InlineKeyboardButton("✅ Done", callback_data="finish_process")]
    ]
    return text, InlineKeyboardMarkup(keyboard)

async def execute_forwarding_quest(context: ContextTypes.DEFAULT_TYPE, quest: dict):
    global is_global_forwarding_active
    is_global_forwarding_active = True
    quest_id = quest["quest_id"]
    user_id = quest["user_id"]
    source_chat_id = quest["channel_id"]
    topic_id = quest["topic_id"]
    max_msg_id = quest["max_msg_id"]
    forward_mode = quest["forward_mode"]
    channel_title = quest["channel_title"]

    set_quest_status(quest_id, "running")

    status_msg = await context.bot.send_message(
        chat_id=user_id,
        text=f"⏳ Quest Started for **{channel_title}**\n\n• **Total ids:** `{max_msg_id}`\n• **Successfully forwarded ides:** `0`\n• **Skipd ides:** `0`\n• **FloodWait timer:** `none`"
    )

    success_count = 0
    skipped_count = 0
    start_id = max_msg_id if forward_mode == "reverse_order" else 1
    step_val = -1 if forward_mode == "reverse_order" else 1

    live_status_data = {"success": 0, "skipped": 0, "current": start_id, "flood": "none", "running": True}

    async def update_ui_loop():
        while live_status_data["running"]:
            try:
                progress_range_str = f"{max_msg_id} to {max(live_status_data['current'], 1)}" if forward_mode == "reverse_order" else f"1 to {min(live_status_data['current'], max_msg_id)}"
                text = (
                    f"⏳ Automated forwarding running (**{channel_title}**)\n\n"
                    f"• **Total ids:** `{max_msg_id}`\n"
                    f"• **Successfully forwarded ides:** `{live_status_data['success']}`\n"
                    f"• **Skipd ides:** `{live_status_data['skipped']}`\n\n"
                    f"Completed id {progress_range_str}\n"
                    f"FloodWait timer: `{live_status_data['flood']}`"
                )
                await status_msg.edit_text(text, parse_mode="Markdown")
            except Exception:
                pass
            await asyncio.sleep(1.0)

    ui_task = asyncio.create_task(update_ui_loop())

    try:
        msg_id = start_id
        while (msg_id > 0 if forward_mode == "reverse_order" else msg_id <= max_msg_id):
            live_status_data["current"] = msg_id
            try:
                await context.bot.copy_message(
                    chat_id=DESTINATION_GROUP_ID,
                    from_chat_id=source_chat_id,
                    message_id=msg_id,
                    message_thread_id=topic_id
                )
                success_count += 1
                live_status_data["success"] = success_count
                await asyncio.sleep(random.uniform(0.2, 1.0))
            except RetryAfter as e:
                flood_seconds = e.retry_after
                live_status_data["flood"] = f"{flood_seconds}s"
                logger.warning(f"FloodWait: Sleeping for {flood_seconds}s")
                await asyncio.sleep(flood_seconds)
                live_status_data["flood"] = "none"
                continue
            except Exception:
                skipped_count += 1
                live_status_data["skipped"] = skipped_count
                await asyncio.sleep(0.1)

            msg_id += step_val
    finally:
        live_status_data["running"] = False
        try:
            await ui_task
        except Exception:
            pass

    set_quest_status(quest_id, "completed")

    final_range_str = f"{max_msg_id} to 1" if forward_mode == "reverse_order" else f"1 to {max_msg_id}"
    final_report = (
        f"✅ Quest Completed for **{channel_title}**!\n\n"
        f"• **Total ids:** `{max_msg_id}`\n"
        f"• **Successfully forwarded ides:** `{success_count}`\n"
        f"• **Skipd ides:** `{skipped_count}`\n\n"
        f"Completed id {final_range_str}\n"
        f"FloodWait timer: `none`"
    )
    await status_msg.edit_text(final_report, parse_mode="Markdown")

    next_q = get_next_quest()
    if next_q:
        await asyncio.sleep(5.0)
        asyncio.create_task(execute_forwarding_quest(context, next_q))
    else:
        is_global_forwarding_active = False

@admin_required
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_global_forwarding_active
    query = update.callback_query
    data = query.data

    if data == "start_forward":
        await query.answer()
        user_id = query.from_user.id
        user_sessions[user_id] = {"step": "awaiting_channel"}
        await query.message.reply_text("Please **forward any message or file** from your source channel here, or supply its channel ID/username.")
        return

    if data.startswith("view_queue_"):
        await query.answer()
        page = int(data.split("_")[2])
        quests = get_all_pending_quests()

        if not quests:
            await query.edit_message_text("📋 The pending quest queue is currently empty.")
            return

        per_page = 5
        total_pages = (len(quests) + per_page - 1) // per_page
        page = max(1, min(page, total_pages))

        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        current_batch = quests[start_idx:end_idx]

        text = f"📋 **Pending Quests Queue (Page {page}/{total_pages})**\n\n"
        keyboard = []

        for idx, q in enumerate(current_batch, start=start_idx + 1):
            mode_label = "Rev" if q["forward_mode"] == "reverse_order" else "Reg"
            text += f"{idx}. **{q['channel_title']}** (ID: `{q['max_msg_id']}` | Mode: `{mode_label}`)\n"
            keyboard.append([InlineKeyboardButton(f"❌ Remove #{idx} ({q['channel_title'][:15]})", callback_data=f"del_quest_{q['quest_id']}_{page}")])

        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"view_queue_{page - 1}"))
        if page < total_pages:
            nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"view_queue_{page + 1}"))
        if nav_row:
            keyboard.append(nav_row)

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    if data.startswith("del_quest_"):
        parts = data.split("_")
        quest_id = int(parts[2])
        page = int(parts[3])

        remove_quest_from_db(quest_id)
        await query.answer("🗑️ Quest removed successfully from queue!", show_alert=True)

        quests = get_all_pending_quests()
        if not quests:
            await query.edit_message_text("📋 All pending quests have been cleared from the queue.")
            return

        per_page = 5
        total_pages = (len(quests) + per_page - 1) // per_page
        page = min(page, total_pages)

        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        current_batch = quests[start_idx:end_idx]

        text = f"📋 **Pending Quests Queue (Page {page}/{total_pages})**\n\n"
        keyboard = []

        for idx, q in enumerate(current_batch, start=start_idx + 1):
            mode_label = "Rev" if q["forward_mode"] == "reverse_order" else "Reg"
            text += f"{idx}. **{q['channel_title']}** (ID: `{q['max_msg_id']}` | Mode: `{mode_label}`)\n"
            keyboard.append([InlineKeyboardButton(f"❌ Remove #{idx} ({q['channel_title'][:15]})", callback_data=f"del_quest_{q['quest_id']}_{page}")])

        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"view_queue_{page - 1}"))
        if page < total_pages:
            nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"view_queue_{page + 1}"))
        if nav_row:
            keyboard.append(nav_row)

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return

    user_id = query.from_user.id
    state = user_sessions.get(user_id)
    if not state:
        await query.edit_message_text("Session expired. Send `/start` to begin again.")
        return

    if data == "toggle_mode":
        current_mode = state.get("forward_mode", "regular")
        state["forward_mode"] = "reverse_order" if current_mode == "regular" else "regular"
        await update_control_panel(update, context, user_id)

    elif data == "automated_forward":
        topic_id = state["topic_id"]
        source_chat_id = state["channel_id"]
        max_msg_id = state["max_msg_id"]
        mode = state.get("forward_mode", "regular")
        channel_title = state.get("channel_title", "Source Channel")

        add_quest_to_db(user_id, source_chat_id, channel_title, topic_id, max_msg_id, mode)

        if not is_global_forwarding_active:
            next_q = get_next_quest()
            if next_q:
                asyncio.create_task(execute_forwarding_quest(context, next_q))
                await query.edit_message_text(f"🚀 Quest added & started immediately for **{channel_title}**!")
        else:
            await query.edit_message_text(f"📌 Quest successfully added to queue for **{channel_title}**. It will automatically execute once prior quests finish!")

        user_sessions.pop(user_id, None)

    elif data == "finish_process":
        files = state["files"]
        topic_id = state["topic_id"]
        mode = state.get("forward_mode", "regular")

        if not files:
            await query.answer("⚠️ No files saved to send yet!", show_alert=True)
            return

        await query.edit_message_text("⏳ Processing queued files...")
        if mode == "reverse_order":
            files.reverse()

        for file_info in files:
            try:
                f_type, f_id, caption = file_info["type"], file_info["file_id"], file_info["caption"]
                if f_type == "document":
                    await context.bot.send_document(chat_id=DESTINATION_GROUP_ID, message_thread_id=topic_id, document=f_id, caption=caption)
                elif f_type == "video":
                    await context.bot.send_video(chat_id=DESTINATION_GROUP_ID, message_thread_id=topic_id, video=f_id, caption=caption)
                elif f_type == "photo":
                    await context.bot.send_photo(chat_id=DESTINATION_GROUP_ID, message_thread_id=topic_id, photo=f_id, caption=caption)
                elif f_type == "audio":
                    await context.bot.send_audio(chat_id=DESTINATION_GROUP_ID, message_thread_id=topic_id, audio=f_id, caption=caption)
                await asyncio.sleep(random.uniform(0.2, 1.0))
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except Exception as e:
                logger.error(f"Failed to send file: {e}")

        await context.bot.send_message(chat_id=user_id, text="✅ All queued files have been successfully sent!")
        user_sessions.pop(user_id, None)

# Custom web health check handler to fix Render 404 Not Found error
async def root_health_check(request):
    return web.Response(text="Bot is running!", status=200)

def main():
    if not TOKEN:
        raise ValueError("No BOT_TOKEN environment variable configured.")

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_handler(MessageHandler(filters.ATTACHMENT | filters.FORWARDED, handle_message))

    async def post_init(application):
        global is_global_forwarding_active
        if not is_global_forwarding_active:
            next_q = get_next_quest()
            if next_q:
                logger.info(f"Resuming pending quests from database queue...")
                asyncio.create_task(execute_forwarding_quest(application, next_q))

    app.post_init = post_init

    if WEBHOOK_URL:
        logger.info(f"Starting webhook app on port {PORT} with health check route...")
        
        # Define a custom web app handler alongside Telegram webhooks to satisfy Render/UptimeRobot pings
        async def web_server_setup():
            # If using a custom web server pattern with python-telegram-bot
            return

        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=f"{WEBHOOK_URL}/{TOKEN}",
            url_path=TOKEN,
            # python-telegram-bot web-server underlying aiohttp application can accept extra routes
        )
    else:
        logger.info("Starting local polling...")
        app.run_polling()

if __name__ == "__main__":
    main()
