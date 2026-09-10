import logging
import asyncio
import os
import re
import aiohttp
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# Enable logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Global session store, task queue, and admin store
user_sessions = {}
task_queue = asyncio.Queue()
DEFAULT_ADMIN_ID = 8323137024
DYNAMIC_ADMINS = set()

def is_admin(user_id: int) -> bool:
    """Helper function to check if a user is an authorized admin."""
    additional_admins = [
        int(uid.strip()) for uid in os.getenv("ADMIN_IDS", "").split(",") if uid.strip().isdigit()
    ]
    return user_id == DEFAULT_ADMIN_ID or user_id in additional_admins or user_id in DYNAMIC_ADMINS

def admin_only(func):
    """Decorator to restrict command and message handlers to admins only."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user or not is_admin(user.id):
            if update.message:
                await update.message.reply_text("❌ Unauthorized: You do not have permission to use this bot.")
            elif update.callback_query:
                await update.callback_query.answer("❌ Unauthorized action.", show_alert=True)
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

def parse_chat_string(s: str):
    """Parses raw string IDs into integers or usernames."""
    s = s.strip()
    if s.startswith('@'):
        return s
    if s.isdigit():
        if len(s) >= 10 and not s.startswith('-100'):
            return int(f"-100{s}")
        return int(s)
    try:
        return int(s)
    except ValueError:
        return s

def parse_chat_and_topic_input(text: str):
    """Robustly parses various chat and topic input formats (IDs, Usernames, URLs)."""
    text = text.strip()
    
    # Case 1: URL with topic like https://t.me/c/4429889875/412 or https://t.me/username/412
    match_url_topic = re.match(r'https?://t\.me/(?:c/(\d+)|([a-zA-Z0-9_]+))/(\d+)', text)
    if match_url_topic:
        g1, g2, t_id = match_url_topic.groups()
        chat_id = int(f"-100{g1}") if g1 else f"@{g2}"
        return chat_id, int(t_id)

    # Case 2: URL without topic like https://t.me/c/4429889875 or https://t.me/username
    match_url = re.match(r'https?://t\.me/(?:c/(\d+)|([a-zA-Z0-9_]+))/?$', text)
    if match_url:
        g1, g2 = match_url.groups()
        chat_id = int(f"-100{g1}") if g1 else f"@{g2}"
        return chat_id, None

    # Case 3: Explicit slash separation like -1004429889875/412 or 4429889875/412
    if '/' in text:
        parts = text.split('/')
        raw_chat = parts[0].strip()
        t_id = parts[1].strip()
        topic_id = int(t_id) if t_id.isdigit() else None
        return parse_chat_string(raw_chat), topic_id

    # Case 4: Plain identifier format (-100..., @username, raw digits)
    return parse_chat_string(text), None

async def health_check(request):
    """Dummy web server handler to satisfy Render's port binding requirement."""
    return web.Response(text="Bot is running!")

async def start_web_server():
    """Starts a lightweight aiohttp web server for Render."""
    app = web.Application()
    app.add_routes([web.get("/", health_check)])
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Web server started on port {port} for Render.")

async def self_ping():
    """Periodically pings the Render app URL to keep it awake on the free tier."""
    url = "https://forwardbot-cx7a.onrender.com"
    await asyncio.sleep(10)
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    logger.info(f"Self-ping successful: {response.status}")
        except Exception as e:
            logger.error(f"Self-ping failed: {e}")
        await asyncio.sleep(300)

@admin_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the sequence with setup links, shortcuts, and a direct forward button."""
    keyboard = [
        [InlineKeyboardButton("➡️ Forward Files to Topic", callback_data="start_forward_workflow")],
        [InlineKeyboardButton("🤖 Add @DPS_xbot", url="https://t.me/DPS_xbot?startchannel=true&admin=post_messages+edit_messages+delete_messages+ban_users+invite_users+change_info+pin_messages+manage_video_chats+manage_topics+add_admins")],
        [InlineKeyboardButton("🤖 Add @Dps_storiesbot", url="https://t.me/fm_Storiesbot?startchannel=true&admin=post_messages+edit_messages+delete_messages+ban_users+invite_users+change_info+pin_messages+manage_video_chats+manage_topics+add_admins")],
        [InlineKeyboardButton("⚡ Auto-fill -100 Prefix", url="https://t.me/share/url?url=-100")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Welcome, Admin! Choose an option below to configure your automated forwarding session.",
        reply_markup=reply_markup
    )
    user_sessions[update.effective_user.id] = {"step": "idle"}

@admin_only
async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command to add a new admin dynamically via User ID or reply."""
    args = context.args
    user_id_to_add = None

    if args and args[0].isdigit():
        user_id_to_add = int(args[0])
    elif update.message.reply_to_message and update.message.reply_to_message.from_user:
        user_id_to_add = update.message.reply_to_message.from_user.id

    if not user_id_to_add:
        await update.message.reply_text(
            "❌ Please provide a valid numeric user ID or reply to the user's message.\nUsage: `/add_admin <user_id>`",
            parse_mode="Markdown"
        )
        return

    DYNAMIC_ADMINS.add(user_id_to_add)
    await update.message.reply_text(f"✅ Successfully added user `{user_id_to_add}` as an admin!", parse_mode="Markdown")

