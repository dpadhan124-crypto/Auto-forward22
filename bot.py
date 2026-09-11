import os
import logging
import asyncio
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

class LocalStorage:
    def __init__(self):
        self.store = {}

    def get(self, user_id):
        return self.store.get(user_id)

    def set(self, user_id, data):
        self.store[user_id] = data

    def pop(self, user_id, default=None):
        return self.store.pop(user_id, default)

storage = LocalStorage()

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
        storage.set(user_id, {"step": "awaiting_chat_id"})
        await query.message.reply_text("Please send your source **Channel ID** or username (supports with or without `-100`, e.g., `123456789` or `-100123456789` or `@channel`):")

    elif query.data == "mode_topic_id":
        storage.set(user_id, {"step": "awaiting_topic_id"})
        await query.message.reply_text("Please send the numeric **Topic ID** of the existing destination group topic:")

    elif query.data == "toggle_mode":
        state = storage.get(user_id)
        if state:
            state["forward_mode"] = "reverse_order" if state.get("forward_mode", "regular") == "regular" else "regular"
            storage.set(user_id, state)
            await update_control_panel(update, context, user_id)

    elif query.data == "finish_process":
        state = storage.get(user_id)
        if not state or not state.get("files"):
            try:
                await query.edit_message_text("⚠️ No files saved to send.")
            except Exception:
                pass
            storage.pop(user_id, None)
            return

        try:
            await query.edit_message_text("⚡ Dispatching all scanned files to the topic at high speed...")
        except Exception:
            pass

        files = state["files"]
        topic_id = state["topic_id"]
        mode = state.get("forward_mode", "regular")

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

        await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ All existing files have been successfully dispatched to the topic!")
        storage.pop(user_id, None)

@admin_required
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_state = storage.get(user_id) or {}
    step = user_state.get("step")

    if step == "awaiting_chat_id":
        channel_input = update.message.text.strip()
        if channel_input.isdigit():
            channel_input = f"-100{channel_input}"

        status_msg = await update.message.reply_text("🔄 Accessing channel and creating forum topic...")
        try:
            chat = await context.bot.get_chat(channel_input)
            channel_id = chat.id
            channel_name = chat.title

            topic = await context.bot.create_forum_topic(
                chat_id=DESTINATION_GROUP_ID,
                name=channel_name
            )
            topic_id = topic.message_thread_id

            await status_msg.edit_text("🔍 Scanning channel files from ID 1 onwards... Please wait.")
            files = await scan_channel_files(context, channel_id, user_id)

            storage.set(user_id, {
                "step": "review_files",
                "topic_id": topic_id,
                "channel_name": channel_name,
                "forward_mode": "regular",
                "files": files
            })
            await status_msg.delete()
            await send_scan_summary(update, context, user_id)
        except Exception as e:
            await status_msg.edit_text(f"❌ Error during channel setup or scan: {e}\nEnsure bot is admin in channel and destination group.")

    elif step == "awaiting_topic_id":
        topic_input = update.message.text.strip()
        if not topic_input.isdigit():
            await update.message.reply_text("❌ Topic ID must be numeric. Please try again:")
            return
        
        topic_id = int(topic_input)
        storage.set(user_id, {
            "step": "awaiting_channel_method_choice",
            "topic_id": topic_id
        })
        keyboard = [
            [InlineKeyboardButton("📡 Scan Source Channel ID", callback_data="choice_scan_channel")],
            [InlineKeyboardButton("📂 Send Files Manually", callback_data="choice_manual_files")]
        ]
        await update.message.reply_text(
            f"Topic ID `{topic_id}` recorded.\nHow would you like to populate files into this topic?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif step == "awaiting_channel_for_topic":
        channel_input = update.message.text.strip()
        if channel_input.isdigit():
            channel_input = f"-100{channel_input}"

        topic_id = user_state["topic_id"]
        status_msg = await update.message.reply_text("🔄 Accessing channel and scanning files...")
        try:
            chat = await context.bot.get_chat(channel_input)
            channel_id = chat.id
            channel_name = chat.title

            files = await scan_channel_files(context, channel_id, user_id)

            storage.set(user_id, {
                "step": "review_files",
                "topic_id": topic_id,
                "channel_name": channel_name,
                "forward_mode": "regular",
                "files": files
            })
            await status_msg.delete()
            await send_scan_summary(update, context, user_id)
        except Exception as e:
            await status_msg.edit_text(f"❌ Error scanning channel: {e}")

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
            if "files" not in user_state:
                user_state["files"] = []
            user_state["files"].append({
                "type": f_type,
                "file_id": file_id,
                "caption": msg.caption or ""
            })
            storage.set(user_id, user_state)
            try:
                await msg.delete()
            except Exception:
                pass
            await update_manual_panel(update, context, user_id)

@admin_required
async def topic_method_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user_state = storage.get(user_id) or {}

    if query.data == "choice_scan_channel":
        user_state["step"] = "awaiting_channel_for_topic"
        storage.set(user_id, user_state)
        await query.message.reply_text("Please send the source **Channel ID** or username to scan all files from:")

    elif query.data == "choice_manual_files":
        user_state["step"] = "collecting_manual_files"
        user_state["files"] = []
        storage.set(user_id, user_state)
        sent_msg = await query.message.reply_text(
            f"📂 **Manual Collection Mode Active**\n\n"
            f"• **Topic ID:** `{user_state['topic_id']}`\n"
            f"• **Total files saved:** `0`\n\n"
            f"*(Send files here to add them to queue)*",
            parse_mode="Markdown"
        )
        user_state["panel_message_id"] = sent_msg.message_id
        user_state["panel_chat_id"] = query.message.chat.id
        storage.set(user_id, user_state)

async def scan_channel_files(context, channel_id, admin_user_id):
    """Scans channel messages from ID 1 up to the latest ID using binary search + forwarding detection."""
    # Find max message ID using binary search
    low, high = 1, 500000
    max_id = 0

    while low <= high:
        mid = (low + high) // 2
        try:
            # Test forward to admin to check if message ID exists
            test_msg = await context.bot.forward_message(chat_id=admin_user_id, from_chat_id=channel_id, message_id=mid)
            await test_msg.delete()
            max_id = mid
            low = mid + 1
        except Exception:
            high = mid - 1

    if max_id == 0:
        return []

    files = []
    chunk_size = 20
    for i in range(1, max_id + 1, chunk_size):
        chunk_end = min(i + chunk_size, max_id + 1)
        tasks = [async_check_and_extract(context, channel_id, admin_user_id, msg_id) for msg_id in range(i, chunk_end)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, dict) and res:
                files.append(res)

    return files

async def async_check_and_extract(context, channel_id, admin_user_id, msg_id):
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
            return {"type": f_type, "file_id": file_id, "caption": caption}
    except Exception:
        pass
    return None

async def send_scan_summary(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    state = storage.get(user_id)
    channel_name = state.get("channel_name", "Channel")
    topic_id = state["topic_id"]
    total_files = len(state["files"])
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
    storage.set(user_id, state)

async def update_control_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    state = storage.get(user_id)
    if not state or "panel_message_id" not in state:
        return
    channel_name = state.get("channel_name", "Channel")
    topic_id = state["topic_id"]
    total_files = len(state["files"])
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
    state = storage.get(user_id)
    if not state or "panel_message_id" not in state:
        return
    total_files = len(state.get("files", []))
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
    app.add_handler(CallbackQueryHandler(topic_method_callback, pattern="^choice_"))
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
