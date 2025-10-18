import os
import re
import shlex
import logging
from telebot import TeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ForceReply
from functools import wraps
from utils import (
    BotConfig, get_bot, get_app_modules, is_admin, send_dynamic_menu,
    get_shipment_details, save_shipment, delete_shipment, confirm_delete_shipment,
    toggle_batch_selection, batch_delete_shipments, confirm_batch_delete,
    trigger_broadcast, toggle_email_notifications, pause_simulation_callback,
    resume_simulation_callback, show_simulation_speed, send_manual_email,
    send_manual_webhook, bulk_pause_shipments, bulk_resume_shipments,
    generate_unique_id, search_shipments, RATE_LIMIT_WINDOW, RATE_LIMIT_MAX,
    safe_redis_operation, redis_client, sanitize_input, console
)

# Logging setup
bot_logger = logging.getLogger('telegram_bot')
bot_logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
bot_logger.addHandler(handler)

# Bot configuration
config = BotConfig(
    telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
    redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    webhook_url=os.getenv("WEBHOOK_URL", "https://signment.onrender.com/telegram/webhook"),
    websocket_server=os.getenv("WEBSOCKET_SERVER", "https://signment.onrender.com"),
    allowed_admins=[int(uid) for uid in os.getenv("ALLOWED_ADMINS", "").split(",") if uid],
    valid_statuses=os.getenv("VALID_STATUSES", "Pending,In_Transit,Out_for_Delivery,Delivered,Returned,Delayed").split(","),
    route_templates=json.loads(os.getenv("ROUTE_TEMPLATES", '{"Lagos, NG": ["Lagos, NG"]}')),
    smtp_host=os.getenv("SMTP_HOST", "smtp.gmail.com"),
    smtp_port=int(os.getenv("SMTP_PORT", 587)),
    smtp_user=os.getenv("SMTP_USER", ""),
    smtp_pass=os.getenv("SMTP_PASS", ""),
    smtp_from=os.getenv("SMTP_FROM", "no-reply@example.com")
)

# Rate limit decorator
def rate_limit(func):
    @wraps(func)
    def wrapper(message):
        user_id = str(message.from_user.id)
        key = f"rate_limit:{user_id}"
        try:
            count = safe_redis_operation(redis_client.incr, key) if redis_client else 0
            if count == 1:
                safe_redis_operation(redis_client.expire, key, RATE_LIMIT_WINDOW)
            if count > RATE_LIMIT_MAX:
                bot.reply_to(message, "Rate limit exceeded. Please try again later.")
                bot_logger.warning(f"Rate limit exceeded for user {user_id}")
                console.print(f"[warning]Rate limit exceeded for user {user_id}[/warning]")
                return
            return func(message)
        except Exception as e:
            bot_logger.error(f"Redis error in rate_limit: {e}")
            console.print(Panel(f"[error]Redis error in rate_limit for {user_id}: {e}[/error]", title="Rate Limit Error", border_style="red"))
            bot.reply_to(message, "Error processing request. Please try again.")
    return wrapper

# Bot instance
bot = get_bot()

def invalidate_cache(tracking_number):
    """Invalidate Redis cache for a shipment."""
    if redis_client:
        try:
            cache_key = f"shipment:{tracking_number}"
            safe_redis_operation(redis_client.delete, cache_key)
            bot_logger.debug(f"Invalidated cache for {tracking_number}")
            console.print(f"[info]Invalidated cache for {tracking_number}[/info]")
        except Exception as e:
            bot_logger.error(f"Error invalidating cache for {tracking_number}: {e}")
            console.print(Panel(f"[error]Error invalidating cache for {tracking_number}: {e}[/error]", title="Cache Error", border_style="red"))

# Command Handlers
@bot.message_handler(commands=['myid'])
@rate_limit
def get_my_id(message):
    """Handle /myid command to return the user's Telegram ID."""
    bot.reply_to(message, f"Your Telegram user ID: `{message.from_user.id}`", parse_mode='Markdown')
    bot_logger.info(f"User requested their ID", extra={'user_id': message.from_user.id})
    console.print(f"[info]User {message.from_user.id} requested their ID[/info]")

@bot.message_handler(commands=['start', 'menu'])
@rate_limit
def send_menu(message):
    """Handle /start and /menu commands to display the admin menu."""
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "Access denied.")
        bot_logger.warning(f"Access denied for user {message.from_user.id}")
        console.print(f"[warning]Access denied for user {message.from_user.id}[/warning]")
        return
    send_dynamic_menu(message.chat.id, page=1)
    bot_logger.info(f"Menu sent to admin {message.from_user.id}")
    console.print(f"[info]Menu sent to admin {message.from_user.id}[/info]")

