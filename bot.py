import logging
import asyncio
import time
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
import telethon.tl.functions.channels
import telethon.tl.functions.messages

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- CONFIGURATION (Render Environment Safe) ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
TARGET_GROUP_ID = -1004440356312

# Pre-defined bot configuration
BOT_1_USERNAME = "Dps_storiesbot"   # Full administrative permissions
BOT_2_USERNAME = "Testdp112232bot"  # Forwarder / message copying bot

# Runtime storage for session string, task queue, and cancellation tracking
RUNTIME_SESSION_STRING = os.environ.get("SESSION_STRING", "")
task_queue = asyncio.Queue()
current_task_cancel_event = asyncio.Event()


def generate_progress_bar(completed, total):
    """Generates a visual progress bar string."""
    percentage = (completed / total) if total > 0 else 0
    filled = int(round(10 * percentage))
    bar = "█" * filled + "░" * (10 - filled)
    return f"[{bar}] {int(percentage * 100)}%"


async def setup_bots_and_topic_telethon(client, channel_input, target_group_id):
    """Promotes predefined bots with native FloodWait handling, fetches channel entity, and creates a forum topic."""
    if isinstance(channel_input, str):
        channel_input = channel_input.strip()
        if channel_input.startswith("-") or channel_input.isdigit():
            channel_input = int(channel_input)

    while True:
        try:
            channel = await client.get_entity(channel_input)
            break
        except FloodWaitError as fwe:
            logger.warning(f"FloodWait on get_entity: sleeping for {fwe.seconds} seconds")
            await asyncio.sleep(fwe.seconds + 2)
        except Exception as e:
            raise ValueError(f"Could not resolve channel ID/username '{channel_input}': {e}")

    channel_title = getattr(channel, 'title', f"Channel {getattr(channel, 'id', 'Unknown')}")

    b1 = BOT_1_USERNAME.strip().replace("@", "")
    b2 = BOT_2_USERNAME.strip().replace("@", "")

    # 1. Invite and promote Bot 1 (Dps_storiesbot)
    while True:
        try:
            await client(telethon.tl.functions.channels.InviteToChannelRequest(channel=channel, users=[b1]))
            break
        except FloodWaitError as fwe:
            logger.warning(f"FloodWait on invite Bot 1: sleeping for {fwe.seconds} seconds")
            await asyncio.sleep(fwe.seconds + 2)
        except Exception:
            break
    
    while True:
        try:
            await client.edit_admin(
                entity=channel,
                user=b1,
                change_info=True, 
                post_messages=True, 
                edit_messages=True,
                delete_messages=True, 
                ban_users=True, 
                invite_users=True,
                pin_messages=True, 
                add_admins=True, 
                anonymous=False,
                manage_call=True
            )
            break
        except FloodWaitError as fwe:
            logger.warning(f"FloodWait on edit_admin Bot 1: sleeping for {fwe.seconds} seconds")
            await asyncio.sleep(fwe.seconds + 2)
        except Exception as e:
            logger.warning(f"Notice regarding Bot 1 promotion: {e}")
            break

    # 2. Invite and promote Bot 2 (Testdp112232bot)
    while True:
        try:
            await client(telethon.tl.functions.channels.InviteToChannelRequest(channel=channel, users=[b2]))
            break
        except FloodWaitError as fwe:
            logger.warning(f"FloodWait on invite Bot 2: sleeping for {fwe.seconds} seconds")
            await asyncio.sleep(fwe.seconds + 2)
        except Exception:
            break

    while True:
        try:
            await client.edit_admin(
                entity=channel,
                user=b2,
                post_messages=True,
                edit_messages=True,
                delete_messages=True
            )
            break
        except FloodWaitError as fwe:
            logger.warning(f"FloodWait on edit_admin Bot 2: sleeping for {fwe.seconds} seconds")
            await asyncio.sleep(fwe.seconds + 2)
        except Exception as e:
            logger.warning(f"Notice regarding Bot 2 promotion: {e}")
            break

    # 3. Create a forum topic in the target group with FloodWait protection
    thread_id = None
    while True:
        try:
            result = await client(telethon.tl.functions.messages.CreateForumTopicRequest(
                peer=target_group_id,
                title=channel_title
            ))
            for update in result.updates:
                if isinstance(update, telethon.tl.types.UpdateMessageService) and isinstance(update.action, telethon.tl.types.MessageActionTopicCreate):
                    thread_id = update.id
                    break
            break
        except FloodWaitError as fwe:
            logger.warning(f"FloodWait on CreateForumTopic: sleeping for {fwe.seconds} seconds")
            await asyncio.sleep(fwe.seconds + 2)
        except Exception as e:
            logger.error(f"Failed to create forum topic via Telethon: {e}")
            break

    return channel, thread_id


