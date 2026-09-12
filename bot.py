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

user_sessions = {}

@admin_required
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the sequence by offering setup links and forward trigger."""
    keyboard = [
        [InlineKeyboardButton("🤖 Add Bot 1 to Channel", url="https://t.me/Dps_Storiesbot?startchannel=true&admin=post_messages+edit_messages+delete_messages+ban_users+invite_users+change_info+pin_messages+manage_video_chats+manage_topics+add_admins")],
        [InlineKeyboardButton("🤖 Add Bot 2 to Channel", url="https://t.me/fm_Storiesbot?startchannel=true&admin=post_messages+edit_messages+delete_messages+ban_users+invite_users+change_info+pin_messages+manage_video_chats+manage_topics+add_admins")],
        [InlineKeyboardButton("➡️ Forward", callback_data="start_forward")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Welcome Admin! Choose an option above to add the bots, or click **Forward** to start the process.",
        reply_markup=reply_markup
    )

@admin_required
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles incoming channel data/files based on current state."""
    user_id = update.effective_user.id
    user_state = user_sessions.get(user_id, {})
    step = user_state.get("step")

    if step == "awaiting_channel":
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
                
                user_sessions[user_id] = {
                    "step": "collecting_files",
                    "topic_id": topic.message_thread_id,
                    "channel_title": channel_title,
                    "forward_mode": None,
                    "files": []
                }

                await update.message.reply_text(f"✅ Successfully linked channel: **{channel_title}** (`{channel_id}`) and created a destination topic!")
                await send_control_panel(update, context, user_id)
            except Exception as e:
                await update.message.reply_text(f"❌ Error creating forum topic: {e}\nEnsure the bot has admin privileges to manage topics in the destination group.")

    elif step == "collecting_files":
        file_id = None
        if update.message.document:
            file_id = update.message.document.file_id
        elif update.message.video:
            file_id = update.message.video.file_id
        elif update.message.photo:
            file_id = update.message.photo[-1].file_id
        elif update.message.audio:
            file_id = update.message.audio.file_id

        if file_id:
            user_sessions[user_id]["files"].append({
                "type": update.message.effective_attachment.__class__.__name__.lower(),
                "file_id": file_id,
                "caption": update.message.caption or ""
            })
            await update.message.delete()
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
    mode = state.get("forward_mode")
    total_files = len(state.get("files", []))
    mode_str = mode.capitalize() if mode else "Not Selected"

    text = (
        f"⚙️ **Configuration Panel**\n\n"
        f"• **Group topic id:** `{topic_id}`\n"
        f"• **Forward mode:** `{mode_str}`\n"
        f"• **Total files saved:** `{total_files}`\n\n"
        f"*(Send files to add them to the queue, then choose a mode & click Done)*"
    )

    keyboard = [
        [
            InlineKeyboardButton("Regular Order", callback_data="mode_regular"),
            InlineKeyboardButton("Reverse Order", callback_data="mode_reverse")
        ],
        [InlineKeyboardButton("✅ Done", callback_data="finish_process")]
    ]
    return text, InlineKeyboardMarkup(keyboard)

@admin_required
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "start_forward":
        user_sessions[user_id] = {"step": "awaiting_channel"}
        await query.message.reply_text("Please **forward any message or file** directly from your source channel here, or send its channel ID/username.")
        return

    state = user_sessions.get(user_id)
    if not state:
        await query.edit_message_text("Session expired. Send `/start` to begin again.")
        return

    if query.data == "mode_regular":
        state["forward_mode"] = "regular"
        await update_control_panel(update, context, user_id)
    elif query.data == "mode_reverse":
        state["forward_mode"] = "reverse_order"
        await update_control_panel(update, context, user_id)

    elif query.data == "finish_process":
        files = state["files"]
        topic_id = state["topic_id"]
        mode = state["forward_mode"]

        if not files:
            await query.edit_message_text("⚠️ No files saved to send.")
            user_sessions.pop(user_id, None)
            return

        if not mode:
            await query.answer("⚠️ Please select a forwarding mode (Regular or Reverse) first!", show_alert=True)
            return

        await query.edit_message_text("⏳ Processing and dispatching files anonymously...")

        if mode == "reverse_order":
            files.reverse()

        for file_info in files:
            f_type = file_info["type"]
            f_id = file_info["file_id"]
            caption = file_info["caption"]

            if "document" in f_type:
                await context.bot.send_document(chat_id=DESTINATION_GROUP_ID, message_thread_id=topic_id, document=f_id, caption=caption)
            elif "video" in f_type:
                await context.bot.send_video(chat_id=DESTINATION_GROUP_ID, message_thread_id=topic_id, video=f_id, caption=caption)
            elif "photo" in f_type:
                await context.bot.send_photo(chat_id=DESTINATION_GROUP_ID, message_thread_id=topic_id, photo=f_id, caption=caption)
            elif "audio" in f_type:
                await context.bot.send_audio(chat_id=DESTINATION_GROUP_ID, message_thread_id=topic_id, audio=f_id, caption=caption)

        await context.bot.send_message(chat_id=user_id, text="✅ All files have been successfully sent anonymously to the destination topic!")
        user_sessions.pop(user_id, None)

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
    app.add_handler(MessageHandler(filters.ATTACHMENT | filters.FORWARDED, handle_message))

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