@bot.message_handler(commands=['track'])
@rate_limit
def track_shipment(message):
    """Handle /track command to view shipment details with interactive controls."""
    db, Shipment, sanitize_tracking_number, _, _, _, _ = get_app_modules()
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "Access denied.")
        bot_logger.warning(f"Access denied for /track by {message.from_user.id}")
        console.print(f"[warning]Access denied for /track by {message.from_user.id}[/warning]")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "Usage: /track <tracking_number>\nExample: /track TRK20231010120000ABC123")
        bot_logger.warning("Invalid /track command format", extra={'user_id': message.from_user.id})
        console.print(f"[warning]Invalid /track command format[/warning]")
        return
    tracking_number = sanitize_tracking_number(sanitize_input(parts[1].strip()))
    if not tracking_number:
        bot.reply_to(message, "Invalid tracking number.")
        bot_logger.error(f"Invalid tracking number: {parts[1]}", extra={'user_id': message.from_user.id})
        console.print(f"[error]Invalid tracking number: {parts[1]}[/error]")
        return
    try:
        shipment = get_shipment_details(tracking_number)
        if not shipment:
            bot.reply_to(message, f"Shipment `{tracking_number}` not found.", parse_mode='Markdown')
            bot_logger.warning(f"Shipment not found: {tracking_number}", extra={'user_id': message.from_user.id})
            console.print(f"[warning]Shipment not found: {tracking_number}[/warning]")
            return
        response = (
            f"*Shipment*: `{tracking_number}`\n"
            f"*Status*: `{shipment['status']}`\n"
            f"*Paused*: `{shipment.get('paused', False)}`\n"
            f"*Speed Multiplier*: `{shipment.get('speed_multiplier', 1.0)}x`\n"
            f"*Delivery Location*: `{shipment['delivery_location']}`\n"
            f"*Checkpoints*: `{shipment.get('checkpoints', 'None')}`"
        )
        markup = InlineKeyboardMarkup(row_width=2)
        if shipment['status'] not in ['Delivered', 'Returned']:
            is_paused = shipment.get('paused', False)
            markup.add(
                InlineKeyboardButton("Pause" if not is_paused else "Resume", callback_data=f"{'pause' if not is_paused else 'resume'}_{tracking_number}_1"),
                InlineKeyboardButton("Set Speed", callback_data=f"setspeed_{tracking_number}")
            )
        markup.add(
            InlineKeyboardButton("Broadcast", callback_data=f"broadcast_{tracking_number}"),
            InlineKeyboardButton("Notify", callback_data=f"notify_{tracking_number}"),
            InlineKeyboardButton("Set Webhook", callback_data=f"set_webhook_{tracking_number}"),
            InlineKeyboardButton("Test Webhook", callback_data=f"test_webhook_{tracking_number}"),
            InlineKeyboardButton("Home", callback_data="menu_page_1")
        )
        bot.reply_to(message, response, parse_mode='Markdown', reply_markup=markup)
        bot_logger.info(f"Sent tracking details for {tracking_number}", extra={'user_id': message.from_user.id})
        console.print(f"[info]Sent tracking details for {tracking_number}[/info]")
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")
        bot_logger.error(f"Error in track command: {e}", extra={'user_id': message.from_user.id})
        console.print(Panel(f"[error]Error in track command: {e}[/error]", title="Track Error", border_style="red"))

@bot.message_handler(commands=['stats'])
@rate_limit
def system_stats(message):
    """Handle /stats command to display system statistics."""
    db, Shipment, _, _, _, _, _ = get_app_modules()
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "Access denied.")
        bot_logger.warning(f"Access denied for /stats by {message.from_user.id}")
        console.print(f"[warning]Access denied for /stats by {message.from_user.id}[/warning]")
        return
    try:
        total_shipments = Shipment.query.count()
        active_shipments = Shipment.query.filter(~Shipment.status.in_(['Delivered', 'Returned'])).count()
        paused_count = len(safe_redis_operation(redis_client.hgetall, "paused_simulations")) if redis_client else 0
        queue_length = redis_client.llen("notification_queue") if redis_client else 0
        response = (
            f"*System Statistics*\n"
            f"*Total Shipments*: `{total_shipments}`\n"
            f"*Active Shipments*: `{active_shipments}`\n"
            f"*Paused Simulations*: `{paused_count}`\n"
            f"*Notification Queue Length*: `{queue_length}`"
        )
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("Active Shipments", callback_data="list_active_1"),
            InlineKeyboardButton("Paused Shipments", callback_data="list_paused_1"),
            InlineKeyboardButton("All Shipments", callback_data="list_1"),
            InlineKeyboardButton("Home", callback_data="menu_page_1")
        )
        bot.reply_to(message, response, parse_mode='Markdown', reply_markup=markup)
        bot_logger.info(f"Sent system stats to admin {message.from_user.id}")
        console.print(f"[info]Sent system stats to admin {message.from_user.id}[/info]")
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")
        bot_logger.error(f"Error in stats command: {e}", extra={'user_id': message.from_user.id})
        console.print(Panel(f"[error]Error in stats command: {e}[/error]", title="Stats Error", border_style="red"))

