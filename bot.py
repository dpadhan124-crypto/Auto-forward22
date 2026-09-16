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
DEFAULT_DESTINATION_GROUP_ID = int(os.getenv("DESTINATION_GROUP_ID", "-1004470555189"))
DEFAULT_BOT1_USERNAME = os.getenv("BOT1_USERNAME", "Dps_xbot")
DEFAULT_BOT2_USERNAME = os.getenv("BOT2_USERNAME", "dps_Storiesbot")

PORT = int(os.environ.get("PORT", "8080"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL")
ADMIN_IDS = [8323137024, 8553702880]
DB_FILE = "forwarder_progress.db"

# --- Database Initialization & Operations ---
def init_db():
    with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS quest_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                channel_id INTEGER,
                channel_title TEXT,
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
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.commit()

init_db()

def get_setting(key: str, default_val: str) -> str:
    with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM bot_settings WHERE key = ?", (key,))
        row = cursor.fetchone()
    return row[0] if row else default_val

def set_setting(key: str, value: str):
    with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()

def add_quest_to_db(user_id: int, channel_id: int, channel_title: str, max_msg_id: int, forward_mode: str):
    with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
        cursor = conn.cursor()
        start_msg = max_msg_id if forward_mode == "reverse_order" else 1
        cursor.execute("""
            INSERT INTO quest_queue (user_id, channel_id, channel_title, topic_id, max_msg_id, current_msg_id, success_count, skipped_count, forward_mode, status)
            VALUES (?, ?, ?, NULL, ?, ?, 0, 0, ?, 'pending')
        """, (user_id, channel_id, channel_title, max_msg_id, start_msg, forward_mode))
        conn.commit()

def get_next_quest():
    with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM quest_queue WHERE status IN ('pending', 'paused') ORDER BY id ASC LIMIT 1")
        row = cursor.fetchone()
    if row:
        return {"quest_id": row[0], "user_id": row[1], "channel_id": row[2], "channel_title": row[3], "topic_id": row[4],
                "max_msg_id": row[5], "current_msg_id": row[6], "success_count": row[7], "skipped_count": row[8],
                "forward_mode": row[9], "status": row[10]}
    return None

def get_active_running_quest():
    with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM quest_queue WHERE status = 'running' LIMIT 1")
        row = cursor.fetchone()
    if row:
        return {"quest_id": row[0]}
    return None

def update_quest_progress(quest_id, current_msg_id, success_count, skipped_count, status, topic_id=None):
    with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
        cursor = conn.cursor()
        if topic_id:
            cursor.execute("UPDATE quest_queue SET current_msg_id=?, success_count=?, skipped_count=?, status=?, topic_id=? WHERE id=?", 
                           (current_msg_id, success_count, skipped_count, status, topic_id, quest_id))
        else:
            cursor.execute("UPDATE quest_queue SET current_msg_id=?, success_count=?, skipped_count=?, status=? WHERE id=?", 
                           (current_msg_id, success_count, skipped_count, status, quest_id))
        conn.commit()

def set_quest_status(quest_id: int, status: str):
    with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
        conn.execute("UPDATE quest_queue SET status = ? WHERE id = ?", (status, quest_id))
        conn.commit()

def remove_quest_from_db(quest_id: int):
    with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
        conn.execute("DELETE FROM quest_queue WHERE id = ?", (quest_id,))
        conn.commit()

def clear_entire_database():
    with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
        conn.execute("DELETE FROM quest_queue")
        conn.execute("DELETE FROM bot_settings")
        conn.execute("DELETE FROM sqlite_sequence WHERE name='quest_queue'")
        conn.commit()

def get_all_quests():
    with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, channel_title, max_msg_id, forward_mode, status FROM quest_queue ORDER BY id ASC")
        rows = cursor.fetchall()
    return [{"quest_id": r[0], "channel_title": r[1], "max_msg_id": r[2], "forward_mode": r[3], "status": r[4]} for r in rows]

# --- Bot State ---
user_sessions = {}
active_quest_task = None

def admin_required(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user or user.id not in ADMIN_IDS:
            if update.message: await update.message.reply_text("❌ <b>Unauthorized access.</b>", parse_mode="HTML")
            elif update.callback_query: await update.callback_query.answer("❌ Unauthorized action.", show_alert=True)
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

@admin_required
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_main_menu(update.message.reply_text)

async def send_main_menu(reply_func, is_edit=False):
    b1, b2 = get_setting("bot1_username", DEFAULT_BOT1_USERNAME), get_setting("bot2_username", DEFAULT_BOT2_USERNAME)
    keyboard = [
        [InlineKeyboardButton("➡️ Forward Messages", callback_data="start_forward")],
        [InlineKeyboardButton("📋 View & Manage Quests", callback_data="view_queue_1")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="open_settings")],
        [
            InlineKeyboardButton("🤖 Add Bot 1", url=f"https://t.me/{b1}?startchannel=true&admin=post_messages+edit_messages+delete_messages+ban_users+invite_users+change_info+pin_messages+manage_video_chats+manage_topics+add_admins"),
            InlineKeyboardButton("🤖 Add Bot 2", url=f"https://t.me/{b2}?startchannel=true&admin=post_messages+edit_messages+delete_messages+ban_users+invite_users+change_info+pin_messages+manage_video_chats+manage_topics+add_admins")
        ]
    ]
    pending_count = len(get_all_quests())
    
    text = (
        "<b>✨ Welcome to the Automated Forwarding Bot!</b>\n\n"
        "I can help you bulk-forward messages from source channels directly to topics in your destination group seamlessly.\n\n"
        "<b>📖 Quick Start Guide:</b>\n"
        "1️⃣ Go to <b>⚙️ Settings</b> to set your Target Group ID.\n"
        "2️⃣ Make sure I am an <b>Admin</b> in all Source Channels.\n"
        "3️⃣ Tap <b>➡️ Forward Messages</b> and send your Channel IDs.\n\n"
        f"<blockquote>📊 <b>Quests in Queue:</b> <code>{pending_count}</code></blockquote>\n\n"
        "<i>Select an option below to get started:</i>"
    )
    
    if is_edit:
        await reply_func(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    else:
        await reply_func(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

# --- Core Forwarding Engine ---
async def execute_forwarding_quest(context: ContextTypes.DEFAULT_TYPE, quest: dict):
    global active_quest_task
    quest_id, user_id, source_chat_id = quest["quest_id"], quest["user_id"], quest["channel_id"]
    max_msg_id, forward_mode, channel_title = quest["max_msg_id"], quest["forward_mode"], quest["channel_title"]
    dest_group = int(get_setting("destination_group_id", str(DEFAULT_DESTINATION_GROUP_ID)))

    topic_id = quest.get("topic_id")
    if not topic_id:
        try:
            forum_topics = await context.bot.get_forum_topics(chat_id=dest_group)
            for t in forum_topics:
                if t.name.strip().lower() == channel_title.strip().lower():
                    topic_id = t.message_thread_id
                    break
        except Exception: pass
        if not topic_id:
            try:
                new_topic = await context.bot.create_forum_topic(chat_id=dest_group, name=channel_title)
                topic_id = new_topic.message_thread_id
            except Exception: topic_id = 1 

    set_quest_status(quest_id, "running")
    status_msg = await context.bot.send_message(
        chat_id=user_id,
        text=f"⏳ <b>Quest Started!</b>\n\nPreparing to forward messages for: <b>{channel_title}</b>",
        parse_mode="HTML"
    )

    success_count, skipped_count, msg_id = quest["success_count"], quest["skipped_count"], quest["current_msg_id"]
    step_val = -1 if forward_mode == "reverse_order" else 1
    live_status_data = {"running": True, "flood": "none"}

    async def update_ui_loop():
        while live_status_data["running"]:
            try:
                text = (
                    f"🔄 <b>Forwarding in Progress...</b>\n"
                    f"📁 <b>Channel:</b> {channel_title}\n\n"
                    f"<blockquote>"
                    f"• <b>Topic ID:</b> <code>{topic_id}</code>\n"
                    f"• <b>Total Messages:</b> <code>{max_msg_id}</code>\n"
                    f"• <b>✅ Forwarded:</b> <code>{success_count}</code>\n"
                    f"• <b>⏭️ Skipped/Deleted:</b> <code>{skipped_count}</code>\n"
                    f"• <b>⏱️ FloodWait:</b> <code>{live_status_data['flood']}</code>"
                    f"</blockquote>\n\n"
                    f"<i>Bot is working safely to avoid telegram limits.</i>"
                )
                await status_msg.edit_text(text, parse_mode="HTML")
            except Exception: pass
            await asyncio.sleep(2.0)

    ui_task = asyncio.create_task(update_ui_loop())

    try:
        while (msg_id > 0 if forward_mode == "reverse_order" else msg_id <= max_msg_id):
            with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
                r = conn.cursor().execute("SELECT status FROM quest_queue WHERE id = ?", (quest_id,)).fetchone()
            
            # Stop if deleted from database or paused
            if not r or r[0] != "running":
                live_status_data["running"] = False
                break

            try:
                await context.bot.copy_message(chat_id=dest_group, from_chat_id=source_chat_id, message_id=msg_id, message_thread_id=topic_id)
                success_count += 1
                update_quest_progress(quest_id, msg_id + step_val, success_count, skipped_count, "running", topic_id)
                await asyncio.sleep(random.uniform(0.3, 1.2))
            except RetryAfter as e:
                live_status_data["flood"] = f"{e.retry_after}s"
                await asyncio.sleep(e.retry_after)
                live_status_data["flood"] = "none"
                continue
            except Exception:
                skipped_count += 1
                update_quest_progress(quest_id, msg_id + step_val, success_count, skipped_count, "running", topic_id)
                await asyncio.sleep(0.1)
            msg_id += step_val
    finally:
        live_status_data["running"] = False
        try: await ui_task
        except: pass

    with sqlite3.connect(DB_FILE, timeout=30.0) as conn:
        row = conn.cursor().execute("SELECT status FROM quest_queue WHERE id = ?", (quest_id,)).fetchone()
        current_status = row[0] if row else "deleted"

    if current_status == "running":
        set_quest_status(quest_id, "completed")
        await status_msg.edit_text(
            f"✅ <b>Quest Completed Successfully!</b>\n\n"
            f"All readable messages from <b>{channel_title}</b> have been forwarded.", 
            parse_mode="HTML"
        )

    # Move to next quest
    next_q = get_next_quest()
    if next_q:
        await asyncio.sleep(2.0)
        active_quest_task = asyncio.create_task(execute_forwarding_quest(context, next_q))
    else:
        active_quest_task = None

# --- Message & UI Handlers ---
def parse_channel_inputs(text: str) -> list:
    raw_tokens = []
    for line in text.splitlines():
        for part in line.replace(",", " ").split():
            clean_part = part.strip()
            if clean_part: raw_tokens.append(clean_part)
    
    parsed_ids = []
    for token in raw_tokens:
        if token.startswith("@") or not token.lstrip("-").isdigit(): parsed_ids.append(token)
        else:
            num_val = int(token)
            if num_val > 0:
                if not token.startswith("-100"): parsed_ids.append(int(f"-100{token}"))
                else: parsed_ids.append(num_val)
            else: parsed_ids.append(num_val)
    return parsed_ids

@admin_required
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = user_sessions.get(user_id, {})
    step = state.get("step")
    
    if step in ["setting_dest_group", "setting_bot1", "setting_bot2"]:
        val = update.message.text.strip().lstrip("@")
        keys = {
            "setting_dest_group": "destination_group_id", 
            "setting_bot1": "bot1_username", 
            "setting_bot2": "bot2_username"
        }
        set_setting(keys[step], val)
        user_sessions.pop(user_id, None)
        await update.message.reply_text(f"✅ <b>Setting Updated Successfully!</b>\n\nNew Value: <code>{val}</code>", parse_mode="HTML")
        await send_main_menu(update.message.reply_text)
        return

    elif step == "awaiting_channel":
        channels = []
        if update.message.forward_origin and hasattr(update.message.forward_origin, "chat"):
            channels.append((update.message.forward_origin.chat.id, update.message.forward_origin.chat.title or "Source"))
        elif update.message.text:
            for ident in parse_channel_inputs(update.message.text): channels.append((ident, None))

        if not channels:
            return await update.message.reply_text("❌ No valid channels recognized. Please try sending the IDs again.")

        status = await update.message.reply_text(f"🔍 <b>Probing {len(channels)} channel(s)...</b>\n<i>Please wait while I check admin rights and fetch data.</i>", parse_mode="HTML")
        linked = []
        valid_channels = []
        
        for idx, (ident, title_pre) in enumerate(channels, 1):
            try:
                chat = await context.bot.get_chat(ident)
                channel_title = chat.title or title_pre or "Source"
                
                # Send a test message to get exact ID, then immediately delete it.
                try:
                    test_msg = await context.bot.send_message(chat_id=chat.id, text="🔍")
                    max_msg_id = test_msg.message_id
                    await test_msg.delete()
                except Exception as e:
                    logger.warning(f"Could not send probe message to {chat.id}, using fallback. Error: {e}")
                    max_msg_id = 10000  # Fallback just in case

                valid_channels.append({
                    "channel_id": chat.id,
                    "channel_title": channel_title,
                    "max_msg_id": max_msg_id
                })
                linked.append(f"✅ <b>{channel_title}</b>\n└ ID: <code>{chat.id}</code> | Msg Count: {max_msg_id}")
            except Exception as e: 
                logger.error(f"Failed to probe {ident}: {e}")
                linked.append(f"❌ <b>Failed:</b> <code>{ident}</code>\n└ <i>Ensure bot is Admin.</i>")

        if valid_channels:
            user_sessions[user_id] = {
                "step": "collecting_files", 
                "channels": valid_channels, 
                "forward_mode": "regular"
            }
            await status.edit_text("📋 <b>Channel Probe Results:</b>\n\n" + "\n\n".join(linked), parse_mode="HTML")
            await send_control_panel(update, context, user_id)
        else: 
            await status.edit_text("❌ <b>Failed to resolve any channels.</b>\nPlease make sure the bot is added to the channel as an Admin.", parse_mode="HTML")

async def send_control_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    st = user_sessions[user_id]
    mode_str = "Reverse Order (Newest First)" if st.get("forward_mode") == "reverse_order" else "Regular (Oldest First)"
    chan_count = len(st.get("channels", []))
    
    text = (
        "⚙️ <b>Forwarding Configuration Panel</b>\n\n"
        "You are about to queue these channels for automated forwarding. Review your settings below:\n\n"
        f"<blockquote>• <b>Channels Selected:</b> <code>{chan_count}</code>\n"
        f"• <b>Forward Mode:</b> <code>{mode_str}</code></blockquote>\n\n"
        "<i>Click 'Toggle Mode' to change the sorting, or 'Start' to push these quests into the queue!</i>"
    )
    kb = [[InlineKeyboardButton(f"🔁 Toggle Mode: {mode_str.split(' ')[0]}", callback_data="toggle_mode")],
          [InlineKeyboardButton("🚀 Start Automated Forwarding", callback_data="automated_forward")]]
    
    # Send a new panel message and store its ID if we need it later
    msg = await context.bot.send_message(chat_id=user_id, text=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
    st["panel_message_id"] = msg.message_id

@admin_required
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_quest_task
    query = update.callback_query
    data = query.data

    if data == "back_home":
        await query.answer()
        await send_main_menu(query.edit_message_text, is_edit=True)
    
    elif data == "open_settings":
        await query.answer()
        text = (
            "⚙️ <b>Bot Settings</b>\n\n"
            "Here you can configure the default IDs and bot usernames. "
            "Click a button below to edit its value.\n\n"
            "<i>Current Setup:</i>\n"
            f"• <b>Group ID:</b> <code>{get_setting('destination_group_id', str(DEFAULT_DESTINATION_GROUP_ID))}</code>"
        )
        kb = [[InlineKeyboardButton("✏️ Edit Group ID", callback_data="set_group_id")],
              [InlineKeyboardButton("✏️ Edit Bot 1 Name", callback_data="set_bot1_name"),
               InlineKeyboardButton("✏️ Edit Bot 2 Name", callback_data="set_bot2_name")],
              [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_home")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    elif data in ["set_group_id", "set_bot1_name", "set_bot2_name"]:
        await query.answer()
        step_map = {
            "set_group_id": "setting_dest_group",
            "set_bot1_name": "setting_bot1",
            "set_bot2_name": "setting_bot2"
        }
        user_sessions[query.from_user.id] = {"step": step_map[data]}
        await query.message.reply_text(
            "📥 <b>Send the new value for this setting:</b>\n\n"
            "<i>Tip: You can copy and paste the ID or username directly into the chat.</i>",
            parse_mode="HTML"
        )

    elif data == "start_forward":
        await query.answer()
        user_sessions[query.from_user.id] = {"step": "awaiting_channel"}
        await query.message.reply_text(
            "📁 <b>Forwarding Setup</b>\n\n"
            "Please send the <b>Channel ID(s)</b> you want to extract from, or simply <b>forward a message</b> from that channel to me.\n\n"
            "<i>Formats accepted:</i>\n"
            "• <code>-1001234567890</code>\n"
            "• <code>@MyPublicChannel</code>\n"
            "• <i>Multiple IDs separated by commas or new lines.</i>",
            parse_mode="HTML"
        )

    elif data == "toggle_mode":
        st = user_sessions.get(query.from_user.id)
        if st:
            st["forward_mode"] = "reverse_order" if st.get("forward_mode") == "regular" else "regular"
            # Delete the old panel to prevent clutter and send a fresh one
            await query.message.delete()
            await send_control_panel(update, context, query.from_user.id)

    elif data == "automated_forward":
        st = user_sessions.pop(query.from_user.id, None)
        if st and "channels" in st:
            # Add a quest for every valid channel fetched
            for ch in st["channels"]:
                add_quest_to_db(
                    query.from_user.id, 
                    ch["channel_id"], 
                    ch["channel_title"], 
                    ch["max_msg_id"], 
                    st["forward_mode"]
                )
                
            if not get_active_running_quest():
                active_quest_task = asyncio.create_task(execute_forwarding_quest(context, get_next_quest()))
                await query.edit_message_text(f"🚀 <b>Success!</b> Started {len(st['channels'])} Quest(s). Check the Main Menu for updates.", parse_mode="HTML")
            else:
                await query.edit_message_text(f"📌 <b>Success!</b> Added {len(st['channels'])} Quest(s) to the queue.", parse_mode="HTML")

    elif data.startswith("view_queue_"):
        await query.answer()
        page = int(data.split("_")[-1])
        quests = get_all_quests()
        if not quests:
            kb = [[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_home")]]
            return await query.edit_message_text("📋 <b>Queue is completely empty.</b>\n\nReady for new tasks!", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

        text = f"📋 <b>Quest Queue (Page {page})</b>\n\nReview your currently running and pending tasks:\n\n"
        kb = []
        
        items_per_page = 4
        total_pages = (len(quests) + items_per_page - 1) // items_per_page
        
        for i, q in enumerate(quests[(page-1)*items_per_page : page*items_per_page], start=(page-1)*items_per_page+1):
            
            # Map status to a nice emoji
            status_emoji = "▶️" if q['status'] == "running" else ("⏸️" if q['status'] == "paused" else "⏳")
            
            text += f"<blockquote><b>#{i}. {q['channel_title']}</b>\nStatus: {status_emoji} {q['status'].capitalize()}</blockquote>\n"
            
            # Format the channel title for the button so it doesn't get too long
            short_title = q['channel_title'][:15] + ("..." if len(q['channel_title']) > 15 else "")
            
            row = []
            if q["status"] == "running": row.append(InlineKeyboardButton("⏸️ Pause", callback_data=f"pause_{q['quest_id']}"))
            elif q["status"] == "paused": row.append(InlineKeyboardButton("▶️ Resume", callback_data=f"resume_{q['quest_id']}"))
            
            row.append(InlineKeyboardButton(f"❌ Cancel {short_title}", callback_data=f"del_{q['quest_id']}"))
            kb.append(row)
        
        # Add Pagination Buttons if there are multiple pages
        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"view_queue_{page-1}"))
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"view_queue_{page+1}"))
            
        if nav_buttons:
            kb.append(nav_buttons)
            
        kb.append([InlineKeyboardButton("🗑️ Clear Entire Database", callback_data="clear_all")])
        kb.append([InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_home")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

    elif data.startswith("del_") or data.startswith("pause_") or data.startswith("resume_"):
        action, q_id = data.split("_")[0], int(data.split("_")[1])
        if action == "del":
            remove_quest_from_db(q_id)
            await query.answer("❌ Quest Cancelled and Deleted!", show_alert=True)
        elif action == "pause":
            set_quest_status(q_id, "paused")
            await query.answer("⏸️ Quest Paused! It will wait until you resume it.", show_alert=True)
        elif action == "resume":
            set_quest_status(q_id, "pending")
            await query.answer("▶️ Quest Resumed! Added back to the active queue.", show_alert=True)
            if not get_active_running_quest():
                active_quest_task = asyncio.create_task(execute_forwarding_quest(context, get_next_quest()))
        
        await button_callback(update, context)

    elif data == "clear_all":
        clear_entire_database()
        
        if active_quest_task:
            active_quest_task.cancel()
            active_quest_task = None
            
        await query.answer("💥 All database records and active tasks cleared!", show_alert=True)
        kb = [[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_home")]]
        await query.edit_message_text("🧹 <b>Database Wiped Clean.</b>\n\nAll quests have been deleted and stopped.", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

# --- Web Server Health Check ---
async def root_health_check(request):
    return web.Response(text="200 OK - Bot is Alive & Running", status=200)

async def main_runner():
    if not TOKEN: raise ValueError("No BOT_TOKEN configured.")
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_handler(MessageHandler(filters.ATTACHMENT | filters.FORWARDED, handle_message))

    await app.initialize()

    # Resume pending quests on startup
    next_q = get_next_quest()
    if next_q and not get_active_running_quest():
        global active_quest_task
        active_quest_task = asyncio.create_task(execute_forwarding_quest(app, next_q))

    # Polling vs Webhook setup
    if WEBHOOK_URL:
        logger.info(f"Setting webhook to {WEBHOOK_URL}")
        await app.bot.set_webhook(url=f"{WEBHOOK_URL}/{TOKEN}")
        await app.start()
    else:
        logger.info("Starting local polling...")
        await app.updater.start_polling()
        await app.start()

    # Web Server for Render/UptimeRobot Pings
    web_app = web.Application()
    web_app.router.add_get("/", root_health_check)
    
    if WEBHOOK_URL:
        async def webhook_handler(request):
            update = Update.de_json(await request.json(), app.bot)
            await app.update_queue.put(update)
            return web.Response(status=200)
        web_app.router.add_post(f"/{TOKEN}", webhook_handler)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    
    logger.info(f"Web server listening on port {PORT}. Ready for pings.")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main_runner())
