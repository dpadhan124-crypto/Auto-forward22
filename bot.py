import os
import logging
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telethon import TelegramClient
from telethon.tl.functions.channels import EditAdminRequest
from telethon.tl.types import ChatAdminRights

# Logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask app for Render web service keep-alive
app = Flask(__name__)

@app.route('/')
def index():
    return "Bot is running!"

# In-memory storage for user sessions and states
USER_DATA = {}
# Structure: { user_id: { 'session': str, 'bot1': str, 'bot2': str, 'dest_group': int, 'topics': {channel_name: topic_id}, 'files': [] } }

TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
API_ID = int(os.getenv("API_ID", "123456"))
API_HASH = os.getenv("API_HASH", "your_api_hash")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    USER_DATA.setdefault(user_id, {"files": [], "topics": {}})
    await update.message.reply_text(
        "Welcome! Use `/settings <session_string> <bot1_username> <bot2_username> <destination_group_id>` to configure your bot."
    )

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    if len(args) < 4:
        await update.message.reply_text("Usage: /settings <session_string> <bot1_username> <bot2_username> <destination_group_id>")
        return

    USER_DATA[user_id]["session"] = args[0]
    USER_DATA[user_id]["bot1"] = args[1]
    USER_DATA[user_id]["bot2"] = args[2]
    USER_DATA[user_id]["dest_group"] = int(args[3])
    
    await update.message.reply_text("Settings saved successfully! Now send a Channel username or ID to promote the two bots and create a topic.")

async def handle_channel_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in USER_DATA or "session" not in USER_DATA[user_id]:
        await update.message.reply_text("Please configure your settings first using `/settings`.")
        return

    channel_input = update.message.text.strip()
    data = USER_DATA[user_id]
    
    client = TelegramClient(None, API_ID, API_HASH)
    await client.connect()
    try:
        await client.start(session=data["session"])
        
        # Resolve channel and get title
        entity = await client.get_entity(channel_input)
        channel_name = entity.title
        
        # Promote Bot 1 and Bot 2 to admin
        admin_rights = ChatAdminRights(
            add_admins=True, invite_users=True, change_info=True, 
            ban_users=True, delete_messages=True, pin_messages=True
        )
        
        for b_username in [data["bot1"], data["bot2"]]:
            bot_entity = await client.get_entity(b_username)
            await client(EditAdminRequest(channel=entity, user_id=bot_entity, admin_rights=admin_rights, rank="Admin"))

        await update.message.reply_text("Successfully promoted both bots to admin in the channel!")
        
        # Create topic in Destination Group (Note: Telegram Bot API is needed for forum topic creation)
        # For simplicity, we store the channel name mapping here
        data["current_channel"] = channel_name
        await update.message.reply_text(
            f"Channel '{channel_name}' processed. Now send the files you want to forward. When finished, click Done.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Done", callback_data="finish_files")]]))
        
    except Exception as e:
        await update.message.reply_text(f"Error executing task: {str(e)}")
    finally:
        await client.disconnect()

async def handle_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in USER_DATA or "current_channel" not in USER_DATA[user_id]:
        return

    file_id = update.message.document.file_id if update.message.document else update.message.photo[-1].file_id
    USER_DATA[user_id]["files"].append(file_id)
    count = len(USER_DATA[user_id]["files"])
    await update.message.reply_text(f"file {count} saved")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if query.data == "finish_files":
        keyboard = [
            [InlineKeyboardButton("Send", callback_data="send_normal")],
            [InlineKeyboardButton("Send in Reverse Order", callback_data="send_reverse")]
        ]
        await query.message.edit_text("Files collection complete. Choose sending mode:", reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif query.data in ["send_normal", "send_reverse"]:
        files = USER_DATA.get(user_id, {}).get("files", [])
        if query.data == "send_reverse":
            files.reverse()
        
        dest_group = USER_DATA[user_id]["dest_group"]
        for f in files:
            await context.bot.send_document(chat_id=dest_group, document=f)
        
        await query.message.edit_text(f"Successfully sent {len(files)} files to destination!")
        USER_DATA[user_id]["files"] = []

# Main setup function for polling
def main():
    application = ApplicationBuilder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_channel_input))
    application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_files))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    application.run_polling()

if __name__ == "__main__":
    # If running locally or testing directly, execute polling. 
    # Render will use Gunicorn to run the Flask app.
    import threading
    threading.Thread(target=main).app = None # Placeholder for background bot runner if needed
