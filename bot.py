import os
import re
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ChatMemberStatus
from motor.motor_asyncio import AsyncIOMotorClient

# --- Configuration (Environment Variables se values lena) ---
try:
    API_ID = int(os.environ.get("API_ID"))
    API_HASH = os.environ.get("API_HASH")
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
    MONGO_URI = os.environ.get("MONGO_URI")
    # Control Channel ID negative mein hoga
    ADMIN_CHANNEL_ID = int(os.environ.get("ADMIN_CHANNEL_ID")) 
    VERIFIER_SESSION = os.environ.get("VERIFIER_SESSION")
except Exception as e:
    print(f"Error loading environment variables: {e}")
    exit()

# --- Database Setup ---
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client.group_seller_db
groups_collection = db.groups
users_collection = db.users

# --- Pyrogram Client Setup ---
# Bot client for main functions
app = Client(
    "selling_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# Secondary client (verifier account) for joining/checking groups
# Yeh account group join karega
verifier_app = Client(
    VERIFIER_SESSION,
    api_id=API_ID,
    api_hash=API_HASH,
)

# --- Utility Functions ---

def get_main_keyboard():
    """Main Menu ke buttons"""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💰 Price", callback_data="price_info")],
            [InlineKeyboardButton("✍️ Profile", callback_data="view_profile"),
             InlineKeyboardButton("💸 Withdraw", callback_data="withdraw_funds")],
            [InlineKeyboardButton("🔗 Group Link Verification", callback_data="start_verification")],
            [InlineKeyboardButton("❓ Support", callback_data="support_contact")],
        ]
    )

async def check_group_age_and_status(chat_link):
    """
    Verifier account ka upyog karke group ko join karta hai aur status check karta hai.
    'Old' group ke liye: Chat ID 1000000000 se chota hona chahiye (rough estimation for old groups).
    """
    
    # Simple logic: Extract chat_id from link or use resolve_invite
    try:
        # Step 1: Join the group using the verifier account
        chat = await verifier_app.join_chat(chat_link)
        chat_id = chat.id

        # Step 2: Verify Age (Approximate check)
        # Older groups ki IDs choti hoti hain, ya ham ek specific threshold rakh sakte hain.
        # Temporary logic: -1001000000000 se upar ki ID ko 'New' mana jaa sakta hai.
        is_old = chat_id < -1001000000000 

        # Step 3: Check ownership status (for further steps, though manual check is needed)
        # Is step par sirf join karna aur basic info nikalna kafi hai.
        
        await verifier_app.leave_chat(chat_id) # Verification ke baad leave karna optional hai
        
        return chat_id, is_old, None # None for ownership status as it's complex to automate

    except Exception as e:
        return None, False, f"Error joining/checking group: {e}"

async def send_to_admin_channel(user_id, username, group_link, group_title, group_id):
    """Admin Channel mein verification request bhejta hai."""
    
    message = (
        f"🚨 **New Group Verification Request** 🚨\n\n"
        f"**User:** @{username} (ID: `{user_id}`)\n"
        f"**Group Title:** {group_title}\n"
        f"**Group Link:** {group_link}\n"
        f"**Group ID:** `{group_id}`\n\n"
        f"**Action required:** Manually verify ownership/age and proceed."
    )
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve_{user_id}_{group_id}")],
        [InlineKeyboardButton("❌ Reject", callback_data=f"admin_reject_{user_id}_{group_id}")]
    ])
    
    await app.send_message(
        chat_id=ADMIN_CHANNEL_ID,
        text=message,
        reply_markup=keyboard
    )
    
# --- Message Handlers ---

# 1. /start command handler
@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    user_id = message.from_user.id
    username = message.from_user.username or "N/A"
    
    # Save/update user profile in DB
    await users_collection.update_one(
        {"_id": user_id},
        {"$set": {"username": username, "first_name": message.from_user.first_name}},
        upsert=True
    )
    
    await message.reply_text(
        f"Welcome, **{message.from_user.first_name}**! 👋\n"
        "This bot helps you sell old Telegram groups. Please use the menu below.",
        reply_markup=get_main_keyboard()
    )

