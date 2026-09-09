import os
import asyncio
import logging
from aiohttp import web
from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession

# Setup Comprehensive Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Environment Configuration
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", 8080))

# Initialize Telethon Bot Client
bot = TelegramClient('bot_session', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# Global dynamic user client instance
user = None

# In-memory State Configuration
config = {
    "order": "old_to_new",
    "remove_sender": False,
    "remove_caption": False,
    "backup_group": None
}

def get_settings_ui():
    return [
        [Button.inline(f"🔄 Order: {'Old -> New' if config['order'] == 'old_to_new' else 'New -> Old'}", b"toggle_order")],
        [Button.inline(f"👤 Remove Sender: {'YES (Copy)' if config['remove_sender'] else 'NO (Forward)'}", b"toggle_sender")],
        [Button.inline(f"📝 Caption: {'Removed' if config['remove_caption'] else 'Original'}", b"toggle_caption")]
    ]

@bot.on(events.NewMessage(incoming=True))
async def log_incoming_messages(event):
    chat = await event.get_chat()
    logger.info(f"Received update from chat {chat.id}: {event.text or '[Media/Other]'}")

@bot.on(events.NewMessage(pattern='/start'))
async def cmd_start(event):
    logger.info("Executing /start command handler.")
    try:
        bot_me = await bot.get_me()
        add_link = f"https://t.me/{bot_me.username}?startchannel=true&admin=post_messages+edit_messages+delete_messages+ban_users+invite_users+change_info+pin_messages+manage_video_chats+manage_topics+add_admins"
        
        await event.reply(
            "**Forwarding & Backup Controller (Telethon)**\n\n"
            "1. Use `/login <telethon_string_session>` to connect your user session.\n"
            "2. Use `/setbackup <group_id>` to specify your backup group.\n"
            "3. Configure your settings via `/settings`.",
            buttons=[[Button.url("➕ Add to Channel / Group", add_link)]]
        )
        logger.info("/start response sent successfully.")
    except Exception as e:
        logger.error(f"Error handling /start command: {e}", exc_info=True)

@bot.on(events.NewMessage(pattern='/login'))
async def cmd_login(event):
    global user
    logger.info("Executing /login command handler.")
    args = event.raw_text.split(maxsplit=1)
    if len(args) < 2:
        return await event.reply("Usage: `/login <telethon_string_session>`")
    
    session_str = args[1]
    status = await event.reply("🔄 Verifying Telethon string session...")
    try:
        if user and user.is_connected():
            await user.disconnect()
        
        user = TelegramClient(StringSession(session_str), API_ID, API_HASH)
        await user.connect()
        
        if not await user.is_user_authorized():
            await status.edit("❌ Login failed: Session string is unauthorized or expired.")
            user = None
            return

        me = await user.get_me()
        await status.edit(f"✅ Successfully logged in as `{me.first_name}` (`{me.id}`) via Telethon!")
        logger.info(f"User session successfully authenticated for ID {me.id}")
    except Exception as e:
        logger.error(f"Failed to login user session: {e}", exc_info=True)
        user = None
        await status.edit(f"❌ Login failed: {e}")

@bot.on(events.NewMessage(pattern='/settings'))
async def cmd_settings(event):
    logger.info("Executing /settings command handler.")
    try:
        await event.reply("**⚙️ Configuration Settings**", buttons=get_settings_ui())
    except Exception as e:
        logger.error(f"Error in /settings: {e}", exc_info=True)

@bot.on(events.CallbackQuery(data=b"toggle_order"))
async def toggle_order(event):
    config["order"] = "new_to_old" if config["order"] == "old_to_new" else "old_to_new"
    await event.edit(buttons=get_settings_ui())
    await event.answer("Order updated!")

@bot.on(events.CallbackQuery(data=b"toggle_sender"))
async def toggle_sender(event):
    config["remove_sender"] = not config["remove_sender"]
    await event.edit(buttons=get_settings_ui())
    await event.answer("Sender setting updated!")

@bot.on(events.CallbackQuery(data=b"toggle_caption"))
async def toggle_caption(event):
    config["remove_caption"] = not config["remove_caption"]
    await event.edit(buttons=get_settings_ui())
    await event.answer("Caption setting updated!")

@bot.on(events.NewMessage(pattern='/setbackup'))
async def cmd_set_backup(event):
    logger.info("Executing /setbackup command handler.")
    args = event.raw_text.split(maxsplit=1)
    if len(args) < 2:
        return await event.reply("Usage: `/setbackup <group_id>`")
    try:
        config["backup_group"] = int(args[1])
        await event.reply(f"✅ Backup group set to: `{config['backup_group']}`")
    except ValueError:
        logger.error("Invalid group ID provided for backup.")
        await event.reply("❌ Invalid group ID. Must be an integer.")

@bot.on(events.NewMessage(pattern='/backup'))
async def cmd_backup(event):
    logger.info("Executing /backup command handler.")
    if not user or not user.is_connected():
        return await event.reply("⚠️ **User Session Required:** Send `/login <string_session>` to the bot first.")
    if not config["backup_group"]:
        return await event.reply("⚠️ **Target Missing:** Use `/setbackup <group_id>` first.")
        
    args = event.raw_text.split()
    if len(args) < 2:
        return await event.reply("Usage: `/backup <channel_id>`")
        
    try:
        source_chat = int(args[1])
    except ValueError:
        return await event.reply("Usage: `/backup <channel_id>` (ID must be integer or username)")

    status = await event.reply("🔄 Initializing Telethon backup process...")
    try:
        chat_info = await user.get_entity(source_chat)
        # Create forum topic if the backup group is a forum
        try:
            topic = await user.create_forum_topic(config["backup_group"], title=f"{getattr(chat_info, 'title', 'Backup')} Backup")
            topic_id = topic.id
        except Exception:
            topic_id = None # Fallback if target group is not a forum
            
        await status.edit("📥 Indexing messages...")
        messages = []
        async for msg in user.iter_messages(source_chat):
            messages.append(msg)
            
        if config["order"] == "old_to_new":
            messages.reverse()
            
        await status.edit(f"🚀 Processing {len(messages)} messages...")
        
        for msg in messages:
            try:
                caption = None if config["remove_caption"] else msg.text
                if config["remove_sender"]:
                    await user.send_message(
                        config["backup_group"],
                        message=msg.media or msg.text,
                        file=msg.media,
                        formatting_entities=msg.entities,
                        reply_to=topic_id
                    )
                else:
                    await user.forward_messages(config["backup_group"], messages=msg, reply_to=topic_id)
                await asyncio.sleep(2)
            except Exception as e:
                logger.warning(f"Skipped msg: {e}")
                
        await status.edit("✅ Backup complete.")
    except Exception as e:
        logger.error(f"Error during backup process: {e}", exc_info=True)
        await status.edit(f"❌ Error during backup: {str(e)}")

# Render Web Service Keep-Alive
async def web_server():
    app = web.Application()
    app.router.add_get('/', lambda r: web.Response(text="Bot is running!"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"Web server started on port {PORT}")

async def main():
    logger.info("Starting application services...")
    server_task = asyncio.create_task(web_server())
    
    logger.info("Bot client running via Telethon event loop.")
    await bot.run_until_disconnected()
    
    if user and user.is_connected():
        await user.disconnect()
    server_task.cancel()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped manually by user.")
