import os
import logging
import asyncio
import sqlite3
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

WEBHOOK_URL = os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL") or "https://forwardbot-cx7a.onrender.com"
ADMIN_IDS = [8323137024, 8553702880]

# SQLite Database Initialization & Setup
DB_FILE = "bot_quests.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            user_id INTEGER PRIMARY KEY,
            channel_input TEXT,
            forward_mode TEXT DEFAULT 'regular',
            step TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            task_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            channel_input TEXT,
            channel_name TEXT,
            topic_id INTEGER,
            forward_mode TEXT,
            status TEXT DEFAULT 'Pending',
            current_msg_id INTEGER DEFAULT 0,
            total_files INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scanned_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER,
            serial_num INTEGER,
            file_type TEXT,
            file_id TEXT,
            caption TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

class DatabaseManager:
    def get_session(self, user_id):
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sessions WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        session = dict(row) if row else None
        conn.close()
        return session

    def set_session(self, user_id, **kwargs):
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM sessions WHERE user_id = ?", (user_id,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO sessions (user_id, forward_mode) VALUES (?, 'regular')", (user_id,))
        
        fields = [f"{k} = ?" for k in kwargs.keys()]
        values = list(kwargs.values()) + [user_id]
        if fields:
            cursor.execute(f"UPDATE sessions SET {', '.join(fields)} WHERE user_id = ?", values)
        conn.commit()
        conn.close()

    def clear_session(self, user_id):
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()

    def clear_all_database(self):
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sessions")
        cursor.execute("DELETE FROM tasks")
        cursor.execute("DELETE FROM scanned_files")
        conn.commit()
        conn.close()

    def create_task(self, user_id, channel_input, channel_name, topic_id, forward_mode):
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO tasks (user_id, channel_input, channel_name, topic_id, forward_mode, status)
            VALUES (?, ?, ?, ?, ?, 'Scanning')
        ''', (user_id, channel_input, channel_name, topic_id, forward_mode))
        task_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return task_id

    def update_task_progress(self, task_id, current_msg_id, total_files, status=None):
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        if status:
            cursor.execute('''
                UPDATE tasks SET current_msg_id = ?, total_files = ?, status = ? WHERE task_id = ?
            ''', (current_msg_id, total_files, status, task_id))
        else:
            cursor.execute('''
                UPDATE tasks SET current_msg_id = ?, total_files = ? WHERE task_id = ?
            ''', (current_msg_id, total_files, task_id))
        conn.commit()
        conn.close()

    def add_scanned_file(self, task_id, serial_num, file_type, file_id, caption):
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO scanned_files (task_id, serial_num, file_type, file_id, caption)
            VALUES (?, ?, ?, ?, ?)
        ''', (task_id, serial_num, file_type, file_id, caption))
        conn.commit()
        conn.close()

    def get_all_tasks(self):
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks ORDER BY task_id ASC")
        tasks = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return tasks

    def get_task(self, task_id):
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        row = cursor.fetchone()
        task = dict(row) if row else None
        conn.close()
        return task

    def get_task_files(self, task_id):
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM scanned_files WHERE task_id = ? ORDER BY serial_num ASC", (task_id,))
        files = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return files

db = DatabaseManager()

# Flask App Initialization for UptimeRobot / Ping Web Server Fix
flask_app = Flask(__name__)

