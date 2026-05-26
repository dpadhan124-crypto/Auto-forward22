# =====================================================================
# STEP 1: MODERN PYTHON EVENT LOOP PATCH
# =====================================================================
import asyncio
import sys

try:
    asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

# =====================================================================
# STEP 2: PACKAGES & CLIENT ROUTINES
# =====================================================================
import os
import threading
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait

# --- HARDCODED TEST CREDENTIALS ---
API_ID = 33902690
API_HASH = '08dfcf902b1bec83fef7aaab24c18278'
BOT_TOKEN = '8697814237:AAERHXm7y28XcNMIkZVlV2ib6K6uGHq-gdY'

TARGET_BOT = "AudioConverterNewBot"
DELAY_SECONDS = 3

# Initialize Main Bot
bot = Client("ControllerBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Global tracker for user session
user_client = None

# =====================================================================
# STEP 3: DUMMY SERVER FOR RENDER WEB SERVICE PORT BINDING
# =====================================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is alive and running!")

def run_health_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    print(f"🌍 Dummy health server listening on port {port} for Render requirements...")
    server.serve_forever()

# =====================================================================
# STEP 4: TELEGRAM BOT COMMANDS
# =====================================================================

@bot.on_message(filters.command("start"))
async def start_cmd(client, message: Message):
    await message.reply_text(
        "👋 Welcome!\n\n"
        "1. Send `/addsession <string>` to authenticate your user account.\n"
        "2. To copy a range from a channel, send:\n"
        "`/copychannel https://t.me/REBORN_IN_MARTIAL_WORLD_uk/1665 https://t.me/REBORN_IN_MARTIAL_WORLD_uk/1698`\n"
        "3. Alternatively, send any media file directly to me, and I will copy it over."
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
        if user_client:
            try:
                await user_client.stop()
            except:
                pass

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


@bot.on_message(filters.command("copychannel"))
async def copy_channel_range_cmd(client, message: Message):
    global user_client
    
    if not user_client:
        await message.reply_text("⚠️ Please hook up a user session first using `/addsession <string>`")
        return

    # Expecting: /copychannel <start_link> <end_link>
    if len(message.command) < 3:
        await message.reply_text("❌ Usage: `/copychannel <start_link> <end_link>`")
        return

    start_link = message.command[1]
    end_link = message.command[2]

    # Regex to extract channel username/id and message id
    pattern = r"t\.me/(?:c/)?([^/]+)/(\d+)"
    start_match = re.search(pattern, start_link)
    end_match = re.search(pattern, end_link)

    if not start_match or not end_match:
        await message.reply_text("❌ Invalid Telegram links provided. Please make sure they match `https://t.me/...` formatting.")
        return

    channel_identifier = start_match.group(1)
    # Handle private channel integer IDs if applicable
    if channel_identifier.isdigit():
        channel_identifier = int(f"-100{channel_identifier}")

    start_id = int(start_match.group(2))
    end_id = int(end_match.group(2))

    if start_id > end_id:
        await message.reply_text("❌ Start ID cannot be greater than End ID.")
        return

    status = await message.reply_text(f"🚀 Batch task started. Copying messages from ID {start_id} to {end_id}...")

    # Iterate through the range of messages
    for msg_id in range(start_id, end_id + 1):
        try:
            # Fetch the message from the source channel via user session
            src_msg = await user_client.get_messages(chat_id=channel_identifier, message_ids=msg_id)
            
            if src_msg and not src_msg.empty:
                await status.edit_text(f"⏳ Pacing execution. Waiting {DELAY_SECONDS}s before sending message ID {msg_id}...")
                await asyncio.sleep(DELAY_SECONDS)
                
                # Using copy_message instead of forward_messages to hide the original source
                await src_msg.copy(chat_id=TARGET_BOT)
                print(f"Copied message ID: {msg_id}")
            else:
                await status.edit_text(f"⏩ Message ID {msg_id} is empty or deleted. Skipping...")
                
        except FloodWait as fw:
            await status.edit_text(f"⚠️ Hit Telegram FloodWait. Sleeping for {fw.value} seconds...")
            await asyncio.sleep(fw.value)
        except Exception as e:
            print(f"Failed to copy message ID {msg_id}: {str(e)}")
            # Fail silently on individual message issues to keep the batch loop running

    await status.edit_text(f"✅ Finished! Successfully copied available messages from {start_id} to {end_id} cleanly.")


# =====================================================================
# STEP 5: DIRECT FILE COPY ROUTINE WITH PACING DELAY (FOR IN-BOT DMs)
# =====================================================================

@bot.on_message(filters.document | filters.audio | filters.video | filters.voice)
async def handle_copy_directly(client, message: Message):
    global user_client
    
    if not user_client:
        await message.reply_text("⚠️ Please hook up a user session first using `/addsession <string>`")
        return

    status = await message.reply_text(f"⏳ Standby... Pacing execution for {DELAY_SECONDS} seconds.")
    
    try:
        await asyncio.sleep(DELAY_SECONDS)
        await status.edit_text(f"🚀 Copying cleanly to @{TARGET_BOT}...")
        
        # copy_message scrubs the forwarded header metadata 
        await message.copy(chat_id=TARGET_BOT)
        
        await status.edit_text("✅ File copied successfully!")

    except Exception as e:
        await status.edit_text(f"❌ Failed to transfer message: {str(e)}")

# =====================================================================
# STEP 6: APPLICATION RUNNER
# =====================================================================
if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    print("🤖 Bot application runtime triggered. Monitoring incoming updates...")
    bot.run()
