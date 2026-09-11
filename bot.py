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
DB_FILE = "bot_storage.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            user_id INTEGER PRIMARY KEY,
            topic_id INTEGER,
            forward_mode TEXT DEFAULT 'regular',
            step TEXT,
            panel_message_id INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            serial_num INTEGER,
            file_type TEXT,
            file_id TEXT,
            caption TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

class SQLiteSessionManager:
    def get_session(self, user_id):
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sessions WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        session = dict(row) if row else None
        
        if session:
            cursor.execute("SELECT * FROM files WHERE user_id = ? ORDER BY serial_num ASC", (user_id,))
            session["files"] = [dict(f) for f in cursor.fetchall()]
        conn.close()
        return session

    def set_session_field(self, user_id, **kwargs):
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute("SELECT user_id FROM sessions WHERE user_id = ?", (user_id,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO sessions (user_id, forward_mode) VALUES (?, 'regular')", (user_id,))
        
        fields = []
        values = []
        for k, v in kwargs.items():
            fields.append(f"{k} = ?")
            values.append(v)
        
        if fields:
            values.append(user_id)
            cursor.execute(f"UPDATE sessions SET {', '.join(fields)} WHERE user_id = ?", values)
            
        conn.commit()
        conn.close()

    def add_file(self, user_id, file_type, file_id, caption):
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM files WHERE user_id = ?", (user_id,))
        count = cursor.fetchone()[0]
        serial_num = count + 1
        
        cursor.execute('''
            INSERT INTO files (user_id, serial_num, file_type, file_id, caption)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, serial_num, file_type, file_id, caption))
        conn.commit()
        conn.close()

    def clear_session(self, user_id):
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM files WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()

user_sessions = SQLiteSessionManager()

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
        [InlineKeyboardButton("➡️ Forward", callback_data="start_forward")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Welcome Admin! Choose an option above to add the bots, or click **Forward** to start the process.",
        reply_markup=reply_markup
    )

@admin_required
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_state = user_sessions.get_session(user_id) or {}
    step = user_state.get("step")

    if step == "awaiting_channel":
        channel_input = update.message.text.strip()
        if channel_input.isdigit():
            channel_input = f"-100{channel_input}"

        try:
            chat = await context.bot.get_chat(channel_input)
            channel_name = chat.title
            
            topic = await context.bot.create_forum_topic(
                chat_id=DESTINATION_GROUP_ID,
                name=channel_name
            )
            
            user_sessions.set_session_field(
                user_id,
                step="collecting_files",
                topic_id=topic.message_thread_id,
                forward_mode="regular"
            )
            await send_control_panel(update, context, user_id)
        except Exception as e:
            await update.message.reply_text(f"❌ Error accessing channel: {e}")
    
    elif step == "collecting_files":
        msg = update.message
        file_id = None
        f_type = "document"

        if msg.document:
            file_id, f_type = msg.document.file_id, "document"
        elif msg.video:
            file_id, f_type = msg.video.file_id, "video"
        elif msg.photo:
            file_id, f_type = msg.photo[-1].file_id, "photo"
        elif msg.audio:
            file_id, f_type = msg.audio.file_id, "audio"

        if file_id:
            user_sessions.add_file(user_id, f_type, file_id, msg.caption or "")
            await msg.delete()
            await update_control_panel(update, context, user_id)

async def send_control_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    state = user_sessions.get_session(user_id)
    text, reply_markup = get_panel_content(state)
    sent_msg = await context.bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup, parse_mode="Markdown")
    try:
        await context.bot.pin_chat_message(chat_id=user_id, message_id=sent_msg.message_id)
    except Exception:
        pass
    user_sessions.set_session_field(user_id, panel_message_id=sent_msg.message_id)

async def update_control_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    state = user_sessions.get_session(user_id)
    if not state:
        return
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
    text = (
        f"⚙️ **Configuration Panel**\n\n"
        f"• **Group topic id:** `{topic_id}`\n"
        f"• **Forward mode:** `{mode}`\n"
        f"• **Total file saved:** `{total_files}`\n\n"
        f"*(Send files to queue them with serial numbers)*"
    )
    keyboard = [
        [InlineKeyboardButton(f"Mode: {mode.capitalize()}", callback_data="toggle_mode")],
        [InlineKeyboardButton("✅ Done", callback_data="finish_process")]
    ]
    return text, InlineKeyboardMarkup(keyboard)

@admin_required
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "start_forward":
        user_sessions.set_session_field(user_id, step="awaiting_channel")
        await query.message.reply_text("Please send your source **Channel ID** (e.g., `1234567890`) or username (`@mychannel`).")
        return

    state = user_sessions.get_session(user_id)
    if not state:
        await query.edit_message_text("Session expired. Send `/start` to begin again.")
        return

    if query.data == "toggle_mode":
        new_mode = "reverse_order" if state.get("forward_mode") == "regular" else "regular"
        user_sessions.set_session_field(user_id, forward_mode=new_mode)
        await update_control_panel(update, context, user_id)

    elif query.data == "finish_process":
        files = state.get("files", [])
        topic_id = state.get("topic_id")
        mode = state.get("forward_mode", "regular")

        if not files:
            await query.edit_message_text("⚠️ No files saved to send.")
            user_sessions.clear_session(user_id)
            return

        await query.edit_message_text("⚡ Processing and dispatching files preserving serial order...")

        if mode == "reverse_order":
            files.reverse()

        tasks = []
        for file_info in files:
            f_type = file_info["file_type"]
            f_id = file_info["file_id"]
            caption = file_info["caption"]

            if f_type == "document":
                tasks.append(context.bot.send_document(chat_id=DESTINATION_GROUP_ID, message_thread_id=topic_id, document=f_id, caption=caption))
            elif f_type == "video":
                tasks.append(context.bot.send_video(chat_id=DESTINATION_GROUP_ID, message_thread_id=topic_id, video=f_id, caption=caption))
            elif f_type == "photo":
                tasks.append(context.bot.send_photo(chat_id=DESTINATION_GROUP_ID, message_thread_id=topic_id, photo=f_id, caption=caption))
            elif f_type == "audio":
                tasks.append(context.bot.send_audio(chat_id=DESTINATION_GROUP_ID, message_thread_id=topic_id, audio=f_id, caption=caption))

        chunk_size = 10
        for i in range(0, len(tasks), chunk_size):
            chunk = tasks[i:i + chunk_size]
            await asyncio.gather(*chunk, return_exceptions=True)

        await context.bot.send_message(chat_id=user_id, text="✅ All files dispatched with correct serial positioning. Session records and cases deleted successfully!")
        user_sessions.clear_session(user_id)

def main():
    if not TOKEN:
        raise ValueError("No BOT_TOKEN environment variable configured.")

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_handler(MessageHandler(filters.ATTACHMENT, handle_message))

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