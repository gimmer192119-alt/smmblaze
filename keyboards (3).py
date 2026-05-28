"""
Inline-клавиатуры Telegram бота
"""
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton as Btn


# ==================== ГЛАВНОЕ МЕНЮ ====================

def main_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn("🎮 FunPay", callback_data="funpay"),
        Btn("💰 Баланс", callback_data="balance"),
    )
    kb.add(
        Btn("🛒 Лоты", callback_data="lots"),
        Btn("📦 Заказы", callback_data="orders"),
    )
    kb.add(
        Btn("🌐 Сервисы API", callback_data="services"),
    )
    kb.add(
        Btn("🎫 Промокоды", callback_data="promos"),
        Btn("🎁 Допы", callback_data="upsells"),
    )
    kb.add(
        Btn("📊 Статистика", callback_data="stats"),
        Btn("💬 Шаблоны", callback_data="templates"),
    )
    kb.add(
        Btn("⚙️ Настройки", callback_data="settings"),
        Btn("📋 Логи", callback_data="logs"),
    )
    return kb


# ==================== ЛОТЫ ====================

def lots_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn("📋 Список лотов", callback_data="lots_list"),
        Btn("🔗 Привязать лот", callback_data="lot_add"),
    )
    kb.add(
        Btn("🔄 Синхронизация", callback_data="lots_sync"),
    )
    kb.row(Btn("◀️ Назад", callback_data="main"))
    return kb


def lot_item(lot_id):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn("✏️ Редактировать", callback_data=f"lot_edit_{lot_id}"),
        Btn("📊 Статистика", callback_data=f"lot_stats_{lot_id}"),
    )
    kb.add(
        Btn("🚀 Лот на FunPay", callback_data=f"lote_fpcreate_{lot_id}"),
    )
    kb.add(
        Btn("⏸ Вкл/Выкл", callback_data=f"lot_toggle_{lot_id}"),
        Btn("🗑 Удалить", callback_data=f"lot_del_{lot_id}"),
    )
    kb.row(Btn("◀️ К лотам", callback_data="lots"))
    return kb


def lot_edit(lot_id):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn("📝 Название", callback_data=f"lote_name_{lot_id}"),
        Btn("💰 Цена", callback_data=f"lote_price_{lot_id}"),
    )
    kb.add(
        Btn("📈 Наценка %", callback_data=f"lote_markup_{lot_id}"),
        Btn("🔗 Сервис API", callback_data=f"lote_service_{lot_id}"),
    )
    kb.add(
        Btn("📉 Мин. кол-во", callback_data=f"lote_min_{lot_id}"),
        Btn("📈 Макс. кол-во", callback_data=f"lote_max_{lot_id}"),
    )
    kb.add(Btn("🗳 Вариант голоса", callback_data=f"lote_vote_{lot_id}"))
    kb.row(Btn("◀️ Назад", callback_data=f"lot_{lot_id}"))
    return kb


def lots_list(lots, page=0, per_page=8):
    kb = InlineKeyboardMarkup(row_width=1)
    start = page * per_page
    end = start + per_page
    for lot in lots[start:end]:
        status = "✅" if lot["is_active"] else "⏸"
        kb.add(Btn(f"{status} {lot['name'][:35]} | {lot['price']}₽", callback_data=f"lot_{lot['id']}"))
    # Пагинация
    nav = []
    if page > 0:
        nav.append(Btn("⬅️", callback_data=f"lots_page_{page-1}"))
    if end < len(lots):
        nav.append(Btn("➡️", callback_data=f"lots_page_{page+1}"))
    if nav:
        kb.row(*nav)
    kb.row(Btn("◀️ Назад", callback_data="lots"))
    return kb


# ==================== ЗАКАЗЫ ====================

def orders_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn("📋 Все заказы", callback_data="orders_all"),
        Btn("⏳ Активные", callback_data="orders_active"),
    )
    kb.add(
        Btn("✅ Выполненные", callback_data="orders_completed"),
        Btn("❌ Ошибки", callback_data="orders_failed"),
    )
    kb.add(
        Btn("➕ Новый заказ", callback_data="order_new"),
        Btn("🔄 Проверить все", callback_data="orders_check_all"),
    )
    kb.row(Btn("◀️ Назад", callback_data="main"))
    return kb


