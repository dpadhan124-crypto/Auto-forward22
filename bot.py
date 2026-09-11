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

# Storage for active setups and channel-to-topic dynamic mappings
class ChannelStore:
    def __init__(self):
        self.sessions = {}   # user_id -> interactive session steps
        self.mappings = {}   # channel_id (int) -> {"topic_id": int, "channel_name": str}

storage = ChannelStore()

@admin_required
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🤖 Add Bot to Channel", url="https://t.me/DPS_xbot?startchannel=true&admin=post_messages+edit_messages+delete_messages+ban_users+invite_users+change_info+pin_messages+manage_video_chats+manage_topics+add_admins")],
        [InlineKeyboardButton("🆕 Auto-Link Channel & Create Topic", callback_data="mode_auto_channel")],
        [InlineKeyboardButton("📁 Link to Existing Topic ID", callback_data="mode_existing_topic")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    target_chat = update.message or update.callback_query.message
    await target_chat.reply_text(
        "Welcome Admin! Choose an option to configure automatic channel forwarding:",
        reply_markup=reply_markup
    )

@admin_required
async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_state = storage.sessions.get(user_id) or {}
    step = user_state.get("step")

    if step == "awaiting_channel":
        channel_input = update.message.text.strip()
        if channel_input.isdigit():
            channel_input = f"-100{channel_input}"

        try:
            chat = await context.bot.get_chat(channel_input)
            channel_id = chat.id
            channel_name = chat.title
            
            # Create a dedicated forum topic automatically using the channel name
            topic = await context.bot.create_forum_topic(
                chat_id=DESTINATION_GROUP_ID,
                name=channel_name
            )
            topic_id = topic.message_thread_id

            # Save permanent mapping for automatic background streaming
            storage.mappings[channel_id] = {
                "topic_id": topic_id,
                "channel_name": channel_name
            }
            
            storage.sessions.pop(user_id, None)
            
            await update.message.reply_text(
                f"✅ **Channel Automatically Linked!**\n\n"
                f"• **Source Channel:** `{channel_name}` (`{channel_id}`)\n"
                f"• **Destination Topic ID:** `{topic_id}`\n\n"
                f"Any new posts published in this channel will now stream automatically to the destination group topic.",
                parse_mode="Markdown"
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Error accessing channel or creating topic: {e}\nMake sure the bot is an admin in both the source channel and destination group.")

    elif step == "awaiting_existing_topic":
        topic_input = update.message.text.strip()
        if not topic_input.isdigit():
            await update.message.reply_text("❌ Topic ID must be numeric value. Please try again:")
            return
        
        user_state["topic_id"] = int(topic_input)
        user_state["step"] = "awaiting_channel_for_existing"
        storage.sessions[user_id] = user_state
        await update.message.reply_text("Now send the source **Channel ID** or username to bind with this existing topic:")

    elif step == "awaiting_channel_for_existing":
        channel_input = update.message.text.strip()
        if channel_input.isdigit():
            channel_input = f"-100{channel_input}"

        try:
            chat = await context.bot.get_chat(channel_input)
            channel_id = chat.id
            channel_name = chat.title
            topic_id = user_state["topic_id"]

            storage.mappings[channel_id] = {
                "topic_id": topic_id,
                "channel_name": channel_name
            }
            storage.sessions.pop(user_id, None)

            await update.message.reply_text(
                f"✅ **Channel Linked to Existing Topic!**\n\n"
                f"• **Source Channel:** `{channel_name}` (`{channel_id}`)\n"
                f"• **Topic ID:** `{topic_id}`",
                parse_mode="Markdown"
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Error binding channel: {e}")

@admin_required
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "mode_auto_channel":
        storage.sessions[user_id] = {"step": "awaiting_channel"}
        await query.message.reply_text(
            "Please send your source **Channel ID** or username (e.g., `-1001234567890` or `@channelname`).\n"
            "A matching topic will be created automatically."
        )
    elif query.data == "mode_existing_topic":
        storage.sessions[user_id] = {"step": "awaiting_existing_topic"}
        await query.message.reply_text("Please send the numeric **Topic ID** of the existing destination group topic:")

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Automatically scans incoming channel updates and streams files/posts to the target topic."""
    msg = update.channel_post
    if not msg:
        return

    chat_id = msg.chat.id
    if chat_id not in storage.mappings:
        return  # Ignore channels that haven't been linked

    topic_id = storage.mappings[chat_id]["topic_id"]

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
    elif msg.text:
        try:
            await context.bot.send_message(
                chat_id=DESTINATION_GROUP_ID,
                message_thread_id=topic_id,
                text=msg.text,
                entities=msg.entities
            )
        except Exception as e:
            logger.error(f"Failed to stream text post: {e}")
        return

    if file_id:
        caption = msg.caption or ""
        try:
            if f_type == "document":
                await context.bot.send_document(chat_id=DESTINATION_GROUP_ID, message_thread_id=topic_id, document=file_id, caption=caption)
            elif f_type == "video":
                await context.bot.send_video(chat_id=DESTINATION_GROUP_ID, message_thread_id=topic_id, video=file_id, caption=caption)
            elif f_type == "photo":
                await context.bot.send_photo(chat_id=DESTINATION_GROUP_ID, message_thread_id=topic_id, photo=file_id, caption=caption)
            elif f_type == "audio":
                await context.bot.send_audio(chat_id=DESTINATION_GROUP_ID, message_thread_id=topic_id, audio=file_id, caption=caption)
        except Exception as e:
            logger.error(f"Failed to stream media file from channel post: {e}")

def main():
    if not TOKEN:
        raise ValueError("No BOT_TOKEN environment variable configured.")

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_input))
    
    # Core handler for real-time channel updates (requires bot to be an admin in the channel)
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, handle_channel_post))

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
