import asyncio
import os
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import Message

# Load variables from the .env file
load_dotenv()

# --- CONFIGURATION ---
API_ID = 33902690
API_HASH = '08dfcf902b1bec83fef7aaab24c18278'
BOT_TOKEN = '8697814237:AAHGUZ7d_9VM3rnUbMeD0nZEW3zSIj79NxM'
TARGET_BOT = os.getenv("TARGET_BOT", "AudioConverterNewBot")
DELAY_SECONDS = int(os.getenv("DELAY_SECONDS", 20))

# Initialize the main controller bot
bot = Client("ControllerBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Global variables to store the user client session and queue management
user_client = None
file_queue = asyncio.Queue()

@bot.on_message(filters.command("start"))
async def start_cmd(client, message: Message):
    await message.reply_text(
        "👋 Welcome! Send `/addsession <your_session_string>` to connect your user account.\n"
        "After connecting, any media file you send here will be forwarded to the audio converter bot."
    )

@bot.on_message(filters.command("addsession"))
async def add_session_cmd(client, message: Message):
    global user_client
    
    # Extract the session string from the command parameters
    if len(message.command) < 2:
        await message.reply_text("❌ Please provide a session string.\nExample: `/addsession AgAAAA...`")
        return
    
    session_string = message.text.split(None, 1)[1].strip()
    await message.reply_text("🔄 Connecting to user session...")

    try:
        # Initialize the userbot client using the provided session string
        user_client = Client(
            "UserSession",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=session_string
        )
        await user_client.start()
        
        # Get user details to confirm login
        me = await user_client.get_me()
        await message.reply_text(f"✅ Successfully connected as **{me.first_name}** (@{me.username})!")
        
        # Start the background worker queue task
        asyncio.create_task(file_worker(message))
        
    except Exception as e:
        await message.reply_text(f"❌ Failed to connect session. Error: {str(e)}")
        user_client = None

@bot.on_message(filters.document | filters.audio | filters.video | filters.voice)
async def handle_incoming_files(client, message: Message):
    global user_client
    
    if not user_client:
        await message.reply_text("⚠️ Please add a user session first using `/addsession <string>`")
        return

    # Let the user know the file is queued safely
    status_msg = await message.reply_text("📥 File added to queue. Waiting for its turn...")
    
    # Push both the incoming file structure and status message tracking to the worker queue
    await file_queue.put((message, status_msg))

async def file_worker(chat_context: Message):
    """Background worker that processes files sequentially with a delay"""
    global user_client
    
    while True:
        # Retrieve next file out of the queue asynchronously 
        incoming_msg, status_msg = await file_queue.get()
        
        try:
            await status_msg.edit_text("⏳ Downloading file to server...")
            file_path = await incoming_msg.download()
            
            await status_msg.edit_text(f"🚀 Sending to @{TARGET_BOT}... (Will wait {DELAY_SECONDS}s after this)")
            
            # The userbot pushes the file into the target bot's direct messages
            await user_client.send_document(chat_id=TARGET_BOT, document=file_path)
            
            # Clean up disk space by deleting local temporary files
            if os.path.exists(file_path):
                os.remove(file_path)
                
            await status_msg.edit_text(f"✅ Successfully sent to @{TARGET_BOT}! Cooling down...")
            
            # Strict delay pacing applied safely between file cycles
            await asyncio.sleep(DELAY_SECONDS)
            await status_msg.edit_text("✅ Done!")
            
        except Exception as e:
            await status_msg.edit_text(f"❌ Error processing file: {str(e)}")
            
        finally:
            # Inform queue pipeline that individual execution cycle finished
            file_queue.task_done()

if __name__ == "__main__":
    print("🤖 Bot is starting up using environment configurations...")
    bot.run()
