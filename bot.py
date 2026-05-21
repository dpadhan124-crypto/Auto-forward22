import os
import sqlite3
import asyncio
from quart import Quart
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- Configuration ---
# Render provides the PORT environment variable automatically
PORT = int(os.environ.get("PORT", 8080))
API_ID = 33902690
API_HASH = '08dfcf902b1bec83fef7aaab24c18278'
BOT_TOKEN = '8697814237:AAHGUZ7d_9VM3rnUbMeD0nZEW3zSIj79NxM'
ADMIN_ID = 8553702880
DB_PATH = 'bot_data.db'

# Use Quart instead of Flask for native asyncio support
app = Quart(__name__)
scheduler = AsyncIOScheduler()
bot = TelegramClient('bot_interface', API_ID, API_HASH)

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS tasks 
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER, dest_id INTEGER, 
                      filters TEXT, add_caption TEXT, last_msg_id INTEGER, interval INTEGER)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')

# --- Bot Commands ---
@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    if event.sender_id == ADMIN_ID:
        await event.reply("👋 **Admin Authenticated.** Use `/addforward` or `/login`.")

@bot.on(events.NewMessage(pattern='/login'))
async def login(event):
    if event.sender_id != ADMIN_ID: return
    async with bot.conversation(event.chat_id) as conv:
        await conv.send_message("🔑 Send your **Telethon Session String**:")
        res = await conv.get_response()
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('session', ?)", (res.text.strip(),))
        await conv.send_message("✅ **Session Saved.**")

@bot.on(events.NewMessage(pattern='/addforward'))
async def add_forward(event):
    if event.sender_id != ADMIN_ID: return
    async with bot.conversation(event.chat_id) as conv:
        try:
            await conv.send_message("📤 **Source ID:**")
            src = int((await conv.get_response()).text)
            await conv.send_message("📥 **Destination ID:**")
            dest = int((await conv.get_response()).text)
            await conv.send_message("🔍 **Filters:** (video,audio,text,all)")
            filt = (await conv.get_response()).text.lower()
            await conv.send_message("📝 **Caption:** (or 'none')")
            cap = (await conv.get_response()).text
            cap = "" if cap.lower() == 'none' else cap
            await conv.send_message("🔢 **Start ID:**")
            sid = int((await conv.get_response()).text)

            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("INSERT INTO tasks (source_id, dest_id, filters, add_caption, last_msg_id) VALUES (?,?,?,?,?)",
                             (src, dest, filt, cap, sid))
            await conv.send_message("🚀 **Task Added!**")
        except Exception as e:
            await conv.send_message(f"❌ Error: {e}")

# --- Forwarding Logic ---
async def process_tasks():
    with sqlite3.connect(DB_PATH) as conn:
        res = conn.execute("SELECT value FROM settings WHERE key='session'").fetchone()
        if not res: return
        tasks = conn.execute("SELECT * FROM tasks").fetchall()

    async with TelegramClient(StringSession(res[0]), API_ID, API_HASH) as client:
        for t in tasks:
            tid, src, dest, filters, caption, last_id = t[0], t[1], t[2], t[3], t[4], t[5]
            async for msg in client.iter_messages(src, min_id=last_id, reverse=True):
                try:
                    # Media Handling & Strip Restrictions
                    if msg.media:
                        path = await client.download_media(msg)
                        await client.send_file(dest, path, caption=f"{msg.text or ''}\n{caption}")
                        if os.path.exists(path): os.remove(path)
                    else:
                        await client.send_message(dest, f"{msg.text}\n{caption}")
                    
                    with sqlite3.connect(DB_PATH) as conn:
                        conn.execute("UPDATE tasks SET last_msg_id = ? WHERE id = ?", (msg.id, tid))
                except Exception as e:
                    print(f"Forward Error: {e}")

# --- Web & Lifecycle ---
@app.route('/')
async def health():
    return "Bot is running", 200

async def main():
    init_db()
    await bot.start(bot_token=BOT_TOKEN)
    scheduler.add_job(process_tasks, 'interval', minutes=30)
    scheduler.start()
    
    # Run the Quart web server and Telethon bot concurrently
    config = asyncio.gather(
        bot.run_until_disconnected(),
        app.run_task(host='0.0.0.0', port=PORT)
    )
    await config

if __name__ == '__main__':
    asyncio.run(main())