@bot.message_handler(commands=['notify'])
@rate_limit
def manual_notification(message):
    """Handle /notify command to send manual email or webhook notification."""
    db, Shipment, sanitize_tracking_number, _, _, _, _ = get_app_modules()
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "Access denied.")
        bot_logger.warning(f"Access denied for /notify by {message.from_user.id}")
        console.print(f"[warning]Access denied for /notify by {message.from_user.id}[/warning]")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "Usage: /notify <tracking_number>\nExample: /notify TRK20231010120000ABC123")
        bot_logger.warning("Invalid /notify command format", extra={'user_id': message.from_user.id})
        console.print(f"[warning]Invalid /notify command format[/warning]")
        return
    tracking_number = sanitize_tracking_number(sanitize_input(parts[1].strip()))
    if not tracking_number:
        bot.reply_to(message, "Invalid tracking number.")
        bot_logger.error(f"Invalid tracking number: {parts[1]}", extra={'user_id': message.from_user.id})
        console.print(f"[error]Invalid tracking number: {parts[1]}[/error]")
        return
    try:
        shipment = get_shipment_details(tracking_number)
        if not shipment:
            bot.reply_to(message, f"Shipment `{tracking_number}` not found.", parse_mode='Markdown')
            bot_logger.warning(f"Shipment not found: {tracking_number}", extra={'user_id': message.from_user.id})
            console.print(f"[warning]Shipment not found: {tracking_number}[/warning]")
            return
        queue_length = redis_client.llen("notification_queue") if redis_client else 0
        bot_logger.debug(f"Notification queue length: {queue_length}", extra={'tracking_number': tracking_number})
        console.print(f"[info]Notification queue length for {tracking_number}: {queue_length}[/info]")
        markup = InlineKeyboardMarkup(row_width=2)
        if shipment.get('recipient_email') and shipment.get('email_notifications'):
            markup.add(InlineKeyboardButton("Send Email", callback_data=f"send_email_{tracking_number}"))
        if shipment.get('webhook_url') or config.websocket_server:
            markup.add(InlineKeyboardButton("Send Webhook", callback_data=f"send_webhook_{tracking_number}"))
        markup.add(InlineKeyboardButton("Home", callback_data="menu_page_1"))
        bot.reply_to(message, f"Select notification type for `{tracking_number}`:", parse_mode='Markdown', reply_markup=markup)
        bot_logger.info(f"Sent notification options for {tracking_number}", extra={'user_id': message.from_user.id})
        console.print(f"[info]Sent notification options for {tracking_number}[/info]")
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")
        bot_logger.error(f"Error in notify command: {e}", extra={'user_id': message.from_user.id})
        console.print(Panel(f"[error]Error in notify command: {e}[/error]", title="Notify Error", border_style="red"))

@bot.message_handler(commands=['search'])
@rate_limit
def search_command(message):
    """Handle /search command to find shipments by query."""
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "Access denied.")
        bot_logger.warning(f"Access denied for /search by {message.from_user.id}")
        console.print(f"[warning]Access denied for /search by {message.from_user.id}[/warning]")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "Usage: /search <query>\nExample: /search Lagos")
        bot_logger.warning("Invalid /search command format", extra={'user_id': message.from_user.id})
        console.print(f"[warning]Invalid /search command format[/warning]")
        return
    query = sanitize_input(parts[1].strip())
    try:
        shipments, total = search_shipments(query, page=1)
        if not shipments:
            bot.reply_to(message, f"No shipments found for query: `{query}`", parse_mode='Markdown')
            bot_logger.debug(f"No shipments found for query: {query}", extra={'user_id': message.from_user.id})
            console.print(f"[info]No shipments found for query: {query}[/info]")
            return
        markup = InlineKeyboardMarkup(row_width=1)
        for tn in shipments:
            markup.add(InlineKeyboardButton(tn, callback_data=f"view_{tn}_1"))
        if total > 5:
            markup.add(InlineKeyboardButton("Next", callback_data=f"search_page_{query}_2"))
        markup.add(InlineKeyboardButton("Home", callback_data="menu_page_1"))
        bot.reply_to(message, f"*Search Results for '{query}'* (Page 1, {total} total):", parse_mode='Markdown', reply_markup=markup)
        bot_logger.info(f"Sent search results for query: {query}", extra={'user_id': message.from_user.id})
        console.print(f"[info]Sent search results for query: {query}[/info]")
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")
        bot_logger.error(f"Error in search command: {e}", extra={'user_id': message.from_user.id})
        console.print(Panel(f"[error]Error in search command: {e}[/error]", title="Search Error", border_style="red"))

@bot.message_handler(commands=['bulk_action'])
@rate_limit
def bulk_action_command(message):
    """Handle /bulk_action command to perform bulk operations."""
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "Access denied.")
        bot_logger.warning(f"Access denied for /bulk_action by {message.from_user.id}")
        console.print(f"[warning]Access denied for /bulk_action by {message.from_user.id}[/warning]")
        return
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("Bulk Pause", callback_data="bulk_pause_menu_1"),
        InlineKeyboardButton("Bulk Resume", callback_data="bulk_resume_menu_1"),
        InlineKeyboardButton("Bulk Delete", callback_data="batch_delete_menu_1"),
        InlineKeyboardButton("Home", callback_data="menu_page_1")
    )
    bot.reply_to(message, "*Select bulk action*:", parse_mode='Markdown', reply_markup=markup)
    bot_logger.info(f"Sent bulk action menu to admin {message.from_user.id}")
    console.print(f"[info]Sent bulk action menu to admin {message.from_user.id}[/info]")