def order_item(order_id, status="processing", allow_ticket=False):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(Btn("🔄 Обновить статус", callback_data=f"order_check_{order_id}"))
    if status in ("processing", "in_progress", "partial"):
        kb.add(
            Btn("🔁 Рефилл", callback_data=f"order_refill_{order_id}"),
            Btn("🚫 Отменить", callback_data=f"order_cancel_{order_id}"),
        )
    if allow_ticket:
        kb.add(Btn("🎫 Отправить тикет", callback_data=f"order_ticket_{order_id}"))
    kb.row(Btn("◀️ К заказам", callback_data="orders"))
    return kb


def orders_list(orders, page=0, per_page=8):
    kb = InlineKeyboardMarkup(row_width=1)
    status_icons = {"pending": "⏳", "processing": "🔄", "in_progress": "🔄", "completed": "✅", "partial": "⚠️", "failed": "❌", "cancelled": "🚫"}
    start = page * per_page
    end = start + per_page
    for o in orders[start:end]:
        icon = status_icons.get(o["status"], "❓")
        name = (o["service_name"] or o["lot_name"])[:25]
        kb.add(Btn(f"{icon} #{o['id']} {name}", callback_data=f"order_{o['id']}"))
    nav = []
    if page > 0:
        nav.append(Btn("⬅️", callback_data=f"ordp_{page-1}"))
    if end < len(orders):
        nav.append(Btn("➡️", callback_data=f"ordp_{page+1}"))
    if nav:
        kb.row(*nav)
    kb.row(Btn("◀️ Назад", callback_data="orders"))
    return kb


# ==================== СЕРВИСЫ ====================

def services_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn("📋 Все сервисы", callback_data="svc_list"),
        Btn("🔄 Загрузить с API", callback_data="svc_sync"),
    )
    kb.add(
        Btn("📂 По категориям", callback_data="svc_cats"),
        Btn("🔍 Поиск", callback_data="svc_search"),
    )
    kb.row(Btn("◀️ Назад", callback_data="main"))
    return kb


def services_list(services, page=0, per_page=8):
    kb = InlineKeyboardMarkup(row_width=1)
    start = page * per_page
    end = start + per_page
    for s in services[start:end]:
        name = s["name"][:30]
        kb.add(Btn(f"#{s['service_id']} {name} | ${s['rate']}", callback_data=f"svc_{s['service_id']}"))
    nav = []
    if page > 0:
        nav.append(Btn("⬅️", callback_data=f"svcp_{page-1}"))
    if end < len(services):
        nav.append(Btn("➡️", callback_data=f"svcp_{page+1}"))
    if nav:
        kb.row(*nav)
    kb.row(Btn("◀️ Назад", callback_data="services"))
    return kb


def service_categories(categories):
    kb = InlineKeyboardMarkup(row_width=1)
    for cat in categories[:20]:
        short = cat[:40]
        kb.add(Btn(f"📂 {short}", callback_data=f"svc_cat_{hash(cat) % 100000}"))
    kb.row(Btn("◀️ Назад", callback_data="services"))
    return kb


def service_item(svc_id):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn("➕ Создать лот", callback_data=f"svc_to_lot_{svc_id}"),
        Btn("🛒 Заказать", callback_data=f"svc_order_{svc_id}"),
    )
    kb.row(Btn("◀️ Назад", callback_data="services"))
    return kb


# ==================== ПРОМОКОДЫ ====================

def promos_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn("📋 Список", callback_data="promo_list"),
        Btn("➕ Создать", callback_data="promo_add"),
    )
    kb.row(Btn("◀️ Назад", callback_data="main"))
    return kb


