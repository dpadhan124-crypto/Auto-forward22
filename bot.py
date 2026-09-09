import os
import logging
from aiohttp import web
from pyrogram import Client, filters, idle
from pyrogram.types import Message
from pyrogram.session.string_session import StringSession

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Environment Configuration
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SESSION_STRING = os.getenv("SESSION_STRING", "") # Optional: for userbot session persistence on Render
PORT = int(os.getenv("PORT", "10000"))

# Simple Web Server for Render Health Checks
async def handle_root(request):
    return web.Response(text="Bot is running and healthy!", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_head("/", handle_root)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Web server started on port {PORT}")

async def main():
    # Start the lightweight health-check web server first
    await start_web_server()

    # Initialize Bot Client
    bot = Client(
        "bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN
    )

    # Optional User Client using StringSession to prevent wipe on Render redeploys
    user = None
    if SESSION_STRING:
        user = Client(
            "user",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=StringSession(SESSION_STRING)
        )

    # Diagnostic Message Handler
    @bot.on_message(filters.text & ~filters.scheduled)
    async def debug_listener(client: Client, message: Message):
        logger.info(f"Received message from {message.chat.id}: {message.text}")
        await message.reply(f"Echo: {message.text}")

    # Start Clients
    logger.info("Starting Pyrofork clients...")
    await bot.start()
    if user:
        await user.start()
        logger.info("User client started successfully.")

    logger.info("Bot client started successfully. Core systems online.")
    
    # Keep application running
    await idle()

    # Stop clients gracefully on exit
    await bot.stop()
    if user:
        await user.stop()

if __name__ == "__main__":
    if not API_ID or not API_HASH or not BOT_TOKEN:
        logger.error("Missing critical environment variables (API_ID, API_HASH, BOT_TOKEN).")
        exit(1)
    
    import asyncio
    asyncio.run(main())