@admin_only
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles inline button interactions."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if query.data == "start_forward_workflow":
        user_sessions[user_id] = {"step": "awaiting_destination"}
        await query.message.reply_text("📥 Please send the **Destination Group ID, Link, or Topic** (e.g., `-1004429889875/412` or `https://t.me/...`):", parse_mode="Markdown")
        return

    state = user_sessions.get(user_id)
    if not state:
        await query.edit_message_text("Session expired. Send `/start` to begin again.")
        return

    if query.data == "toggle_mode":
        state["forward_mode"] = "reverse_order" if state.get("forward_mode") == "regular" else "regular"
        await update_control_panel(context, user_id)

    elif query.data == "finish_process":
        await finalize_process(update, context, user_id)

@admin_only
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles flexible text inputs and media files."""
    user_id = update.effective_user.id
    user_state = user_sessions.get(user_id, {})
    step = user_state.get("step")

    if step == "awaiting_destination":
        dest_input = update.message.text.strip()
        try:
            dest_chat_id, dest_topic_id = parse_chat_and_topic_input(dest_input)
            member = await context.bot.get_chat_member(chat_id=dest_chat_id, user_id=context.bot.id)
            if member.status not in ["administrator", "creator"]:
                raise Exception("Bot is not an admin")
            
            user_sessions[user_id]["destination_group_id"] = dest_chat_id
            if dest_topic_id:
                user_sessions[user_id]["destination_topic_id"] = dest_topic_id

            user_sessions[user_id]["step"] = "awaiting_source"
            await update.message.reply_text("✅ Destination saved! Now send your source **Channel ID, Username, or Link** (e.g., `@tdbbbsh`, `4429889875`, or `https://t.me/...`):")
        except Exception:
            keyboard = [[InlineKeyboardButton("➕ Add Bot as Admin", url="https://t.me/DPS_xbot?startchannel=true&admin=post_messages+edit_messages+delete_messages+ban_users+invite_users+change_info+pin_messages+manage_video_chats+manage_topics+add_admins")]]
            await update.message.reply_text(
                "❌ Error: Bot is not an admin in the destination group or input is invalid.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    elif step == "awaiting_source":
        source_input = update.message.text.strip()
        try:
            source_chat_id, _ = parse_chat_and_topic_input(source_input)
            chat = await context.bot.get_chat(source_chat_id)
            member = await context.bot.get_chat_member(chat_id=chat.id, user_id=context.bot.id)
            if member.status not in ["administrator", "creator"]:
                raise Exception("Bot is not an admin")

            channel_name = chat.title or "Forwarded Files"
            dest_group_id = user_sessions[user_id]["destination_group_id"]
            preset_topic_id = user_sessions[user_id].get("destination_topic_id")
            
            if preset_topic_id:
                topic_id = preset_topic_id
            else:
                topic = await context.bot.create_forum_topic(chat_id=dest_group_id, name=channel_name)
                topic_id = topic.message_thread_id
            
            user_sessions[user_id].update({
                "step": "collecting_files",
                "topic_id": topic_id,
                "forward_mode": "regular",
                "files": []
            })

            await send_control_panel(update, context, user_id)
        except Exception:
            keyboard = [[InlineKeyboardButton("➕ Add Bot as Admin", url="https://t.me/DPS_xbot?startchannel=true&admin=post_messages+edit_messages+delete_messages+ban_users+invite_users+change_info+pin_messages+manage_video_chats+manage_topics+add_admins")]]
            await update.message.reply_text(
                "❌ Error: Bot lacks admin permissions in the source or input format is invalid.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    elif step == "collecting_files":
        file_id = None
        if update.message.document:
            file_id = update.message.document.file_id
        elif update.message.video:
            file_id = update.message.video.file_id
        elif update.message.photo:
            file_id = update.message.photo[-1].file_id
        elif update.message.audio:
            file_id = update.message.audio.file_id

        if file_id:
            user_sessions[user_id]["files"].append({
                "type": update.message.effective_attachment.__class__.__name__.lower(),
                "file_id": file_id,
                "caption": update.message.caption or ""
            })
            await update.message.delete()
            await update_control_panel(context, user_id)

@admin_only
async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggers final processing via text command `/done`."""
    user_id = update.effective_user.id
    if user_sessions.get(user_id, {}).get("step") == "collecting_files":
        await finalize_process(update, context, user_id)

