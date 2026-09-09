import logging
import asyncio
import time
import os
from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, filters
from telethon import TelegramClient
from telethon.sessions import StringSession
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
BOT_1_USERNAME = "Dps_storiesbot"   # Full permissions including Stories & Admins
BOT_2_USERNAME = "Testdp112232bot"  # Forwarder / message copying bot

# Runtime storage for session string if added via command
RUNTIME_SESSION_STRING = os.environ.get("SESSION_STRING", "")


def generate_progress_bar(completed, total):
    """Generates a visual progress bar string."""
    percentage = (completed / total) if total > 0 else 0
    filled = int(round(10 * percentage))
    bar = "█" * filled + "░" * (10 - filled)
    return f"[{bar}] {int(percentage * 100)}%"


async def setup_bots_and_topic_telethon(channel_input, target_group_id):
    """Promotes predefined bots, fetches channel title, and creates a forum topic in the target group."""
    session_to_use = RUNTIME_SESSION_STRING or os.environ.get("SESSION_STRING", "")
    client = TelegramClient(StringSession(session_to_use), API_ID, API_HASH)
    
    async with client:
        if channel_input.startswith("-") or channel_input.isdigit():
            channel_input = int(channel_input)

        channel = await client.get_entity(channel_input)
        channel_title = getattr(channel, 'title', f"Channel {channel.id}")

        # Clean usernames
        b1 = BOT_1_USERNAME.strip().replace("@", "")
        b2 = BOT_2_USERNAME.strip().replace("@", "")

        # 1. Invite and promote Bot 1 (Dps_storiesbot)
        try:
            await client(telethon.tl.functions.channels.InviteToChannelRequest(channel=channel, users=[b1]))
        except Exception:
            pass
        
        await client.edit_admin(
            entity=channel,
            user=b1,
            change_info=True, post_messages=True, edit_messages=True,
            delete_messages=True, ban_users=True, invite_users=True,
            pin_messages=True, add_admins=True, anonymous=False,
            manage_call=True, manage_topics=True, 
            post_stories=True, edit_stories=True, delete_stories=True
        )

        # 2. Invite and promote Bot 2 (Testdp112232bot)
        try:
            await client(telethon.tl.functions.channels.InviteToChannelRequest(channel=channel, users=[b2]))
        except Exception:
            pass

        await client.edit_admin(
            entity=channel,
            user=b2,
            post_messages=True,
            edit_messages=True,
            delete_messages=True
        )

        # 3. Create a forum topic in the target group named after the source channel title
        thread_id = None
        try:
            result = await client(telethon.tl.functions.messages.CreateForumTopicRequest(
                peer=target_group_id,
                title=channel_title
            ))
            for update in result.updates:
                if isinstance(update, telethon.tl.types.UpdateMessageService) and isinstance(update.action, telethon.tl.types.MessageActionTopicCreate):
                    thread_id = update.id
                    break
        except Exception as e:
            logger.error(f"Failed to create forum topic via Telethon: {e}")

        return channel.id, thread_id


async def add_session_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /add_session command to initiate writing a session string."""
    await update.message.reply_text(
        "🔑 Please send your **Telethon Session String** in the next message:",
        parse_mode="Markdown"
    )
    context.user_data['step'] = 'waiting_session_string'


async def send_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /send command utilizing pre-defined bots and queue processing."""
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

    status_msg = await update.message.reply_text("⚙️ Setting up bots (@Dps_storiesbot & @Testdp112232bot), creating forum topic, and indexing files...")

    try:
        source_chat_id, message_thread_id = await setup_bots_and_topic_telethon(source_channel_str, TARGET_GROUP_ID)
    except Exception as e:
        await status_msg.edit_text(f"❌ Setup failed: {e}")
        return

    # Fetch message IDs using Telethon client
    client = TelegramClient(StringSession(session_to_use), API_ID, API_HASH)
    message_ids = []
    async with client:
        channel_entity = await client.get_entity(source_chat_id)
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

    bot = context.bot
    for idx, msg_id in enumerate(message_ids, start=1):
        try:
            kwargs = {
                "chat_id": TARGET_GROUP_ID,
                "from_chat_id": source_chat_id,
                "message_id": msg_id
            }
            if message_thread_id:
                kwargs["message_thread_id"] = message_thread_id

            await bot.copy_message(**kwargs)
            forwarded_files += 1
        except Exception as err:
            logger.error(f"Error forwarding message {msg_id}: {err}")
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
        parse_Mode="Markdown"
    )


async def handle_message_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manages multi-step conversational input specifically for session configuration."""
    global RUNTIME_SESSION_STRING
    user_data = context.user_data
    step = user_data.get('step')

    if step == 'waiting_session_string':
        RUNTIME_SESSION_STRING = update.message.text.strip()
        user_data['step'] = None
        await update.message.reply_text("✅ **Session String successfully saved** for this runtime session!", parse_mode="Markdown")
        return


async def run_bot():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("send", send_command))
    application.add_handler(CommandHandler("add_session", add_session_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message_flow))
    
    await application.initialize()
    await application.start()
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
