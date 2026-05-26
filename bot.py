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
from http.server import BaseHTTPRequestHandler, HTTPServer
from pyrogram import Client, filters
from pyrogram.types import Message

# --- HARDCODED TEST CREDENTIALS ---
API_ID = 33902690
API_HASH = '08dfcf902b1bec83fef7aaab24c18278'
# Updated with your new token:
BOT_TOKEN = '8697814237:AAERHXm7y28XcNMIkZVlV2ib6K6uGHq-gdY'

TARGET_BOT = "AudioConverterNewBot"
DELAY_SECONDS = 30

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
    # Render automatically passes the PORT environment variable
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

# =====================================================================
# STEP 5: DIRECT FILE FORWARDING ROUTINE WITH PACING DELAY
# =====================================================================

@bot.on_message(filters.document | filters.audio | filters.video | filters.voice)
async def handle_forward_directly(client, message: Message):
    global user_client
    
    if not user_client:
        await message.reply_text("⚠️ Please hook up a user session first using `/addsession <string>`")
        return

    status = await message.reply_text(f"⏳ Standby... Pacing execution for {DELAY_SECONDS} seconds.")
    
    try:
        await asyncio.sleep(DELAY_SECONDS)
        await status.edit_text(f"🚀 Forwarding cleanly to @{TARGET_BOT}...")
        
        await user_client.forward_messages(
            chat_id=TARGET_BOT,
            from_chat_id=message.chat.id,
            message_ids=message.id
        )
        
        await status.edit_text("✅ File forwarded successfully!")

    except Exception as e:
        await status.edit_text(f"❌ Failed to transfer message: {str(e)}")

# =====================================================================
# STEP 6: APPLICATION RUNNER
# =====================================================================
if __name__ == "__main__":
    # Start the web port binder thread so Render Web Service doesn't panic
    threading.Thread(target=run_health_server, daemon=True).start()
    
    print("🤖 Bot application runtime triggered. Monitoring incoming updates...")
    bot.run()

