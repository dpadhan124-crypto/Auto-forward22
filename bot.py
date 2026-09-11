import os
import logging
import asyncio
import sqlite3
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
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

# Lightweight Database Setup (SQLite - zero external configuration required)
class LocalDB:
    def __init__(self, db_name="bot_storage.db"):
        self.db_name = db_name
        self.init_db()

    def get_connection(self):
        return sqlite3.connect(self.db_name)

    def init_db(self):
        with self.get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scanned_files (
                    user_id INTEGER,
                    channel_id TEXT,
                    msg_id INTEGER,
                    file_type TEXT,
                    file_id TEXT,
                    caption TEXT,
                    PRIMARY KEY(user_id, channel_id, msg_id)
                )
            """)
            conn.commit()

    def save_file(self, user_id, channel_id, msg_id, file_type, file_id, caption):
        with self.get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO scanned_files 
                (user_id, channel_id, msg_id, file_type, file_id, caption)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, str(channel_id), msg_id, file_type, file_id, caption))
            conn.commit()

    def get_files(self, user_id, channel_id):
        with self.get_connection() as conn:
            cursor = conn.execute("""
                SELECT file_type, file_id, caption FROM scanned_files 
                WHERE user_id = ? AND channel_id = ? 
                ORDER BY msg_id ASC
            """, (user_id, str(channel_id)))
            return [{"type": row[0], "file_id": row[1], "caption": row[2]} for row in cursor.fetchall()]

    def clear_data(self, user_id, channel_id):
        with self.get_connection() as conn:
            conn.execute("DELETE FROM scanned_files WHERE user_id = ? AND channel_id = ?", (user_id, str(channel_id)))
            conn.commit()

db = LocalDB()

# In-memory session and processing queues for multi-channel ordering
user_sessions = {}
user_queues = {}

@admin_required
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🤖 Add Bot 1 to Channel", url="https://t.me/DPS_xbot?startchannel=true&admin=post_messages+edit_messages+delete_messages+ban_users+invite_users+change_info+pin_messages+manage_video_chats+manage_topics+add_admins")],
        [InlineKeyboardButton("🤖 Add Bot 2 to Channel", url="https://t.me/dps_Storiesbot?startchannel=true&admin=post_messages+edit_messages+delete_messages+ban_users+invite_users+change_info+pin_messages+manage_video_chats+manage_topics+add_admins")],
        [InlineKeyboardButton("📁 By chat_id", callback_data="mode_chat_id")],
        [InlineKeyboardButton("📁 By topic_id", callback_data="mode_topic_id")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    target_chat = update.message or update.callback_query.message
    await target_chat.reply_text(
        "Welcome Admin! Choose how you would like to configure your file routing:",
        reply_markup=reply_markup
    )

@admin_required
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "mode_chat_id":
        user_sessions[user_id] = {"step": "awaiting_chat_id"}
        user_queues[user_id] = asyncio.Queue()
        await query.message.reply_text(
            "Please send your source **Channel ID(s)** or username(s).\n"
            "You can send multiple channels one by one; they will be processed sequentially!"
        )

    elif query.data == "mode_topic_id":
        user_sessions[user_id] = {"step": "awaiting_topic_id"}
        await query.message.reply_text("Please send the numeric **Topic ID** of the existing destination group topic:")

    elif query.data == "toggle_mode":
        state = user_sessions.get(user_id)
        if state:
            state["forward_mode"] = "reverse_order" if state.get("forward_mode", "regular") == "regular" else "regular"
            user_sessions[user_id] = state
            await update_control_panel(update, context, user_id)

    elif query.data == "finish_process":
        state = user_sessions.get(user_id)
        if not state:
            try:
                await query.edit_message_text("⚠️ Session expired.")
            except Exception:
                pass
            return

        channel_id = state.get("channel_id")
        topic_id = state["topic_id"]
        mode = state.get("forward_mode", "regular")
        files = db.get_files(user_id, channel_id)

        if not files:
            try:
                await query.edit_message_text("⚠️ No files found to send.")
            except Exception:
                pass
            db.clear_data(user_id, channel_id)
            user_sessions.pop(user_id, None)
            return

        try:
            await query.edit_message_text("⚡ Dispatching all scanned files to the topic at high speed...")
        except Exception:
            pass

        if mode == "reverse_order":
            files.reverse()

        tasks = []
        for file_info in files:
            f_type = file_info["type"]
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

        await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ All files dispatched successfully! Database cleared.")
        db.clear_data(user_id, channel_id)
        user_sessions.pop(user_id, None)

        # Process next queued channel if available
        if user_id in user_queues and not user_queues[user_id].empty():
            next_channel = await user_queues[user_id].get()
            asyncio.create_task(process_channel_workflow(update, context, user_id, next_channel))

@admin_required
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_state = user_sessions.get(user_id) or {}
    step = user_state.get("step")

    if step == "awaiting_chat_id":
        channel_input = update.message.text.strip()
        if channel_input.isdigit():
            channel_input = f"-100{channel_input}"

        if user_id not in user_queues:
            user_queues[user_id] = asyncio.Queue()

        if user_sessions.get(user_id, {}).get("processing"):
            await user_queues[user_id].put(channel_input)
            await update.message.reply_text(f"📥 Channel `{channel_input}` added to processing queue.", parse_mode="Markdown")
        else:
            user_sessions[user_id]["processing"] = True
            asyncio.create_task(process_channel_workflow(update, context, user_id, channel_input))

    elif step == "awaiting_topic_id":
        topic_input = update.message.text.strip()
        if not topic_input.isdigit():
            await update.message.reply_text("❌ Topic ID must be numeric. Please try again:")
            return
        
        topic_id = int(topic_input)
        user_state["topic_id"] = topic_id
        user_state["step"] = "awaiting_channel_for_topic"
        user_sessions[user_id] = user_state
        await update.message.reply_text("Now send the source **Channel ID** to scan and copy all files into this topic:")

    elif step == "awaiting_channel_for_topic":
        channel_input = update.message.text.strip()
        if channel_input.isdigit():
            channel_input = f"-100{channel_input}"

        topic_id = user_state["topic_id"]
        await process_channel_workflow(update, context, user_id, channel_input, predefined_topic_id=topic_id)

    elif step == "collecting_manual_files":
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
            channel_id = user_state["channel_id"]
            files = db.get_files(user_id, channel_id)
            msg_id = len(files) + 1
            db.save_file(user_id, channel_id, msg_id, f_type, file_id, msg.caption or "")
            try:
                await msg.delete()
            except Exception:
                pass
            await update_manual_panel(update, context, user_id)

async def process_channel_workflow(update, context, user_id, channel_input, predefined_topic_id=None):
    status_msg = await update.message.reply_text(f"🔄 Accessing channel `{channel_input}`...")
    try:
        chat = await context.bot.get_chat(channel_input)
        channel_id = chat.id
        channel_name = chat.title

        if predefined_topic_id:
            topic_id = predefined_topic_id
        else:
            topic = await context.bot.create_forum_topic(
                chat_id=DESTINATION_GROUP_ID,
                name=channel_name
            )
            topic_id = topic.message_thread_id

        await status_msg.edit_text(f"🔍 Scanning channel `{channel_name}` strictly in order (ID 1 onwards)... Please wait.")
        
        # Scan files sequentially or in clean batches to avoid UI blinking
        await scan_channel_files_safely(context, channel_id, user_id, status_msg)

        files = db.get_files(user_id, channel_id)
        total_files = len(files)

        user_sessions[user_id] = {
            "step": "review_files",
            "topic_id": topic_id,
            "channel_id": str(channel_id),
            "channel_name": channel_name,
            "forward_mode": "regular",
            "processing": False
        }

        await status_msg.delete()
        await send_scan_summary(update, context, user_id)

    except Exception as e:
        await status_msg.edit_text(f"❌ Error processing channel `{channel_input}`: {e}")
        user_sessions[user_id]["processing"] = False
        
        # Process next queue item if available
        if user_id in user_queues and not user_queues[user_id].empty():
            next_channel = await user_queues[user_id].get()
            asyncio.create_task(process_channel_workflow(update, context, user_id, next_channel))

async def scan_channel_files_safely(context, channel_id, admin_user_id, status_msg):
    # Binary search to find max message ID
    low, high = 1, 500000
    max_id = 0

    while low <= high:
        mid = (low + high) // 2
        try:
            test_msg = await context.bot.forward_message(chat_id=admin_user_id, from_chat_id=channel_id, message_id=mid)
            await test_msg.delete()
            max_id = mid
            low = mid + 1
        except Exception:
            high = mid - 1

    if max_id == 0:
        return

    # Sequential/batched scan with strict ordering preserved via sqlite storage (`ORDER BY msg_id ASC`)
    chunk_size = 20
    for i in range(1, max_id + 1, chunk_size):
        chunk_end = min(i + chunk_size, max_id + 1)
        tasks = [async_extract_file(context, channel_id, admin_user_id, msg_id) for msg_id in range(i, chunk_end)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, tuple) and res:
                msg_id, f_type, file_id, caption = res
                db.save_file(admin_user_id, channel_id, msg_id, f_type, file_id, caption)

async def async_extract_file(context, channel_id, admin_user_id, msg_id):
    try:
        fwd = await context.bot.forward_message(chat_id=admin_user_id, from_chat_id=channel_id, message_id=msg_id)
        msg = fwd
        file_id, f_type = None, "document"

        if msg.document:
            file_id, f_type = msg.document.file_id, "document"
        elif msg.video:
            file_id, f_type = msg.video.file_id, "video"
        elif msg.photo:
            file_id, f_type = msg.photo[-1].file_id, "photo"
        elif msg.audio:
            file_id, f_type = msg.audio.file_id, "audio"

        caption = msg.caption or ""
        await fwd.delete()

        if file_id:
            return (msg_id, f_type, file_id, caption)
    except Exception:
        pass
    return None

async def send_scan_summary(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    state = user_sessions.get(user_id)
    if not state:
        return
    channel_name = state.get("channel_name", "Channel")
    topic_id = state["topic_id"]
    files = db.get_files(user_id, state["channel_id"])
    total_files = len(files)
    mode = state.get("forward_mode", "regular")

    text = (
        f"Channel name.: `{channel_name}`\n"
        f"Create topic id: `{topic_id}`\n"
        f"Found files: total file from id 1 to {total_files}."
    )
    keyboard = [
        [InlineKeyboardButton(f"Mode: {mode.capitalize()}", callback_data="toggle_mode")],
        [InlineKeyboardButton("✅ Done", callback_data="finish_process")]
    ]
    chat_id = update.effective_chat.id
    sent_msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    state["panel_message_id"] = sent_msg.message_id
    state["panel_chat_id"] = chat_id
    user_sessions[user_id] = state

async def update_control_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    state = user_sessions.get(user_id)
    if not state or "panel_message_id" not in state:
        return
    channel_name = state.get("channel_name", "Channel")
    topic_id = state["topic_id"]
    files = db.get_files(user_id, state["channel_id"])
    total_files = len(files)
    mode = state.get("forward_mode", "regular")

    text = (
        f"Channel name.: `{channel_name}`\n"
        f"Create topic id: `{topic_id}`\n"
        f"Found files: total file from id 1 to {total_files}."
    )
    keyboard = [
        [InlineKeyboardButton(f"Mode: {mode.capitalize()}", callback_data="toggle_mode")],
        [InlineKeyboardButton("✅ Done", callback_data="finish_process")]
    ]
    try:
        await context.bot.edit_message_text(
            chat_id=state["panel_chat_id"],
            message_id=state["panel_message_id"],
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            logger.error(f"Failed to update panel: {e}")

async def update_manual_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    state = user_sessions.get(user_id)
    if not state or "panel_message_id" not in state:
        return
    files = db.get_files(user_id, state["channel_id"])
    total_files = len(files)
    text = (
        f"📂 **Manual Collection Mode Active**\n\n"
        f"• **Topic ID:** `{state['topic_id']}`\n"
        f"• **Total files saved:** `{total_files}`\n\n"
        f"*(Send files here to add them to queue)*"
    )
    keyboard = [
        [InlineKeyboardButton("✅ Done", callback_data="finish_process")]
    ]
    try:
        await context.bot.edit_message_text(
            chat_id=state["panel_chat_id"],
            message_id=state["panel_message_id"],
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    except Exception:
        pass

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