def promos_list(promos, page=0, per_page=8):
    kb = InlineKeyboardMarkup(row_width=1)
    start = page * per_page
    end = start + per_page
    for p in promos[start:end]:
        status = "✅" if p["is_active"] else "⏸"
        val = f"{p['discount_value']}%" if p["discount_type"] == "percent" else f"{p['discount_value']}₽"
        kb.add(Btn(f"{status} {p['code']} — {val} ({p['used_count']}/{p['max_uses']})", callback_data=f"promo_{p['id']}"))
    nav = []
    if page > 0:
        nav.append(Btn("⬅️", callback_data=f"promp_{page-1}"))
    if end < len(promos):
        nav.append(Btn("➡️", callback_data=f"promp_{page+1}"))
    if nav:
        kb.row(*nav)
    kb.row(Btn("◀️ Назад", callback_data="promos"))
    return kb


def promo_item(promo_id):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn("⏸ Вкл/Выкл", callback_data=f"promo_toggle_{promo_id}"),
        Btn("🗑 Удалить", callback_data=f"promo_del_{promo_id}"),
    )
    kb.row(Btn("◀️ Назад", callback_data="promos"))
    return kb


# ==================== ДОПЫ ====================

def upsells_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn("📋 Список", callback_data="upsell_list"),
        Btn("➕ Создать", callback_data="upsell_add"),
    )
    kb.row(Btn("◀️ Назад", callback_data="main"))
    return kb


def upsell_item(upsell_id):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn("✏️ Редактировать", callback_data=f"upsell_edit_{upsell_id}"),
        Btn("⏸ Вкл/Выкл", callback_data=f"upsell_toggle_{upsell_id}"),
    )
    kb.add(Btn("🗑 Удалить", callback_data=f"upsell_del_{upsell_id}"))
    kb.row(Btn("◀️ Назад", callback_data="upsells"))
    return kb


# ==================== ШАБЛОНЫ ====================

def templates_menu():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        Btn("📋 Все шаблоны", callback_data="tpl_list"),
        Btn("➕ Добавить", callback_data="tpl_add"),
    )
    kb.row(Btn("◀️ Назад", callback_data="main"))
    return kb


def template_item(tpl_name):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn("✏️ Редактировать", callback_data=f"tpl_edit_{tpl_name}"),
        Btn("⏸ Вкл/Выкл", callback_data=f"tpl_toggle_{tpl_name}"),
    )
    kb.row(Btn("◀️ Назад", callback_data="templates"))
    return kb


# ==================== СТАТИСТИКА ====================

def stats_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn("📊 Общая", callback_data="stats_all"),
        Btn("📅 Сегодня", callback_data="stats_today"),
    )
    kb.add(
        Btn("📆 Неделя", callback_data="stats_week"),
        Btn("📆 Месяц", callback_data="stats_month"),
    )
    kb.add(
        Btn("📤 Экспорт заказов CSV", callback_data="export_orders"),
        Btn("📤 Экспорт стат. JSON", callback_data="export_stats"),
    )
    kb.row(Btn("◀️ Назад", callback_data="main"))
    return kb


# ==================== НАСТРОЙКИ ====================

def settings_menu():
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        Btn("🎮 Golden Key (FunPay)", callback_data="set_golden_key"),
        Btn("🔑 API ключ TwiBoost", callback_data="set_api_key"),
        Btn("💱 Курс USD/RUB", callback_data="set_usd_rate"),
        Btn("🔔 Уведомления", callback_data="set_notif"),
        Btn("⏱ Интервал проверки заказов", callback_data="set_check_interval"),
        Btn("⏱ Интервал FunPay", callback_data="set_fp_interval"),
        Btn("💰 Порог низкого баланса", callback_data="set_low_balance"),
        Btn("💾 Создать бэкап", callback_data="backup"),
    )
    kb.row(Btn("◀️ Назад", callback_data="main"))
    return kb


def notif_settings(cfg):
    kb = InlineKeyboardMarkup(row_width=1)
    n = cfg.get("notifications", {})
    for key, label in [
        ("new_order", "🛒 Новый заказ"),
        ("buyer_message", "💬 Сообщение покупателя"),
        ("order_completed", "✅ Заказ завершён"),
        ("order_error", "❌ Ошибка заказа"),
        ("low_balance", "💰 Низкий баланс"),
        ("support_ticket", "🎫 Тикет через 24 часа"),
        ("review_bonus", "⭐ Бонус за 5★"),
        ("daily_report", "📊 Ежедневный отчёт"),
    ]:
        status = "✅" if n.get(key, True) else "❌"
        kb.add(Btn(f"{status} {label}", callback_data=f"notif_toggle_{key}"))
    kb.row(Btn("◀️ Назад", callback_data="settings"))
    return kb


