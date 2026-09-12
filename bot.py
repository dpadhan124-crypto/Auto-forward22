import os
import asyncio
import logging
import sqlite3
import random
import threading
from flask import Flask, render_template_string, request, redirect, url_for, session as flask_session
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
WEBHOOK_URL = os.getenv("WEBHOOK_URL") or33 os.getenv("RENDER_EXTERNAL_URL")

# Authorized Admin IDs
ADMIN_IDS = [8323137024, 8553702880]

# SQLite Database Initialization with Persistent Quest Queue & Settings
DB_FILE = "forwarder_progress.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot1_username', 'Dps_Storiesbot')")
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('bot2_username', 'fm_Storiesbot')")
    conn.commit()
    conn.close()

init_db()

def get_setting(key: str, default: str = "") -> str:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else default

def set_setting(key: str, value: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()

def save_progress_db(data: dict):
    conn = sqlite3.connect(DB_FILE)
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
    conn.close()

def load_progress_db(user_id: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, channel_id, topic_id, max_msg_id, current_msg_id, success_count, skipped_count, forward_mode, status FROM progress WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
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
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM progress WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def add_quest_to_db(user_id: int, channel_id: int, channel_title: str, topic_id: int, max_msg_id: int, forward_mode: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO quest_queue (user_id, channel_id, channel_title, topic_id, max_msg_id, forward_mode, status)
        VALUES (?, ?, ?, ?, ?, ?, 'pending')
    """, (user_id, channel_id, channel_title, topic_id, max_msg_id, forward_mode))
    conn.commit()
    conn.close()

def get_next_quest():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, user_id, channel_id, channel_title, topic_id, max_msg_id, forward_mode FROM quest_queue WHERE status = 'pending' ORDER BY id ASC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
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
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE quest_queue SET status = ? WHERE id = ?", (status, quest_id))
    conn.commit()
    conn.close()

def reset_running_quests():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE quest_queue SET status = 'pending' WHERE status = 'running'")
    conn.commit()
    conn.close()

reset_running_quests()

user_sessions = {}
is_global_forwarding_active = False

# Flask Web Dashboard Initialization
flask_app = Flask(__name__)
flask_app.secret_key = os.urandom(24)

@flask_app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == "@dps":
            flask_session["logged_in"] = True
            return redirect(url_for("dashboard"))
        else:
            error = "Invalid password. Try again."
    
    return """
    <!DOCTYPE html>
    <html>
    <head><title>Login - Bot Dashboard</title>
    <style>
        body { font-family: Arial, sans-serif; background: #f4f7f6; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .login-box { background: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.1); width: 300px; text-align: center; }
        input[type="password"] { width: 90%; padding: 10px; margin: 15px 0; border: 1px solid #ddd; border-radius: 4px; }
        button { background: #007bff; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; width: 100%; }
        button:hover { background: #0056b3; }
        .error { color: red; font-size: 14px; }
    </style>
    </head>
    <body>
        <div class="login-box">
            <h2>🔒 Dashboard Login</h2>
            {% if error %}<p class="error">{{ error }}</p>{% endif %}
            <form method="POST">
                <input type="password" name="password" placeholder="Enter password" required autofocus>
                <button type="submit">Login</button>
            </form>
        </div>
    </body>
    </html>
    """

@flask_app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if not flask_session.get("logged_in"):
        return redirect(url_for("login"))

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    if request.method == "POST":
        action = request.form.get("action")
        if action == "update_settings":
            bot1 = request.form.get("bot1_username")
            bot2 = request.form.get("bot2_username")
            if bot1:
                set_setting("bot1_username", bot1.strip().lstrip("@"))
            if bot2:
                set_setting("bot2_username", bot2.strip().lstrip("@"))
        elif action == "delete_quest":
            qid = request.form.get("quest_id")
            cursor.execute("DELETE FROM quest_queue WHERE id = ?", (qid,))
            conn.commit()
        elif action == "clear_progress":
            cursor.execute("DELETE FROM progress")
            conn.commit()

    cursor.execute("SELECT id, user_id, channel_title, max_msg_id, forward_mode, status FROM quest_queue")
    quests = cursor.fetchall()

    cursor.execute("SELECT user_id, channel_id, topic_id, max_msg_id, current_msg_id, success_count, status FROM progress")
    progress_rows = cursor.fetchall()

    cursor.execute("SELECT key, value FROM settings")
    settings = cursor.fetchall()

    conn.close()

    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Bot Admin Dashboard</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 30px; background: #f4f7f6; color: #333; }
            h2 { color: #007bff; margin-top: 30px; }
            table { width: 100%; border-collapse: collapse; margin-bottom: 20px; background: #fff; box-shadow: 0 2px 5px rgba(0,0,0,0.1); border-radius: 5px; overflow: hidden; }
            th, td { padding: 12px; border: 1px solid #ddd; text-align: left; }
            th { background-color: #007bff; color: white; }
            tr:nth-child(even) { background-color: #f9f9f9; }
            .btn { background: #dc3545; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; }
            .btn:hover { background: #c82333; }
            .btn-primary { background: #28a745; }
            .btn-primary:hover { background: #218838; }
            input[type="text"] { padding: 6px; width: 250px; border: 1px solid #ddd; border-radius: 4px; }
            .card { background: white; padding: 20px; border-radius: 5px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); margin-bottom: 20px; }
            .logout { float: right; background: #6c757d; color: white; padding: 8px 15px; text-decoration: none; border-radius: 4px; }
        </style>
    </head>
    <body>
        <a href="/logout" class="logout">Logout</a>
        <h1>🤖 Telegram Bot Control & Inspector Dashboard</h1>
        
        <div class="card">
            <h2>⚙️ Bot Settings Manager</h2>
            <form method="POST">
                <input type="hidden" name="action" value="update_settings">
                {% for s in settings %}
                    <p><b>{{ s[0] }}:</b> <input type="text" name="{{ s[0] }}" value="{{ s[1] }}"></p>
                {% endfor %}
                <button type="submit" class="btn btn-primary">Save Settings</button>
            </form>
        </div>

        <h2>📋 Quest Queue Table (`quest_queue`)</h2>
        <table>
            <tr><th>ID</th><th>User ID</th><th>Channel Title</th><th>Max ID</th><th>Mode</th><th>Status</th><th>Action</th></tr>
            {% for q in quests %}
            <tr>
                <td>{{ q[0] }}</td><td>{{ q[1] }}</td><td>{{ q[2] }}</td><td>{{ q[3] }}</td><td>{{ q[4] }}</td><td><b>{{ q[5] }}</b></td>
                <td>
                    <form method="POST" style="margin:0;">
                        <input type="hidden" name="action" value="delete_quest">
                        <input type="hidden" name="quest_id" value="{{ q[0] }}">
                        <button type="submit" class="btn">Delete</button>
                    </form>
                </td>
            </tr>
            {% endfor %}
        </table>

        <h2>📊 Progress Table (`progress`)</h2>
        <form method="POST" style="margin-bottom: 10px;">
            <input type="hidden" name="action" value="clear_progress">
            <button type="submit" class="btn">Clear Progress Table</button>
        </form>
        <table>
            <tr><th>User ID</th><th>Channel ID</th><th>Topic ID</th><th>Max ID</th><th>Current ID</th><th>Success</th><th>Status</th></tr>
            {% for p in progress_rows %}
            <tr><td>{{ p[0] }}</td><td>{{ p[1] }}</td><td>{{ p[2] }}</td><td>{{ p[3] }}</td><td>{{ p[4] }}</td><td>{{ p[5] }}</td><td>{{ p[6] }}</td></tr>
            {% endfor %}
        </table>
    </body>
    </html>
    """
    return render_template_string(html_template, quests=quests, progress_rows=progress_rows, settings=settings)

@flask_app.route("/logout")
def logout():
    flask_session.pop("logged_in", None)
    return redirect(url_for("login"))

def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

def admin_required(func):
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
    bot1 = get_setting("bot1_username", "Dps_Storiesbot")
    bot2 = get_setting("bot2_username", "fm_Storiesbot")

    keyboard = [
        [InlineKeyboardButton("🤖 Add Bot 1 to Channel", url=f"https://t.me/{bot1}?startchannel=true&admin=post_messages+edit_messages+delete_messages+ban_users+invite_users+change_info+pin_messages+manage_video_chats+manage_topics+add_admins")],
        [InlineKeyboardButton("🤖 Add Bot 2 to Channel", url=f"https://t.me/{bot2}?startchannel=true&admin=post_messages+edit_messages+delete_messages+ban_users+invite_users+change_info+pin_messages+manage_video_chats+manage_topics+add_admins")],
        [InlineKeyboardButton("➡️ Forward", callback_data="start_forward")],
        [InlineKeyboardButton("📊 Database Inspector", callback_data="view_db")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Welcome Admin! Choose an option below to add bots, start forwarding, or inspect database tables.",
        reply_markup=reply_markup
    )

@admin_required
async def db_inspector_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    output = "📊 **Database Inspector (`forwarder_progress.db`)**\n\n"

    output += "**1. Table: `quest_queue`**\n```text\n"
    cursor.execute("SELECT id, user_id, channel_title, max_msg_id, forward_mode, status FROM quest_queue")
    rows = cursor.fetchall()
    if rows:
        output += f"{'ID':<4} | {'User ID':<10} | {'Channel':<15} | {'Max ID':<6} | {'Mode':<12} | {'Status':<10}\n"
        output += "-" * 65 + "\n"
        for r in rows:
            output += f"{r[0]:<4} | {r[1]:<10} | {str(r[2])[:15]:<15} | {r[3]:<6} | {r[4]:<12} | {r[5]:<10}\n"
    else:
        output += "No records found in quest_queue.\n"
    output += "```\n\n"

    output += "**2. Table: `progress`**\n```text\n"
    cursor.execute("SELECT user_id, channel_id, topic_id, max_msg_id, current_msg_id, success_count, status FROM progress")
    rows = cursor.fetchall()
    if rows:
        output += f"{'User ID':<10} | {'Topic':<6} | {'Current ID':<10} | {'Success':<8} | {'Status':<10}\n"
        output += "-" * 55 + "\n"
        for r in rows:
            output += f"{r[0]:<10} | {r[2]:<6} | {r[4]:<10} | {r[5]:<8} | {r[6]:<10}\n"
    else:
        output += "No active progress records found.\n"
    output += "```\n\n"

    output += "**3. Table: `settings`**\n```text\n"
    cursor.execute("SELECT key, value FROM settings")
    rows = cursor.fetchall()
    if rows:
        output += f"{'Key':<20} | {'Value':<20}\n"
        output += "-" * 45 + "\n"
        for r in rows:
            output += f"{r[0]:<20} | {r[1]:<20}\n"
    output += "```\n\n"

    conn.close()

    if update.callback_query:
        await update.callback_query.message.reply_text(output, parse_mode="Markdown")
        await update.callback_query.answer()
    else:
        await update.message.reply_text(output, parse_mode="Markdown")

@admin_required
async def set_bot_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    cmd = update.message.text.split()[0].lower()

    if not args:
        await update.message.reply_text("❌ Please provide a username. Example: `/setbot1 MyNewBot`", parse_mode="Markdown")
        return

    new_username = args[0].strip().lstrip("@")
    if "1" in cmd:
        set_setting("bot1_username", new_username)
        await update.message.reply_text(f"✅ Bot 1 username successfully updated to: `@{new_username}`", parse_mode="Markdown")
    elif "2" in cmd:
        set_setting("bot2_username", new_username)
        await update.message.reply_text(f"✅ Bot 2 username successfully updated to: `@{new_username}`", parse_mode="Markdown")

async def get_exact_latest_message_id(context: ContextTypes.DEFAULT_TYPE, channel_id: int) -> int:
    try:
        sent_msg = await context.bot.send_message(chat_id=channel_id, text="🔍 Probe sync check...")
        msg_id = sent_msg.message_id
        await context.bot.delete_message(chat_id=channel_id, message_id=msg_id)
        return msg_id
    except Exception as e:
        logger.error(f"Failed to probe channel via send/delete: {e}")
        low = 1
        high = 1
        while True:
            try:
                await context.bot.forward_message(
                    chat_id=DESTINATION_GROUP_ID,
                    from_chat_id=channel_id,
                    message_id=high
                )
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
                await context.bot.forward_message(
                    chat_id=DESTINATION_GROUP_ID,
                    from_chat_id=channel_id,
                    message_id=mid
                )
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
                await update.message.reply_text(f"❌ Error accessing channel: {e}\nForward any post directly from the channel or supply a valid ID/username.")
                return

        if channel_id:
            status_prompt = await update.message.reply_text(f"🔍 Probing channel **{channel_title}** to fetch exact real message count... Please wait.")
            
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
                    new_topic = await context.bot.create_forum_topic(
                        chat_id=DESTINATION_GROUP_ID,
                        name=channel_title
                    )
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

                await status_prompt.edit_text(f"✅ Successfully linked channel: **{channel_title}** (`{channel_id}`) with Exact Real Total IDs: `{max_msg_id}`!")
                await send_control_panel(update, context, user_id)
            except Exception as e:
                await status_prompt.edit_text(f"❌ Error setting up forum topic: {e}\nEnsure the bot has admin privileges to manage topics in the destination group.")

    elif step == "collecting_files":
        file_id = None
        attachment_type = None

        if update.message.document:
            file_id = update.message.document.file_id
            attachment_type = "document"
        elif update.message.video:
            file_id = update.message.video.file_id
            attachment_type = "video"
        elif update.message.photo:
            file_id = update.message.photo[-1].file_id
            attachment_type = "photo"
        elif update.message.audio:
            file_id = update.message.audio.file_id
            attachment_type = "audio"

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
    except Exception as e:
        logger.error(f"Could not pin message: {e}")

    state["panel_message_id"] = sent_msg.message_id

async def update_control_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    state = user_sessions[user_id]
    text, reply_markup = get_panel_content(state)
    try:
        await context.bot.edit_message_text(
            chat_id=user_id,
            message_id=state["panel_message_id"],
            text=text,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
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
        f"*(Send files to add them to the queue, choose a mode, then click Done)*"
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
                if forward_mode == "reverse_order":
                    progress_range_str = f"{max_msg_id} to {max(live_status_data['current'], 1)}"
                else:
                    progress_range_str = f"1 to {min(live_status_data['current'], max_msg_id)}"

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
            await asyncio.sleep(0.5)

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
                
                await asyncio.sleep(random.uniform(0.2, 10.0))
            except RetryAfter as e:
                flood_seconds = e.retry_after
                live_status_data["flood"] = f"{flood_seconds}s"
                logger.warning(f"FloodWait encountered: Sleeping for {flood_seconds} seconds.")
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
        await asyncio.sleep(15.0)
        asyncio.create_task(execute_forwarding_quest(context, next_q))
    else:
        is_global_forwarding_active = False

async def resume_pending_quests_on_startup(app):
    await asyncio.sleep(3)
    global is_global_forwarding_active
    if not is_global_forwarding_active:
        next_q = get_next_quest()
        if next_q:
            logger.info(f"Resuming pending quest #{next_q['quest_id']} for channel {next_q['channel_title']}")
            asyncio.create_task(execute_forwarding_quest(app.bot_data.get("context_holder"), next_q))

@admin_required
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global is_global_forwarding_active
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "view_db":
        await db_inspector_command(update, context)
        return

    if query.data == "start_forward":
        user_sessions[user_id] = {"step": "awaiting_channel"}
        await query.message.reply_text("Please **forward any message or file** directly from your source channel here, or send its channel ID/username.")
        return

    state = user_sessions.get(user_id)
    if not state:
        await query.edit_message_text("Session expired. Send `/start` to begin again.")
        return

    if query.data == "toggle_mode":
        current_mode = state.get("forward_mode", "regular")
        if current_mode == "regular":
            state["forward_mode"] = "reverse_order"
        else:
            state["forward_mode"] = "regular"
        await update_control_panel(update, context, user_id)

    elif query.data == "automated_forward":
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
            await query.edit_message_text(f"📌 Quest successfully added to queue for **{channel_title}**. It will automatically execute once the current active quest completes!")

        user_sessions.pop(user_id, None)

    elif query.data == "finish_process":
        files = state["files"]
        topic_id = state["topic_id"]
        mode = state.get("forward_mode", "regular")

        if not files:
            await query.answer("⚠️ No files saved to send yet! Please send files first or use Automated Forwarding.", show_alert=True)
            return

        await query.edit_message_text("⏳ Processing and dispatching queued files strictly one by one...")

        if mode == "reverse_order":
            files.reverse()

        for file_info in files:
            f_type = file_info["type"]
            f_id = file_info["file_id"]
            caption = file_info["caption"]

            try:
                if f_type == "document":
                    await context.bot.send_document(chat_id=DESTINATION_GROUP_ID, message_thread_id=topic_id, document=f_id, caption=caption)
                elif f_type == "video":
                    await context.bot.send_video(chat_id=DESTINATION_GROUP_ID, message_thread_id=topic_id, video=f_id, caption=caption)
                elif f_type == "photo":
                    await context.bot.send_photo(chat_id=DESTINATION_GROUP_ID, message_thread_id=topic_id, photo=f_id, caption=caption)
                elif f_type == "audio":
                    await context.bot.send_audio(chat_id=DESTINATION_GROUP_ID, message_thread_id=topic_id, audio=f_id, caption=caption)
                await asyncio.sleep(random.uniform(0.2, 10.0))
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except Exception as e:
                logger.error(f"Failed to send file: {e}")

        await context.bot.send_message(chat_id=user_id, text="✅ All queued files have been successfully sent to the destination topic!")
        user_sessions.pop(user_id, None)

def main():
    if not TOKEN:
        raise ValueError("No BOT_TOKEN environment variable configured.")

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # Start Flask Web Dashboard Server in a background thread
    threading.Thread(target=run_flask, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()
    
    app.bot_data["context_holder"] = type('Obj', (object,), {'bot': app.bot})()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("db", db_inspector_command))
    app.add_handler(CommandHandler("setbot1", set_bot_username))
    app.add_handler(CommandHandler("setbot2", set_bot_username))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_handler(MessageHandler(filters.ATTACHMENT | filters.FORWARDED, handle_message))

    loop.create_task(resume_pending_quests_on_startup(app))

    if WEBHOOK_URL:
        logger.info(f"Starting webhook server on port {PORT}...")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=f"{WEBHOOK_URL}/{TOKEN}",
            url_path=TOKEN
        )
    else:
        logger.info("Starting local polling...")
        app.run_polling()

if __name__ == "__main__":
    main()