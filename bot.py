import logging
import asyncio
import time
import os
from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler, filters
from telethon import TelegramClient
from telethon.sessions import StringSession
import telethon.tl.functions.channels

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- CONFIGURATION (Render Environment Safe) ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
TARGET_GROUP_ID = -1004440356312


def generate_progress_bar(completed, total):
    """Generates a visual progress bar string."""
    percentage = (completed / total) if total > 0 else 0
    filled = int(round(10 * percentage))
    bar = "█" * filled + "░" * (10 - filled)
    return f"[{bar}] {int(percentage * 100)}%"

async def promote_bots_telethon(channel_input, bot1_username, bot2_username):
    """Promotes both specified bot usernames with respective permission levels using Telethon."""
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    async with client:
        if channel_input.startswith("-") or channel_input.isdigit():
            channel_input = int(channel_input)

        channel = await client.get_entity(channel_input)

        # Clean usernames
        b1 = bot1_username.strip().replace("@", "")
        b2 = bot2_username.strip().replace("@", "")

        # 1. Invite and promote Bot 1 (All Permissions including Stories & Admins)
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

        # 2. Invite and promote Bot 2 (Read, Post, Edit, Delete permissions)
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

        return channel.id

async def send_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /send command, asks for the 2 bot usernames interactively via chat, and processes queue."""
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/send {source_channel_id} [r]`", parse_mode="Markdown")
        return

    source_channel_str = args[0]
    reverse_order = len(args) > 1 and args[1].lower() == 'r'

    await update.message.reply_text("🤖 Please send the **Username of Bot 1** (Full Permissions):", parse_mode="Markdown")
    
    # Simple Conversation flow helper using context user data
    context.user_data['step'] = 'waiting_bot1'
    context.user_data['source_channel'] = source_channel_str
    context.user_data['reverse'] = reverse_order

async def handle_message_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manages multi-step conversational input for bot usernames required on Render."""
    user_data = context.user_data
    step = user_data.get('step')

    if step == 'waiting_bot1':
        user_data['bot1'] = update.message.text
        user_data['step'] = 'waiting_bot2'
        await update.message.reply_text("🤖 Got it. Now send the **Username of Bot 2** (Forwarder):", parse_mode="Markdown")
        return

    elif step == 'waiting_bot2':
        user_data['bot2'] = update.message.text
        user_data['step'] = None
        
        channel_str = user_data.get('source_channel')
        reverse_order = user_data.get('reverse')
        bot1 = user_data.get('bot1')
        bot2 = update.message.text

        status_msg = await update.message.reply_text("⚙️ Promoting bots via userbot and indexing files...")

        try:
            source_chat_id = await promote_bots_telethon(channel_str, bot1, bot2)
        except Exception as e:
            await status_msg.edit_text(f"❌ Failed to promote bots: {e}")
            return

        # Fetch message IDs
        client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
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
                await bot.copy_message(
                    chat_id=TARGET_GROUP_ID,
                    from_chat_id=source_chat_id,
                    message_id=msg_id
                )
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
            parse_mode="Markdown"
        )

def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("send", send_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message_flow))
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
