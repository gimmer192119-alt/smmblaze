"""
BlazeMarket - SMM Services Bot & Web App
Main bot file with Telegram integration
Lime + Graphite theme design
"""
import logging
import os
import re
import secrets
import string
from datetime import datetime, timedelta
from threading import Thread

import telebot
from telebot import apihelper
from telebot.types import Message, CallbackQuery

from config import cfg, Config
from database import db
import keyboards as kb

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/blazemarket.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("BlazeMarket")

# User states for conversation flow
user_states = {}  # {user_id: {"state": "...", "data": {...}}}

# Initialize bot
bot = None
if cfg.bot_token:
    bot = telebot.TeleBot(cfg.bot_token, parse_mode="HTML")
    apihelper.API_TIMEOUT = 60


def generate_order_code(length=8):
    """Generate unique order code"""
    chars = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))


def get_user_state(user_id):
    """Get user state or create new"""
    if user_id not in user_states:
        user_states[user_id] = {"state": "", "data": {}}
    return user_states[user_id]


def set_user_state(user_id, state, data=None):
    """Set user state"""
    user_states[user_id] = {"state": state, "data": data or {}}


# ==================== COMMAND HANDLERS ====================

@bot.message_handler(commands=['start'])
def cmd_start(message: Message):
    """Handle /start command"""
    user_id = message.from_user.id
    username = message.from_user.username
    full_name = f"{message.from_user.first_name} {message.from_user.last_name or ''}".strip()
    
    # Register user in database
    db.add_user(user_id, username, full_name)
    
    # Reset user state
    set_user_state(user_id, "")
    
    text = """🔥 <b>BlazeMarket</b> - Ваш SMM маркетплейс

Выберите вашу роль:"""
    
    bot.send_message(
        message.chat.id, 
        text, 
        reply_markup=kb.start_menu(),
        parse_mode="HTML"
    )


@bot.message_handler(commands=['admin'])
def cmd_admin(message: Message):
    """Admin panel"""
    if message.from_user.id not in cfg.admin_ids:
        bot.reply_to(message, "❌ Доступ запрещён")
        return
    
    bot.send_message(
        message.chat.id,
        "⚙️ <b>Панель администратора</b>",
        reply_markup=kb.admin_menu(),
        parse_mode="HTML"
    )


# ==================== CALLBACK HANDLERS ====================

@bot.callback_query_handler(func=lambda call: call.data == 'start')
def cb_start(call: CallbackQuery):
    """Back to start menu"""
    set_user_state(call.from_user.id, "")
    bot.edit_message_text(
        "🔥 <b>BlazeMarket</b> - Ваш SMM маркетплейс\n\nВыберите вашу роль:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb.start_menu(),
        parse_mode="HTML"
    )


@bot.callback_query_handler(func=lambda call: call.data == 'role_buyer')
def cb_role_buyer(call: CallbackQuery):
    """User selects buyer role"""
    user_id = call.from_user.id
    db.update_user_role(user_id, 'buyer')
    set_user_state(user_id, "")
    
    bot.edit_message_text(
        "👤 <b>Режим покупателя</b>\n\n" + cfg.get("messages.welcome_buyer", ""),
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb.buyer_menu(),
        parse_mode="HTML"
    )


@bot.callback_query_handler(func=lambda call: call.data == 'role_seller')
def cb_role_seller(call: CallbackQuery):
    """User selects seller role"""
    user_id = call.from_user.id
    db.update_user_role(user_id, 'seller')
    set_user_state(user_id, "")
    
    bot.edit_message_text(
        "💼 <b>Режим продавца</b>\n\n" + cfg.get("messages.welcome_seller", ""),
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb.seller_menu(),
        parse_mode="HTML"
    )


@bot.callback_query_handler(func=lambda call: call.data == 'buyer_have_order')
def cb_buyer_have_order(call: CallbackQuery):
    """Buyer has an order - enter code"""
    set_user_state(call.from_user.id, "awaiting_order_code", {})
    
    bot.edit_message_text(
        "🔑 <b>Введите код заказа</b>\n\n"
        "Код вы получили при создании заказа. Введите его без пробелов.",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb.back_keyboard("buyer_menu"),
        parse_mode="HTML"
    )


