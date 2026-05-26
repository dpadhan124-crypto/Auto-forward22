import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message

# --- CONFIGURATION ---
API_ID = 33902690
API_HASH = '08dfcf902b1bec83fef7aaab24c18278'
BOT_TOKEN = '8697814237:AAHGUZ7d_9VM3rnUbMeD0nZEW3zSIj79NxM'

TARGET_BOT = "AudioConverterNewBot"
DELAY_SECONDS = 20

# Initialize the main bot
bot = Client("ControllerBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Global variable to store your user session
user_client = None

@bot.on_message(filters.command("start"))
async def start_cmd(client, message: Message):
    await message.reply_text(
        "👋 Hi! Use `/addsession <your_session_string>` to connect.\n"
        "After that, any file you send here will be forwarded directly via your user account with a 20s delay."
    )

@bot.on_message(filters.command("addsession"))
async def add_session_cmd(client, message: Message):
    global user_client
    
    if len(message.command) < 2:
        await message.reply_text("❌ Missing session string! Example: `/addsession AgAAAA...`")
        return
    
    session_string = message.text.split(None, 1)[1].strip()
    status = await message.reply_text("🔄 Connecting to your user account...")

    try:
        if user_client:
            try: await user_client.stop()
            except: pass

        user_client = Client("UserSession", api_id=API_ID, api_hash=API_HASH, session_string=session_string)
        await user_client.start()
        
        me = await user_client.get_me()
        await status.edit_text(f"✅ Connected as **{me.first_name}** (@{me.username})!")
        
    except Exception as e:
        await status.edit_text(f"❌ Connection failed: {str(e)}")
        user_client = None

@bot.on_message(filters.document | filters.audio | filters.video | filters.voice)
async def handle_forward_directly(client, message: Message):
    global user_client
    
    if not user_client:
        await message.reply_text("⚠️ Please link your account first using `/addsession`")
        return

    # 1. Notify the user that the delay timer has started
    status = await message.reply_text(f"⏳ Waiting {DELAY_SECONDS} seconds before forwarding...")
    
    try:
        # 2. Wait exactly 20 seconds
        await asyncio.sleep(DELAY_SECONDS)

        # 3. Update status and forward the message as a user
        await status.edit_text(f"🚀 Forwarding to @{TARGET_BOT}...")
        
        # user_client forwards the exact message to the target bot using its message ID
        await user_client.forward_messages(
            chat_id=TARGET_BOT,
            from_chat_id=message.chat.id,
            message_ids=message.id
        )
        
        await status.edit_text("✅ Message successfully forwarded!")

    except Exception as e:
        await status.edit_text(f"❌ Error while forwarding: {str(e)}")

if __name__ == "__main__":
    print("🤖 Direct Forward Bot is running...")
    bot.run()
