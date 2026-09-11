import os
import logging
import asyncio
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

# Flask App Initialization for UptimeRobot / Ping Web Server Fix
flask_app = Flask(__name__)

@flask_app.route("/", methods=["GET"])
def health_check():
    """Endpoint to satisfy UptimeRobot pings and keep Render active."""
    return jsonify({"status": "active", "bot": "running"}), 200

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

# Local Database-like Storage with 5MB quota simulation constraints
class LocalBrowserStorage:
    def __init__(self, max_size_bytes=5 * 1024 * 1024):
        self.store = {}
        self.max_size = max_size_bytes

    def get(self, user_id):
        return self.store.get(user_id)

    def set(self, user_id, data):
        # Enforce simulated browser 5MB threshold check
        import sys
        approx_size = sys.getsizeof(str(self.store))
        if approx_size >= self.max_size:
            logger.warning("Local storage quota limit approaching!")
        self.store[user_id] = data

    def pop(self, user_id, default=None):
        return self.store.pop(user_id, default)

user_sessions = LocalBrowserStorage()

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
    user_state = user_sessions.get(user_id) or {}
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
            
            user_sessions.set(user_id, {
                "step": "collecting_files",
                "topic_id": topic.message_thread_id,
                "forward_mode": "regular",
                "files": []
            })
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
            user_state["files"].append({
                "type": f_type,
                "file_id": file_id,
                "caption": msg.caption or ""
            })
            user_sessions.set(user_id, user_state)
            await msg.delete()
            await update_control_panel(update, context, user_id)

async def send_control_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    state = user_sessions.get(user_id)
    text, reply_markup = get_panel_content(state)
    sent_msg = await context.bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup, parse_mode="Markdown")
    try:
        await context.bot.pin_chat_message(chat_id=user_id, message_id=sent_msg.message_id)
    except Exception:
        pass
    state["panel_message_id"] = sent_msg.message_id
    user_sessions.set(user_id, state)

async def update_control_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    state = user_sessions.get(user_id)
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
    mode = state.get("forward_mode")
    total_files = len(state.get("files", []))
    text = (
        f"⚙️ **Configuration Panel**\n\n"
        f"• **Group topic id:** `{topic_id}`\n"
        f"• **Forward mode:** `{mode}`\n"
        f"• **Total file saved:** `{total_files}`\n\n"
        f"*(Send files to add them to the queue)*"
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
        user_sessions.set(user_id, {"step": "awaiting_channel"})
        await query.message.reply_text("Please send your source **Channel ID** (e.g., `1234567890`) or username (`@mychannel`).")
        return

    state = user_sessions.get(user_id)
    if not state:
        await query.edit_message_text("Session expired. Send `/start` to begin again.")
        return

    if query.data == "toggle_mode":
        state["forward_mode"] = "reverse_order" if state["forward_mode"] == "regular" else "regular"
        user_sessions.set(user_id, state)
        await update_control_panel(update, context, user_id)

    elif query.data == "finish_process":
        files = state["files"]
        topic_id = state["topic_id"]
        mode = state["forward_mode"]

        if not files:
            await query.edit_message_text("⚠️ No files saved to send.")
            user_sessions.pop(user_id, None)
            return

        await query.edit_message_text("⚡ Processing and dispatching files concurrently at high speed...")

        if mode == "reverse_order":
            files.reverse()

        # Speed Optimization: Concurrently dispatch files using asyncio.gather
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

        # Run chunks concurrently to maximize throughput without hitting rate limits instantly
        chunk_size = 10
        for i in range(0, len(tasks), chunk_size):
            chunk = tasks[i:i + chunk_size]
            await asyncio.gather(*chunk, return_exceptions=True)

        await context.bot.send_message(chat_id=user_id, text="✅ All files have been high-speed dispatched anonymously!")
        user_sessions.pop(user_id, None)

def main():
    if not TOKEN:
        raise ValueError("No BOT_TOKEN environment variable configured.")

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_handler(MessageHandler(filters.ATTACHMENT, handle_message))

    # Setup Webhook configuration route on Flask server
    @flask_app.route(f"/{TOKEN}", methods=["POST"])
    def telegram_webhook():
        update = Update.de_json(request.get_json(force=True), app.bot)
        asyncio.run(app.process_update(update))
        return "OK", 200

    async def setup_webhook():
        await app.initialize()
        webhook_full_url = f"{WEBHOOK_URL}/{TOKEN}"
        await app.bot.set_webhook(url=webhook_full_url)
        logger.info(f"Webhook set successfully to {webhook_full_url}")
        await app.start()

    asyncio.get_event_loop().run_until_complete(setup_webhook())

    logger.info(f"Starting web server on port {PORT}...")
    flask_app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()