@bot.message_handler(commands=['stop'])
@rate_limit
def stop_simulation(message):
    """Handle /stop command to pause a shipment's simulation."""
    db, Shipment, sanitize_tracking_number, _, _, _, _ = get_app_modules()
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "Access denied.")
        bot_logger.warning(f"Access denied for /stop by {message.from_user.id}")
        console.print(f"[warning]Access denied for /stop by {message.from_user.id}[/warning]")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "Usage: /stop <tracking_number>\nExample: /stop TRK20231010120000ABC123")
        bot_logger.warning("Invalid /stop command format", extra={'user_id': message.from_user.id})
        console.print(f"[warning]Invalid /stop command format[/warning]")
        return
    tracking_number = sanitize_tracking_number(sanitize_input(parts[1].strip()))
    if not tracking_number:
        bot.reply_to(message, "Invalid tracking number.")
        bot_logger.error(f"Invalid tracking number: {parts[1]}", extra={'user_id': message.from_user.id})
        console.print(f"[error]Invalid tracking number: {parts[1]}[/error]")
        return
    try:
        shipment = get_shipment_details(tracking_number)
        if not shipment:
            bot.reply_to(message, f"Shipment `{tracking_number}` not found.", parse_mode='Markdown')
            bot_logger.warning(f"Shipment not found: {tracking_number}", extra={'user_id': message.from_user.id})
            console.print(f"[warning]Shipment not found: {tracking_number}[/warning]")
            return
        if shipment['status'] in ['Delivered', 'Returned']:
            bot.reply_to(message, f"Shipment `{tracking_number}` is already completed (`{shipment['status']}`).", parse_mode='Markdown')
            bot_logger.warning(f"Cannot pause completed shipment: {tracking_number}", extra={'user_id': message.from_user.id})
            console.print(f"[warning]Cannot pause completed shipment: {tracking_number}[/warning]")
            return
        if redis_client and safe_redis_operation(redis_client.hget, "paused_simulations", tracking_number) == "true":
            bot.reply_to(message, f"Simulation for `{tracking_number}` is already paused.", parse_mode='Markdown')
            bot_logger.warning(f"Simulation already paused: {tracking_number}", extra={'user_id': message.from_user.id})
            console.print(f"[warning]Simulation already paused: {tracking_number}[/warning]")
            return
        if redis_client:
            safe_redis_operation(redis_client.hset, "paused_simulations", tracking_number, "true")
        invalidate_cache(tracking_number)
        bot_logger.info(f"Paused simulation for {tracking_number}", extra={'user_id': message.from_user.id})
        console.print(f"[info]Paused simulation for {tracking_number}[/info]")
        bot.reply_to(message, f"Simulation paused for `{tracking_number}`.", parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")
        bot_logger.error(f"Error in stop command: {e}", extra={'user_id': message.from_user.id})
        console.print(Panel(f"[error]Error in stop command: {e}[/error]", title="Stop Error", border_style="red"))

@bot.message_handler(commands=['continue'])
@rate_limit
def continue_simulation(message):
    """Handle /continue command to resume a paused shipment's simulation."""
    db, Shipment, sanitize_tracking_number, _, _, _, _ = get_app_modules()
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "Access denied.")
        bot_logger.warning(f"Access denied for /continue by {message.from_user.id}")
        console.print(f"[warning]Access denied for /continue by {message.from_user.id}[/warning]")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "Usage: /continue <tracking_number>\nExample: /continue TRK20231010120000ABC123")
        bot_logger.warning("Invalid /continue command format", extra={'user_id': message.from_user.id})
        console.print(f"[warning]Invalid /continue command format[/warning]")
        return
    tracking_number = sanitize_tracking_number(sanitize_input(parts[1].strip()))
    if not tracking_number:
        bot.reply_to(message, "Invalid tracking number.")
        bot_logger.error(f"Invalid tracking number: {parts[1]}", extra={'user_id': message.from_user.id})
        console.print(f"[error]Invalid tracking number: {parts[1]}[/error]")
        return
    try:
        if redis_client and safe_redis_operation(redis_client.hget, "paused_simulations", tracking_number) != "true":
            bot.reply_to(message, f"Simulation for `{tracking_number}` is not paused.", parse_mode='Markdown')
            bot_logger.warning(f"Simulation not paused: {tracking_number}", extra={'user_id': message.from_user.id})
            console.print(f"[warning]Simulation not paused: {tracking_number}[/warning]")
            return
        shipment = get_shipment_details(tracking_number)
        if not shipment:
            bot.reply_to(message, f"Shipment `{tracking_number}` not found.", parse_mode='Markdown')
            bot_logger.warning(f"Shipment not found: {tracking_number}", extra={'user_id': message.from_user.id})
            console.print(f"[warning]Shipment not found: {tracking_number}[/warning]")
            return
        if shipment['status'] in ['Delivered', 'Returned']:
            bot.reply_to(message, f"Shipment `{tracking_number}` is already completed (`{shipment['status']}`).", parse_mode='Markdown')
            bot_logger.warning(f"Cannot resume completed shipment: {tracking_number}", extra={'user_id': message.from_user.id})
            console.print(f"[warning]Cannot resume completed shipment: {tracking_number}[/warning]")
            return
        if redis_client:
            safe_redis_operation(redis_client.hdel, "paused_simulations", tracking_number)
        invalidate_cache(tracking_number)
        bot_logger.info(f"Resumed simulation for {tracking_number}", extra={'user_id': message.from_user.id})
        console.print(f"[info]Resumed simulation for {tracking_number}[/info]")
        bot.reply_to(message, f"Simulation resumed for `{tracking_number}`.", parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")
        bot_logger.error(f"Error in continue command: {e}", extra={'user_id': message.from_user.id})
        console.print(Panel(f"[error]Error in continue command: {e}[/error]", title="Continue Error", border_style="red"))

@bot.message_handler(commands=['setspeed'])
@rate_limit
def set_simulation_speed(message):
    """Handle /setspeed command to set simulation speed for a shipment."""
    db, Shipment, sanitize_tracking_number, _, _, _, _ = get_app_modules()
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "Access denied.")
        bot_logger.warning(f"Access denied for /setspeed by {message.from_user.id}")
        console.print(f"[warning]Access denied for /setspeed by {message.from_user.id}[/warning]")
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        bot.reply_to(message, "Usage: /setspeed <tracking_number> <speed>\nExample: /setspeed TRK20231010120000ABC123 2.0")
        bot_logger.warning("Invalid /setspeed command format", extra={'user_id': message.from_user.id})
        console.print(f"[warning]Invalid /setspeed command format[/warning]")
        return
    tracking_number = sanitize_tracking_number(sanitize_input(parts[1].strip()))
    if not tracking_number:
        bot.reply_to(message, "Invalid tracking number.")
        bot_logger.error(f"Invalid tracking number: {parts[1]}", extra={'user_id': message.from_user.id})
        console.print(f"[error]Invalid tracking number: {parts[1]}[/error]")
        return
    try:
        speed = float(sanitize_input(parts[2].strip()))
        if speed < 0.1 or speed > 10:
            bot.reply_to(message, "Speed must be between 0.1 and 10.0.")
            bot_logger.warning(f"Invalid speed value: {speed}", extra={'user_id': message.from_user.id})
            console.print(f"[warning]Invalid speed value: {speed}[/warning]")
            return
        shipment = get_shipment_details(tracking_number)
        if not shipment:
            bot.reply_to(message, f"Shipment `{tracking_number}` not found.", parse_mode='Markdown')
            bot_logger.warning(f"Shipment not found: {tracking_number}", extra={'user_id': message.from_user.id})
            console.print(f"[warning]Shipment not found: {tracking_number}[/warning]")
            return
        if redis_client:
            safe_redis_operation(redis_client.hset, "sim_speed_multipliers", tracking_number, str(speed))
        invalidate_cache(tracking_number)
        bot_logger.info(f"Set simulation speed for {tracking_number} to {speed}x", extra={'user_id': message.from_user.id})
        console.print(f"[info]Set simulation speed for {tracking_number} to {speed}x[/info]")
        bot.reply_to(message, f"Simulation speed set to `{speed}x` for `{tracking_number}`.", parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")
        bot_logger.error(f"Error in setspeed command: {e}", extra={'user_id': message.from_user.id})
        console.print(Panel(f"[error]Error in setspeed command: {e}[/error]", title="Setspeed Error", border_style="red"))

# Callback Handlers
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    """Handle all callback queries from inline buttons."""
    data = call.data
    try:
        if data.startswith("menu_page_"):
            page = int(data.split("_")[-1])
            send_dynamic_menu(call.message.chat.id, call.message.message_id, page)
        elif data.startswith("view_"):
            tracking_number, page = data.split("_")[1], int(data.split("_")[2])
            show_shipment_details(call, tracking_number, page)
        elif data.startswith("delete_"):
            tracking_number, page = data.split("_")[1], int(data.split("_")[2])
            delete_shipment(call, tracking_number, page)
        elif data.startswith("batch_select_"):
            tracking_number = data.split("_", 2)[2]
            toggle_batch_selection(call, tracking_number)
        elif data.startswith("batch_delete_confirm_"):
            page = int(data.split("_")[-1])
            batch_delete_shipments(call, page)
        elif data.startswith("broadcast_"):
            tracking_number = data.split("_", 1)[1]
            trigger_broadcast(call, tracking_number)
        elif data.startswith("notify_"):
            tracking_number = data.split("_", 1)[1]
            shipment = get_shipment_details(tracking_number)
            if not shipment:
                bot.answer_callback_query(call.id, f"Shipment `{tracking_number}` not found.", show_alert=True)
                bot_logger.warning(f"Shipment not found: {tracking_number}", extra={'user_id': call.from_user.id})
                console.print(f"[warning]Shipment not found: {tracking_number}[/warning]")
                return
            queue_length = redis_client.llen("notification_queue") if redis_client else 0
            bot_logger.debug(f"Notification queue length: {queue_length}", extra={'tracking_number': tracking_number})
            console.print(f"[info]Notification queue length for {tracking_number}: {queue_length}[/info]")
            markup = InlineKeyboardMarkup(row_width=2)
            if shipment.get('recipient_email') and shipment.get('email_notifications'):
                markup.add(InlineKeyboardButton("Send Email", callback_data=f"send_email_{tracking_number}"))
            if shipment.get('webhook_url') or config.websocket_server:
                markup.add(InlineKeyboardButton("Send Webhook", callback_data=f"send_webhook_{tracking_number}"))
            markup.add(InlineKeyboardButton("Home", callback_data="menu_page_1"))
            bot.edit_message_text(f"Select notification type for `{tracking_number}`:", chat_id=call.message.chat.id,
                                 message_id=call.message.message_id, parse_mode='Markdown', reply_markup=markup)
        elif data.startswith("send_email_"):
            tracking_number = data.split("_", 2)[2]
            queue_length = redis_client.llen("notification_queue") if redis_client else 0
            bot_logger.debug(f"Enqueuing email notification, queue length: {queue_length}", extra={'tracking_number': tracking_number})
            console.print(f"[info]Enqueuing email notification for {tracking_number}, queue length: {queue_length}[/info]")
            send_manual_email(call, tracking_number)
        elif data.startswith("send_webhook_"):
            tracking_number = data.split("_", 2)[2]
            queue_length = redis_client.llen("notification_queue") if redis_client else 0
            bot_logger.debug(f"Enqueuing webhook notification, queue length: {queue_length}", extra={'tracking_number': tracking_number})
            console.print(f"[info]Enqueuing webhook notification for {tracking_number}, queue length: {queue_length}[/info]")
            send_manual_webhook(call, tracking_number)
        elif data.startswith("toggle_email_menu_"):
            page = int(data.split("_")[-1])
            show_shipment_menu(call, page, prefix="toggle_email", prompt="Select shipment to toggle email notifications")
        elif data.startswith("toggle_email_"):
            tracking_number, page = data.split("_")[1], int(data.split("_")[2])
            toggle_email_notifications(call, tracking_number, page)
        elif data.startswith("pause_"):
            tracking_number, page = data.split("_")[1], int(data.split("_")[2])
            pause_simulation_callback(call, tracking_number, page)
            invalidate_cache(tracking_number)
        elif data.startswith("resume_"):
            tracking_number, page = data.split("_")[1], int(data.split("_")[2])
            resume_simulation_callback(call, tracking_number, page)
            invalidate_cache(tracking_number)
        elif data.startswith("setspeed_"):
            tracking_number = data.split("_", 1)[1]
            bot.answer_callback_query(call.id, f"Enter speed for `{tracking_number}` (0.1 to 10.0):", show_alert=True)
            bot.register_next_step_handler(call.message, lambda msg: handle_set_speed(msg, tracking_number))
        elif data.startswith("getspeed_"):
            tracking_number = data.split("_", 1)[1]
            show_simulation_speed(call, tracking_number)
        elif data.startswith("bulk_pause_menu_"):
            page = int(data.split("_")[-1])
            show_shipment_menu(call, page, prefix="bulk_pause", prompt="Select shipments to pause",
                              extra_buttons=[InlineKeyboardButton("Confirm Pause", callback_data=f"bulk_pause_confirm_{page}"),
                                            InlineKeyboardButton("Home", callback_data="menu_page_1")])
        elif data.startswith("bulk_resume_menu_"):
            page = int(data.split("_")[-1])
            show_shipment_menu(call, page, prefix="bulk_resume", prompt="Select shipments to resume",
                              extra_buttons=[InlineKeyboardButton("Confirm Resume", callback_data=f"bulk_resume_confirm_{page}"),
                                            InlineKeyboardButton("Home", callback_data="menu_page_1")])
        elif data.startswith("bulk_pause_confirm_"):
            page = int(data.split("_")[-1])
            bulk_pause_shipments(call, page)
        elif data.startswith("bulk_resume_confirm_"):
            page = int(data.split("_")[-1])
            bulk_resume_shipments(call, page)
        elif data == "generate_id":
            new_id = generate_unique_id()
            bot.answer_callback_query(call.id, f"Generated ID: `{new_id}`", show_alert=True)
            bot_logger.info(f"Generated ID {new_id} for admin {call.from_user.id}")
            console.print(f"[info]Generated ID {new_id} for admin {call.from_user.id}[/info]")
        elif data == "add":
            bot.answer_callback_query(call.id, "Enter shipment details (tracking_number status delivery_location [recipient_email] [origin_location] [webhook_url]):", show_alert=True)
            bot.register_next_step_handler(call.message, handle_add_shipment)
        elif data.startswith("set_webhook_"):
            tracking_number = data.split("_", 2)[2]
            bot.answer_callback_query(call.id, f"Enter webhook URL for `{tracking_number}`:", show_alert=True)
            bot.register_next_step_handler(call.message, lambda msg: handle_set_webhook(msg, tracking_number))
        elif data.startswith("test_webhook_"):
            tracking_number = data.split("_", 2)[2]
            queue_length = redis_client.llen("notification_queue") if redis_client else 0
            bot_logger.debug(f"Enqueuing test webhook, queue length: {queue_length}", extra={'tracking_number': tracking_number})
            console.print(f"[info]Enqueuing test webhook for {tracking_number}, queue length: {queue_length}[/info]")
            send_manual_webhook(call, tracking_number)
        elif data.startswith("list_"):
            page = int(data.split("_")[-1])
            show_shipment_menu(call, page, prefix="view", prompt="Select shipment to view")
        elif data.startswith("delete_menu_"):
            page = int(data.split("_")[-1])
            show_shipment_menu(call, page, prefix="delete", prompt="Select shipment to delete")
        elif data.startswith("batch_delete_menu_"):
            page = int(data.split("_")[-1])
            show_shipment_menu(call, page, prefix="batch_select", prompt="Select shipments to delete",
                              extra_buttons=[InlineKeyboardButton("Confirm Delete", callback_data=f"batch_delete_confirm_{page}"),
                                            InlineKeyboardButton("Home", callback_data="menu_page_1")])
        elif data.startswith("broadcast_menu_"):
            page = int(data.split("_")[-1])
            show_shipment_menu(call, page, prefix="broadcast", prompt="Select shipment to broadcast")
        elif data.startswith("setspeed_menu_"):
            page = int(data.split("_")[-1])
            show_shipment_menu(call, page, prefix="setspeed", prompt="Select shipment to set simulation speed")
        elif data.startswith("getspeed_menu_"):
            page = int(data.split("_")[-1])
            show_shipment_menu(call, page, prefix="getspeed", prompt="Select shipment to view simulation speed")
        elif data == "settings":
            bot.edit_message_text("Settings: Not implemented yet.", chat_id=call.message.chat.id,
                                 message_id=call.message.message_id)
            bot_logger.info(f"Settings accessed by admin {call.from_user.id}")
            console.print(f"[info]Settings accessed by admin {call.from_user.id}[/info]")
        elif data == "help":
            bot.edit_message_text(
                "*Help Menu*\n"
                "Available commands:\n"
                "/start or /menu - Show main menu\n"
                "/myid - Get your Telegram ID\n"
                "/track <tracking_number> - Track a shipment\n"
                "/stats - View system statistics\n"
                "/notify <tracking_number> - Send manual notification\n"
                "/search <query> - Search shipments\n"
                "/bulk_action - Perform bulk operations\n"
                "/stop <tracking_number> - Pause simulation\n"
                "/continue <tracking_number> - Resume simulation\n"
                "/setspeed <tracking_number> <speed> - Set simulation speed\n"
                "Example: /track TRK20231010120000ABC123\n"
                "Example: /setspeed TRK20231010120000ABC123 2.0",
                chat_id=call.message.chat.id, message_id=call.message.message_id,
                parse_mode='Markdown', reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("Home", callback_data="menu_page_1")))
            bot_logger.info(f"Help menu accessed by admin {call.from_user.id}")
            console.print(f"[info]Help menu accessed by admin {call.from_user.id}[/info]")
        bot_logger.info(f"Processed callback {data} from admin {call.from_user.id}")
        console.print(f"[info]Processed callback {data} from admin {call.from_user.id}[/info]")
    except Exception as e:
        bot.answer_callback_query(call.id, f"Error: {e}", show_alert=True)
        bot_logger.error(f"Error in callback handler: {e}", extra={'user_id': call.from_user.id})
        console.print(Panel(f"[error]Error in callback handler: {e}[/error]", title="Callback Error", border_style="red"))

