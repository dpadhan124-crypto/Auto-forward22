from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Replace 'YOUR_TOKEN_HERE' with the token you got from BotFather
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Define the function that runs when a user sends /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_name = update.effective_user.first_name
    welcome_message = f"Hello {user_name}! Welcome to my bot. How can I help you today?"
    await update.message.reply_text(welcome_message)

if __name__ == "__main__":
    # Build the bot application
    app = ApplicationBuilder().token(TOKEN).build()

    # Register the /start command handler
    app.add_handler(CommandHandler("start", start))

    print("Bot is running... Press Ctrl+C to stop.")
    
    # Start polling for messages
    app.run_polling()