async def send_control_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Sends and pins the interactive progress/control panel UI."""
    state = user_sessions[user_id]
    text, reply_markup = get_panel_content(state)
    sent_msg = await context.bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup, parse_mode="Markdown")
    state["panel_message_id"] = sent_msg.message_id
    try:
        await context.bot.pin_chat_message(chat_id=user_id, message_id=sent_msg.message_id)
    except Exception:
        pass

async def update_control_panel(context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Dynamically updates the pinned control panel message."""
    state = user_sessions[user_id]
    text, reply_markup = get_panel_content(state)
    try:
        await context.bot.edit_message_text(
            chat_id=user_id,
            message_id=state["panel_message_id"],
            text=text,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    except Exception:
        pass

def get_panel_content(state):
    """Generates panel strings and forward-order toggle buttons."""
    topic_id = state.get("topic_id", "N/A")
    mode = state.get("forward_mode", "regular")
    total_files = len(state.get("files", []))

    text = (
        f"⚙️ **Configuration Panel**\n\n"
        f"• **Group topic id:** `{topic_id}`\n"
        f"• **Forward mode:** `{mode}`\n"
        f"• **Total files saved:** `{total_files}`\n\n"
        f"*(Send files or type `/done` when finished)*"
    )

    keyboard = [
        [InlineKeyboardButton(f"Mode: {mode.capitalize()}", callback_data="toggle_mode")],
        [InlineKeyboardButton("✅ Done", callback_data="finish_process")]
    ]
    return text, InlineKeyboardMarkup(keyboard)

async def finalize_process(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Puts the task into the background queue to support concurrent workflows."""
    state = user_sessions.pop(user_id, None)
    if not state or not state.get("files"):
        target_chat = update.effective_chat.id
        await context.bot.send_message(chat_id=target_chat, text="⚠️ No files saved to send.")
        return

    await context.bot.send_message(chat_id=user_id, text="⏳ Task queued! Processing will run sequentially without blocking new sessions...")
    await task_queue.put((user_id, state, context))

async def process_queue_worker(application):
    """Background worker that executes tasks from the queue sequentially and concurrently across sessions."""
    while True:
        user_id, state, context = await task_queue.get()
        try:
            files = state["files"]
            topic_id = state["topic_id"]
            dest_group_id = state["destination_group_id"]
            mode = state["forward_mode"]

            if mode == "reverse_order":
                files.reverse()

            tasks = []
            for file_info in files:
                f_type = file_info["type"]
                f_id = file_info["file_id"]
                caption = file_info["caption"]

                if "document" in f_type:
                    tasks.append(context.bot.send_document(chat_id=dest_group_id, message_thread_id=topic_id, document=f_id, caption=caption))
                elif "video" in f_type:
                    tasks.append(context.bot.send_video(chat_id=dest_group_id, message_thread_id=topic_id, video=f_id, caption=caption))
                elif "photo" in f_type:
                    tasks.append(context.bot.send_photo(chat_id=dest_group_id, message_thread_id=topic_id, photo=f_id, caption=caption))
                elif "audio" in f_type:
                    tasks.append(context.bot.send_audio(chat_id=dest_group_id, message_thread_id=topic_id, audio=f_id, caption=caption))

            chunk_size = 10
            for i in range(0, len(tasks), chunk_size):
                await asyncio.gather(*tasks[i:i+chunk_size])

            await context.bot.send_message(chat_id=user_id, text="✅ All files successfully dispatched to the destination topic!")
        except Exception as e:
            logger.error(f"Error processing queue for user {user_id}: {e}")
            await context.bot.send_message(chat_id=user_id, text=f"❌ Error during file dispatch: {e}")
        finally:
            task_queue.task_done()

async def post_init(application):
    """Initializes background web server, worker tasks, and self-ping post application build."""
    await start_web_server()
    asyncio.create_task(process_queue_worker(application))
    asyncio.create_task(self_ping())
    logger.info("Self-ping background task initialized.")

def main():
    TOKEN = os.getenv("BOT_TOKEN")
    if not TOKEN:
        raise ValueError("No BOT_TOKEN environment variable found. Please set it in your environment/Render dashboard.")
    
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("done", done_command))
    app.add_handler(CommandHandler("add_admin", add_admin_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    app.add_handler(MessageHandler(filters.ATTACHMENT, handle_message))

    print("Bot is up and running on Render...")
    app.run_polling()

if __name__ == "__main__":
    main()