async def process_forwarding_task(task_data):
    """Processes a single automated task securely using Telethon userbot session."""
    global current_task_cancel_event
    current_task_cancel_event.clear()

    update = task_data['update']
    status_msg = task_data['status_msg']
    source_channel_str = task_data['source_channel_str']
    reverse_order = task_data['reverse_order']

    session_to_use = RUNTIME_SESSION_STRING or os.environ.get("SESSION_STRING", "")
    client = TelegramClient(StringSession(session_to_use), API_ID, API_HASH)

    async with client:
        try:
            await status_msg.edit_text("⚙️ Setting up bots and creating destination forum topic...")
            channel_entity, message_thread_id = await setup_bots_and_topic_telethon(client, source_channel_str, TARGET_GROUP_ID)
        except Exception as e:
            error_reason = f"Setup failed: `{type(e).__name__}: {str(e)}`"
            logger.error(error_reason)
            await status_msg.edit_text(f"❌ **Task Failed & Cancelled**\n\nReason: {error_reason}", parse_mode="Markdown")
            return

        message_ids = []
        while True:
            if current_task_cancel_event.is_set():
                await status_msg.edit_text("🛑 **Task Cancelled by User** during file indexing.", parse_mode="Markdown")
                return
            try:
                async for message in client.iter_messages(channel_entity):
                    message_ids.append(message.id)
                break
            except FloodWaitError as fwe:
                logger.warning(f"FloodWait during iteration: sleeping for {fwe.seconds} seconds")
                await asyncio.sleep(fwe.seconds + 2)
            except Exception as e:
                error_reason = f"Failed to iterate channel messages: `{type(e).__name__}: {str(e)}`"
                logger.error(error_reason)
                await status_msg.edit_text(f"❌ **Task Failed**\n\nReason: {error_reason}", parse_mode="Markdown")
                return

        if reverse_order:
            message_ids.reverse()

        total_files = len(message_ids)
        forwarded_files = 0
        error_files = 0
        last_error_reason = "None"
        start_time = time.time()

        await status_msg.edit_text(
            f"🚀 **Automated Forwarding Started**\n\n"
            f"📊 Progress: [░░░░░░░░░░] 0%\n"
            f"📁 Total Files: {total_files}\n"
            f"✅ Forwarded: 0\n"
            f"❌ Errors: 0\n"
            f"⏳ Remaining Time: Calculating...",
            parse_mode="Markdown"
        )

        bot = update.get_bot()
        for idx, msg_id in enumerate(message_ids, start=1):
            if current_task_cancel_event.is_set():
                await status_msg.edit_text(
                    f"🛑 **Task Cancelled by User**\n\n"
                    f"📊 Progress: {generate_progress_bar(idx, total_files)}\n"
                    f"✅ Forwarded: {forwarded_files} | ❌ Errors: {error_files}",
                    parse_mode="Markdown"
                )
                return

            success = False
            retries = 3
            while retries > 0 and not success:
                try:
                    kwargs = {
                        "chat_id": TARGET_GROUP_ID,
                        "from_chat_id": channel_entity.id,
                        "message_id": msg_id
                    }
                    if message_thread_id:
                        kwargs["message_thread_id"] = int(message_thread_id)

                    await bot.copy_message(**kwargs)
                    forwarded_files += 1
                    success = True
                except Exception as err:
                    err_str = str(err).lower()
                    if "flood" in err_str or "retry after" in err_str:
                        import re
                        match = re.search(r"retry after (\d+)", err_str)
                        sleep_time = int(match.group(1)) if match else 15
                        logger.warning(f"Telegram Bot API FloodWait: sleeping for {sleep_time}s")
                        await asyncio.sleep(sleep_time + 2)
                        retries -= 1
                    else:
                        last_error_reason = f"`{type(err).__name__}: {str(err)}`"
                        logger.error(f"Error copying message {msg_id}: {err}")
                        error_files += 1
                        break

            if not success and retries == 0 and not error_files:
                error_files += 1

            if idx % 5 == 0 or idx == total_files:
                elapsed = time.time() - start_time
                avg_time = elapsed / idx if idx > 0 else 0
                eta = int(avg_time * (total_files - idx))
                eta_str = f"{eta // 60}m {eta % 60}s" if eta > 60 else f"{eta}s"

                try:
                    await status_msg.edit_text(
                        f"🚀 **Automated Forwarding in Progress**\n\n"
                        f"📊 Progress: {generate_progress_bar(idx, total_files)}\n"
                        f"📁 Total Files: {total_files}\n"
                        f"✅ Forwarded: {forwarded_files}\n"
                        f"❌ Errors: {error_files}\n"
                        f"⏳ Remaining Time: {eta_str}",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

        summary_text = (
            f"✨ **Automated Forwarding Completed!**\n\n"
            f"📊 Progress: [██████████] 100%\n"
            f"📁 Total Files: {total_files}\n"
            f"✅ Forwarded: {forwarded_files}\n"
            f"❌ Errors: {error_files}\n"
            f"⏱️ Total Time: {int(time.time() - start_time)}s"
        )
        if error_files > 0:
            summary_text += f"\n\n⚠️ **Last Formed Error Detected:**\n{last_error_reason}"

        await status_msg.edit_text(summary_text, parse_mode="Markdown")


async def task_worker():
    """Background worker that continuously pulls tasks from the queue sequentially (Quest flow)."""
    while True:
        task_data = await task_queue.get()
        try:
            if task_data['type'] == 'automated':
                await process_forwarding_task(task_data)
        except Exception as e:
            logger.error(f"Error in task worker queue: {e}")
        finally:
            task_queue.task_done()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /start command."""
    await update.message.reply_text(
        "👋 Welcome! I am your automated forwarding and management bot.\n\n"
        "Commands:\n"
        "• `/add_session` - Save your Telethon session string\n"
        "• `/send {channel_id} [r]` - Setup source channel and prompt mode selection (Automated/Manual)\n"
        "• `/forward` - Interactive custom file collection mode\n"
        "• `/cancel` - Cancel active process and clear remaining queue",
        parse_mode="Markdown"
    )


async def add_session_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /add_session command."""
    await update.message.reply_text(
        "🔑 Please send your **Telethon Session String** in the next message:",
        parse_mode="Markdown"
    )
    context.user_data['step'] = 'waiting_session_string'


async def send_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /send command by promoting bots, creating topic, and presenting choice buttons."""
    session_to_use = RUNTIME_SESSION_STRING or os.environ.get("SESSION_STRING", "")
    if not session_to_use:
        await update.message.reply_text("⚠️ No session string configured! Please use `/add_session` first.", parse_mode="Markdown")
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/send {source_channel_id} [r]`", parse_mode="Markdown")
        return

    source_channel_str = args[0]
    reverse_order = len(args) > 1 and args[1].lower() == 'r'

    status_msg = await update.message.reply_text("⚙️ Setting up bots in source channel and creating destination forum topic...")

    client = TelegramClient(StringSession(session_to_use), API_ID, API_HASH)
    async with client:
        try:
            channel_entity, message_thread_id = await setup_bots_and_topic_telethon(client, source_channel_str, TARGET_GROUP_ID)
        except Exception as e:
            await status_msg.edit_text(f"❌ Setup failed: {e}")
            return

    # Save details into user_data for mode callback execution
    context.user_data['source_channel_str'] = source_channel_str
    context.user_data['reverse_order'] = reverse_order
    context.user_data['topic_id'] = message_thread_id
    context.user_data['status_msg_id'] = status_msg.id

    keyboard = [
        [InlineKeyboardButton("🤖 Automated Forwarding", callback_data="mode_auto")],
        [InlineKeyboardButton("📝 Manual Forwarding (Custom Files)", callback_data="mode_manual")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await status_msg.edit_text(
        f"✅ **Admins successfully promoted & Topic created!**\n\n"
        f"Please select your preferred forwarding mode below:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def mode_selection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles selection between Automated and Manual forwarding after successful setup."""
    query = update.callback_query
    await query.answer()

    data = query.data
    source_channel_str = context.user_data.get('source_channel_str')
    reverse_order = context.user_data.get('reverse_order', False)
    topic_id = context.user_data.get('topic_id')

    if data == "mode_auto":
        queue_position = task_queue.qsize() + 1
        await query.edit_message_text(
            f"📋 **Automated Task Added to Quest Queue!**\n"
            f"📌 Position in Queue: `{queue_position}`\n"
            f"⏳ Waiting for previous tasks to finish...",
            parse_mode="Markdown"
        )
        task_data = {
            'type': 'automated',
            'update': update,
            'source_channel_str': source_channel_str,
            'reverse_order': reverse_order,
            'status_msg': query.message
        }
        await task_queue.put(task_data)

    elif data == "mode_manual":
        context.user_data['manual_topic_id'] = topic_id
        context.user_data['manual_order'] = 'normal'
        context.user_data['manual_files'] = []
        context.user_data['step'] = 'collecting_manual_files'

        keyboard = [
            [InlineKeyboardButton("🔄 Order: Normal", callback_data="toggle_manual_order")],
            [InlineKeyboardButton("✅ Done / Start Manual Dispatch", callback_data="trigger_manual_don")]
        ]
        await query.edit_message_text(
            f"📝 **Manual Forwarding Mode Initialized**\n"
            f"• Destination Topic ID: `{topic_id}`\n"
            f"• Current Order: `Normal`\n\n"
            f"Send files to the bot one by one. Click **Done** when finished.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )


async def manual_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles inline buttons for manual forwarding settings."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "toggle_manual_order":
        current_order = context.user_data.get('manual_order', 'normal')
        new_order = 'reverse' if current_order == 'normal' else 'normal'
        context.user_data['manual_order'] = new_order

        order_text = "Reverse" if new_order == 'reverse' else "Normal"
        topic_id = context.user_data.get('manual_topic_id')

        keyboard = [
            [InlineKeyboardButton(f"🔄 Order: {order_text.capitalize()}", callback_data="toggle_manual_order")],
            [InlineKeyboardButton("✅ Done / Start Manual Dispatch", callback_data="trigger_manual_don")]
        ]
        await query.edit_message_text(
            f"📝 **Manual Forwarding Mode Initialized**\n"
            f"• Destination Topic ID: `{topic_id}`\n"
            f"• Current Order: `{order_text}`\n\n"
            f"Send files to the bot one by one. Click **Done** when finished.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif data == "trigger_manual_don":
        await execute_manual_forward(query.message, context)


async def execute_manual_forward(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Processes and dispatches manually collected files anonymously into the destination topic."""
    files = context.user_data.get('manual_files', [])
    if not files:
        await message.reply_text("⚠️ No files have been saved yet! Send files first.", parse_mode="Markdown")
        return

    order = context.user_data.get('manual_order', 'normal')
    topic_id = context.user_data.get('manual_topic_id')

    if order == 'reverse':
        files.reverse()

    status_msg = await message.reply_text(f"🚀 Starting anonymous dispatch of `{len(files)}` files...")

    forwarded = 0
    errors = 0
    bot = message.get_bot()

    for item in files:
        chat_id = item['chat_id']
        msg_id = item['msg_id']
        try:
            kwargs = {
                "chat_id": TARGET_GROUP_ID,
                "from_chat_id": chat_id,
                "message_id": msg_id
            }
            if topic_id:
                kwargs["message_thread_id"] = int(topic_id)

            await bot.copy_message(**kwargs)
            forwarded += 1
            await asyncio.sleep(0.5)
        except Exception as err:
            logger.error(f"Error copying manual file {msg_id}: {err}")
            errors += 1

    context.user_data['manual_files'] = []
    context.user_data['step'] = None

    await status_msg.edit_text(
        f"✨ **Manual Forwarding Completed!**\n\n"
        f"✅ Forwarded Anonymously: `{forwarded}`\n"
        f"❌ Errors: `{errors}`",
        parse_mode="Markdown"
    )


async def forward_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Standalone /forward command for manual custom file routing."""
    context.user_data['manual_order'] = 'normal'
    context.user_data['manual_topic_id'] = None
    context.user_data['manual_files'] = []
    context.user_data['step'] = 'collecting_manual_files'

    keyboard = [
        [InlineKeyboardButton("🔄 Order: Normal", callback_data="toggle_manual_order")],
        [InlineKeyboardButton("📌 Set Topic ID", callback_data="set_manual_topic")],
        [InlineKeyboardButton("✅ Done / Start", callback_data="trigger_manual_don")]
    ]
    await update.message.reply_text(
        "📦 **Custom Manual Forwarding Mode**\n\n"
        "Send files to the bot one by one, then click **Done**.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /cancel command to abort active processing and empty the pending queue."""
    global current_task_cancel_event
    current_task_cancel_event.set()

    cleared_count = 0
    while not task_queue.empty():
        try:
            task_queue.get_nowait()
            task_queue.task_done()
            cleared_count += 1
        except Exception:
            break

    context.user_data['step'] = None
    context.user_data['manual_files'] = []

    await update.message.reply_text(
        f"🛑 **Cancellation Triggered!**\n"
        f"• Active process aborted.\n"
        f"• Cleared `{cleared_count}` pending tasks from the quest queue.",
        parse_mode="Markdown"
    )


async def handle_message_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manages multi-step conversational input."""
    global RUNTIME_SESSION_STRING
    user_data = context.user_data
    step = user_data.get('step')

    if step == 'waiting_session_string':
        RUNTIME_SESSION_STRING = update.message.text.strip()
        user_data['step'] = None
        await update.message.reply_text("✅ **Session String successfully saved** for this runtime session!", parse_mode="Markdown")
        return

    elif step == 'collecting_manual_files':
        user_data['manual_files'].append({
            'chat_id': update.effective_chat.id,
            'msg_id': update.message.id
        })
        count = len(user_data['manual_files'])
        await update.message.reply_text(f"📥 File #{count} saved. Send more files or click Done.", parse_mode="Markdown")
        return


class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")


def run_http_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()


async def run_bot():
    # Start the HTTP server to satisfy Render's port binding requirements
    threading.Thread(target=run_http_server, daemon=True).start()

    application = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("send", send_command))
    application.add_handler(CommandHandler("forward", forward_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("add_session", add_session_command))
    application.add_handler(CallbackQueryHandler(mode_selection_callback, pattern="^mode_"))
    application.add_handler(CallbackQueryHandler(manual_callback, pattern="^(toggle_manual_order|trigger_manual_don|set_manual_topic)$"))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message_flow))
    
    await application.initialize()
    await application.start()
    
    # Start background task worker loop for sequential quest execution
    asyncio.create_task(task_worker())
    
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    
    stop_event = asyncio.Event()
    await stop_event.wait()


def main() -> None:
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
