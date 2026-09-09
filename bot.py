import os
import asyncio
import logging
from aiohttp import web
from pyrogram import Client, filters, idle
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

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
SESSION_STRING = os.environ.get("SESSION_STRING", "")
PORT = int(os.environ.get("PORT", 8080))

# Initialize Pyrogram Clients (Compatible with Pyrogram v2 / Pyrofork)
bot = Client(
    "bot_session",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

user = Client(
    "user_session",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING
) if SESSION_STRING else None

# In-memory State Configuration
config = {
    "order": "old_to_new",
    "remove_sender": False,
    "remove_caption": False,
    "backup_group": None
}

def get_settings_ui():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔄 Order: {'Old -> New' if config['order'] == 'old_to_new' else 'New -> Old'}", callback_data="toggle_order")],
        [InlineKeyboardButton(f"👤 Remove Sender: {'YES (Copy)' if config['remove_sender'] else 'NO (Forward)'}", callback_data="toggle_sender")],
        [InlineKeyboardButton(f"📝 Caption: {'Removed' if config['remove_caption'] else 'Original'}", callback_data="toggle_caption")]
    ])

@bot.on_message(filters.incoming)
async def log_incoming_messages(client, message):
    logger.info(f"Received update from chat {message.chat.id} ({message.chat.type}): {message.text or '[Media/Other]'}")

@bot.on_message(filters.command("start"))
async def cmd_start(client, message):
    logger.info("Executing /start command handler.")
    try:
        bot_me = await client.get_me()
        add_link = f"https://t.me/{bot_me.username}?startchannel=true&admin=post_messages+edit_messages+delete_messages+ban_users+invite_users+change_info+pin_messages+manage_video_chats+manage_topics+add_admins"
        
        await message.reply(
            "**Forwarding & Backup Controller**\n\n"
            "Configure your backup settings and add the bot to your target channels.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Add to Channel / Group", url=add_link)]])
        )
        logger.info("/start response sent successfully.")
    except Exception as e:
        logger.error(f"Error handling /start command: {e}", exc_info=True)

@bot.on_message(filters.command("settings"))
async def cmd_settings(client, message):
    logger.info("Executing /settings command handler.")
    try:
        await message.reply("**⚙️ Configuration Settings**", reply_markup=get_settings_ui())
    except Exception as e:
        logger.error(f"Error in /settings: {e}", exc_info=True)

@bot.on_callback_query(filters.regex("toggle_"))
async def callback_settings(client, callback_query):
    logger.info(f"Received callback query: {callback_query.data}")
    try:
        action = callback_query.data.split("_")[1]
        
        if action == "order":
            config["order"] = "new_to_old" if config["order"] == "old_to_new" else "old_to_new"
        elif action == "sender":
            config["remove_sender"] = not config["remove_sender"]
        elif action == "caption":
            config["remove_caption"] = not config["remove_caption"]
            
        await callback_query.message.edit_reply_markup(get_settings_ui())
        await callback_query.answer("Settings updated!")
    except Exception as e:
        logger.error(f"Error processing callback query: {e}", exc_info=True)

@bot.on_message(filters.command("setbackup"))
async def cmd_set_backup(client, message):
    logger.info("Executing /setbackup command handler.")
    if len(message.command) < 2:
        return await message.reply("Usage: `/setbackup <group_id>`")
    try:
        config["backup_group"] = int(message.command[1])
        await message.reply(f"✅ Backup topic-enabled group set to: `{config['backup_group']}`")
    except ValueError:
        logger.error("Invalid group ID provided for backup.")
        await message.reply("❌ Invalid group ID. Must be an integer.")

@bot.on_message(filters.command("backup"))
async def cmd_backup(client, message):
    logger.info("Executing /backup command handler.")
    if not user:
        return await message.reply("⚠️ **User Session Required:** Add `SESSION_STRING` in Render environments to run backups.")
    if not config["backup_group"]:
        return await message.reply("⚠️ **Target Missing:** Use `/setbackup <group_id>` first.")
        
    try:
        source_chat = int(message.command[1])
    except (IndexError, ValueError):
        return await message.reply("Usage: `/backup <channel_id>`")

    status = await message.reply("🔄 Initializing user session...")
    try:
        chat_info = await user.get_chat(source_chat)
        topic = await user.create_forum_topic(config["backup_group"], f"{chat_info.title} Backup")
        topic_id = topic.id
        
        await status.edit("📥 Indexing messages...")
        messages = []
        async for msg in user.get_chat_history(source_chat):
            messages.append(msg)
            
        if config["order"] == "old_to_new":
            messages.reverse()
            
        await status.edit(f"🚀 Processing {len(messages)} messages to topic ID: {topic_id}...")
        
        for msg in messages:
            caption = None if config["remove_caption"] else (msg.caption or msg.text)
            try:
                if config["remove_sender"]:
                    await msg.copy(config["backup_group"], message_thread_id=topic_id, caption=caption)
                else:
                    await msg.forward(config["backup_group"], message_thread_id=topic_id)
                await asyncio.sleep(2)
            except Exception as e:
                logger.warning(f"Skipped msg {msg.id}: {e}")
                
        await status.edit("✅ Backup complete.")
    except Exception as e:
        logger.error(f"Error during backup process: {e}", exc_info=True)
        await status.edit(f"❌ Error during backup: {str(e)}")

@bot.on_message(filters.command("forward_topic"))
async def cmd_fwd_topic(client, message):
    logger.info("Executing /forward_topic command handler.")
    if len(message.command) < 4:
        return await message.reply("Usage: `/forward_topic <source_id> <target_id> <topic_id>`")
    
    try:
        source_id, target_id, topic_id = map(int, message.command[1:4])
    except ValueError:
        return await message.reply("❌ IDs must be valid integers.")

    status = await message.reply("🔄 Forwarding latest messages to topic...")
    try:
        async for msg in client.get_chat_history(source_id, limit=50):
            if config["remove_sender"]:
                await msg.copy(target_id, message_thread_id=topic_id)
            else:
                await msg.forward(target_id, message_thread_id=topic_id)
            await asyncio.sleep(1.5)
        await status.edit("✅ Topic forward complete.")
    except Exception as e:
        logger.error(f"Error in forward_topic: {e}", exc_info=True)
        await status.edit(f"❌ Error: {e}")

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
    
    try:
        await bot.start()
        logger.info("Bot client started successfully.")
    except Exception as e:
        logger.critical(f"Failed to start bot client: {e}", exc_info=True)
        return

    if user:
        try:
            await user.start()
            logger.info("User client started successfully.")
        except Exception as e:
            logger.error(f"Failed to start user client: {e}", exc_info=True)
            
    logger.info("Core systems online and listening for updates.")
    await idle()
    
    logger.info("Shutting down clients...")
    await bot.stop()
    if user:
        await user.stop()
    server_task.cancel()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped manually by user.")
