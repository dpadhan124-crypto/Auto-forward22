import os
import asyncio
import logging
import sqlite3
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
DESTINATION_GROUP_ID = int(os.getenv("DESTINATION_GROUP_ID", "-1004470555189"))
PORT = int(os.environ.get("PORT", "8080"))
WEBHOOK_URL = os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL")

# Authorized Admin IDs
ADMIN_IDS = [8323137024, 8553702880]

# SQLite Database Initialization
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
    conn.commit()
    conn.close()

init_db()

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
        [InlineKeyboardButton("🤖 Add Bot 1 to Channel", url="https://t.me/Dps_xbot?startchannel=true&admin=post_messages+edit_messages+delete_messages+ban_users+invite_users+change_info+pin_messages+manage_video_chats+manage_topics+add_admins")],
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
    """Handles incoming channel identification or queued files."""
    user_id = update.effective_user.id
    user_state = user_sessions.get(user_id)

    if not user_state:
        return

    step = user_state.get("step")

    if step == "awaiting_channel":
        channel_id = None
        channel_title = "Source Channel"
        max_msg_id = None

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

        # If no forwarded message ID was captured, probe for the latest message ID dynamically
        if channel_id and not max_msg_id:
            test_id = 500
            while True:
                try:
                    await context.bot.forward_message(
                        chat_id=user_id,
                        from_chat_id=channel_id,
                        message_id=test_id
                    )
                    max_msg_id = test_id
                    test_id += 500  # Jump higher to find the upper bound fast
                except Exception:
                    if test_id <= 500:
                        max_msg_id = 1
                        break
                    # Binary/linear fine-tuning backwards to find the exact latest message ID
                    test_id -= 499
                    for candidate in range(test_id + 498, test_id - 1, -1):
                        try:
                            await context.bot.forward_message(
                                chat_id=user_id,
                                from_chat_id=channel_id,
                                message_id=candidate
                            )
                            max_msg_id = candidate
                            break
                        except Exception:
                            continue
                    break

        if channel_id:
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
                    "files": []
                }

                await update.message.reply_text(f"✅ Successfully linked channel: **{channel_title}** (`{channel_id}`) with Total Estimated IDs: `{max_msg_id}`!")
                await send_control_panel(update, context, user_id)
            except Exception as e:
                await update.message.reply_text(f"❌ Error setting up forum topic: {e}\nEnsure the bot has admin privileges to manage topics in the destination group.")

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

@admin_required
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "start_forward":
        db_state = load_progress_db(user_id)
        if db_state and db_state["status"] == "running":
            user_sessions[user_id] = db_state
            await query.message.reply_text("🔄 Resumed existing session from database.")
            return

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
            text=f"⏳ Automated forwarding running...\n\n• **Total ids:** `{max_msg_id}`\n• **Successfully forwarded ides:** `0`\n• **Skipd ides:** `0`\n• **FloodWait timer:** `none`"
        )

        total_ids = max_msg_id
        success_count = 0
        skipped_count = 0
        start_id = max_msg_id if mode == "reverse_order" else 1
        step_val = -1 if mode == "reverse_order" else 1

        db_data = {
            "user_id": user_id,
            "channel_id": source_chat_id,
            "topic_id": topic_id,
            "max_msg_id": max_msg_id,
            "current_msg_id": start_id,
            "success_count": 0,
            "skipped_count": 0,
            "forward_mode": mode,
            "status": "running"
        }
        save_progress_db(db_data)

        live_status_data = {"success": 0, "skipped": 0, "current": start_id, "flood": "none", "running": True}

        async def update_ui_loop():
            while live_status_data["running"]:
                try:
                    if mode == "reverse_order":
                        progress_range_str = f"{max_msg_id} to {max(live_status_data['current'], 1)}"
                    else:
                        progress_range_str = f"1 to {min(live_status_data['current'], max_msg_id)}"

                    text = (
                        f"⏳ Automated forwarding running\n\n"
                        f"• **Total ids:** `{total_ids}`\n"
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
            while (msg_id > 0 if mode == "reverse_order" else msg_id <= max_msg_id):
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
                    
                    # Normal files / regular messages delay: 0.2 seconds
                    await asyncio.sleep(0.2)
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
                    
                    # Service messages delay: 0.1 seconds
                    await asyncio.sleep(0.1)

                db_data["current_msg_id"] = msg_id
                db_data["success_count"] = success_count
                db_data["skipped_count"] = skipped_count
                save_progress_db(db_data)

                msg_id += step_val
        finally:
            live_status_data["running"] = False
            try:
                await ui_task
            except Exception:
                pass

        clear_progress_db(user_id)
        
        final_range_str = f"{max_msg_id} to 1" if mode == "reverse_order" else f"1 to {max_msg_id}"
        final_report = (
            f"✅ Automated forwarding completed!\n\n"
            f"• **Total ids:** `{total_ids}`\n"
            f"• **Successfully forwarded ides:** `{success_count}`\n"
            f"• **Skipd ides:** `{skipped_count}`\n\n"
            f"Completed id {final_range_str}\n"
            f"FloodWait timer: `none`"
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
                await asyncio.sleep(0.2)
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