# 2. Group Link Input (State Handling for Verification)
# Users ko "start_verification" callback ke baad link bhej na hai
user_states = {} # Simple dictionary for temporary state management

@app.on_message(filters.text & filters.private)
async def text_handler(client, message):
    user_id = message.from_user.id
    text = message.text
    
    # Check if user is in 'waiting_for_link' state
    if user_states.get(user_id) == "waiting_for_link":
        # Regular expression to find a Telegram link
        link_match = re.search(r'(https?://t.me/[^\s]+|@\w+)', text)
        
        if link_match:
            group_link = link_match.group(0)
            await message.reply_text("🔗 Link received. Starting verification process...")
            
            # Reset state
            user_states[user_id] = "verifying"

            # --- Core Verification Logic ---
            chat_id, is_old, error_msg = await check_group_age_and_status(group_link)
            
            if error_msg:
                await message.reply_text(f"❌ Verification failed. Error: {error_msg}")
                del user_states[user_id]
                return

            group_title = "Unknown Group"
            try:
                # Get group info with the bot client (as it's already joined)
                group_info = await client.get_chat(chat_id)
                group_title = group_info.title
            except:
                pass # Continue even if title is hard to get

            # Save the request to DB
            await groups_collection.insert_one({
                "group_id": chat_id,
                "user_id": user_id,
                "group_link": group_link,
                "group_title": group_title,
                "is_old_approx": is_old,
                "status": "PENDING_VERIFICATION",
                "timestamp": message.date
            })

            # Send A to chat to confirm acceptance
            await client.send_message(chat_id, "A")
            
            # Notify user
            await message.reply_text(
                f"✅ **Group accepted for review!** (ID: `{chat_id}`)\n"
                f"Your group's approx. age status: **{'OLD' if is_old else 'NEW'}**.\n"
                "Now, you must proceed with ownership transfer."
            )

            # Move to next state: Waiting for transfer
            user_states[user_id] = "waiting_for_transfer"
            
            await message.reply_text(
                "➡️ **Next Step: Ownership Transfer**\n\n"
                "Please go to your Group Settings -> Manage Group -> Administrators -> Add Administrator.\n"
                "You need to **Transfer Ownership** to one of our dedicated accounts **manually**.\n\n"
                "Once done, press the button below.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("✅ Ownership Transferred", callback_data=f"transfer_done_{chat_id}")]]
                )
            )

            # Send request to Admin Channel
            await send_to_admin_channel(
                user_id, 
                message.from_user.username or "N/A", 
                group_link, 
                group_title, 
                chat_id
            )

            del user_states[user_id] # State management for simplicity is reset
        else:
            await message.reply_text("⚠️ Please send a valid Telegram Group link (t.me/...) or username (@...).")

# 3. All other text messages (if not in a state)
    elif user_states.get(user_id) != "verifying":
        await message.reply_text(
            "I only respond to the menu buttons. Please select an option:",
            reply_markup=get_main_keyboard()
        )

# --- Callback Query Handlers (Button clicks) ---

