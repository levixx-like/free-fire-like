import threading
import os
import sqlite3
from datetime import datetime
from flask import Flask, request, jsonify
import discord
from discord.ext import commands
import requests

# --- PULL SECRETS SAFELY FROM CLOUD ENVIRONMENT VARIABLES ---
MASTER_API_KEY = os.environ.get("MASTER_API_KEY")
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
ALLOWED_CHANNEL_ID = int(os.environ.get("ALLOWED_CHANNEL_ID", "0"))

# Add your Discord User ID here for admin bypass (can also be environment-based if preferred)
ADMIN_USER_IDS = [987654321098765432]  # Replace with your actual Discord User ID
DB_NAME = "freefire_limits.db"

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_usage (
            discord_id TEXT PRIMARY KEY,
            last_date TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS uid_limits (
            uid TEXT,
            date TEXT,
            likes_sent INTEGER,
            PRIMARY KEY (uid, date)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- FLASK WEB SERVER API ---
app = Flask(__name__)

@app.route('/like', methods=['GET'])
def handle_like():
    api_key = request.args.get('api_key')
    if api_key != MASTER_API_KEY:
        return jsonify({"success": False, "message": "Unauthorized API key."}), 403

    server = request.args.get('server')
    uid = request.args.get('uid')
    amount_str = request.args.get('amount')
    discord_id = request.args.get('discord_id')
    is_admin = request.args.get('is_admin') == 'true'

    if not server or not uid or not amount_str or not discord_id:
        return jsonify({"success": False, "message": "Missing required parameters."}), 400

    try:
        amount = int(amount_str)
    except ValueError:
        return jsonify({"success": False, "message": "Amount must be a number."}), 400

    today = datetime.utcnow().strftime('%Y-%m-%d')
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    if not is_admin:
        cursor.execute('SELECT last_date FROM user_usage WHERE discord_id = ?', (discord_id,))
        row = cursor.fetchone()
        if row and row[0] == today:
            conn.close()
            return jsonify({"success": False, "message": "You have already used your free daily chance today!"}), 400

        cursor.execute('SELECT likes_sent FROM uid_limits WHERE uid = ? AND date = ?', (uid, today))
        uid_row = cursor.fetchone()
        current_uid_likes = uid_row[0] if uid_row else 0

        if current_uid_likes + amount > 100:
            conn.close()
            return jsonify({"success": False, "message": f"This UID has already received {current_uid_likes} likes today. Max limit is 100/day."}), 400

        if amount > 100:
            conn.close()
            return jsonify({"success": False, "message": "Max 100 likes per request."}), 400

        cursor.execute('INSERT OR REPLACE INTO user_usage (discord_id, last_date) VALUES (?, ?)', (discord_id, today))
        
        if uid_row:
            new_total = uid_row[0] + amount
            cursor.execute('UPDATE uid_limits SET likes_sent = ? WHERE uid = ? AND date = ?', (new_total, uid, today))
        else:
            cursor.execute('INSERT INTO uid_limits (uid, date, likes_sent) VALUES (?, ?, ?)', (uid, today, amount))
        
        conn.commit()

    conn.close()
    print(f"[API] Processing {amount} likes for UID {uid} on {server} (User: {discord_id})")

    return jsonify({
        "success": True,
        "message": f"Successfully delivered {amount} likes!",
        "server": server.upper(),
        "uid": uid,
        "delivered": amount
    })

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

# --- DISCORD BOT ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Cloud Bot logged in successfully as {bot.user.name}!")

@bot.command(name="like")
async def send_like(ctx, server: str = None, uid: str = None, amount: int = None):
    if ctx.channel.id != ALLOWED_CHANNEL_ID:
        try:
            await ctx.message.delete()
        except:
            pass
        return

    if not server or not uid or not amount:
        await ctx.send("⚠️ Usage: `!like [server] [uid] [amount]`\n*Example:* `!like IND 11959685790 50`")
        return

    is_admin = ctx.author.id in ADMIN_USER_IDS
    status_msg = await ctx.send(f"⏳ Processing `{amount}` likes for UID `{uid}`...")

    try:
        port = os.environ.get("PORT", 5000)
        api_url = f"http://127.0.0.1:{port}/like"
        params = {
            "server": server,
            "uid": uid,
            "amount": amount,
            "discord_id": str(ctx.author.id),
            "is_admin": "true" if is_admin else "false",
            "api_key": MASTER_API_KEY
        }

        response = requests.get(api_url, params=params, timeout=10)
        data = response.json()

        if response.status_code == 200 and data.get("success"):
            await status_msg.edit(content=f"✅ Success! Sent `{amount}` likes to UID `{uid}`.")
        else:
            await status_msg.edit(content=f"❌ Denied: `{data.get('message')}`")

    except Exception as e:
        await status_msg.edit(content="❌ Error executing the request.")

# --- START BOTH SERVICES ---
if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    bot.run(DISCORD_BOT_TOKEN)
