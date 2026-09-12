import os
import asyncio
import logging
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
WEBHOOK_URL = os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL")

# Authorized Admin IDs
ADMIN_IDS = [8323137024, 8553702880]

# Global dictionary to store active channel auto-forward mappings: {channel_id: topic_id}
active_channels = {}
user_sessions = {}

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
    """Starts the sequence by offering setup links and forward trigger."""
    keyboard = [
        [InlineKeyboardButton("🤖 Add Bot 1 to Channel", url="https://t.me/DPS_xbot?startchannel=true&admin=post_messages+edit_messages+delete_messages+ban_users+invite_users+change_info+pin_messages+manage_video_chats+manage_topics+add_admins")],
        [InlineKeyboardButton("🤖 Add Bot 2 to Channel", url="https://t.me/dps_Storiesbot?startchannel=true&admin=post_messages+edit_messages+delete_messages+ban_users+invite_users+change_info+pin_messages+manage_video_chats+manage_topics+add_admins")],
        [InlineKeyboardButton("➡️ Forward", callback_data="start_forward")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Welcome Admin! Choose an option above to add the bots, or click **Forward** to start auto-forwarding.",
        reply_markup=reply_markup
    )

@admin_required
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles incoming channel identification (forwarded post or channel ID/username)."""
    user_id = update.effective_user.id
    user_state = user_sessions.get(user_id)

    if not user_state or user_state.get("step") != "awaiting_channel":
        return

    channel_id = None
    channel_title = "Source Channel"

    if update.message.forward_origin and hasattr(update.message.forward_origin, "chat"):
        chat = update.message.forward_origin.chat
        channel_id = chat.id
        channel_title = chat.title or "Source Channel"
    elif update.message.text:
        channel_input = update.message.text.strip()
        if channel_input.isdigit():
            channel_input = f"-100{channel_input}"
        try:
            chat = await context.bot.get_chat(channel_input)
            channel_id = chat.id
            channel_title = chat.title or "Source Channel"
        except Exception as e:
            await update.message.reply_text(f"❌ Error accessing channel: {e}\nForward any post directly from the channel or supply a valid ID/username where the bot is an admin.")
            return

    if channel_id:
        try:
            topic = await context.bot.create_forum_topic(
                chat_id=DESTINATION_GROUP_ID,
                name=channel_title
            )
            
            topic_id = topic.message_thread_id
            active_channels[channel_id] = topic_id

            await update.message.reply_text(
                f"✅ **Auto-Forwarding Activated!**\n\n"
                f"• **Channel:** `{channel_title}` (`{channel_id}`)\n"
                f"• **Destination Topic ID:** `{topic_id}`\n\n"
                f"Any new messages posted in the source channel will now be automatically forwarded here one by one!"
            )
            user_sessions.pop(user_id, None)

        except Exception as e:
            await update.message.reply_text(f"❌ Error creating forum topic: {e}\nEnsure the bot has admin privileges to manage topics in the destination group.")

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Automatically forwards incoming channel posts one by one to their corresponding topic."""
    post = update.channel_post
    if not post:
        return

    channel_id = post.chat.id
    topic_id = active_channels.get(channel_id)

    if topic_id:
        try:
            await context.bot.copy_message(
                chat_id=DESTINATION_GROUP_ID,
                from_chat_id=channel_id,
                message_id=post.message_id,
                message_thread_id=topic_id
            )
            logger.info(f"Automatically forwarded post {post.message_id} from channel {channel_id} to topic {topic_id}")
        except Exception as e:
            logger.error(f"Failed to auto-forward channel post: {e}")

@admin_required
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "start_forward":
        user_sessions[user_id] = {"step": "awaiting_channel"}
        await query.message.reply_text("Please **forward any message or file** directly from your source channel here, or send its channel ID/username.")
        return

def main():
    if not TOKEN:
        raise ValueError("No BOT_TOKEN environment variable configured.")

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_handler(MessageHandler(filters.FORWARDED, handle_message))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel_post))

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