@app.on_callback_query()
async def callback_handler(client, callback_query):
    data = callback_query.data
    user_id = callback_query.from_user.id
    
    await callback_query.answer() # Hide the loading animation

    # 1. Main Menu Button Handlers
    if data == "price_info":
        await callback_query.message.edit_text(
            "💰 **Our Group Pricing**\n\n"
            "Pricing depends on the group's age, number of members, and activity. "
            "Verification is required to get a final quote.",
            reply_markup=get_main_keyboard()
        )
    
    elif data == "view_profile":
        user_data = await users_collection.find_one({"_id": user_id})
        await callback_query.message.edit_text(
            f"👤 **Your Profile**\n\n"
            f"**ID:** `{user_id}`\n"
            f"**Username:** @{user_data.get('username', 'N/A')}\n"
            f"**Groups submitted:** {await groups_collection.count_documents({'user_id': user_id})}",
            reply_markup=get_main_keyboard()
        )
        
    elif data == "withdraw_funds":
        # Placeholder for complex payment logic
        await callback_query.message.edit_text(
            "💸 **Withdrawal Request**\n\n"
            "Our withdrawal process is manual. Please contact support to initiate a withdrawal after group sale.",
            reply_markup=get_main_keyboard()
        )

    elif data == "support_contact":
        await callback_query.message.edit_text(
            "❓ **Support**\n\n"
            "For any queries, please contact our support team:\n"
            "**@YourSupportUsername**",
            reply_markup=get_main_keyboard()
        )
        
    elif data == "start_verification":
        # Set user state to wait for link
        user_states[user_id] = "waiting_for_link"
        await callback_query.message.edit_text(
            "🔗 **Group Link Verification**\n\n"
            "Please send the **private or public link** of the group you want to sell now.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_menu")]]
            )
        )
        
    elif data == "back_to_menu":
        if user_id in user_states:
            del user_states[user_id]
        await callback_query.message.edit_text(
            "Welcome back to the main menu.",
            reply_markup=get_main_keyboard()
        )

    # 2. Ownership Transfer Button Handler
    elif data.startswith("transfer_done_"):
        group_id = int(data.split("_")[2])
        
        # Update status in DB
        await groups_collection.update_one(
            {"group_id": group_id, "user_id": user_id},
            {"$set": {"status": "WAITING_OWNERSHIP_CONFIRMATION", "transfer_timestamp": callback_query.message.date}}
        )
        
        await callback_query.message.edit_text(
            "⏳ **Transfer Claimed!**\n\n"
            "We have been notified that the ownership transfer is complete. Our admin will **manually** verify this now. "
            "This may take some time. We will notify you once verified and proceed with payment.",
            reply_markup=get_main_keyboard()
        )
        
        # Notify Admin Channel about transfer completion
        await app.send_message(
            chat_id=ADMIN_CHANNEL_ID,
            text=f"📢 **OWNERSHIP TRANSFER CLAIMED**\n\n"
                 f"User `{user_id}` claims ownership is transferred for Group ID `{group_id}`.\n"
                 f"**ACTION:** Manually check the group ownership."
        )

    # 3. Admin Button Handlers (In ADMIN_CHANNEL)
    elif data.startswith("admin_") and user_id in await client.get_chat_members(ADMIN_CHANNEL_ID, filters=filters.me):
        action, _, target_user_id, group_id = data.split("_")
        target_user_id = int(target_user_id)
        group_id = int(group_id)

        # Update DB and notify user logic here (simplified)
        
        if action == "admin_approve":
            await groups_collection.update_one(
                {"group_id": group_id, "user_id": target_user_id},
                {"$set": {"status": "APPROVED_AWAITING_TRANSFER"}}
            )
            await app.send_message(target_user_id, 
                                   f"✅ **Admin Approved!**\n"
                                   f"Your group (ID: `{group_id}`) is approved for transfer. Please proceed with ownership transfer now.")
            await callback_query.message.edit_text(callback_query.message.text + "\n\n**STATUS: APPROVED**")

        elif action == "admin_reject":
            await groups_collection.update_one(
                {"group_id": group_id, "user_id": target_user_id},
                {"$set": {"status": "REJECTED"}}
            )
            await app.send_message(target_user_id, 
                                   f"❌ **Admin Rejected!**\n"
                                   f"Your group (ID: `{group_id}`) was rejected. Please contact support for details.")
            await callback_query.message.edit_text(callback_query.message.text + "\n\n**STATUS: REJECTED**")


# --- Main Bot Execution ---
async def main():
    # Both clients ko start karo
    await verifier_app.start()
    await app.start()
    
    print("Verifier Account Started.")
    print("Telegram Selling Bot Started! Press Ctrl+C to stop.")
    
    # Ye block hone se rokega aur clients ko chalta rakhega
    await app.run_until_disconnected()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
  