def show_shipment_menu(call, page, prefix, prompt, extra_buttons=None):
    """Show a menu of shipments for a specific action."""
    shipments, total = get_shipment_list(page=page)
    if not shipments:
        bot.edit_message_text(f"No shipments available.", chat_id=call.message.chat.id,
                             message_id=call.message.message_id)
        bot_logger.debug(f"No shipments available for {prefix}, page {page}", extra={'user_id': call.from_user.id})
        console.print(f"[info]No shipments available for {prefix}, page {page}[/info]")
        return
    markup = InlineKeyboardMarkup(row_width=1)
    for tn in shipments:
        markup.add(InlineKeyboardButton(tn, callback_data=f"{prefix}_{tn}_{page}"))
    if total > 5:
        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton("Previous", callback_data=f"{prefix}_menu_{page-1}"))
        if page * 5 < total:
            nav_buttons.append(InlineKeyboardButton("Next", callback_data=f"{prefix}_menu_{page+1}"))
        markup.add(*nav_buttons)
    if extra_buttons:
        markup.add(*extra_buttons)
    bot.edit_message_text(f"*{prompt}* (Page {page}, {total} total):", chat_id=call.message.chat.id,
                         message_id=call.message.message_id, parse_mode='Markdown', reply_markup=markup)
    bot_logger.info(f"Sent shipment menu for {prefix}, page {page}", extra={'user_id': call.from_user.id})
    console.print(f"[info]Sent shipment menu for {prefix}, page {page}[/info]")