@flask_app.route("/", methods=["GET"])
def health_check():
    return jsonify({"status": "active", "bot": "running"}), 200

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
    keyboard = [
        [InlineKeyboardButton("🤖 Add Bot 1 to Channel", url="https://t.me/DPS_xbot?startchannel=true&admin=post_messages+edit_messages+delete_messages+ban_users+invite_users+change_info+pin_messages+manage_video_chats+manage_topics+add_admins")],
        [InlineKeyboardButton("🤖 Add Bot 2 to Channel", url="https://t.me/dps_Storiesbot?startchannel=true&admin=post_messages+edit_messages+delete_messages+ban_users+invite_users+change_info+pin_messages+manage_video_chats+manage_topics+add_admins")],
        [InlineKeyboardButton("➡️ Forward & Auto-Scan", callback_data="start_forward")],
        [InlineKeyboardButton("📜 Quest Status", callback_data="show_quests")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = update.message or (update.callback_query and update.callback_query.message)
    if msg:
        if update.callback_query:
            try:
                await update.callback_query.message.edit_text("Welcome Admin! Choose an option below:", reply_markup=reply_markup)
            except Exception:
                await update.callback_query.message.reply_text("Welcome Admin! Choose an option below:", reply_markup=reply_markup)
        else:
            await msg.reply_text("Welcome Admin! Choose an option below:", reply_markup=reply_markup)

@admin_required
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = db.get_session(user_id) or {}
    step = session.get("step")

    if step == "awaiting_channel":
        channel_input = update.message.text.strip()
        if channel_input.isdigit():
            channel_input = f"-100{channel_input}"

        # Save channel input temporarily in session and ask for mode & done button confirmation
        db.set_session(user_id, channel_input=channel_input, step="awaiting_mode_confirmation")
        
        keyboard = [
            [InlineKeyboardButton("Mode: Regular", callback_data="set_mode_regular"),
             InlineKeyboardButton("Mode: Reverse", callback_data="set_mode_reverse")],
            [InlineKeyboardButton("✅ Done / Start Scan", callback_data="confirm_scan_start")]
        ]
        await update.message.reply_text(
            f"📢 Channel target received: `{channel_input}`\n\n"
            f"Current Forward Mode: **Regular**\n"
            f"Click mode button to toggle, then click **✅ Done / Start Scan** to begin processing.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

async def run_channel_scanner(bot, task_id):
    task = db.get_task(task_id)
    if not task:
        return

    channel_id = task["channel_input"]
    topic_id = task["topic_id"]
    forward_mode = task["forward_mode"]
    user_id = task["user_id"]

    msg_id = 1
    consecutive_misses = 0
    max_misses = 40  # Increased threshold to avoid stopping too early on sparse or missing gaps
    total_files = 0

    logger.info(f"Starting auto-scan for task {task_id} ({channel_id})")

    while consecutive_misses < max_misses:
        try:
            # Force copy/forward message to destination topic or fallback test chat
            # Since bot is admin in channel, copy_message works reliably for pulling media/content without failing on restricted channels if permissions permit
            forwarded = None
            try:
                forwarded = await bot.copy_message(
                    chat_id=DESTINATION_GROUP_ID,
                    message_thread_id=topic_id,
                    from_chat_id=channel_id,
                    message_id=msg_id
                )
            except Exception:
                pass

            if forwarded:
                total_files += 1
                db.add_scanned_file(task_id, total_files, "copied", str(forwarded.message_id), "")
                consecutive_misses = 0
            else:
                consecutive_misses += 1

        except Exception as e:
            logger.debug(f"Scan skip msg_id {msg_id}: {e}")
            consecutive_misses += 1

        db.update_task_progress(task_id, msg_id, total_files)
        msg_id += 1
        await asyncio.sleep(0.05) # High-speed concurrent throttling

    # Finished scanning via direct copy stream or fallback collection
    db.update_task_progress(task_id, msg_id - 1, total_files, status="Completed")
    try:
        await bot.send_message(chat_id=user_id, text=f"✅ Task #{task_id} scan completed! Total files/messages processed & forwarded: `{total_files}`.")
    except Exception:
        pass

@admin_required
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = db.get_session(user_id) or {}

    if query.data == "start_forward":
        db.set_session(user_id, step="awaiting_channel", forward_mode="regular")
        await query.message.reply_text("Please send your source **Channel ID** (e.g., `1234567890`) or username (`@mychannel`).")
        return

    elif query.data == "set_mode_regular":
        db.set_session(user_id, forward_mode="regular")
        keyboard = [
            [InlineKeyboardButton("Mode: Regular ✅", callback_data="set_mode_regular"),
             InlineKeyboardButton("Mode: Reverse", callback_data="set_mode_reverse")],
            [InlineKeyboardButton("✅ Done / Start Scan", callback_data="confirm_scan_start")]
        ]
        await query.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif query.data == "set_mode_reverse":
        db.set_session(user_id, forward_mode="reverse_order")
        keyboard = [
            [InlineKeyboardButton("Mode: Regular", callback_data="set_mode_regular"),
             InlineKeyboardButton("Mode: Reverse ✅", callback_data="set_mode_reverse")],
            [InlineKeyboardButton("✅ Done / Start Scan", callback_data="confirm_scan_start")]
        ]
        await query.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif query.data == "confirm_scan_start":
        channel_input = session.get("channel_input")
        forward_mode = session.get("forward_mode", "regular")
        db.clear_session(user_id)

        if not channel_input:
            await query.message.edit_text("⚠️ Session expired or channel missing. Please start over with `/start`.")
            return

        try:
            chat = await context.bot.get_chat(channel_input)
            channel_name = chat.title
            
            topic = await context.bot.create_forum_topic(
                chat_id=DESTINATION_GROUP_ID,
                name=channel_name
            )
            
            task_id = db.create_task(user_id, channel_input, channel_name, topic.message_thread_id, forward_mode)
            
            await query.message.edit_text(
                f"✅ Channel **{channel_name}** verified & locked!\n"
                f"📌 Created Topic ID: `{topic.message_thread_id}`\n"
                f"⚡ Forceful background auto-scan initiated from ID 1 upwards. Check **📜 Quest Status** for live progress."
            )
            
            asyncio.create_task(run_channel_scanner(context.bot, task_id))

        except Exception as e:
            await query.message.edit_text(f"❌ Error initiating channel scan: {e}")
        return

    elif query.data == "show_quests":
        tasks = db.get_all_tasks()
        keyboard = [
            [InlineKeyboardButton("🔄 Refresh", callback_data="show_quests"),
             InlineKeyboardButton("🗑️ Clear Database", callback_data="clear_database")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_start")]
        ]
        if not tasks:
            await query.message.edit_text("📜 No active or completed quests found.", reply_markup=InlineKeyboardMarkup(keyboard))
            return

        text = "📜 **Quest System Status**\n\n"
        for t in tasks:
            status_emoji = "🔄" if t["status"] == "Scanning" else ("⚡" if t["status"] == "Dispatching" else "✅")
            text += f"**#{t['task_id']}** | 📢 {t['channel_name']}\n"
            text += f"• Status: {status_emoji} `{t['status']}`\n"
            text += f"• Scanned Msg ID: `{t['current_msg_id']}` | Files Found: `{t['total_files']}`\n"
            text += f"• Mode: `{t['forward_mode']}`\n\n"

        await query.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif query.data == "clear_database":
        db.clear_all_database()
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="back_to_start")]]
        await query.message.edit_text("🗑️ Database successfully cleared and all task history wiped!", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    elif query.data == "back_to_start":
        await start(update, context)
        return

def main():
    if not TOKEN:
        raise ValueError("No BOT_TOKEN environment variable configured.")

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    @flask_app.route(f"/{TOKEN}", methods=["POST"])
    def telegram_webhook():
        update = Update.de_json(request.get_json(force=True), app.bot)
        
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
        loop.run_until_complete(app.process_update(update))
        return "OK", 200

    async def setup_webhook():
        await app.initialize()
        webhook_full_url = f"{WEBHOOK_URL}/{TOKEN}"
        await app.bot.set_webhook(url=webhook_full_url)
        logger.info(f"Webhook set successfully to {webhook_full_url}")
        await app.start()

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    loop.run_until_complete(setup_webhook())

    logger.info(f"Starting web server on port {PORT}...")
    flask_app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()