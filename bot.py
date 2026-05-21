import os
import asyncio
import sqlite3
import threading
from flask import Flask
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- Configuration ---
API_ID = 33902690  # Replace with your API ID
API_HASH = '08dfcf902b1bec83fef7aaab24c18278'
BOT_TOKEN = '8697814237:AAHGUZ7d_9VM3rnUbMeD0nZEW3zSIj79NxM'
ADMIN_ID = 8553702880  # <--- YOUR Telegram User ID (Get it from @userinfobot)
DB_PATH = 'bot_data.db'

app = Flask(__name__)
scheduler = AsyncIOScheduler()

# --- Security Middleware ---
def is_admin(user_id):
    return user_id == ADMIN_ID

# --- Database Setup ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS tasks 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER, dest_id INTEGER, 
                  filters TEXT, add_caption TEXT, last_msg_id INTEGER, interval INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    conn.commit()
    conn.close()

init_db()

# --- Telegram Bot Interface ---
bot = TelegramClient('bot_interface', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    if not is_admin(event.sender_id):
        return await event.reply("⛔ **Access Denied.** You are not authorized to use this bot.")
    await event.reply("👋 **Admin Authenticated.** Use `/addforward` or `/login` to begin.")

@bot.on(events.NewMessage(pattern='/login'))
async def login(event):
    if not is_admin(event.sender_id): return
    
    async with event.client.conversation(event.chat_id) as conv:
        await conv.send_message("🔑 Send your **Telethon Session String**:")
        session_msg = await conv.get_response()
        
        conn = sqlite3.connect(DB_PATH)
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('session', ?)", (session_msg.text.strip(),))
        conn.commit()
        conn.close()
        await conv.send_message("✅ **Session Saved.** The UserBot is now active.")

@bot.on(events.NewMessage(pattern='/addforward'))
async def add_forward(event):
    if not is_admin(event.sender_id): return
    
    async with event.client.conversation(event.chat_id) as conv:
        try:
            await conv.send_message("📤 **Source ID:** (e.g., -100123456)")
            src = int((await conv.get_response()).text)
            
            await conv.send_message("📥 **Destination ID:**")
            dest = int((await conv.get_response()).text)
            
            await conv.send_message("🔍 **Filters:** (audio,video,document,text or 'all')")
            filters = (await conv.get_response()).text.lower()
            
            await conv.send_message("📝 **Add Caption:** (Type your text or 'none')")
            caption = (await conv.get_response()).text
            caption = "" if caption.lower() == 'none' else caption
            
            await conv.send_message("🔢 **Starting Message ID:** (Use 1 for all past messages)")
            last_id = int((await conv.get_response()).text)

            conn = sqlite3.connect(DB_PATH)
            conn.execute("INSERT INTO tasks (source_id, dest_id, filters, add_caption, last_msg_id, interval) VALUES (?,?,?,?,?,?)",
                         (src, dest, filters, caption, last_id, 30))
            conn.commit()
            conn.close()
            await conv.send_message("🚀 **Forwarding Task Activated!**")
        except Exception as e:
            await conv.send_message(f"❌ **Error:** {str(e)}")

@bot.on(events.NewMessage(pattern='/removeforward'))
async def remove_forward(event):
    if not is_admin(event.sender_id): return
    
    conn = sqlite3.connect(DB_PATH)
    tasks = conn.execute("SELECT id, source_id, dest_id FROM tasks").fetchall()
    
    if not tasks:
        return await event.reply("No active tasks found.")
    
    msg = "**Select Task ID to remove:**\n"
    for t in tasks:
        msg += f"ID: `{t[0]}` | `{t[1]}` ➡️ `{t[2]}`\n"
    
    async with event.client.conversation(event.chat_id) as conv:
        await conv.send_message(msg)
        target_id = int((await conv.get_response()).text)
        conn.execute("DELETE FROM tasks WHERE id=?", (target_id,))
        conn.commit()
        await conv.send_message(f"🗑 Task `{target_id}` deleted.")
    conn.close()

@bot.on(events.NewMessage(pattern='/statistics'))
async def stats(event):
    if not is_admin(event.sender_id): return
    
    conn = sqlite3.connect(DB_PATH)
    tasks = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    conn.close()
    await event.reply(f"📊 **System Status**\nActive Forwarders: `{tasks}`\nAdmin: `Authorized`")

# --- Background Forwarding Logic (Restricted Content Proof) ---
async def process_forwarding():
    conn = sqlite3.connect(DB_PATH)
    res = conn.execute("SELECT value FROM settings WHERE key='session'").fetchone()
    if not res: return
    
    async with TelegramClient(StringSession(res[0]), API_ID, API_HASH) as client:
        tasks = conn.execute("SELECT * FROM tasks").fetchall()
        for task in tasks:
            tid, src, dest, filters, caption, last_id, _ = task
            filter_list = [f.strip() for f in filters.split(',')]
            
            async for msg in client.iter_messages(src, min_id=last_id, reverse=True):
                try:
                    # Filter matching
                    content_type = 'text'
                    if msg.photo: content_type = 'image'
                    elif msg.video: content_type = 'video'
                    elif msg.audio: content_type = 'audio'
                    elif msg.document: content_type = 'document'
                    
                    if 'all' not in filter_list and content_type not in filter_list:
                        continue

                    # Bypassing Restrictions via Download/Upload
                    final_caption = f"{msg.text or ''}\n\n{caption}".strip()
                    
                    if msg.media:
                        tmp_file = await client.download_media(msg)
                        await client.send_file(dest, tmp_file, caption=final_caption)
                        if os.path.exists(tmp_file): os.remove(tmp_file)
                    else:
                        await client.send_message(dest, final_caption)
                    
                    conn.execute("UPDATE tasks SET last_msg_id = ? WHERE id = ?", (msg.id, tid))
                    conn.commit()
                except Exception as e:
                    print(f"Task {tid} Error: {e}")
    conn.close()

# --- Web Server & Main ---
@app.route('/')
def health_check():
    return "Bot Online", 200

def run_flask():
    app.run(host='0.0.0.0', port=8080)

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    scheduler.add_job(process_forwarding, 'interval', minutes=30)
    scheduler.start()
    bot.run_until_disconnected()
