# =====================================================================
# STEP 1: MODERN PYTHON EVENT LOOP PATCH (CRITICAL FOR RENDER DEPLOYS)
# =====================================================================
import asyncio
import sys

# Forces an active asyncio loop into the thread to stop older Pyrogram 
# source structures from crashing instantly on startup.
try:
    asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

# =====================================================================
# STEP 2: PACKAGES & CLIENT ROUTINES
# =====================================================================
import os
from pyrogram import Client, filters
from pyrogram.types import Message

# --- HARDCODED TEST CREDENTIALS ---
API_ID = 33902690
API_HASH = '08dfcf902b1bec83fef7aaab24c18278'
BOT_TOKEN = '8697814237:AAERHXm7y28XcNMIkZVlV2ib6K6uGHq-gdY'

TARGET_BOT = "AudioConverterNewBot"
DELAY_SECONDS = 20

# Initialize Main Bot
bot = Client("ControllerBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Global tracker for user session
user_client = None

# =====================================================================
# STEP 3: TELEGRAM BOT COMMANDS
# =====================================================================

@bot.on_message(filters.command("start"))
async def start_cmd(client, message: Message):
    await message.reply_text(
        "👋 Welcome!\n\n"
        "1. Send `/addsession <string>` to authenticate your user account.\n"
        "2. Send any media file directly to this bot.\n"
        "3. Your user account will forward it to @AudioConverterNewBot with a 20-second delay."
    )

@bot.on_message(filters.command("addsession"))
async def add_session_cmd(client, message: Message):
    global user_client
    
    if len(message.command) < 2:
        await message.reply_text("❌ Please provide a session string.\nExample: `/addsession AgAAAA...`")
        return
    
    session_string = message.text.split(None, 1)[1].strip()
    status = await message.reply_text("🔄 Connecting user account session...")

    try:
        # Tear down preexisting user clients if they are running
        if user_client:
            try:
                await user_client.stop()
            except:
                pass

        # Spin up new user session 
        user_client = Client(
            "UserSession",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=session_string
        )
        await user_client.start()
        
        me = await user_client.get_me()
        await status.edit_text(f"✅ Successfully connected as **{me.first_name}** (@{me.username})!")
        
    except Exception as e:
        await status.edit_text(f"❌ Connection failed. Error: {str(e)}")
        user_client = None

# =====================================================================
# STEP 4: DIRECT FILE FORWARDING ROUTINE WITH PACING DELAY
# =====================================================================

@bot.on_message(filters.document | filters.audio | filters.video | filters.voice)
async def handle_forward_directly(client, message: Message):
    global user_client
    
    if not user_client:
        await message.reply_text("⚠️ Please hook up a user session first using `/addsession <string>`")
        return

    # Post an inline update to see execution tracking in real-time
    status = await message.reply_text(f"⏳ Standby... Pacing execution for {DELAY_SECONDS} seconds.")
    
    try:
        # Pacing throttle execution block
        await asyncio.sleep(DELAY_SECONDS)

        await status.edit_text(f"🚀 Forwarding cleanly to @{TARGET_BOT}...")
        
        # User client replicates the original message via direct chat forwarding pipelines
        await user_client.forward_messages(
            chat_id=TARGET_BOT,
            from_chat_id=message.chat.id,
            message_ids=message.id
        )
        
        await status.edit_text("✅ File forwarded successfully!")

    except Exception as e:
        await status.edit_text(f"❌ Failed to transfer message: {str(e)}")

# =====================================================================
# STEP 5: APPLICATION RUNNER
# =====================================================================
if __name__ == "__main__":
    print("🤖 Application runtime triggered. Monitoring incoming updates...")
    bot.run()