def handle_add_shipment(message):
    """Handle adding a new shipment."""
    db, Shipment, sanitize_tracking_number, validate_email, validate_location, validate_webhook_url, _ = get_app_modules()
    parts = shlex.split(sanitize_input(message.text.strip()))
    if len(parts) < 3:
        bot.reply_to(message, "Usage: tracking_number status delivery_location [recipient_email] [origin_location] [webhook_url]\n"
                            "Example: TRK20231010120000ABC123 Pending 'Lagos, NG' user@example.com 'Abuja, NG' https://example.com")
        bot_logger.warning("Invalid add shipment input", extra={'user_id': message.from_user.id})
        console.print(f"[warning]Invalid add shipment input[/warning]")
        return
    tracking_number, status, delivery_location = parts[:3]
    recipient_email = parts[3] if len(parts) > 3 else ''
    origin_location = parts[4] if len(parts) > 4 else None
    webhook_url = parts[5] if len(parts) > 5 else None
    try:
        save_shipment(tracking_number, status, '', delivery_location, recipient_email, origin_location, webhook_url)
        bot.reply_to(message, f"Shipment `{tracking_number}` added.", parse_mode='Markdown')
        bot_logger.info(f"Added shipment {tracking_number} by admin {message.from_user.id}")
        console.print(f"[info]Added shipment {tracking_number} by admin {message.from_user.id}[/info]")
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")
        bot_logger.error(f"Error adding shipment: {e}", extra={'user_id': message.from_user.id})
        console.print(Panel(f"[error]Error adding shipment: {e}[/error]", title="Add Shipment Error", border_style="red"))

