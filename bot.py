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

# Runtime storage for session string and task queue
RUNTIME_SESSION_STRING = os.environ.get("SESSION_STRING", "")
task_queue = asyncio.Queue()


def generate_progress_bar(completed, total):
    """Generates a visual progress bar string."""
    percentage = (completed / total) if total > 0 else 0
    filled = int(round(10 * percentage))
    bar = "█" * filled + "░" * (10 - filled)
    return f"[{bar}] {int(percentage * 100)}%"


async def setup_bots_and_topic_telethon(channel_input, target_group_id):
    """Promotes predefined bots with FloodWait safety, fetches channel entity, and creates a forum topic."""
    session_to_use = RUNTIME_SESSION_STRING or os.environ.get("SESSION_STRING", "")
    client = TelegramClient(StringSession(session_to_use), API_ID, API_HASH)
    
    async with client:
        if isinstance(channel_input, str):
            channel_input = channel_input.strip()
            if channel_input.startswith("-") or channel_input.isdigit():
                channel_input = int(channel_input)

        try:
            channel = await client.get_entity(channel_input)
        except FloodWaitError as fwe:
            logger.warning(f"FloodWait on get_entity: sleeping for {fwe.seconds}s")
            await asyncio.sleep(fwe.seconds + 2)
            channel = await client.get_entity(channel_input)
        except Exception as e:
            raise ValueError(f"Could not resolve channel ID/username '{channel_input}': {e}")

        channel_title = getattr(channel, 'title', f"Channel {getattr(channel, 'id', 'Unknown')}")

        b1 = BOT_1_USERNAME.strip().replace("@", "")
        b2 = BOT_2_USERNAME.strip().replace("@", "")

        # 1. Invite and promote Bot 1 (Dps_storiesbot)
        try:
            await client(telethon.tl.functions.channels.InviteToChannelRequest(channel=channel, users=[b1]))
        except FloodWaitError as fwe:
            await asyncio.sleep(fwe.seconds + 2)
            await client(telethon.tl.functions.channels.InviteToChannelRequest(channel=channel, users=[b1]))
        except Exception:
            pass
        
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
        except FloodWaitError as fwe:
            await asyncio.sleep(fwe.seconds + 2)
            await client.edit_admin(
                entity=channel, user=b1, change_info=True, post_messages=True, 
                edit_messages=True, delete_messages=True, ban_users=True, 
                invite_users=True, pin_messages=True, add_admins=True, 
                anonymous=False, manage_call=True
            )
        except Exception as e:
            logger.warning(f"Notice regarding Bot 1 promotion: {e}")

        # 2. Invite and promote Bot 2 (Testdp112232bot)
        try:
            await client(telethon.tl.functions.channels.InviteToChannelRequest(channel=channel, users=[b2]))
        except FloodWaitError as fwe:
            await asyncio.sleep(fwe.seconds + 2)
            await client(telethon.tl.functions.channels.InviteToChannelRequest(channel=channel, users=[b2]))
        except Exception:
            pass

        try:
            await client.edit_admin(
                entity=channel,
                user=b2,
                post_messages=True,
                edit_messages=True,
                delete_messages=True
            )
        except FloodWaitError as fwe:
            await asyncio.sleep(fwe.seconds + 2)
            await client.edit_admin(
                entity=channel, user=b2, post_messages=True,
                edit_messages=True, delete_messages=True
            )
        except Exception as e:
            logger.warning(f"Notice regarding Bot 2 promotion: {e}")

        # 3. Create a forum topic in the target group
        thread_id = None
        try:
            result = await client(telethon.tl.functions.channels.CreateForumTopicRequest(
                channel=target_group_id,
                title=channel_title
            ))
            for update in result.updates:
                if isinstance(update, telethon.tl.types.UpdateMessageService) and isinstance(update.action, telethon.tl.types.MessageActionTopicCreate):
                    thread_id = update.id
                    break
        except FloodWaitError as fwe:
            await asyncio.sleep(fwe.seconds + 2)
        except Exception as e:
            logger.error(f"Failed to create forum topic via Telethon: {e}")

        return channel, thread_id


async def process_forwarding_task(task_data):
    """Processes a single task from the queue with FloodWait protection and real-time updates."""
    update = task_data['update']
    source_channel_str = task_data['source_channel_str']
    reverse_order = task_data['reverse_order']
    status_msg = task_data['status_msg']

    session_to_use = RUNTIME_SESSION_STRING or os.environ.get("SESSION_STRING", "")

    try:
        await status_msg.edit_text("⚙️ Setting up bots, creating forum topic, and indexing files...")
        channel_entity, message_thread_id = await setup_bots_and_topic_telethon(source_channel_str, TARGET_GROUP_ID)
    except Exception as e:
        await status_msg.edit_text(f"❌ Setup failed: {e}")
        return

    # Fetch message IDs using Telethon client with FloodWait protection
    client = TelegramClient(StringSession(session_to_use), API_ID, API_HASH)
    message_ids = []
    
    async with client:
        try:
            async for message in client.iter_messages(channel_entity):
                message_ids.append(message.id)
        except FloodWaitError as fwe:
            logger.warning(f"FloodWait during iteration: sleeping for {fwe.seconds}s")
            await asyncio.sleep(fwe.seconds + 2)
            async for message in client.iter_messages(channel_entity):
                message_ids.append(message.id)

    if reverse_order:
        message_ids.reverse()

    total_files = len(message_ids)
    forwarded_files = 0
    error_files = 0
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

    bot = update.get_bot()
    for idx, msg_id in enumerate(message_ids, start=1):
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
                    kwargs["message_thread_id"] = message_thread_id

                await bot.copy_message(**kwargs)
                forwarded_files += 1
                success = True
            except Exception as err:
                # Check for Telegram Bot API FloodWait or rate limit error strings
                err_str = str(err).lower()
                if "flood" in err_str or "retry after" in err_str:
                    import re
                    match = re.search(r"retry after (\d+)", err_str)
                    sleep_time = int(match.group(1)) if match else 15
                    logger.warning(f"Telegram Bot API FloodWait: sleeping for {sleep_time}s")
                    await asyncio.sleep(sleep_time + 2)
                    retries -= 1
                else:
                    logger.error(f"Error forwarding message {msg_id}: {err}")
                    error_files += 1
                    break

        if not success and not error_files:
            error_files += 1

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

    await status_msg.edit_text(
        f"✨ **Forwarding Task Completed!**\n\n"
        f"📊 Progress: [██████████] 100%\n"
        f"📁 Total Files: {total_files}\n"
        f"✅ Forwarded: {forwarded_files}\n"
        f"❌ Errors: {error_files}\n"
        f"⏱️ Total Time: {int(time.time() - start_time)}s",
        parse_mode="Markdown"
    )


async def task_worker():
    """Background worker that pulls tasks sequentially from the queue."""
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
        "👋 Welcome! I am your automated forwarding and management bot with Quest Queue support.\n\n"
        "Commands:\n"
        "• `/add_session` - Save your Telethon session string\n"
        "• `/send {channel_id} [r]` - Add forwarding task to the quest queue",
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
        'update': update,
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

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("send", send_command))
    application.add_handler(CommandHandler("add_session", add_session_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message_flow))
    
    await application.initialize()
    await application.start()
    
    # Start background task worker loop for sequential quest execution
    asyncio.create_task(task_worker())
    
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    
    stop_event = asyncio.Event()
    await stop_event.wait()


def main() -> None:
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