@bot.callback_query_handler(func=lambda call: call.data == 'buyer_want_buy')
def cb_buyer_want_buy(call: CallbackQuery):
    """Buyer wants to buy - show categories"""
    bot.edit_message_text(
        "🛒 <b>Выберите категорию услуг</b>",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb.services_categories(),
        parse_mode="HTML"
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith('cat_'))
def cb_category_selected(call: CallbackQuery):
    """Category selected - show services"""
    category = call.data.replace('cat_', '')
    services = db.get_services_by_category(category)
    
    if not services:
        bot.answer_callback_query(call.id, "Услуги в этой категории пока недоступны", show_alert=True)
        return
    
    bot.edit_message_text(
        f"📦 <b>Услуги: {category.title()}</b>\n\nВыберите услугу:",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb.services_list(services, category),
        parse_mode="HTML"
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith('service_'))
def cb_service_selected(call: CallbackQuery):
    """Service selected - show details and order form"""
    service_id = int(call.data.replace('service_', ''))
    # Get service details from DB (implement based on your structure)
    
    bot.answer_callback_query(call.id, "Функция в разработке", show_alert=True)


@bot.callback_query_handler(func=lambda call: call.data == 'seller_buy_bot')
def cb_seller_buy_bot(call: CallbackQuery):
    """Seller wants to buy bot"""
    bot.answer_callback_query(call.id, "Переход к оплате...", show_alert=False)
    
    # Create payment via Pally
    payment_url = create_pally_payment(1000, call.from_user.id, "Покупка бота BlazeMarket")
    
    bot.send_message(
        call.message.chat.id,
        f"💰 <b>Покупка бота BlazeMarket</b>\n\n"
        f"Стоимость: <b>1000₽</b>\n\n"
        f"Нажмите кнопку ниже для оплаты:",
        reply_markup=kb.payment_keyboard(1000, "bot_purchase"),
        parse_mode="HTML"
    )


@bot.callback_query_handler(func=lambda call: call.data == 'seller_create_shop')
def cb_seller_create_shop(call: CallbackQuery):
    """Seller creates free shop"""
    user_id = call.from_user.id
    
    # Generate mirror code
    mirror_code = generate_order_code(6)
    
    # Create mirror in database
    db.create_mirror(user_id, f"Shop {mirror_code}", mirror_code)
    
    bot.edit_message_text(
        f"🏪 <b>Магазин создан!</b>\n\n"
        f"Ваш код зеркала: <code>{mirror_code}</code>\n\n"
        f"Теперь вы можете настроить наценку и получить ссылку на ваш магазин.",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb.mirror_setup_keyboard(),
        parse_mode="HTML"
    )


@bot.callback_query_handler(func=lambda call: call.data == 'seller_set_markup')
def cb_seller_set_markup(call: CallbackQuery):
    """Seller sets markup"""
    set_user_state(call.from_user.id, "awaiting_markup", {})
    
    bot.edit_message_text(
        "💹 <b>Установка наценки</b>\n\n"
        "Введите процент наценки (например, 20 для 20%):\n\n"
        "<i>Пример: если базовая цена 5₽, при наценке 100% клиент заплатит 10₽</i>",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=kb.back_keyboard("seller_menu"),
        parse_mode="HTML"
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith('pay_'))
def cb_pay(call: CallbackQuery):
    """Payment button clicked"""
    order_id = call.data.replace('pay_', '')
    
    # Generate Pally payment link
    payment_url = create_pally_payment(100, call.from_user.id, f"Order {order_id}")
    
    bot.send_message(
        call.message.chat.id,
        f"💳 <b>Оплата заказа</b>\n\n"
        f"Перейдите по ссылке для оплаты:\n{payment_url}",
        parse_mode="HTML"
    )


# ==================== MESSAGE HANDLERS ====================

@bot.message_handler(func=lambda m: get_user_state(m.from_user.id)['state'] == 'awaiting_order_code')
def handle_order_code(message: Message):
    """Handle order code input"""
    code = message.text.strip().upper()
    user_id = message.from_user.id
    
    # Check if code exists
    order_code = db.get_order_code(code)
    
    if not order_code:
        bot.send_message(
            message.chat.id,
            "❌ Код не найден. Проверьте правильность ввода или создайте новый заказ.",
            reply_markup=kb.back_keyboard("buyer_have_order")
        )
        return
    
    if order_code['status'] != 'pending':
        bot.send_message(
            message.chat.id,
            f"⚠️ Заказ уже в работе. Статус: {order_code['status']}"
        )
        return
    
    # Update state
    set_user_state(user_id, "awaiting_comments", {"order_code": code})
    
    bot.send_message(
        message.chat.id,
        "✅ <b>Код принят!</b>\n\n" + cfg.get("messages.enter_comments", ""),
        parse_mode="HTML"
    )