def handle_set_speed(message, tracking_number):
    """Handle setting simulation speed for a shipment."""
    try:
        speed = float(sanitize_input(message.text.strip()))
        if speed < 0.1 or speed > 10:
            bot.reply_to(message, "Speed must be between 0.1 and 10.0.")
            bot_logger.warning(f"Invalid speed value: {speed}", extra={'user_id': message.from_user.id})
            console.print(f"[warning]Invalid speed value: {speed}[/warning]")
            return
        shipment = get_shipment_details(tracking_number)
        if not shipment:
            bot.reply_to(message, f"Shipment `{tracking_number}` not found.", parse_mode='Markdown')
            bot_logger.warning(f"Shipment not found: {tracking_number}", extra={'user_id': message.from_user.id})
            console.print(f"[warning]Shipment not found: {tracking_number}[/warning]")
            return
        if redis_client:
            safe_redis_operation(redis_client.hset, "sim_speed_multipliers", tracking_number, str(speed))
        invalidate_cache(tracking_number)
        bot_logger.info(f"Set simulation speed for {tracking_number} to {speed}x", extra={'user_id': message.from_user.id})
        console.print(f"[info]Set simulation speed for {tracking_number} to {speed}x[/info]")
        bot.reply_to(message, f"Simulation speed set to `{speed}x` for `{tracking_number}`.", parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")
        bot_logger.error(f"Error setting speed: {e}", extra={'user_id': message.from_user.id})
        console.print(Panel(f"[error]Error setting speed: {e}[/error]", title="Set Speed Error", border_style="red"))

def handle_set_webhook(message, tracking_number):
    """Handle setting webhook URL for a shipment."""
    db, Shipment, _, _, _, validate_webhook_url, _ = get_app_modules()
    webhook_url = sanitize_input(message.text.strip())
    if not validate_webhook_url(webhook_url):
        bot.reply_to(message, "Invalid webhook URL.")
        bot_logger.warning(f"Invalid webhook URL: {webhook_url}", extra={'user_id': message.from_user.id})
        console.print(f"[warning]Invalid webhook URL: {webhook_url}[/warning]")
        return
    try:
        shipment = Shipment.query.filter_by(tracking_number=tracking_number).first()
        if not shipment:
            bot.reply_to(message, f"Shipment `{tracking_number}` not found.", parse_mode='Markdown')
            bot_logger.warning(f"Shipment not found: {tracking_number}", extra={'user_id': message.from_user.id})
            console.print(f"[warning]Shipment not found: {tracking_number}[/warning]")
            return
        shipment.webhook_url = webhook_url
        db.session.commit()
        invalidate_cache(tracking_number)
        bot_logger.info(f"Set webhook URL for {tracking_number}", extra={'user_id': message.from_user.id})
        console.print(f"[info]Set webhook URL for {tracking_number}[/info]")
        bot.reply_to(message, f"Webhook URL set for `{tracking_number}`.", parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")
        bot_logger.error(f"Error setting webhook: {e}", extra={'user_id': message.from_user.id})
        console.print(Panel(f"[error]Error setting webhook: {e}[/error]", title="Set Webhook Error", border_style="red"))

if __name__ == "__main__":
    try:
        bot.infinity_polling()
    except Exception as e:
        bot_logger.error(f"Bot polling error: {e}")
        console.print(Panel(f"[error]Bot polling error: {e}[/error]", title="Polling Error", border_style="red"))
        raise