# ==================== ЛОГИ ====================

def logs_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn("📋 Все", callback_data="logs_all"),
        Btn("❌ Ошибки", callback_data="logs_errors"),
    )
    kb.add(
        Btn("🧹 Очистить", callback_data="logs_cleanup"),
    )
    kb.row(Btn("◀️ Назад", callback_data="main"))
    return kb


# ==================== ПОДТВЕРЖДЕНИЕ ====================

def confirm(action, item_id=""):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn("✅ Да", callback_data=f"confirm_{action}_{item_id}"),
        Btn("❌ Нет", callback_data=f"cancel_{action}_{item_id}"),
    )
    return kb


def back(to="main"):
    kb = InlineKeyboardMarkup()
    kb.add(Btn("◀️ Назад", callback_data=to))
    return kb


# ==================== FUNPAY ====================

def funpay_menu(connected=False):
    kb = InlineKeyboardMarkup(row_width=1)
    if connected:
        kb.add(
            Btn("📋 Мои продажи", callback_data="fp_sales"),
            Btn("🛒 Мои лоты", callback_data="fp_lots"),
            Btn("🔄 Обновить статус", callback_data="fp_refresh"),
        )
    else:
        kb.add(Btn("🔑 Ввести Golden Key", callback_data="set_golden_key"))
    kb.row(Btn("◀️ Назад", callback_data="main"))
    return kb


def funpay_sales(orders, page=0, per_page=8):
    kb = InlineKeyboardMarkup(row_width=1)
    status_icons = {"paid": "💳", "closed": "✅", "refunded": "↩️"}
    start = page * per_page
    end = start + per_page
    for o in orders[start:end]:
        icon = status_icons.get(o.status, "❓")
        desc = o.description[:25] if o.description else "—"
        kb.add(Btn(f"{icon} #{o.order_id} {desc} | {o.price}{o.currency}", callback_data=f"fp_order_{o.order_id}"))
    nav = []
    if page > 0:
        nav.append(Btn("⬅️", callback_data=f"fp_salep_{page-1}"))
    if end < len(orders):
        nav.append(Btn("➡️", callback_data=f"fp_salep_{page+1}"))
    if nav:
        kb.row(*nav)
    kb.row(Btn("◀️ Назад", callback_data="funpay"))
    return kb


def funpay_order(order_id, status="paid"):
    kb = InlineKeyboardMarkup(row_width=2)
    if status == "paid":
        kb.add(Btn("⚡ Авто-обработать", callback_data=f"fp_process_{order_id}"))
    kb.add(Btn("💬 Написать покупателю", callback_data=f"fp_msg_{order_id}"))
    kb.row(Btn("◀️ К продажам", callback_data="fp_sales"))
    return kb


def funpay_lots(lots):
    kb = InlineKeyboardMarkup(row_width=1)
    for lot in lots[:15]:
        title = lot["title"][:35]
        oid = lot.get("offer_id", "")
        kb.add(Btn(f"🛒 {title} | {lot['price']}₽", callback_data=f"fp_lot_{oid}"))
    kb.row(Btn("◀️ Назад", callback_data="funpay"))
    return kb


def funpay_lot_detail(offer_id):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(Btn("🔗 Привязать к боту", callback_data=f"fp_bind_{offer_id}"))
    kb.row(Btn("◀️ К лотам", callback_data="fp_lots"))
    return kb


# ==================== ПРИВЯЗКИ ====================
# Legacy: removed manual bindings menu


def upsells_menu():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn("📋 Список", callback_data="upsell_list"),
        Btn("⚙️ Условие", callback_data="upsell_rule_add"),
    )
    kb.row(Btn("◀️ Назад", callback_data="main"))
    return kb
