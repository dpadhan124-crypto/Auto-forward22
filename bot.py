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
DESTINATION_GROUP_ID = int(os.getenv("DESTINATION_GROUP_ID", "4441022456"))
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
    """Handles incoming channel identification or queued files."""
    user_id = update.effective_user.id
    user_state = user_sessions.get(user_id)

    if not user_state:
        return

    step = user_state.get("step")

    if step == "awaiting_channel":
        channel_id = None
        channel_title = "Source Channel"
        max_msg_id = 100  # Default fallback range upper limit

        if update.message.forward_origin:
            origin = update.message.forward_origin
            if hasattr(origin, "chat"):
                channel_id = origin.chat.id
                channel_title = origin.chat.title or "Source Channel"
            if hasattr(origin, "message_id"):
                max_msg_id = origin.message_id
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
            try:
                topic = await context.bot.create_forum_topic(
                    chat_id=DESTINATION_GROUP_ID,
                    name=channel_title
                )
                
                user_sessions[user_id] = {
                    "step": "collecting_files",
                    "topic_id": topic.message_thread_id,
                    "channel_id": channel_id,
                    "max_msg_id": max_msg_id,
                    "forward_mode": "regular",  # Default to regular
                    "files": []
                }

                await update.message.reply_text(f"✅ Successfully linked channel: **{channel_title}** (`{channel_id}`)!")
                await send_control_panel(update, context, user_id)
            except Exception as e:
                await update.message.reply_text(f"❌ Error creating forum topic: {e}\nEnsure the bot has admin privileges to manage topics in the destination group.")

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
    mode_str = mode.capitalize()

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

        status_msg = await context.bot.send_message(
            chat_id=user_id,
            text="⏳ Automated forwarding running from message ID 1 onwards... Please wait."
        )

        total_ids = max_msg_id
        success_count = 0
        skipped_count = 0
        log_lines = []

        # Determine the sequence order based on the user's explicit selection
        message_sequence = range(1, max_msg_id + 1)
        if mode == "reverse_order":
            message_sequence = range(max_msg_id, 0, -1)

        # Enforce strict serial execution (one-by-one sequential loop with strict awaits)
        for msg_id in message_sequence:
            try:
                await context.bot.copy_message(
                    chat_id=DESTINATION_GROUP_ID,
                    from_chat_id=source_chat_id,
                    message_id=msg_id,
                    message_thread_id=topic_id
                )
                success_count += 1
                await asyncio.sleep(0.4)  # Safe delay to preserve proper sequential order on Telegram servers
            except Exception:
                skipped_count += 1
                log_lines.append(f"ID {msg_id} Skipd it's a service id")

        logs_text = "\n".join(log_lines[:20])
        if len(log_lines) > 20:
            logs_text += f"\n... and {len(log_lines) - 20} more skipped items."

        final_report = (
            f"⏳ Automated forwarding running\n\n"
            f"• **Total ids:** `{total_ids}`\n"
            f"• **Successfully forwarded ides:** `{success_count}`\n"
            f"• **Skipd ides:** `{skipped_count}`\n\n"
            f"**Logs:**\n{logs_text}"
        )

        await status_msg.edit_text(final_report, parse_mode="Markdown")
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
                await asyncio.sleep(0.4)
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