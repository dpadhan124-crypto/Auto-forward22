import os
import logging
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Setup logging to catch connection states and runtime errors
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT = int(os.environ.get("PORT", "8443"))
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_name = update.effective_user.first_name
        welcome_message = f"Hello {user_name}! Welcome to my bot. How can I help you today?"
        await update.message.reply_text(welcome_message)
        logger.info(f"Successfully processed /start for user {update.effective_user.id}")
    except Exception as e:
        logger.error(f"Failed to process /start command: {e}")
        await update.message.reply_text(
            "Sorry, something went wrong while processing your request. Please try again later."
        )

if __name__ == "__main__":
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is not set.")

    # Build the bot application
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Register the /start command handler
    app.add_handler(CommandHandler("start", start))

    # Explicitly manage the event loop for Python 3.14+ compatibility
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # Render requires binding to a web port using webhooks to prevent 'No open ports detected' errors
    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL}/{BOT_TOKEN}"
        logger.info(f"Starting webhook server on port {PORT} with URL {webhook_url}")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=BOT_TOKEN,
            webhook_url=webhook_url,
        )
    else:
        logger.warning("RENDER_EXTERNAL_URL not found. Falling back to polling mode.")
        app.run_polling()
