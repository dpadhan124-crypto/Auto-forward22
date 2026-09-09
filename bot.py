import logging
import asyncio
import time
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, filters
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
    """Processes a single task securely using Telethon userbot session to bypass private channel blocks."""
    global current_task_cancel_event
    current_task_cancel_event.clear()

    status_msg = task_data['status_msg']
    source_channel_str = task_data['source_channel_str']
    reverse_order = task_data['reverse_order']

    session_to_use = RUNTIME_SESSION_STRING or os.environ.get("SESSION_STRING", "")
    client = TelegramClient(StringSession(session_to_use), API_ID, API_HASH)

    async with client:
        try:
            await status_msg.edit_text("⚙️ Setting up bots, creating forum topic, and indexing files...")
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
            f"🚀 **Forwarding Task Started**\n\n"
            f"📊 Progress: [░░░░░░░░░░] 0%\n"
            f"📁 Total Files: {total_files}\n"
            f"✅ Forwarded: 0\n"
            f"❌ Errors: 0\n"
            f"⏳ Remaining Time: Calculating...",
            parse_mode="Markdown"
        )

        try:
            target_entity = await client.get_entity(TARGET_GROUP_ID)
        except Exception as e:
            await status_msg.edit_text(f"❌ Failed to resolve target group: {e}")
            return

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
            while not success:
                try:
                    await client.forward_messages(
                        entity=target_entity,
                        messages=msg_id,
                        from_peer=channel_entity,
                        reply_to=int(message_thread_id) if message_thread_id else None
                    )
                    forwarded_files += 1
                    success = True
                except FloodWaitError as fwe:
                    logger.warning(f"Telethon FloodWait during forwarding: sleeping for {fwe.seconds} seconds")
                    await asyncio.sleep(fwe.seconds + 2)
                except Exception as err:
                    last_error_reason = f"`{type(err).__name__}: {str(err)}`"
                    logger.error(f"Error forwarding message {msg_id}: {err}")
                    error_files += 1
                    break

            if idx % 5 == 0 or idx == total_files:
                elapsed = time.time() - start_time
                avg_time = elapsed / idx if idx > 0 else 0
                eta = int(avg_time * (total_files - idx))
                eta_str = f"{eta // 60}m {eta % 60}s" if eta > 60 else f"{eta}s"

                try:
                    await status_msg.edit_text(
                        f"🚀 **Forwarding Task in Progress**\n\n"
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
            f"✨ **Forwarding Task Completed!**\n\n"
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
            await process_forwarding_task(task_data)
        except Exception as e:
            logger.error(f"Error in task worker queue: {e}")
        finally:
            task_queue.task_done()


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /start command."""
    await update.message.reply_text(
        "👋 Welcome! I am your automated forwarding and management bot with Quest Queue & Error Diagnostics.\n\n"
        "Commands:\n"
        "• `/add_session` - Save your Telethon session string\n"
        "• `/send {channel_id} [r]` - Add forwarding task to queue\n"
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

    await update.message.reply_text(
        f"🛑 **Cancellation Triggered!**\n"
        f"• Active process aborted.\n"
        f"• Cleared `{cleared_count}` pending tasks from the quest queue.",
        parse_mode="Markdown"
    )


async def send_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /send command by adding tasks into the sequential quest queue."""
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

    queue_position = task_queue.qsize() + 1
    status_msg = await update.message.reply_text(
        f"📋 Task added to Quest Queue!\n"
        f"📌 Position in Queue: `{queue_position}`\n"
        f"⏳ Waiting for previous tasks to finish...",
        parse_mode="Markdown"
    )

    task_data = {
        'source_channel_str': source_channel_str,
        'reverse_order': reverse_order,
        'status_msg': status_msg
    }

    await task_queue.put(task_data)


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
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("add_session", add_session_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message_flow))
    
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
