"""
BlazeMarket Keyboards - Inline keyboards for Telegram bot
Lime + Graphite theme design
"""
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton as Btn


def start_menu():
    """Main start menu - choose buyer or seller"""
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        Btn("👤 Я покупатель", callback_data="role_buyer"),
        Btn("💼 Я продавец", callback_data="role_seller")
    )
    return kb


def buyer_menu():
    """Buyer main menu"""
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn("📋 У меня есть заказ", callback_data="buyer_have_order"),
        Btn("🛒 Я хочу купить", callback_data="buyer_want_buy")
    )
    kb.row(Btn("◀️ Назад", callback_data="start"))
    return kb


def seller_menu():
    """Seller main menu"""
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        Btn("💰 Купить бота за 1000₽", callback_data="seller_buy_bot"),
        Btn("🏪 Создать магазин бесплатно", callback_data="seller_create_shop"),
        Btn("⚙️ Настроить наценку", callback_data="seller_set_markup")
    )
    kb.row(Btn("◀️ Назад", callback_data="start"))
    return kb


def services_categories():
    """Service categories with social media icons"""
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn("📱 Telegram", callback_data="cat_telegram"),
        Btn("📸 Instagram", callback_data="cat_instagram")
    )
    kb.add(
        Btn("🎵 TikTok", callback_data="cat_tiktok"),
        Btn("📺 YouTube", callback_data="cat_youtube")
    )
    kb.add(
        Btn("🐦 Twitter/X", callback_data="cat_twitter"),
        Btn("📘 Facebook", callback_data="cat_facebook")
    )
    kb.add(
        Btn("🔵 VKontakte", callback_data="cat_vk"),
        Btn("📦 Другое", callback_data="cat_other")
    )
    kb.row(Btn("◀️ К категориям", callback_data="buyer_want_buy"))
    return kb


def services_list(services, category):
    """List of services in a category"""
    kb = InlineKeyboardMarkup(row_width=1)
    for service in services[:10]:  # Show max 10 services per page
        name = service['name'][:40]
        price = f"{service['rate']:.2f}₽"
        kb.add(Btn(f"{name} - {price}", callback_data=f"service_{service['id']}"))
    
    kb.row(Btn("◀️ К категориям", callback_data="buyer_want_buy"))
    return kb


def enter_code_keyboard():
    """Keyboard for entering order code"""
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(Btn("◀️ Назад", callback_data="buyer_have_order"))
    return kb


def comments_submit_keyboard():
    """Keyboard after submitting comments"""
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn("✅ Подтвердить", callback_data="comments_confirm"),
        Btn("✏️ Изменить", callback_data="comments_edit")
    )
    return kb


def payment_keyboard(amount, order_id):
    """Payment keyboard with Pally integration"""
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        Btn(f"💳 Оплатить {amount}₽", callback_data=f"pay_{order_id}")
    )
    kb.add(
        Btn("❌ Отмена", callback_data="cancel_order")
    )
    return kb


def mirror_setup_keyboard():
    """Keyboard for mirror shop setup"""
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        Btn("🔗 Получить ссылку на зеркало", callback_data="mirror_get_link"),
        Btn("💹 Установить наценку", callback_data="mirror_set_markup"),
        Btn("📊 Статистика", callback_data="mirror_stats")
    )
    kb.row(Btn("◀️ Назад", callback_data="seller_menu"))
    return kb


def admin_menu():
    """Admin panel keyboard"""
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn("👥 Пользователи", callback_data="admin_users"),
        Btn("📦 Заказы", callback_data="admin_orders")
    )
    kb.add(
        Btn("🏪 Зеркала", callback_data="admin_mirrors"),
        Btn("⚙️ Настройки", callback_data="admin_settings")
    )
    kb.add(
        Btn("📊 Статистика", callback_data="admin_stats"),
        Btn("💰 Финансы", callback_data="admin_finance")
    )
    return kb


def back_keyboard(back_callback="start"):
    """Simple back button"""
    kb = InlineKeyboardMarkup(row_width=1)
    kb.row(Btn("◀️ Назад", callback_data=back_callback))
    return kb