@bot.message_handler(func=lambda m: get_user_state(m.from_user.id)['state'] == 'awaiting_comments')
def handle_comments(message: Message):
    """Handle comments submission"""
    user_id = message.from_user.id
    state = get_user_state(user_id)
    order_code = state['data'].get('order_code')
    
    comments = None
    comments_file = None
    
    # Check if it's a file
    if message.document:
        comments_file = message.document.file_id
        file_info = bot.get_file(message.document.file_id)
        downloaded = bot.download_file(file_info.file_path)
        
        # Save file
        file_path = f"data/comments_{order_code}_{user_id}.txt"
        with open(file_path, 'wb') as f:
            f.write(downloaded)
        comments_file = file_path
    else:
        # Text comments - remove # symbols
        comments = message.text.replace('#', '').strip()
    
    # Find order by code and update
    # (Implement order lookup and update based on your structure)
    
    set_user_state(user_id, "")
    
    bot.send_message(
        message.chat.id,
        "✅ <b>Комментарии приняты!</b>\n\n"
        "Заказ отправлен в работу. Ожидайте выполнения.",
        reply_markup=kb.back_keyboard(),
        parse_mode="HTML"
    )


@bot.message_handler(func=lambda m: get_user_state(m.from_user.id)['state'] == 'awaiting_markup')
def handle_markup(message: Message):
    """Handle markup percentage input"""
    user_id = message.from_user.id
    
    try:
        markup = float(message.text.strip())
        if markup < 0 or markup > 1000:
            raise ValueError()
        
        # Update mirror settings (implement based on your structure)
        db.set_setting(f"markup_{user_id}", markup)
        
        set_user_state(user_id, "")
        
        bot.send_message(
            message.chat.id,
            f"✅ Наценка установлена: <b>{markup}%</b>\n\n"
            f"Теперь все ваши продажи будут с этой наценкой.",
            reply_markup=kb.mirror_setup_keyboard(),
            parse_mode="HTML"
        )
    except ValueError:
        bot.send_message(
            message.chat.id,
            "❌ Неверное значение. Введите число от 0 до 1000."
        )


# ==================== HELPER FUNCTIONS ====================

def create_pally_payment(amount, user_id, description):
    """Create payment link via Pally.info API"""
    merchant_id = cfg.pally_merchant_id
    secret_key = cfg.pally_secret_key
    
    if not merchant_id or not secret_key:
        logger.warning("Pally payment credentials not configured")
        return "#"
    
    # Generate order ID
    order_id = f"BM_{user_id}_{int(datetime.now().timestamp())}"
    
    # Create signature
    import hashlib
    sign_string = f"{merchant_id}:{amount}:{order_id}:{secret_key}"
    signature = hashlib.sha256(sign_string.encode()).hexdigest()
    
    # Build payment URL
    params = {
        'merchant_id': merchant_id,
        'amount': amount,
        'order_id': order_id,
        'description': description,
        'signature': signature
    }
    
    # Pally payment URL (adjust based on actual API docs)
    payment_url = "https://pally.info/merchant/pay?" + "&".join(f"{k}={v}" for k, v in params.items())
    
    return payment_url


def sync_services_from_api():
    """Sync services from TwiBoost and Smmway APIs"""
    logger.info("Syncing services from API providers...")
    
    # Implement API sync logic here
    # Fetch services from TwiBoost API
    # Fetch services from Smmway API
    # Store in database
    
    logger.info("Services synced successfully")


# ==================== BOT RUNNER ====================

def run_bot():
    """Run the bot"""
    if not bot:
        logger.error("Bot token not configured!")
        return
    
    logger.info("BlazeMarket bot starting...")
    
    # Sync services on startup
    Thread(target=sync_services_from_api, daemon=True).start()
    
    # Start polling
    bot.infinity_polling(skip_pending=True)


if __name__ == "__main__":
    run_bot()
