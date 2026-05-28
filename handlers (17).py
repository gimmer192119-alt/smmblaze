"""
Telegram Bot Handlers — все команды, callback'и и состояния
"""
import html
import json
import logging
import os
import requests
import shutil
import subprocess
import sys
import time
import secrets
import string
import re
from pathlib import Path
from datetime import datetime, timedelta
from threading import Thread, Lock

import telebot
from telebot.types import Message, CallbackQuery

import keyboards as kb
from FunPayAPI.common.enums import MessageTypes
from config import Config, LICENSE_PLANS
from database import Database
from twiboost import TwiBoostAPI
from funpay import FunPayClient, FunPayEventType
from support_center import FunPaySupportClient
from create_mirror_instance import MIRRORS_DIR, load_base_config, build_mirror_config, write_launchers, write_readme, slugify

logger = logging.getLogger("SMM.handlers")

# 🔥 Отдельный логгер для заказов с комментариями
Path("logs").mkdir(parents=True, exist_ok=True)
comments_logger = logging.getLogger("SMM.comments")
comments_logger.setLevel(logging.INFO)
comments_handler = logging.FileHandler("logs/comments_orders.log", encoding="utf-8", mode="a")
comments_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s"))
comments_logger.addHandler(comments_handler)
comments_logger.propagate = False  # Не дублировать в основной лог

# Состояния для ввода текста
user_states = {}  # {user_id: {"state": "...", "data": {...}}}
recent_funpay_order_events = {}
recent_funpay_order_events_lock = Lock()
recent_funpay_payment_messages = {}
recent_funpay_payment_messages_lock = Lock()
recent_review_dispatch_events = {}
recent_review_dispatch_events_lock = Lock()

YES_WORDS = {"да", "yes", "y", "подтверждаю", "принял", "ок", "да, подтверждаю"}
NO_WORDS = {"нет", "no", "n", "отмена", "cancel", "не"}
LINK_REGEX = re.compile(r"https?://\S+")
PROMO_CODE_REGEX = re.compile(r"^[A-Za-z0-9_-]{4,32}$")
STATUS_WORDS = {"/status", "status", "info", "инфо", "статус"}
REFILL_WORDS = {"/refill", "refill", "рефил", "рефилл"}
SPLIT_WORDS = {"разделить", "split"}
LIST_WORDS = {"список", "list"}
REFUSE_BONUS_WORDS = {"без бонуса", "не нужен бонус", "не нужно", "не надо", "skip", "пропустить"}
KB_WORDS = {"база знаний", "faq", "помощь"}
SPEED_WORDS = {"скорость", "speed"}
FUNPAY_ORDER_ID_RE = re.compile(r"#([A-Z0-9]{6,12})")
STATUS_INDEX_RE = re.compile(r"^(?:/status|status|info|инфо|статус)\s*(\d+)$", re.IGNORECASE)
REFILL_INDEX_RE = re.compile(r"^(?:/refill|refill|рефил|рефилл)\s*(\d+)$", re.IGNORECASE)
FUNPAY_REVIEW_EVENT_TYPES = {
    MessageTypes.NEW_FEEDBACK,
    MessageTypes.FEEDBACK_CHANGED,
    MessageTypes.FEEDBACK_DELETED,
}
FUNPAY_CONFIRM_EVENT_TYPES = {
    MessageTypes.ORDER_CONFIRMED,
    MessageTypes.ORDER_CONFIRMED_BY_ADMIN,
}
TELEGRAM_REACTION_SERVICE_IDS = [
    2819, 2820, 2821, 2822, 2823, 2824, 2825, 2826, 2827, 2828, 2829, 2830,
    2831, 2832, 2833, 2834, 2835, 2836, 2837, 2838, 2839, 2840, 2841, 2842,
    2843, 2844, 2845, 2846, 2847, 2848, 2849, 2850, 2851, 2852, 2853, 2854,
    2855, 2856, 2857, 2858, 2859, 2860, 2861, 2862, 2863, 2864, 2866, 2865,
    2867, 2868, 2869, 2870, 2873, 2874, 2875, 2876, 2877, 2878, 2882, 2879,
    2883, 2886, 2885, 4046, 2888, 4048, 4031, 2881, 4047, 2871, 2880, 4029,
    2872, 4030,
]

# === РЕЕСТР ПРОВАЙДЕРОВ ===
_api_clients = {}
def get_api_client(provider_name):
    provider = (provider_name or "twiboost").lower()
    if provider in _api_clients:
        return _api_clients[provider]
    
    if provider == "twiboost" and cfg.twiboost_api_key:
        _api_clients[provider] = TwiBoostAPI(cfg.twiboost_api_key, cfg.twiboost_api_url, cfg.get("twiboost_web", {}))
    elif provider == "smmway" and cfg.get("smmway_api_key"):
        from smmway import SmmwayAPI
        _api_clients[provider] = SmmwayAPI(cfg.get("smmway_api_key"), cfg.get("smmway_api_url", "https://smmway.ru/api/v2"))
    return _api_clients.get(provider)

def get_enabled_providers():
    enabled = []
    if cfg.twiboost_api_key: enabled.append("twiboost")
    if cfg.get("smmway_api_key"): enabled.append("smmway")
    return enabled

def _kb_lot_mode_selector(back_callback, lot_id=None):
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    if lot_id is None:
        markup.add(
            telebot.types.InlineKeyboardButton("Обычный", callback_data="lotmode_add_normal"),
            telebot.types.InlineKeyboardButton("Голоса", callback_data="lotmode_add_vote"),
        )
        markup.add(
            telebot.types.InlineKeyboardButton("Реакции", callback_data="lotmode_add_reaction"),
            telebot.types.InlineKeyboardButton("Комментарии", callback_data="lotmode_add_comments"),
        )
    else:
        markup.add(
            telebot.types.InlineKeyboardButton("Обычный", callback_data=f"lotmode_edit_{lot_id}_normal"),
            telebot.types.InlineKeyboardButton("Голоса", callback_data=f"lotmode_edit_{lot_id}_vote"),
        )
        markup.add(
            telebot.types.InlineKeyboardButton("Реакции", callback_data=f"lotmode_edit_{lot_id}_reaction"),
            telebot.types.InlineKeyboardButton("Комментарии", callback_data=f"lotmode_edit_{lot_id}_comments"),
        )
    markup.row(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data=back_callback))
    return markup


def _kb_lot_edit(lot_id):
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton("📝 Название", callback_data=f"lote_name_{lot_id}"),
        telebot.types.InlineKeyboardButton("💰 Цена", callback_data=f"lote_price_{lot_id}"),
    )
    markup.add(
        telebot.types.InlineKeyboardButton("📈 Наценка %", callback_data=f"lote_markup_{lot_id}"),
        telebot.types.InlineKeyboardButton("🔗 Сервис API", callback_data=f"lote_service_{lot_id}"),
    )
    markup.add(
        telebot.types.InlineKeyboardButton("📉 Мин. кол-во", callback_data=f"lote_min_{lot_id}"),
        telebot.types.InlineKeyboardButton("📈 Макс. кол-во", callback_data=f"lote_max_{lot_id}"),
    )
    markup.add(
        telebot.types.InlineKeyboardButton("🗳 Режим заказа", callback_data=f"lote_mode_{lot_id}"),
        telebot.types.InlineKeyboardButton("🎁 Доп. отзыв", callback_data=f"lote_reviewbonus_{lot_id}"),
    )
    markup.add(
        telebot.types.InlineKeyboardButton("➗ Разделение", callback_data=f"lote_split_{lot_id}"),
    )
    markup.add(
    telebot.types.InlineKeyboardButton("🔁 Принуд. рефилл", callback_data=f"lote_force_refill_{lot_id}"),
    )
    markup.add(
        telebot.types.InlineKeyboardButton("🚀 Создать лот на FunPay", callback_data=f"lote_fpcreate_{lot_id}"),
    )
    markup.row(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data=f"lot_{lot_id}"))
    return markup


def _kb_funpay_runtime(connected=False):
    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    if connected:
        markup.add(
            telebot.types.InlineKeyboardButton("📋 Мои продажи", callback_data="fp_sales"),
            telebot.types.InlineKeyboardButton("🛒 Мои лоты", callback_data="fp_lots"),
            telebot.types.InlineKeyboardButton("💸 Вывод средств", callback_data="fp_withdraw"),
            telebot.types.InlineKeyboardButton("🔄 Обновить статус", callback_data="fp_refresh"),
        )
    else:
        markup.add(telebot.types.InlineKeyboardButton("🔑 Ввести Golden Key", callback_data="set_golden_key"))
    markup.row(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data="main"))
    return markup


def _kb_funpay_withdraw_menu():
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton("📱 Телефон", callback_data="fpw_wallet"),
        telebot.types.InlineKeyboardButton("🏦 Банк", callback_data="fpw_wallet_extra"),
    )
    markup.add(
        telebot.types.InlineKeyboardButton("💰 Сумма", callback_data="fpw_amount"),
        telebot.types.InlineKeyboardButton("🔐 2FA", callback_data="fpw_2fa"),
    )
    markup.add(
        telebot.types.InlineKeyboardButton("👀 Проверить вывод", callback_data="fpw_preview"),
    )
    markup.add(
        telebot.types.InlineKeyboardButton("✅ Отправить вывод", callback_data="fpw_submit"),
    )
    markup.add(
        telebot.types.InlineKeyboardButton("🤖 Автовывод", callback_data="fpw_auto_toggle"),
        telebot.types.InlineKeyboardButton("🎯 Порог", callback_data="fpw_auto_min"),
    )
    markup.row(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data="funpay"))
    return markup


def _kb_settings_menu():
    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        telebot.types.InlineKeyboardButton("🎮 Golden Key (FunPay)", callback_data="set_golden_key"),
        telebot.types.InlineKeyboardButton("🔑 API ключ TwiBoost", callback_data="set_api_key"),
        telebot.types.InlineKeyboardButton("🌐 API ключ SmmWay", callback_data="set_smmway_key"),
        telebot.types.InlineKeyboardButton("💱 Курс USD/RUB", callback_data="set_usd_rate"),
        telebot.types.InlineKeyboardButton("🔔 Уведомления", callback_data="set_notif"),
        telebot.types.InlineKeyboardButton("📚 База знаний", callback_data="kb_settings"),
        telebot.types.InlineKeyboardButton("⏱ Интервал проверки заказов", callback_data="set_check_interval"),
        telebot.types.InlineKeyboardButton("⏱ Интервал FunPay", callback_data="set_fp_interval"),
        telebot.types.InlineKeyboardButton("💰 Порог низкого баланса", callback_data="set_low_balance"),
        telebot.types.InlineKeyboardButton("💾 Создать бэкап", callback_data="backup"),
    )
    markup.row(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data="main"))
    return markup


def _kb_knowledge_base_menu(entries):
    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    enabled = "✅ Выключить базу знаний" if _knowledge_enabled() else "❌ Включить базу знаний"
    markup.add(telebot.types.InlineKeyboardButton(enabled, callback_data="kb_toggle"))
    markup.add(telebot.types.InlineKeyboardButton("✏️ Текст на приветствие", callback_data="kb_greeting"))
    markup.add(telebot.types.InlineKeyboardButton("➕ Добавить ответ", callback_data="kb_add"))
    for idx, entry in enumerate(entries[:12]):
        title = str(entry.get("title") or f"Ответ {idx + 1}").strip()[:34]
        markup.add(telebot.types.InlineKeyboardButton(f"🗑 {title}", callback_data=f"kb_del_{idx}"))
    markup.row(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data="settings"))
    return markup


def _kb_lots_menu():
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton("📋 Список лотов", callback_data="lots_list"),
        telebot.types.InlineKeyboardButton("🔗 Привязать лот", callback_data="lot_add"),
    )
    markup.add(
        telebot.types.InlineKeyboardButton("🚀 Лот на FunPay", callback_data="lots_fpcreate_pick"),
        telebot.types.InlineKeyboardButton("🔄 Синхронизация", callback_data="lots_sync"),
    )
    markup.row(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data="main"))
    return markup


def _kb_lots_fpcreate_picker(lots, page=0, per_page=8):
    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    start = page * per_page
    end = start + per_page
    for lot in lots[start:end]:
        title = str(lot.get("name") or "").strip()[:40]
        markup.add(
            telebot.types.InlineKeyboardButton(
                f"🚀 #{lot['id']} {title}",
                callback_data=f"fpcreatepick_{lot['id']}",
            )
        )
    nav = []
    if page > 0:
        nav.append(telebot.types.InlineKeyboardButton("⬅️", callback_data=f"lots_fpcreate_page_{page-1}"))
    if end < len(lots):
        nav.append(telebot.types.InlineKeyboardButton("➡️", callback_data=f"lots_fpcreate_page_{page+1}"))
    if nav:
        markup.row(*nav)
    markup.row(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data="lots"))
    return markup


def _kb_funpay_offer_preset_picker(lot_id, presets, back_callback):
    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    for preset in presets[:20]:
        node_id = int(preset.get("subcategory_id") or preset.get("node_id") or 0)
        if node_id <= 0:
            continue
        category_name = str(preset.get("category_name") or "").strip()
        subcategory_name = str(preset.get("subcategory_name") or "").strip()
        label = subcategory_name or category_name or f"Node {node_id}"
        if category_name and subcategory_name and category_name != subcategory_name:
            label = f"{category_name} → {subcategory_name}"
        markup.add(
            telebot.types.InlineKeyboardButton(
                f"📂 {label}",
                callback_data=f"lotfppreset_{lot_id}_{node_id}",
            )
        )
    markup.add(telebot.types.InlineKeyboardButton("🆔 Ввести node ID вручную", callback_data=f"lotfpmanual_{lot_id}"))
    markup.row(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data=back_callback))
    return markup


def _kb_funpay_offer_entry(lot_id, has_presets, back_callback, related_categories=False):
    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    if related_categories:
        markup.add(telebot.types.InlineKeyboardButton("🎯 Подходящие категории", callback_data=f"lotfprelated_{lot_id}"))
        markup.add(telebot.types.InlineKeyboardButton("📂 Все категории FunPay", callback_data=f"lotfpbrowse_{lot_id}"))
    else:
        markup.add(telebot.types.InlineKeyboardButton("📂 Выбрать категорию FunPay", callback_data=f"lotfpbrowse_{lot_id}"))
    if has_presets:
        markup.add(telebot.types.InlineKeyboardButton("⭐ Быстрые пресеты", callback_data=f"lotfppresets_{lot_id}"))
    markup.add(telebot.types.InlineKeyboardButton("🆔 Ввести node ID вручную", callback_data=f"lotfpmanual_{lot_id}"))
    markup.row(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data=back_callback))
    return markup


def _kb_funpay_categories(lot_id, categories, page=0, per_page=12, back_callback="lots_fpcreate_pick"):
    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    start = page * per_page
    end = start + per_page
    for category in categories[start:end]:
        title = str(category.get("name") or "").strip()[:48]
        markup.add(
            telebot.types.InlineKeyboardButton(
                f"📂 {title}",
                callback_data=f"lotfpcat_{lot_id}_{int(category.get('id') or 0)}",
            )
        )
    nav = []
    if page > 0:
        nav.append(telebot.types.InlineKeyboardButton("⬅️", callback_data=f"lotfpcatpage_{lot_id}_{page-1}"))
    if end < len(categories):
        nav.append(telebot.types.InlineKeyboardButton("➡️", callback_data=f"lotfpcatpage_{lot_id}_{page+1}"))
    if nav:
        markup.row(*nav)
    markup.row(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data=f"lotfpentry_{lot_id}"))
    return markup


def _kb_funpay_subcategories(lot_id, category_id, subcategories, page=0, per_page=12):
    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    start = page * per_page
    end = start + per_page
    for subcategory in subcategories[start:end]:
        title = str(subcategory.get("name") or "").strip()[:48]
        node_id = int(subcategory.get("id") or 0)
        if node_id <= 0:
            continue
        markup.add(
            telebot.types.InlineKeyboardButton(
                f"📄 {title}",
                callback_data=f"lotfpsub_{lot_id}_{node_id}",
            )
        )
    nav = []
    if page > 0:
        nav.append(telebot.types.InlineKeyboardButton("⬅️", callback_data=f"lotfpsubpage_{lot_id}_{category_id}_{page-1}"))
    if end < len(subcategories):
        nav.append(telebot.types.InlineKeyboardButton("➡️", callback_data=f"lotfpsubpage_{lot_id}_{category_id}_{page+1}"))
    if nav:
        markup.row(*nav)
    markup.row(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data=f"lotfpbrowse_{lot_id}"))
    return markup


def _kb_funpay_en_mode(lot_id):
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton("🤖 Авто-перевод", callback_data=f"lotfpen_auto_{lot_id}"),
        telebot.types.InlineKeyboardButton("✍️ Вручную", callback_data=f"lotfpen_manual_{lot_id}"),
    )
    markup.add(telebot.types.InlineKeyboardButton("📋 Как в RU", callback_data=f"lotfpen_copy_{lot_id}"))
    markup.row(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data=f"lot_edit_{lot_id}"))
    return markup


def _kb_funpay_offer_field_options(lot_id, field_id, options, back_callback, page=0, per_page=10):
    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    start = page * per_page
    end = start + per_page
    for idx, option in enumerate(options[start:end], start=start):
        label = str(option.get("label") or option.get("value") or "").strip()[:48]
        markup.add(
            telebot.types.InlineKeyboardButton(
                label or f"Вариант {idx + 1}",
                callback_data=f"lotfpchoice_{idx}",
            )
        )
    nav = []
    if page > 0:
        nav.append(telebot.types.InlineKeyboardButton("⬅️", callback_data=f"lotfpchoicepage_{page-1}"))
    if end < len(options):
        nav.append(telebot.types.InlineKeyboardButton("➡️", callback_data=f"lotfpchoicepage_{page+1}"))
    if nav:
        markup.row(*nav)
    markup.row(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data=back_callback))
    return markup


def _kb_lot_create_mode():
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton("Обычный", callback_data="lotcreate_mode_normal"),
        telebot.types.InlineKeyboardButton("Голоса", callback_data="lotcreate_mode_vote"),
    )
    markup.add(
        telebot.types.InlineKeyboardButton("Реакции", callback_data="lotcreate_mode_reaction"),
        telebot.types.InlineKeyboardButton("Комментарии", callback_data="lotcreate_mode_comments"),
    )
    markup.row(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data="lots"))
    return markup


def _lot_review_bonus_service_id(lot):
    try:
        return int((lot or {}).get("review_bonus_service_id") or 0)
    except (TypeError, ValueError):
        return 0


def _lot_review_bonus_quantity(lot):
    try:
        return max(0, int((lot or {}).get("review_bonus_quantity") or 0))
    except (TypeError, ValueError):
        return 0


def _lot_review_bonus_enabled(lot):
    if not lot:
        return False
    try:
        enabled = int(lot.get("review_bonus_enabled") or 0) == 1
    except (TypeError, ValueError):
        enabled = False
    return enabled and _lot_review_bonus_service_id(lot) > 0 and _lot_review_bonus_quantity(lot) > 0


def _lot_review_bonus_service_label(lot):
    service_id = _lot_review_bonus_service_id(lot)
    service_name = str((lot or {}).get("review_bonus_service_name") or "").strip()
    if service_id <= 0:
        return "не выбран"
    if service_name:
        return f"#{service_id} {service_name}"
    return f"#{service_id}"


def _lot_review_bonus_payload(lot):
    if not lot:
        return None
    service_id = _lot_review_bonus_service_id(lot)
    quantity = _lot_review_bonus_quantity(lot)
    if service_id <= 0 or quantity <= 0:
        return None
    service = db.get_service("twiboost", service_id) if db else None
    service_type = str((lot.get("review_bonus_service_type") or (service or {}).get("type") or "")).strip().lower()
    payload = dict(lot)
    payload.update({
        "api_service_id": service_id,
        "api_service_name": lot.get("review_bonus_service_name") or (service or {}).get("name") or "Бонус за отзыв",
        "service_type": service_type,
        "api_rate": float((service or {}).get("rate") or 0),
        "min_quantity": int((service or {}).get("min_order") or quantity or 1),
        "max_quantity": int((service or {}).get("max_order") or max(quantity, 1)),
        "category": (service or {}).get("category") or lot.get("category") or "",
        "platform": (service or {}).get("platform") or lot.get("platform") or "",
        "order_mode": "vote" if service_type == "vote" else "normal",
        "vote_answer_number": "",
    })
    return payload


def _lot_review_bonus_card_text(lot):
    status = "✅ Включён" if _lot_review_bonus_enabled(lot) else "⏸ Выключен"
    quantity = _lot_review_bonus_quantity(lot)
    quantity_text = str(quantity) if quantity > 0 else "не задано"
    return (
        f"🎁 Бонус за 5★: {status}\n"
        f"🔗 Бонусный сервис: {_lot_review_bonus_service_label(lot)}\n"
        f"📊 Бонусное количество: {quantity_text}"
    )


def _get_twiboost_service_by_id(service_id):
    try:
        service_id = int(service_id)
    except (TypeError, ValueError):
        return None
    def _persist_service(payload):
        if not db or not payload:
            return payload
        platform = payload.get("platform") or (api.detect_platform(payload.get("category") or "") if api else "other")
        db.upsert_service(
            "twiboost",
            int(payload.get("service_id") or service_id),
            name=payload.get("name") or "",
            type=payload.get("type") or "",
            category=payload.get("category") or "",
            rate=payload.get("rate") or 0,
            min_order=payload.get("min_order") or payload.get("min") or 0,
            max_order=payload.get("max_order") or payload.get("max") or 0,
            refill=int(bool(payload.get("refill"))),
            cancel=int(bool(payload.get("cancel"))),
            platform=platform,
        )
        return db.get_service("twiboost", service_id)

    service = db.get_service("twiboost", service_id) if db else None
    if service:
        return service
    if api:
        try:
            direct = api.get_service_by_id(service_id)
        except Exception:
            direct = None
        if direct:
            persisted = _persist_service(direct)
            if persisted:
                return persisted

        try:
            result = api.get_services()
        except Exception:
            result = {"success": False}
        if result.get("success"):
            for svc in result.get("services", []):
                try:
                    current_id = int(svc.get("service_id") or 0)
                except (TypeError, ValueError):
                    current_id = 0
                if current_id != service_id:
                    continue
                persisted = _persist_service(svc)
                if persisted:
                    return persisted

    # TwiBoost API sometimes returns only part of the catalog.
    # Keep the entered service ID usable instead of rejecting it outright.
    fallback = {
        "service_id": service_id,
        "name": f"Сервис #{service_id}",
        "type": "",
        "category": "",
        "rate": 0,
        "min_order": 1,
        "max_order": 1000000,
        "refill": 0,
        "cancel": 0,
        "platform": "other",
    }
    persisted = _persist_service(fallback)
    if persisted:
        return persisted
    return fallback

def _get_smmway_service_by_id(service_id):
    try:
        service_id = int(service_id)
    except (TypeError, ValueError):
        return None

    def _persist_smmway_service(payload):
        if not db or not payload:
            return payload
        api_client = get_api_client("smmway")
        platform = payload.get("platform") or (api_client.detect_platform(payload.get("category") or "") if api_client else "other")
        db.upsert_service(
            "smmway", int(payload.get("service_id") or service_id),
            name=payload.get("name") or "", type=payload.get("type") or "", category=payload.get("category") or "",
            rate=payload.get("rate") or 0, min_order=payload.get("min_order") or payload.get("min") or 0,
            max_order=payload.get("max_order") or payload.get("max") or 0,
            refill=int(bool(payload.get("refill"))), cancel=int(bool(payload.get("cancel"))), platform=platform,
        )
        return db.get_service("smmway", service_id)

    service = db.get_service("smmway", service_id) if db else None
    if service:
        return service

    api_client = get_api_client("smmway")
    if api_client:
        try:
            result = api_client.get_services()
        except Exception:
            result = {"success": False}
        if result.get("success"):
            for svc in result.get("services", []):
                try:
                    current_id = int(svc.get("service_id") or 0)
                except (TypeError, ValueError):
                    current_id = 0
                if current_id != service_id:
                    continue
                persisted = _persist_smmway_service(svc)
                if persisted:
                    return persisted

    fallback = {
        "service_id": service_id, "name": f"SmmWay #{service_id}", "type": "", "category": "",
        "rate": 0, "min_order": 1, "max_order": 1000000, "refill": 0, "cancel": 0, "platform": "other"
    }
    return _persist_smmway_service(fallback)

def _kb_lot_review_bonus(lot_id, enabled=False):
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton("🔗 Сервис бонуса", callback_data=f"lotrb_service_{lot_id}"),
        telebot.types.InlineKeyboardButton("📊 Кол-во бонуса", callback_data=f"lotrb_qty_{lot_id}"),
    )
    toggle_label = "⏸ Выключить" if enabled else "✅ Включить"
    markup.add(
        telebot.types.InlineKeyboardButton(toggle_label, callback_data=f"lotrb_toggle_{lot_id}"),
        telebot.types.InlineKeyboardButton("🗑 Сбросить", callback_data=f"lotrb_clear_{lot_id}"),
    )
    markup.row(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data=f"lot_edit_{lot_id}"))
    return markup


kb.lot_mode_selector = _kb_lot_mode_selector
kb.lot_edit = _kb_lot_edit
kb.lots_menu = _kb_lots_menu
kb.lots_fpcreate_picker = _kb_lots_fpcreate_picker

FUNPAY_OFFER_STANDARD_FIELDS = {"summary", "desc", "payment_msg", "images"}
FUNPAY_TELEGRAM_CATEGORY_ID = 224
FUNPAY_CATEGORY_ALIASES = {
    "telegram": ["telegram", "телеграм"],
    "youtube": ["youtube", "ютуб"],
    "tiktok": ["tiktok", "tik tok", "тикток", "тикток"],
    "instagram": ["instagram", "инстаграм"],
    "vk": ["vk", "вк", "vkontakte", "вконтакте"],
    "discord": ["discord", "дискорд"],
    "facebook": ["facebook", "фейсбук"],
    "twitter": ["twitter", "x", "твиттер"],
    "twitch": ["twitch", "твич"],
    "spotify": ["spotify", "спотифай"],
    "steam": ["steam", "стим"],
    "facebook": ["facebook", "фейсбук"],
}


def _funpay_offer_field_condition_ok(field, values):
    conditions = field.get("conditions") or []
    for cond in conditions:
        cond_id = str(cond.get("id") or "").strip()
        if not cond_id:
            continue
        allowed = [str(v).strip().lower() for v in (cond.get("list") or [])]
        current = str(values.get(cond_id, "")).strip().lower()
        if allowed and current not in allowed:
            return False
    return True


def _funpay_offer_dynamic_fields(schema, values):
    result = []
    for field in schema or []:
        field_id = str(field.get("id") or "").strip()
        if not field_id or field_id in FUNPAY_OFFER_STANDARD_FIELDS:
            continue
        if not _funpay_offer_field_condition_ok(field, values):
            continue
        result.append(field)
    return result


def _funpay_offer_next_field(schema, values):
    for field in _funpay_offer_dynamic_fields(schema, values):
        field_id = str(field.get("id") or "").strip()
        if not str(values.get(field_id, "")).strip():
            return field
    return None


def _funpay_offer_field_prompt(field):
    label = str(field.get("label") or field.get("id") or "поле").strip()
    options = field.get("options") or []
    if options:
        lines = [f"🧩 <b>{html.escape(label)}</b>", "", "Введите одно из значений:"]
        for option in options:
            lines.append(f"• {html.escape(str(option.get('label') or option.get('value') or ''))}")
        return "\n".join(lines)
    return f"🧩 <b>{html.escape(label)}</b>\n\nВведите значение:"


def _advance_funpay_offer_field_flow(chat_id, user_id, lot_id, node_id, schema, field_values, defaults, back_callback, *, msg_id=None):
    next_field = _funpay_offer_next_field(schema, field_values)
    if next_field:
        set_state(
            user_id,
            "lotfp_field",
            lot_id=lot_id,
            node_id=node_id,
            form_schema=schema,
            field_values=field_values,
            defaults=defaults,
            current_field_id=next_field.get("id"),
            back_callback=back_callback,
        )
        options = next_field.get("options") or []
        if options:
            text = (
                f"🧩 <b>{html.escape(str(next_field.get('label') or next_field.get('id') or 'поле'))}</b>\n\n"
                "Выберите один из вариантов:"
            )
            markup = _kb_funpay_offer_field_options(lot_id, str(next_field.get("id") or ""), options, back_callback)
            if msg_id:
                _edit(chat_id, msg_id, text, markup)
            else:
                bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)
        else:
            if msg_id:
                _edit(chat_id, msg_id, _funpay_offer_field_prompt(next_field), kb.back(back_callback))
            else:
                bot.send_message(chat_id, _funpay_offer_field_prompt(next_field), parse_mode="HTML", reply_markup=kb.back(back_callback))
        return
    _start_funpay_offer_text_flow(
        chat_id,
        user_id,
        lot_id,
        node_id,
        schema,
        field_values,
        defaults,
        back_callback=back_callback,
    )


def _funpay_offer_defaults_for_lot(lot):
    lot_name = str((lot or {}).get("name") or "").strip()
    service_name = str((lot or {}).get("api_service_name") or "").strip()
    qty = int((lot or {}).get("quantity_per_order") or 1)
    summary = lot_name or service_name or "SMM услуга"
    desc_ru = summary
    if service_name and service_name != summary:
        desc_ru = f"{summary}\n\nУслуга: {service_name}"
    return {
        "summary_ru": summary,
        "summary_en": summary,
        "desc_ru": desc_ru,
        "desc_en": summary,
        "payment_msg_ru": "",
        "payment_msg_en": "",
        "price": round(float((lot or {}).get("price") or 0) * max(qty, 1), 2),
        "amount": 100,
    }


def _is_telegram_funpay_lot(lot):
    if not lot:
        return False
    if str((lot or {}).get("platform") or "").strip().lower() == "telegram":
        return True
    if _lot_order_mode(lot) in {"vote", "reaction", "comments"}:
        return True
    haystack = " ".join([
        str((lot or {}).get("name") or ""),
        str((lot or {}).get("api_service_name") or ""),
        str((lot or {}).get("category") or ""),
    ]).lower()
    return "telegram" in haystack


def _normalize_funpay_category_text(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _lot_funpay_category_keywords(lot):
    keywords = set()
    platform = str((lot or {}).get("platform") or "").strip().lower()
    if platform:
        keywords.update(FUNPAY_CATEGORY_ALIASES.get(platform, []))
        keywords.add(platform)
    source_text = " ".join([
        str((lot or {}).get("name") or ""),
        str((lot or {}).get("api_service_name") or ""),
        str((lot or {}).get("category") or ""),
    ]).lower()
    if "telegram" in source_text or "телеграм" in source_text:
        keywords.update(FUNPAY_CATEGORY_ALIASES.get("telegram", []))
    if "youtube" in source_text or "ютуб" in source_text:
        keywords.update(FUNPAY_CATEGORY_ALIASES.get("youtube", []))
    if "tiktok" in source_text or "тикток" in source_text:
        keywords.update(FUNPAY_CATEGORY_ALIASES.get("tiktok", []))
    if "instagram" in source_text or "инстаграм" in source_text:
        keywords.update(FUNPAY_CATEGORY_ALIASES.get("instagram", []))
    if "discord" in source_text or "дискорд" in source_text:
        keywords.update(FUNPAY_CATEGORY_ALIASES.get("discord", []))
    if "steam" in source_text or "стим" in source_text:
        keywords.update(FUNPAY_CATEGORY_ALIASES.get("steam", []))
    return [kw for kw in keywords if len(kw.strip()) >= 2]


def _lot_funpay_related_categories(lot):
    categories = _get_funpay_categories_catalog()
    keywords = _lot_funpay_category_keywords(lot)
    if not categories or not keywords:
        return []
    result = []
    seen = set()
    for category in categories:
        cat_id = int(category.get("id") or 0)
        if cat_id <= 0 or cat_id in seen:
            continue
        name = _normalize_funpay_category_text(category.get("name") or "")
        if any(keyword in name for keyword in keywords):
            seen.add(cat_id)
            result.append(category)
    return result


def _translate_ru_to_en(text):
    text = str(text or "").strip()
    if not text:
        return ""
    try:
        resp = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": "ru",
                "tl": "en",
                "dt": "t",
                "q": text,
            },
            timeout=20,
        )
        data = resp.json()
        if isinstance(data, list) and data and isinstance(data[0], list):
            return "".join(str(item[0] or "") for item in data[0] if isinstance(item, list)).strip() or text
    except Exception:
        pass
    return text


def _get_funpay_offer_presets(lot):
    service_id = int((lot or {}).get("api_service_id") or 0)
    seen = set()
    presets = []
    for preset in (cfg.funpay_category_presets or {}).get(str(service_id), []):
        node_id = int(preset.get("subcategory_id") or preset.get("node_id") or 0)
        if node_id <= 0 or node_id in seen:
            continue
        seen.add(node_id)
        presets.append({
            "subcategory_id": node_id,
            "category_name": preset.get("category_name") or "",
            "subcategory_name": preset.get("subcategory_name") or "",
        })
    lots, _ = _get_funpay_lots_preview(limit=200)
    for item in lots:
        node_id = int(item.get("subcategory_id") or 0)
        if node_id <= 0 or node_id in seen:
            continue
        seen.add(node_id)
        presets.append({
            "subcategory_id": node_id,
            "category_name": item.get("category") or "",
            "subcategory_name": item.get("category") or "",
        })
    return presets


def _get_funpay_categories_catalog():
    if not fp_client_ready():
        return []
    try:
        categories = fp.get_categories() or []
    except Exception:
        return []
    result = []
    for category in categories:
        subcategories = []
        for subcategory in category.get("subcategories") or []:
            sub_id = int(subcategory.get("id") or 0)
            if sub_id <= 0:
                continue
            subcategories.append({
                "id": sub_id,
                "name": str(subcategory.get("name") or "").strip(),
                "type": str(subcategory.get("type") or "").strip(),
                "category_name": str(category.get("name") or "").strip(),
            })
        if subcategories:
            result.append({
                "id": int(category.get("id") or 0),
                "name": str(category.get("name") or "").strip(),
                "subcategories": subcategories,
            })
    return result


def _start_funpay_offer_text_flow(chat_id, user_id, lot_id, node_id, schema, field_values, defaults, *, back_callback=None):
    set_state(
        user_id,
        "lotfp_summary_ru",
        lot_id=lot_id,
        node_id=node_id,
        form_schema=schema,
        field_values=field_values,
        defaults=defaults,
        back_callback=back_callback or f"lot_{lot_id}",
    )
    bot.send_message(
        chat_id,
        "📝 <b>Краткое описание RU</b>\n\n"
        f"Текущее значение:\n<code>{html.escape(str(defaults.get('summary_ru') or ''))}</code>\n\n"
        "Отправьте новый текст или <b>-</b>, чтобы оставить автоматически.",
        parse_mode="HTML",
        reply_markup=kb.back(back_callback or f"lot_{lot_id}"),
    )


def _begin_funpay_offer_node(chat_id, user_id, lot_id, node_id, *, msg_id=None, back_callback=None):
    lot = db.get_lot(lot_id)
    if not lot:
        if msg_id:
            _edit(chat_id, msg_id, "❌ Лот не найден.", kb.lots_menu())
        else:
            bot.send_message(chat_id, "❌ Лот не найден.", reply_markup=kb.lots_menu())
        return
    if not fp:
        clear_state(user_id)
        text = "❌ FunPay не подключен."
        if msg_id:
            _edit(chat_id, msg_id, text, kb.back(back_callback or f"lot_{lot_id}"))
        else:
            bot.send_message(chat_id, text, reply_markup=kb.back(back_callback or f"lot_{lot_id}"))
        return
    form = fp.get_offer_edit_form(node_id)
    if not form.get("success"):
        text = f"❌ Не удалось открыть форму FunPay:\n{html.escape(form.get('error', 'unknown'))}"
        if msg_id:
            _edit(chat_id, msg_id, text, kb.back(back_callback or f"lot_{lot_id}"))
        else:
            bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb.back(back_callback or f"lot_{lot_id}"))
        return
    defaults = _funpay_offer_defaults_for_lot(lot)
    field_values = {}
    _advance_funpay_offer_field_flow(
        chat_id,
        user_id,
        lot_id,
        node_id,
        form.get("field_schema", []),
        field_values,
        defaults,
        back_callback or f"lot_{lot_id}",
        msg_id=msg_id,
    )


def _start_funpay_offer_create(chat_id, msg_id, user_id, lot_id, *, back_callback=None):
    lot = db.get_lot(lot_id)
    if not lot:
        if msg_id:
            _edit(chat_id, msg_id, "❌ Лот не найден.", kb.lots_menu())
        else:
            bot.send_message(chat_id, "❌ Лот не найден.", reply_markup=kb.lots_menu())
        return
    presets = _get_funpay_offer_presets(lot)
    related_categories = _lot_funpay_related_categories(lot)
    text = (
        "🚀 <b>Создание лота на FunPay</b>\n\n"
        "Выберите способ:\n"
        "• через каталог категорий FunPay\n"
        "• через быстрые пресеты\n"
        "• через ручной node ID"
    )
    if related_categories:
        text += (
            "\n\n"
            "Для этого лота доступен быстрый вход в подходящие категории FunPay."
        )
    markup = _kb_funpay_offer_entry(
        lot_id,
        bool(presets),
        back_callback or "lots_fpcreate_pick",
        related_categories=bool(related_categories),
    )
    if msg_id:
        _edit(chat_id, msg_id, text, markup)
    else:
        bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)


def _create_lot_draft_from_payload(payload):
    order_mode = str(payload.get("order_mode") or "normal").strip().lower()
    provider = str(payload.get("api_provider") or "twiboost").strip().lower()
    service = payload.get("service") or {}
    if order_mode == "reaction":
        service_id = 0
        service_name = "Авто-подбор реакции"
        service_type = ""
        service_rate = 0
        service_category = ""
        platform = "telegram"
        min_q = max(1, int(payload.get("quantity_per_order") or 1))
        max_q = max(min_q, 100000)
    else:
        service_id = int(payload.get("service_id") or 0)
        service_name = service.get("name") or ""
        service_type = service.get("type") or ""
        service_rate = float(service.get("rate") or 0)
        service_category = service.get("category") or ""
        platform = api.detect_platform(service_category) if api and service_category else ""
        min_q = int(service.get("min_order") or payload.get("quantity_per_order") or 1)
        max_q = int(service.get("max_order") or max(min_q, payload.get("quantity_per_order") or 1))
    return db.add_lot(
        name=payload["name"],
        api_service_id=service_id,
        api_service_name=service_name,
        service_type=service_type,
        order_mode=order_mode,
        vote_answer_number="",
        api_rate=service_rate,
        category=service_category,
        platform=platform,
        min_quantity=min_q,
        max_quantity=max_q,
        funpay_lot_id="",
        funpay_lot_name="",
        quantity_per_order=int(payload.get("quantity_per_order") or 1),
        price_mode="fixed",
        price_input=0,
        price_per_unit=0,
        price=0,
        markup=30,
        is_active=0,
        api_provider=provider,
    )


def _show_funpay_categories(chat_id, msg_id, lot_id, page=0):
    categories = _get_funpay_categories_catalog()
    if not categories:
        lot = db.get_lot(lot_id) if db else None
        _edit(
            chat_id,
            msg_id,
            "⚠️ Не удалось загрузить каталог категорий FunPay.\n\nМожно использовать быстрые пресеты или ввести node ID вручную.",
            _kb_funpay_offer_entry(
                lot_id,
                bool(_get_funpay_offer_presets(lot)),
                "lots_fpcreate_pick",
                related_categories=bool(_lot_funpay_related_categories(lot)),
            ),
        )
        return
    _edit(
        chat_id,
        msg_id,
        "📂 <b>Категории FunPay</b>\n\nВыберите категорию:",
        _kb_funpay_categories(lot_id, categories, page=page),
    )


def _show_funpay_related_categories(chat_id, msg_id, lot_id, page=0):
    lot = db.get_lot(lot_id) if db else None
    categories = _lot_funpay_related_categories(lot)
    if not categories:
        _show_funpay_categories(chat_id, msg_id, lot_id, page=0)
        return
    if len(categories) == 1:
        _show_funpay_subcategories(chat_id, msg_id, lot_id, int(categories[0].get("id") or 0), page=0)
        return
    _edit(
        chat_id,
        msg_id,
        "🎯 <b>Подходящие категории FunPay</b>\n\nВыберите категорию:",
        _kb_funpay_categories(lot_id, categories, page=page),
    )


def _find_funpay_category_by_id(value):
    try:
        target_id = int(value or 0)
    except (TypeError, ValueError):
        return None
    if target_id <= 0:
        return None
    for category in _get_funpay_categories_catalog():
        if int(category.get("id") or 0) == target_id:
            return category
    return None


def _show_funpay_subcategories(chat_id, msg_id, lot_id, category_id, page=0):
    categories = _get_funpay_categories_catalog()
    category = next((item for item in categories if int(item.get("id") or 0) == int(category_id)), None)
    if not category:
        _show_funpay_categories(chat_id, msg_id, lot_id)
        return
    _edit(
        chat_id,
        msg_id,
        f"📄 <b>{html.escape(str(category.get('name') or 'Категория'))}</b>\n\nВыберите подкатегорию:",
        _kb_funpay_subcategories(lot_id, category_id, category.get("subcategories") or [], page=page),
    )


def _kb_main_menu():
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton("🎮 FunPay", callback_data="funpay"),
        telebot.types.InlineKeyboardButton("💰 Баланс", callback_data="balance"),
    )
    markup.add(
        telebot.types.InlineKeyboardButton("🛒 Лоты", callback_data="lots"),
        telebot.types.InlineKeyboardButton("📦 Заказы", callback_data="orders"),
    )
    markup.add(telebot.types.InlineKeyboardButton("🌐 Сервисы API", callback_data="services"))
    markup.add(
        telebot.types.InlineKeyboardButton("🎫 Промокоды", callback_data="promos"),
        telebot.types.InlineKeyboardButton("🎁 Допы", callback_data="upsells"),
    )
    markup.add(
        telebot.types.InlineKeyboardButton("📊 Статистика", callback_data="stats"),
        telebot.types.InlineKeyboardButton("💬 Шаблоны", callback_data="templates"),
    )
    if cfg and str(cfg.get("app.role", "owner")).lower() == "mirror":
        markup.add(
            telebot.types.InlineKeyboardButton("📄 Мой долг", callback_data="mirror_due"),
            telebot.types.InlineKeyboardButton("⚙️ Настройки", callback_data="settings"),
        )
    else:
        markup.add(
            telebot.types.InlineKeyboardButton("🪞 Зеркала", callback_data="mirrors"),
            telebot.types.InlineKeyboardButton("⚙️ Настройки", callback_data="settings"),
        )
    markup.add(telebot.types.InlineKeyboardButton("📋 Логи", callback_data="logs"))
    return markup


def _is_mirror_role():
    return bool(cfg and str(cfg.get("app.role", "owner")).lower() == "mirror")


def _forced_markup_percent():
    if not cfg:
        return 0.0
    try:
        return float(cfg.get("app.forced_markup_percent", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


kb.main_menu = _kb_main_menu
kb.settings_menu = _kb_settings_menu


MIRROR_TEXT_STATES = {
    "mirror_create_name",
    "mirror_create_bot_token",
    "mirror_create_funpay_key",
    "mirror_create_twiboost_key",
    "mirror_report_revenue",
    "mirror_edit_bot_token",
    "mirror_edit_funpay_key",
    "mirror_edit_twiboost_key",
    "mirror_admin_share",
    "mirror_admin_create_name",
    "mirror_admin_create_user_id",
    "mirror_admin_create_bot_token",
    "mirror_admin_create_percent",
}


def _mirror_enabled():
    return bool(cfg.get("mirrors.enabled", True)) if cfg else False


def _mask_secret(value, left=4, right=4):
    raw = str(value or "").strip()
    if not raw:
        return "не указано"
    if len(raw) <= left + right:
        return raw
    return f"{raw[:left]}...{raw[-right:]}"


def _mirror_month_key():
    return datetime.now().strftime("%Y-%m")


def _mirror_due_amount(revenue, share_percent):
    try:
        return round(float(revenue or 0) * float(share_percent or 0) / 100.0, 2)
    except (TypeError, ValueError):
        return 0.0


def _mirror_share_percent(mirror=None):
    try:
        if _is_mirror_role():
            return float(cfg.get("owner_meta.share_percent", cfg.get("mirrors.default_share_percent", 5.0)) or 5.0)
        if mirror:
            return float(mirror.get("share_percent", cfg.get("mirrors.default_share_percent", 5.0)) or 5.0)
    except (TypeError, ValueError):
        pass
    try:
        return float(cfg.get("mirrors.default_share_percent", 5.0) or 5.0)
    except (TypeError, ValueError):
        return 5.0


def _mirror_runtime_financials():
    month = _mirror_month_key()
    # Считаем напрямую из заказов, чтобы избежать 0₽
    total_revenue = 0.0
    total_cost = 0.0
    order_count = 0
    try:
        orders = db.get_orders(limit=2000) if db else []
        for o in orders:
            if not o.get("completed_at") or not o["completed_at"].startswith(month):
                continue
            if str(o.get("status", "")).lower() in ("cancelled", "failed", "refunded"):
                continue
            total_revenue += float(o.get("sell_price", 0) or 0)
            total_cost += float(o.get("cost_price", 0) or 0)
            order_count += 1
    except Exception:
        pass

    net_profit = round(max(0.0, total_revenue - total_cost), 2)
    share_percent = _mirror_share_percent()
    due = _mirror_due_amount(net_profit, share_percent)
    return {
        "report_month": month,
        "total_orders": order_count,
        "total_revenue": round(total_revenue, 2),
        "total_cost": round(total_cost, 2),
        "net_profit": net_profit,
        "share_percent": share_percent,
        "amount_due": due,
    }


def _mirror_runtime_main_text():
    s = _mirror_runtime_financials()
    return (
        "╔══════════════════════════╗\n"
        "║  🤖 <b>SMM Auto Bot</b>  ║\n"
        "╚══════════════════════════╝\n\n"
        "🪞 <b>Зеркало</b>\n"
        f"📅 Текущий месяц: <b>{html.escape(str(s['report_month']))}</b>\n"
        f"📦 Заказов: <b>{s['total_orders']}</b>\n"
        f"📈 Чистая прибыль: <b>{s['net_profit']:.2f}₽</b>\n"
        f"💸 Доля владельца: <b>{s['share_percent']:.2f}%</b>\n"
        f"✅ К переводу: <b>{s['amount_due']:.2f}₽</b>\n\n"
        "Выберите раздел:"
    )


def _mirror_status_label(status):
    status = str(status or "pending").lower()
    if status == "active":
        return "✅ Активно"
    if status == "blocked":
        return "⛔ Заблокировано"
    return "⏳ Ожидает проверки"


def _mirror_name(mirror):
    if not mirror:
        return "Зеркало"
    return (
        mirror.get("mirror_name")
        or mirror.get("full_name")
        or (f"@{mirror['username']}" if mirror.get("username") else "")
        or f"ID {mirror.get('telegram_user_id')}"
    )


def _mirror_current_report(mirror):
    if not db or not mirror:
        return None
    return db.get_mirror_report(mirror["id"], _mirror_month_key())


def _mirror_user_menu(mirror=None):
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    if not mirror:
        markup.add(telebot.types.InlineKeyboardButton("🪞 Создать зеркало", callback_data="mirror_create"))
        return markup
    if str(mirror.get("status")) == "blocked":
        markup.add(telebot.types.InlineKeyboardButton("🪞 Данные зеркала", callback_data="mirror_main"))
        return markup
    markup.add(
        telebot.types.InlineKeyboardButton("🪞 Настройки зеркала", callback_data="mirror_settings"),
        telebot.types.InlineKeyboardButton("💸 Отчет за месяц", callback_data="mirror_report"),
    )
    markup.add(
        telebot.types.InlineKeyboardButton("📄 Мой долг", callback_data="mirror_due"),
        telebot.types.InlineKeyboardButton("🔄 Обновить", callback_data="mirror_main"),
    )
    return markup


def _mirror_settings_menu():
    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    markup.add(telebot.types.InlineKeyboardButton("🤖 Изменить токен бота", callback_data="mirror_edit_bot_token"))
    markup.add(telebot.types.InlineKeyboardButton("🎮 Изменить Golden Key", callback_data="mirror_edit_funpay_key"))
    markup.add(telebot.types.InlineKeyboardButton("🌐 Изменить TwiBoost API", callback_data="mirror_edit_twiboost_key"))
    markup.add(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data="mirror_main"))
    return markup


def _mirror_admin_list_markup(mirrors, page=0, per_page=8):
    markup = telebot.types.InlineKeyboardMarkup(row_width=1)
    start = page * per_page
    end = start + per_page
    for mirror in mirrors[start:end]:
        status_icon = "⛔" if str(mirror.get("status")) == "blocked" else "✅" if str(mirror.get("status")) == "active" else "⏳"
        report = db.get_latest_mirror_report(mirror["id"]) if db else None
        due_text = f"{float(report.get('amount_due', 0)):.2f}₽" if report else "0.00₽"
        label = f"{status_icon} {_mirror_name(mirror)[:26]} | {due_text}"
        markup.add(telebot.types.InlineKeyboardButton(label, callback_data=f"mirroradm_{mirror['id']}"))
    nav = []
    markup.add(
        telebot.types.InlineKeyboardButton("🧪 Моё зеркало", callback_data="mirror_self"),
        telebot.types.InlineKeyboardButton("➕ Создать зеркало", callback_data="mirroradm_create"),
    )
    if page > 0:
        nav.append(telebot.types.InlineKeyboardButton("⬅️", callback_data=f"mirroradm_page_{page-1}"))
    if end < len(mirrors):
        nav.append(telebot.types.InlineKeyboardButton("➡️", callback_data=f"mirroradm_page_{page+1}"))
    if nav:
        markup.row(*nav)
    markup.add(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data="main"))
    return markup

def _mirror_admin_item_markup(mirror):
    mirror_id = mirror["id"]
    is_blocked = str(mirror.get("status")) == "blocked"
    markup = telebot.types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        telebot.types.InlineKeyboardButton("📄 Отчеты", callback_data=f"mirroradm_reports_{mirror_id}"),
        telebot.types.InlineKeyboardButton("💸 % владельца", callback_data=f"mirroradm_share_{mirror_id}"),
    )
    markup.add(
        telebot.types.InlineKeyboardButton("✅ Разблокировать" if is_blocked else "⛔ Заблокировать", callback_data=f"mirroradm_toggle_{mirror_id}"),
    )
    markup.add(
        telebot.types.InlineKeyboardButton("🗑 Удалить зеркало", callback_data=f"mirroradm_delete_{mirror_id}"),
        telebot.types.InlineKeyboardButton("🔄 Сбросить долг", callback_data=f"mirroradm_reset_due_{mirror_id}"),
    )
    markup.add(telebot.types.InlineKeyboardButton("◀️ К зеркалам", callback_data="mirrors"))
    return markup

def _render_mirror_dashboard_text(mirror):
    if not _mirror_enabled():
        return "⛔ Режим зеркал отключен владельцем."

    if not mirror:
        return (
            "🪞 <b>Зеркало бота</b>\n\n"
            "Основное управление доступно только владельцу.\n"
            "Вы можете оставить данные для зеркала и ежемесячно сдавать отчет.\n\n"
            "Условия:\n"
            "• владелец получает процент от вашей месячной выручки\n"
            "• сейчас по умолчанию это 5%\n"
            "• владелец может смотреть ваши сохраненные настройки и блокировать доступ"
        )

    report = _mirror_current_report(mirror) or db.get_latest_mirror_report(mirror["id"])
    settings = _mirror_settings_data(mirror)
    report_month = report.get("report_month") if report else _mirror_month_key()
    revenue = float(report.get("revenue", 0)) if report else 0.0
    due = float(report.get("amount_due", 0)) if report else 0.0
    bot_username = str(settings.get("bot_username") or "").strip()

    lines = [
        "🪞 <b>Ваше зеркало</b>",
        "",
        f"👤 {_mirror_name(mirror)}",
        f"📌 Статус: {_mirror_status_label(mirror.get('status'))}",
        f"💸 Доля владельца: <b>{float(mirror.get('share_percent', 5) or 5):.2f}%</b>",
        "",
        "Сохраненные данные:",
        f"🤖 Токен бота: <code>{html.escape(_mask_secret(mirror.get('bot_token')))}</code>",
        f"🎮 Golden Key: <code>{html.escape(_mask_secret(mirror.get('funpay_golden_key')))}</code>",
        f"🌐 TwiBoost API: <code>{html.escape(_mask_secret(mirror.get('twiboost_api_key')))}</code>",
        "",
        f"📅 Отчетный месяц: <b>{html.escape(str(report_month))}</b>",
        f"💰 Выручка: <b>{revenue:.2f}₽</b>",
        f"📄 К переводу владельцу: <b>{due:.2f}₽</b>",
    ]
    if bot_username:
        lines.extend(["", f"🤖 Ваш бот: @{html.escape(bot_username)}", f"🔗 https://t.me/{html.escape(bot_username)}"])
    if str(mirror.get("status")) == "blocked":
        lines.extend(["", "⛔ Доступ к зеркалу временно заблокирован владельцем."])
    return "\n".join(lines)


def _render_mirror_admin_text(mirror):
    report = db.get_latest_mirror_report(mirror["id"]) if db else None
    revenue = float(report.get("revenue", 0)) if report else 0.0
    due = float(report.get("amount_due", 0)) if report else 0.0
    report_month = report.get("report_month") if report else "нет"
    return (
        "🪞 <b>Карточка зеркала</b>\n\n"
        f"👤 <b>{html.escape(_mirror_name(mirror))}</b>\n"
        f"🆔 Telegram ID: <code>{mirror.get('telegram_user_id')}</code>\n"
        f"📌 Статус: {_mirror_status_label(mirror.get('status'))}\n"
        f"💸 Доля владельца: <b>{float(mirror.get('share_percent', 5) or 5):.2f}%</b>\n\n"
        f"🤖 Токен бота: <code>{html.escape(_mask_secret(mirror.get('bot_token')))}</code>\n"
        f"🎮 Golden Key: <code>{html.escape(_mask_secret(mirror.get('funpay_golden_key')))}</code>\n"
        f"🌐 TwiBoost API: <code>{html.escape(_mask_secret(mirror.get('twiboost_api_key')))}</code>\n\n"
        f"📅 Последний отчет: <b>{html.escape(str(report_month))}</b>\n"
        f"💰 Выручка: <b>{revenue:.2f}₽</b>\n"
        f"📄 Должен перевести: <b>{due:.2f}₽</b>"
    )


def _show_mirror_dashboard(chat_id, tg_user, msg_id=None):
    mirror = db.get_mirror_user(tg_user.id) if db else None
    text = _render_mirror_dashboard_text(mirror)
    markup = _mirror_user_menu(mirror)
    if msg_id:
        _edit(chat_id, msg_id, text, markup)
    else:
        bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)


def _show_mirror_admin_list(chat_id, msg_id=None, page=0):
    mirrors = db.get_mirror_users() if db else []
    text = f"🪞 <b>Зеркала ({len(mirrors)})</b>"
    if not mirrors:
        text += "\n\nПока никто не зарегистрировал зеркало."
    else:
        preview = []
        for mirror in mirrors[:5]:
            preview.append(
                f"• {html.escape(_mirror_name(mirror))} — {_mirror_status_label(mirror.get('status'))}"
            )
        text += "\n\n" + "\n".join(preview)
    markup = _mirror_admin_list_markup(mirrors, page)
    if msg_id:
        _edit(chat_id, msg_id, text, markup)
    else:
        bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)


def _show_mirror_admin_item(chat_id, msg_id, mirror_id):
    mirror = db.get_mirror_user_by_id(mirror_id) if db else None
    if not mirror:
        _edit(chat_id, msg_id, "❌ Зеркало не найдено.", telebot.types.InlineKeyboardMarkup().add(telebot.types.InlineKeyboardButton("◀️ К зеркалам", callback_data="mirrors")))
        return
    _edit(chat_id, msg_id, _render_mirror_admin_text(mirror), _mirror_admin_item_markup(mirror))


def _mirror_settings_data(mirror):
    if not mirror:
        return {}
    raw = mirror.get("settings_json") or "{}"
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _fetch_bot_identity(bot_token):
    token = str(bot_token or "").strip()
    if not token:
        return {"success": False, "error": "Токен пустой"}
    if cfg and token == str(cfg.bot_token or "").strip():
        return {"success": False, "error": "Нельзя использовать токен основного бота для зеркала"}
    try:
        response = requests.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=15,
            verify=False,
        )
        data = response.json()
    except Exception as exc:
        return {"success": False, "error": str(exc)}
    if not data.get("ok"):
        description = data.get("description") or "Не удалось проверить токен"
        return {"success": False, "error": str(description)}
    result = data.get("result") or {}
    return {
        "success": True,
        "id": result.get("id"),
        "username": result.get("username") or "",
        "first_name": result.get("first_name") or "",
    }


def _write_mirror_instance_config(instance_dir, mirror, bot_token=None, funpay_golden_key=None, twiboost_api_key=None):
    base = load_base_config()
    share_percent = float(mirror.get("share_percent", cfg.get("mirrors.default_share_percent", 5.0)) or 5.0)
    config = build_mirror_config(
        base,
        mirror.get("mirror_name") or mirror.get("full_name") or f"mirror-{mirror['telegram_user_id']}",
        int(mirror.get("telegram_user_id") or 0),
        bot_token if bot_token is not None else (mirror.get("bot_token") or ""),
        0,
    )
    config["funpay_golden_key"] = funpay_golden_key if funpay_golden_key is not None else (mirror.get("funpay_golden_key") or "")
    config["twiboost_api_key"] = twiboost_api_key if twiboost_api_key is not None else (mirror.get("twiboost_api_key") or "")
    
    # 🔥 Явно прописываем SmmWay из ГЛАВНОГО конфига, чтобы ключи не терялись при рестарте зеркала
    config["smmway_api_key"] = cfg.get("smmway_api_key", mirror.get("smmway_api_key", ""))
    config["smmway_web"] = dict(cfg.get("smmway_web", mirror.get("smmway_web", {})))
    
    # 🔥 Копируем ВСЕ настройки из базового конфига, кроме защищённых
    protected_keys = {
        "owner_meta",           # Права владельца — не копируем
        "admin_ids",            # Админы — у зеркала свои
        "bot_token",            # Токен бота — уже задан выше
        "funpay_golden_key",    # Golden Key — уже задан выше
        "twiboost_api_key",     # API ключ — уже задан выше
        "smmway_api_key",       # SmmWay API ключ — уже задан выше
        "smmway_web",           # SmmWay Web конфиг — уже задан выше
        "mirrors",              # Настройки зеркал — управляются отдельно
        "app",                  # Мета-информация приложения
    }
    
    for key, value in base.items():
        if key not in protected_keys:
            if isinstance(value, dict):
                # Для вложенных словарей — глубокое копирование
                config.setdefault(key, {})
                for subkey, subvalue in value.items():
                    if subkey not in protected_keys:
                        config[key][subkey] = subvalue
            else:
                config[key] = value
    
    # 🔥 Явно гарантируем копирование support_center
    if "support_center" in base:
        config.setdefault("support_center", {})
        for sc_key in ["enabled", "php_sessid", "form_id", "login_field_id", "order_field_id", 
                      "role_field_id", "subject_field_id", "role_value", "subject_value", 
                      "funpay_login", "user_agent", "timeout"]:
            if sc_key in base["support_center"]:
                config["support_center"][sc_key] = base["support_center"][sc_key]
    
    # 🔥 Настройки зеркал — только свои
    config.setdefault("mirrors", {})
    config["mirrors"]["enabled"] = False  # Зеркало не должно создавать свои зеркала
    config["mirrors"]["default_share_percent"] = 0
    
    # 🔥 owner_meta — только доля, без прав админа
    config.setdefault("owner_meta", {})
    config["owner_meta"]["share_percent"] = share_percent
    # ❌ НЕ копируем owner_admin_ids — у зеркала нет прав владельца
    
    (instance_dir / "data").mkdir(parents=True, exist_ok=True)
    (instance_dir / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    write_launchers(instance_dir)
    write_readme(instance_dir, mirror.get("mirror_name") or f"mirror-{mirror['telegram_user_id']}", share_percent)

def _stop_mirror_instance_process(pid):
    try:
        pid = int(pid or 0)
    except Exception:
        return
    if pid <= 0:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        else:
            os.kill(pid, 15)
    except Exception:
        pass


def _start_mirror_instance_process(instance_dir):
    env = os.environ.copy()
    env["SMM_CONFIG_PATH"] = str(instance_dir / "config.json")
    env["SMM_DB_PATH"] = str(instance_dir / "data" / "smm_bot.db")
    env["SMM_LOG_PATH"] = str(instance_dir / "smm_bot.log")
    main_path = MIRRORS_DIR.parent / "main.py"
    kwargs = {
        "cwd": str(MIRRORS_DIR.parent),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen([sys.executable, str(main_path)], **kwargs)
    (instance_dir / "mirror.pid").write_text(str(proc.pid), encoding="utf-8")
    return proc.pid


def _create_or_restart_mirror_instance(mirror, bot_token=None, funpay_golden_key=None, twiboost_api_key=None, restart=True):
    settings = _mirror_settings_data(mirror)
    slug = settings.get("instance_slug") or slugify(mirror.get("mirror_name") or f"mirror-{mirror['telegram_user_id']}")
    instance_dir = MIRRORS_DIR / slug
    current_pid = settings.get("process_pid")
    if restart and current_pid:
        _stop_mirror_instance_process(current_pid)
    _write_mirror_instance_config(instance_dir, mirror, bot_token=bot_token, funpay_golden_key=funpay_golden_key, twiboost_api_key=twiboost_api_key)
    pid = _start_mirror_instance_process(instance_dir)
    token_value = bot_token if bot_token is not None else (mirror.get("bot_token") or "")
    identity = _fetch_bot_identity(token_value)
    settings.update({
        "instance_slug": slug,
        "instance_dir": str(instance_dir),
        "process_pid": pid,
        "started_at": datetime.now().isoformat(),
    })
    if identity.get("success"):
        settings["bot_username"] = identity.get("username") or ""
        settings["bot_first_name"] = identity.get("first_name") or ""
    db.update_mirror_user(mirror["id"], settings_json=json.dumps(settings, ensure_ascii=False), notes=f"hosted:{slug}")
    mirror = db.get_mirror_user_by_id(mirror["id"])
    return mirror, instance_dir, pid


def _create_mirror_instance_bundle(mirror_name, admin_id, bot_token, forced_percent):
    base = load_base_config()
    slug = slugify(mirror_name)
    instance_dir = MIRRORS_DIR / slug
    (instance_dir / "data").mkdir(parents=True, exist_ok=True)
    config = build_mirror_config(base, mirror_name, admin_id, bot_token, forced_percent)
    (instance_dir / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    write_launchers(instance_dir)
    write_readme(instance_dir, mirror_name, forced_percent)
    return instance_dir


def _delete_mirror_instance(mirror):
    if not mirror:
        return False, "Зеркало не найдено."
    settings = _mirror_settings_data(mirror)
    current_pid = settings.get("process_pid")
    if current_pid:
        _stop_mirror_instance_process(current_pid)
    instance_dir = settings.get("instance_dir")
    if not instance_dir:
        slug = settings.get("instance_slug") or slugify(mirror.get("mirror_name") or f"mirror-{mirror.get('telegram_user_id')}")
        instance_dir = str(MIRRORS_DIR / slug)
    try:
        if instance_dir and os.path.isdir(instance_dir):
            shutil.rmtree(instance_dir, ignore_errors=False)
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.exception("Не удалось удалить папку зеркала %s", instance_dir)
        return False, f"Не удалось удалить файлы зеркала: {exc}"
    try:
        db.delete_mirror_user(mirror["id"])
    except Exception as exc:
        logger.exception("Не удалось удалить зеркало #%s из базы", mirror.get("id"))
        return False, f"Не удалось удалить зеркало из базы: {exc}"
    return True, "Зеркало удалено."

def restore_mirror_instances():
    if not _mirror_enabled() or not db:
        return
    mirrors_dir = Path(MIRRORS_DIR)
    if not mirrors_dir.exists():
        return

    for mirror_dir in mirrors_dir.iterdir():
        if not mirror_dir.is_dir():
            continue
        config_path = mirror_dir / "config.json"
        if not config_path.exists():
            continue

        with open(config_path, 'r', encoding='utf-8') as f:
            try:
                cfg_data = json.load(f)
            except:
                continue

        admin_ids = cfg_data.get("app", {}).get("admin_ids", [])
        if not admin_ids or not isinstance(admin_ids, list):
            continue
        admin_id = int(admin_ids[0])

        mirror = db.get_mirror_user(admin_id)
        if not mirror:
            continue

        settings = _mirror_settings_data(mirror)
        current_pid = settings.get("process_pid")
        is_alive = False

        if current_pid:
            try:
                if os.name == 'nt':
                    proc = subprocess.run(['tasklist', '/FI', f'PID eq {current_pid}', '/NH'], 
                                          capture_output=True, text=True, check=False)
                    is_alive = str(current_pid) in proc.stdout
                else:
                    os.kill(int(current_pid), 0)
                    is_alive = True
            except:
                is_alive = False

        # 🔥 Если процесс жив - НЕ перезаписываем конфиг и не трогаем PID
        if is_alive:
            logger.info(f"✅ Зеркало {mirror_dir.name} уже запущено (PID {current_pid}), пропускаем.")
            continue
            
        logger.info(f"🔄 Восстанавливаю зеркало {mirror_dir.name} (admin={admin_id})")
        try:
            _create_or_restart_mirror_instance(
                mirror,
                bot_token=mirror.get("bot_token"),
                funpay_golden_key=mirror.get("funpay_golden_key"),
                twiboost_api_key=mirror.get("twiboost_api_key"),
                restart=True
            )
            logger.info(f"✅ Зеркало {mirror_dir.name} успешно перезапущено")
        except Exception as e:
            logger.error(f"❌ Ошибка восстановления зеркала {mirror_dir.name}: {e}")

def _notify_admin_mirror_event(text):
    for admin_id in cfg.admin_ids if cfg else []:
        try:
            bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception:
            pass


def _handle_mirror_text_input(m: Message):
    uid = m.from_user.id
    st = get_state(uid)
    state = st.get("state", "")
    data = st.get("data", {})
    if state not in MIRROR_TEXT_STATES:
        return False

    if not _mirror_enabled():
        clear_state(uid)
        bot.send_message(m.chat.id, "⛔ Режим зеркал отключен владельцем.")
        return True

    mirror = db.get_mirror_user(uid) if db else None

    if state == "mirror_create_name":
        set_state(uid, "mirror_create_bot_token", mirror_name=m.text.strip())
        bot.send_message(m.chat.id, "🤖 Отправьте токен вашего Telegram-бота для зеркала.", reply_markup=_mirror_user_menu(mirror))
        return True

    if state == "mirror_create_bot_token":
        identity = _fetch_bot_identity(m.text.strip())
        if not identity.get("success"):
            bot.send_message(m.chat.id, f"❌ {identity.get('error')}")
            return True
        merged = dict(data)
        merged["bot_token"] = m.text.strip()
        merged["bot_username"] = identity.get("username", "")
        set_state(uid, "mirror_create_funpay_key", **merged)
        bot.send_message(m.chat.id, "🎮 Отправьте ваш Golden Key FunPay.", reply_markup=_mirror_user_menu(mirror))
        return True

    if state == "mirror_create_funpay_key":
        merged = dict(data)
        merged["funpay_golden_key"] = m.text.strip()
        set_state(uid, "mirror_create_twiboost_key", **merged)
        bot.send_message(m.chat.id, "🌐 Отправьте API ключ TwiBoost.", reply_markup=_mirror_user_menu(mirror))
        return True

    if state == "mirror_create_twiboost_key":
        payload = dict(data)
        payload["twiboost_api_key"] = m.text.strip()
        share_percent = float(cfg.get("mirrors.default_share_percent", 5.0))
        db.upsert_mirror_user(
            uid,
            username=m.from_user.username or "",
            full_name=(" ".join(filter(None, [m.from_user.first_name, m.from_user.last_name]))).strip(),
            mirror_name=payload.get("mirror_name", ""),
            status="active" if not mirror else mirror.get("status", "active"),
            share_percent=mirror.get("share_percent", share_percent) if mirror else share_percent,
            bot_token=payload.get("bot_token", ""),
            funpay_golden_key=payload.get("funpay_golden_key", ""),
            twiboost_api_key=payload.get("twiboost_api_key", ""),
        )
        clear_state(uid)
        mirror = db.get_mirror_user(uid)
        settings = _mirror_settings_data(mirror)
        _notify_admin_mirror_event(
            "🪞 <b>Новая заявка на зеркало</b>\n\n"
            f"👤 {html.escape(_mirror_name(mirror))}\n"
            f"🆔 <code>{uid}</code>\n"
            f"💸 Доля владельца: <b>{float(mirror.get('share_percent', 5)):.2f}%</b>"
        )
        try:
            mirror, instance_dir, pid = _create_or_restart_mirror_instance(mirror)
            settings = _mirror_settings_data(mirror)
            bot_username = settings.get("bot_username") or payload.get("bot_username") or ""
            text = "✅ <b>Зеркало создано и запущено</b>\n\n"
            if bot_username:
                text += f"🤖 Бот зеркала: @{html.escape(bot_username)}\n🔗 https://t.me/{html.escape(bot_username)}\n\n"
            text += "Дальше вся работа идёт уже в отдельном боте зеркала."
            bot.send_message(m.chat.id, text, parse_mode="HTML")
        except Exception as exc:
            logger.exception("Mirror instance start failed")
            bot.send_message(
                m.chat.id,
                f"⚠️ Зеркало сохранено, но не запустилось.\nПричина: {html.escape(str(exc))}",
                parse_mode="HTML",
            )
        _show_mirror_dashboard(m.chat.id, m.from_user)
        return True

    if state == "mirror_report_revenue":
        try:
            revenue = float(m.text.strip().replace(",", "."))
        except ValueError:
            bot.send_message(m.chat.id, "❌ Введите сумму числом, например 1000 или 1250.50.")
            return True
        if not mirror:
            clear_state(uid)
            bot.send_message(m.chat.id, "❌ Сначала создайте зеркало.")
            return True
        share_percent = float(mirror.get("share_percent", cfg.get("mirrors.default_share_percent", 5.0)) or 5.0)
        amount_due = _mirror_due_amount(revenue, share_percent)
        month_key = _mirror_month_key()
        db.upsert_mirror_report(mirror["id"], month_key, revenue, share_percent, amount_due, status="pending")
        clear_state(uid)
        _notify_admin_mirror_event(
            "💸 <b>Новый отчет зеркала</b>\n\n"
            f"👤 {html.escape(_mirror_name(mirror))}\n"
            f"📅 {html.escape(month_key)}\n"
            f"💰 Выручка: <b>{revenue:.2f}₽</b>\n"
            f"📄 К переводу: <b>{amount_due:.2f}₽</b>"
        )
        _show_mirror_dashboard(m.chat.id, m.from_user)
        return True

    if state in {"mirror_edit_bot_token", "mirror_edit_funpay_key", "mirror_edit_twiboost_key"}:
        if not mirror:
            clear_state(uid)
            bot.send_message(m.chat.id, "❌ Сначала создайте зеркало.")
            return True
        updates = {}
        prompts = {
            "mirror_edit_bot_token": "bot_token",
            "mirror_edit_funpay_key": "funpay_golden_key",
            "mirror_edit_twiboost_key": "twiboost_api_key",
        }
        if state == "mirror_edit_bot_token":
            identity = _fetch_bot_identity(m.text.strip())
            if not identity.get("success"):
                bot.send_message(m.chat.id, f"❌ {identity.get('error')}")
                return True
        updates[prompts[state]] = m.text.strip()
        db.update_mirror_user(mirror["id"], **updates)
        clear_state(uid)
        mirror = db.get_mirror_user_by_id(mirror["id"])
        try:
            _create_or_restart_mirror_instance(mirror)
            settings = _mirror_settings_data(mirror)
            bot_username = settings.get("bot_username") or ""
            text = "✅ Зеркало обновлено и перезапущено."
            if bot_username:
                text += f"\n\n🤖 @{html.escape(bot_username)}\n🔗 https://t.me/{html.escape(bot_username)}"
            bot.send_message(m.chat.id, text, parse_mode="HTML")
        except Exception as exc:
            logger.exception("Mirror instance restart failed")
            bot.send_message(
                m.chat.id,
                f"⚠️ Настройка сохранена, но перезапуск не выполнен.\nПричина: {html.escape(str(exc))}",
                parse_mode="HTML",
            )

        _show_mirror_dashboard(m.chat.id, m.from_user)
        return True

    if state == "mirror_admin_share":
        if not is_admin(uid):
            clear_state(uid)
            return True
        mirror_id = data.get("mirror_id")
        mirror = db.get_mirror_user_by_id(mirror_id) if mirror_id else None
        if not mirror:
            clear_state(uid)
            bot.send_message(m.chat.id, "❌ Зеркало не найдено.")
            return True
        try:
            share_percent = float(m.text.strip().replace(",", "."))
        except ValueError:
            bot.send_message(m.chat.id, "❌ Введите число, например 5 или 7.5.")
            return True
        if share_percent < 0:
            bot.send_message(m.chat.id, "❌ Процент не может быть отрицательным.")
            return True
        db.update_mirror_user(mirror_id, share_percent=share_percent)
        clear_state(uid)
        bot.send_message(m.chat.id, f"✅ Доля для зеркала обновлена: {share_percent:.2f}%")
        return True

    if state == "mirror_admin_create_name":
        set_state(uid, "mirror_admin_create_user_id", mirror_name=m.text.strip())
        bot.send_message(m.chat.id, "👤 Отправьте Telegram ID пользователя, которому принадлежит зеркало.")
        return True

    if state == "mirror_admin_create_user_id":
        try:
            mirror_user_id = int(m.text.strip())
        except ValueError:
            bot.send_message(m.chat.id, "❌ Telegram ID должен быть числом.")
            return True
        merged = dict(data)
        merged["mirror_user_id"] = mirror_user_id
        set_state(uid, "mirror_admin_create_bot_token", **merged)
        bot.send_message(m.chat.id, "🤖 Отправьте токен Telegram-бота зеркала.")
        return True

    if state == "mirror_admin_create_bot_token":
        identity = _fetch_bot_identity(m.text.strip())
        if not identity.get("success"):
            bot.send_message(m.chat.id, f"❌ {identity.get('error')}")
            return True
        merged = dict(data)
        merged["bot_token"] = m.text.strip()
        merged["bot_username"] = identity.get("username", "")
        set_state(uid, "mirror_admin_create_percent", **merged)
        bot.send_message(m.chat.id, f"💸 Отправьте процент владельца. По умолчанию {float(cfg.get('mirrors.default_share_percent', 5.0)):.2f}%.")
        return True

    if state == "mirror_admin_create_percent":
        try:
            share_percent = float(m.text.strip().replace(',', '.'))
        except ValueError:
            bot.send_message(m.chat.id, "❌ Введите число, например 5.")
            return True
        payload = dict(data)
        mirror_user_id = int(payload["mirror_user_id"])
        db.upsert_mirror_user(
            mirror_user_id,
            mirror_name=payload.get("mirror_name", ""),
            status="active",
            share_percent=share_percent,
            bot_token=payload.get("bot_token", ""),
            funpay_golden_key="",
            twiboost_api_key="",
        )
        clear_state(uid)
        mirror = db.get_mirror_user(mirror_user_id)
        try:
            mirror, instance_dir, pid = _create_or_restart_mirror_instance(mirror)
            settings = _mirror_settings_data(mirror)
            bot_username = settings.get("bot_username") or payload.get("bot_username") or ""
            text = "✅ <b>Зеркало создано и запущено</b>\n\n"
            if bot_username:
                text += f"🤖 Бот зеркала: @{html.escape(bot_username)}\n🔗 https://t.me/{html.escape(bot_username)}\n\n"
            text += f"PID: <code>{pid}</code>"
            bot.send_message(m.chat.id, text, parse_mode="HTML")
        except Exception as exc:
            logger.exception("Admin mirror instance start failed")
            bot.send_message(
                m.chat.id,
                f"⚠️ Зеркало создано, но не запустилось.\nПричина: {html.escape(str(exc))}",
                parse_mode="HTML",
            )



        return True

    return False


def _handle_mirror_user_callback(c: CallbackQuery, d: str, uid: int, chat_id: int, msg_id: int):
    if not _mirror_enabled():
        _edit(chat_id, msg_id, "⛔ Режим зеркал отключен владельцем.", None)
        return True
    if d not in {
        "main",
        "mirror_main",
        "mirror_create",
        "mirror_settings",
        "mirror_report",
        "mirror_due",
        "mirror_edit_bot_token",
        "mirror_edit_funpay_key",
        "mirror_edit_twiboost_key",
    }:
        return False

    mirror = db.get_mirror_user(uid) if db else None

    if d in {"main", "mirror_main"}:
        _show_mirror_dashboard(chat_id, c.from_user, msg_id=msg_id)
        return True

    if d == "mirror_create":
        set_state(uid, "mirror_create_name")
        _edit(
            chat_id,
            msg_id,
            "🪞 <b>Создание hosted-зеркала</b>\n\nОтправьте название зеркала. После этого бот попросит токен зеркала, Golden Key и TwiBoost API, затем сам запустит отдельный инстанс на сервере.",
            telebot.types.InlineKeyboardMarkup().add(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data="mirror_main")),
        )
        return True

    if not mirror:
        _show_mirror_dashboard(chat_id, c.from_user, msg_id=msg_id)
        return True

    if str(mirror.get("status")) == "blocked":
        _show_mirror_dashboard(chat_id, c.from_user, msg_id=msg_id)
        return True

    if d == "mirror_settings":
        _edit(chat_id, msg_id, _render_mirror_dashboard_text(mirror), _mirror_settings_menu())
        return True

    if d == "mirror_report":
        set_state(uid, "mirror_report_revenue")
        _edit(
            chat_id,
            msg_id,
            "💸 <b>Отчет за месяц</b>\n\nВведите сумму вашей выручки за текущий месяц в рублях.",
            telebot.types.InlineKeyboardMarkup().add(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data="mirror_main")),
        )
        return True

    if d == "mirror_due":
        if _is_mirror_role():
            s = _mirror_runtime_financials()
            _edit(
                chat_id,
                msg_id,
                "📄 <b>Ваш долг владельцу</b>\n\n"
                f"📅 Месяц: <b>{html.escape(str(s['report_month']))}</b>\n"
                f"📦 Заказов: <b>{s['total_orders']}</b>\n"
                f"📈 Чистая прибыль: <b>{s['net_profit']:.2f}₽</b>\n"
                f"💸 Процент владельца: <b>{s['share_percent']:.2f}%</b>\n"
                f"✅ К переводу: <b>{s['amount_due']:.2f}₽</b>",
                telebot.types.InlineKeyboardMarkup().add(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data="main")),
            )
            return True
        report = _mirror_current_report(mirror) or db.get_latest_mirror_report(mirror["id"])
        revenue = float(report.get("revenue", 0)) if report else 0.0
        due = float(report.get("amount_due", 0)) if report else 0.0
        month = report.get("report_month") if report else _mirror_month_key()
        _edit(
            chat_id,
            msg_id,
            "📄 <b>Ваш долг владельцу</b>\n\n"
            f"📅 Месяц: <b>{html.escape(str(month))}</b>\n"
            f"💰 Выручка: <b>{revenue:.2f}₽</b>\n"
            f"💸 Процент владельца: <b>{float(mirror.get('share_percent', 5) or 5):.2f}%</b>\n"
            f"✅ К переводу: <b>{due:.2f}₽</b>",
            telebot.types.InlineKeyboardMarkup().add(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data="mirror_main")),
        )
        return True

    state_map = {
        "mirror_edit_bot_token": ("mirror_edit_bot_token", "🤖 Отправьте новый токен Telegram-бота."),
        "mirror_edit_funpay_key": ("mirror_edit_funpay_key", "🎮 Отправьте новый Golden Key FunPay."),
        "mirror_edit_twiboost_key": ("mirror_edit_twiboost_key", "🌐 Отправьте новый API ключ TwiBoost."),
    }
    if d in state_map:
        state_name, prompt = state_map[d]
        set_state(uid, state_name)
        _edit(chat_id, msg_id, prompt, telebot.types.InlineKeyboardMarkup().add(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data="mirror_main")))
        return True

    return False


def _handle_mirror_admin_callback(c: CallbackQuery, d: str, uid: int, chat_id: int, msg_id: int):
    if not is_admin(uid):
        return False
    if d in {
        "mirror_main",
        "mirror_create",
        "mirror_settings",
        "mirror_report",
        "mirror_due",
        "mirror_edit_bot_token",
        "mirror_edit_funpay_key",
        "mirror_edit_twiboost_key",
    }:
        return _handle_mirror_user_callback(c, d, uid, chat_id, msg_id)
    if d == "mirror_self":
        _show_mirror_dashboard(chat_id, c.from_user, msg_id=msg_id)
        return True
    if d == "mirrors":
        _show_mirror_admin_list(chat_id, msg_id=msg_id)
        return True
    if d == "mirroradm_create":
        set_state(uid, "mirror_admin_create_name")
        _edit(chat_id, msg_id, "🪞 <b>Создание зеркала</b>\n\nОтправьте название нового зеркала.", telebot.types.InlineKeyboardMarkup().add(telebot.types.InlineKeyboardButton("◀️ К зеркалам", callback_data="mirrors")))
        return True
    if d.startswith("mirroradm_page_"):
        page = int(d.split("_")[-1])
        _show_mirror_admin_list(chat_id, msg_id=msg_id, page=page)
        return True
    if d.startswith("mirroradm_reports_"):
        mirror_id = int(d.split("_")[-1])
        mirror = db.get_mirror_user_by_id(mirror_id)
        if not mirror:
            _edit(chat_id, msg_id, "❌ Зеркало не найдено.", telebot.types.InlineKeyboardMarkup().add(telebot.types.InlineKeyboardButton("◀️ К зеркалам", callback_data="mirrors")))
            return True
        reports = db.get_mirror_reports(mirror_id, limit=12)
        lines = [f"📄 <b>Отчеты зеркала {_mirror_name(mirror)}</b>", ""]
        if not reports:
            lines.append("Пока нет отчетов.")
        else:
            for report in reports:
                lines.append(
                    f"• {report['report_month']}: "
                    f"{float(report.get('revenue', 0)):.2f}₽ → "
                    f"{float(report.get('amount_due', 0)):.2f}₽"
                )
        markup = telebot.types.InlineKeyboardMarkup().add(telebot.types.InlineKeyboardButton("◀️ К зеркалу", callback_data=f"mirroradm_{mirror_id}"))
        _edit(chat_id, msg_id, "\n".join(lines), markup)
        return True
    if d.startswith("mirroradm_share_"):
        mirror_id = int(d.split("_")[-1])
        mirror = db.get_mirror_user_by_id(mirror_id)
        if mirror:
            set_state(uid, "mirror_admin_share", mirror_id=mirror_id)
            _edit(chat_id, msg_id, f"💸 Текущая доля: <b>{float(mirror.get('share_percent', 5) or 5):.2f}%</b>\n\nВведите новый процент владельца:", telebot.types.InlineKeyboardMarkup().add(telebot.types.InlineKeyboardButton("◀️ К зеркалу", callback_data=f"mirroradm_{mirror_id}")))
        return True
    if d.startswith("mirroradm_toggle_"):
        mirror_id = int(d.split("_")[-1])
        mirror = db.get_mirror_user_by_id(mirror_id)
        if mirror:
            new_status = "active" if str(mirror.get("status")) == "blocked" else "blocked"
            db.update_mirror_user(mirror_id, status=new_status)
            _show_mirror_admin_item(chat_id, msg_id, mirror_id)
        return True
    if d.startswith("mirroradm_reset_due_"):
        mirror_id = int(d.split("_")[-1])
        month = _mirror_month_key()
        # Обнуляем долг за текущий месяц, помечая как "paid"
        if db:
            report = db.get_mirror_report(mirror_id, month)
            if report:
                db.upsert_mirror_report(mirror_id, month, revenue=report.get("revenue",0), share_percent=report.get("share_percent",0), amount_due=0, status="paid")
            else:
                db.upsert_mirror_report(mirror_id, month, revenue=0, share_percent=0, amount_due=0, status="paid")
        bot.answer_callback_query(c.id, "✅ Долг за текущий месяц обнулен")
        _show_mirror_admin_item(chat_id, msg_id, mirror_id)
        return True
    
    if d.startswith("mirroradm_delete_confirm_"):
        mirror_id = int(d.split("_")[-1])
        mirror = db.get_mirror_user_by_id(mirror_id)
        ok, message = _delete_mirror_instance(mirror)
        if ok:
            _show_mirror_admin_list(chat_id, msg_id=msg_id)
            try:
                bot.send_message(chat_id, f"✅ {message}", parse_mode="HTML")
            except Exception:
                pass
        else:
            markup = telebot.types.InlineKeyboardMarkup()
            markup.add(telebot.types.InlineKeyboardButton("◀️ К зеркалам", callback_data="mirrors"))
            _edit(chat_id, msg_id, f"❌ {html.escape(message)}", markup)
        return True
    if d.startswith("mirroradm_delete_"):
        mirror_id = int(d.split("_")[-1])
        mirror = db.get_mirror_user_by_id(mirror_id)
        if not mirror:
            _edit(chat_id, msg_id, "❌ Зеркало не найдено.", telebot.types.InlineKeyboardMarkup().add(telebot.types.InlineKeyboardButton("◀️ К зеркалам", callback_data="mirrors")))
            return True
        markup = telebot.types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            telebot.types.InlineKeyboardButton("🗑 Да, удалить", callback_data=f"mirroradm_delete_confirm_{mirror_id}"),
            telebot.types.InlineKeyboardButton("◀️ Назад", callback_data=f"mirroradm_{mirror_id}"),
        )
        _edit(
            chat_id,
            msg_id,
            "⚠️ <b>Удаление зеркала</b>\n\n"
            f"Будут удалены:\n"
            f"• запись зеркала <b>{html.escape(_mirror_name(mirror))}</b>\n"
            "• его внутренняя база и логи\n"
            "• запущенный процесс зеркала\n\n"
            "После этого пользователь сможет создать новое зеркало заново.",
            markup,
        )
        return True
    if d.startswith("mirroradm_"):
        mirror_id = int(d.split("_")[-1])
        _show_mirror_admin_item(chat_id, msg_id, mirror_id)
        return True
    return False

def _usd_rub_rate():
    return cfg.get("usd_rub_rate", 92) if cfg else 92


def _format_twiboost_balance(balance, currency):
    currency = str(currency or "USD").upper()
    usd_rub = _usd_rub_rate()
    symbol_map = {"USD": "$", "EUR": "EUR", "RUB": "₽", "RUR": "₽"}
    symbol = symbol_map.get(currency, currency)
    if currency == "USD":
        return f"{balance:.2f}$", f"\n💴 ≈ {balance * usd_rub:.0f}₽ (курс {usd_rub})"
    if currency in {"RUB", "RUR"}:
        return f"{balance:.2f}₽", ""
    if currency == "EUR":
        return f"{balance:.2f} EUR", ""
    return f"{balance:.2f} {currency}", ""


def _funpay_withdraw_cfg():
    return cfg.get("funpay_withdraw", {}) if cfg else {}


def _funpay_withdraw_options():
    if not fp_client_ready():
        return {"banks": [], "ext_currency_options": []}
    try:
        result = fp.get_withdraw_options() or {}
    except Exception:
        result = {}
    return {
        "banks": result.get("banks") or [],
        "ext_currency_options": result.get("ext_currency_options") or [],
    }


def _funpay_withdraw_bank_label(wallet_extra):
    code = str(wallet_extra or "").strip()
    if not code:
        return ""
    for item in _funpay_withdraw_options().get("banks", []):
        if str(item.get("value") or "").strip() == code:
            return str(item.get("label") or "").strip()
    return ""


def _resolve_funpay_bank_input(value):
    raw = str(value or "").strip()
    if not raw:
        return "", ""
    normalized = raw.lower()
    banks = _funpay_withdraw_options().get("banks", [])
    for item in banks:
        code = str(item.get("value") or "").strip()
        label = str(item.get("label") or "").strip()
        if raw == code:
            return code, label
        if normalized == label.lower():
            return code, label
    for item in banks:
        code = str(item.get("value") or "").strip()
        label = str(item.get("label") or "").strip()
        if normalized in label.lower():
            return code, label
    return raw, ""


def _render_funpay_withdraw_text(preview_result=None):
    data = _funpay_withdraw_cfg()
    wallet = str(data.get("wallet") or "").strip() or "не задан"
    wallet_extra_code = str(data.get("wallet_extra") or "").strip()
    wallet_extra_name = _funpay_withdraw_bank_label(wallet_extra_code)
    if wallet_extra_code:
        wallet_extra = f"{wallet_extra_name} ({wallet_extra_code})" if wallet_extra_name else wallet_extra_code
    else:
        wallet_extra = "не задан"
    amount_int = int(data.get("amount_int") or 0)
    auto_enabled = bool(data.get("auto_enabled"))
    auto_min_balance = int(data.get("auto_min_balance") or 0)
    lines = [
        "💸 <b>Вывод средств FunPay</b>",
        "",
        f"💱 Валюта: {data.get('currency_id') or 'rub'} -> {data.get('ext_currency_id') or 'fps'}",
        f"📱 Телефон: <b>{wallet}</b>",
        f"🏦 Банк / код СБП: <b>{wallet_extra}</b>",
        f"💰 Сумма: <b>{amount_int}</b> ₽",
        f"🔐 2FA: {'задан' if str(data.get('twofactor_code') or '').strip() else 'не задан'}",
        f"🤖 Автовывод: {'✅ включён' if auto_enabled else '❌ выключен'}",
        f"🎯 Порог запуска: <b>{auto_min_balance}</b> ₽" if auto_min_balance > 0 else "🎯 Порог запуска: <b>не задан</b>",
        "",
        "Минимум для вывода: 80 ₽.",
        "Комиссия FunPay: 3%, но не меньше 30 ₽.",
        "Кнопка «Проверить вывод» делает только preview-запрос и не выводит деньги.",
    ]
    if preview_result is not None:
        lines.extend(["", "──────────", "👀 <b>Результат preview</b>"])
        if preview_result.get("success"):
            raw_text = str(preview_result.get("raw_text") or "")[:1200]
            data = preview_result.get("data") or {}
            msg = ""
            if isinstance(data, dict):
                msg = str(data.get("msg") or "").strip()
                if data.get("error") is False:
                    wallet = str(data.get("wallet") or "").strip()
                    amount_int = str(data.get("amount_int") or "").strip()
                    amount_ext = str(data.get("amount_ext") or "").strip()
                    bank_name = str(data.get("fps_bank_name") or "").strip()
                    preview_lines = ["✅ Preview успешен"]
                    if wallet:
                        preview_lines.append(f"📱 Телефон: {wallet}")
                    if bank_name:
                        preview_lines.append(f"🏦 Банк: {bank_name}")
                    if amount_int:
                        preview_lines.append(f"💸 Списать: {amount_int} ₽")
                    if amount_ext:
                        preview_lines.append(f"📥 К получению: {amount_ext} ₽")
                    lines.append("<code>" + html.escape("\n".join(preview_lines)) + "</code>")
                else:
                    lines.append("<code>" + html.escape(msg or raw_text or str(data)) + "</code>")
            else:
                lines.append("<code>" + html.escape(msg or raw_text or str(data)) + "</code>")
        else:
            lines.append(f"❌ {html.escape(preview_result.get('error') or 'unknown')}")
    return "\n".join(lines)


def _fp_currency_text(currency):
    if hasattr(currency, "name"):
        if currency.name == "RUB":
            return "₽"
        if currency.name == "USD":
            return "$"
        if currency.name == "EUR":
            return "€"
    text = str(currency)
    if text in {"₽", "в‚Ѕ", "Р₽", "RUB", "RUR"}:
        return "₽"
    if text in {"€", "в‚¬", "Р€", "EUR"}:
        return "€"
    return text


def _format_ru_datetime(iso_value):
    if not iso_value:
        return "не указано"
    try:
        return datetime.fromisoformat(str(iso_value)).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return str(iso_value)


def _build_buyer_completed_message(session, order):
    service_name = order.get("service_name") or session.get("lot_name") or "Услуга"
    quantity = order.get("quantity") or session.get("pending_qty") or 0
    order_label = order.get("id") or session.get("order_id") or "?"
    lines = [
        "✅ Заказ успешно завершён",
        "",
        f"🧾 Номер заказа: #{order_label}",
        f"📦 Услуга: {service_name}",
    ]
    if quantity:
        lines.append(f"📊 Количество: {quantity}")
    lines.extend([
        "",
        "Если всё в порядке, не забудьте подтвердить заказ на FunPay.",
        "",
        "Доступные команды:",
        "Инфо /status - показать текущий статус",
        "Рефил /refill - запросить рефилл, если он доступен",
    ])
    return "\n".join(lines)


def _build_admin_completed_message(order, completed_at=None):
    completed_at = completed_at or order.get("completed_at")
    service_name = html.escape(order.get("service_name") or order.get("lot_name") or "Услуга")
    buyer = html.escape(order.get("buyer_username") or "неизвестно")
    completed_text = html.escape(_format_ru_datetime(completed_at))
    return (
        f"✅ <b>Заказ #{order['id']} завершён</b>\n\n"
        f"📦 {service_name}\n"
        f"👤 {buyer}\n"
        f"🕒 {completed_text}\n"
        f"💰 +{order.get('profit', 0):.0f}₽"
    )


def notify_funpay_order_completed(order_id, completed_at=None):
    order = db.get_order(order_id)
    if not order:
        return False
        
    completed_at = completed_at or order.get("completed_at") or datetime.now().isoformat()
    session = db.get_funpay_session(order.get("funpay_order_id")) or db.get_funpay_session_by_order(order_id)

    # 🔥 1. Всегда пытаемся уведомить покупателя (без проверок buyer_confirmed)
    if session and session.get("chat_id"):
        try:
            msg = _session_completed_text(session, order)
            _funpay_send_message(session["chat_id"], msg)
        except Exception as e:
            logger.warning(f"Failed to send completion message to buyer #{order_id}: {e}")

    # Обновляем состояние сессии
    if session:
        session_updates = {"state": "completed"}
        if not session.get("buyer_confirmed"):
            try:
                due_at = datetime.fromisoformat(str(completed_at)) + timedelta(days=1)
            except ValueError:
                due_at = datetime.now() + timedelta(days=1)
            session_updates["support_ticket_due_at"] = due_at.isoformat()
            session_updates["buyer_confirmed"] = 0
            session_updates["support_ticket_sent"] = 0
        else:
            session_updates["support_ticket_due_at"] = ""
            session_updates["buyer_confirmed"] = 1
            session_updates["support_ticket_sent"] = 0
        db.update_funpay_session(session["funpay_order_id"], **session_updates)

    # 🔥 2. Уведомление админу с ФАКТИЧЕСКОЙ прибылью
    if cfg.get("notifications.order_completed", True):
        actual_cost = round(order.get("cost_price", 0), 2)
        actual_profit = round(order.get("profit", 0), 2)
        
        lines = [
            f"✅  <b>Заказ #{order['id']} завершён</b>",
            "",
            f"📦 {html.escape(order.get('service_name') or order.get('lot_name') or 'Услуга')}",
            f"👤 {html.escape(order.get('buyer_username') or 'неизвестно')}",
            f"🕒 {_format_ru_datetime(completed_at)}",
            "━" * 25,
            f"💰 Цена на FunPay: <b>{_format_money(order.get('sell_price'))}</b>",
            f"💵 Списано с TwiBoost: <b>{_format_money(actual_cost)}</b>",
            f"📈 Фактическая прибыль: <b>{_format_money(actual_profit)}</b>",
        ]
        text = "\n".join(lines)
        
        for uid in cfg.admin_ids:
            try:
                bot.send_message(uid, text, parse_mode="HTML")
            except Exception:
                pass
                
    return True


def build_support_ticket_message(session, order=None):
    service_name = session.get("lot_name") or "Услуга"
    completed_at = session.get("updated_at")
    local_order_id = session.get("order_id") or "—"
    if order:
        service_name = order.get("service_name") or order.get("lot_name") or service_name
        completed_at = order.get("completed_at") or completed_at
        local_order_id = order.get("id") or local_order_id
    service_name = str(service_name or "Услуга").strip()
    if len(service_name) > 90:
        service_name = service_name[:89].rstrip() + "…"

    return (
        "⏰ <b>Прошли 24 часа после завершения заказа</b>\n\n"
        f"🎮 FunPay заказ: <b>#{html.escape(session.get('funpay_order_id') or '—')}</b>\n"
        f"🧾 Внутренний заказ: <b>#{html.escape(str(local_order_id))}</b>\n"
        f"📦 Услуга: {html.escape(service_name)}\n"
        f"👤 Покупатель: {html.escape(session.get('buyer_username') or 'неизвестно')}\n"
        f"🕒 Выполнен: {html.escape(_format_ru_datetime(completed_at))}\n"
        "💬 Если покупатель не подтвердил заказ на FunPay, проверьте диалог и при необходимости напишите в поддержку."
    )


def build_funpay_support_request_message(session, order=None):
    service_name = session.get("lot_name") or "Услуга"
    completed_at = session.get("updated_at")
    local_order_id = session.get("order_id") or "—"
    if order:
        service_name = order.get("service_name") or order.get("lot_name") or service_name
        completed_at = order.get("completed_at") or completed_at
        local_order_id = order.get("id") or local_order_id

    lines = [
        "Здравствуйте.",
        "",
        "Прошу проверить заказ, покупатель не подтвердил его более 24 часов после выполнения.",
        f"FunPay заказ: #{session.get('funpay_order_id') or '—'}",
        f"Внутренний заказ: #{local_order_id}",
        f"Услуга: {service_name}",
        f"Покупатель: {session.get('buyer_username') or 'неизвестно'}",
        f"Выполнен: {_format_ru_datetime(completed_at)}",
    ]
    return "\n".join(lines)


def _notification_enabled(key):
    return bool(cfg.get(f"notifications.{key}", True))


def _format_money(value):
    try:
        return f"{float(value):.2f}₽"
    except (TypeError, ValueError):
        return "0.00₽"


def _promo_code_looks_valid(text):
    return bool(PROMO_CODE_REGEX.match((text or "").strip()))


def _is_recent_funpay_order_event(order_id, ttl=15):
    now = time.time()
    with recent_funpay_order_events_lock:
        stale = [key for key, ts in recent_funpay_order_events.items() if now - ts > ttl]
        for key in stale:
            recent_funpay_order_events.pop(key, None)
        last_seen = recent_funpay_order_events.get(order_id)
        if last_seen and now - last_seen <= ttl:
            return True
        recent_funpay_order_events[order_id] = now
        return False


def _should_send_funpay_payment_message(order_id, ttl=60):
    now = time.time()
    with recent_funpay_payment_messages_lock:
        stale = [key for key, ts in recent_funpay_payment_messages.items() if now - ts > ttl]
        for key in stale:
            recent_funpay_payment_messages.pop(key, None)
        last_seen = recent_funpay_payment_messages.get(order_id)
        if last_seen and now - last_seen <= ttl:
            return False
        recent_funpay_payment_messages[order_id] = now
        return True


def _is_recent_review_dispatch(order_id, stars=0, ttl=15):
    now = time.time()
    key = f"{order_id}:{int(stars or 0)}"
    with recent_review_dispatch_events_lock:
        stale = [item for item, ts in recent_review_dispatch_events.items() if now - ts > ttl]
        for item in stale:
            recent_review_dispatch_events.pop(item, None)
        last_seen = recent_review_dispatch_events.get(key)
        if last_seen and now - last_seen <= ttl:
            return True
        recent_review_dispatch_events[key] = now
        return False


def _build_buyer_confirmed_message(session, order=None):
    service_name = session.get("lot_name") or "Услуга"
    order_label = session.get("funpay_order_id") or "?"
    if order:
        service_name = order.get("service_name") or order.get("lot_name") or service_name
        order_label = order.get("funpay_order_id") or order_label
    return (
        "🫶 Заказ подтверждён\n\n"
        f"🎮 FunPay: #{order_label}\n"
        f"📦 Услуга: {service_name}\n\n"
        "Спасибо за подтверждение. Если понадобится помощь, напишите в этот чат."
    )


def _build_buyer_promo_hint(quantity):
    lines = [
        "",
        "Если у вас есть промокод, отправьте его отдельным сообщением.",
        "Если промокода нет, просто отправьте ссылку.",
    ]
    if quantity:
        lines.insert(0, f"Текущее количество: {quantity}")
    return "\n".join(lines)


def _get_review_bonus_offer(order=None):
    lot_id = order.get("lot_id") if order else None
    offers = db.get_upsells(active_only=True, lot_id=lot_id) if db else []
    if offers:
        order_price = float((order or {}).get("sell_price") or (order or {}).get("price") or 0)
        matched = []
        for offer in offers:
            min_amount = float(offer.get("min_order_amount") or 0)
            max_amount = float(offer.get("max_order_amount") or 0)
            if order_price < min_amount:
                continue
            if max_amount > 0 and order_price > max_amount:
                continue
            matched.append(offer)
        if matched:
            matched.sort(key=lambda item: (float(item.get("min_order_amount") or 0), float(item.get("discount_value") or 0)), reverse=True)
            return matched[0]
        return None
    return None


def _create_review_bonus_promo(order, session=None):
    offer = _get_review_bonus_offer(order)
    if not offer:
        return None
    percent = float(offer.get("discount_value") or 0)
    if percent <= 0:
        return None
    prefix = re.sub(r"[^A-Z0-9]", "", str(offer.get("promo_prefix") or "BONUS").upper())[:8] or "BONUS"
    base_suffix = re.sub(r"[^A-Z0-9]", "", str(order.get("funpay_order_id") or order.get("id") or ""))[-6:] or "NEXT"
    code = f"{prefix}{base_suffix}"
    promo_max_uses = max(1, min(100, int(offer.get("promo_max_uses") or 1)))
    promo_apply_min_amount = float(offer.get("promo_apply_min_amount") or 0)
    promo_apply_max_amount = float(offer.get("promo_apply_max_amount") or 0)
    apply_range_text = _amount_range_text(promo_apply_min_amount, promo_apply_max_amount)
    if promo_apply_min_amount > 0 or promo_apply_max_amount > 0:
        default_bonus_text = f"+{percent:.0f}% к количеству на заказ {apply_range_text}"
    else:
        default_bonus_text = f"+{percent:.0f}% к количеству в следующем заказе"
    bonus_text = offer.get("bonus_text") or default_bonus_text
    expires_at = (datetime.now() + timedelta(days=int(offer.get("promo_duration_days") or 7))).isoformat()
    existing = db.get_promo(code)
    if existing:
        db.update_promo(
            code,
            discount_type="percent",
            discount_value=percent,
            max_uses=max(1, int(existing.get("max_uses") or promo_max_uses)),
            min_order_amount=promo_apply_min_amount,
            max_order_amount=promo_apply_max_amount,
            for_username=(session or {}).get("buyer_username") or order.get("buyer_username") or "",
            is_active=1,
            valid_until=expires_at,
        )
        existing = db.get_promo(code) or existing
        existing["bonus_text"] = bonus_text
        return existing
    db.add_promo(
        code=code,
        discount_type="percent",
        discount_value=percent,
        max_uses=promo_max_uses,
        min_order_amount=promo_apply_min_amount,
        max_order_amount=promo_apply_max_amount,
        for_username=(session or {}).get("buyer_username") or order.get("buyer_username") or "",
        is_active=1,
        valid_until=expires_at,
    )
    if offer.get("id"):
        db.increment_upsell(offer["id"], "times_shown")
    promo = db.get_promo(code) or {"code": code, "discount_value": percent, "valid_until": expires_at, "max_uses": promo_max_uses}
    promo["bonus_text"] = bonus_text
    return promo


def _apply_promo_bonus(base_quantity, promo):
    quantity = int(base_quantity or 0)
    if quantity <= 0 or not promo:
        return quantity
    value = float(promo.get("discount_value") or 0)
    if str(promo.get("discount_type") or "").lower() == "fixed":
        return quantity + max(0, int(round(value)))
    bonus_qty = max(1, int(round(quantity * value / 100.0))) if value > 0 else 0
    return quantity + bonus_qty


def _session_current_quantity(session):
    return int(session.get("pending_qty") or 0)


def _session_base_quantity(session):
    current_qty = _session_current_quantity(session)
    promo_value = float(session.get("promo_value") or 0)
    if current_qty <= 0:
        return current_qty
    if promo_value <= 0:
        return current_qty
    estimated = int(round(current_qty / (1 + promo_value / 100.0)))
    return max(1, estimated)


def _validate_session_promo(session, code):
    promo = db.get_promo(code)
    if not promo:
        return None, "Промокод не найден."

    now = datetime.now()
    valid_until = str(promo.get("valid_until") or "").strip()
    if not promo.get("is_active"):
        return None, "Промокод отключён."
    if promo.get("max_uses") and int(promo.get("used_count") or 0) >= int(promo.get("max_uses") or 0):
        return None, "Лимит использований промокода исчерпан."
    if valid_until:
        try:
            if datetime.fromisoformat(valid_until) < now:
                db.deactivate_expired_promos()
                return None, "Промокод истёк."
        except ValueError:
            pass

    for_username = str(promo.get("for_username") or "").strip().lower()
    buyer_username = str(session.get("buyer_username") or "").strip().lower()
    if for_username and for_username != buyer_username:
        return None, "Этот промокод выдан другому покупателю."
    if promo.get("min_order_amount") and float(session.get("price") or 0) < float(promo.get("min_order_amount") or 0):
        return None, f"Промокод доступен только для заказов от {float(promo.get('min_order_amount') or 0):.2f}₽."
    if promo.get("max_order_amount") and float(session.get("price") or 0) > float(promo.get("max_order_amount") or 0):
        return None, f"Промокод доступен только для заказов до {float(promo.get('max_order_amount') or 0):.2f}₽."

    return promo, ""


def _reset_session_promo(funpay_order_id):
    session = db.get_funpay_session(funpay_order_id)
    if not session:
        return
    db.update_funpay_session(
        funpay_order_id,
        promo_code="",
        promo_value=0,
        pending_qty=_session_base_quantity(session),
    )


def _promo_uses_text(max_uses):
    try:
        uses = max(1, int(max_uses or 1))
    except (TypeError, ValueError):
        uses = 1
    if uses == 1:
        return "1 применение"
    return f"{uses} применений"


def _amount_range_text(min_amount=0, max_amount=0):
    min_amount = float(min_amount or 0)
    max_amount = float(max_amount or 0)
    if min_amount > 0 and max_amount > 0:
        return f"от {min_amount:.0f}₽ до {max_amount:.0f}₽"
    if min_amount > 0:
        return f"от {min_amount:.0f}₽"
    if max_amount > 0:
        return f"до {max_amount:.0f}₽"
    return "без ограничений"


def _review_bonus_code(order):
    offer = _get_review_bonus_offer(order)
    prefix = re.sub(r"[^A-Z0-9]", "", str((offer or {}).get("promo_prefix") or "BONUS").upper())[:8] or "BONUS"
    base_suffix = re.sub(r"[^A-Z0-9]", "", str(order.get("funpay_order_id") or order.get("id") or ""))[-6:] or "NEXT"
    return f"{prefix}{base_suffix}"


def _session_lot_review_bonus_message(lot):
    service_name = str((lot or {}).get("name") or (lot or {}).get("funpay_lot_name") or "этого товара").strip()
    quantity = _lot_review_bonus_quantity(lot)
    lines = [
        "🎁 Спасибо за отзыв 5★!",
        "",
        "Для этого лота у вас доступен бонусный запуск.",
        f"📦 Товар: {service_name}",
        f"📊 Количество: {quantity}",
        "",
        "Отправьте ссылку на пост, канал или страницу, куда нужно начислить бонус.",
        "Если бонус сейчас не нужен, отправьте: без бонуса",
        "После получения ссылки бот сразу запустит бонусный заказ.",
    ]
    return "\n".join(lines)


def _session_lot_review_bonus_invalid_message(lot):
    quantity = _lot_review_bonus_quantity(lot)
    lines = [
        "❌ Ссылка не распознана",
        "",
        f"🎁 Бонусное количество: {quantity}",
        "Отправьте ссылку на пост, канал или страницу одним сообщением.",
    ]
    return "\n".join(lines)


def _session_lot_review_bonus_started_message(order_id, lot, link):
    service_name = str((lot or {}).get("name") or (lot or {}).get("funpay_lot_name") or "этого товара").strip()
    quantity = _lot_review_bonus_quantity(lot)
    return (
        "✅ Бонусный заказ запущен!\n\n"
        f"🧾 Номер заказа: #{order_id}\n"
        f"📦 Товар: {service_name}\n"
        f"📊 Количество: {quantity}\n"
        f"🔗 Ссылка: {link}\n\n"
        "Если понадобится помощь, просто напишите в этот чат."
    )


def _session_lot_review_bonus_error_message(error):
    return (
        "❌ Не удалось запустить бонусный заказ.\n\n"
        f"Причина: {error}\n\n"
        "Попробуйте отправить ссылку ещё раз чуть позже."
    )


def _revoke_review_bonus(order, notify=False, stars=0, deleted=False, details=None):
    session = db.get_funpay_session(order["funpay_order_id"]) or db.get_funpay_session_by_order(order["id"])
    promo_code = _review_bonus_code(order)
    promo = db.get_promo(promo_code)
    last_review_stars = max(0, min(5, int(order.get("last_review_stars") or 0)))
    bonus_order_id = 0
    bonus_pending = False
    if session:
        try:
            bonus_order_id = int(session.get("review_bonus_order_id") or 0)
        except (TypeError, ValueError):
            bonus_order_id = 0
        bonus_pending = bool(session.get("review_bonus_state")) and bonus_order_id <= 0
    had_reward = bool(promo and promo.get("is_active")) or bonus_pending or bonus_order_id > 0 or last_review_stars >= 5
    if promo and promo.get("is_active"):
        db.update_promo(promo_code, is_active=0)
    if session and bonus_pending:
        db.update_funpay_session(
            session["funpay_order_id"],
            review_bonus_state="",
            review_bonus_link="",
        )
        session = db.get_funpay_session(session["funpay_order_id"]) or session
    db.update_order(order["id"], review_bonus_sent=0)
    if notify and had_reward and bonus_order_id <= 0:
        chat_id = _session_review_chat(details or {}, session)
        if _notification_enabled("review_bonus") and chat_id:
            _funpay_send_message(
                chat_id,
                _session_review_bonus_revoked_message(
                    stars=stars,
                    promo_code=promo_code if promo else "",
                    deleted=deleted,
                ),
            )
    return had_reward


def _check_all_orders_v2(chat_id, msg_id):
    result = sync_active_orders_core(db, api, cfg, bot)
    if not result.get("success"):
        _edit(chat_id, msg_id, f"❌ {result.get('error', 'Ошибка проверки заказов')}", kb.orders_menu())
        return
    if result.get("checked", 0) == 0:
        _edit(chat_id, msg_id, "✅ Нет активных заказов.", kb.orders_menu())
        return
    text = (
        f"✅ Проверено: <b>{result.get('checked', 0)}</b> заказов\n"
        f"🔄 Обновлено: <b>{result.get('updated', 0)}</b>"
    )
    _edit(chat_id, msg_id, text, kb.orders_menu())
    if promo.get("is_active"):
        db.update_promo(promo_code, is_active=0)
    db.update_order(order["id"], review_bonus_sent=0)
    return True


def _try_apply_funpay_promo(session, raw_text, chat_id):
    code = (raw_text or "").strip().upper()
    if not _promo_code_looks_valid(code):
        return False
    if session.get("promo_code"):
        _funpay_send_message(chat_id, f"⚠️ На этот заказ уже применён промокод {session.get('promo_code')}.")
        return True
    promo = db.get_promo(code)
    if not promo:
        return False
    now = datetime.now()
    valid_until = str(promo.get("valid_until") or "").strip()
    if not promo.get("is_active"):
        _funpay_send_message(chat_id, "⚠️ Этот промокод уже отключен.")
        return True
    if promo.get("max_uses") and int(promo.get("used_count") or 0) >= int(promo.get("max_uses") or 0):
        _funpay_send_message(chat_id, "⚠️ Этот промокод уже использован.")
        return True
    if valid_until:
        try:
            if datetime.fromisoformat(valid_until) < now:
                db.deactivate_expired_promos()
                _funpay_send_message(chat_id, "⏰ Срок действия промокода истёк.")
                return True
        except ValueError:
            pass
    for_username = str(promo.get("for_username") or "").strip().lower()
    buyer_username = str(session.get("buyer_username") or "").strip().lower()
    if for_username and for_username != buyer_username:
        _funpay_send_message(chat_id, "⚠️ Этот промокод выдан другому покупателю.")
        return True
    if promo.get("min_order_amount") and float(session.get("price") or 0) < float(promo.get("min_order_amount") or 0):
        _funpay_send_message(chat_id, "⚠️ Этот промокод не подходит для текущего заказа.")
        return True
    if promo.get("max_order_amount") and float(session.get("price") or 0) > float(promo.get("max_order_amount") or 0):
        _funpay_send_message(chat_id, f"⚠️ Этот промокод действует только для заказов до {float(promo.get('max_order_amount') or 0):.0f}₽.")
        return True
    base_qty = _session_base_quantity(session)
    new_qty = _apply_promo_bonus(base_qty, promo)
    db.update_funpay_session(
        session["funpay_order_id"],
        pending_qty=new_qty,
        promo_code=code,
        promo_value=float(promo.get("discount_value") or 0),
    )
    session = db.get_funpay_session(session["funpay_order_id"]) or session
    hint = "Теперь отправьте ссылку для запуска заказа."
    if session.get("pending_link"):
        hint = "Если всё верно, ответьте 'Да' для запуска."
    _funpay_send_message(
        chat_id,
        "🎫 Промокод принят!\n\n"
        f"Код: {code}\n"
        f"Новое количество: {new_qty}\n\n"
        f"{hint}"
    )
    return True


def _session_review_bonus_message(order, session, promo):
    percent = float(promo.get("discount_value") or 0)
    bonus_text = promo.get("bonus_text") or f"+{percent:.0f}% к количеству в следующем заказе"
    uses_text = _promo_uses_text(promo.get("max_uses"))
    return (
        "🎁 Спасибо за отзыв!\n\n"
        "⭐ Вы поставили 5 звёзд.\n"
        f"🎫 Ваш промокод: {promo['code']}\n"
        f"📈 Бонус: {bonus_text}\n"
        f"♻️ Доступно: {uses_text}\n"
        f"⏰ Действует до: {_format_ru_datetime(promo.get('valid_until'))}\n\n"
        "На следующей покупке после оплаты просто отправьте этот код в чат."
    )


def _session_review_thanks_message():
    return (
        "💜 Спасибо за отзыв!\n\n"
        "Мы получили вашу оценку. Если понадобится помощь или дополнительный запуск, просто напишите в этот чат."
    )


def _session_review_low_rating_message(stars=0):
    try:
        stars = max(0, min(5, int(stars or 0)))
    except (TypeError, ValueError):
        stars = 0
    stars_text = "⭐" * stars if stars > 0 else "без оценки"
    return (
        "💬 Спасибо за отзыв.\n\n"
        f"Ваша оценка: {stars_text}\n"
        "Нам жаль, что не всё прошло идеально.\n"
        "Если захотите, напишите, что именно можно улучшить, и мы постараемся помочь."
    )


def _session_review_bonus_revoked_message(stars=0, promo_code="", deleted=False):
    try:
        stars = max(0, min(5, int(stars or 0)))
    except (TypeError, ValueError):
        stars = 0
    stars_text = "⭐" * stars if stars > 0 else "без оценки"
    lines = [
        "⚠️ Бонус за отзыв сброшен",
        "",
    ]
    if deleted or stars <= 0:
        lines.append("Вы удалили отзыв 5★, поэтому выданный бонус больше недоступен.")
    else:
        lines.append(f"Ваша оценка изменена на {stars_text}, поэтому бонус за 5★ больше недоступен.")
        lines.append("Нам жаль, что не всё прошло идеально.")
    if promo_code:
        lines.append(f"🎫 Промокод: {promo_code}")
    lines.extend([
        "",
        "Если вы снова поставите 5★, бонус появится автоматически.",
    ])
    return "\n".join(lines)


def _session_completed_message(session, order, buyer_confirmed=False):
    service_name = order.get("service_name") or session.get("lot_name") or "Услуга"
    quantity = order.get("quantity") or session.get("pending_qty") or 0
    order_label = order.get("id") or session.get("order_id") or "?"
    lines = [
        "✅ Заказ успешно завершён",
        "",
        f"🧾 Номер заказа: #{order_label}",
        f"📦 Услуга: {service_name}",
    ]
    if quantity:
        lines.append(f"📊 Количество: {quantity}")
    lines.append("")
    if buyer_confirmed:
        lines.append("Спасибо, что вы уже подтвердили заказ на FunPay.")
    else:
        lines.append("Если всё в порядке, не забудьте подтвердить заказ на FunPay.")
    lines.extend([
        "",
        "Доступные команды:",
        "Инфо /status - показать текущий статус",
        "Рефил /refill - запросить рефилл, если он доступен",
    ])
    return "\n".join(lines)


def _session_initial_message(order_id, service_name, quantity):
    return (
        f"Спасибо за оплату #{order_id}.\n"
        f"Услуга: {service_name}\n"
        f"Количество: {quantity}\n"
        f"{_build_buyer_promo_hint(quantity)}"
    )


def _session_started_message(order_id, service_name, quantity):
    return (
            "✅ Заказ запущен!\n\n"
            f"Номер заказа: #{order_id}\n"
            f"Услуга: {service_name}\n"
            f"Количество: {quantity}\n\n"
            "Доступные команды:\n"
            "Инфо или /status - показать текущий статус\n"
            "Рефил или /refill - запросить рефилл, если он доступен"
        )


def _session_status_message(order):
    progress = _get_order_progress(order)
    quantity = max(int(order.get("quantity") or 0), 0)
    completed = _get_order_completed_quantity(order)
    current_total = _get_order_current_total(order)
    remains = _safe_int(order.get("api_remains"))
    if order.get("status") == "completed":
        remains = 0
    status_labels = {
        "pending": "⏳ Ожидание",
        "processing": "🔄 Обработка",
        "in_progress": "🔄 Выполняется",
        "completed": "✅ Выполнен",
        "partial": "⚠️ Частично выполнен",
        "failed": "❌ Ошибка",
        "cancelled": "🚫 Отменён",
    }
    lines = [
        "📋 Информация по заказу",
        "",
        f"🧾 Номер: #{order['id']}",
        f"➗ Часть: {order.get('split_index')}/{order.get('split_total')}" if int(order.get("split_total") or 0) > 1 else "",
        f"📦 Услуга: {order.get('service_name') or order.get('lot_name') or 'Услуга'}",
        f"🔗 Ссылка: {order.get('link') or '—'}",
        f"📊 Заказано: {quantity}",
        f"✅ Выполнено: {completed} из {quantity}",
    ]
    lines = [line for line in lines if line]
    if current_total is not None:
        lines.append(f"🔢 Сейчас на странице: {current_total}")
    lines.extend([
        f"📈 Прогресс: {progress}%",
        f"📌 Статус: {status_labels.get(order.get('status'), order.get('status'))}",
    ])
    if remains is not None:
        lines.append(f"📉 Осталось: {max(remains, 0)}")
    return "\n".join(lines)


def _session_order_create_message(order_id, service_name, quantity, promo_code=""):
    lines = [
        "✅ Заказ запущен!",
        "",
        f"Номер заказа: #{order_id}",
        f"Услуга: {service_name}",
        f"Количество: {quantity}",
    ]
    if promo_code:
        lines.append(f"🎫 Промокод: {promo_code}")
    lines.extend([
        "",
        "Доступные команды:",
        "Инфо или /status - показать текущий статус",
        "Рефил или /refill - запросить рефилл, если он доступен",
    ])
    return "\n".join(lines)


def _session_active_promo_message(session):
    code = session.get("promo_code")
    if not code:
        return ""
    qty = _session_current_quantity(session)
    return (
        "\n\n"
        f"🎫 Промокод: {code}\n"
        f"Текущее количество: {qty}"
    )


def _build_review_prompt_message(session):
    qty = _session_current_quantity(session)
    return (
        "Получена ссылка.\n"
        "Подтвердите, что всё верно, ответив 'Да' для запуска заказа."
        f"{_session_active_promo_message(session) if qty else ''}"
    )


def _resend_payment_prompt(session):
    return _session_initial_message(
        session.get("funpay_order_id"),
        session.get("lot_name") or "Услуга",
        _session_current_quantity(session),
    )


def _session_has_confirmed(session):
    return bool(session.get("buyer_confirmed"))


def _process_review_bonus_once(order):
    # 🔥 1. Жесткая защита от дублей (игнорируем повторные события в течение 120 сек)
    if not _is_recent_review_dispatch(order["id"], stars=5, ttl=120):
        return False

    current_order = db.get_order(order["id"]) or order
    # Если уже отправлено — выходим
    if current_order.get("review_bonus_sent"):
        return True

    session = db.get_funpay_session(current_order["funpay_order_id"]) or db.get_funpay_session_by_order(current_order["id"])
    details = fp.get_order_details(order["funpay_order_id"]) if fp_client_ready() else {"success": False}
    if not details.get("success"):
        return False

    stars = int(details.get("review_stars") or 0)
    if stars <= 0:
        return False

    # 🔥 2. Явно берем лот из заказа, чтобы не запустить бонус на другую услугу
    lot = db.get_lot(current_order.get("lot_id")) if current_order.get("lot_id") else None
    if not lot:
        logger.warning("Review bonus skipped for #%s: lot_id missing", current_order["funpay_order_id"])
        db.update_order(current_order["id"], review_bonus_sent=1)
        return True

    # 🔥 3. Проверка соответствия лота и FunPay заказа
    if lot.get("funpay_lot_id") and details.get("offer_id") and str(lot.get("funpay_lot_id")) != str(details.get("offer_id")):
        logger.warning("Review bonus skipped for #%s: lot mismatch", current_order["funpay_order_id"])
        db.update_order(current_order["id"], review_bonus_sent=1)
        return True

    chat_id = session.get("chat_id") if session else details.get("chat_id")
    if not chat_id and details.get("buyer_id"):
        try: chat_id = fp.get_chat_id_by_username(details["buyer_id"])
        except: chat_id = 0
    if not chat_id and details.get("buyer_username"):
        chat = fp.get_chat_by_name(details["buyer_username"], make_request=True)
        chat_id = chat.id if chat else 0
    if not chat_id:
        return False

    if stars >= 5:
        # Если сессия уже ждет ссылку — не шлем дубль
        if session and session.get("review_bonus_state") == "awaiting_link":
            db.update_order(current_order["id"], review_bonus_sent=1)
            return True

        if _lot_review_bonus_enabled(lot):
            db.update_funpay_session(
                current_order["funpay_order_id"],
                review_bonus_state="awaiting_link",
                review_bonus_link="",
                review_bonus_order_id=0
            )
            if _funpay_send_message(chat_id, _session_lot_review_bonus_message(lot)):
                db.update_order(current_order["id"], review_bonus_sent=1)
                return True

        promo = _create_review_bonus_promo(current_order, session)
        message = _session_review_bonus_message(current_order, session or {}, promo) if promo else _session_review_thanks_message()
        if _notification_enabled("review_bonus") and _funpay_send_message(chat_id, message):
            db.update_order(current_order["id"], review_bonus_sent=1)
            return True

        db.update_order(current_order["id"], review_bonus_sent=1)
        return False

    if _notification_enabled("review_bonus") and _funpay_send_message(chat_id, _session_review_low_rating_message(stars)):
        db.update_order(current_order["id"], review_bonus_sent=1)
        return True

    db.update_order(current_order["id"], review_bonus_sent=1)
    return False


def _session_apply_promo_if_any(session, text, chat_id):
    return _try_apply_funpay_promo(session, text, chat_id)


def _session_awaiting_link_invalid_message():
    return "Если у вас есть промокод, отправьте его. Иначе пришлите ссылку вида https://..."


def _session_awaiting_confirmation_message(session):
    return "Ответьте 'Да' для запуска заказа, 'Нет' для изменения ссылки или отправьте промокод."


def _session_should_send_completed(session):
    return bool(session.get("chat_id"))


def _session_completed_payload(session, order):
    buyer_confirmed = _session_has_confirmed(session)
    return _session_completed_message(session, order, buyer_confirmed=buyer_confirmed), buyer_confirmed


def _session_started_payload(session, order_id):
    return _session_order_create_message(
        order_id,
        session.get("lot_name") or "Услуга",
        _session_current_quantity(session),
        promo_code=session.get("promo_code") or "",
    )


def _session_status_payload(order):
    return _session_status_message(order)


def _session_payment_payload(session):
    return _resend_payment_prompt(session)


def _session_after_promo_link_prompt(session):
    if session.get("pending_link"):
        return _build_review_prompt_message(session)
    return _resend_payment_prompt(session)


def _session_apply_promo_to_order(session, order_kwargs):
    promo_code = session.get("promo_code") or ""
    promo_value = float(session.get("promo_value") or 0)
    if promo_code:
        order_kwargs["promo_code"] = promo_code
        order_kwargs["promo_discount"] = promo_value
        db.update_daily_stats(promos_used=1)
        try:
            offer = _get_review_bonus_offer({
                "lot_id": order_kwargs.get("lot_id"),
                "sell_price": float(session.get("price") or order_kwargs.get("sell_price") or 0),
            })
            if offer.get("id"):
                db.increment_upsell(offer["id"], "times_used")
        except Exception:
            pass
    return order_kwargs


def _session_payment_message(order_id, service_name, quantity):
    return _session_initial_message(order_id, service_name, quantity)


def _session_confirmed_message(session, order=None):
    return _build_buyer_confirmed_message(session, order)


def _session_handle_review_bonus(order):
    return _process_review_bonus_once(order)


def _session_completion_text(session, order):
    return _session_completed_message(session, order, buyer_confirmed=_session_has_confirmed(session))


def _session_status_text(order):
    return _session_status_message(order)


def _session_order_started_text(session, order_id):
    return _session_started_payload(session, order_id)


def _session_prompt_text(session):
    return _session_payment_payload(session)


def _session_promo_applied_text(session):
    return _session_after_promo_link_prompt(session)


def _session_mark_review_sent(order_id):
    db.update_order(order_id, review_bonus_sent=1)


def _session_review_bonus_enabled():
    return _notification_enabled("review_bonus")


def _session_can_send(chat_id):
    return bool(chat_id)


def _session_send(chat_id, text):
    return _funpay_send_message(chat_id, text)


def _session_build_thanks(stars, order, session):
    if stars >= 5:
        promo = _create_review_bonus_promo(order, session)
        if promo:
            return _session_review_bonus_message(order, session or {}, promo)
        return _session_review_thanks_message()
    return _session_review_low_rating_message(stars)


def _session_message_for_review(stars, order, session):
    return _session_build_thanks(stars, order, session)


def _session_review_chat(details, session):
    chat_id = session.get("chat_id") if session else details.get("chat_id")
    if isinstance(chat_id, str) and chat_id.isdigit():
        chat_id = int(chat_id)
    if not chat_id and details.get("buyer_id"):
        try:
            chat_id = fp.get_chat_id_by_username(details["buyer_id"])
        except Exception:
            chat_id = 0
    if not chat_id and session and session.get("buyer_id"):
        try:
            chat_id = fp.get_chat_id_by_username(session["buyer_id"])
        except Exception:
            chat_id = 0
    if not chat_id and details.get("buyer_username"):
        chat = fp.get_chat_by_name(details["buyer_username"], make_request=True)
        chat_id = chat.id if chat else 0
    if not chat_id and session and session.get("buyer_username"):
        chat = fp.get_chat_by_name(session["buyer_username"], make_request=True)
        chat_id = chat.id if chat else 0
    return chat_id


def _session_store_review_result(order, sent):
    if sent:
        db.update_order(order["id"], review_bonus_sent=1)


def _session_confirmation_state_updates():
    return {"buyer_confirmed": 1, "support_ticket_due_at": "", "support_ticket_sent": 0}


def _session_completion_state_updates(completed_at, buyer_confirmed):
    updates = {"state": "completed"}
    if not buyer_confirmed:
        try:
            due_at = datetime.fromisoformat(str(completed_at)) + timedelta(days=1)
        except ValueError:
            due_at = datetime.now() + timedelta(days=1)
        updates["support_ticket_due_at"] = due_at.isoformat()
        updates["buyer_confirmed"] = 0
        updates["support_ticket_sent"] = 0
    else:
        updates["support_ticket_due_at"] = ""
        updates["buyer_confirmed"] = 1
        updates["support_ticket_sent"] = 0
    return updates


def _session_allow_review_message(order):
    return bool(order.get("funpay_order_id"))


def _session_should_try_review(details):
    return int(details.get("review_stars") or 0) > 0


def _session_order_payload_for_create(session, lot, result, quantity, cost_price, sell_price, profit, link=None, split_index=0, split_total=0):
    payload = dict(
        api_order_id=str(result["order_id"]),
        lot_id=lot["id"], lot_name=lot["name"],
        api_service_id=lot["api_service_id"], service_name=session.get("lot_name") or lot["api_service_name"],
        link=link if link is not None else session.get("pending_link"),
        quantity=quantity,
        cost_price=cost_price,
        sell_price=sell_price,
        profit=profit,
        status="processing",
        split_index=int(split_index or 0),
        split_total=int(split_total or 0),
        funpay_order_id=session["funpay_order_id"],
        buyer_username=session["buyer_username"],
    )
    return _session_apply_promo_to_order(session, payload)


def _session_clear_promo(funpay_order_id):
    db.update_funpay_session(funpay_order_id, promo_code="", promo_value=0)


def _session_reset_link(funpay_order_id):
    db.update_funpay_session(funpay_order_id, state="awaiting_link", pending_link="")


def _session_promo_status(session):
    code = session.get("promo_code") or ""
    if not code:
        return ""
    return f"\nПромокод: {code}"


def _session_prompt_after_confirm(session):
    return _session_started_payload(session, session.get("order_id") or "?")


def _session_build_completion(session, order):
    return _session_completed_payload(session, order)


def _session_build_review(details, order, session):
    stars = int(details.get("review_stars") or 0)
    if stars <= 0:
        return "", 0
    return _session_message_for_review(stars, order, session), stars


def _session_mark_confirmation(funpay_order_id):
    db.update_funpay_session(funpay_order_id, **_session_confirmation_state_updates())


def _session_mark_completed(funpay_order_id, completed_at, buyer_confirmed):
    db.update_funpay_session(funpay_order_id, **_session_completion_state_updates(completed_at, buyer_confirmed))


def _session_promo_prompt_text(quantity):
    return _build_buyer_promo_hint(quantity)


def _session_rebuild_payment_text(order_id, service_name, quantity):
    return _session_initial_message(order_id, service_name, quantity)


def _session_review_order(order):
    return order


def _session_resolve_review(order):
    return db.get_funpay_session(order["funpay_order_id"]) or db.get_funpay_session_by_order(order["id"])


def _session_review_send(order, session, details):
    text, stars = _session_build_review(details, order, session)
    if not text or stars <= 0:
        return False
    chat_id = _session_review_chat(details, session)
    if not _session_review_bonus_enabled():
        _session_mark_review_sent(order["id"])
        return True
    if not _session_can_send(chat_id):
        return False
    sent = _session_send(chat_id, text)
    _session_store_review_result(order, sent)
    return sent


def _session_review_processor(order):
    details = fp.get_order_details(order["funpay_order_id"])
    if not details.get("success") or not _session_should_try_review(details):
        return False
    session = _session_resolve_review(order)
    return _session_review_send(order, session, details)


def _session_has_promo(session):
    return bool(session.get("promo_code"))


def _session_quantity_text(session):
    return _session_current_quantity(session)


def _session_prompt_with_promo(session):
    return _session_rebuild_payment_text(
        session.get("funpay_order_id"),
        session.get("lot_name") or "Услуга",
        _session_quantity_text(session),
    )


def _session_payment_intro(session):
    return _session_prompt_with_promo(session)


def _session_is_reviewed(order):
    return bool(order.get("review_bonus_sent"))


def _session_update_review_state(order):
    return not _session_is_reviewed(order)


def _session_prompt_or_confirm(session):
    if session.get("pending_link"):
        return _build_review_prompt_message(session)
    return _session_payment_intro(session)


def _session_handle_possible_promo(session, text, chat_id):
    return _session_apply_promo_if_any(session, text, chat_id)


def _session_invalid_link_message():
    return _session_awaiting_link_invalid_message()


def _session_confirmation_prompt():
    return _session_awaiting_confirmation_message()


def _session_completed_body(session, order):
    text, buyer_confirmed = _session_build_completion(session, order)
    return text, buyer_confirmed


def _session_started_body(session, order_id):
    return _session_order_started_text(session, order_id)


def _session_info_body(order):
    return _session_status_text(order)


def _session_review_body(order):
    return _session_review_processor(order)


def _session_order_payload(session, lot, result, quantity, cost_price, sell_price, profit, link=None, split_index=0, split_total=0):
    return _session_order_payload_for_create(session, lot, result, quantity, cost_price, sell_price, profit, link=link, split_index=split_index, split_total=split_total)


def _session_cleanup_promo(session):
    if session.get("promo_code"):
        _session_clear_promo(session["funpay_order_id"])


def _session_refresh(funpay_order_id):
    return db.get_funpay_session(funpay_order_id)


def _session_mark_review(order_id):
    _session_mark_review_sent(order_id)


def _session_payment_request_text(order_id, service_name, quantity):
    return _session_payment_message(order_id, service_name, quantity)


def _session_confirmed_text(session, order=None):
    return _session_confirmed_message(session, order)


def _session_completed_text(session, order):
    return _session_completion_text(session, order)


def _session_started_text(session, order_id):
    return _session_order_started_text(session, order_id)


def _session_status_response(order):
    return _session_status_text(order)


def _session_review_process(order):
    return _session_handle_review_bonus(order)


def _session_review_query(order):
    return _session_allow_review_message(order)


def _session_buyer_confirmed(session):
    return _session_has_confirmed(session)


def _session_payment_prompt(session):
    return _session_prompt_or_confirm(session)


def _session_apply_promo_message(session, text, chat_id):
    return _session_handle_possible_promo(session, text, chat_id)


def _session_invalid_input_text():
    return _session_invalid_link_message()


def _session_confirm_input_text():
    return _session_confirmation_prompt()


def _session_mark_closed(funpay_order_id):
    _session_mark_confirmation(funpay_order_id)


def _session_mark_refunded(funpay_order_id):
    db.update_funpay_session(
        funpay_order_id,
        state="refunded",
        buyer_confirmed=1,
        support_ticket_due_at="",
        support_ticket_sent=1,
    )


def _session_mark_done(funpay_order_id, completed_at, buyer_confirmed):
    _session_mark_completed(funpay_order_id, completed_at, buyer_confirmed)


def _session_review_should_process(order):
    return _session_update_review_state(order)


def _session_promo_applied(session):
    return _session_has_promo(session)


def _session_payment_after_promo(session):
    return _session_prompt_with_promo(session)


def _session_remove_promo(session):
    _session_cleanup_promo(session)


def _session_db(funpay_order_id):
    return _session_refresh(funpay_order_id)


def _session_review_done(order_id):
    _session_mark_review(order_id)


def _session_text_completed(session, order):
    return _session_completed_text(session, order)


def _session_text_started(session, order_id):
    return _session_started_text(session, order_id)


def _session_text_status(order):
    return _session_status_response(order)


def _session_text_payment(order_id, service_name, quantity):
    return _session_payment_request_text(order_id, service_name, quantity)


def _session_text_confirmed(session, order=None):
    return _session_confirmed_text(session, order)


def _session_use_promo_payload(session, lot, result, quantity, cost_price, sell_price, profit, link=None, split_index=0, split_total=0):
    return _session_order_payload(session, lot, result, quantity, cost_price, sell_price, profit, link=link, split_index=split_index, split_total=split_total)


def _session_review_dispatch(order):
    return _session_review_process(order)


def _session_review_pending(order):
    return _session_review_should_process(order)


def _sync_review_state(order, details=None, msg_type=None):
    if not order:
        return False
    current_order = db.get_order(order["id"]) or order
    details = details or {}
    previous_stars = max(0, min(5, int(current_order.get("last_review_stars") or 0)))
    stars = max(0, min(5, int((details or {}).get("review_stars") or 0)))
    changed = stars != previous_stars

    if stars >= 5:
        if changed or _session_review_pending(current_order):
            _session_review_dispatch(current_order)
        db.update_order(current_order["id"], last_review_stars=stars)
        return True

    if stars <= 0:
        _revoke_review_bonus(
            current_order,
            notify=changed or previous_stars > 0 or bool(current_order.get("review_bonus_sent")),
            deleted=(msg_type == MessageTypes.FEEDBACK_DELETED or previous_stars >= 5),
            details=details,
        )
        db.update_order(current_order["id"], last_review_stars=0)
        return True

    if previous_stars >= 5:
        _revoke_review_bonus(current_order, notify=True, stars=stars, details=details)
        db.update_order(current_order["id"], last_review_stars=stars)
        return True

    if changed or _session_review_pending(current_order):
        _session_review_dispatch(current_order)
    db.update_order(current_order["id"], last_review_stars=stars)
    return True


def _session_input_promo(session, text, chat_id):
    return _session_apply_promo_message(session, text, chat_id)


def _session_input_invalid():
    return _session_invalid_input_text()


def _session_input_confirm():
    return _session_confirm_input_text()


def _knowledge_entries():
    kb_cfg = cfg.get("knowledge_base", {}) if cfg else {}
    return kb_cfg.get("entries", []) if isinstance(kb_cfg, dict) else []


def _knowledge_greeting_text():
    kb_cfg = cfg.get("knowledge_base", {}) if cfg else {}
    return str((kb_cfg or {}).get("greeting_text") or "").strip()


def _save_knowledge_entries(entries):
    cleaned = []
    for entry in entries:
        title = str(entry.get("title") or "").strip()
        reply = str(entry.get("reply") or "").strip()
        triggers = []
        for trigger in entry.get("triggers", []) or []:
            trigger = str(trigger or "").strip()
            if trigger and trigger not in triggers:
                triggers.append(trigger)
        if title and reply and triggers:
            cleaned.append({"title": title, "triggers": triggers, "reply": reply})
    cfg.set("knowledge_base.entries", cleaned)


def _render_knowledge_base_text():
    entries = _knowledge_entries()
    lines = [
        "📚 <b>База знаний</b>",
        "",
        f"Статус: {'✅ включена' if _knowledge_enabled() else '❌ выключена'}",
        f"Ответов: <b>{len(entries)}</b>",
        "",
        "👋 Приветствие:",
        html.escape(_knowledge_greeting_text() or "не задано"),
    ]
    if entries:
        lines.extend(["", "Популярные ответы:"])
        for idx, entry in enumerate(entries[:12], start=1):
            triggers = ", ".join(entry.get("triggers", [])[:4])
            lines.append(f"{idx}. <b>{html.escape(str(entry.get('title') or 'Ответ'))}</b>")
            if triggers:
                lines.append(f"Триггеры: <code>{html.escape(triggers)}</code>")
            lines.append(f"Ответ: {html.escape(str(entry.get('reply') or '')[:120])}")
            lines.append("")
    else:
        lines.extend(["", "Пока нет ни одной записи."])
    lines.append("Чтобы покупатель получил все ответы списком, он может написать: База знаний")
    return "\n".join(lines).strip()


def _knowledge_enabled():
    kb_cfg = cfg.get("knowledge_base", {}) if cfg else {}
    return bool((kb_cfg or {}).get("enabled", False))


def _knowledge_reply_for_text(text: str):
    if not _knowledge_enabled():
        return ""
    normalized = _normalized_command_text(text)
    greeting_text = _knowledge_greeting_text()
    if greeting_text and normalized in {"привет", "здравствуйте", "добрый день", "hello", "hi"}:
        return greeting_text
    for entry in _knowledge_entries():
        triggers = [ _normalized_command_text(x) for x in entry.get("triggers", []) ]
        if normalized in triggers:
            return str(entry.get("reply") or "").strip()
    return ""


def _knowledge_index_text():
    if not _knowledge_enabled():
        return ""
    lines = ["📚 База знаний", ""]
    for entry in _knowledge_entries():
        title = str(entry.get("title") or "").strip()
        reply = str(entry.get("reply") or "").strip()
        if title and reply:
            lines.append(f"• {title}")
            lines.append(reply)
            lines.append("")
    return "\n".join(line for line in lines if line is not None).strip()


def _session_speed_target(session, text):
    urls = LINK_REGEX.findall(text or "")
    if urls:
        for url in urls:
            if "twiboost.com" in url:
                return {"url": url}
    match = re.search(r"(\d{3,6})", str(text or ""))
    if match:
        return {"service_id": int(match.group(1))}
    lot = _session_lot(session)
    if lot and int(lot.get("api_service_id") or 0) > 0:
        return {"service_id": int(lot.get("api_service_id") or 0), "service_name": lot.get("api_service_name") or ""}
    return {}


def _session_speed_text(result):
    lines = ["🚀 Скорость услуги TwiBoost", ""]
    if result.get("speed_label"):
        lines.append(f"⚡ Скорость: {result['speed_label']}")
    if result.get("last_order_text"):
        lines.append(f"🕒 Последний заказ: {result['last_order_text']}")
    recent = result.get("recent") or []
    if recent:
        lines.extend(["", "Недавние заказы:"])
        lines.extend([f"• {item['time']} — {item['count']} выполнений" for item in recent])
    if result.get("url"):
        lines.extend(["", f"🔗 {result['url']}"])
    return "\n".join(lines)


def _try_handle_knowledge_or_speed(session, msg):
    text = str(getattr(msg, "text", "") or "").strip()
    if not text:
        return False
    normalized = _normalized_command_text(text)
    if normalized in KB_WORDS:
        response = _knowledge_index_text()
        if response:
            _funpay_send_message(msg.chat_id, response)
            return True
    if normalized in SPEED_WORDS or normalized.startswith("скорость ") or normalized.startswith("speed "):
        target = _session_speed_target(session, text)
        if target.get("url"):
            result = api.get_service_speed_by_url(target["url"]) if api else {"success": False, "error": "API недоступно"}
        elif target.get("service_id"):
            result = api.get_service_speed(target["service_id"], target.get("service_name") or "") if api else {"success": False, "error": "API недоступно"}
        else:
            result = {
                "success": False,
                "error": "Напишите: скорость <ссылка TwiBoost> или просто скорость в заказе с привязанной услугой.",
            }
        if result.get("success"):
            _funpay_send_message(msg.chat_id, _session_speed_text(result))
        else:
            _funpay_send_message(msg.chat_id, f"⚠️ {result.get('error')}")
        return True
    response = _knowledge_reply_for_text(text)
    if response and str(session.get("state") or "") in {"awaiting_link", "completed", "order_created"}:
        _funpay_send_message(msg.chat_id, response)
        return True
    return False


def _session_state_label(state):
    return {
        "awaiting_link": "ожидает ссылку",
        "awaiting_confirmation": "ожидает подтверждение",
        "creating_order": "создаётся",
        "order_created": "в работе",
        "completed": "завершён",
    }.get(state, state or "неизвестно")


def _build_new_order_admin_message(*, funpay_order_id="", api_order_id="", buyer="", service_name="", quantity=0, sell_price=0, cost_price=0, profit=0, link="", state_label="Новый заказ"):
    lines = [
        f"🛒 <b>{html.escape(state_label)}</b>",
        "",
    ]
    if funpay_order_id:
        lines.append(f"🎮 FunPay: <b>#{html.escape(str(funpay_order_id))}</b>")
    if api_order_id:
        lines.append(f"🤖 TwiBoost: <b>#{html.escape(str(api_order_id))}</b>")
    if buyer:
        lines.append(f"👤 Покупатель: {html.escape(buyer)}")
    if service_name:
        lines.append(f"📦 Услуга: {html.escape(service_name)}")
    if quantity:
        lines.append(f"📊 Количество: {quantity}")
    lines.append(f"💵 Себестоимость TwiBoost: <b>{_format_money(cost_price)}</b>")
    lines.append(f"💰 Цена на FunPay: <b>{_format_money(sell_price)}</b>")
    lines.append(f"📈 Прибыль: <b>{_format_money(profit)}</b>")
    if link:
        lines.append(f"🔗 Ссылка: {html.escape(link[:120])}")
    return "\n".join(lines)


def _notify_admin_new_order(*, funpay_order_id="", api_order_id="", buyer="", service_name="", quantity=0, sell_price=0, cost_price=0, profit=0, link="", state_label="Новый заказ"):
    if not _notification_enabled("new_order"):
        return
    text = _build_new_order_admin_message(
        funpay_order_id=funpay_order_id,
        api_order_id=api_order_id,
        buyer=buyer,
        service_name=service_name,
        quantity=quantity,
        sell_price=sell_price,
        cost_price=cost_price,
        profit=profit,
        link=link,
        state_label=state_label,
    )
    for uid in cfg.admin_ids:
        try:
            bot.send_message(uid, text, parse_mode="HTML")
        except Exception:
            pass


def _notify_admin_buyer_message(session, message_text):
    if not _notification_enabled("buyer_message"):
        return
    order = db.get_order(session["order_id"]) if session.get("order_id") else None
    service_name = session.get("lot_name") or ""
    if order:
        service_name = order.get("service_name") or order.get("lot_name") or service_name
    text = (
        "💬 <b>Новое сообщение от покупателя</b>\n\n"
        f"🎮 FunPay: <b>#{html.escape(session.get('funpay_order_id') or '—')}</b>\n"
        f"🧾 Заказ: <b>#{html.escape(str(session.get('order_id') or '—'))}</b>\n"
        f"👤 Покупатель: {html.escape(session.get('buyer_username') or 'неизвестно')}\n"
        f"📦 Услуга: {html.escape(service_name or 'не указана')}\n"
        f"📊 Статус: {_session_state_label(session.get('state'))}\n\n"
        f"📝 {html.escape((message_text or '').strip()[:600])}"
    )
    for uid in cfg.admin_ids:
        try:
            bot.send_message(uid, text, parse_mode="HTML")
        except Exception:
            pass


def _render_notifications_text():
    lines = [
        "🔔 <b>Уведомления</b>",
        "",
        "Настройте, что бот будет присылать в Telegram админам.",
        "",
    ]
    labels = [
        ("new_order", "Новый заказ"),
        ("buyer_message", "Сообщение покупателя"),
        ("order_completed", "Заказ завершён"),
        ("order_error", "Ошибка заказа"),
        ("low_balance", "Низкий баланс"),
        ("support_ticket", "Тикет через 24 часа"),
        ("review_bonus", "Бонус за 5★"),
        ("daily_report", "Ежедневный отчёт"),
    ]
    for key, label in labels:
        state = "✅" if _notification_enabled(key) else "❌"
        lines.append(f"{state} {label}")
    return "\n".join(lines)


def _order_has_ticket(order):
    return bool(order and (order.get("funpay_order_id") or db.get_funpay_session_by_order(order["id"])))


def _send_test_ticket_preview(chat_id, order_id):
    order = db.get_order(order_id)
    if not order:
        bot.send_message(chat_id, "❌ Заказ не найден.")
        return
    session = None
    if order.get("funpay_order_id"):
        session = db.get_funpay_session(order["funpay_order_id"])
    if not session:
        session = db.get_funpay_session_by_order(order_id)
    if not session:
        bot.send_message(chat_id, "⚠️ Для этого заказа нет активной FunPay-сессии.")
        return
    if not support_client or not support_client.is_enabled():
        bot.send_message(
            chat_id,
            "⚠️ Интеграция центра заявок FunPay выключена.\n\n"
            "Проверьте support_center.enabled и funpay_golden_key в config.json.",
        )
        return
    bot.send_message(chat_id, "⏳ Отправляю тестовый тикет в центр заявок FunPay...")
    try:
        result = support_client.create_unconfirmed_confirmation_ticket(session, order)
    except Exception as e:
        result = {"success": False, "error": str(e)}
    if result.get("success"):
        db.update_funpay_session(
            session["funpay_order_id"],
            support_ticket_sent=1,
            support_ticket_due_at="",
        )
        ticket_id = result.get("ticket_id") or "создан"
        text = (
            "✅ <b>Тестовый тикет отправлен</b>\n\n"
            f"🎮 FunPay заказ: <b>#{html.escape(session.get('funpay_order_id') or '—')}</b>\n"
            f"🎫 Номер заявки: <b>{html.escape(str(ticket_id))}</b>"
        )
        if result.get("ticket_url"):
            text += f"\n🔗 {html.escape(result['ticket_url'])}"
        bot.send_message(chat_id, text, parse_mode="HTML")
    else:
        bot.send_message(
            chat_id,
            "❌ <b>Тестовый тикет не отправлен</b>\n\n"
            f"Причина: {html.escape(str(result.get('error') or 'неизвестная ошибка'))}",
            parse_mode="HTML",
        )


def process_review_bonuses(limit=30):
    if not fp_client_ready():
        return
    for order in db.get_orders_for_review_bonus(limit=limit):
        details = fp.get_order_details(order["funpay_order_id"])
        if not details.get("success"):
            continue
        stars = int(details.get("review_stars") or 0)
        if stars <= 0:
            continue
        if stars < 5:
            db.update_order(order["id"], review_bonus_sent=1)
            continue
        session = db.get_funpay_session(order["funpay_order_id"]) or db.get_funpay_session_by_order(order["id"])
        chat_id = session.get("chat_id") if session else details.get("chat_id")
        sent = False
        if _notification_enabled("review_bonus") and chat_id:
            message = (
                "🎁 Спасибо за отзыв!\n\n"
                "⭐ Вы поставили 5 звёзд.\n"
                "🎉 Ваш бонус: +10% к количеству в следующем заказе.\n\n"
                "Напишите перед оплатой, и мы учтём бонус."
            )
            result = fp.send_message(chat_id, message)
            sent = bool(result.get("success"))
        if sent or not _notification_enabled("review_bonus"):
            db.update_order(order["id"], review_bonus_sent=1)


def process_review_bonuses(limit=30):
    if not fp_client_ready():
        return
    # Уменьшаем частоту сканирования, чтобы не создавать гонку с событиями FunPay
    pending_limit = max(50, limit * 2)
    for order in db.get_orders_for_review_bonus(limit=pending_limit):
        try:
            # _process_review_bonus_once уже содержит защиту от дублей
            _process_review_bonus_once(order)
        except Exception as e:
            logger.warning("Pending review dispatch failed for order #%s: %s", order.get("funpay_order_id"), e)

    # Синхронизация оставшихся отзывов
    review_scan_limit = max(100, limit * 3)
    for order in db.get_orders_for_review_sync(limit=review_scan_limit):
        details = fp.get_order_details(order["funpay_order_id"])
        if not details.get("success"):
            continue
        _sync_review_state(order, details=details)


def _fp_status_text(status):
    if hasattr(status, "name"):
        return status.name.lower()
    text = str(status).strip().lower()
    return {
        "0": "paid",
        "1": "closed",
        "2": "refunded",
    }.get(text, text)


def _normalize_fp_text(value):
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _funpay_message_order_id(msg):
    text = str(getattr(msg, "text", "") or "")
    match = FUNPAY_ORDER_ID_RE.search(text)
    return match.group(1) if match else None


def _handle_funpay_review_event(order_id, msg_type=None):
    if not order_id or not fp_client_ready():
        return False
    order = db.get_order_by_funpay_order_id(order_id)
    if not order:
        return False

    details = fp.get_order_details(order_id)
    if not details.get("success"):
        if msg_type == MessageTypes.FEEDBACK_DELETED:
            _sync_review_state(order, details={"review_stars": 0}, msg_type=msg_type)
            return True
        return False

    return _sync_review_state(order, details=details, msg_type=msg_type)


def _handle_funpay_confirmation_event(order_id):
    if not order_id or not fp_client_ready():
        return False
    session = db.get_funpay_session(order_id)
    if not session:
        return False

    linked_order = db.get_order_by_funpay_order_id(order_id)
    was_confirmed = _session_buyer_confirmed(session)
    _session_mark_closed(order_id)
    session = db.get_funpay_session(order_id) or session
    if not was_confirmed and session.get("chat_id"):
        _funpay_send_message(session["chat_id"], _session_text_confirmed(session, linked_order))
    if linked_order and _session_review_should_process(linked_order):
        _session_review_dispatch(linked_order)
    return True


def _handle_funpay_system_message(msg):
    msg_type = getattr(msg, "type", None)
    if msg_type not in FUNPAY_REVIEW_EVENT_TYPES and msg_type not in FUNPAY_CONFIRM_EVENT_TYPES:
        return False

    order_id = _funpay_message_order_id(msg)
    if not order_id:
        return False

    if msg_type in FUNPAY_CONFIRM_EVENT_TYPES:
        return _handle_funpay_confirmation_event(order_id)
    if msg_type in FUNPAY_REVIEW_EVENT_TYPES:
        return _handle_funpay_review_event(order_id, msg_type=msg_type)
    return False


def _resolve_funpay_chat_id(order_obj, order_details=None):
    order_details = order_details or {}
    chat_id = order_details.get("chat_id") or getattr(order_obj, "chat_id", None)
    if isinstance(chat_id, int):
        return chat_id
    if isinstance(chat_id, str) and chat_id.isdigit():
        return int(chat_id)

    buyer_id = order_details.get("buyer_id") or getattr(order_obj, "buyer_id", None)
    buyer_username = order_details.get("buyer_username") or getattr(order_obj, "buyer_username", None)
    resolved = 0
    if fp_client_ready() and buyer_id:
        resolved = fp.get_chat_id_by_username(buyer_id)
    if not resolved and fp_client_ready() and buyer_username:
        resolved = fp.get_chat_id_by_username(buyer_username)
    return resolved or chat_id


def _match_funpay_bound_lot(description="", price=0, offer_id=None, amount=None, short_description=""):
    # 🔥 СТРОГАЯ ПРИВЯЗКА ПО ID ЛОТА FUNPAY (исключает запуск другой услуги)
    if offer_id:
        lot = _find_lot_by_funpay_lot_id(str(offer_id))
        if lot:
            logger.info("✅ Matched FunPay lot by exact offer_id: %s -> Bot Lot #%s", offer_id, lot["id"])
            return lot

    # Если точного совпадения нет, идём по описанию/цене (старая логика)
    description_norm = _normalize_fp_text(description)
    short_description_norm = _normalize_fp_text(short_description)
    amount = int(amount or 0) if str(amount or "").isdigit() else amount
    try:
        price = float(price or 0)
    except (TypeError, ValueError):
        price = 0.0

    candidates = []
    for lot in db.get_lots(active_only=True):
        if not lot.get("funpay_lot_id"):
            continue
        score = 0
        price_diff = 10**9
        lot_funpay_id = str(lot.get("funpay_lot_id") or "")
        if offer_id and lot_funpay_id == str(offer_id):
            score += 1000

        lot_names = [
            _normalize_fp_text(lot.get("funpay_lot_name")),
            _normalize_fp_text(lot.get("name")),
            _normalize_fp_text(lot.get("api_service_name")),
        ]
        lot_names = [name for name in lot_names if name]

        for name in lot_names:
            if short_description_norm and (short_description_norm in name or name in short_description_norm):
                score += 200
            if description_norm and (name in description_norm or description_norm in name):
                score += 120

        amount_value = amount if isinstance(amount, int) and amount > 0 else None
        candidate_prices = []
        for value in (lot.get("price_per_unit"), lot.get("price"), lot.get("price_input")):
            try:
                candidate = float(value or 0)
            except (TypeError, ValueError):
                continue
            if candidate <= 0:
                continue
            candidate_prices.append(candidate * amount_value if amount_value else candidate)
        if candidate_prices:
            price_diff = min(abs(candidate - price) for candidate in candidate_prices)
            if price_diff <= 0.001:
                score += 120
            elif price and price_diff <= max(0.05, price * 0.15):
                score += 60
            elif price and price_diff <= max(0.15, price * 0.35):
                score += 20

        if score > 0:
            candidates.append((score, -price_diff, lot.get("id", 0), lot))

    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][3]
    return None


def _estimate_funpay_quantity(lot, price_fp):
    quantity_per_order = lot.get("quantity_per_order", 1) or 1
    lot_price_per_order = (lot.get("price_per_unit", 0) or 0) * quantity_per_order
    if lot_price_per_order > 0:
        quantity = int((float(price_fp or 0) / lot_price_per_order) * quantity_per_order)
    else:
        quantity = quantity_per_order
    quantity = max(quantity, lot.get("min_quantity", quantity))
    quantity = min(quantity, lot.get("max_quantity", quantity))
    return quantity


def _safe_int(value):
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None
    
def _normalize_twiboost_status(raw_status: str) -> str:
    if not raw_status:
        return "in_progress"
    s = raw_status.lower().strip()
    if "complet" in s:
        return "completed"
    if "partial" in s:
        return "partial"
    if "cancel" in s or "fail" in s or "error" in s:
        return "failed"
    if "progress" in s or "await" in s or "pending" in s or "processing" in s:
        return "in_progress"
    return "in_progress"  # по умолчанию считаем в работе


def _extract_requested_quantity(order_details=None, order_obj=None, lot=None):
    order_details = order_details or {}
    quantity = _safe_int(order_details.get("amount"))
    if quantity is None and order_obj is not None:
        quantity = _safe_int(getattr(order_obj, "amount", None))
    if quantity is None:
        text_pool = " ".join(
            str(x or "") for x in (
                order_details.get("description"),
                order_details.get("short_description"),
                getattr(order_obj, "description", None) if order_obj else None,
            )
        )
        match = re.search(r"(\d+)\s*(шт|штук|pcs|pc)?", text_pool.lower())
        if match:
            quantity = _safe_int(match.group(1))
    if quantity is None and lot is not None:
        quantity = _estimate_funpay_quantity(lot, order_details.get("price") or getattr(order_obj, "price", 0))
    return quantity or 0


def _resolve_funpay_service_name(order_details=None, order_obj=None, lot=None):
    order_details = order_details or {}
    for value in (
        order_details.get("short_description"),
        getattr(order_obj, "subcategory_name", None) if order_obj else None,
        lot.get("funpay_lot_name") if lot else None,
        lot.get("name") if lot else None,
        order_details.get("description"),
    ):
        if value:
            return str(value).strip()
    return "Услуга"


def _convert_api_charge_to_rub(charge, currency):
    try:
        amount = float(charge or 0)
    except (TypeError, ValueError):
        return 0.0
    code = str(currency or "RUB").upper()
    if code == "USD":
        return amount * _usd_rub_rate()
    if code == "EUR":
        return amount * (_usd_rub_rate() * 1.08)
    return amount


def _update_order_finances_from_api(order, upd):
    # Берём валюту и фактическое списание (charge) из ответа API
    currency = upd.get("currency") or order.get("currency") or "RUB"
    charge_value = upd.get("api_charge", order.get("api_charge", 0))
    charge_rub = _convert_api_charge_to_rub(charge_value, currency)

    # 🔥 Если API вернул реальное списание > 0 — берём его как себестоимость
    if charge_rub > 0:
        upd["cost_price"] = round(charge_rub, 2)
    else:
        # Фолбэк: считаем по тарифу лота × количество, если charge не пришёл
        lot = db.get_lot(order.get("lot_id")) if order.get("lot_id") else None
        if lot:
            estimated = _lot_cost_per_unit(lot) * int(order.get("quantity", 0))
            upd["cost_price"] = round(estimated, 2)
        else:
            upd["cost_price"] = order.get("cost_price", 0)

    # Прибыль = цена продажи - фактическая себестоимость
    upd["profit"] = round((order.get("sell_price") or 0) - upd["cost_price"], 2)
    return upd


def _create_api_order_for_lot(lot, link, quantity):
    if not api:
        return {"success": False, "error": "API недоступно"}
    service_type = str(lot.get("service_type") or "").lower()
    if not service_type and lot.get("api_service_id"):
        service = db.get_service("twiboost", lot["api_service_id"])
        service_type = str((service or {}).get("type") or "").lower()
    if service_type == "vote":
        answer_number = str(lot.get("vote_answer_number") or "").strip()
        if not answer_number:
            return {"success": False, "error": "Для vote-услуги не настроен номер варианта голосования"}
        return api.create_vote_order(
            lot["api_service_id"],
            link,
            quantity,
            option_field="answer_number",
            option_value=answer_number,
        )
    return api.create_order(lot["api_service_id"], link, quantity)


def _lot_order_mode(lot):
    if not lot:
        return "normal"
    mode = str(lot.get("order_mode") or "").strip().lower()
    if mode in {"normal", "vote", "reaction", "comments"}:
        return mode
    service_type = str(lot.get("service_type") or "").strip().lower()
    if service_type == "vote":
        return "vote"
    return "normal"


def _lot_order_mode_title(mode_or_lot):
    mode = mode_or_lot if isinstance(mode_or_lot, str) else _lot_order_mode(mode_or_lot)
    return {
        "vote": "Голоса",
        "reaction": "Реакции",
        "comments": "Комментарии",
    }.get(mode, "Обычный")


def _lot_split_enabled(lot):
    return bool((lot or {}).get("split_enabled"))


def _session_split_plan(session):
    raw = str((session or {}).get("pending_split_json") or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    result = []
    for item in data:
        if not isinstance(item, dict):
            continue
        link = str(item.get("link") or "").strip()
        try:
            quantity = int(item.get("quantity") or 0)
        except (TypeError, ValueError):
            quantity = 0
        if link and quantity > 0:
            result.append({"link": link, "quantity": quantity})
    return result


def _session_split_summary_lines(session):
    plan = _session_split_plan(session)
    return [f"{idx}. {item['link']} — {item['quantity']}" for idx, item in enumerate(plan, start=1)]


def _session_split_reactions(session):
    raw = str((session or {}).get("pending_reaction") or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    result = []
    for item in data:
        value = _normalize_reaction_value(item)
        if value:
            result.append(value)
    return result


def _session_related_orders(session):
    if not db or not session:
        return []
    orders = db.get_orders_by_funpay_order_id(session.get("funpay_order_id") or "")
    return orders or ([db.get_order(session["order_id"])] if session.get("order_id") and db.get_order(session["order_id"]) else [])


def _session_order_by_index(session, index):
    orders = _session_related_orders(session)
    if not orders:
        return None
    if index <= 0 or index > len(orders):
        return None
    return orders[index - 1]


def _parse_status_index(text):
    match = STATUS_INDEX_RE.match(str(text or "").strip().lower())
    return int(match.group(1)) if match else 0


def _parse_refill_index(text):
    match = REFILL_INDEX_RE.match(str(text or "").strip().lower())
    return int(match.group(1)) if match else 0


def _reaction_candidates_for_lot(lot):
    if not db:
        return []
    result = []
    seen = set()
    if _lot_order_mode(lot) == "reaction":
        for service_id in TELEGRAM_REACTION_SERVICE_IDS:
            svc = db.get_service("twiboost", service_id)
            if not svc:
                svc = _get_twiboost_service_by_id(service_id)
            if not svc:
                continue
            name = str(svc.get("name") or "")
            if "реакц" not in name.lower() and "reaction" not in name.lower():
                continue
            if int(svc.get("service_id") or 0) in seen:
                continue
            seen.add(int(svc.get("service_id") or 0))
            result.append(svc)
        if result:
            return result

    services = db.get_services(provider="twiboost", category=lot.get("category")) if db and lot and lot.get("category") else db.get_services(provider="twiboost", limit=5000) if db else []
    result = []
    for svc in services:
        name = str(svc.get("name") or "")
        category = str(svc.get("category") or "").lower()
        if "реакц" in category or "reaction" in category or "реакц" in name.lower() or "reaction" in name.lower():
            result.append(svc)
    return result


def _normalize_reaction_value(value):
    text = str(value or "").strip()
    if not text:
        return ""
    replacements = {
        "❤": "❤️",
        "♥": "❤️",
        "👍🏻": "👍",
        "👍🏼": "👍",
        "👍🏽": "👍",
        "👍🏾": "👍",
        "👍🏿": "👍",
        "👎🏻": "👎",
        "👎🏼": "👎",
        "👎🏽": "👎",
        "👎🏾": "👎",
        "👎🏿": "👎",
    }
    return replacements.get(text, text)


def _reaction_from_service_name(name):
    value = str(name or "")
    bracket_match = re.findall(r"\[([^\]]+)\]", value)
    for item in bracket_match:
        item = _normalize_reaction_value(item.strip())
        if 0 < len(item) <= 8:
            return item
    for token in value.split():
        token = _normalize_reaction_value(token.strip())
        if 0 < len(token) <= 8 and not re.search(r"[A-Za-zА-Яа-я0-9]", token):
            return token
    return ""


def _reaction_options_for_lot(lot):
    options = []
    seen = set()
    for svc in _reaction_candidates_for_lot(lot):
        reaction = _reaction_from_service_name(svc.get("name") or "")
        if reaction and reaction not in seen:
            seen.add(reaction)
            options.append(reaction)
    return options


def _reaction_options_text(lot, limit=None):
    options = _reaction_options_for_lot(lot)
    if limit:
        options = options[:limit]
    if not options:
        return ""
    lines = []
    chunk = []
    for item in options:
        chunk.append(item)
        if len(chunk) >= 12:
            lines.append(" ".join(chunk))
            chunk = []
    if chunk:
        lines.append(" ".join(chunk))
    return "\n".join(lines)


def _find_reaction_service_for_lot(lot, reaction_value):
    reaction = _normalize_reaction_value(reaction_value)
    if not reaction:
        return None
    candidates = _reaction_candidates_for_lot(lot)
    exact = []
    fuzzy = []
    for svc in candidates:
        name = str(svc.get("name") or "")
        service_reaction = _reaction_from_service_name(name)
        if reaction == _normalize_reaction_value(service_reaction) or reaction in name:
            exact.append(svc)
        elif any(ch in name for ch in reaction if ch.strip()):
            fuzzy.append(svc)
    if exact:
        exact.sort(key=lambda x: int(x.get("service_id") or 0))
        return exact[0]
    if fuzzy:
        fuzzy.sort(key=lambda x: int(x.get("service_id") or 0))
        return fuzzy[0]
    return None


def _extract_reaction_value(text):
    text = re.sub(r"^\s*\d+\s*[\.\)\-:]\s*", "", str(text or "").strip())
    value = _normalize_reaction_value(text)
    if not value:
        return ""
    if len(value) > 16:
        return ""
    return value


def _extract_comment_lines(text):
    lines = [line.strip() for line in str(text or "").splitlines()]
    return [line for line in lines if line]


def _extract_hash_comment_lines(text):
    raw_lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    comments = []
    invalid = []
    for line in raw_lines:
        if not line.startswith("#"):
            invalid.append(line)
            continue
        comment = line[1:].strip()
        if not comment:
            invalid.append(line)
            continue
        comments.append(comment)
    return comments, invalid


def _normalized_command_text(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _is_yes_text(value):
    return _normalized_command_text(value) in YES_WORDS


def _is_no_text(value):
    return _normalized_command_text(value) in NO_WORDS


def _is_refuse_bonus_text(value):
    return _normalized_command_text(value) in REFUSE_BONUS_WORDS


def _validate_comment_lines(lines, expected_count):
    if not lines:
        return "не найдено ни одного комментария"
    if len(lines) != expected_count:
        return f"ожидается строк: {expected_count}, получено: {len(lines)}"
    for idx, line in enumerate(lines, start=1):
        if len(line) > 500:
            return f"комментарий #{idx} слишком длинный (максимум 500 символов)"
    return ""


def _parse_split_lines(text, expected_parts):
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if len(lines) != expected_parts:
        return None, f"ожидается строк: {expected_parts}, получено: {len(lines)}"
    items = []
    for idx, line in enumerate(lines, start=1):
        urls = LINK_REGEX.findall(line)
        if not urls:
            return None, f"в строке {idx} не найдена ссылка"
        link = urls[0]
        remainder = line.replace(link, " ", 1).strip()
        match = re.search(r"(\d+)", remainder)
        if not match:
            return None, f"в строке {idx} не найдено количество"
        quantity = int(match.group(1))
        if quantity <= 0:
            return None, f"в строке {idx} количество должно быть больше нуля"
        items.append({"link": link, "quantity": quantity})
    return items, ""


def _session_lot(session):
    if not session or not session.get("lot_id"):
        return None
    return db.get_lot(session["lot_id"])


def _session_order_mode(session, lot=None):
    return _lot_order_mode(lot or _session_lot(session))


def _session_payment_message_rich(order_id, service_name, quantity, order_mode="normal", promo_code="", split_enabled=False):
    lines = [
        "💸 Заказ оплачен",
        "",
        f"🧾 ID заказа: #{order_id}",
        f"📦 Услуга: {service_name}",
        f"📊 Количество: {quantity}",
    ]
    if promo_code:
        lines.append(f"🎫 Промокод: {promo_code}")
    lines.extend([
        "",
        "Если у вас есть промокод, отправьте его отдельным сообщением.",
        "Если промокода нет, отправьте ссылку на пост или канал для запуска заказа.",
    ])
    if split_enabled:
        lines.extend([
            "",
            "➗ Если хотите разделить заказ на несколько ссылок, отправьте: Разделить",
        ])
    if order_mode == "vote":
        lines.extend([
            "",
            "После ссылки бот попросит указать номер варианта ответа.",
        ])
    elif order_mode == "reaction":
        lines.extend([
            "",
            "После ссылки бот попросит отправить нужную реакцию одним сообщением.",
            "Например: ❤️ или 💔",
        ])
    elif order_mode == "comments":
        lines.extend([
            "",
            "После ссылки бот попросит отправить комментарии: по одному на строку.",
        ])
    lines.extend([
        "",
        "Если хотите отменить заказ до запуска, отправьте команду: Отмена",
    ])
    return "\n".join(lines)


def _session_vote_prompt_message(session):
    lines = [
        "🗳 Укажите вариант ответа",
        "",
        f"🧾 ID заказа: #{session.get('funpay_order_id')}",
        f"📦 Услуга: {session.get('lot_name') or 'Услуга'}",
        f"📊 Количество: {_session_current_quantity(session)}",
        f"🔗 Ссылка: {session.get('pending_link') or '—'}",
        "",
        "Отправьте номер варианта, за который нужно голосовать.",
        "Например: 1",
    ]
    if session.get("promo_code"):
        lines.insert(5, f"🎫 Промокод: {session.get('promo_code')}")
    return "\n".join(lines)


def _session_vote_prompt_invalid_message():
    return (
        "❌ Неверный вариант ответа.\n\n"
        "Отправьте только номер варианта, например: 1"
    )


def _session_reaction_prompt_message(session):
    split_plan = _session_split_plan(session)
    split_reactions = _session_split_reactions(session)
    lines = [
        "😍 Укажите реакцию для запуска заказа",
        "",
        f"🧾 ID заказа: #{session.get('funpay_order_id')}",
        f"📦 Услуга: {session.get('lot_name') or 'Услуга'}",
        f"📊 Количество: {_session_current_quantity(session)}",
        f"🔗 Ссылка: {session.get('pending_link') or '—'}",
    ]
    if split_plan:
        next_index = min(len(split_reactions) + 1, len(split_plan))
        lines.extend([
            "",
            f"➗ Частей: {len(split_plan)}",
        ])
        lines.extend([
            f"{idx}. {item['quantity']} -> {item['link']}" + (f" — {split_reactions[idx-1]}" if len(split_reactions) >= idx else "")
            for idx, item in enumerate(split_plan, start=1)
        ])
        lines.extend([
            "",
            f"Сейчас ждём реакцию для части #{next_index}.",
            "Отправляйте реакции по одной на строку или по одной в сообщении.",
            f"Сейчас получено: {len(split_reactions)} из {len(split_plan)}",
            "Порядок должен совпадать с порядком частей.",
            "Пример:",
            "❤️",
            "или",
            "1. ❤️",
            "2. 💔",
            "Если хотите посмотреть доступные варианты, отправьте: список",
        ])
    else:
        lines.extend([
            "",
            "Отправьте одну реакцию одним сообщением.",
            "Если хотите посмотреть доступные варианты, отправьте: список",
        ])
    lines.append("Например: ❤️, 💔, 🔥, 👍")
    return "\n".join(lines)


def _session_reaction_list_message(session=None):
    lot = _session_lot(session) if session else None
    options_text = _reaction_options_text(lot) if lot else ""
    text = "📋 Доступные реакции для этого лота\n\n"
    if options_text:
        text += options_text
    else:
        text += "Список пока не найден. Попробуйте отправить одну реакцию одним сообщением, например: ❤️"
    return text


def _session_reaction_prompt_invalid_message(session=None, reaction_value=None):
    lot = _session_lot(session) if session else None
    text = "❌ Реакция не поддерживается для этого лота.\n\n"
    if reaction_value:
        text += f"Вы отправили: {reaction_value}\n\n"
    text += "Отправьте одну из доступных реакций.\n"
    text += "Чтобы посмотреть варианты, отправьте: список"
    options_text = _reaction_options_text(lot, limit=24) if lot else ""
    if options_text:
        text += "\n\n" + options_text
    else:
        text += "\n\nНапример: ❤️"
    return text


def _session_comments_prompt_message(session):
    qty = int(_session_current_quantity(session) or 0)
    current = len(_extract_comment_lines(session.get("pending_comments") or ""))
    remaining = max(0, qty - current)
    return (
        "💬 Отправьте комментарии для запуска заказа\n\n"
        f"🧾 ID заказа: #{session.get('funpay_order_id')}\n"
        f"📦 Услуга: {session.get('lot_name') or 'Услуга'}\n"
        f"📊 Нужно комментариев: {qty}\n"
        f"✅ Уже получено: {current}\n"
        f"🕒 Осталось: {remaining}\n"
        f"🔗 Ссылка: {session.get('pending_link') or '—'}\n\n"
        "Отправляйте комментарии только с символа # в начале.\n"
        "Пример:\n"
        "#Привет\n"
        "#Отличный пост\n\n"
        "Можно отправить сразу несколько строк одним сообщением или присылать по одному комментарию за раз."
    )


def _session_comments_prompt_invalid_message(expected, actual):
    return (
        "❌ Количество комментариев не совпадает.\n\n"
        f"Ожидается строк: {expected}\n"
        f"Получено строк: {actual}\n\n"
        "Отправьте комментарии ещё раз, каждый с символа # в начале."
    )


def _session_comments_prompt_error_message(reason):
    return (
        "❌ Комментарии не прошли проверку.\n\n"
        f"Причина: {reason}\n\n"
        "Формат комментария: #Ваш текст"
    )


def _session_comments_progress_message(current, expected):
    remaining = max(0, expected - current)
    return (
        "💬 Комментарий сохранён.\n\n"
        f"✅ Уже получено: {current} из {expected}\n"
        f"🕒 Осталось: {remaining}\n\n"
        "Отправьте следующий комментарий в формате:\n"
        "#Ваш комментарий"
    )


def _session_reaction_progress_message(current, expected):
    remaining = max(0, expected - current)
    next_index = min(current + 1, expected)
    return (
        "😍 Реакция сохранена.\n\n"
        f"✅ Уже получено: {current} из {expected}\n"
        f"🕒 Осталось: {remaining}\n\n"
        f"Сейчас ждём реакцию для части #{next_index}.\n\n"
        "Отправьте следующую реакцию одним сообщением\n"
        "или сразу несколько реакций по одной на строку."
    )


def _session_split_count_prompt(session, lot):
    base_qty = int(_session_current_quantity(session) or 0)
    multiplier = int((lot or {}).get("quantity_per_order") or 1)
    quantity = base_qty * multiplier  # 🔥 Используем итоговое количество для API
    min_qty = int((lot or {}).get("min_quantity") or 0)
    max_parts = min(5, max(2, quantity // max(min_qty, 1))) if min_qty > 0 else 5
    max_parts = max(2, max_parts)
    return (
        "➗ Разделение заказа\n\n"
        f"🧾 ID заказа: #{session.get('funpay_order_id')}\n"
        f"📊 Общее количество: {quantity}\n"
        f"📉 Минимум на одну часть: {min_qty}\n\n"
        f"Введите количество частей от 2 до {max_parts}."
    )


def _session_split_lines_prompt(session, parts_count, lot):
    base_qty = int(_session_current_quantity(session) or 0)
    multiplier = int((lot or {}).get("quantity_per_order") or 1)
    quantity = base_qty * multiplier  # 🔥 Итоговое количество для API
    min_qty = int((lot or {}).get("min_quantity") or 0)
    max_qty = int((lot or {}).get("max_quantity") or 0)
    return (
        "🔗 Отправьте ссылки и количество для каждой части\n\n"
        f"Нужно частей: {parts_count}\n"
        f"Общее количество: {quantity}\n"
        f"Минимум на часть: {min_qty}\n"
        f"Максимум на часть: {max_qty}\n\n"
        "Формат: ссылка пробел количество\n"
        "Одна часть = одна строка.\n\n"
        "Пример:\n"
        "https://t.me/channel1/123 500\n"
        "https://t.me/channel2/321 500"
    )


def _session_split_invalid_message(reason):
    return f"❌ Не удалось принять разделение.\n\nПричина: {reason}"


def _session_confirmation_message_rich(session):
    order_mode = _session_order_mode(session)
    split_plan = _session_split_plan(session)
    lines = [
        "✅ Проверьте данные перед запуском",
        "",
        f"🧾 ID заказа: #{session.get('funpay_order_id')}",
        f"📦 Услуга: {session.get('lot_name') or 'Услуга'}",
        f"📊 Количество: {_session_current_quantity(session)}",
        f"🔗 Ссылка: {session.get('pending_link') or '—'}",
    ]
    if session.get("promo_code"):
        lines.append(f"🎫 Промокод: {session.get('promo_code')}")
    if split_plan:
        lines.append(f"➗ Частей: {len(split_plan)}")
        lines.extend([f"   {idx}. {item['quantity']} -> {item['link']}" for idx, item in enumerate(split_plan, start=1)])
    if order_mode == "vote":
        lines.append(f"🗳 Вариант ответа: {session.get('pending_answer_number') or '—'}")
        lines.extend([
            "",
            "⚠️ Если вариант ответа выбран неверно, возврат средств невозможен.",
        ])
    elif order_mode == "reaction":
        split_reactions = _session_split_reactions(session)
        if split_plan and split_reactions:
            lines.append("😍 Реакции по частям:")
            lines.extend([f"   {idx}. {value}" for idx, value in enumerate(split_reactions, start=1)])
        else:
            lines.append(f"😍 Реакция: {session.get('pending_reaction') or '—'}")
        lines.extend([
            "",
            "⚠️ Если реакция выбрана неверно, возврат средств невозможен.",
        ])
    elif order_mode == "comments":
        comments = _extract_comment_lines(session.get("pending_comments") or "")
        lines.append(f"💬 Комментариев: {len(comments)}")
        preview = comments[:3]
        if preview:
            lines.append("📝 Превью:")
            lines.extend([f"• {item}" for item in preview])
            if len(comments) > len(preview):
                lines.append(f"… и ещё {len(comments) - len(preview)}")
        lines.extend([
            "",
            "⚠️ Проверьте комментарии внимательно. После запуска возврат средств невозможен.",
        ])
    lines.extend([
        "",
        "Ответьте «Да», чтобы запустить заказ.",
        "Ответьте «Нет», чтобы изменить данные и отправить их заново.",
    ])
    return "\n".join(lines)


def _session_reset_buyer_inputs(funpay_order_id):
    db.update_funpay_session(
        funpay_order_id,
        state="awaiting_link",
        pending_link="",
        pending_answer_number="",
        pending_reaction="",
        pending_comments="",
        pending_split_parts=0,
        pending_split_json="",
    )


def _cancel_funpay_session_by_buyer(session):
    state = str(session.get("state") or "").strip().lower()
    if state not in {"awaiting_link", "awaiting_vote_answer", "awaiting_reaction", "awaiting_comments", "awaiting_confirmation", "awaiting_split_parts", "awaiting_split_lines"}:
        return False
    funpay_order_id = session.get("funpay_order_id")
    if not funpay_order_id:
        return False
    if not fp_client_ready():
        _funpay_send_message(session["chat_id"], "❌ Возврат сейчас недоступен. Попробуйте позже.")
        return True
    refund_result = fp.refund_order(funpay_order_id)
    if not refund_result.get("success"):
        _funpay_send_message(
            session["chat_id"],
            "❌ Не удалось отменить заказ автоматически.\n\n"
            f"Причина: {refund_result.get('error') or 'неизвестная ошибка'}",
        )
        return True
    _session_mark_refunded(funpay_order_id)
    _funpay_send_message(
        session["chat_id"],
        "↩️ Заказ отменён\n\n"
        "Средства возвращены через FunPay.\n"
        "Если захотите оформить заказ заново, просто напишите в чат снова.",
    )
    return True


def _create_api_order_for_lot(lot, link, quantity, answer_number=None, reaction_value=None, comments=None):
    provider = str(lot.get("api_provider") or "twiboost").lower()
    api_client = get_api_client(provider)
    if not api_client:
        return {"success": False, "error": f"Провайдер {provider} не настроен"}

    order_mode = _lot_order_mode(lot)
    if order_mode == "comments":
        comments_logger.info(
            f"🔄 Starting comments order | Service:{lot.get('api_service_id')} | "
            f"Link:{link[:50]} | Qty:{quantity} | Comments_lines:{len(comments) if comments else 0}"
        )
    if order_mode == "vote":
        answer_number = str(answer_number or lot.get("vote_answer_number") or "").strip()
        if not answer_number:
            return {"success": False, "error": "Не указан номер варианта ответа для услуги голосов."}
        return api_client.create_vote_order(
            lot["api_service_id"],
            link,
            quantity,
            option_field="answer_number",
            option_value=answer_number,
        )
    if order_mode == "reaction":
        reaction_service = _find_reaction_service_for_lot(lot, reaction_value)
        if not reaction_service:
            options = _reaction_options_for_lot(lot)
            options_text = f" Доступно: {' '.join(options[:20])}" if options else ""
            return {"success": False, "error": f"Не найдена услуга TwiBoost для реакции {reaction_value or '—'}.{options_text}"}
        return api_client.create_order(int(reaction_service["service_id"]), link, quantity)
    if order_mode == "comments":
        comment_lines = comments or []
        if not comment_lines:
            logger.error(f"Comments mode but no comments: lot_id={lot.get('id')}, link={link[:50]}")
            return {"success": False, "error": "Не указаны комментарии"}
    
    # 🔥 Просто передаём в API — веб-фолбэк уже есть в twiboost.py
        comments_text = "\n".join(comment_lines)
    
        return api_client.create_order(
            lot["api_service_id"],
            link,
            quantity,
            extra_params={
                "comments_text": comments_text,
                "comments": comment_lines,
            }
        )

# 🔥 Обычный режим — просто вызываем API
    else:
        return api_client.create_order(lot["api_service_id"], link, quantity)

def _create_review_bonus_order(session, order, lot, link):
    bonus_lot = _lot_review_bonus_payload(lot)
    if not bonus_lot:
        return {"success": False, "error": "Для этого лота не настроен бонус за отзыв."}
    if _lot_order_mode(bonus_lot) == "vote":
        return {"success": False, "error": "Бонус за отзыв пока поддерживает только обычные услуги."}

    quantity = _lot_review_bonus_quantity(lot)
    if quantity <= 0:
        return {"success": False, "error": "Не задано количество бонуса."}

    service = db.get_service("twiboost", bonus_lot["api_service_id"]) if db else None
    min_order = int((service or {}).get("min_order") or bonus_lot.get("min_quantity") or 0)
    max_order = int((service or {}).get("max_order") or bonus_lot.get("max_quantity") or 0)
    if min_order > 0 and quantity < min_order:
        return {"success": False, "error": f"Количество бонуса должно быть не меньше {min_order}."}
    if max_order > 0 and quantity > max_order:
        return {"success": False, "error": f"Количество бонуса должно быть не больше {max_order}."}

    result = _create_api_order_for_lot(bonus_lot, link, quantity)
    if not result.get("success"):
        return result

    api_currency = result.get("currency") or "RUB"
    api_charge = result.get("charge", 0)
    cost_price = round(_lot_cost_per_unit(bonus_lot) * quantity, 2)
    api_cost_price = round(_convert_api_charge_to_rub(api_charge, api_currency), 2)
    if api_cost_price > 0:
        cost_price = api_cost_price
    profit = round(-cost_price, 2)
    api_status = str(result.get("status") or "processing")
    status_map = {
        "awaiting": "pending",
        "in progress": "in_progress",
        "completed": "completed",
        "partial": "partial",
        "canceled": "cancelled",
        "fail": "failed",
        "processing": "processing",
    }
    status = status_map.get(api_status.lower(), "processing")
    service_name = bonus_lot.get("api_service_name") or bonus_lot.get("name") or "Бонус за отзыв"
    local_order_id = db.add_order(
        api_order_id=result["order_id"],
        lot_id=0,
        lot_name=f"{lot.get('name') or 'Лот'} [Бонус 5★]",
        api_provider=bonus_lot.get("api_provider") or "twiboost",
        api_service_id=bonus_lot["api_service_id"],
        service_name=f"{service_name} [Бонус 5★]",
        buyer_username=session.get("buyer_username") or order.get("buyer_username") or "",
        link=link,
        quantity=quantity,
        cost_price=cost_price,
        sell_price=0,
        profit=profit,
        status=status,
        api_status=api_status,
        api_charge=api_charge,
        api_start_count=_safe_int(result.get("start_count")) or 0,
        api_remains=_safe_int(result.get("remains")),
        currency=api_currency,
        error_message="",
        refill_count=0,
        promo_code="",
        promo_discount=0,
        funpay_order_id="",
    )
    return {
        "success": True,
        "order_id": local_order_id,
        "api_order_id": result["order_id"],
        "service_name": service_name,
        "quantity": quantity,
        "cost_price": cost_price,
        "profit": profit,
    }


def _build_bulk_order_update(order, api_data):
    status_map = {
        "awaiting": "pending",
        "in progress": "in_progress",
        "completed": "completed",
        "partial": "partial",
        "canceled": "cancelled",
        "fail": "failed",
        "processing": "processing",
    }
    new_status = status_map.get(str(api_data.get("status", "")).lower(), order.get("status"))
    upd = {
        "status": new_status,
        "api_status": api_data.get("status", order.get("api_status", "")),
        "api_charge": api_data.get("charge", order.get("api_charge", 0)),
        "api_start_count": api_data.get("start_count", order.get("api_start_count", 0)),
        "api_remains": api_data.get("remains", order.get("api_remains", 0)),
        "currency": api_data.get("currency", order.get("currency", "RUB")),
    }
    upd = _update_order_finances_from_api(order, upd)
    changed = (
        new_status != order.get("status")
        or str(upd["api_status"]) != str(order.get("api_status", ""))
        or float(upd["api_charge"]) != float(order.get("api_charge", 0) or 0)
        or int(upd["api_start_count"]) != int(order.get("api_start_count", 0) or 0)
        or int(upd["api_remains"]) != int(order.get("api_remains", 0) or 0)
        or str(upd["currency"]) != str(order.get("currency", "RUB"))
        or round(float(upd.get("cost_price", order.get("cost_price", 0)) or 0), 2)
        != round(float(order.get("cost_price", 0) or 0), 2)
        or round(float(upd.get("profit", order.get("profit", 0)) or 0), 2)
        != round(float(order.get("profit", 0) or 0), 2)
    )
    return new_status, upd, changed

def _notify_buyer_order_status(order, status_type, executed, total):
    fp_id = order.get("funpay_order_id")
    if not fp_id: return
    sess = db.get_funpay_session(fp_id) if db else None
    chat_id = sess.get("chat_id") if sess else None
    if not chat_id or not fp_client_ready(): return

    if status_type == "full_cancel":
        msg = (
            f"⚠️ Заказ #{order['id']} полностью отменён сервисом.\n\n"
            f"📦 Услуга: {order.get('service_name')}\n"
            f"📊 Заказано: {total}\n"
            f"❌ Выполнено: 0\n\n"
            f"💡 Напишите «Отмена» для возврата средств\n"
            f"💡 Напишите «Повторить» для создания заказа заново"
        )
    elif status_type == "partial":
        remaining = total - executed
        msg = (
            f"⚠️ Заказ #{order['id']} выполнен частично.\n\n"
            f"📦 Услуга: {order.get('service_name')}\n"
            f"✅ Выполнено: {executed}\n"
            f"❌ Осталось/Отменено: {remaining}\n\n"
            f"ℹ️ Возврат за выполненную часть невозможен.\n"
            f"💡 Напишите «Дозаказать», чтобы запустить остаток"
        )
    else: return

    _funpay_send_message(chat_id, msg)


def sync_active_orders_core(db_obj, api_obj, cfg_obj, bot_obj=None):
    active = db_obj.get_active_orders()
    if not active:
        return {"success": True, "checked": 0, "updated": 0, "active": 0}
     
    # 🔥 Группируем заказы по провайдерам
    prov_orders = {}
    for o in active:
        prov = str(o.get("api_provider") or "twiboost").lower()
        if o.get("api_order_id"):
            prov_orders.setdefault(prov, []).append(o["api_order_id"])

    if not prov_orders:
        return {"success": True, "checked": 0, "updated": 0, "active": len(active)}

    # 🔥 Проверяем каждый провайдер отдельно
    result = {"success": True, "orders": {}}
    for prov, ids in prov_orders.items():
        client = get_api_client(prov)
        if not client:
            continue
        # Пакетная проверка (работает у TwiBoost и SmmWay)
        if hasattr(client, "check_orders_status"):
            r = client.check_orders_status(ids)
            if r.get("success"):
                result["orders"].update(r.get("orders", {}))
        else:
            # Фолбэк: проверка по одному
            for oid in ids:
                sr = client.check_order_status(oid)
                if sr.get("success"):
                    result["orders"][oid] = sr

    updated = 0
    completed_ids = []

    # 🔥 2. Обрабатываем каждый заказ
    for order in active:
        aid = order.get("api_order_id")
        if not aid or aid not in result.get("orders", {}):
            continue

        api_data = result["orders"][aid]
        if api_data.get("status") == "Error" or not api_data:
            continue

        raw_status = str(api_data.get("status", ""))
        remains = _safe_int(api_data.get("remains"))
        start_count = _safe_int(api_data.get("start_count"))

        # 🔥 3. Надежное определение статуса
        new_status = _normalize_twiboost_status(raw_status)

        # 🔥 Фолбэк: если статус не clear, но осталось 0 и заказ стартовал → завершен
        if new_status not in ("completed", "partial") and remains == 0 and start_count > 0:
            new_status = "completed"

        # Пропускаем, если статус не изменился
        if new_status == order.get("status"):
            continue

        upd = {
            "status": new_status,
            "api_status": raw_status,
            "api_charge": api_data.get("charge", order.get("api_charge", 0)),
            "api_start_count": start_count,
            "api_remains": remains,
            "currency": api_data.get("currency", order.get("currency", "RUB")),
        }
        upd = _update_order_finances_from_api(order, upd)

        # 🔥 4. Обработка завершения
        if new_status == "completed" and order.get("status") != "completed":
            upd["completed_at"] = datetime.now().isoformat()
            db_obj.update_daily_stats(
                completed_orders=1,
                total_revenue=order["sell_price"],
                total_cost=upd.get("cost_price", order["cost_price"]),
                total_profit=upd.get("profit", order["profit"])
            )
            completed_ids.append(order["id"])

        elif new_status in ("failed", "cancelled", "partial") and order.get("status") not in ("failed", "cancelled", "partial"):
            db_obj.update_daily_stats(failed_orders=1)

            # 🔥 Рассчитываем факт выполнения на основе свежих данных от API
            executed = start_count  # уже получен выше из api_data
            total = int(order.get("quantity") or 0)
            status_type = "full_cancel" if executed == 0 else "partial"

            # ✅ Используем твою готовую функцию — она чище и правильнее
            _notify_buyer_order_status(order, status_type, executed, total)

            # Уведомление админу в Telegram
            if cfg_obj.get("notifications.order_error", True) and bot_obj:
                text = f"❌ Заказ #{order['id']} {status_type.replace('_', ' ')}\n📦 {order['service_name'][:40]}"
                for admin_uid in cfg_obj.admin_ids:
                    try: bot_obj.send_message(admin_uid, text, parse_mode="HTML")
                    except: pass

        db_obj.update_order(order["id"], **upd)
        db_obj.add_log("INFO", "checker", f"#{order['id']}: {order.get('status')} → {new_status}")
        updated += 1

    # 🔥 5. Отправляем уведомления о завершении ТОЛЬКО ОДИН РАЗ
    for order_id in completed_ids:
        notify_funpay_order_completed(order_id)

    return {"success": True, "checked": len(api_ids), "updated": updated, "active": len(active)}


def order_checker_loop_v2(db_obj, api_obj, cfg_obj, bot_obj, stop_event_obj, logger_obj):
    interval = 30
    while not stop_event_obj.is_set():
        try:
            active_count = len(db_obj.get_active_orders())
            logger_obj.debug(f"Checking orders... Active: {active_count}")
            sync_result = sync_active_orders_core(db_obj, api_obj, cfg_obj, bot_obj)
            
            if not sync_result.get("success"):
                logger_obj.error(f"Failed to check orders: {sync_result.get('error')}")
            elif sync_result.get("updated"):
                logger_obj.info(f"✅ Updated {sync_result['updated']} orders | Checked {sync_result['checked']}")
                
        except Exception as e:
            logger_obj.error(f"Order checker error: {e}", exc_info=True)
            db_obj.add_log("ERROR", "checker", str(e))
            
        stop_event_obj.wait(interval)
        
        
def _notify_buyer_order_failed(db_obj, order, reason=""):
    funpay_id = order.get("funpay_order_id")
    if not funpay_id:
        return
    sess = db_obj.get_funpay_session(funpay_id)
    if not sess or not sess.get("chat_id"):
        return

    msg = f"⚠️ Заказ #{order.get('id')} был отменён или не выполнен.\n\n"
    msg += f"📦 Услуга: {order.get('service_name')}\n"
    if reason:
        msg += f"⚙️ Причина: {reason}\n\n"
    msg += "Пожалуйста, попробуйте оформить заказ заново или напишите 'Отмена', чтобы вернуть средства."
    
    _funpay_send_message(sess["chat_id"], msg)


def _get_order_progress(order):
    completed = _get_order_completed_quantity(order)
    quantity = max(int(order.get("quantity") or 0), 0)
    if quantity <= 0:
        return 0
    progress = round((completed / quantity) * 100)
    return max(0, min(100, progress))


def _get_order_completed_quantity(order):
    quantity = max(int(order.get("quantity") or 0), 0)
    status = order.get("status")
    if quantity <= 0:
        return 0
    if status == "completed":
        return quantity
    remains = _safe_int(order.get("api_remains"))
    if remains is None:
        return 0
    remains = max(remains, 0)
    completed = quantity - min(remains, quantity)
    return max(0, min(quantity, completed))


def _get_order_current_total(order):
    start_count = _safe_int(order.get("api_start_count"))
    if start_count is None:
        return None
    return max(start_count, 0) + _get_order_completed_quantity(order)


def _service_refill_enabled(order):
    service = db.get_service("twiboost", order.get("api_service_id")) if order and order.get("api_service_id") else None
    return bool(service and service.get("refill")), service


def _build_refill_unavailable_message(order, service=None):
    # Убраны HTML-теги, так как FunPay их не парсит и выводит как обычный текст
    service_name = (order.get("service_name") or order.get("lot_name") or "Услуга") if order else "Услуга"
    msg = f"⚠️ Рефилл недоступен для заказа #{order.get('id')}\n\n"
    msg += f"📦 Услуга: {service_name}\n"
    if service and not service.get("refill"):
        msg += "\nℹ️ Для данного товара рефилл не предусмотрен поставщиком."
    else:
        msg += "\nℹ️ Сейчас панель не разрешает отправить рефилл для этого заказа."
    return msg


def _refresh_order_from_api(order):
    if not order or not order.get("api_order_id"): return order
    provider = str(order.get("api_provider") or "twiboost").lower()
    api_client = get_api_client(provider)
    if not api_client: return order
    response = api_client.check_order_status(order["api_order_id"])
    if not response.get("success"): return order
    status_map = {
        "awaiting": "pending",
        "in progress": "in_progress",
        "completed": "completed",
        "partial": "partial",
        "canceled": "cancelled",
        "fail": "failed",
        "processing": "processing",
    }
    upd = {
        "api_status": response.get("status", order.get("api_status", "")),
        "api_charge": response.get("charge", order.get("api_charge", 0)),
        "api_start_count": response.get("start_count", order.get("api_start_count", 0)),
        "api_remains": response.get("remains", order.get("api_remains", 0)),
        "currency": response.get("currency", order.get("currency", "RUB")),
        "status": status_map.get(str(response.get("status", "")).lower(), order.get("status")),
    }
    if order.get("funpay_order_id") and fp_client_ready():
        details = fp.get_order_details(order["funpay_order_id"])
        if details.get("success"):
            qty = _extract_requested_quantity(details, None, None)
            service_name = _resolve_funpay_service_name(details, None, None)
            if qty > 0:
                upd["quantity"] = qty
            if service_name:
                upd["service_name"] = service_name
    upd = _update_order_finances_from_api(order, upd)
    if upd["status"] == "completed" and order.get("status") != "completed":
        upd["completed_at"] = datetime.now().isoformat()
    db.update_order(order["id"], **upd)
    refreshed = db.get_order(order["id"]) or order
    if upd["status"] == "completed" and order.get("status") != "completed":
        notify_funpay_order_completed(order["id"], completed_at=upd.get("completed_at"))
        refreshed = db.get_order(order["id"]) or refreshed
    return refreshed


def _format_order_status_message(order):
    progress = _get_order_progress(order)
    status_labels = {
        "pending": "⏳ Ожидание",
        "processing": "🔄 Обработка",
        "in_progress": "🔄 Выполняется",
        "completed": "✅ Выполнен",
        "partial": "⚠️ Частично выполнен",
        "failed": "❌ Ошибка",
        "cancelled": "🚫 Отменён",
    }
    lines = [
        "📋 Информация по заказу",
        "",
        f"🧾 Номер: #{order['id']}",
        f"📦 Услуга: {order.get('service_name') or order.get('lot_name') or 'Услуга'}",
        f"🔗 Ссылка: {order.get('link') or '—'}",
        f"📊 Количество: {order.get('quantity') or 0}",
        f"📈 Прогресс: {progress}%",
        f"📌 Статус: {status_labels.get(order.get('status'), order.get('status'))}",
    ]
    if order.get("api_remains") not in ("", None):
        lines.append(f"📉 Осталось: {order.get('api_remains')}")
    lines.extend([
        "",
        f"💰 Продажа на FunPay: {_format_money(order.get('sell_price'))}",
        f"💵 Цена TwiBoost: {_format_money(order.get('cost_price'))}",
        f"📈 Прибыль: {_format_money(order.get('profit'))}",
    ])
    return "\n".join(lines)


def _format_order_status_message(order):
    return _session_status_text(order)


def _compute_price_per_unit(api_rate, price_mode, price_input):
    cost_per_unit = (api_rate or 0) / 1000
    if price_mode == "markup":
        return round(cost_per_unit * (1 + (price_input or 0) / 100), 4)
    return round(price_input or 0, 4)


def _lot_cost_per_unit(lot):
    return (lot.get("api_rate") or 0) / 1000.0


def _lot_price_per_unit(lot):
    value = lot.get("price_per_unit") or 0
    if value and value > 0:
        return value
    return (lot.get("price") or 0) / 1000.0


def _lot_price_per_1000(lot):
    return round(_lot_price_per_unit(lot) * 1000, 2)

def generate_withdrawal_statistics(db_obj, cfg_obj):
    """Генерирует детальную статистику для авто-вывода FunPay"""
    month = datetime.now().strftime("%Y-%m")
    orders = db_obj.get_orders(limit=2000) if db_obj else []
    total_rev, total_cost, bound_rev, bound_cost, unbound_rev, unbound_cost, count = 0,0,0,0,0,0,0
    
    for o in orders:
        if not o.get("completed_at") or not o["completed_at"].startswith(month):
            continue
        if str(o.get("status", "")).lower() in ("cancelled", "failed", "refunded"):
            continue
        rev = float(o.get("sell_price", 0) or 0)
        cost = float(o.get("cost_price", 0) or 0)
        total_rev += rev
        total_cost += cost
        count += 1
        if o.get("lot_id") and db_obj.get_lot(o["lot_id"]):
            bound_rev += rev
            bound_cost += cost
        else:
            unbound_rev += rev
            unbound_cost += cost
            
    net_profit = round(max(0, total_rev - total_cost), 2)
    share_pct = float(cfg_obj.get("owner_meta", {}).get("share_percent", cfg_obj.get("mirrors", {}).get("default_share_percent", 5.0)) or 5.0)
    owner_share = round(net_profit * share_pct / 100, 2)
    
    return {
        "month": month, "order_count": count,
        "total_revenue": round(total_rev, 2), "total_cost": round(total_cost, 2),
        "bound_revenue": round(bound_rev, 2), "bound_cost": round(bound_cost, 2),
        "unbound_revenue": round(unbound_rev, 2), "unbound_cost": round(unbound_cost, 2),
        "net_profit": net_profit, "owner_share": owner_share, "share_percent": share_pct
    }


def _get_funpay_lots_preview(limit=10):
    if not fp_client_ready():
        return [], "⚠️ FunPay не подключен. Введите ID вручную."
    try:
        resp = fp.get_profile_lots()
    except Exception as e:
        logger.warning(f"Не удалось получить лоты FunPay: {e}")
        return [], "⚠️ Не удалось получить лоты. Введите ID вручную."
    if not resp.get("success"):
        return [], f"⚠️ {resp.get('error', 'Ошибка получения лотов')}. Введите ID вручную."
    lots = resp.get("lots", [])
    if not lots:
        return [], "⚠️ На FunPay нет активных лотов. Введите ID вручную."
    preview = "\n".join(
        f"#{lot['offer_id']} — {lot['title'][:40]} ({lot.get('price')} {lot.get('currency')})"
        for lot in lots[:limit]
    )
    if len(lots) > limit:
        preview += "\n..."
    return lots, preview

# Глобальные объекты (инициализируются в setup)
bot: telebot.TeleBot = None
cfg: Config = None
db: Database = None
api: TwiBoostAPI = None
fp: FunPayClient = None
support_client: FunPaySupportClient = None


def setup(tg_bot, config, database, api_client, funpay_client=None, support_center_client=None):
    """Инициализация хэндлеров"""
    global bot, cfg, db, api, fp, support_client
    bot = tg_bot
    cfg = config
    db = database
    api = api_client
    fp = funpay_client
    support_client = support_center_client
    if fp and fp_client_ready():
        fp.on(FunPayEventType.NEW_ORDER, _fp_on_new_order)
        fp.on(FunPayEventType.NEW_MESSAGE, _fp_on_new_message)
        fp.on(FunPayEventType.ORDER_STATUS_CHANGED, _fp_on_order_status)
    _register_handlers()


def is_admin(user_id):
    return user_id in cfg.admin_ids


def set_state(user_id, state, **data):
    payload = {"state": state, "data": data}
    user_states[user_id] = payload
    if db:
        db.save_user_state(user_id, state, data)


def clear_state(user_id):
    user_states.pop(user_id, None)
    if db:
        db.delete_user_state(user_id)


def get_state(user_id):
    if user_id in user_states:
        return user_states[user_id]
    if db:
        payload = db.get_user_state(user_id)
        if payload:
            user_states[user_id] = payload
            return payload
    return {}


def fp_client_ready():
    return fp is not None and getattr(fp, "_initiated", False)


def _find_lot_by_funpay_lot_id(funpay_lot_id: str):
    lots = db.get_lots(active_only=True)
    for lot in lots:
        if lot.get("funpay_lot_id") == funpay_lot_id:
            return lot
    return None


def _funpay_send_message(chat_id, text):
    if not fp_client_ready() or not text:
        return False
    try:
        if isinstance(chat_id, str) and chat_id.isdigit():
            chat_id = int(chat_id)
        if isinstance(chat_id, (int, str)):
            result = fp.send_message(chat_id, text)
            if not result.get("success"):
                logger.warning("FunPay send message failed for %s: %s", chat_id, result.get("error"))
                return False
            return True
        logger.warning(f"Invalid chat_id type: {type(chat_id)}")
        return False
    except Exception as e:
        logger.warning(f"FunPay send message error: {e}")
        return False


def _create_session_from_order(order_obj):
    order_details = fp.get_order_details(order_obj.id) if fp_client_ready() else {"success": False}
    offer_id = order_details.get("offer_id") if order_details.get("success") else getattr(order_obj, "offer_id", "")
    lot = _match_funpay_bound_lot(
        getattr(order_obj, "description", ""),
        getattr(order_obj, "price", 0),
        offer_id=offer_id,
        amount=order_details.get("amount") if order_details.get("success") else getattr(order_obj, "amount", None),
        short_description=order_details.get("short_description", "") if order_details.get("success") else "",
    )
    chat_id = _resolve_funpay_chat_id(order_obj, order_details if order_details.get("success") else {})
    requested_qty = _extract_requested_quantity(order_details if order_details.get("success") else {}, order_obj, lot)
    service_name = _resolve_funpay_service_name(order_details if order_details.get("success") else {}, order_obj, lot)
    session = {
        "chat_id": str(chat_id),
        "buyer_username": order_obj.buyer_username,
        "buyer_id": order_obj.buyer_id,
        "lot_id": lot["id"] if lot else 0,
        "lot_name": service_name,
        "pending_qty": requested_qty,
        "price": order_obj.price,
        "currency": _fp_currency_text(order_obj.currency),
        "state": "awaiting_link",
    }
    db.upsert_funpay_session(order_obj.id, **session)
    return db.get_funpay_session(order_obj.id)


def _fp_on_new_order(event):
    if not fp_client_ready():
        return
    order = event.order
    if _is_recent_funpay_order_event(order.id):
        logger.info("Recent FunPay order event ignored for #%s", order.id)
        return
    existing_session = db.get_funpay_session(order.id)
    if existing_session and existing_session.get("state") in {
        "awaiting_link", "awaiting_confirmation", "creating_order", "order_created", "completed"
    }:
        logger.info("Duplicate FunPay order event ignored for #%s (state=%s)", order.id, existing_session.get("state"))
        return
    
    logger.info(f"New FunPay order: #{order.id}, price: {order.price}, buyer: {order.buyer_username}")
    
    order_details = {"success": False}
    try:
        order_details = fp.get_order_details(order.id)
        if order_details.get("success"):
            logger.info(
                "Full order retrieved: offer_id=%s, chat_id=%s",
                order_details.get("offer_id"),
                order_details.get("chat_id"),
            )
    except Exception as e:
        logger.error(f"Failed to get full order details: {e}")
    
    matched_lot = _match_funpay_bound_lot(
        getattr(order, "description", ""),
        getattr(order, "price", 0),
        offer_id=order_details.get("offer_id"),
        amount=order_details.get("amount") or getattr(order, "amount", None),
        short_description=order_details.get("short_description", ""),
    )
    
    if not matched_lot:
        logger.warning(f"No matching lot found for order #{order.id}")
    else:
        logger.info("Matched FunPay order #%s to lot #%s (%s)", order.id, matched_lot["id"], matched_lot["name"])
    
    if matched_lot:
        requested_qty = _extract_requested_quantity(order_details if order_details.get("success") else {}, order, matched_lot)
        service_name = _resolve_funpay_service_name(order_details if order_details.get("success") else {}, order, matched_lot)
        if not LINK_REGEX.findall(getattr(order, "description", "") or ""):
            session = _create_session_from_order(order)
            if session:
                db.update_funpay_session(
                    order.id,
                    lot_id=matched_lot["id"],
                    lot_name=service_name,
                    pending_qty=requested_qty,
                    state="awaiting_link",
                )
                _funpay_send_message(
                    session["chat_id"],
                    f"Спасибо за оплату #{order.id}.\n"
                    f"Услуга: {service_name}\n"
                    f"Количество: {requested_qty}\n\n"
                    f"Отправьте ссылку для запуска заказа.",
                )
            return

        # Send immediate confirmation to buyer
        try:
            chat_id = getattr(order, "chat_id", None) or order_details.get("chat_id")
            if not chat_id and hasattr(order, 'buyer_username') and order.buyer_username:
                chat = fp.get_chat_by_name(order.buyer_username, make_request=True)
                if chat:
                    chat_id = chat.id
                    logger.info(f"Found chat by username: {order.buyer_username} -> {chat_id}")
            
            if chat_id:
                msg_text = (
                    f"🎉 Спасибо за заказ #{order.id}!\n\n"
                    f"📦 Услуга: {service_name}\n"
                    f"💰 Сумма: {order.price}{_fp_currency_text(order.currency)}\n\n"
                    f"✅ Заказ принят в обработку. Ожидайте начала выполнения!"
                )
                fp.send_message(chat_id, msg_text, chat_name=order.buyer_username)
                logger.info(f"Sent confirmation to buyer for order #{order.id}")
            else:
                logger.warning(f"Could not find chat for buyer {order.buyer_username}")
        except Exception as e:
            logger.error(f"Failed to send buyer confirmation: {e}")
        
        # Auto-create order in TwiBoost
        success = process_funpay_order_auto(
            order,
            admin_chat_id=cfg.admin_ids[0] if cfg.admin_ids else None,
            matched_lot=matched_lot,
            offer_id=order_details.get("offer_id"),
        )
        if success:
            logger.info(f"FunPay order #{order.id} auto-processed for lot {matched_lot['id']}")
        else:
            logger.warning(f"FunPay order #{order.id} failed auto-processing")
    else:
        # No binding found - create session for manual handling
        session = _create_session_from_order(order)
        if not session:
            return
        text = (
            "⚠️ Мы получили ваш заказ, но он пока не привязан к лоту."
            " Напишите, пожалуйста, название услуги и ссылку."
        )
        _funpay_send_message(session["chat_id"], text)


def _fp_on_new_message(event):
    if not fp_client_ready():
        return
    msg = event.message
    if msg.by_bot or not msg.text:
        return
    if getattr(msg, "author_id", None) == 0:
        _handle_funpay_system_message(msg)
        return
    session = db.get_funpay_session_by_chat(msg.chat_id)
    if not session and getattr(msg, "interlocutor_id", None):
        session = db.get_funpay_session_by_buyer(msg.interlocutor_id)
        if session and str(session.get("chat_id", "")) != str(msg.chat_id):
            db.update_funpay_session(session["funpay_order_id"], chat_id=str(msg.chat_id))
    if not session:
        return
    state = session.get("state")
    text_lower = msg.text.lower().strip()
    _notify_admin_buyer_message(session, msg.text)

    if state == "awaiting_link":
        urls = LINK_REGEX.findall(msg.text)
        if urls:
            db.update_funpay_session(session["funpay_order_id"], pending_link=urls[0], state="awaiting_confirmation")
            _funpay_send_message(msg.chat_id,
                                 "Получена ссылка. Подтвердите, что всё верно, ответив 'Да' для запуска заказа.")
        else:
            _funpay_send_message(msg.chat_id, "Нужна ссылка вида https://... Пожалуйста, отправьте корректный URL.")
        return

    if state == "awaiting_confirmation":
        if any(word in text_lower for word in YES_WORDS):
            _confirm_funpay_session(session)
        elif any(word in text_lower for word in NO_WORDS):
            db.update_funpay_session(session["funpay_order_id"], state="awaiting_link", pending_link="",
                                     pending_qty=0)
            _funpay_send_message(msg.chat_id, "Ок, пришлите корректную ссылку.")
        else:
            _funpay_send_message(msg.chat_id, "Ответьте 'Да' для запуска заказа или 'Нет' для изменения ссылки.")
        return

    if state in {"creating_order", "order_created", "completed"} and (
        any(word in text_lower for word in YES_WORDS) or text_lower in STATUS_WORDS
    ):
        _send_session_status(session, msg.chat_id)
        return

    if text_lower in STATUS_WORDS or text_lower.startswith("/status"):
        _send_session_status(session, msg.chat_id)
    elif text_lower in REFILL_WORDS or text_lower.startswith("/refill"):
        _handle_refill_request(session, msg.chat_id)


def _fp_on_order_status(event):
    order = event.order
    session = db.get_funpay_session(order.id)
    if not session or not fp_client_ready():
        return
    status = _fp_status_text(order.status)
    if status in ("completed", "closed"):
        db.update_funpay_session(
            session["funpay_order_id"],
            state="completed",
            support_ticket_due_at="",
            support_ticket_sent=1,
        )


def _send_session_status(session, chat_id):
    state = session.get("state")
    order_id = session.get("order_id")
    if order_id:
        order = db.get_order(order_id)
        if order:
            order = _refresh_order_from_api(order)
            _funpay_send_message(chat_id, _format_order_status_message(order))
            return
    status_text = {
        "awaiting_link": "🔗 Ждём ссылку от вас",
        "awaiting_confirmation": "✅ Ждём подтверждение ссылки",
        "order_created": "🔄 Заказ уже запущен и находится в работе",
        "creating_order": "⏳ Заказ создаётся",
        "completed": "✅ Заказ выполнен",
    }.get(state, state or "ожидание")
    _funpay_send_message(chat_id, f"📋 Текущий статус\n\n{status_text}")


def _handle_refill_request(session, chat_id, part_index=0):
    buyer_username = session.get("buyer_username")
    if not buyer_username:
        _funpay_send_message(chat_id, "⚠️ Не удалось определить покупателя.")
        return
        
    all_user_orders = db.get_orders(limit=50)
    user_orders = [o for o in all_user_orders if o.get("buyer_username") == buyer_username]
    
    if not user_orders:
        _funpay_send_message(chat_id, "⚠️ У вас ещё нет активных заказов.")
        return

    refillable = []
    for order in user_orders:
        service = db.get_service("twiboost", order.get("api_service_id")) if order.get("api_service_id") else None
        supports_refill = bool(service and service.get("refill"))
        valid_status = order.get("status") in ("completed", "in_progress", "partial", "processing")
        if supports_refill and valid_status:
            refillable.append(order)

    if not refillable:
        _funpay_send_message(chat_id, "🔁 Рефилл недоступен.\n\nВозможно, для ваших услуг рефилл не предусмотрен или заказы ещё не запущены.")
        return

    if part_index > 0:
        if part_index <= len(refillable):
            order = refillable[part_index - 1]
            result = api.refill_order(order["api_order_id"]) if api else {"success": False, "error": "API недоступно"}
            if result.get("success"):
                db.update_order(order["id"], refill_count=order.get("refill_count", 0) + 1)
                _funpay_send_message(
                    chat_id,
                    f"✅ Запрос на рефилл отправлен для заказа #{order['id']}!\n\n"
                    f"📦 Услуга: {order.get('service_name')}\n"
                    "⏳ Как только панель примет запрос, заказ начнёт восстанавливаться."
                )
            else:
                _funpay_send_message(chat_id, f"❌ Ошибка рефилла: {result.get('error', 'Неизвестная ошибка')}")
        else:
            _funpay_send_message(chat_id, f"⚠️ Заказ под номером #{part_index} не найден или не поддерживает рефилл.")
        return

    if len(refillable) == 1:
        order = refillable[0]
        result = api.refill_order(order["api_order_id"]) if api else {"success": False, "error": "API недоступно"}
        if result.get("success"):
            db.update_order(order["id"], refill_count=order.get("refill_count", 0) + 1)
            _funpay_send_message(
                chat_id,
                "🔁 Запрос на рефилл отправлен!\n\n"
                f"🧾 Номер заказа: #{order['id']}\n"
                f"📦 Услуга: {order.get('service_name')}\n"
                "⏳ Ожидайте восстановления."
            )
        else:
            _funpay_send_message(chat_id, f"❌ Ошибка рефилла: {result.get('error', 'Неизвестная ошибка')}")
        return

    if len(refillable) > 1:
        lines = [
            "🔁 Выберите заказ для рефилла\n\n",
            "У вас есть несколько заказов, доступных для восстановления:\n",
        ]
        for idx, order in enumerate(refillable, start=1):
            svc_name = (order.get("service_name") or "Услуга")[:40]
            qty = order.get("quantity", 0)
            refills = order.get("refill_count", 0)
            status = order.get("status", "unknown")
            lines.append(
                f"{idx}. 📦 {svc_name}\n"
                f"   📊 Кол-во: {qty} | 🔁 Рефиллов: {refills}\n"
                f"   📌 Статус: {status}\n"
                f"   💡 Команда: Рефил{idx} или /refill{idx}\n"
            )
        lines.extend([
            "\n💬 Напишите номер или команду, например: Рефил1",
            "❌ Для отмены напишите: Отмена"
        ])
        _funpay_send_message(chat_id, "\n".join(lines))
# ==================== РЕГИСТРАЦИЯ ХЭНДЛЕРОВ ====================

def _fp_on_new_order(event):
    if not fp_client_ready():
        return
    order = event.order
    existing_session = db.get_funpay_session(order.id)
    if existing_session and existing_session.get("state") in {
        "awaiting_link", "awaiting_confirmation", "creating_order", "order_created", "completed"
    }:
        logger.info("Duplicate FunPay order event ignored for #%s (state=%s)", order.id, existing_session.get("state"))
        return

    logger.info(f"New FunPay order: #{order.id}, price: {order.price}, buyer: {order.buyer_username}")
    order_details = {"success": False}
    try:
        order_details = fp.get_order_details(order.id)
        if order_details.get("success"):
            logger.info("Full order retrieved: offer_id=%s, chat_id=%s", order_details.get("offer_id"), order_details.get("chat_id"))
    except Exception as e:
        logger.error(f"Failed to get full order details: {e}")

    matched_lot = _match_funpay_bound_lot(
        getattr(order, "description", ""),
        getattr(order, "price", 0),
        offer_id=order_details.get("offer_id"),
        amount=order_details.get("amount") or getattr(order, "amount", None),
        short_description=order_details.get("short_description", ""),
    )

    if not matched_lot:
        logger.warning(f"No matching lot found for order #{order.id}")
        session = _create_session_from_order(order)
        if session:
            _funpay_send_message(
                session["chat_id"],
                "⚠️ Мы получили ваш заказ, но он пока не привязан к лоту. "
                "Напишите, пожалуйста, название услуги и ссылку.",
            )
        return

    requested_qty = _extract_requested_quantity(order_details if order_details.get("success") else {}, order, matched_lot)
    service_name = _resolve_funpay_service_name(order_details if order_details.get("success") else {}, order, matched_lot)
    if not LINK_REGEX.findall(getattr(order, "description", "") or ""):
        session = _create_session_from_order(order)
        if session:
            db.update_funpay_session(
                order.id,
                lot_id=matched_lot["id"],
                lot_name=service_name,
                pending_qty=requested_qty,
                state="awaiting_link",
                promo_code="",
                promo_value=0,
            )
            _funpay_send_message(session["chat_id"], _session_text_payment(order.id, service_name, requested_qty))
        return

    success = process_funpay_order_auto(
        order,
        admin_chat_id=cfg.admin_ids[0] if cfg.admin_ids else None,
        matched_lot=matched_lot,
        offer_id=order_details.get("offer_id"),
    )
    if success:
        logger.info(f"FunPay order #{order.id} auto-processed for lot {matched_lot['id']}")
    else:
        logger.warning(f"FunPay order #{order.id} failed auto-processing")


def _fp_on_new_message(event):
    if not fp_client_ready():
        return
    msg = event.message
    if msg.by_bot or not msg.text:
        return
    if getattr(msg, "author_id", None) == 0:
        _handle_funpay_system_message(msg)
        return
    session = db.get_funpay_session_by_chat(msg.chat_id)
    if not session and getattr(msg, "interlocutor_id", None):
        session = db.get_funpay_session_by_buyer(msg.interlocutor_id)
        if session and str(session.get("chat_id", "")) != str(msg.chat_id):
            db.update_funpay_session(session["funpay_order_id"], chat_id=str(msg.chat_id))
            session = db.get_funpay_session(session["funpay_order_id"]) or session
    if not session:
        return

    author_id = getattr(msg, "author_id", None)
    buyer_id = session.get("buyer_id")
    if buyer_id not in (None, "", 0, "0") and author_id not in (None, 0):
        try:
            if int(author_id) != int(buyer_id):
                return
        except (TypeError, ValueError):
            pass
    if author_id not in (None, 0) and getattr(fp, "user_id", None):
        try:
            if int(author_id) == int(fp.user_id):
                return
        except (TypeError, ValueError):
            pass
    author_name = str(getattr(msg, "author", "") or "").strip().lower()
    if author_name and str(getattr(fp, "username", "") or "").strip().lower() == author_name:
        return

    state = session.get("state")
    text_lower = msg.text.lower().strip()
    _notify_admin_buyer_message(session, msg.text)

    if _try_handle_knowledge_or_speed(session, msg):
        return

    review_bonus_state = str(session.get("review_bonus_state") or "").strip().lower()
    if review_bonus_state == "awaiting_link":
        lot = db.get_lot(session.get("lot_id")) if session.get("lot_id") else None
        if not _lot_review_bonus_enabled(lot):
            db.update_funpay_session(
                session["funpay_order_id"],
                review_bonus_state="",
                review_bonus_link="",
                review_bonus_order_id=0,
            )
            _funpay_send_message(
                msg.chat_id,
                "⚠️ Бонус за отзыв для этого лота больше не настроен. Если понадобится помощь, напишите в этот чат.",
            )
            return
        if _is_refuse_bonus_text(msg.text):
            db.update_funpay_session(
                session["funpay_order_id"],
                review_bonus_state="skipped",
                review_bonus_link="",
                review_bonus_order_id=0,
            )
            _funpay_send_message(
                msg.chat_id,
                "👌 Бонус пропущен.\n\nЕсли позже захотите воспользоваться им, просто поставьте 5★ заново.",
            )
            return
        if text_lower in STATUS_WORDS or text_lower.startswith("/status"):
            _send_session_status(session, msg.chat_id)
            return
        if text_lower in REFILL_WORDS or text_lower.startswith("/refill"):
            _handle_refill_request(session, msg.chat_id)
            return
        urls = LINK_REGEX.findall(msg.text)
        if not urls:
            _funpay_send_message(msg.chat_id, _session_lot_review_bonus_invalid_message(lot))
            return
        linked_order = db.get_order(session["order_id"]) if session.get("order_id") else {}
        result = _create_review_bonus_order(session, linked_order or {}, lot, urls[0])
        if not result.get("success"):
            _funpay_send_message(msg.chat_id, _session_lot_review_bonus_error_message(result.get("error") or "неизвестная ошибка"))
            return
        db.update_funpay_session(
            session["funpay_order_id"],
            review_bonus_state="done",
            review_bonus_link=urls[0],
            review_bonus_order_id=result["order_id"],
        )
        _notify_admin_new_order(
            funpay_order_id=session.get("funpay_order_id") or "",
            api_order_id=result.get("api_order_id") or "",
            buyer=session.get("buyer_username") or "",
            service_name=result.get("service_name") or lot.get("review_bonus_service_name") or "Бонус за отзыв",
            quantity=result.get("quantity") or _lot_review_bonus_quantity(lot),
            sell_price=0,
            cost_price=result.get("cost_price") or 0,
            profit=result.get("profit") or 0,
            link=urls[0],
            state_label="Бонус за 5★ запущен",
        )
        _funpay_send_message(msg.chat_id, _session_lot_review_bonus_started_message(result["order_id"], lot, urls[0]))
        return

    if state == "awaiting_link":
        if _session_input_promo(session, msg.text, msg.chat_id):
            return
        urls = LINK_REGEX.findall(msg.text)
        if urls:
            db.update_funpay_session(session["funpay_order_id"], pending_link=urls[0], state="awaiting_confirmation")
            session = db.get_funpay_session(session["funpay_order_id"]) or session
            _funpay_send_message(msg.chat_id, _build_review_prompt_message(session))
        else:
            _funpay_send_message(msg.chat_id, _session_input_invalid())
        return

    if state == "awaiting_confirmation":
        if any(word in text_lower for word in YES_WORDS):
            _confirm_funpay_session(session)
        elif any(word in text_lower for word in NO_WORDS):
            db.update_funpay_session(session["funpay_order_id"], state="awaiting_link", pending_link="")
            session = db.get_funpay_session(session["funpay_order_id"]) or session
            _funpay_send_message(msg.chat_id, _session_payment_prompt(session))
        elif _session_input_promo(session, msg.text, msg.chat_id):
            return
        else:
            _funpay_send_message(msg.chat_id, _session_input_confirm())
        return

    if state in {"creating_order", "order_created", "completed"} and (
        any(word in text_lower for word in YES_WORDS) or text_lower in STATUS_WORDS
    ):
        _send_session_status(session, msg.chat_id)
        return

    if text_lower in STATUS_WORDS or text_lower.startswith("/status"):
        _send_session_status(session, msg.chat_id)
    elif text_lower in REFILL_WORDS or text_lower.startswith("/refill"):
        _handle_refill_request(session, msg.chat_id)


def _fp_on_order_status(event):
    order = event.order
    session = db.get_funpay_session(order.id)
    if not session or not fp_client_ready():
        return
    status = _fp_status_text(order.status)
    if status == "refunded":
        linked_order = db.get_order_by_funpay_order_id(order.id)
        _session_mark_refunded(session["funpay_order_id"])
        if linked_order and linked_order.get("status") not in {"cancelled", "refunded"}:
            db.update_order(linked_order["id"], status="cancelled", error_message="Возврат на FunPay")
        return
    if status not in ("completed", "closed"):
        return
    linked_order = db.get_order_by_funpay_order_id(order.id)
    was_confirmed = _session_buyer_confirmed(session)
    _session_mark_closed(session["funpay_order_id"])
    session = db.get_funpay_session(session["funpay_order_id"]) or session
    if not was_confirmed and session.get("chat_id"):
        _funpay_send_message(session["chat_id"], _session_text_confirmed(session, linked_order))
    if linked_order and _session_review_should_process(linked_order):
        _session_review_dispatch(linked_order)



def _extract_vote_answer_number(text):
    match = re.search(r"(\d+)", str(text or ""))
    if not match:
        return ""
    value = match.group(1)
    return value if int(value) > 0 else ""


def _session_payment_prompt(session):
    return _session_payment_message_rich(
        session.get("funpay_order_id"),
        session.get("lot_name") or "Услуга",
        _session_current_quantity(session),
        order_mode=_session_order_mode(session),
        promo_code=session.get("promo_code") or "",
        split_enabled=_lot_split_enabled(_session_lot(session)),
    )


def _session_text_payment(order_id, service_name, quantity):
    return _session_payment_message_rich(order_id, service_name, quantity)


def _send_session_status(session, chat_id):
    state = session.get("state")
    orders = _session_related_orders(session)
    if orders:
        if len(orders) == 1:
            order = _refresh_order_from_api(orders[0])
            _funpay_send_message(chat_id, _format_order_status_message(order))
            return
        lines = [
            "📋 Заказ разделён на несколько частей",
            "",
        ]
        for idx, order in enumerate(orders, start=1):
            lines.append(
                f"{idx}. #{order['id']} | {order.get('status') or '—'} | "
                f"{order.get('quantity') or 0} | {order.get('link') or '—'}"
            )
        lines.extend([
            "",
            "Команды:",
            "Инфо1 / Инфо2 ... — статус нужной части",
            "Рефил1 / Рефил2 ... — рефилл нужной части",
        ])
        _funpay_send_message(chat_id, "\n".join(lines))
        return
    status_text = {
        "awaiting_link": "🔗 Ждём ссылку от вас",
        "awaiting_vote_answer": "🗳 Ждём номер варианта ответа",
        "awaiting_reaction": "😍 Ждём реакцию",
        "awaiting_comments": "💬 Ждём комментарии",
        "awaiting_split_parts": "➗ Ждём количество частей",
        "awaiting_split_lines": "🔗 Ждём ссылки и количество для частей",
        "awaiting_confirmation": "✅ Ждём подтверждение запуска",
        "order_created": "🔄 Заказ уже запущен и находится в работе",
        "creating_order": "⏳ Заказ создаётся",
        "completed": "✅ Заказ выполнен",
    }.get(state, state or "ожидание")
    _funpay_send_message(chat_id, f"📋 Текущий статус\n\n{status_text}")


def _fp_on_new_order(event):
    if not fp_client_ready():
        return
    order = event.order
    now_ts = time.time()
    with recent_funpay_order_events_lock:
        last_seen = recent_funpay_order_events.get(order.id)
        if last_seen and now_ts - last_seen < 15:
            logger.info("Duplicate FunPay order event throttled for #%s", order.id)
            return
        recent_funpay_order_events[order.id] = now_ts

    existing_session = db.get_funpay_session(order.id)
    if existing_session and existing_session.get("state") in {
        "awaiting_link", "awaiting_vote_answer", "awaiting_reaction", "awaiting_comments",
        "awaiting_split_parts", "awaiting_split_lines", "awaiting_confirmation",
        "creating_order", "order_created", "completed"
    }:
        logger.info("Duplicate FunPay order event ignored for #%s (state=%s)", order.id, existing_session.get("state"))
        return

    logger.info("New FunPay order: #%s, price: %s, buyer: %s", order.id, order.price, order.buyer_username)
    order_details = {"success": False}
    try:
        order_details = fp.get_order_details(order.id)
        if order_details.get("success"):
            logger.info("Full order retrieved: offer_id=%s, chat_id=%s", order_details.get("offer_id"), order_details.get("chat_id"))
    except Exception as exc:
        logger.error("Failed to get full order details: %s", exc)

    matched_lot = _match_funpay_bound_lot(
        getattr(order, "description", ""),
        getattr(order, "price", 0),
        offer_id=order_details.get("offer_id"),
        amount=order_details.get("amount") or getattr(order, "amount", None),
        short_description=order_details.get("short_description", ""),
    )
    if not matched_lot:
        logger.warning("No matching lot found for order #%s", order.id)
        session = _create_session_from_order(order)
        if session:
            _funpay_send_message(
                session["chat_id"],
                "⚠️ Мы получили ваш заказ, но он пока не привязан к лоту. Напишите, пожалуйста, название услуги и ссылку.",
            )
        return

    requested_qty = _extract_requested_quantity(order_details if order_details.get("success") else {}, order, matched_lot)
    service_name = _resolve_funpay_service_name(order_details if order_details.get("success") else {}, order, matched_lot)
    order_mode = _lot_order_mode(matched_lot)
    session = _create_session_from_order(order)
    if not session:
        logger.warning("Failed to create FunPay session for order #%s", order.id)
        return

    links = LINK_REGEX.findall(getattr(order, "description", "") or "")
    updates = dict(
        lot_id=matched_lot["id"],
        lot_name=service_name,
        pending_qty=requested_qty,
        promo_code="",
        promo_value=0,
        pending_answer_number="",
    )
    if links:
        updates["pending_link"] = links[0]
        updates["state"] = "awaiting_vote_answer" if order_mode == "vote" else "awaiting_confirmation"
    else:
        updates["pending_link"] = ""
        updates["state"] = "awaiting_link"
    db.update_funpay_session(order.id, **updates)
    session = db.get_funpay_session(order.id) or session

    if _should_send_funpay_payment_message(order.id):
        _funpay_send_message(
            session["chat_id"],
            _session_payment_message_rich(
                order.id,
                service_name,
                requested_qty,
                order_mode=order_mode,
                promo_code=session.get("promo_code") or "",
                split_enabled=_lot_split_enabled(matched_lot),
            ),
        )
    if links:
        if order_mode == "vote":
            _funpay_send_message(session["chat_id"], _session_vote_prompt_message(session))
        else:
            _funpay_send_message(session["chat_id"], _session_confirmation_message_rich(session))


def _fp_on_new_message(event):
    if not fp_client_ready():
        return
    msg = event.message
    if msg.by_bot or not msg.text:
        return
    if getattr(msg, "author_id", None) == 0:
        _handle_funpay_system_message(msg)
        return

    session = db.get_funpay_session_by_chat(msg.chat_id)
    if not session and getattr(msg, "interlocutor_id", None):
        session = db.get_funpay_session_by_buyer(msg.interlocutor_id)
        if session and str(session.get("chat_id", "")) != str(msg.chat_id):
            db.update_funpay_session(session["funpay_order_id"], chat_id=str(msg.chat_id))
            session = db.get_funpay_session(session["funpay_order_id"]) or session
    if not session:
        return

    author_id = getattr(msg, "author_id", None)
    fp_user_id = getattr(fp, "user_id", None)
    fp_username = str(getattr(fp, "username", "") or "").strip().lower()
    buyer_id = session.get("buyer_id")
    buyer_username = str(session.get("buyer_username") or "").strip().lower()
    author_name = str(getattr(msg, "author", "") or "").strip().lower()

    # Обрабатываем только реальные сообщения покупателя, а не свои ответы в чат.
    try:
        if fp_user_id is not None and author_id is not None and int(author_id) == int(fp_user_id):
            return
    except (TypeError, ValueError):
        pass
    if fp_username and author_name and author_name == fp_username:
        return
    try:
        if buyer_id not in (None, "", 0, "0") and author_id not in (None, 0) and int(author_id) != int(buyer_id):
            return
    except (TypeError, ValueError):
        pass
    if buyer_username and author_name and author_name != buyer_username:
        return

    state = session.get("state")
    text_lower = msg.text.lower().strip()
    _notify_admin_buyer_message(session, msg.text)

    review_bonus_state = str(session.get("review_bonus_state") or "").strip().lower()
    if review_bonus_state == "awaiting_link":
        lot = db.get_lot(session.get("lot_id")) if session.get("lot_id") else None
        if not _lot_review_bonus_enabled(lot):
            db.update_funpay_session(
                session["funpay_order_id"],
                review_bonus_state="",
                review_bonus_link="",
                review_bonus_order_id=0,
            )
            _funpay_send_message(
                msg.chat_id,
                "⚠️ Бонус за отзыв для этого лота больше не настроен. Если понадобится помощь, напишите в этот чат.",
            )
            return
        if text_lower in STATUS_WORDS or text_lower.startswith("/status"):
            _send_session_status(session, msg.chat_id)
            return
        if text_lower in REFILL_WORDS or text_lower.startswith("/refill"):
            _handle_refill_request(session, msg.chat_id)
            return
        urls = LINK_REGEX.findall(msg.text)
        if not urls:
            _funpay_send_message(msg.chat_id, _session_lot_review_bonus_invalid_message(lot))
            return
        linked_order = db.get_order(session["order_id"]) if session.get("order_id") else {}
        result = _create_review_bonus_order(session, linked_order or {}, lot, urls[0])
        if not result.get("success"):
            _funpay_send_message(msg.chat_id, _session_lot_review_bonus_error_message(result.get("error") or "неизвестная ошибка"))
            return
        db.update_funpay_session(
            session["funpay_order_id"],
            review_bonus_state="done",
            review_bonus_link=urls[0],
            review_bonus_order_id=result["order_id"],
        )
        _notify_admin_new_order(
            funpay_order_id=session.get("funpay_order_id") or "",
            api_order_id=result.get("api_order_id") or "",
            buyer=session.get("buyer_username") or "",
            service_name=result.get("service_name") or lot.get("review_bonus_service_name") or "Бонус за отзыв",
            quantity=result.get("quantity") or _lot_review_bonus_quantity(lot),
            sell_price=0,
            cost_price=result.get("cost_price") or 0,
            profit=result.get("profit") or 0,
            link=urls[0],
            state_label="Бонус за 5★ запущен",
        )
        _funpay_send_message(msg.chat_id, _session_lot_review_bonus_started_message(result["order_id"], lot, urls[0]))
        return

    if state == "awaiting_link":
        lot = _session_lot(session)
        if text_lower in SPLIT_WORDS:
            if not _lot_split_enabled(lot):
                _funpay_send_message(msg.chat_id, "⚠️ Для этого лота разделение не включено.")
                return
            db.update_funpay_session(
                session["funpay_order_id"],
                state="awaiting_split_parts",
                pending_link="",
                pending_split_parts=0,
                pending_split_json="",
            )
            refreshed = db.get_funpay_session(session["funpay_order_id"]) or session
            _funpay_send_message(msg.chat_id, _session_split_count_prompt(refreshed, lot))
            return
        if text_lower == "отмена":
            _cancel_funpay_session_by_buyer(session)
            return
        if _session_input_promo(session, msg.text, msg.chat_id):
            return
        urls = LINK_REGEX.findall(msg.text)
        if not urls:
            _funpay_send_message(msg.chat_id, _session_payment_prompt(session))
            return
        order_mode = _session_order_mode(session)
        next_state = {
            "vote": "awaiting_vote_answer",
            "reaction": "awaiting_reaction",
            "comments": "awaiting_comments",
        }.get(order_mode, "awaiting_confirmation")
        db.update_funpay_session(
            session["funpay_order_id"],
            pending_link=urls[0],
            pending_answer_number="",
            pending_reaction="",
            pending_comments="",
            state=next_state,
        )
        session = db.get_funpay_session(session["funpay_order_id"]) or session
        if next_state == "awaiting_vote_answer":
            _funpay_send_message(msg.chat_id, _session_vote_prompt_message(session))
        elif next_state == "awaiting_reaction":
            _funpay_send_message(msg.chat_id, _session_reaction_prompt_message(session))
        elif next_state == "awaiting_comments":
            _funpay_send_message(msg.chat_id, _session_comments_prompt_message(session))
        else:
            _funpay_send_message(msg.chat_id, _session_confirmation_message_rich(session))
        return

    if state == "awaiting_split_parts":
        try:
            if text_lower == "отмена":
                _cancel_funpay_session_by_buyer(session)
                return

            lot = _session_lot(session)
            if not lot:
                _funpay_send_message(msg.chat_id, "❌ Лот для заказа не найден. Обратитесь в поддержку.")
                return

            # 🔥 Берём итоговое количество для API (умноженное на множитель лота)
            base_qty = int(session.get("pending_qty") or 0)
            multiplier = int(lot.get("quantity_per_order") or 1)
            total_api_qty = max(1, base_qty * multiplier)

            min_qty = max(1, int(lot.get("min_quantity") or 1))
            max_parts = min(5, max(2, total_api_qty // min_qty)) if total_api_qty >= min_qty * 2 else 1

            try:
                match = re.search(r"(\d+)", msg.text or "")
                parts_count = int(match.group(1)) if match else 0
            except Exception:
                parts_count = 0

            if parts_count < 2 or parts_count > max_parts:
                _funpay_send_message(msg.chat_id, _session_split_invalid_message(f"количество частей должно быть от 2 до {max_parts}."))
                return

            db.update_funpay_session(
                session["funpay_order_id"],
                state="awaiting_split_lines",
                pending_split_parts=parts_count,
                pending_split_json=""
            )
            refreshed = db.get_funpay_session(session["funpay_order_id"]) or session
            _funpay_send_message(msg.chat_id, _session_split_lines_prompt(refreshed, parts_count, lot))
        except Exception as e:
            logger.error("Error in awaiting_split_parts: %s", e, exc_info=True)
            _funpay_send_message(msg.chat_id, "⚠️ Произошла ошибка при обработке разделения. Попробуйте ещё раз или напишите: Отмена")
        return

    if state == "awaiting_split_lines":
        if text_lower == "отмена":
            _cancel_funpay_session_by_buyer(session)
            return

        lot = _session_lot(session)
        if not lot:
            _funpay_send_message(msg.chat_id, "❌ Лот не найден.")
            return

        expected_parts = int(session.get("pending_split_parts") or 0)
        if expected_parts <= 0:
            _funpay_send_message(msg.chat_id, "⚠️ Ошибка: количество частей не задано.")
            return

        # Загружаем уже собранные части из БД
        try:
            collected_items = json.loads(session.get("pending_split_json") or "[]")
            if not isinstance(collected_items, list):
                collected_items = []
        except Exception:
            collected_items = []

        # Парсим новые строки из текущего сообщения
        new_items = []
        lines = [line.strip() for line in msg.text.splitlines() if line.strip()]
        for line in lines:
            urls = LINK_REGEX.findall(line)
            if not urls:
                _funpay_send_message(msg.chat_id, f"❌ В строке не найдена ссылка:\n{line[:60]}")
                return
            link = urls[0]
            match = re.search(r"(\d+)", line.replace(link, "", 1))
            if not match:
                _funpay_send_message(msg.chat_id, f"❌ В строке не найдено количество:\n{line[:60]}")
                return
            qty = int(match.group(1))
            if qty <= 0:
                _funpay_send_message(msg.chat_id, f"❌ Количество должно быть больше нуля:\n{line[:60]}")
                return
            new_items.append({"link": link, "quantity": qty})

        # Добавляем к уже собранным
        collected_items.extend(new_items)

        # Проверка на превышение
        if len(collected_items) > expected_parts:
            _funpay_send_message(msg.chat_id, f"⚠️ Принято слишком много частей ({len(collected_items)}/{expected_parts}). Введите Отмена и начните заново.")
            return

        # Сохраняем промежуточное состояние
        db.update_funpay_session(session["funpay_order_id"], pending_split_json=json.dumps(collected_items, ensure_ascii=False))

        # Если ещё не все части собраны — показываем прогресс
        if len(collected_items) < expected_parts:
            _funpay_send_message(msg.chat_id, f"✅ Часть {len(collected_items)}/{expected_parts} принята.\n\nОтправьте следующую часть в формате:\nссылка количество")
            return

        # Все части собраны — проверяем общую сумму (с учётом множителя лота)
        base_qty = int(session.get("pending_qty") or 0)
        multiplier = int(lot.get("quantity_per_order") or 1)
        total_api_qty = max(1, base_qty * multiplier)

        current_total = sum(item["quantity"] for item in collected_items)
        if current_total != total_api_qty:
            _funpay_send_message(msg.chat_id, f"❌ Сумма всех частей должна быть ровно {total_api_qty}. Сейчас: {current_total}.\n\nВведите Отмена и начните заново.")
            db.update_funpay_session(session["funpay_order_id"], pending_split_json="[]")
            return

        # Проверка лимитов на каждую часть
        min_qty = max(1, int(lot.get("min_quantity") or 1))
        max_qty = max(min_qty, int(lot.get("max_quantity") or min_qty))
        for item in collected_items:
            if item["quantity"] < min_qty or item["quantity"] > max_qty:
                _funpay_send_message(msg.chat_id, _session_split_invalid_message(f"Каждая часть должна быть от {min_qty} до {max_qty}."))
                db.update_funpay_session(session["funpay_order_id"], pending_split_json="[]")
                return

        # Переход к следующему шагу
        next_state = {
            "vote": "awaiting_vote_answer",
            "reaction": "awaiting_reaction",
            "comments": "awaiting_comments"
        }.get(_session_order_mode(session), "awaiting_confirmation")

        db.update_funpay_session(session["funpay_order_id"], state=next_state)
        refreshed = db.get_funpay_session(session["funpay_order_id"]) or session

        if next_state == "awaiting_vote_answer":
            _funpay_send_message(msg.chat_id, _session_vote_prompt_message(refreshed))
        elif next_state == "awaiting_reaction":
            _funpay_send_message(msg.chat_id, _session_reaction_prompt_message(refreshed))
        elif next_state == "awaiting_comments":
            _funpay_send_message(msg.chat_id, _session_comments_prompt_message(refreshed))
        else:
            _funpay_send_message(msg.chat_id, _session_confirmation_message_rich(refreshed))
        return

    if state == "awaiting_vote_answer":
        if text_lower == "отмена":
            _cancel_funpay_session_by_buyer(session)
            return
        if _is_no_text(msg.text):
            _session_reset_buyer_inputs(session["funpay_order_id"])
            session = db.get_funpay_session(session["funpay_order_id"]) or session
            _funpay_send_message(msg.chat_id, _session_payment_prompt(session))
            return
        if _session_input_promo(session, msg.text, msg.chat_id):
            return
        answer_number = _extract_vote_answer_number(msg.text)
        if not answer_number:
            _funpay_send_message(msg.chat_id, _session_vote_prompt_invalid_message())
            return
        db.update_funpay_session(
            session["funpay_order_id"],
            pending_answer_number=answer_number,
            state="awaiting_confirmation",
        )
        session = db.get_funpay_session(session["funpay_order_id"]) or session
        _funpay_send_message(msg.chat_id, _session_confirmation_message_rich(session))
        return

    if state == "awaiting_reaction":
        if text_lower == "отмена":
            _cancel_funpay_session_by_buyer(session)
            return
        if text_lower in LIST_WORDS:
            _funpay_send_message(msg.chat_id, _session_reaction_list_message(session))
            return
        if _is_no_text(msg.text):
            _session_reset_buyer_inputs(session["funpay_order_id"])
            session = db.get_funpay_session(session["funpay_order_id"]) or session
            _funpay_send_message(msg.chat_id, _session_payment_prompt(session))
            return
        if _session_input_promo(session, msg.text, msg.chat_id):
            return
        lot = _session_lot(session)
        split_plan = _session_split_plan(session)
        if split_plan:
            existing = _session_split_reactions(session)
            candidates = [_extract_reaction_value(line) for line in str(msg.text or "").splitlines()]
            candidates = [value for value in candidates if value]
            if not candidates:
                _funpay_send_message(msg.chat_id, _session_reaction_prompt_invalid_message(session, msg.text.strip()))
                return
            merged = list(existing)
            for reaction_value in candidates:
                if not _find_reaction_service_for_lot(lot, reaction_value):
                    _funpay_send_message(msg.chat_id, _session_reaction_prompt_invalid_message(session, reaction_value))
                    return
                merged.append(reaction_value)
            expected = len(split_plan)
            if len(merged) > expected:
                _funpay_send_message(
                    msg.chat_id,
                    f"❌ Получено слишком много реакций.\n\nНужно: {expected}\nПолучено: {len(merged)}"
                )
                return
            db.update_funpay_session(
                session["funpay_order_id"],
                pending_reaction=json.dumps(merged, ensure_ascii=False),
            )
            if len(merged) < expected:
                _funpay_send_message(msg.chat_id, _session_reaction_progress_message(len(merged), expected))
                return
            db.update_funpay_session(
                session["funpay_order_id"],
                state="awaiting_confirmation",
            )
            session = db.get_funpay_session(session["funpay_order_id"]) or session
            _funpay_send_message(msg.chat_id, _session_confirmation_message_rich(session))
            return

        reaction_value = _extract_reaction_value(msg.text)
        if not reaction_value:
            _funpay_send_message(msg.chat_id, _session_reaction_prompt_invalid_message(session, msg.text.strip()))
            return
        if not _find_reaction_service_for_lot(lot, reaction_value):
            _funpay_send_message(msg.chat_id, _session_reaction_prompt_invalid_message(session, reaction_value))
            return
        db.update_funpay_session(
            session["funpay_order_id"],
            pending_reaction=reaction_value,
            state="awaiting_confirmation",
        )
        session = db.get_funpay_session(session["funpay_order_id"]) or session
        _funpay_send_message(msg.chat_id, _session_confirmation_message_rich(session))
        return

    if state == "awaiting_comments":
        if text_lower == "отмена":
            _cancel_funpay_session_by_buyer(session)
            return
    
        expected = int(_session_current_quantity(session) or 0)
    
    # Получаем уже собранные комментарии из сессии
        current_comments = _extract_comment_lines(session.get("pending_comments") or "")
    
    # Парсим новые комментарии из текущего сообщения
        new_comments, invalid_lines = _extract_hash_comment_lines(msg.text)
    
    # Если есть невалидные строки или нет новых комментариев - ошибка
        if invalid_lines or not new_comments:
            _funpay_send_message(
                msg.chat_id,
                _session_comments_prompt_error_message(
                    "каждый комментарий должен начинаться с символа #. Пример: #Привет"
                ),
            )
            return
    
    # Объединяем старые и новые комментарии
        comment_lines = current_comments + new_comments
    
        # Проверка на превышение
        if len(comment_lines) > expected:
            _funpay_send_message(msg.chat_id, _session_comments_prompt_invalid_message(expected, len(comment_lines)))
            return
    
    # Проверка длины каждого комментария
        for idx, line in enumerate(comment_lines, start=1):
            if len(line) > 500:
                _funpay_send_message(
                    msg.chat_id,
                    _session_comments_prompt_error_message(
                        f"комментарий #{idx} слишком длинный (максимум 500 символов)"
                    ),
                )
                return
    
    # Сохраняем комментарии в сессию
        db.update_funpay_session(
            session["funpay_order_id"],
            pending_comments="\n".join(comment_lines),
        )
    
        # Если ещё не все комментарии собраны - показываем прогресс
        if len(comment_lines) < expected:
            _funpay_send_message(msg.chat_id, _session_comments_progress_message(len(comment_lines), expected))
            return
    
    # Все комментарии собраны - переходим к подтверждению
        db.update_funpay_session(
            session["funpay_order_id"],
            state="awaiting_confirmation",
        )
        session = db.get_funpay_session(session["funpay_order_id"]) or session
        _funpay_send_message(msg.chat_id, _session_confirmation_message_rich(session))
        return

    if state == "awaiting_confirmation":
        if text_lower == "отмена":
            _cancel_funpay_session_by_buyer(session)
            return
        if _is_yes_text(msg.text):
            _confirm_funpay_session(session)
        elif _is_no_text(msg.text):
            _session_reset_buyer_inputs(session["funpay_order_id"])
            session = db.get_funpay_session(session["funpay_order_id"]) or session
            _funpay_send_message(msg.chat_id, _session_payment_prompt(session))
        elif _session_input_promo(session, msg.text, msg.chat_id):
            return
        else:
            _funpay_send_message(msg.chat_id, _session_confirmation_message_rich(session))
        return

    if state in {"creating_order", "order_created", "completed"} and (
        _is_yes_text(msg.text) or text_lower in STATUS_WORDS or _parse_status_index(text_lower) > 0
    ):
        _send_session_status(session, msg.chat_id)
        return

    status_index = _parse_status_index(text_lower)
    refill_index = _parse_refill_index(text_lower)
    if text_lower in STATUS_WORDS or text_lower.startswith("/status"):
        _send_session_status(session, msg.chat_id)
    elif status_index > 0:
        order = _session_order_by_index(session, status_index)
        if not order:
            _funpay_send_message(msg.chat_id, f"⚠️ Часть #{status_index} не найдена.")
        else:
            _funpay_send_message(msg.chat_id, _format_order_status_message(_refresh_order_from_api(order)))
    elif text_lower in REFILL_WORDS or text_lower.startswith("/refill"):
        _handle_refill_request(session, msg.chat_id)
    elif refill_index > 0:
        _handle_refill_request(session, msg.chat_id, part_index=refill_index)
        # 🔥 Обработка текстовых команд после создания заказа
    if state in ("order_created", "completed", "failed", "partial"):
        cmd = text_lower.strip()
        if cmd == "отмена":
            _handle_buyer_refund_cmd(session)
            return
        if cmd in ("повторить", "заново"):
            _handle_buyer_reorder_cmd(session)
            return
        if cmd in ("дозаказать", "дозаказ"):
            _handle_buyer_partial_refill_cmd(session)
            return
        
def _handle_buyer_refund_cmd(session):
    fp_id = session.get("funpay_order_id")
    chat_id = session.get("chat_id")
    if not fp_id or not fp_client_ready() or not chat_id:
        _funpay_send_message(chat_id, "❌ Не удалось обработать запрос. Попробуйте позже.")
        return

    # 1. Ищем связанный заказ в базе
    order = db.get_order_by_funpay_order_id(fp_id)
    
    # Если заказа в БД ещё нет (или он уже удалён), пробуем вернуть напрямую
    if not order:
        res = fp.refund_order(fp_id)
        if res.get("success"):
            _funpay_send_message(chat_id, "✅ Заявка на возврат принята. Средства вернутся на баланс FunPay.")
            db.update_funpay_session(fp_id, state="refunded", buyer_confirmed=1, support_ticket_sent=1)
        else:
            _funpay_send_message(chat_id, f"❌ Ошибка возврата: {res.get('error')}")
        return

    # 2. Обновляем свежие данные из API, чтобы проверить реальный прогресс
    order = _refresh_order_from_api(order)

    completed = _get_order_completed_quantity(order)
    start_count = int(order.get("api_start_count") or 0)
    status = order.get("status", "")

    # 🛑 БЛОКИРУЕМ ВОЗВРАТ, если заказ уже начал выполняться или выполнен
    # (даже если статус "in_progress", но выполнено 0 — лучше перестраховаться)
    if completed > 0 or start_count > 0 or status in ("in_progress", "completed", "partial"):
        _funpay_send_message(
            chat_id,
            "❌ Возврат невозможен: заказ уже запущен и выполняется (или выполнен).\n\n"
            "Если возникла проблема, напишите в поддержку."
        )
        return

    # ✅ Если выполнение ещё не началось (0 выполнено, 0 стартовало) — разрешаем возврат
    res = fp.refund_order(fp_id)
    if res.get("success"):
        _funpay_send_message(chat_id, "✅ Заявка на возврат принята. Средства вернутся на баланс FunPay.")
        db.update_funpay_session(fp_id, state="refunded", buyer_confirmed=1, support_ticket_sent=1)
        # Обновляем статус заказа в БД
        db.update_order(order["id"], status="cancelled", error_message="Возврат по запросу покупателя")
    else:
        _funpay_send_message(chat_id, f"❌ Ошибка возврата: {res.get('error')}")

def _handle_buyer_reorder_cmd(session):
 
    fp_id = session.get("funpay_order_id")
    db.update_funpay_session(
        fp_id, 
        state="awaiting_link", 
        pending_link="", 
        pending_qty=session.get("pending_qty", 0),
        pending_comments="", 
        pending_split_json="[]"
    )
    _funpay_send_message(session["chat_id"], "🔄 Сессия сброшена. Отправьте новую ссылку для запуска.")

def _handle_buyer_partial_refill_cmd(session):
    fp_id = session.get("funpay_order_id")
    order = db.get_order_by_funpay_order_id(fp_id)
    if not order:
        _funpay_send_message(session.get("chat_id"), "❌ Заказ не найден в базе.")
        return
    
    executed = _get_order_completed_quantity(order)
    total = int(order.get("quantity") or 0)
    remaining = max(0, total - executed)

    if remaining == 0:
        _funpay_send_message(session["chat_id"], "ℹ️ Заказ уже выполнен полностью.")
        return

    # 🔥 Используем провайдер, указанный в заказе
    provider = str(order.get("api_provider") or "twiboost").lower()
    api_client = get_api_client(provider)
    
    if not api_client:
        _funpay_send_message(session["chat_id"], "❌ API провайдера недоступен.")
        return

    res = api_client.refill_order(order["api_order_id"])
    if res.get("success"):
        db.update_order(order["id"], refill_count=order.get("refill_count", 0) + 1)
        _funpay_send_message(session["chat_id"], f"✅ Запрос на дозаказ ({remaining} шт.) отправлен. Ожидайте выполнения.")
    else:
        _funpay_send_message(session["chat_id"], f"❌ Не удалось дозаказать: {res.get('error')}")


def _confirm_funpay_session(session):
    funpay_order_id = session["funpay_order_id"]
    current_session = db.get_funpay_session(funpay_order_id) or session
    existing_order = db.get_order_by_funpay_order_id(funpay_order_id)
    
    # Если заказ уже создан или в процессе — просто показываем статус
    if existing_order or current_session.get("state") in {"creating_order", "order_created", "completed"}:
        if existing_order and not current_session.get("order_id"):
            db.update_funpay_session(funpay_order_id, order_id=existing_order["id"], state="order_created")
            current_session = db.get_funpay_session(funpay_order_id) or current_session
        _send_session_status(current_session, current_session.get("chat_id") or session["chat_id"])
        return

    lot = db.get_lot(current_session["lot_id"]) if current_session.get("lot_id") else None
    if not lot:
        _funpay_send_message(session["chat_id"], "Невозможно обработать заказ: нет привязанного лота. Свяжитесь с поддержкой.")
        return

    split_plan = _session_split_plan(current_session)
    link = current_session.get("pending_link")
    if not link and not split_plan:
        _funpay_send_message(session["chat_id"], "Ссылка не найдена. Пришлите её ещё раз.")
        db.update_funpay_session(funpay_order_id, state="awaiting_link")
        return

    order_mode = _lot_order_mode(lot)
    answer_number = str(current_session.get("pending_answer_number") or "").strip()
    reaction_value = str(current_session.get("pending_reaction") or "").strip()
    split_reactions = _session_split_reactions(current_session)
    
    # 🔥 Извлекаем комментарии: убираем # в начале каждой строки (ОДИН РАЗ)
    raw_comments = current_session.get("pending_comments") or ""
    comment_lines, invalid = _extract_hash_comment_lines(raw_comments)
    if invalid:
        logger.warning(f"Invalid comment lines for order #{funpay_order_id}: {invalid}")
    
    # 🔥 Фолбэк для комментариев: если парсер вернул пусто — пробуем разбить вручную
    if order_mode == "comments" and not comment_lines:
        if raw_comments.strip():
            comment_lines = [line.strip() for line in raw_comments.splitlines() if line.strip()]
        if not comment_lines:
            db.update_funpay_session(funpay_order_id, state="awaiting_comments")
            refreshed = db.get_funpay_session(funpay_order_id) or current_session
            _funpay_send_message(session["chat_id"], _session_comments_prompt_message(refreshed))
            return

    # 🔥 Проверки для разных режимов заказа
    if order_mode == "vote" and not answer_number:
        db.update_funpay_session(funpay_order_id, state="awaiting_vote_answer")
        refreshed = db.get_funpay_session(funpay_order_id) or current_session
        _funpay_send_message(session["chat_id"], _session_vote_prompt_message(refreshed))
        return
    if order_mode == "reaction" and not reaction_value:
        db.update_funpay_session(funpay_order_id, state="awaiting_reaction")
        refreshed = db.get_funpay_session(funpay_order_id) or current_session
        _funpay_send_message(session["chat_id"], _session_reaction_prompt_message(refreshed))
        return
    if order_mode == "reaction" and split_plan and len(split_reactions) != len(split_plan):
        db.update_funpay_session(funpay_order_id, state="awaiting_reaction")
        refreshed = db.get_funpay_session(funpay_order_id) or current_session
        _funpay_send_message(session["chat_id"], _session_reaction_prompt_message(refreshed))
        return

    # 🔥 1. Получаем базовое количество и множитель (с защитой от None)
    try:
        base_qty = int(current_session.get("pending_qty") or lot.get("min_quantity") or 1)
    except (TypeError, ValueError):
        base_qty = 1
    try:
        quantity_per_order = int(lot.get("quantity_per_order") or 1)
    except (TypeError, ValueError):
        quantity_per_order = 1

    # 🔥 Промокод
    promo_code = (current_session.get("promo_code") or "").strip().upper()
    promo_reserved = False
    if promo_code:
        promo, promo_error = _validate_session_promo(current_session, promo_code)
        if not promo:
            _reset_session_promo(funpay_order_id)
            db.update_funpay_session(funpay_order_id, state="awaiting_confirmation")
            refreshed = db.get_funpay_session(funpay_order_id) or current_session
            prompt = _build_review_prompt_message(refreshed) if refreshed.get("pending_link") else _resend_payment_prompt(refreshed)
            _funpay_send_message(session["chat_id"], f"❌ Промокод больше недоступен.\n\nПричина: {promo_error}\n\n{prompt}")
            return
        promo_reserved = db.use_promo(promo_code)
        if not promo_reserved:
            _reset_session_promo(funpay_order_id)
            db.update_funpay_session(funpay_order_id, state="awaiting_confirmation")
            refreshed = db.get_funpay_session(funpay_order_id) or current_session
            prompt = _build_review_prompt_message(refreshed) if refreshed.get("pending_link") else _resend_payment_prompt(refreshed)
            _funpay_send_message(session["chat_id"], "❌ Промокод больше недоступен.\n\nПричина: лимит использований исчерпан или срок действия закончился.\n\n" + prompt)
            return

    db.update_funpay_session(funpay_order_id, state="creating_order")

    # 🔥 3. Формируем финальный план отправки в API
    if split_plan:
         plan = split_plan
    else:
        try:
            base_qty_safe = int(base_qty)
            multiplier = int(quantity_per_order)
        except (TypeError, ValueError):
            base_qty_safe, multiplier = 1, 1
        api_qty = base_qty_safe * multiplier
        api_qty = max(int(lot["min_quantity"]), min(int(lot["max_quantity"]), api_qty))
        plan = [{"link": link, "quantity": api_qty}]

    created_api_order_ids = []
    created_local_order_ids = []
    split_total = len(plan)
    comments_offset = 0

    for idx, item in enumerate(plan, start=1):
        item_reaction = reaction_value
        if order_mode == "reaction" and split_total > 1:
            item_reaction = split_reactions[idx - 1]
        
        item_comments = comment_lines if order_mode == "comments" else None
        if order_mode == "comments" and split_total > 1:
            take = int(item["quantity"] or 0)
            item_comments = comment_lines[comments_offset:comments_offset + take] if comment_lines else []
            comments_offset += take
        if item_comments is None:
            item_comments = []

        # 🔥 Лог ПЕРЕД созданием заказа
        logger.info(
            f"Creating API order: funpay_order_id={funpay_order_id}, lot_id={lot.get('id')}, "
            f"link={item['link'][:50]}, qty={item['quantity']}, mode={order_mode}, "
            f"comments_count={len(item_comments) if item_comments else 0}"
        )

        result = _create_api_order_for_lot(
            lot,
            item["link"],
            item["quantity"],
            answer_number=answer_number,
            reaction_value=item_reaction,
            comments=item_comments,
        )
        
        # 🔥 Логирование для режима комментариев
        if order_mode == "comments":
            if result.get("success"):
                logger.info(
                    f"✅ Comments order | FP:{funpay_order_id} | Lot:{lot.get('id')} | "
                    f"Qty:{item['quantity']} | Comments:{len(item_comments)} | "
                    f"Link:{item['link'][:60]}"
                )
            else:
                logger.error(
                    f"❌ Comments order FAILED | FP:{funpay_order_id} | "
                    f"Error:{result.get('error', 'unknown')}"
                )

        # 🔥 Обработка результата
        if result.get("success"):
            logger.info(
                f"✅ API order created: order_id={result.get('order_id')}, "
                f"funpay_order_id={funpay_order_id}, qty={item['quantity']}"
            )
        else:
            logger.error(
                f"❌ Failed to create API order: {result.get('error')}, "
                f"funpay_order_id={funpay_order_id}, lot_id={lot.get('id')}"
            )
            # Откат уже созданных заказов при ошибке
            for api_order_id in created_api_order_ids:
                try:
                    api.cancel_order(api_order_id)  # ✅ Реальный откат
                    logger.info(f"Rolled back API order {api_order_id}")
                except Exception as e:
                    logger.warning(f"Failed to rollback {api_order_id}: {e}")
            if promo_reserved and promo_code:
                db.rollback_promo_use(promo_code)
            db.update_funpay_session(funpay_order_id, state="awaiting_confirmation")
            refreshed = db.get_funpay_session(funpay_order_id) or current_session
            _funpay_send_message(
                session["chat_id"],
                f"❌ Не удалось создать заказ.\n\nПричина: {result.get('error')}\n\n{_session_confirmation_message_rich(refreshed)}",
            )
            return

        order_id = result.get("order_id")
        if not order_id:
            logger.error(f"API returned success but no order_id. Response: {result}")
            if promo_reserved and promo_code:
                db.rollback_promo_use(promo_code)
            _funpay_send_message(
                session["chat_id"],
                "❌ Заказ не создан: панель не вернула ID заказа. Попробуйте позже или напишите в поддержку."
            )
            db.update_funpay_session(funpay_order_id, state="awaiting_confirmation")
            return
        created_api_order_ids.append(order_id)

        
        # 💰 Финансы
        cost_price = _lot_cost_per_unit(lot) * item["quantity"]
        sell_price = _lot_price_per_unit(lot) * item["quantity"]
        profit = sell_price - cost_price
        
        current_session = db.get_funpay_session(funpay_order_id) or current_session
        order_payload = _session_use_promo_payload(
            current_session,
            lot,
            result,
            item["quantity"],
            cost_price,
            sell_price,
            profit,
            link=item["link"],
            split_index=idx if split_total > 1 else 0,
            split_total=split_total if split_total > 1 else 0,
        )
        order_id = db.add_order(**order_payload)
        created_local_order_ids.append(order_id)
        
        db.add_log("INFO", "funpay", f"Session #{funpay_order_id}: order #{order_id} created")
        _notify_admin_new_order(
            funpay_order_id=funpay_order_id,
            api_order_id=result["order_id"],
            buyer=current_session["buyer_username"],
            service_name=current_session.get("lot_name") or lot["name"],
            quantity=item["quantity"],
            sell_price=sell_price,
            cost_price=cost_price,
            profit=profit,
            link=item["link"],
            state_label=f"Новый заказ запущен{f' (часть {idx}/{split_total})' if split_total > 1 else ''}",
        )

    # 🔥 Финальное обновление сессии
    first_order_id = created_local_order_ids[0] if created_local_order_ids else 0
    db.update_funpay_session(
        funpay_order_id,
        state="order_created",
        order_id=first_order_id,
        buyer_confirmed=0,
        support_ticket_due_at="",
        support_ticket_sent=0,
    )
    current_session = db.get_funpay_session(funpay_order_id) or current_session
    
    # 🔥 Ответ пользователю
    if split_total > 1:
        lines = [
            "✅ Заказы запущены!",
            "",
            f"🧾 FunPay заказ: #{funpay_order_id}",
            f"➗ Частей: {split_total}",
            "",
        ]
        for idx, order_id in enumerate(created_local_order_ids, start=1):
            part = db.get_order(order_id) or {}
            lines.append(
                f"{idx}. Заказ #{order_id} — {part.get('quantity') or 0} — {part.get('link') or '—'}"
            )
        lines.extend([
            "",
            "Команды:",
            "Инфо1 / Инфо2 ... — статус части",
            "Рефил1 / Рефил2 ... — рефилл части",
        ])
        _funpay_send_message(current_session["chat_id"], "\n".join(lines))
    else:
        _funpay_send_message(current_session["chat_id"], _session_text_started(current_session, first_order_id))

def _register_handlers():

    @bot.message_handler(commands=["start", "smm"])
    def cmd_start(m: Message):
        if not is_admin(m.from_user.id):
            if _is_mirror_role():
                bot.reply_to(m, "⛔ Доступ запрещён.")
                return
            _show_mirror_dashboard(m.chat.id, m.from_user)
            return
        if _is_mirror_role():
            text = _mirror_runtime_main_text()
        else:
            text = (
                "╔══════════════════════════╗\n"
                "║  🤖 <b>SMM Auto Bot</b>  ║\n"
                "╚══════════════════════════╝\n\n"
                "Автоматизация SMM бизнеса\n"
                "Выберите раздел:"
            )
        bot.send_message(m.chat.id, text, parse_mode="HTML", reply_markup=kb.main_menu())
        
    @bot.message_handler(commands=["twiboost_cookies"])
    def cmd_twiboost_cookies(m: Message):
        set_state(m.from_user.id, "awaiting_twiboost_cookies")
        bot.reply_to(
            m,
            "🍪 Отправьте строку куки из браузера (F12 → Application → Cookies → twiboost.com).\n\n"
            "Бот автоматически найдёт и сохранит:\n"
            "• panel_users_auth\n"
            "• socpanel_session\n"
            "• XSRF-TOKEN\n\n"
            "Просто скопируйте всю строку Cookie и отправьте сюда одним сообщением.",
            parse_mode="HTML"
        )
    @bot.message_handler(commands=["smmway_cookies"])
    def cmd_smmway_cookies(m: Message):
        if not is_admin(m.from_user.id): return
        set_state(m.from_user.id, "awaiting_smmway_cookies")
        bot.reply_to(
            m,
            "🌐 Отправьте строку куки для SmmWay.ru (F12 → Application → Cookies → smmway.ru).\n\n"
            "Бот автоматически найдёт и сохранит:\n"
            "• panel_users_auth\n"
            "• XSRF-TOKEN\n\n"
            "Просто скопируйте всю строку Cookie и отправьте сюда одним сообщением.",
            parse_mode="HTML"
        )
    @bot.message_handler(commands=["balance"])
    def cmd_balance(m: Message):
        if not is_admin(m.from_user.id):
            return
        _show_balance(m.chat.id)

    @bot.message_handler(commands=["stats"])
    def cmd_stats(m: Message):
        if not is_admin(m.from_user.id):
            return
        _show_stats(m.chat.id, days=None)
        
    @bot.message_handler(commands=["settings", "настройки"])
    def cmd_settings(m: Message):
        if not is_admin(m.from_user.id):
            return

        def mask(val, show=4):
            val = str(val or "").strip()
            if not val or val == " ": return "❌ Не задано"
            if len(val) <= show * 2: return val
            return f"{val[:show]}...{val[-show:]}"

        web_cfg = cfg.get("twiboost_web", {})
        api_key = str(cfg.twiboost_api_key or "").strip()
        fp_key = str(cfg.funpay_golden_key or "").strip()

        lines = [
            "⚙️ <b>Текущие настройки бота</b>",
            "━" * 30,
            f"🌐 Web Fallback: {'✅ Вкл' if web_cfg.get('enabled') else '❌ Выкл'}",
            f"🍪 Cookies: {mask(web_cfg.get('cookies'))}",
            f"🔑 XSRF-Token: {mask(web_cfg.get('xsrf_token'))}",
            f"🔗 Orders URL: {web_cfg.get('orders_url', 'не задан')}",
            "",
            f"🔑 TwiBoost API: ...{api_key[-4:] if api_key else '❌'}",
            f"🎮 FunPay Key: ...{fp_key[-4:] if fp_key else '❌'}",
            f"💱 Курс USD/RUB: {cfg.get('usd_rub_rate', 92)}₽",
            f"⏱ Интервал проверки: {cfg.get('order_check_interval', 60)} сек",
            "━" * 30,
            "💡 <i>Для обновления кук: /twiboost_cookies</i>"
        ]
        bot.reply_to(m, "\n".join(lines), parse_mode="HTML")

    # ==================== ТЕКСТОВЫЙ ВВОД ====================

    @bot.message_handler(func=lambda m: m.from_user.id in user_states or (db is not None and db.get_user_state(m.from_user.id) is not None))
    def handle_text_input(m: Message):
        # ✅ 1. Глобальные и локальные переменные
        global api, fp
        chat_id = m.chat.id  # <--- Объявляем chat_id, чтобы не было ошибок
        uid = m.from_user.id
        st = get_state(uid)
        state = st.get("state", " ")
        data = st.get("data", {})

        # ✅ 2. Проверки
        if _handle_mirror_text_input(m):
            return
        if not is_admin(uid):
            return

        # ✅ 3. Обработка куки
        if state == "awaiting_twiboost_cookies":
            clear_state(uid)
            raw = m.text.strip()
            raw = raw.replace(" & ", "; ").replace("\n", "; ").replace("\r", " ")
            pairs = [p.strip() for p in raw.split("; ") if "=" in p and p.strip()]
            cleaned = "; ".join(pairs)

            if not cleaned or "panel_users_auth=" not in cleaned.lower():
                bot.send_message(chat_id, "❌ Не удалось найти ключевые куки. Убедитесь, что в строке есть `panel_users_auth`. Скопируйте куки заново через F12.")
                return

            if is_admin(uid):
                cfg.set("twiboost_web.cookies", cleaned)
                if api:
                    api.web_config["cookies"] = cleaned
                    api.web_config["enabled"] = bool(api.web_config.get("xsrf_token"))
                bot.send_message(
                    chat_id,
                    "✅ <b>Куки сохранены для основного бота!</b>\n\n"
                    "Теперь бот будет автоматически подтягивать свежий XSRF-TOKEN при каждом запросе.\n"
                    "Основная сессия (panel_users_auth) будет работать ~3-7 дней.",
                    parse_mode="HTML"
                )
            else:
                mirror = db.get_mirror_user(uid) if db else None
                if mirror:
                    settings = json.loads(mirror.get("settings_json", "{}") or "{}")
                    settings["twiboost_cookies"] = cleaned
                    db.update_mirror_user(mirror["id"], settings_json=json.dumps(settings, ensure_ascii=False))
                    bot.send_message(
                    chat_id,
                        "✅ <b>Куки сохранены для вашего зеркала!</b>\n\n"
                        "Они применятся при следующем запуске или перезагрузке инстанса.",
                        parse_mode="HTML"
                    )
                else:
                    cfg.set(f"user_cookies.{uid}", cleaned)
                    bot.send_message(chat_id, "✅ Куки сохранены в локальном кэше.", parse_mode="HTML")
            return  # ⚠️ Важно: возвращаем управление
        # --- НАСТРОЙКИ ---
        elif state == "awaiting_smmway_cookies":
            clear_state(uid)
            raw = m.text.strip()
        
        # 🔥 Унифицируем разделители: переводим всё в "; "
            raw = re.sub(r'[\n\r\t]+', ';', raw)
            raw = raw.replace('&', ';').replace(';', '; ')
            raw = re.sub(r';\s*;+', ';', raw).strip('; ')

        # Собираем валидные пары key=value
            pairs = [p.strip() for p in raw.split(';') if '=' in p and p.strip()]
            cleaned = '; '.join(pairs)

        # 🔍 Проверяем наличие ключа (игнорируем пробелы вокруг =)
            check_str = cleaned.lower().replace(' ', '')
            if not cleaned or "panel_users_auth=" not in check_str:
                bot.send_message(chat_id, "❌ Не удалось найти ключевые куки. Убедитесь, что скопировали строку целиком из вкладки Application → Cookies → smmway.ru")
                return

            # 💾 Сохраняем в конфиг
            cfg.set("smmway_web.cookies", cleaned)
        
            # 🔄 Сбрасываем кэш клиента, чтобы подтянулись новые куки
            if "smmway" in _api_clients:
                del _api_clients["smmway"]
            
        # Автоматически включаем web-фолбэк
            if "smmway_web" not in cfg._data:
                cfg._data["smmway_web"] = {}
            cfg._data["smmway_web"]["cookies"] = cleaned
            cfg._data["smmway_web"]["enabled"] = True

            bot.send_message(
                chat_id, 
                "✅ <b>Куки SmmWay успешно сохранены!</b>\n\n"
                "Бот будет использовать их для резервных запросов через Web-интерфейс.", 
                parse_mode="HTML", 
                reply_markup=kb.back("settings")
            )
            return
    
        if state == "set_api_key":
            # clear_state(uid) is delayed until promo uses are entered
            cfg.set("twiboost_api_key", m.text.strip())
            api = TwiBoostAPI(cfg.twiboost_api_key, cfg.twiboost_api_url, cfg.get("twiboost_web", {}))
            r = api.test_connection()
            if r["success"]:
                balance_text, _ = _format_twiboost_balance(r["balance"], r.get("currency", "USD"))
                bot.send_message(m.chat.id, f"✅ API ключ сохранён!\n💰 Баланс: <b>{balance_text}</b>", parse_mode="HTML", reply_markup=kb.back("settings"))
            else:
                bot.send_message(m.chat.id, f"⚠️ Ключ сохранён, но тест не прошёл:\n{r['error']}", reply_markup=kb.back("settings"))
                
        elif state == "set_smmway_key":
            clear_state(uid)
            cfg.set("smmway_api_key", m.text.strip())
            client = get_api_client("smmway")
            r = client.get_balance() if client else {"success": False}
            if r.get("success"):
                bot.send_message(m.chat.id, f"✅ Ключ SmmWay сохранён!\n💰 Баланс: {r['balance']} {r.get('currency')}", parse_mode="HTML", reply_markup=kb.back("settings"))
            else:
                bot.send_message(m.chat.id, "⚠️ Ключ сохранён, но тест не прошёл.", reply_markup=kb.back("settings"))

        elif state == "fpw_wallet":
            clear_state(uid)
            cfg.set("funpay_withdraw.wallet", m.text.strip())
            bot.send_message(m.chat.id, "✅ Телефон для вывода сохранён.", reply_markup=kb.back("fp_withdraw"))

        elif state == "fpw_wallet_extra":
            clear_state(uid)
            bank_code, bank_label = _resolve_funpay_bank_input(m.text.strip())
            cfg.set("funpay_withdraw.wallet_extra", bank_code)
            if bank_label:
                bot.send_message(m.chat.id, f"✅ Банк сохранён: {bank_label} ({bank_code})", reply_markup=kb.back("fp_withdraw"))
            else:
                bot.send_message(m.chat.id, f"✅ Код банка сохранён: {bank_code}", reply_markup=kb.back("fp_withdraw"))

        elif state == "fpw_amount":
            clear_state(uid)
            try:
                amount = int(re.search(r"(\d+)", m.text or "").group(1))
            except Exception:
                bot.send_message(m.chat.id, "❌ Введите сумму числом.", reply_markup=kb.back("fp_withdraw"))
                return
            if amount <= 0:
                bot.send_message(m.chat.id, "❌ Сумма должна быть больше нуля.", reply_markup=kb.back("fp_withdraw"))
                return
            cfg.set("funpay_withdraw.amount_int", amount)
            bot.send_message(m.chat.id, f"✅ Сумма сохранена: {amount} ₽", reply_markup=kb.back("fp_withdraw"))

        elif state == "fpw_2fa":
            clear_state(uid)
            value = m.text.strip()
            cfg.set("funpay_withdraw.twofactor_code", "" if value == "0" else value)
            bot.send_message(m.chat.id, "✅ 2FA код сохранён.", reply_markup=kb.back("fp_withdraw"))

        elif state == "fpw_auto_min":
            clear_state(uid)
            try:
                amount = int(re.search(r"(\d+)", m.text or "").group(1))
            except Exception:
                bot.send_message(m.chat.id, "❌ Введите сумму порога числом.", reply_markup=kb.back("fp_withdraw"))
                return
            if amount < 0:
                bot.send_message(m.chat.id, "❌ Порог не может быть отрицательным.", reply_markup=kb.back("fp_withdraw"))
                return
            cfg.set("funpay_withdraw.auto_min_balance", amount)
            bot.send_message(m.chat.id, f"✅ Порог автовывода сохранён: {amount} ₽", reply_markup=kb.back("fp_withdraw"))

        elif state == "set_usd_rate":
            clear_state(uid)
            try:
                rate = float(m.text.strip())
                cfg.set("usd_rub_rate", rate)
                bot.send_message(m.chat.id, f"✅ Курс: <b>1 USD = {rate}₽</b>", parse_mode="HTML", reply_markup=kb.back("settings"))
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите число.", reply_markup=kb.back("settings"))

        elif state == "set_check_interval":
            clear_state(uid)
            try:
                val = int(m.text.strip())
                cfg.set("order_check_interval", val)
                bot.send_message(m.chat.id, f"✅ Интервал: <b>{val} сек</b>", parse_mode="HTML", reply_markup=kb.back("settings"))
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите число.", reply_markup=kb.back("settings"))

        elif state == "set_low_balance":
            clear_state(uid)
            try:
                val = float(m.text.strip())
                cfg.set("low_balance_threshold", val)
                bot.send_message(m.chat.id, f"✅ Порог: <b>${val}</b>", parse_mode="HTML", reply_markup=kb.back("settings"))
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите число.", reply_markup=kb.back("settings"))

        elif state == "set_golden_key":
            clear_state(uid)
            key = m.text.strip()
            cfg.set("funpay_golden_key", key)
            global fp
            fp = FunPayClient(key)
            r = fp.test_connection()
            if r["success"]:
                bot.send_message(m.chat.id, f"✅ Golden Key сохранён!\n\n👤 <b>{r['username']}</b>\n🆔 {r['user_id']}\n💰 {r['balance']}₽", parse_mode="HTML", reply_markup=kb.back("settings"))
            else:
                bot.send_message(m.chat.id, f"⚠️ Ключ сохранён, но тест не прошёл:\n{r['error']}", reply_markup=kb.back("settings"))

        elif state == "set_fp_interval":
            clear_state(uid)
            try:
                val = int(m.text.strip())
                cfg.set("funpay_check_interval", val)
                bot.send_message(m.chat.id, f"✅ Интервал FunPay: <b>{val} сек</b>", parse_mode="HTML", reply_markup=kb.back("settings"))
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите число.", reply_markup=kb.back("settings"))

        elif state == "kb_set_greeting":
            clear_state(uid)
            text = m.text.strip()
            if not text:
                bot.send_message(m.chat.id, "❌ Текст не должен быть пустым.", reply_markup=kb.back("kb_settings"))
                return
            cfg.set("knowledge_base.greeting_text", text)
            bot.send_message(m.chat.id, "✅ Приветствие сохранено.", reply_markup=kb.back("kb_settings"))

        elif state == "kb_add_title":
            title = m.text.strip()
            if not title:
                bot.send_message(m.chat.id, "❌ Название не должно быть пустым.", reply_markup=kb.back("kb_settings"))
                return
            set_state(uid, "kb_add_triggers", title=title)
            bot.send_message(
                m.chat.id,
                "Шаг 2/3. Отправьте триггеры через запятую или с новой строки.\n\n"
                "Например:\n<code>привет, здравствуйте, hello</code>",
                parse_mode="HTML",
                reply_markup=kb.back("kb_settings"),
            )

        elif state == "kb_add_triggers":
            raw = str(m.text or "").replace(";", "\n").replace(",", "\n")
            triggers = []
            seen = set()
            for item in raw.splitlines():
                trigger = str(item or "").strip()
                normalized = _normalized_command_text(trigger)
                if trigger and normalized and normalized not in seen:
                    seen.add(normalized)
                    triggers.append(trigger)
            if not triggers:
                bot.send_message(m.chat.id, "❌ Нужен хотя бы один триггер.", reply_markup=kb.back("kb_settings"))
                return
            set_state(uid, "kb_add_reply", title=data.get("title") or "Новая запись", triggers=triggers)
            bot.send_message(
                m.chat.id,
                "Шаг 3/3. Отправьте текст ответа, который бот будет писать покупателю.",
                reply_markup=kb.back("kb_settings"),
            )

        elif state == "kb_add_reply":
            reply = m.text.strip()
            if not reply:
                bot.send_message(m.chat.id, "❌ Ответ не должен быть пустым.", reply_markup=kb.back("kb_settings"))
                return
            entries = list(_knowledge_entries())
            entries.append({
                "title": str(data.get("title") or "Новая запись").strip(),
                "triggers": list(data.get("triggers") or []),
                "reply": reply,
            })
            _save_knowledge_entries(entries)
            clear_state(uid)
            bot.send_message(m.chat.id, "✅ Запись базы знаний добавлена.", reply_markup=kb.back("kb_settings"))

        # --- ПРИВЯЗКИ ЛОТОВ (FunPay лот → бот лот) ---
        elif state == "fp_send_msg":
            clear_state(uid)
            fp_order_id = data.get("fp_order_id", "")
            if fp and fp_order_id:
                r = fp.get_order_details(fp_order_id)
                if r["success"] and (r.get("chat_id") or r.get("buyer_id")):
                    chat_id_fp = r.get("chat_id") or fp.get_chat_id_by_username(r["buyer_id"])
                    send_r = fp.send_message(chat_id_fp, m.text.strip(), chat_name=r.get("buyer_username"))
                    if send_r["success"]:
                        bot.send_message(m.chat.id, f"✅ Сообщение отправлено покупателю <b>{r['buyer_username']}</b>", parse_mode="HTML", reply_markup=kb.back("fp_sales"))
                    else:
                        bot.send_message(m.chat.id, f"❌ Ошибка: {send_r['error']}", reply_markup=kb.back("fp_sales"))
                else:
                    bot.send_message(m.chat.id, "❌ Не удалось получить данные заказа.", reply_markup=kb.back("fp_sales"))
            else:
                bot.send_message(m.chat.id, "❌ FunPay не подключен.", reply_markup=kb.back("funpay"))

        # --- ЛОТЫ ---
        elif state == "lotfp_node":
            lot_id = int(data.get("lot_id") or 0)
            lot = db.get_lot(lot_id) if db and lot_id else None
            if not lot:
                clear_state(uid)
                bot.send_message(m.chat.id, "❌ Лот не найден.", reply_markup=kb.lots_menu())
                return
            try:
                node_id = int(m.text.strip())
                if node_id <= 0:
                    raise ValueError()
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите числовой node ID, например 703.", reply_markup=kb.back(data.get("back_callback") or f"lot_{lot_id}"))
                return
            if category := _find_funpay_category_by_id(node_id):
                clear_state(uid)
                bot.send_message(
                    m.chat.id,
                    f"📂 <b>{html.escape(str(category.get('name') or 'Категория'))}</b>\n\nВыберите подкатегорию:",
                    parse_mode="HTML",
                    reply_markup=_kb_funpay_subcategories(
                        lot_id,
                        int(category.get("id") or 0),
                        category.get("subcategories") or [],
                        page=0,
                    ),
                )
                return
            _begin_funpay_offer_node(
                m.chat.id,
                uid,
                lot_id,
                node_id,
                back_callback=data.get("back_callback") or f"lot_{lot_id}",
            )
            return

        elif state == "lotfp_field":
            lot_id = int(data.get("lot_id") or 0)
            lot = db.get_lot(lot_id) if db and lot_id else None
            if not lot:
                clear_state(uid)
                bot.send_message(m.chat.id, "❌ Лот не найден.", reply_markup=kb.lots_menu())
                return
            schema = data.get("form_schema", [])
            field_values = dict(data.get("field_values") or {})
            field_id = str(data.get("current_field_id") or "").strip()
            current_field = next((f for f in schema if str(f.get("id")) == field_id), None)
            value = m.text.strip()
            if not current_field:
                clear_state(uid)
                bot.send_message(m.chat.id, "❌ Состояние формы потеряно. Начните заново.", reply_markup=kb.back(f"lot_edit_{lot_id}"))
                return
            options = current_field.get("options") or []
            if options:
                matched = None
                for option in options:
                    raw_value = str(option.get("value") or "").strip()
                    raw_label = str(option.get("label") or "").strip()
                    if value.lower() == raw_value.lower() or value.lower() == raw_label.lower():
                        matched = raw_value
                        break
                if not matched:
                    bot.send_message(m.chat.id, "❌ Введите одно из значений из списка.", reply_markup=kb.back(f"lot_edit_{lot_id}"))
                    return
                value = matched
            field_values[field_id] = value
            _advance_funpay_offer_field_flow(
                m.chat.id,
                uid,
                lot_id,
                int(data.get("node_id") or 0),
                schema,
                field_values,
                data.get("defaults", {}),
                data.get("back_callback") or f"lot_{lot_id}",
            )
            return

        elif state == "lotfp_summary_ru":
            defaults = dict(data.get("defaults") or {})
            text_value = m.text.strip()
            if text_value not in {"", "-"}:
                defaults["summary_ru"] = text_value
            set_state(uid, "lotfp_desc_ru", **{**data, "defaults": defaults})
            bot.send_message(
                m.chat.id,
                "📄 <b>Подробное описание RU</b>\n\n"
                f"Текущее значение:\n<code>{html.escape(str(defaults.get('desc_ru') or ''))}</code>\n\n"
                "Отправьте новый текст или <b>-</b>, чтобы оставить автоматически.",
                parse_mode="HTML",
                reply_markup=kb.back(data.get("back_callback") or f"lot_{int(data.get('lot_id') or 0)}"),
            )
            return

        elif state == "lotfp_desc_ru":
            defaults = dict(data.get("defaults") or {})
            text_value = m.text.strip()
            if text_value not in {"", "-"}:
                defaults["desc_ru"] = text_value
            set_state(uid, "lotfp_payment_ru", **{**data, "defaults": defaults})
            bot.send_message(
                m.chat.id,
                "💬 <b>Сообщение покупателю после оплаты (RU)</b>\n\n"
                f"Текущее значение:\n<code>{html.escape(str(defaults.get('payment_msg_ru') or ''))}</code>\n\n"
                "Отправьте текст или <b>-</b>, чтобы оставить пустым.",
                parse_mode="HTML",
                reply_markup=kb.back(data.get("back_callback") or f"lot_{int(data.get('lot_id') or 0)}"),
            )
            return

        elif state == "lotfp_payment_ru":
            defaults = dict(data.get("defaults") or {})
            text_value = m.text.strip()
            defaults["payment_msg_ru"] = "" if text_value in {"", "-"} else text_value
            lot_id = int(data.get("lot_id") or 0)
            set_state(uid, "lotfp_en_mode", **{**data, "defaults": defaults})
            bot.send_message(
                m.chat.id,
                "🌍 <b>Английские тексты</b>\n\n"
                "Выберите режим:\n"
                "• авто-перевод\n"
                "• вручную\n"
                "• взять такие же, как RU",
                parse_mode="HTML",
                reply_markup=_kb_funpay_en_mode(lot_id),
            )
            return

        elif state == "lotfp_summary_en":
            defaults = dict(data.get("defaults") or {})
            text_value = m.text.strip()
            if text_value not in {"", "-"}:
                defaults["summary_en"] = text_value
            set_state(uid, "lotfp_desc_en", **{**data, "defaults": defaults})
            bot.send_message(
                m.chat.id,
                "📄 <b>Подробное описание EN</b>\n\n"
                "Отправьте текст или <b>-</b>, чтобы оставить авто-вариант.",
                parse_mode="HTML",
                reply_markup=kb.back(data.get("back_callback") or f"lot_{int(data.get('lot_id') or 0)}"),
            )
            return

        elif state == "lotfp_desc_en":
            defaults = dict(data.get("defaults") or {})
            text_value = m.text.strip()
            if text_value not in {"", "-"}:
                defaults["desc_en"] = text_value
            set_state(uid, "lotfp_payment_en", **{**data, "defaults": defaults})
            bot.send_message(
                m.chat.id,
                "💬 <b>Сообщение покупателю после оплаты (EN)</b>\n\n"
                "Отправьте текст или <b>-</b>, чтобы оставить пустым.",
                parse_mode="HTML",
                reply_markup=kb.back(data.get("back_callback") or f"lot_{int(data.get('lot_id') or 0)}"),
            )
            return

        elif state == "lotfp_payment_en":
            defaults = dict(data.get("defaults") or {})
            text_value = m.text.strip()
            defaults["payment_msg_en"] = "" if text_value in {"", "-"} else text_value
            set_state(uid, "lotfp_price", **{**data, "defaults": defaults})
            bot.send_message(
                m.chat.id,
                f"💰 <b>Цена лота на FunPay</b>\n\nРекомендуемая цена: <b>{float(defaults.get('price', 0) or 0):.2f}₽</b>\nВведите цену за 1 шт. на FunPay.",
                parse_mode="HTML",
                reply_markup=kb.back(data.get("back_callback") or f"lot_{int(data.get('lot_id') or 0)}")
            )
            return

        elif state == "lotfp_price":
            lot_id = int(data.get("lot_id") or 0)
            lot = db.get_lot(lot_id) if db and lot_id else None
            if not lot:
                clear_state(uid)
                bot.send_message(m.chat.id, "❌ Лот не найден.", reply_markup=kb.lots_menu())
                return
            try:
                price = float(m.text.strip().replace(",", "."))
                if price <= 0:
                    raise ValueError()
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите цену числом, например 100 или 149.9.", reply_markup=kb.back(data.get("back_callback") or f"lot_{lot_id}"))
                return
            defaults = dict(data.get("defaults", {}))
            defaults["price"] = price
            set_state(uid, "lotfp_amount", lot_id=lot_id, node_id=data.get("node_id"), form_schema=data.get("form_schema", []), field_values=data.get("field_values", {}), defaults=defaults, back_callback=data.get("back_callback") or f"lot_{lot_id}")
            bot.send_message(
                m.chat.id,
                f"📦 <b>Наличие лота</b>\n\nРекомендуемое значение: <b>{int(defaults.get('amount', 100) or 100)}</b>\nВведите количество пакетов в наличии.",
                parse_mode="HTML",
                reply_markup=kb.back(data.get("back_callback") or f"lot_{lot_id}")
            )
            return

        elif state == "lotfp_amount":
            lot_id = int(data.get("lot_id") or 0)
            lot = db.get_lot(lot_id) if db and lot_id else None
            if not lot:
                clear_state(uid)
                bot.send_message(m.chat.id, "❌ Лот не найден.", reply_markup=kb.lots_menu())
                return
            try:
                amount = int(m.text.strip())
                if amount < 0:
                    raise ValueError()
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите целое число 0 или больше.", reply_markup=kb.back(data.get("back_callback") or f"lot_{lot_id}"))
                return
            if not fp:
                clear_state(uid)
                bot.send_message(m.chat.id, "❌ FunPay не подключен.", reply_markup=kb.back(data.get("back_callback") or f"lot_{lot_id}"))
                return
            defaults = dict(data.get("defaults", {}))
            defaults["amount"] = amount
            result = fp.create_offer(
                int(data.get("node_id") or 0),
                field_values=data.get("field_values", {}),
                price=defaults.get("price", 0),
                amount=amount,
                summary_ru=defaults.get("summary_ru", lot.get("name", "")),
                summary_en=defaults.get("summary_en", lot.get("name", "")),
                desc_ru=defaults.get("desc_ru", lot.get("name", "")),
                desc_en=defaults.get("desc_en", lot.get("name", "")),
                payment_msg_ru=defaults.get("payment_msg_ru", ""),
                payment_msg_en=defaults.get("payment_msg_en", ""),
                active=True,
            )
            clear_state(uid)
            if not result.get("success"):
                bot.send_message(
                    m.chat.id,
                    f"❌ Не удалось создать лот на FunPay.\n\nПричина: {html.escape(result.get('error', 'unknown'))}",
                    parse_mode="HTML",
                    reply_markup=kb.back(data.get("back_callback") or f"lot_{lot_id}")
                )
                return
            offer_id = str(result.get("offer_id") or "").strip()
            title = result.get("title") or defaults.get("summary_ru") or lot.get("name") or ""
            updates = {}
            if offer_id:
                updates["funpay_lot_id"] = offer_id
            if title:
                updates["funpay_lot_name"] = title
            updates["is_active"] = 1
            if updates:
                db.update_lot(lot_id, **updates)
            bot.send_message(
                m.chat.id,
                "✅ <b>Лот создан на FunPay</b>\n\n"
                f"🛒 Бот-лот: <b>#{lot_id}</b>\n"
                f"🎯 FunPay ID: <b>{html.escape(offer_id or 'не найден')}</b>\n"
                f"📝 Название: {html.escape(str(title))}\n"
                f"💰 Цена: <b>{float(defaults.get('price', 0) or 0):.2f}₽</b>\n"
                f"📦 Наличие: <b>{amount}</b>",
                parse_mode="HTML",
                reply_markup=kb.lot_item(lot_id)
            )
            return

        elif state == "lot_add_name":
            enabled = get_enabled_providers()
            if len(enabled) > 1:
                set_state(uid, "lot_add_provider", name=m.text.strip())
                markup = telebot.types.InlineKeyboardMarkup(row_width=2)
                markup.add(telebot.types.InlineKeyboardButton("🌐 TwiBoost", callback_data="lotprov_twiboost"))
                markup.add(telebot.types.InlineKeyboardButton("🌐 SmmWay", callback_data="lotprov_smmway"))
                bot.send_message(m.chat.id, "🌐 Выберите сервис для накрутки (нажмите кнопку):", reply_markup=markup)
                return
        
            default_prov = enabled[0] if enabled else "twiboost"
            set_state(uid, "lot_add_service", name=m.text.strip(), api_provider=default_prov)
            bot.send_message(m.chat.id, f"🔢 Введите <b>ID сервиса</b> {default_prov}: ", parse_mode="HTML", reply_markup=kb.back("lots"))
        
        elif state == "lot_add_name_v3":
            name = m.text.strip()
            order_mode = str(data.get("order_mode") or "normal").strip().lower()
        
        # 🔥 Проверяем, сколько провайдеров подключено
            enabled = get_enabled_providers()
            if len(enabled) > 1:
                markup = telebot.types.InlineKeyboardMarkup(row_width=2)
                for prov in enabled:
                    label = "🌐 TwiBoost" if prov == "twiboost" else "🌐 SmmWay"
                    markup.add(telebot.types.InlineKeyboardButton(label, callback_data=f"lotprov_{prov}"))
            
                set_state(uid, "lot_add_provider", name=name, order_mode=order_mode)
                bot.send_message(m.chat.id, "🌐 Выберите сервис для накрутки этого лота:", reply_markup=markup)
                return

        # Если провайдер один — идем дальше по стандартному сценарию
            default_prov = enabled[0] if enabled else "twiboost"
        
            if order_mode == "reaction":
                set_state(uid, "lot_add_quantity_v3", name=name, order_mode=order_mode, api_provider=default_prov)
                bot.send_message(
                    m.chat.id,
                     "📊  <b >Количество для одного заказа </b >\n\n "
                     "Например:\n "
                     "• 1000 просмотров\n "
                     "• 500 реакций\n "
                     "• 10 комментариев ",
                    parse_mode= "HTML ",
                    reply_markup=kb.back( "lots "),
                )
            else:
                set_state(uid, "lot_add_service_v3", name=name, order_mode=order_mode, api_provider=default_prov)
                bot.send_message(m.chat.id, f"🔢 Введите <b>ID сервиса</b> {default_prov}: ", parse_mode= "HTML ", reply_markup=kb.back( "lots "))
            return

        elif state == "lot_add_service_v3":
            try:
                svc_id = int(m.text.strip())
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите число — ID сервиса.", reply_markup=kb.back("lots"))
                return
            svc = _get_twiboost_service_by_id(svc_id)
            if not svc:
                bot.send_message(m.chat.id, "❌ Сервис не найден в TwiBoost.", reply_markup=kb.back("lots"))
                return
            preview = (
                f"📝 {svc.get('name') or ('Сервис #' + str(svc_id))}\n"
                f"📂 {svc.get('category') or '—'}\n"
                f"🏷 Тип: {svc.get('type') or '—'}\n"
                f"💰 Цена: {svc.get('rate') or 0}₽/1000\n"
                f"📊 {svc.get('min_order') or 0} — {svc.get('max_order') or 0}\n"
                f"🔁 Рефилл: {'✅' if svc.get('refill') else '❌'}\n"
                f"🚫 Отмена: {'✅' if svc.get('cancel') else '❌'}"
            )
            set_state(
                uid,
                "lot_add_quantity_v3",
                name=data["name"],
                order_mode=data.get("order_mode") or "normal",
                service_id=svc_id,
                service=svc,
            )
            bot.send_message(
                m.chat.id,
                f"{preview}\n\n📊 <b>Количество для одного заказа</b>\n\nНапример 1000 или 500.",
                parse_mode="HTML",
                reply_markup=kb.back("lots"),
            )
            return

        elif state == "lot_add_quantity_v3":
            try:
                quantity_per_order = int(m.text.strip())
                if quantity_per_order <= 0:
                    raise ValueError()
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите положительное число.", reply_markup=kb.back("lots"))
                return
            payload = dict(data)
            payload["quantity_per_order"] = quantity_per_order

            # 🔥 Если подключено >1 провайдера и он ещё не выбран — спрашиваем
            enabled = get_enabled_providers()
            if len(enabled) > 1 and not payload.get("api_provider"):
                set_state(uid, "lot_add_provider", **payload)
                markup = telebot.types.InlineKeyboardMarkup(row_width=2)
                markup.add(telebot.types.InlineKeyboardButton("🌐 TwiBoost", callback_data="lotprov_twiboost"))
                markup.add(telebot.types.InlineKeyboardButton("🌐 SmmWay", callback_data="lotprov_smmway"))
                bot.send_message(m.chat.id, "🌐 Выберите провайдера для этого лота:", reply_markup=markup)
                return

            # По умолчанию используем TwiBoost, если провайдер не указан или он один
            payload.setdefault("api_provider", "twiboost")

            lot_id = _create_lot_draft_from_payload(payload)
            bot.send_message(
                m.chat.id,
                "✅ <b>Черновик лота создан</b>\n\n"
                f"🛒 Лот: <b>#{lot_id}</b>\n"
                f"📝 Название: {html.escape(payload['name'])}\n"
                f"🗳 Режим: {_lot_order_mode_title(payload.get('order_mode'))}\n\n"
                "Теперь оформим карточку на FunPay.",
                parse_mode="HTML",
            )
            _start_funpay_offer_create(m.chat.id, None, uid, lot_id, back_callback="lots")
            return

        elif state == "lot_add_service":
            try:
                svc_id = int(m.text.strip())
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите число — ID сервиса.")
                return

            prov = data.get("api_provider", "twiboost")
            if prov == "smmway":
                svc = _get_smmway_service_by_id(svc_id)
            else:
                svc = _get_twiboost_service_by_id(svc_id)

            if not svc:
                bot.send_message(m.chat.id, f"❌ Сервис не найден в {prov}.", reply_markup=kb.back("lots"))
                return

            preview = (
                f"📝 {svc.get('name') or ('Сервис #' + str(svc_id))}\n"
                f"📂 {svc.get('category') or '—'}\n"
                f"🏷 Тип: {svc.get('type') or '—'}\n"
                f"💰 Цена: {svc.get('rate') or 0}₽/1000\n"
                f"📊 {svc.get('min_order') or 0} — {svc.get('max_order') or 0}\n"
                f"🔁 Рефилл: {'✅' if svc.get('refill') else '❌'}\n"
                f"🚫 Отмена: {'✅' if svc.get('cancel') else '❌'}"
            )
            lots, preview_lots = _get_funpay_lots_preview()
            set_state(uid, "lot_add_funpay_lot", name=data["name"], service_id=svc_id, service=svc, funpay_lots=lots, api_provider=prov)
            prompt = "🎯 Введите <b>ID лота FunPay</b> для привязки к этой услуге."
            if preview_lots:
                prompt += f"\n\nДоступные лоты:\n{preview_lots}"
            bot.send_message(m.chat.id, f"{preview}\n\n{prompt}", parse_mode="HTML", reply_markup=kb.back("lots"))
        elif state == "lot_add_funpay_lot":
            svc = data["service"]
            funpay_id = m.text.strip()
            if not funpay_id:
                bot.send_message(m.chat.id, "❌ Введите ID лота FunPay (число).", reply_markup=kb.back("lots"))
                return
            funpay_name = ""
            funpay_price = 0
            selected_funpay_lot = None
            if data.get("funpay_lots"):
                for lot in data["funpay_lots"]:
                    if str(lot.get("offer_id")) == funpay_id:
                        selected_funpay_lot = lot
                        funpay_name = lot.get("title", "")
                        funpay_price = lot.get("price", 0)
                        break
                if selected_funpay_lot and int(selected_funpay_lot.get("subcategory_id") or 0) > 0:
                    cfg.add_funpay_category_preset(
                        data["service_id"],
                        {
                            "category_name": selected_funpay_lot.get("category") or "",
                            "subcategory_name": selected_funpay_lot.get("category") or "",
                            "subcategory_id": int(selected_funpay_lot.get("subcategory_id") or 0),
                        },
                    )
            
            # Ask for quantity multiplier
            payload = data.copy()
            payload["funpay_lot_id"] = funpay_id
            payload["funpay_lot_name"] = funpay_name
            payload["funpay_price"] = funpay_price
            set_state(uid, "lot_add_quantity", **payload)
            
            bot.send_message(
                m.chat.id, 
                f"📊 <b>Количество для заказа</b>\n\n"
                f"FunPay лот: {funpay_id} {funpay_name or ''}\n"
                f"Цена на FunPay: {funpay_price}₽\n\n"
                f"Введите количество для одного заказа на FunPay:\n"
                f"• <b>1</b> - если цена на FunPay за 1 единицу\n"
                f"• <b>100</b> - если цена на FunPay за 100 единиц\n"
                f"• <b>1000</b> - если цена на FunPay за 1000 единиц\n\n"
                f"Это нужно чтобы правильно рассчитать количество при заказах.",
                parse_mode="HTML", 
                reply_markup=kb.back("lots")
            )

        elif state == "lot_add_quantity_v2":
            pass

        elif state == "lot_add_quantity":
            try:
                quantity_per_order = int(m.text.strip())
                if quantity_per_order <= 0:
                    raise ValueError()
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите положительное число (1, 100, 1000 и т.д.).", reply_markup=kb.back("lots"))
                return

            funpay_price = data["funpay_price"]
            price_per_unit = funpay_price / quantity_per_order if quantity_per_order > 0 else 0

            payload = data.copy()
            payload["quantity_per_order"] = quantity_per_order
            payload["price_per_unit"] = price_per_unit
            payload["price_mode"] = "fixed"
            payload["price_input"] = price_per_unit
            svc = payload["service"]

            set_state(uid, "lot_add_mode", **payload)
            bot.send_message(
                m.chat.id,
                "🗳 <b>Выберите режим для этого лота</b>\n\n"
                "Обычный — бот после оплаты попросит только ссылку и подтверждение.\n"
                "Голоса — после ссылки бот попросит номер варианта ответа.\n"
                "Реакции — после ссылки бот попросит отправить нужную реакцию.\n"
                "Комментарии — после ссылки бот попросит комментарии по одному на строку.",
                parse_mode="HTML",
                reply_markup=kb.lot_mode_selector("lots")
            )
            return

        elif state == "lot_add_mode":
            text_value = m.text.strip().lower()
            mode_map = {
                "обычный": "normal",
                "голоса": "vote",
                "реакции": "reaction",
                "комментарии": "comments",
            }
            if text_value not in mode_map:
                bot.send_message(
                    m.chat.id,
                    "❌ Выберите режим кнопками ниже или напишите: обычный / голоса / реакции / комментарии.",
                    reply_markup=kb.lot_mode_selector("lots")
                )
                return
            payload = data.copy()
            payload["order_mode"] = mode_map[text_value]
            set_state(uid, "lot_add_summary_v2", **payload)
            svc = payload["service"]
            mode_title = _lot_order_mode_title(payload["order_mode"])
            summary = (
                "<b>Подтвердите создание лота</b>\n\n"
                f"📝 Название: {payload['name']}\n"
                f"🌐 Сервис: #{payload['service_id']} {svc['name']}\n"
                f"🎯 FunPay лот: {payload['funpay_lot_id']} {payload['funpay_lot_name'] or ''}\n"
                f"🗳 Режим: {mode_title}\n"
                f"💵 Себестоимость: {svc['rate']}₽/1000\n"
                f"📊 Количество в заказе FunPay: {payload['quantity_per_order']} шт\n"
                f"💰 Цена за 1 шт: {payload['price_per_unit']:.3f}₽\n"
                f"💰 Цена заказа FunPay: {payload['funpay_price']}₽\n\n"
                "Введите 'да' для подтверждения."
            )
            bot.send_message(m.chat.id, summary, parse_mode="HTML", reply_markup=kb.back("lots"))
            return

        elif state == "lot_add_summary_v2":
            if m.text.strip().lower() not in YES_WORDS:
                bot.send_message(m.chat.id, "❌ Создание отменено. Начните заново.", reply_markup=kb.back("lots"))
                clear_state(uid)
                return
            payload = data
            svc = payload["service"]
            price_per_unit = payload["price_per_unit"]
            min_q = svc["min_order"] or 100
            max_q = svc["max_order"] or 10000
            platform = api.detect_platform(svc["category"]) if api and svc.get("category") else ""
            lot_id = db.add_lot(
                name=payload["name"],
                api_service_id=payload["service_id"],
                api_service_name=svc["name"],
                service_type=svc.get("type", ""),
                order_mode=payload.get("order_mode", "normal"),
                vote_answer_number="",
                api_rate=svc["rate"],
                category=svc["category"],
                platform=platform,
                min_quantity=min_q,
                max_quantity=max_q,
                funpay_lot_id=payload.get("funpay_lot_id", ""),
                funpay_lot_name=payload.get("funpay_lot_name", ""),
                quantity_per_order=payload.get("quantity_per_order", 1),
                price_mode=payload["price_mode"],
                price_input=payload["price_input"],
                price_per_unit=price_per_unit,
                price=price_per_unit,
                markup=30,
            )
            mode_title = _lot_order_mode_title(payload.get("order_mode"))
            bot.send_message(
                m.chat.id,
                (
                    f"✅ Лот #{lot_id} создан!\n"
                    f"🎯 FunPay: {payload.get('funpay_lot_id')} {payload.get('funpay_lot_name') or ''}\n"
                    f"📊 Кол-во: {payload.get('quantity_per_order', 1)} шт\n"
                    f"🗳 Режим: {mode_title}\n"
                    f"💰 {price_per_unit:.3f}₽/шт"
                ),
                parse_mode="HTML",
                reply_markup=kb.lot_item(lot_id)
            )
            clear_state(uid)
            return

        elif state == "lot_add_quantity":
            try:
                quantity_per_order = int(m.text.strip())
                if quantity_per_order <= 0:
                    raise ValueError()
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите положительное число (1, 100, 1000 и т.д.).", reply_markup=kb.back("lots"))
                return
            
            # Calculate price per unit based on FunPay price and quantity
            funpay_price = data["funpay_price"]
            price_per_unit = funpay_price / quantity_per_order if quantity_per_order > 0 else 0
            
            payload = data.copy()
            payload["quantity_per_order"] = quantity_per_order
            payload["price_per_unit"] = price_per_unit
            payload["price_mode"] = "fixed"
            payload["price_input"] = price_per_unit
            svc = payload["service"]
            if str(svc.get("type") or "").lower() == "vote":
                set_state(uid, "lot_add_vote_answer", **payload)
                bot.send_message(
                    m.chat.id,
                    "🗳 <b>Номер варианта голосования</b>\n\nВведите номер варианта, за который нужно голосовать в TwiBoost.\nНапример: <b>1</b>",
                    parse_mode="HTML",
                    reply_markup=kb.back("lots")
                )
                return
            set_state(uid, "lot_add_summary", **payload)

            # Show summary
            summary = (
                "<b>Подтвердите создание лота</b>\n\n"
                f"📝 Название: {data['name']}\n"
                f"🌐 Сервис: #{data['service_id']} {svc['name']}\n"
                f"🎯 FunPay лот: {data['funpay_lot_id']} {data['funpay_lot_name'] or ''}\n"
                f"💵 Себестоимость: {svc['rate']}₽/1000\n"
                f"📊 Количество в заказе FunPay: {quantity_per_order} шт\n"
                f"💰 Цена за 1 шт: {price_per_unit:.3f}₽\n"
                f"💰 Цена заказа FunPay: {funpay_price}₽\n\n"
                "Введите 'да' для подтверждения или измените данные, начав заново."
            )
            bot.send_message(m.chat.id, summary, parse_mode="HTML", reply_markup=kb.back("lots"))

        elif state == "lot_add_vote_answer":
            answer_number = m.text.strip()
            if not answer_number.isdigit() or int(answer_number) <= 0:
                bot.send_message(m.chat.id, "❌ Введите положительный номер варианта, например 1.", reply_markup=kb.back("lots"))
                return
            payload = data.copy()
            payload["vote_answer_number"] = answer_number
            set_state(uid, "lot_add_summary", **payload)
            svc = payload["service"]
            summary = (
                "<b>Подтвердите создание лота</b>\n\n"
                f"📝 Название: {payload['name']}\n"
                f"🌐 Сервис: #{payload['service_id']} {svc['name']}\n"
                f"🎯 FunPay лот: {payload['funpay_lot_id']} {payload['funpay_lot_name'] or ''}\n"
                f"🗳 Вариант голоса: {answer_number}\n"
                f"💵 Себестоимость: {svc['rate']}₽/1000\n"
                f"📊 Количество в заказе FunPay: {payload['quantity_per_order']} шт\n"
                f"💰 Цена за 1 шт: {payload['price_per_unit']:.3f}₽\n"
                f"💰 Цена заказа FunPay: {payload['funpay_price']}₽\n\n"
                "Введите 'да' для подтверждения или измените данные, начав заново."
            )
            bot.send_message(m.chat.id, summary, parse_mode="HTML", reply_markup=kb.back("lots"))

        
        elif state == "lot_add_summary":
            if m.text.strip().lower() not in YES_WORDS:
                bot.send_message(m.chat.id, "❌ Создание отменено. Начните заново.", reply_markup=kb.back("lots"))
                clear_state(uid)
                return
            payload = data
            svc = payload["service"]
            price_per_unit = payload["price_per_unit"]
            min_q = svc["min_order"] or 100
            max_q = svc["max_order"] or 10000
            platform = api.detect_platform(svc["category"]) if api and svc.get("category") else ""
            lot_id = db.add_lot(
                name=payload["name"],
                api_service_id=payload["service_id"],
                api_service_name=svc["name"],
                service_type=svc.get("type", ""),
                vote_answer_number=payload.get("vote_answer_number", ""),
                api_rate=svc["rate"],
                category=svc["category"],
                platform=platform,
                min_quantity=min_q,
                max_quantity=max_q,
                funpay_lot_id=payload.get("funpay_lot_id", ""),
                funpay_lot_name=payload.get("funpay_lot_name", ""),
                quantity_per_order=payload.get("quantity_per_order", 1),
                price_mode=payload["price_mode"],
                price_input=payload["price_input"],
                price_per_unit=price_per_unit,
                price=price_per_unit,
                markup=30,
            )
            bot.send_message(
                m.chat.id,
                (
                    f"✅ Лот #{lot_id} создан!\n"
                    f"🎯 FunPay: {payload.get('funpay_lot_id')} {payload.get('funpay_lot_name') or ''}\n"
                    f"📊 Кол-во: {payload.get('quantity_per_order', 1)} шт\n"
                    + (f"🗳 Вариант: {payload.get('vote_answer_number')}\n" if payload.get("vote_answer_number") else "")
                    + f"💰 {price_per_unit:.3f}₽/шт"
                ),
                parse_mode="HTML",
                reply_markup=kb.lot_item(lot_id)
            )
            clear_state(uid)

        elif state == "lote_name":
            clear_state(uid)
            lot_id = data["lot_id"]
            db.update_lot(lot_id, name=m.text.strip())
            bot.send_message(m.chat.id, "✅ Название обновлено.", reply_markup=kb.lot_item(lot_id))

        elif state == "lote_price":
            clear_state(uid)
            lot_id = data["lot_id"]
            try:
                db.update_lot(lot_id, price=float(m.text.strip()))
                bot.send_message(m.chat.id, "✅ Цена обновлена.", reply_markup=kb.lot_item(lot_id))
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите число.", reply_markup=kb.lot_item(lot_id))

        elif state == "lote_markup":
            clear_state(uid)
            lot_id = data["lot_id"]
            try:
                db.update_lot(lot_id, markup=float(m.text.strip().replace(",", ".")))
                bot.send_message(m.chat.id, "✅ Наценка обновлена.", reply_markup=kb.lot_item(lot_id))
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите число.", reply_markup=kb.lot_item(lot_id))





        elif state == "lote_service":
            clear_state(uid)
            lot_id = data["lot_id"]
            lot = db.get_lot(lot_id)
            provider = (lot.get("api_provider") or "twiboost").lower()

            try:
                svc_id = int(m.text.strip())
            # 🔥 Динамически выбираем функцию получения сервиса по провайдеру лота
                if provider == "smmway":
                    svc = _get_smmway_service_by_id(svc_id)
                else:
                    svc = _get_twiboost_service_by_id(svc_id)

                if svc:
                    current_lot = db.get_lot(lot_id) or {}
                    service_type = str(svc.get("type") or "").lower()
                    next_mode = "normal" if service_type != "vote" else (current_lot.get("order_mode") or "vote")
                    db.update_lot(lot_id, api_service_id=svc_id, api_service_name=svc["name"], api_rate=svc["rate"],
                                  min_quantity=svc["min_order"], max_quantity=svc["max_order"], category=svc["category"],
                                  service_type=svc.get("type", ""),
                                  order_mode=next_mode,
                                  vote_answer_number="" if service_type != "vote" else current_lot.get("vote_answer_number", ""))
                    bot.send_message(m.chat.id, f"✅ Сервис обновлён ({provider}): #{svc_id} {svc['name']}", reply_markup=kb.lot_item(lot_id))
                else:
                    bot.send_message(m.chat.id, f"❌ Сервис не найден в {provider.capitalize()}.", reply_markup=kb.lot_item(lot_id))
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите число.", reply_markup=kb.lot_item(lot_id))

        elif state == "lote_min":
            clear_state(uid)
            lot_id = data["lot_id"]
            try:
                db.update_lot(lot_id, min_quantity=int(m.text.strip()))
                bot.send_message(m.chat.id, "✅ Мин. кол-во обновлено.", reply_markup=kb.lot_item(lot_id))
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите число.", reply_markup=kb.lot_item(lot_id))

        elif state == "lote_max":
            clear_state(uid)
            lot_id = data["lot_id"]
            try:
                db.update_lot(lot_id, max_quantity=int(m.text.strip()))
                bot.send_message(m.chat.id, "✅ Макс. кол-во обновлено.", reply_markup=kb.lot_item(lot_id))
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите число.", reply_markup=kb.lot_item(lot_id))

        elif state == "lote_vote":
            clear_state(uid)
            lot_id = data["lot_id"]
            value = m.text.strip()
            if value in ("0", "-", "нет", "очистить"):
                db.update_lot(lot_id, vote_answer_number="")
                bot.send_message(m.chat.id, "✅ Вариант голоса очищен.", reply_markup=kb.lot_item(lot_id))
                return
            if not value.isdigit() or int(value) <= 0:
                bot.send_message(m.chat.id, "❌ Введите положительный номер варианта или 0 для очистки.", reply_markup=kb.lot_item(lot_id))
                return
            db.update_lot(lot_id, vote_answer_number=value)
            bot.send_message(m.chat.id, f"✅ Вариант голоса: {value}", reply_markup=kb.lot_item(lot_id))

        elif state == "lote_review_bonus_service":
            clear_state(uid)
            lot_id = data["lot_id"]
            try:
                svc_id = int(m.text.strip())
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите ID сервиса числом.", reply_markup=kb.lot_item(lot_id))
                return
            svc = _get_twiboost_service_by_id(svc_id)
            if not svc:
                bot.send_message(m.chat.id, "❌ Сервис не найден в TwiBoost.", reply_markup=kb.lot_item(lot_id))
                return
            service_type = str(svc.get("type") or "").strip().lower()
            if service_type == "vote":
                bot.send_message(
                    m.chat.id,
                    "❌ Для бонуса за отзыв пока поддерживаются только обычные услуги. Выберите другой сервис.",
                    reply_markup=_kb_lot_review_bonus(lot_id, enabled=_lot_review_bonus_enabled(db.get_lot(lot_id))),
                )
                return
            db.update_lot(
                lot_id,
                review_bonus_service_id=svc_id,
                review_bonus_service_name=svc.get("name") or "",
                review_bonus_service_type=service_type,
            )
            lot = db.get_lot(lot_id)
            bot.send_message(
                m.chat.id,
                "✅ Бонусный сервис сохранён.\n\n"
                f"{_lot_review_bonus_card_text(lot)}",
                reply_markup=_kb_lot_review_bonus(lot_id, enabled=_lot_review_bonus_enabled(lot)),
            )

        elif state == "lote_review_bonus_qty":
            clear_state(uid)
            lot_id = data["lot_id"]
            try:
                qty = int(m.text.strip())
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите количество числом.", reply_markup=kb.lot_item(lot_id))
                return
            if qty < 0:
                bot.send_message(m.chat.id, "❌ Количество должно быть 0 или больше.", reply_markup=kb.lot_item(lot_id))
                return
            lot = db.get_lot(lot_id)
            if not lot:
                bot.send_message(m.chat.id, "❌ Лот не найден.", reply_markup=kb.lots_menu())
                return
            service_id = _lot_review_bonus_service_id(lot)
            service = _get_twiboost_service_by_id(service_id) if service_id else None
            if qty > 0 and service:
                min_order = int(service.get("min_order") or 0)
                max_order = int(service.get("max_order") or 0)
                if min_order > 0 and qty < min_order:
                    bot.send_message(
                        m.chat.id,
                        f"❌ Для этого сервиса минимальное количество: {min_order}.",
                        reply_markup=kb.lot_item(lot_id),
                    )
                    return
                if max_order > 0 and qty > max_order:
                    bot.send_message(
                        m.chat.id,
                        f"❌ Для этого сервиса максимальное количество: {max_order}.",
                        reply_markup=kb.lot_item(lot_id),
                    )
                    return
            db.update_lot(lot_id, review_bonus_quantity=qty, review_bonus_enabled=0 if qty <= 0 else int(lot.get("review_bonus_enabled") or 0))
            lot = db.get_lot(lot_id)
            if qty <= 0:
                bot.send_message(
                    m.chat.id,
                    "✅ Бонусное количество очищено.\n\n"
                    f"{_lot_review_bonus_card_text(lot)}",
                    reply_markup=_kb_lot_review_bonus(lot_id, enabled=_lot_review_bonus_enabled(lot)),
                )
            else:
                bot.send_message(
                    m.chat.id,
                    "✅ Бонусное количество сохранено.\n\n"
                    f"{_lot_review_bonus_card_text(lot)}",
                    reply_markup=_kb_lot_review_bonus(lot_id, enabled=_lot_review_bonus_enabled(lot)),
                )

        # --- ЗАКАЗЫ ---
        elif state == "order_new_lot":
            clear_state(uid)
            try:
                lot_id = int(m.text.strip())
                lot = db.get_lot(lot_id)
                if not lot:
                    bot.send_message(m.chat.id, "❌ Лот не найден.", reply_markup=kb.back("orders"))
                    return
                set_state(uid, "order_new_link", lot_id=lot_id)
                bot.send_message(m.chat.id, f"🔗 Введите <b>ссылку</b> для заказа:\n\n📦 {lot['name']}", parse_mode="HTML", reply_markup=kb.back("orders"))
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите ID лота (число).", reply_markup=kb.back("orders"))

        elif state == "order_new_link":
            set_state(uid, "order_new_qty", lot_id=data["lot_id"], link=m.text.strip())
            lot = db.get_lot(data["lot_id"])
            bot.send_message(m.chat.id, f"📊 Введите <b>количество</b> ({lot['min_quantity']}-{lot['max_quantity']}):", parse_mode="HTML", reply_markup=kb.back("orders"))

        elif state == "order_new_qty":
            clear_state(uid)
            lot_id = data["lot_id"]
            link = data["link"]
            try:
                qty = int(m.text.strip())
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите число.", reply_markup=kb.back("orders"))
                return
            lot = db.get_lot(lot_id)
            if not lot:
                bot.send_message(m.chat.id, "❌ Лот не найден.", reply_markup=kb.back("orders"))
                return
            bot.send_message(m.chat.id, "⏳ Создаю заказ в API...")
            Thread(target=_create_order_async, args=(m.chat.id, lot, link, qty), daemon=True).start()

        # --- ПОИСК СЕРВИСОВ ---
        elif state == "svc_search":
            clear_state(uid)
            query = m.text.strip().lower()
            services = db.get_services(provider="twiboost", limit=500)
            found = [s for s in services if query in s["name"].lower() or query in s["category"].lower()]
            if not found:
                bot.send_message(m.chat.id, "🔍 Ничего не найдено.", reply_markup=kb.back("services"))
            else:
                text = f"🔍 Найдено: <b>{len(found)}</b>\n"
                bot.send_message(m.chat.id, text, parse_mode="HTML", reply_markup=kb.services_list(found[:50]))

        # --- ПРОМОКОДЫ ---
        elif state == "promo_add_code":
            set_state(uid, "promo_add_type", code=m.text.strip().upper())
            bot.send_message(m.chat.id, "📊 Тип скидки:\n\n<b>percent</b> — процент\n<b>fixed</b> — фиксированная сумма", parse_mode="HTML", reply_markup=kb.back("promos"))

        elif state == "promo_add_type":
            dtype = m.text.strip().lower()
            if dtype not in ("percent", "fixed"):
                bot.send_message(m.chat.id, "❌ Введите <b>percent</b> или <b>fixed</b>.", parse_mode="HTML")
                return
            set_state(uid, "promo_add_value", code=data["code"], discount_type=dtype)
            bot.send_message(m.chat.id, "💰 Введите <b>значение скидки</b> (число):", parse_mode="HTML", reply_markup=kb.back("promos"))

        elif state == "promo_add_value":
            clear_state(uid)
            try:
                val = float(m.text.strip())
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите число.", reply_markup=kb.back("promos"))
                return
            code = data["code"]
            dtype = data["discount_type"]
            pid = db.add_promo(code=code, discount_type=dtype, discount_value=val, max_uses=100)
            val_str = f"{val}%" if dtype == "percent" else f"{val}₽"
            text = f"✅ <b>Промокод создан!</b>\n\n🎫 <code>{code}</code>\n💰 Скидка: {val_str}"
            bot.send_message(m.chat.id, text, parse_mode="HTML", reply_markup=kb.promo_item(pid))

        # --- ДОПЫ ---
        elif state == "upsell_rule_min":
            try:
                min_amount = float(m.text.strip().replace(",", "."))
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите число, например 100.", reply_markup=kb.back("upsells"))
                return
            if min_amount < 0:
                bot.send_message(m.chat.id, "❌ Цена должна быть 0 или больше.", reply_markup=kb.back("upsells"))
                return
            set_state(uid, "upsell_rule_discount", min_order_amount=min_amount)
            bot.send_message(m.chat.id, "💰 Введите <b>% бонуса</b> для промокода:", parse_mode="HTML", reply_markup=kb.back("upsells"))

        elif state == "upsell_rule_discount":
            try:
                disc = float(m.text.strip().replace(",", "."))
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите число.", reply_markup=kb.back("upsells"))
                return
            if disc <= 0:
                bot.send_message(m.chat.id, "❌ Процент должен быть больше 0.", reply_markup=kb.back("upsells"))
                return
            set_state(uid, "upsell_rule_uses", min_order_amount=data["min_order_amount"], discount_value=disc)
            bot.send_message(m.chat.id, "♻️ Введите <b>количество применений</b> для бонусного промокода.\n\nВведите <b>1</b>, если код должен быть одноразовым.", parse_mode="HTML", reply_markup=kb.back("upsells"))

        elif state == "upsell_rule_max":
            try:
                max_amount = float(m.text.strip().replace(",", "."))
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите число.", reply_markup=kb.back("upsells"))
                return
            min_amount = float(data["min_order_amount"])
            disc = float(data["discount_value"])
            if max_amount < 0:
                bot.send_message(m.chat.id, "❌ Верхняя цена должна быть 0 или больше.", reply_markup=kb.back("upsells"))
                return
            if max_amount > 0 and max_amount < min_amount:
                bot.send_message(m.chat.id, "❌ Цена 'до' не может быть меньше цены 'от'.", reply_markup=kb.back("upsells"))
                return
            set_state(
                uid,
                "upsell_rule_uses",
                min_order_amount=min_amount,
                discount_value=disc,
                max_order_amount=max_amount,
            )
            bot.send_message(
                m.chat.id,
                "♻️ Введите <b>количество применений</b> для бонусного промокода.\n\nВведите <b>1</b>, если код должен быть одноразовым.",
                parse_mode="HTML",
                reply_markup=kb.back("upsells"),
            )
            return
            if max_amount > 0:
                name = f"Бонус {disc:.0f}% | {min_amount:.0f}-{max_amount:.0f}₽"
                bonus_text = f"+{disc:.0f}% к количеству для заказов от {min_amount:.0f}₽ до {max_amount:.0f}₽"
            else:
                name = f"Бонус {disc:.0f}% | от {min_amount:.0f}₽"
                bonus_text = f"+{disc:.0f}% к количеству для заказов от {min_amount:.0f}₽"
            uid_db = db.add_upsell(
                name=name,
                discount_value=disc,
                min_order_amount=min_amount,
                max_order_amount=max_amount,
                bonus_text=bonus_text,
            )
            limit_text = f"от {min_amount:.0f}₽ до {max_amount:.0f}₽" if max_amount > 0 else f"от {min_amount:.0f}₽"
            text = (
                "✅ <b>Условие создано!</b>\n\n"
                f"🎁 {name}\n"
                f"💰 Бонус: {disc:.0f}%\n"
                f"📊 Диапазон: {limit_text}\n\n"
                "⭐ Триггер: отзыв 5★ на FunPay"
            )
            bot.send_message(m.chat.id, text, parse_mode="HTML", reply_markup=kb.upsell_item(uid_db))

        elif state == "upsell_rule_uses":
            try:
                promo_max_uses = int(m.text.strip())
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите целое число от 1 до 100.", reply_markup=kb.back("upsells"))
                return
            if promo_max_uses < 1 or promo_max_uses > 100:
                bot.send_message(m.chat.id, "❌ Количество применений должно быть от 1 до 100.", reply_markup=kb.back("upsells"))
                return
            set_state(
                uid,
                "upsell_rule_apply_min",
                min_order_amount=float(data["min_order_amount"]),
                discount_value=float(data["discount_value"]),
                promo_max_uses=promo_max_uses,
            )
            bot.send_message(
                m.chat.id,
                "📉 Введите <b>минимальную сумму нового заказа</b> в ₽, на которой можно применить промокод.\n\n"
                "Введите <b>0</b>, если нижней границы не должно быть.",
                parse_mode="HTML",
                reply_markup=kb.back("upsells"),
            )
            return

        elif state == "upsell_rule_apply_min":
            try:
                promo_apply_min_amount = float(m.text.strip().replace(",", "."))
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите число.", reply_markup=kb.back("upsells"))
                return
            if promo_apply_min_amount < 0:
                bot.send_message(m.chat.id, "❌ Нижняя граница должна быть 0 или больше.", reply_markup=kb.back("upsells"))
                return
            set_state(
                uid,
                "upsell_rule_apply_max",
                min_order_amount=float(data["min_order_amount"]),
                discount_value=float(data["discount_value"]),
                promo_max_uses=int(data["promo_max_uses"]),
                promo_apply_min_amount=promo_apply_min_amount,
            )
            bot.send_message(
                m.chat.id,
                "📈 Введите <b>максимальную сумму нового заказа</b> в ₽, на которой можно применить промокод.\n\n"
                "Введите <b>0</b>, если верхней границы не должно быть.",
                parse_mode="HTML",
                reply_markup=kb.back("upsells"),
            )

        elif state == "upsell_rule_apply_max":
            clear_state(uid)
            try:
                promo_apply_max_amount = float(m.text.strip().replace(",", "."))
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите число.", reply_markup=kb.back("upsells"))
                return
            if promo_apply_max_amount < 0:
                bot.send_message(m.chat.id, "❌ Цена применения должна быть 0 или больше.", reply_markup=kb.back("upsells"))
                return
            min_amount = float(data["min_order_amount"])
            disc = float(data["discount_value"])
            promo_max_uses = int(data["promo_max_uses"])
            promo_apply_min_amount = float(data["promo_apply_min_amount"])
            if promo_apply_max_amount > 0 and promo_apply_max_amount < promo_apply_min_amount:
                bot.send_message(m.chat.id, "❌ Верхняя граница применения не может быть меньше нижней.", reply_markup=kb.back("upsells"))
                return
            name = f"Бонус {disc:.0f}% | от {min_amount:.0f}₽"
            apply_text = _amount_range_text(promo_apply_min_amount, promo_apply_max_amount)
            bonus_text = f"+{disc:.0f}% за отзыв на заказ от {min_amount:.0f}₽"
            if promo_apply_min_amount > 0 or promo_apply_max_amount > 0:
                bonus_text += f". Применить можно на заказ {apply_text}"
            uid_db = db.add_upsell(
                name=name,
                discount_value=disc,
                min_order_amount=min_amount,
                max_order_amount=0,
                promo_apply_min_amount=promo_apply_min_amount,
                promo_max_uses=promo_max_uses,
                promo_apply_max_amount=promo_apply_max_amount,
                bonus_text=bonus_text,
            )
            text = (
                "✅ <b>Условие создано!</b>\n\n"
                f"🎁 {name}\n"
                f"💰 Бонус: {disc:.0f}%\n"
                f"📊 Выдача бонуса: от {min_amount:.0f}₽\n"
                f"🧾 Применение промокода: {apply_text}\n"
                f"♻️ Применений: {_promo_uses_text(promo_max_uses)}\n\n"
                "⭐ Триггер: отзыв 5★ на FunPay"
            )
            bot.send_message(m.chat.id, text, parse_mode="HTML", reply_markup=kb.upsell_item(uid_db))

        elif state == "upsell_add_name":
            set_state(uid, "upsell_add_discount", name=m.text.strip())
            bot.send_message(m.chat.id, "💰 Введите <b>% скидки</b> для бонуса:", parse_mode="HTML", reply_markup=kb.back("upsells"))

        elif state == "upsell_add_discount":
            clear_state(uid)
            try:
                disc = float(m.text.strip())
            except ValueError:
                bot.send_message(m.chat.id, "❌ Введите число.", reply_markup=kb.back("upsells"))
                return
            uid_db = db.add_upsell(name=data["name"], discount_value=disc, promo_max_uses=1, bonus_text=f"Скидка {disc}% на следующий заказ")
            text = f"✅ <b>Доп создан!</b>\n\n📝 {data['name']}\n💰 Скидка: {disc}%\n\n⚡ Триггер: отзыв ⭐⭐⭐⭐⭐ на FunPay"
            bot.send_message(m.chat.id, text, parse_mode="HTML", reply_markup=kb.upsell_item(uid_db))

        # --- ШАБЛОНЫ ---
        elif state == "tpl_edit_text":
            clear_state(uid)
            tpl_name = data["tpl_name"]
            db.upsert_template(tpl_name, m.text.strip())
            cfg.set(f"messages.{tpl_name}", m.text.strip())
            bot.send_message(m.chat.id, f"✅ Шаблон <b>{tpl_name}</b> обновлён.", parse_mode="HTML", reply_markup=kb.template_item(tpl_name))

        elif state == "tpl_add_name":
            set_state(uid, "tpl_add_text", tpl_name=m.text.strip())
            bot.send_message(m.chat.id, "📝 Введите <b>текст шаблона</b>.\n\nПеременные: {order_id}, {service_name}, {link}, {quantity}, {price}, {buyer}, {promo_code}, {bonus_text}, {expires_at}", parse_mode="HTML", reply_markup=kb.back("templates"))

        elif state == "tpl_add_text":
            clear_state(uid)
            tpl_name = data["tpl_name"]
            db.upsert_template(tpl_name, m.text.strip())
            bot.send_message(m.chat.id, f"✅ Шаблон <b>{tpl_name}</b> создан.", parse_mode="HTML", reply_markup=kb.template_item(tpl_name))

        else:
            clear_state(uid)

    # ==================== CALLBACK QUERIES ====================

    @bot.callback_query_handler(func=lambda c: True)
    def handle_callback(c: CallbackQuery):
        d = c.data
        uid = c.from_user.id
        chat_id = c.message.chat.id
        msg_id = c.message.message_id

        if not is_admin(uid):
            if _is_mirror_role():
                bot.answer_callback_query(c.id, "⛔ Доступ запрещён")
                return
            if _handle_mirror_user_callback(c, d, uid, chat_id, msg_id):
                return
            bot.answer_callback_query(c.id, "⛔ Доступ к основному управлению закрыт")
            return

        try:
            if _handle_mirror_admin_callback(c, d, uid, chat_id, msg_id):
                return
            # --- ГЛАВНОЕ МЕНЮ ---
            if d == "main":
                clear_state(uid)
                text = _mirror_runtime_main_text() if _is_mirror_role() else (
                    "╔══════════════════════════╗\n"
                    "║  🤖 <b>SMM Auto Bot</b>  ║\n"
                    "╚══════════════════════════╝\n\n"
                    "Выберите раздел:"
                )
                _edit(chat_id, msg_id, text, kb.main_menu())

            # --- БАЛАНС ---
            elif d == "balance":
                bot.answer_callback_query(c.id, "⏳ Запрос...")
                _show_balance(chat_id, msg_id)

            # ==================== ЛОТЫ ====================
            elif d == "lots":
                _edit(chat_id, msg_id, "🛒 <b>Управление лотами</b>", kb.lots_menu())

            elif d == "lots_list":
                lots = db.get_lots()
                if not lots:
                    _edit(chat_id, msg_id, "📭 Лотов пока нет.", kb.lots_menu())
                else:
                    _edit(chat_id, msg_id, f"🛒 <b>Лоты ({len(lots)})</b>", kb.lots_list(lots))

            elif d.startswith("lots_page_"):
                page = int(d.split("_")[-1])
                lots = db.get_lots()
                _edit(chat_id, msg_id, f"🛒 <b>Лоты ({len(lots)})</b>", kb.lots_list(lots, page))

            elif d == "lots_fpcreate_pick":
                lots = db.get_lots()
                if not lots:
                    _edit(chat_id, msg_id, "📭 Сначала создайте хотя бы один бот-лот.", kb.lots_menu())
                else:
                    _edit(chat_id, msg_id, "🚀 <b>Выберите лот для публикации на FunPay</b>", kb.lots_fpcreate_picker(lots))

            elif d.startswith("lots_fpcreate_page_"):
                page = int(d.split("_")[-1])
                lots = db.get_lots()
                _edit(chat_id, msg_id, "🚀 <b>Выберите лот для публикации на FunPay</b>", kb.lots_fpcreate_picker(lots, page=page))

            elif d.startswith("fpcreatepick_"):
                lot_id = int(d.split("_")[-1])
                _start_funpay_offer_create(chat_id, msg_id, uid, lot_id, back_callback="lots_fpcreate_pick")

            elif d.startswith("lotfpentry_"):
                lot_id = int(d.split("_")[-1])
                _start_funpay_offer_create(chat_id, msg_id, uid, lot_id, back_callback="lots_fpcreate_pick")

            elif d.startswith("lotfpbrowse_"):
                lot_id = int(d.split("_")[-1])
                _show_funpay_categories(chat_id, msg_id, lot_id, page=0)

            elif d.startswith("lotfprelated_"):
                lot_id = int(d.split("_")[-1])
                _show_funpay_related_categories(chat_id, msg_id, lot_id, page=0)

            elif d.startswith("lotfptelegram_"):
                lot_id = int(d.split("_")[-1])
                _show_funpay_related_categories(chat_id, msg_id, lot_id, page=0)

            elif d.startswith("lotfpcatpage_"):
                parts = d.split("_")
                lot_id = int(parts[-2])
                page = int(parts[-1])
                _show_funpay_categories(chat_id, msg_id, lot_id, page=page)

            elif d.startswith("lotfpcat_"):
                parts = d.split("_")
                lot_id = int(parts[-2])
                category_id = int(parts[-1])
                _show_funpay_subcategories(chat_id, msg_id, lot_id, category_id, page=0)

            elif d.startswith("lotfpsubpage_"):
                parts = d.split("_")
                lot_id = int(parts[-3])
                category_id = int(parts[-2])
                page = int(parts[-1])
                _show_funpay_subcategories(chat_id, msg_id, lot_id, category_id, page=page)

            elif d.startswith("lotfpsub_"):
                parts = d.split("_")
                lot_id = int(parts[-2])
                node_id = int(parts[-1])
                _begin_funpay_offer_node(
                    chat_id,
                    uid,
                    lot_id,
                    node_id,
                    msg_id=msg_id,
                    back_callback=f"lot_{lot_id}",
                )

            elif d.startswith("lotfppresets_"):
                lot_id = int(d.split("_")[-1])
                lot = db.get_lot(lot_id)
                presets = _get_funpay_offer_presets(lot)
                _edit(
                    chat_id,
                    msg_id,
                    "⭐ <b>Быстрые пресеты</b>\n\nВыберите известную категорию / подкатегорию для этой услуги.",
                    _kb_funpay_offer_preset_picker(lot_id, presets, f"lotfpentry_{lot_id}"),
                )

            
            elif d == "lot_add":
            # 🔥 Перенаправляем сразу на V3 поток, где уже есть логика выбора провайдера
                set_state(uid, "lot_add_name_v3", order_mode="normal")
                _edit(chat_id, msg_id, "📝 Введите <b>название лота</b>:", kb.back("lots"))

            elif d.startswith("lotcreate_mode_"):
                mode = d.split("_")[-1]
                set_state(uid, "lot_add_name_v3", order_mode=mode if mode in {"vote", "reaction", "comments"} else "normal")
                _edit(chat_id, msg_id, "📝 Введите <b>название лота</b>:", kb.back("lots"))
            
            elif d.startswith("lotprov_"):
                prov = d.split("_")[-1]
                st = get_state(uid)
                if st.get("state") == "lot_add_provider":
                    set_state(uid, "lot_add_service", api_provider=prov, name=st["data"].get("name", ""))
                    bot.send_message(chat_id, f"🔢 Введите <b>ID сервиса</b> {prov}:", parse_mode="HTML", reply_markup=kb.back("lots"))
                    bot.answer_callback_query(c.id)
                return


            elif d.startswith("lot_edit_"):
                lot_id = int(d.split("_")[-1])
                _edit(chat_id, msg_id, "✏️ <b>Редактирование лота</b>", kb.lot_edit(lot_id))

            elif d.startswith("lot_toggle_"):
                lot_id = int(d.split("_")[-1])
                lot = db.get_lot(lot_id)
                if lot:
                    new_active = 0 if lot["is_active"] else 1
                    db.update_lot(lot_id, is_active=new_active)
                    status = "✅ Включён" if new_active else "⏸ Выключен"
                    bot.answer_callback_query(c.id, status)
                    _show_lot(chat_id, msg_id, lot_id)

            elif d.startswith("lot_del_"):
                lot_id = int(d.split("_")[-1])
                _edit(chat_id, msg_id, f"🗑 Удалить лот #{lot_id}?", kb.confirm("lot_del", lot_id))

            elif d.startswith("lot_stats_"):
                lot_id = int(d.split("_")[-1])
                lot = db.get_lot(lot_id)
                if lot:
                    stats = db.get_lot_stats(lot_id)
                    text = (
                        f"📊 <b>Статистика лота</b>\n\n"
                        f"📝 {lot['name']}\n"
                        f"{'━' * 25}\n"
                        f"📦 Всего заказов: <b>{stats['total_orders']}</b>\n"
                        f"✅ Выполнено: <b>{stats['completed_orders']}</b>\n"
                        f"💰 Выручка: <b>{stats['total_revenue']:.2f}₽</b>\n"
                        f"💵 Себестоимость: <b>{stats['total_cost']:.2f}₽</b>\n"
                        f"📈 Прибыль: <b>{stats['total_profit']:.2f}₽</b>"
                    )
                    _edit(chat_id, msg_id, text, kb.lot_item(lot_id))

            elif d.startswith("lot_") and d.count("_") == 1:
                lot_id = int(d.split("_")[1])
                _show_lot(chat_id, msg_id, lot_id)

            # Редактирование лота — ожидание ввода
            elif d.startswith("lote_fpcreate_"):
                lot_id = int(d.split("_")[-1])
                _start_funpay_offer_create(chat_id, msg_id, uid, lot_id, back_callback=f"lot_edit_{lot_id}")

            elif d.startswith("lotfpmanual_"):
                lot_id = int(d.split("_")[-1])
                set_state(uid, "lotfp_node", lot_id=lot_id, back_callback=f"lot_{lot_id}")
                _edit(
                    chat_id,
                    msg_id,
                    "🆔 <b>Введите node ID</b> нужной категории FunPay.\n\n"
                    "Как пользоваться:\n"
                    "• если введёте ID корневой категории, бот покажет её подкатегории\n"
                    "• если введёте ID подкатегории, бот сразу откроет форму создания лота\n\n"
                    "Примеры:\n"
                    "• <b>224</b> — Telegram\n"
                    "• <b>703</b> — Telegram / Услуги\n\n"
                    "Если не уверены, лучше идите через каталог кнопками, а не вручную.",
                    kb.back(f"lot_{lot_id}"),
                )

            elif d.startswith("lotfppreset_"):
                _, lot_id_raw, node_id_raw = d.split("_", 2)
                _begin_funpay_offer_node(
                    chat_id,
                    uid,
                    int(lot_id_raw),
                    int(node_id_raw),
                    msg_id=msg_id,
                    back_callback=f"lot_{int(lot_id_raw)}",
                )

            elif d.startswith("lotfpchoicepage_"):
                page = int(d.split("_")[-1])
                state_payload = get_state(uid)
                if state_payload.get("state") != "lotfp_field":
                    bot.answer_callback_query(c.id, "Начните создание лота заново.")
                    return
                state_data = dict(state_payload.get("data") or {})
                schema = state_data.get("form_schema", [])
                field_id = str(state_data.get("current_field_id") or "").strip()
                current_field = next((f for f in schema if str(f.get("id") or "") == field_id), None)
                if not current_field:
                    bot.answer_callback_query(c.id, "Поле не найдено.")
                    return
                options = current_field.get("options") or []
                if not options:
                    bot.answer_callback_query(c.id, "Для этого поля нет вариантов.")
                    return
                text = (
                    f"🧩 <b>{html.escape(str(current_field.get('label') or current_field.get('id') or 'поле'))}</b>\n\n"
                    "Выберите один из вариантов:"
                )
                _edit(
                    chat_id,
                    msg_id,
                    text,
                    _kb_funpay_offer_field_options(
                        int(state_data.get("lot_id") or 0),
                        field_id,
                        options,
                        state_data.get("back_callback") or f"lot_{int(state_data.get('lot_id') or 0)}",
                        page=page,
                    ),
                )

            elif d.startswith("lotfpchoice_"):
                option_index = int(d.split("_")[-1])
                state_payload = get_state(uid)
                if state_payload.get("state") != "lotfp_field":
                    bot.answer_callback_query(c.id, "Начните создание лота заново.")
                    return
                state_data = dict(state_payload.get("data") or {})
                schema = state_data.get("form_schema", [])
                field_values = dict(state_data.get("field_values") or {})
                field_id = str(state_data.get("current_field_id") or "").strip()
                current_field = next((f for f in schema if str(f.get("id") or "") == field_id), None)
                if not current_field:
                    bot.answer_callback_query(c.id, "Поле не найдено.")
                    return
                options = current_field.get("options") or []
                if option_index < 0 or option_index >= len(options):
                    bot.answer_callback_query(c.id, "Вариант недоступен.")
                    return
                selected = options[option_index]
                selected_value = str(selected.get("value") or "").strip()
                if not selected_value:
                    bot.answer_callback_query(c.id, "Пустой вариант не поддерживается.")
                    return
                field_values[field_id] = selected_value
                _advance_funpay_offer_field_flow(
                    chat_id,
                    uid,
                    int(state_data.get("lot_id") or 0),
                    int(state_data.get("node_id") or 0),
                    schema,
                    field_values,
                    state_data.get("defaults", {}),
                    state_data.get("back_callback") or f"lot_{int(state_data.get('lot_id') or 0)}",
                    msg_id=msg_id,
                )

            elif d.startswith("lotfpen_"):
                parts = d.split("_")
                mode = parts[1]
                lot_id = int(parts[2])
                state_payload = get_state(uid)
                if state_payload.get("state") != "lotfp_en_mode":
                    bot.answer_callback_query(c.id, "Начните создание лота заново.")
                    return
                state_data = dict(state_payload.get("data") or {})
                defaults = dict(state_data.get("defaults") or {})
                if mode == "copy":
                    defaults["summary_en"] = defaults.get("summary_ru", "")
                    defaults["desc_en"] = defaults.get("desc_ru", "")
                    defaults["payment_msg_en"] = defaults.get("payment_msg_ru", "")
                    set_state(uid, "lotfp_price", **{**state_data, "defaults": defaults})
                    _edit(
                        chat_id,
                        msg_id,
                        f"💰 <b>Цена лота на FunPay</b>\n\nРекомендуемая цена: <b>{float(defaults.get('price', 0) or 0):.2f}₽</b>\nВведите цену за 1 шт. на FunPay.",
                        kb.back(f"lot_{lot_id}")
                    )
                elif mode == "auto":
                    defaults["summary_en"] = _translate_ru_to_en(defaults.get("summary_ru", ""))
                    defaults["desc_en"] = _translate_ru_to_en(defaults.get("desc_ru", ""))
                    defaults["payment_msg_en"] = _translate_ru_to_en(defaults.get("payment_msg_ru", ""))
                    set_state(uid, "lotfp_price", **{**state_data, "defaults": defaults})
                    _edit(
                        chat_id,
                        msg_id,
                        f"💰 <b>Цена лота на FunPay</b>\n\nРекомендуемая цена: <b>{float(defaults.get('price', 0) or 0):.2f}₽</b>\nВведите цену за 1 шт. на FunPay.",
                        kb.back(f"lot_{lot_id}")
                    )
                else:
                    set_state(uid, "lotfp_summary_en", **state_data)
                    _edit(
                        chat_id,
                        msg_id,
                        "📝 <b>Краткое описание EN</b>\n\nОтправьте текст или <b>-</b>, чтобы оставить авто-вариант.",
                        kb.back(f"lot_{lot_id}")
                    )

            elif d.startswith("lote_mode_"):
                lot_id = int(d.split("_")[-1])
                _edit(
                    chat_id,
                    msg_id,
                    "🗳 <b>Выберите режим заказа</b>\n\n"
                    "Обычный — покупатель отправляет ссылку и подтверждает запуск.\n"
                    "Голоса — после ссылки бот спрашивает номер варианта ответа.\n"
                    "Реакции — после ссылки бот спрашивает нужную реакцию.\n"
                    "Комментарии — после ссылки бот просит комментарии по одному на строку.",
                    kb.lot_mode_selector(f"lot_edit_{lot_id}", lot_id=lot_id),
                )

            elif d.startswith("lote_split_"):
                lot_id = int(d.split("_")[-1])
                lot = db.get_lot(lot_id)
                if not lot:
                    _edit(chat_id, msg_id, "❌ Лот не найден.", kb.lots_menu())
                else:
                    db.update_lot(lot_id, split_enabled=0 if _lot_split_enabled(lot) else 1)
                    bot.answer_callback_query(c.id, "✅ Настройка разделения обновлена")
                    _show_lot(chat_id, msg_id, lot_id)
            elif d.startswith("lotmode_add_"):
                mode = d.split("_")[-1]
                state_payload = get_state(uid)
                if state_payload.get("state") != "lot_add_mode":
                    bot.answer_callback_query(c.id, "Начните создание лота заново.")
                else:
                    payload = dict(state_payload.get("data") or {})
                    payload["order_mode"] = mode if mode in {"vote", "reaction", "comments"} else "normal"
                    set_state(uid, "lot_add_summary_v2", **payload)
                    svc = payload["service"]
                    mode_title = _lot_order_mode_title(payload["order_mode"])
                    summary = (
                        "<b>Подтвердите создание лота</b>\n\n"
                        f"📝 Название: {payload['name']}\n"
                        f"🌐 Сервис: #{payload['service_id']} {svc['name']}\n"
                        f"🎯 FunPay лот: {payload['funpay_lot_id']} {payload['funpay_lot_name'] or ''}\n"
                        f"🗳 Режим: {mode_title}\n"
                        f"💵 Себестоимость: {svc['rate']}₽/1000\n"
                        f"📊 Количество в заказе FunPay: {payload['quantity_per_order']} шт\n"
                        f"💰 Цена за 1 шт: {payload['price_per_unit']:.3f}₽\n"
                        f"💰 Цена заказа FunPay: {payload['funpay_price']}₽\n\n"
                        "Введите 'да' для подтверждения."
                    )
                    _edit(chat_id, msg_id, summary, kb.back("lots"))

            elif d.startswith("lotmode_edit_"):
                parts = d.split("_")
                lot_id = int(parts[2])
                mode = parts[3]
                db.update_lot(
                    lot_id,
                    order_mode=mode if mode in {"vote", "reaction", "comments"} else "normal",
                    vote_answer_number="",
                )
                bot.answer_callback_query(c.id, "✅ Режим обновлён")
                _show_lot(chat_id, msg_id, lot_id)

            elif d.startswith("lote_reviewbonus_"):
                lot_id = int(d.split("_")[-1])
                _show_lot_review_bonus_editor(chat_id, msg_id, lot_id)

            elif d.startswith("lotrb_service_"):
                lot_id = int(d.split("_")[-1])
                set_state(uid, "lote_review_bonus_service", lot_id=lot_id)
                _edit(
                    chat_id,
                    msg_id,
                    "🔗 Введите <b>ID бонусного сервиса</b> TwiBoost.\n\n"
                    "Этот сервис будет запускаться после отзыва 5★ вместо обычного промокода.",
                    kb.back(f"lote_reviewbonus_{lot_id}"),
                )

            elif d.startswith("lotrb_qty_"):
                lot_id = int(d.split("_")[-1])
                set_state(uid, "lote_review_bonus_qty", lot_id=lot_id)
                _edit(
                    chat_id,
                    msg_id,
                    "📊 Введите <b>бонусное количество</b>.\n\n"
                    "Например: 1000",
                    kb.back(f"lote_reviewbonus_{lot_id}"),
                )

            elif d.startswith("lotrb_toggle_"):
                lot_id = int(d.split("_")[-1])
                lot = db.get_lot(lot_id)
                if not lot:
                    _edit(chat_id, msg_id, "❌ Лот не найден.", kb.lots_menu())
                    return
                if not _lot_review_bonus_service_id(lot) or _lot_review_bonus_quantity(lot) <= 0:
                    bot.answer_callback_query(c.id, "Сначала задайте бонусный сервис и количество.")
                    _show_lot_review_bonus_editor(chat_id, msg_id, lot_id)
                    return
                if str(lot.get("review_bonus_service_type") or "").strip().lower() == "vote":
                    bot.answer_callback_query(c.id, "Бонус за отзыв пока не поддерживает vote-услуги.")
                    _show_lot_review_bonus_editor(chat_id, msg_id, lot_id)
                    return
                new_value = 0 if _lot_review_bonus_enabled(lot) else 1
                db.update_lot(lot_id, review_bonus_enabled=new_value)
                bot.answer_callback_query(c.id, "✅ Настройка обновлена")
                _show_lot_review_bonus_editor(chat_id, msg_id, lot_id)

            elif d.startswith("lotrb_clear_"):
                lot_id = int(d.split("_")[-1])
                db.update_lot(
                    lot_id,
                    review_bonus_enabled=0,
                    review_bonus_service_id=0,
                    review_bonus_service_name="",
                    review_bonus_service_type="",
                    review_bonus_quantity=0,
                )
                bot.answer_callback_query(c.id, "✅ Бонус за отзыв очищен")
                _show_lot_review_bonus_editor(chat_id, msg_id, lot_id)
                
            elif d.startswith("lote_force_refill_"):
                lot_id = int(d.split("_")[-1])
                lot = db.get_lot(lot_id) if db else None
                if not lot:
                    bot.answer_callback_query(c.id, "❌ Лот не найден")
                    return
                # Ищем последний завершённый заказ этого лота для рефилла
                orders = db.get_orders(lot_id=lot_id, status="completed", limit=1)
                if not orders:
                    bot.answer_callback_query(c.id, "⚠️ Нет выполненных заказов для рефилла")
                    return
                order = orders[0]
                if not order.get("api_order_id"):
                    bot.answer_callback_query(c.id, "❌ Нет API ID заказа")
                    return
                bot.answer_callback_query(c.id, "⏳ Отправляю принудительный рефилл...")
                # Пытаемся отправить рефилл напрямую в API
                try:
                    result = api.refill_order(order["api_order_id"]) if api else {"success": False, "error": "API недоступно"}
                    if result.get("success"):
                        db.update_order(order["id"], refill_count=order["refill_count"] + 1)
                        bot.answer_callback_query(c.id, f"✅ Рефилл отправлен! Refill ID: {result.get('refill_id')}")
                    else:
                        bot.answer_callback_query(c.id, f"❌ Ошибка: {result.get('error', 'Неизвестная ошибка')}")
                except Exception as e:
                     bot.answer_callback_query(c.id, f"❌ Ошибка: {str(e)[:100]}")
                return
            elif d.startswith("lote_"):
                parts = d.split("_")
                field = parts[1]
                lot_id = int(parts[2])
                lot = db.get_lot(lot_id)
                provider = (lot.get("api_provider") or "twiboost").lower()
                prov_label = "SmmWay" if provider == "smmway" else "TwiBoost"
                
                labels = {
                    "name": "📝 Новое название: ",
                    "price": "💰 Новая цена (₽/1000): ",
                    "markup": "📈 Наценка (%): ",
                    "service": f"🔢 ID сервиса API ({prov_label}): ",
                    "min": "📉 Мин. количество: ",
                    "max": "📈 Макс. количество: ",
                    "vote": "🗳 Номер варианта голоса:\n\nВведите число или 0 для очистки. ",
                }
                set_state(uid, f"lote_{field}", lot_id=lot_id)
                prompt = labels.get(field, "Введите значение: ")
                if field == "service":
                    prompt += f"\nВведите номер сервиса из панели {prov_label}."
                _edit(chat_id, msg_id, prompt, kb.back(f"lot_edit_{lot_id}"))

            elif d == "lots_sync":
                bot.answer_callback_query(c.id, "⏳ Синхронизация...")
                Thread(target=_sync_services, args=(chat_id, msg_id), daemon=True).start()

            # ==================== ЗАКАЗЫ ====================
            elif d == "orders":
                _edit(chat_id, msg_id, "📦 <b>Управление заказами</b>", kb.orders_menu())

            elif d == "orders_all":
                orders = db.get_orders(limit=100)
                if not orders:
                    _edit(chat_id, msg_id, "📭 Заказов нет.", kb.orders_menu())
                else:
                    _edit(chat_id, msg_id, f"📦 <b>Заказы ({len(orders)})</b>", kb.orders_list(orders))

            elif d == "orders_active":
                orders = db.get_active_orders()
                if not orders:
                    _edit(chat_id, msg_id, "✅ Нет активных заказов.", kb.orders_menu())
                else:
                    _edit(chat_id, msg_id, f"⏳ <b>Активные ({len(orders)})</b>", kb.orders_list(orders))

            elif d == "orders_completed":
                orders = db.get_orders(status="completed", limit=50)
                if not orders:
                    _edit(chat_id, msg_id, "📭 Нет выполненных заказов.", kb.orders_menu())
                else:
                    _edit(chat_id, msg_id, f"✅ <b>Выполненные ({len(orders)})</b>", kb.orders_list(orders))

            elif d == "orders_failed":
                orders = db.get_orders(status="failed", limit=50)
                if not orders:
                    _edit(chat_id, msg_id, "✅ Нет ошибок.", kb.orders_menu())
                else:
                    _edit(chat_id, msg_id, f"❌ <b>Ошибки ({len(orders)})</b>", kb.orders_list(orders))

            elif d.startswith("ordp_"):
                page = int(d.split("_")[1])
                orders = db.get_orders(limit=100)
                _edit(chat_id, msg_id, f"📦 <b>Заказы</b>", kb.orders_list(orders, page))

            elif d.startswith("order_") and not d.startswith("order_check") and not d.startswith("order_refill") and not d.startswith("order_cancel") and not d.startswith("order_new") and not d.startswith("order_ticket"):
                order_id = int(d.split("_")[1])
                _show_order(chat_id, msg_id, order_id)

            elif d == "order_new":
                lots = db.get_lots(active_only=True)
                if not lots:
                    _edit(chat_id, msg_id, "❌ Нет активных лотов. Сначала создайте лот.", kb.orders_menu())
                else:
                    text = "🛒 <b>Выберите лот или введите его ID:</b>\n\n"
                    for lot in lots[:15]:
                        text += f"  <b>{lot['id']}</b> — {lot['name']} ({_lot_price_per_1000(lot)}₽/1000)\n"
                    set_state(uid, "order_new_lot")
                    _edit(chat_id, msg_id, text, kb.back("orders"))

            elif d.startswith("order_check_"):
                order_id = int(d.split("_")[-1])
                bot.answer_callback_query(c.id, "⏳ Проверка...")
                Thread(target=_check_order, args=(chat_id, msg_id, order_id), daemon=True).start()

            elif d.startswith("order_refill_"):
                order_id = int(d.split("_")[-1])
                bot.answer_callback_query(c.id, "⏳ Рефилл...")
                Thread(target=_refill_order, args=(chat_id, msg_id, order_id), daemon=True).start()

            elif d.startswith("order_cancel_"):
                order_id = int(d.split("_")[-1])
                _edit(chat_id, msg_id, f"🚫 Отменить заказ #{order_id}?", kb.confirm("order_cancel", order_id))

            elif d.startswith("order_ticket_"):
                order_id = int(d.split("_")[-1])
                bot.answer_callback_query(c.id, "🎫 Проверяю отправку тикета...")
                Thread(target=_send_test_ticket_preview, args=(chat_id, order_id), daemon=True).start()

            elif d == "orders_check_all":
                bot.answer_callback_query(c.id, "⏳ Проверяю все...")
                Thread(target=_check_all_orders_v2, args=(chat_id, msg_id), daemon=True).start()

            # ==================== СЕРВИСЫ ====================
            elif d == "services":
                _edit(chat_id, msg_id, "🌐 <b>Сервисы API</b>", kb.services_menu())

            elif d == "svc_list":
                svcs = db.get_services(provider="twiboost", limit=200)
                if not svcs:
                    _edit(chat_id, msg_id, "📭 Сервисов нет. Нажмите «Загрузить с API».", kb.services_menu())
                else:
                    _edit(chat_id, msg_id, f"🌐 <b>Сервисы ({len(svcs)})</b>", kb.services_list(svcs))

            elif d.startswith("svcp_"):
                page = int(d.split("_")[1])
                svcs = db.get_services(provider="twiboost", limit=200)
                _edit(chat_id, msg_id, f"🌐 <b>Сервисы</b>", kb.services_list(svcs, page))

            elif d == "svc_sync":
                bot.answer_callback_query(c.id, "⏳ Загрузка...")
                Thread(target=_sync_services, args=(chat_id, msg_id), daemon=True).start()

            elif d == "svc_cats":
                cats = db.get_service_categories()
                if not cats:
                    _edit(chat_id, msg_id, "📭 Категорий нет. Загрузите сервисы.", kb.services_menu())
                else:
                    _edit(chat_id, msg_id, f"📂 <b>Категории ({len(cats)})</b>", kb.service_categories(cats))

            elif d == "svc_search":
                set_state(uid, "svc_search")
                _edit(chat_id, msg_id, "🔍 Введите <b>запрос</b> для поиска:", kb.back("services"))

            elif d.startswith("svc_to_lot_"):
                svc_id = int(d.split("_")[-1])
                svc = _get_twiboost_service_by_id(svc_id)
                if svc:
                    lots, preview_lots = _get_funpay_lots_preview()
                    set_state(uid, "lot_add_funpay_lot", name=svc["name"], service_id=svc_id, service=svc, funpay_lots=lots)
                    preview = (
                        f"📝 {svc['name']}\n"
                        f"📂 {svc['category']}\n"
                        f"🏷 Тип: {svc['type']}\n"
                        f"💰 Цена: {svc['rate']}₽/1000\n"
                        f"📊 {svc['min_order']} — {svc['max_order']}"
                    )
                    prompt = "🎯 Введите <b>ID лота FunPay</b> для привязки."
                    if preview_lots:
                        prompt += f"\n\n{preview_lots}"
                    _edit(chat_id, msg_id, f"{preview}\n\n{prompt}", kb.back("services"))
                else:
                    bot.answer_callback_query(c.id, "❌ Сервис не найден")

            elif d.startswith("svc_order_"):
                svc_id = int(d.split("_")[-1])
                svc = _get_twiboost_service_by_id(svc_id)
                if svc:
                    # Создаём временный лот и переходим в создание заказа
                    set_state(uid, "order_new_link", lot_id=0, svc_id=svc_id, svc=svc)
                    _edit(chat_id, msg_id, f"🔗 Введите <b>ссылку</b> для заказа:\n\n📦 {svc['name']}\n💰 {svc['rate']}₽/1000\n📊 {svc['min_order']}-{svc['max_order']}", kb.back("services"))

            elif d.startswith("svc_") and d.count("_") == 1:
                svc_id = int(d.split("_")[1])
                svc = _get_twiboost_service_by_id(svc_id)
                if svc:
                    text = (
                        f"🌐 <b>Сервис #{svc['service_id']}</b>\n\n"
                        f"📝 {svc['name']}\n"
                        f"📂 {svc['category'][:60]}\n"
                        f"🏷 Тип: {svc['type']}\n"
                        f"💰 Цена: <b>${svc['rate']}/1000</b>\n"
                        f"📊 {svc['min_order']} — {svc['max_order']}\n"
                        f"🔁 Рефилл: {'✅' if svc['refill'] else '❌'}\n"
                        f"🚫 Отмена: {'✅' if svc['cancel'] else '❌'}"
                    )
                    _edit(chat_id, msg_id, text, kb.service_item(svc_id))

            # ==================== ПРОМОКОДЫ ====================
            elif d == "promos":
                _edit(chat_id, msg_id, "🎫 <b>Промокоды</b>", kb.promos_menu())

            elif d == "promo_list":
                promos = db.get_promos()
                if not promos:
                    _edit(chat_id, msg_id, "📭 Промокодов нет.", kb.promos_menu())
                else:
                    _edit(chat_id, msg_id, f"🎫 <b>Промокоды ({len(promos)})</b>", kb.promos_list(promos))

            elif d.startswith("promp_"):
                page = int(d.split("_")[1])
                promos = db.get_promos()
                _edit(chat_id, msg_id, f"🎫 <b>Промокоды</b>", kb.promos_list(promos, page))

            elif d == "promo_add":
                set_state(uid, "promo_add_code")
                _edit(chat_id, msg_id, "🎫 Введите <b>код промокода</b> (латиница, цифры):", kb.back("promos"))

            elif d.startswith("promo_toggle_"):
                pid = int(d.split("_")[-1])
                promo = db.get_promos()
                for p in promo:
                    if p["id"] == pid:
                        from database import Database
                        conn = db._conn()
                        new_val = 0 if p["is_active"] else 1
                        conn.execute("UPDATE promo_codes SET is_active = ? WHERE id = ?", (new_val, pid))
                        conn.commit()
                        conn.close()
                        bot.answer_callback_query(c.id, "✅ Включён" if new_val else "⏸ Выключен")
                        break

            elif d.startswith("promo_del_"):
                pid = int(d.split("_")[-1])
                _edit(chat_id, msg_id, f"🗑 Удалить промокод #{pid}?", kb.confirm("promo_del", pid))

            elif d.startswith("promo_") and d.count("_") == 1:
                pid = int(d.split("_")[1])
                promos = db.get_promos()
                for p in promos:
                    if p["id"] == pid:
                        val_str = f"{p['discount_value']}%" if p["discount_type"] == "percent" else f"{p['discount_value']}₽"
                        text = (
                            f"🎫 <b>Промокод</b>\n\n"
                            f"🏷 Код: <code>{p['code']}</code>\n"
                            f"💰 Скидка: {val_str}\n"
                            f"📊 Использований: {p['used_count']}/{p['max_uses']}\n"
                            f"👤 Для: {p['for_username'] or 'все'}\n"
                            f"📅 До: {p['valid_until'][:10] if p['valid_until'] else '∞'}\n"
                            f"{'✅ Активен' if p['is_active'] else '⏸ Выключен'}"
                        )
                        _edit(chat_id, msg_id, text, kb.promo_item(pid))
                        break

            # ==================== ДОПЫ ====================
            elif d == "upsells":
                _edit(chat_id, msg_id, "🎁 <b>Дополнительные предложения</b>\n\n⭐ Триггер: отзыв 5 звёзд", kb.upsells_menu())

            elif d == "upsell_list":
                upsells = db.get_upsells()
                if not upsells:
                    _edit(chat_id, msg_id, "📭 Допов нет.", kb.upsells_menu())
                else:
                    text = "🎁 <b>Допы</b>\n\n"
                    for u in upsells:
                        st = "✅" if u["is_active"] else "⏸"
                        text += f"{st} <b>{u['name']}</b> — {u['discount_value']}% (показано: {u['times_shown']}, конверсия: {u['times_used']})\n"
                    text = "🎁 <b>Допы</b>\n\n"
                    for u in upsells:
                        st = "✅" if u["is_active"] else "⏸"
                        min_amount = float(u.get("min_order_amount") or 0)
                        issue_text = f"от {min_amount:.0f}₽"
                        apply_min = float(u.get("promo_apply_min_amount") or 0)
                        apply_max = float(u.get("promo_apply_max_amount") or 0)
                        apply_text = _amount_range_text(apply_min, apply_max)
                        text += f"{st} <b>{u['name']}</b> — {u['discount_value']}% | выдача: {issue_text} | применение: {apply_text} | {_promo_uses_text(u.get('promo_max_uses'))} (показано: {u['times_shown']}, использовано: {u['times_used']})\n"
                    ikb = telebot.types.InlineKeyboardMarkup(row_width=1)
                    for u in upsells:
                        ikb.add(telebot.types.InlineKeyboardButton(f"{'✅' if u['is_active'] else '⏸'} {u['name']}", callback_data=f"upsell_{u['id']}"))
                    ikb.row(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data="upsells"))
                    _edit(chat_id, msg_id, text, ikb)

            elif d == "upsell_rule_add":
                set_state(uid, "upsell_rule_min")
                _edit(chat_id, msg_id, "💸 Введите <b>цену заказа ОТ</b> в ₽, после которой будет доступен бонусный промокод:", kb.back("upsells"))

            elif d == "upsell_add":
                set_state(uid, "upsell_add_name")
                _edit(chat_id, msg_id, "📝 Введите <b>название допа</b>:", kb.back("upsells"))

            elif d.startswith("upsell_toggle_"):
                uid_u = int(d.split("_")[-1])
                u = db.get_upsell(uid_u)
                if u:
                    db.update_upsell(uid_u, is_active=0 if u["is_active"] else 1)
                    bot.answer_callback_query(c.id, "✅" if not u["is_active"] else "⏸")

            elif d.startswith("upsell_del_"):
                uid_u = int(d.split("_")[-1])
                _edit(chat_id, msg_id, f"🗑 Удалить доп #{uid_u}?", kb.confirm("upsell_del", uid_u))

            elif d.startswith("upsell_") and d.count("_") == 1:
                uid_u = int(d.split("_")[1])
                u = db.get_upsell(uid_u)
                if u:
                    min_amount = float(u.get("min_order_amount") or 0)
                    issue_text = f"от {min_amount:.0f}₽"
                    apply_min = float(u.get("promo_apply_min_amount") or 0)
                    apply_max = float(u.get("promo_apply_max_amount") or 0)
                    apply_text = _amount_range_text(apply_min, apply_max)
                    text = (
                        f"🎁 <b>{u['name']}</b>\n\n"
                        f"💰 Бонус: {u['discount_value']}%\n"
                        f"📊 Выдача бонуса: {issue_text}\n"
                        f"🧾 Применение: {apply_text}\n"
                        f"♻️ Применений: {_promo_uses_text(u.get('promo_max_uses'))}\n"
                        f"📈 Показано: {u['times_shown']} | Использовано: {u['times_used']}\n"
                        f"{'✅ Активен' if u['is_active'] else '⏸ Выключен'}\n\n"
                        "⭐ Триггер: отзыв 5 звёзд на FunPay"
                    )
                    _edit(chat_id, msg_id, text, kb.upsell_item(uid_u))

            # ==================== ШАБЛОНЫ ====================
            elif d == "templates":
                _edit(chat_id, msg_id, "💬 <b>Шаблоны сообщений</b>", kb.templates_menu())

            elif d == "tpl_list":
                tpls = db.get_templates()
                if not tpls:
                    # Инициализируем дефолтные
                    for name, text in cfg._data.get("messages", {}).items():
                        db.upsert_template(name, text, msg_type=name)
                    tpls = db.get_templates()
                ikb = telebot.types.InlineKeyboardMarkup(row_width=1)
                for t in tpls:
                    st = "✅" if t["is_active"] else "⏸"
                    ikb.add(telebot.types.InlineKeyboardButton(f"{st} {t['name']}", callback_data=f"tpl_{t['name']}"))
                ikb.row(telebot.types.InlineKeyboardButton("◀️ Назад", callback_data="templates"))
                _edit(chat_id, msg_id, f"💬 <b>Шаблоны ({len(tpls)})</b>", ikb)

            elif d == "tpl_add":
                set_state(uid, "tpl_add_name")
                _edit(chat_id, msg_id, "📝 Введите <b>имя шаблона</b> (латиница, _):", kb.back("templates"))

            elif d.startswith("tpl_edit_"):
                tpl_name = d[9:]
                set_state(uid, "tpl_edit_text", tpl_name=tpl_name)
                tpl = db.get_template(tpl_name)
                current = tpl["text"][:200] if tpl else "—"
                _edit(chat_id, msg_id, f"✏️ <b>{tpl_name}</b>\n\nТекущий:\n<code>{current}</code>\n\nВведите новый текст:", kb.back("templates"))

            elif d.startswith("tpl_toggle_"):
                tpl_name = d[11:]
                tpl = db.get_template(tpl_name)
                if tpl:
                    new_val = 0 if tpl["is_active"] else 1
                    db.upsert_template(tpl_name, tpl["text"], tpl["msg_type"], new_val)
                    bot.answer_callback_query(c.id, "✅" if new_val else "⏸")

            elif d.startswith("tpl_") and d.count("_") == 1:
                tpl_name = d[4:]
                tpl = db.get_template(tpl_name)
                if tpl:
                    text = (
                        f"💬 <b>{tpl['name']}</b>\n"
                        f"{'✅ Активен' if tpl['is_active'] else '⏸ Выключен'}\n\n"
                        f"<code>{tpl['text'][:500]}</code>"
                    )
                    _edit(chat_id, msg_id, text, kb.template_item(tpl_name))

            # ==================== СТАТИСТИКА ====================
            elif d == "stats":
                _edit(chat_id, msg_id, "📊 <b>Статистика</b>", kb.stats_menu())

            elif d == "stats_all":
                _show_stats(chat_id, days=None, msg_id=msg_id)

            elif d == "stats_today":
                _show_stats(chat_id, days=1, msg_id=msg_id)

            elif d == "stats_week":
                _show_stats(chat_id, days=7, msg_id=msg_id)

            elif d == "stats_month":
                _show_stats(chat_id, days=30, msg_id=msg_id)

            elif d == "export_orders":
                bot.answer_callback_query(c.id, "⏳ Экспорт...")
                path = db.export_orders_csv()
                if path:
                    with open(path, "rb") as f:
                        bot.send_document(chat_id, f, caption="📤 Экспорт заказов")
                else:
                    bot.send_message(chat_id, "📭 Нет данных для экспорта.")

            elif d == "export_stats":
                bot.answer_callback_query(c.id, "⏳ Экспорт...")
                path = db.export_stats_json()
                if path:
                    with open(path, "rb") as f:
                        bot.send_document(chat_id, f, caption="📤 Экспорт статистики")

            # ==================== FUNPAY ====================
            elif d == "funpay":
                connected = fp is not None and fp._initiated
                if connected:
                    active_lots = db.get_lots(active_only=True)
                    bound_count = sum(1 for lot in active_lots if lot.get("funpay_lot_id"))
                    profile_lots = fp.get_profile_lots()
                    profile_lots_count = profile_lots.get("count", len(profile_lots.get("lots", []))) if profile_lots.get("success") else "?"
                    text = (
                        f"🎮 <b>FunPay</b>\n\n"
                        f"👤 {fp.username}\n"
                        f"🆔 {fp.user_id}\n"
                        f"💰 {fp.balance}₽\n\n"
                        f"🔄 Авто-обработка: {'✅' if cfg.funpay_auto_process else '❌'}\n"
                        f"⏱ Интервал: {cfg.get('funpay_check_interval', 30)} сек\n"
                        f"🛒 Лотов на FunPay: {profile_lots_count}\n"
                        f"🔗 Привязанных лотов: {bound_count}"
                    )
                else:
                    text = "🎮 <b>FunPay</b>\n\n⚠️ Не подключен.\nВведите Golden Key для подключения."
                _edit(chat_id, msg_id, text, _kb_funpay_runtime(connected))

            elif d == "fp_refresh":
                if not fp:
                    bot.answer_callback_query(c.id, "⚠️ FunPay не подключен")
                    return
                bot.answer_callback_query(c.id, "🔄 Обновление...")
                r = fp.test_connection()
                if r["success"]:
                    text = (
                        f"🎮 <b>FunPay</b>\n\n"
                        f"👤 {r['username']}\n"
                        f"🆔 {r['user_id']}\n"
                        f"💰 {r['balance']}₽\n"
                        f"🛒 {r.get('lots_count', 0)} лотов\n\n"
                        f"✅ Подключение обновлено"
                    )
                else:
                    text = f"❌ Ошибка: {r['error']}"
                _edit(chat_id, msg_id, text, _kb_funpay_runtime(fp._initiated))

            elif d == "fp_withdraw":
                if not fp:
                    bot.answer_callback_query(c.id, "⚠️ FunPay не подключен")
                    return
                _edit(chat_id, msg_id, _render_funpay_withdraw_text(), _kb_funpay_withdraw_menu())

            elif d == "fpw_wallet":
                set_state(uid, "fpw_wallet")
                _edit(chat_id, msg_id, "📱 Введите номер телефона для вывода через СБП:", kb.back("fp_withdraw"))

            elif d == "fpw_wallet_extra":
                set_state(uid, "fpw_wallet_extra")
                banks = _funpay_withdraw_options().get("banks", [])
                bank_hint = ""
                if banks:
                    top = ", ".join(str(item.get("label") or "") for item in banks[:8] if str(item.get("label") or "").strip())
                    if top:
                        bank_hint = "\n\nПопулярные варианты:\n" + top
                _edit(
                    chat_id,
                    msg_id,
                    "🏦 Введите банк или код банка для СБП.\n\n"
                    "Например: Сбербанк, Т-Банк, Альфа-Банк или 100000000004."
                    f"{bank_hint}\n\n"
                    "Бот сам сохранит нужный код FunPay.",
                    kb.back("fp_withdraw")
                )

            elif d == "fpw_amount":
                set_state(uid, "fpw_amount")
                _edit(chat_id, msg_id, "💰 Введите сумму вывода в рублях:", kb.back("fp_withdraw"))

            elif d == "fpw_2fa":
                set_state(uid, "fpw_2fa")
                _edit(chat_id, msg_id, "🔐 Введите код 2FA.\n\nЕсли не нужен, отправьте 0.", kb.back("fp_withdraw"))

            elif d == "fpw_auto_toggle":
                current = bool(cfg.get("funpay_withdraw.auto_enabled", False))
                cfg.set("funpay_withdraw.auto_enabled", not current)
                _edit(chat_id, msg_id, _render_funpay_withdraw_text(), _kb_funpay_withdraw_menu())

            elif d == "fpw_auto_min":
                set_state(uid, "fpw_auto_min")
                _edit(
                    chat_id,
                    msg_id,
                    "🎯 Введите сумму баланса FunPay, от которой нужно автоматически отправлять вывод.\n\n"
                    "Например: <b>1000</b>\n"
                    "Если баланс достигнет этого значения или станет выше, бот отправит вывод на сумму из поля «Сумма».",
                    kb.back("fp_withdraw"),
                )

            elif d == "fpw_preview":
                if not fp:
                    bot.answer_callback_query(c.id, "⚠️ FunPay не подключен")
                    return
                data = _funpay_withdraw_cfg()
                wallet = str(data.get("wallet") or "").strip()
                wallet_extra = str(data.get("wallet_extra") or "").strip()
                amount_int = int(data.get("amount_int") or 0)
                if not wallet or not wallet_extra or amount_int <= 0:
                    _edit(
                        chat_id,
                        msg_id,
                        "⚠️ Для preview нужно задать телефон, банк и сумму.",
                        _kb_funpay_withdraw_menu(),
                    )
                    return
                bot.answer_callback_query(c.id, "⏳ Проверяю вывод...")
                result = fp.preview_withdraw(
                    currency_id=str(data.get("currency_id") or "rub"),
                    ext_currency_id=str(data.get("ext_currency_id") or "fps"),
                    wallet=wallet,
                    wallet_extra=wallet_extra,
                    amount_int=amount_int,
                    twofactor_code=str(data.get("twofactor_code") or "").strip(),
                )
                _edit(chat_id, msg_id, _render_funpay_withdraw_text(result), _kb_funpay_withdraw_menu())

            elif d == "fpw_submit":
                if not fp:
                    bot.answer_callback_query(c.id, "⚠️ FunPay не подключен")
                    return
                data = _funpay_withdraw_cfg()
                wallet = str(data.get("wallet") or "").strip()
                wallet_extra = str(data.get("wallet_extra") or "").strip()
                amount_int = int(data.get("amount_int") or 0)
                if not wallet or not wallet_extra or amount_int <= 0:
                    _edit(
                        chat_id,
                        msg_id,
                        "⚠️ Для вывода нужно задать телефон, банк и сумму.",
                        _kb_funpay_withdraw_menu(),
                    )
                    return
                bot.answer_callback_query(c.id, "⏳ Отправляю вывод...")
                result = fp.create_withdraw(
                    currency_id=str(data.get("currency_id") or "rub"),
                    ext_currency_id=str(data.get("ext_currency_id") or "fps"),
                    wallet=wallet,
                    wallet_extra=wallet_extra,
                    amount_int=amount_int,
                    twofactor_code=str(data.get("twofactor_code") or "").strip(),
                    preview=False,
                )
                text = _render_funpay_withdraw_text()
                payload = result.get("data") or {}
                if result.get("success") and isinstance(payload, dict) and payload.get("error") is False:
                    bank_name = str(payload.get("fps_bank_name") or _funpay_withdraw_bank_label(wallet_extra) or "").strip()
                    amount_ext = str(payload.get("amount_ext") or "").strip()
                    lines = [
                        text,
                        "",
                        "──────────",
                        "✅ <b>Вывод отправлен</b>",
                    ]
                    if bank_name:
                        lines.append(f"🏦 Банк: <b>{html.escape(bank_name)}</b>")
                    if amount_int:
                        lines.append(f"💸 Списано: <b>{amount_int}</b> ₽")
                    if amount_ext:
                        lines.append(f"📥 К получению: <b>{html.escape(amount_ext)}</b> ₽")
                    _edit(chat_id, msg_id, "\n".join(lines), _kb_funpay_withdraw_menu())
                else:
                    error_text = ""
                    if isinstance(payload, dict):
                        error_text = str(payload.get("msg") or "").strip()
                    if not error_text:
                        error_text = str(result.get("error") or result.get("raw_text") or "неизвестная ошибка")
                    _edit(
                        chat_id,
                        msg_id,
                        _render_funpay_withdraw_text() + "\n\n──────────\n❌ <b>Вывод не отправлен</b>\n" + html.escape(error_text),
                        _kb_funpay_withdraw_menu(),
                    )

            elif d == "fp_sales":
                if not fp:
                    bot.answer_callback_query(c.id, "⚠️ FunPay не подключен")
                    return
                bot.answer_callback_query(c.id, "⏳ Загрузка...")
                r = fp.get_sales()
                if r["success"]:
                    orders = r["orders"]
                    if not orders:
                        _edit(chat_id, msg_id, "📭 Нет заказов.", kb.back("funpay"))
                    else:
                        paid = sum(1 for o in orders if o.status == "paid")
                        text = f"📋 <b>Продажи FunPay</b> ({len(orders)})\n💳 Оплачено: {paid}"
                        _edit(chat_id, msg_id, text, kb.funpay_sales(orders))
                else:
                    _edit(chat_id, msg_id, f"❌ {r['error']}", kb.back("funpay"))

            elif d.startswith("fp_salep_"):
                page = int(d.split("_")[-1])
                if fp:
                    r = fp.get_sales()
                    if r["success"]:
                        _edit(chat_id, msg_id, f"📋 <b>Продажи FunPay</b>", kb.funpay_sales(r["orders"], page))

            elif d.startswith("fp_order_"):
                oid = d[9:]
                if not fp:
                    return
                r = fp.get_order_details(oid)
                if r["success"]:
                    status_labels = {"paid": "💳 Оплачен", "closed": "✅ Закрыт", "refunded": "↩️ Возврат"}
                    text = (
                        f"📦 <b>Заказ #{r['order_id']}</b>\n\n"
                        f"📝 {r['description'][:60]}\n"
                        f"👤 {r['buyer_username']}\n"
                        f"💰 {r['price']} {r['currency']}\n"
                        f"📊 {status_labels.get(r['status'], r['status'])}\n"
                    )
                    if r['review_stars']:
                        text += f"⭐ Отзыв: {'⭐' * r['review_stars']}\n"

                    bound = _find_lot_by_funpay_lot_id(str(r.get("offer_id", "")))
                    if bound:
                        text += f"\n🔗 Привязан к: <b>{bound['name']}</b>"
                    else:
                        text += "\n⚠️ Нет привязки к лоту бота"
                    _edit(chat_id, msg_id, text, kb.funpay_order(oid, r["status"]))
                else:
                    _edit(chat_id, msg_id, f"❌ {r['error']}", kb.back("fp_sales"))

            elif d.startswith("fp_process_"):
                oid = d[11:]
                bot.answer_callback_query(c.id, "⚡ Обработка...")
                Thread(target=_process_funpay_order, args=(chat_id, msg_id, oid), daemon=True).start()

            elif d.startswith("fp_msg_"):
                oid = d[7:]
                set_state(uid, "fp_send_msg", fp_order_id=oid)
                _edit(chat_id, msg_id, f"💬 Введите сообщение для покупателя (заказ #{oid}):", kb.back("fp_sales"))

            elif d == "fp_lots":
                if not fp:
                    bot.answer_callback_query(c.id, "⚠️ FunPay не подключен")
                    return
                bot.answer_callback_query(c.id, "⏳ Загрузка...")
                r = fp.get_profile_lots()
                if r["success"]:
                    if not r["lots"]:
                        _edit(chat_id, msg_id, "📭 Нет лотов на FunPay.", kb.back("funpay"))
                    else:
                        lots = r["lots"]
                        text = f"🛒 <b>Лоты FunPay</b> ({r.get('count', len(lots))})\n\n"
                        for lot in lots:
                            text += f"#{lot['offer_id']} — {lot['title'][:40]} ({lot['price']} {lot['currency']})\n"
                        text += "\nВведите ID лота FunPay, который нужно связать с бот-лотом."
                        set_state(uid, "fp_bind_auto", funpay_lots=lots)
                        _edit(chat_id, msg_id, text, kb.back("funpay"))
                else:
                    _edit(chat_id, msg_id, f"❌ {r['error']}", kb.back("funpay"))

            # ==================== ПРИВЯЗКИ (авто) ====================
            elif get_state(uid) == "fp_bind_auto":
                lots = data.get("funpay_lots", [])
                funpay_id = c.message.text.strip()
                lot_info = next((lot for lot in lots if str(lot.get("offer_id")) == funpay_id), None)
                if not lot_info:
                    bot.send_message(c.message.chat.id, "❌ Лот не найден. Введите ID из списка.", reply_markup=kb.back("funpay"))
                    return
                active_lots = db.get_lots(active_only=True)
                if not active_lots:
                    bot.send_message(c.message.chat.id, "⚠️ Нет активных лотов бота. Сначала создайте лот.")
                    return
                text = "Выберите лот бота для привязки:\n\n"
                for bot_lot in active_lots[:15]:
                    text += f"#{bot_lot['id']} — {bot_lot['name']}\n"
                set_state(c.message.chat.id, "fp_bind_select_bot", funpay_lot=lot_info)
                bot.send_message(c.message.chat.id, text, reply_markup=kb.back("funpay"))


            # ==================== НАСТРОЙКИ ====================
            elif d == "settings":
                clear_state(uid)
                bal_text = ""
                if cfg.twiboost_api_key:
                    bal_text = f"\n🔑 API: ...{cfg.twiboost_api_key[-6:]}"
                fp_text = ""
                if fp and fp._initiated:
                    fp_text = f"\n🎮 FunPay: {fp.username}"
                else:
                    fp_text = "\n🎮 FunPay: ❌ не подключен"
                text = (
                    f"⚙️ <b>Настройки</b>\n"
                    f"{bal_text}"
                    f"{fp_text}\n"
                    f"💱 Курс: {cfg.get('usd_rub_rate', 92)}₽/$\n"
                    f"📚 База знаний: {'вкл' if _knowledge_enabled() else 'выкл'}\n"
                    f"⏱ Проверка заказов: каждые {cfg.get('order_check_interval', 60)} сек\n"
                    f"⏱ Проверка FunPay: каждые {cfg.get('funpay_check_interval', 30)} сек\n"
                    f"💰 Порог: ${cfg.get('low_balance_threshold', 5)}"
                )
                _edit(chat_id, msg_id, text, kb.settings_menu())

            elif d == "set_golden_key":
                set_state(uid, "set_golden_key")
                _edit(chat_id, msg_id, "🎮 Введите <b>Golden Key</b> FunPay:\n\n<i>Найти: Куки браузера → funpay.com → golden_key</i>", kb.back("settings"))

            elif d == "set_fp_interval":
                set_state(uid, "set_fp_interval")
                _edit(chat_id, msg_id, f"⏱ Текущий: <b>{cfg.get('funpay_check_interval', 30)} сек</b>\n\nВведите новый (секунды):", kb.back("settings"))
                
            
                
            elif d == "set_api_key":
                set_state(uid, "set_api_key")
                _edit(chat_id, msg_id, "🔑 Введите <b>API ключ</b> TwiBoost:", kb.back("settings"))
                
            elif d == "set_smmway_key":
                set_state(uid, "set_smmway_key")
                _edit(chat_id, msg_id, "🔑 Введите API ключ SmmWay:", kb.back("settings"))

            elif d == "set_usd_rate":
                set_state(uid, "set_usd_rate")
                _edit(chat_id, msg_id, f"💱 Текущий курс: <b>{cfg.get('usd_rub_rate', 92)}₽</b>\n\nВведите новый:", kb.back("settings"))

            elif d == "set_check_interval":
                set_state(uid, "set_check_interval")
                _edit(chat_id, msg_id, f"⏱ Текущий: <b>{cfg.get('order_check_interval', 60)} сек</b>\n\nВведите новый (секунды):", kb.back("settings"))

            elif d == "set_low_balance":
                set_state(uid, "set_low_balance")
                _edit(chat_id, msg_id, f"💰 Текущий: <b>${cfg.get('low_balance_threshold', 5)}</b>\n\nВведите новый ($):", kb.back("settings"))

            elif d == "set_notif":
                _edit(chat_id, msg_id, _render_notifications_text(), kb.notif_settings(cfg._data))

            elif d.startswith("notif_toggle_"):
                key = d[13:]
                current = cfg.get(f"notifications.{key}", True)
                cfg.set(f"notifications.{key}", not current)
                _edit(chat_id, msg_id, _render_notifications_text(), kb.notif_settings(cfg._data))

            elif d == "kb_settings":
                clear_state(uid)
                _edit(chat_id, msg_id, _render_knowledge_base_text(), _kb_knowledge_base_menu(_knowledge_entries()))

            elif d == "kb_toggle":
                cfg.set("knowledge_base.enabled", not _knowledge_enabled())
                _edit(chat_id, msg_id, _render_knowledge_base_text(), _kb_knowledge_base_menu(_knowledge_entries()))

            elif d == "kb_greeting":
                set_state(uid, "kb_set_greeting")
                current = _knowledge_greeting_text() or "не задано"
                _edit(
                    chat_id,
                    msg_id,
                    "👋 <b>Текст приветствия</b>\n\n"
                    "Этот текст бот отправит на сообщения вроде <code>привет</code>.\n\n"
                    f"Текущее значение:\n{html.escape(current)}\n\n"
                    "Отправьте новый текст одним сообщением.",
                    kb.back("kb_settings"),
                )

            elif d == "kb_add":
                set_state(uid, "kb_add_title")
                _edit(
                    chat_id,
                    msg_id,
                    "➕ <b>Новая запись базы знаний</b>\n\n"
                    "Шаг 1/3. Отправьте название ответа.\n"
                    "Например: <code>Автовыдача</code>",
                    kb.back("kb_settings"),
                )

            elif d.startswith("kb_del_"):
                try:
                    idx = int(d.split("_")[-1])
                except Exception:
                    idx = -1
                entries = list(_knowledge_entries())
                if 0 <= idx < len(entries):
                    entries.pop(idx)
                    _save_knowledge_entries(entries)
                    bot.answer_callback_query(c.id, "🗑 Запись удалена")
                else:
                    bot.answer_callback_query(c.id, "⚠️ Запись не найдена")
                _edit(chat_id, msg_id, _render_knowledge_base_text(), _kb_knowledge_base_menu(_knowledge_entries()))

            elif d == "backup":
                bot.answer_callback_query(c.id, "⏳ Бэкап...")
                path = db.backup()
                with open(path, "rb") as f:
                    bot.send_document(chat_id, f, caption="💾 Резервная копия БД")

            # ==================== ЛОГИ ====================
            elif d == "logs":
                _edit(chat_id, msg_id, "📋 <b>Логи</b>", kb.logs_menu())

            elif d == "logs_all":
                logs = db.get_logs(limit=20)
                text = "📋 <b>Последние логи</b>\n\n"
                for lg in logs:
                    icon = "❌" if lg["level"] == "ERROR" else "⚠️" if lg["level"] == "WARNING" else "ℹ️"
                    text += f"{icon} <code>{lg['created_at'][11:19]}</code> {lg['message'][:60]}\n"
                if not logs:
                    text += "Пусто."
                _edit(chat_id, msg_id, text, kb.logs_menu())

            elif d == "logs_errors":
                logs = db.get_logs(level="ERROR", limit=20)
                text = "❌ <b>Ошибки</b>\n\n"
                for lg in logs:
                    text += f"<code>{lg['created_at'][11:19]}</code> {lg['message'][:60]}\n"
                if not logs:
                    text += "Ошибок нет ✅"
                _edit(chat_id, msg_id, text, kb.logs_menu())

            elif d == "logs_cleanup":
                db.cleanup_old_logs(7)
                bot.answer_callback_query(c.id, "🧹 Очищено")

            # ==================== ПОДТВЕРЖДЕНИЯ ====================
            elif d.startswith("confirm_lot_del_"):
                lot_id = int(d.split("_")[-1])
                db.delete_lot(lot_id)
                _edit(chat_id, msg_id, "✅ Лот удалён.", kb.lots_menu())

            elif d.startswith("confirm_order_cancel_"):
                order_id = int(d.split("_")[-1])
                Thread(target=_cancel_order, args=(chat_id, msg_id, order_id), daemon=True).start()

            elif d.startswith("confirm_promo_del_"):
                pid = int(d.split("_")[-1])
                db.delete_promo(pid)
                _edit(chat_id, msg_id, "✅ Промокод удалён.", kb.promos_menu())

            elif d.startswith("confirm_upsell_del_"):
                uid_u = int(d.split("_")[-1])
                db.delete_upsell(uid_u)
                _edit(chat_id, msg_id, "✅ Доп удалён.", kb.upsells_menu())

            elif d.startswith("cancel_"):
                # Отмена подтверждения — назад
                _edit(chat_id, msg_id, "❌ Отменено.", kb.main_menu())

        except Exception as e:
            logger.error(f"Callback error: {e}", exc_info=True)
            try:
                bot.answer_callback_query(c.id, f"❌ Ошибка: {str(e)[:50]}")
            except Exception:
                pass


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def _edit(chat_id, msg_id, text, markup=None):
    try:
        bot.edit_message_text(text, chat_id, msg_id, parse_mode="HTML", reply_markup=markup)
    except Exception:
        bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)


def _show_balance(chat_id, msg_id=None):
    lines = ["💰 <b>Балансы API</b>", ""]
    
    # 🔹 TwiBoost
    tb_client = get_api_client("twiboost")
    if tb_client and cfg.twiboost_api_key:
        r = tb_client.get_balance()
        if r.get("success"):
            lines.append(f"🌐 TwiBoost: <b>{r['balance']:.2f} {r.get('currency', 'USD')}</b>")
        else:
            lines.append("🌐 TwiBoost: ⚠️ Ошибка API")
    else:
        lines.append("🌐 TwiBoost: ❌ Ключ не настроен")

    # 🔹 SmmWay
    sw_client = get_api_client("smmway")
    if sw_client and cfg.get("smmway_api_key"):
        r = sw_client.get_balance()
        if r.get("success"):
            lines.append(f"🌐 SmmWay: <b>{r['balance']:.2f} {r.get('currency', 'USD')}</b>")
        else:
            lines.append("🌐 SmmWay: ⚠️ Ошибка API")
    else:
        lines.append("🌐 SmmWay: ❌ Ключ не настроен")

    active = len(db.get_active_orders()) if db else 0
    lines.extend(["", f"📦 Активных заказов: <b>{active}</b>"])

    text = "\n".join(lines)
    if msg_id:
        _edit(chat_id, msg_id, text, kb.back("main"))
    else:
        bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb.back("main"))


def _show_lot(chat_id, msg_id, lot_id):
    lot = db.get_lot(lot_id)
    if not lot:
        _edit(chat_id, msg_id, "❌ Лот не найден.", kb.lots_menu())
        return
    status = "✅ Активен" if lot["is_active"] else "⏸ Выключен"
    text = (
        f"🛒 <b>Лот #{lot['id']}</b>\n\n"
        f"📝 {lot['name']}\n"
        f"{'━' * 25}\n"
        f"🌐 Сервис: #{lot['api_service_id']} {lot['api_service_name'][:30]}\n"
        f"🏷 Тип: {lot.get('service_type') or 'default'}\n"
        f"💰 Цена: <b>{_lot_price_per_1000(lot)}₽/1000</b>\n"
        f"💵 Себестоимость: {lot['api_rate']}₽/1000\n"
        f"📈 Наценка: {lot['markup']}%\n"
        f"📊 {lot['min_quantity']} — {lot['max_quantity']}\n"
        f"📂 {lot['category'][:40]}\n"
        f"📱 {lot['platform']}\n"
        + (f"🗳 Вариант голоса: {lot.get('vote_answer_number')}\n" if lot.get("vote_answer_number") else "")
        + "\n"
        f"{status}"
    )
    _edit(chat_id, msg_id, text, kb.lot_item(lot_id))


def _show_lot_review_bonus_editor(chat_id, msg_id, lot_id):
    lot = db.get_lot(lot_id)
    if not lot:
        _edit(chat_id, msg_id, "❌ Лот не найден.", kb.lots_menu())
        return
    text = (
        f"🎁 <b>Бонус за отзыв для лота #{lot['id']}</b>\n\n"
        f"📝 {lot['name']}\n"
        f"{'━' * 25}\n"
        f"{_lot_review_bonus_card_text(lot)}\n\n"
        "После 5★ покупатель получит не обычный промокод, а отдельный бонусный запуск.\n"
        "Бот попросит ссылку и создаст бонусный заказ по настроенному сервису."
    )
    _edit(chat_id, msg_id, text, _kb_lot_review_bonus(lot_id, enabled=_lot_review_bonus_enabled(lot)))


def _show_lot(chat_id, msg_id, lot_id):
    lot = db.get_lot(lot_id)
    if not lot:
        _edit(chat_id, msg_id, "❌ Лот не найден.", kb.lots_menu())
        return
    status = "✅ Активен" if lot["is_active"] else "⏸ Выключен"
    mode_title = _lot_order_mode_title(lot)
    text = (
        f"🛒 <b>Лот #{lot['id']}</b>\n\n"
        f"📝 {lot['name']}\n"
        f"{'━' * 25}\n"
        f"🌐 Сервис: #{lot['api_service_id']} {lot['api_service_name'][:30]}\n"
        f"🏷 Тип API: {lot.get('service_type') or 'default'}\n"
        f"🗳 Режим: {mode_title}\n"
        f"➗ Разделение: {'включено' if _lot_split_enabled(lot) else 'выключено'}\n"
        f"💰 Цена: <b>{_lot_price_per_1000(lot)}₽/1000</b>\n"
        f"💵 Себестоимость: {lot['api_rate']}₽/1000\n"
        f"📈 Наценка: {lot['markup']}%\n"
        f"📊 {lot['min_quantity']} — {lot['max_quantity']}\n"
        f"📂 {lot['category'][:40]}\n"
        f"📱 {lot['platform']}\n"
        f"{_lot_review_bonus_card_text(lot)}\n\n"
        f"{status}"
    )
    _edit(chat_id, msg_id, text, kb.lot_item(lot_id))


def _show_order(chat_id, msg_id, order_id):
    order = db.get_order(order_id)
    if not order:
        _edit(chat_id, msg_id, "❌ Заказ не найден.", kb.orders_menu())
        return
    order = _refresh_order_from_api(order)
    allow_ticket = _order_has_ticket(order)
    progress = _get_order_progress(order)
    status_labels = {"pending": "⏳ Ожидание", "processing": "🔄 Обработка", "in_progress": "🔄 Выполняется", "completed": "✅ Выполнен", "partial": "⚠️ Частично", "failed": "❌ Ошибка", "cancelled": "🚫 Отменён"}
    lines = [
        f"📦 <b>Заказ #{order['id']}</b>",
        "",
        f"🤖 TwiBoost: #{order['api_order_id']}",
        f"🎮 FunPay: #{order['funpay_order_id'] or '—'}",
    ]
    if int(order.get("split_total") or 0) > 1:
        lines.append(f"➗ Часть: {order.get('split_index')}/{order.get('split_total')}")
    lines.extend([
        f"📦 Услуга: {order['service_name'][:60] or order['lot_name'][:60]}",
        f"🔗 Ссылка: {order['link'][:90]}",
        f"📊 Количество: {order['quantity']}",
        f"📈 Прогресс: <b>{progress}%</b>",
        f"{'━' * 25}",
        f"💰 Продано на FunPay: <b>{order['sell_price']:.2f}₽</b>",
        f"💵 Цена TwiBoost: <b>{order['cost_price']:.2f}₽</b>",
        f"📈 Прибыль: <b>{order['profit']:.2f}₽</b>",
        f"{'━' * 25}",
        f"📌 Статус: <b>{status_labels.get(order['status'], order['status'])}</b>",
        f"🔄 API: {order['api_status'] or '—'}",
        f"📊 Start: {order['api_start_count']} | Remains: {order['api_remains']}",
        f"🔁 Рефиллов: {order['refill_count']}",
        f"📅 Создан: {order['created_at'][:16]}",
    ])
    text = "\n".join(lines)
    if order.get("completed_at"):
        text += f"\n✅ Завершён: {order['completed_at'][:16]}"
    if order["error_message"]:
        text += f"\n⚠️ {order['error_message'][:100]}"
    _edit(chat_id, msg_id, text, kb.order_item(order_id, order["status"], allow_ticket=allow_ticket))


def _show_stats(chat_id, days=None, msg_id=None):
    s = db.get_stats_summary(days=days)
    period = "за всё время" if not days else f"за {days} дн."
    popular = db.get_most_popular_lot(days=days)
    text = (
        f"📊 <b>Статистика {period}</b>\n"
        f"{'━' * 28}\n\n"
        f"📦 Заказов: <b>{s.get('total_orders', 0)}</b>\n"
        f"✅ Выполнено: <b>{s.get('completed_orders', 0)}</b>\n"
        f"❌ Ошибок: <b>{s.get('failed_orders', 0)}</b>\n"
        f"🚫 Отменено: <b>{s.get('cancelled_orders', 0)}</b>\n\n"
        f"💰 Выручка: <b>{s.get('total_revenue', 0):.0f}₽</b>\n"
        f"💵 Расходы: <b>{s.get('total_cost', 0):.0f}₽</b>\n"
        f"📈 Прибыль: <b>{s.get('total_profit', 0):.0f}₽</b>\n\n"
        f"🎫 Промо: {s.get('promos_used', 0)} | 🎁 Допы: {s.get('upsells_shown', 0)}"
    )
    if popular:
        text += (
            f"\n\n🏆 Самый популярный лот:\n"
            f"🛒 {popular['lot_name']}\n"
            f"📦 Заказов: <b>{popular['total_orders']}</b>\n"
            f"💰 Выручка: <b>{popular['total_revenue']:.2f}₽</b>"
        )
    if msg_id:
        _edit(chat_id, msg_id, text, kb.stats_menu())
    else:
        bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb.stats_menu())


# ==================== ASYNC ОПЕРАЦИИ ====================

def _sync_services(chat_id, msg_id):
    if not api or not cfg.twiboost_api_key:
        _edit(chat_id, msg_id, "⚠️ API ключ не настроен.", kb.services_menu())
        return
    r = api.get_services()
    if not r["success"]:
        _edit(chat_id, msg_id, f"❌ Ошибка: {r['error']}", kb.services_menu())
        return
    count = 0
    for svc in r["services"]:
        platform = api.detect_platform(svc["category"])
        db.upsert_service(
            "twiboost", svc["service_id"],
            name=svc["name"], type=svc["type"], category=svc["category"],
            rate=svc["rate"], min_order=svc["min"], max_order=svc["max"],
            refill=int(svc["refill"]), cancel=int(svc["cancel"]), platform=platform
        )
        count += 1
    db.add_log("INFO", "sync", f"Синхронизировано {count} сервисов")
    _edit(chat_id, msg_id, f"✅ Загружено <b>{count}</b> сервисов!", kb.services_menu())


def _create_order_async(chat_id, lot, link, qty):
    if not api or not cfg.twiboost_api_key:
        bot.send_message(chat_id, "⚠️ API не настроен.", reply_markup=kb.back("orders"))
        return

    svc_id = lot["api_service_id"]
    r = _create_api_order_for_lot(lot, link, qty)

    usd_rub = cfg.get("usd_rub_rate", 92)
    cost = (lot["api_rate"] / 1000) * qty * usd_rub
    sell = (lot["price"] / 1000) * qty
    profit = sell - cost

    if r["success"]:
        oid = db.add_order(
            api_order_id=str(r["order_id"]),
            lot_id=lot["id"], lot_name=lot["name"],
            api_service_id=svc_id, service_name=lot["api_service_name"],
            link=link, quantity=qty,
            cost_price=cost, sell_price=sell, profit=profit,
            status="processing"
        )
        db.update_daily_stats(total_orders=1)
        db.add_log("INFO", "order", f"Заказ #{oid} создан: API #{r['order_id']}")
        text = (
            f"✅ <b>Заказ создан!</b>\n\n"
            f"🆔 #{oid} | API: #{r['order_id']}\n"
            f"📦 {lot['api_service_name'][:40]}\n"
            f"🔗 {link[:50]}\n"
            f"📊 {qty} шт\n"
            f"💰 {sell:.0f}₽ | 📈 {profit:.0f}₽"
        )
        bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb.order_item(oid))
    else:
        oid = db.add_order(
            lot_id=lot["id"], lot_name=lot["name"],
            api_service_id=svc_id, service_name=lot["api_service_name"],
            link=link, quantity=qty,
            cost_price=cost, sell_price=sell, profit=profit,
            status="failed", error_message=r.get("error", "")
        )
        db.update_daily_stats(total_orders=1, failed_orders=1)
        db.add_log("ERROR", "order", f"Ошибка: {r.get('error')}")
        text = f"❌ <b>Ошибка создания заказа</b>\n\n⚠️ {r.get('error', 'Неизвестная ошибка')}"
        bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb.back("orders"))


def _check_order(chat_id, msg_id, order_id):
    order = db.get_order(order_id)
    if not order or not order["api_order_id"]:
        _edit(chat_id, msg_id, "❌ Нет API ID.", kb.orders_menu())
        return

    # 🔥 Используем провайдер, указанный в заказе
    provider = str(order.get("api_provider") or "twiboost").lower()
    api_client = get_api_client(provider)
    
    if not api_client:
        _edit(chat_id, msg_id, f"❌ Клиент {provider} не настроен.", kb.orders_menu())
        return

    r = api_client.check_order_status(order["api_order_id"])
    if r["success"]:
        status_map = {
            "awaiting": "pending", "in progress": "in_progress", "completed": "completed",
            "partial": "partial", "canceled": "cancelled", "fail": "failed", "processing": "processing"
        }
        new_status = status_map.get(r["status"].lower(), order["status"])
        upd = {
            "api_status": r["status"], "api_charge": r["charge"],
            "api_start_count": r["start_count"], "api_remains": r["remains"],
            "currency": r.get("currency", order.get("currency", "RUB")),
            "status": new_status
        }
        upd = _update_order_finances_from_api(order, upd)
        if new_status == "completed" and order["status"] != "completed":
            upd["completed_at"] = datetime.now().isoformat()
            db.update_daily_stats(
                completed_orders=1, total_revenue=order["sell_price"],
                total_cost=upd.get("cost_price", order["cost_price"]),
                total_profit=upd.get("profit", order["profit"])
            )
        db.update_order(order_id, **upd)
        if new_status == "completed" and order["status"] != "completed":
            notify_funpay_order_completed(order_id, completed_at=upd["completed_at"])
        _show_order(chat_id, msg_id, order_id)
    else:
        _edit(chat_id, msg_id, f"❌ {r['error']}", kb.order_item(order_id, order["status"], allow_ticket=_order_has_ticket(order)))


def _refill_order(chat_id, msg_id, order_id):
    order = db.get_order(order_id)
    if not order or not order["api_order_id"]:
        return

    # 🔥 Используем провайдер, указанный в заказе
    provider = str(order.get("api_provider") or "twiboost").lower()
    api_client = get_api_client(provider)
    
    if not api_client: return # Или можно вывести ошибку

    r = api_client.refill_order(order["api_order_id"])
    if r["success"]:
        db.update_order(order_id, refill_count=order.get("refill_count", 0) + 1)
        db.add_log("INFO", "order", f"Рефилл #{order_id}: refill_id={r.get('refill_id')}")
        _edit(chat_id, msg_id, f"✅ Рефилл отправлен!\n\nRefill ID: {r.get('refill_id')}", kb.order_item(order_id, order["status"], allow_ticket=_order_has_ticket(order)))
    else:
        _edit(chat_id, msg_id, f"❌ {r['error']}", kb.order_item(order_id, order["status"], allow_ticket=_order_has_ticket(order)))


def _cancel_order(chat_id, msg_id, order_id):
    order = db.get_order(order_id)
    if not order or not order["api_order_id"]:
        return

    # 🔥 Используем провайдер, указанный в заказе
    provider = str(order.get("api_provider") or "twiboost").lower()
    api_client = get_api_client(provider)
    
    if not api_client: return

    r = api_client.cancel_order(order["api_order_id"])
    if r["success"]:
        db.update_order(order_id, status="cancelled")
        db.update_daily_stats(cancelled_orders=1)
        db.add_log("INFO", "order", f"Заказ #{order_id} отменён")
        _edit(chat_id, msg_id, f"✅ Заказ #{order_id} отменён.", kb.orders_menu())
    else:
        _edit(chat_id, msg_id, f"❌ {r.get('error', 'Ошибка отмены')}", kb.order_item(order_id, order["status"], allow_ticket=_order_has_ticket(order)))


def _check_all_orders(chat_id, msg_id):
    active = db.get_active_orders()
    if not active:
        _edit(chat_id, msg_id, "✅ Нет активных заказов.", kb.orders_menu())
        return
    api_ids = [o["api_order_id"] for o in active if o["api_order_id"]]
    if not api_ids:
        _edit(chat_id, msg_id, "✅ Нет заказов с API ID.", kb.orders_menu())
        return
    r = api.check_orders_status(api_ids)
    if not r["success"]:
        _edit(chat_id, msg_id, f"❌ {r['error']}", kb.orders_menu())
        return

    updated = 0
    status_map = {"awaiting": "pending", "in progress": "in_progress", "completed": "completed", "partial": "partial", "canceled": "cancelled", "fail": "failed"}
    for order in active:
        if order["api_order_id"] in r["orders"]:
            d = r["orders"][order["api_order_id"]]
            if d.get("status") == "Error":
                continue
            new_status = status_map.get(d["status"].lower(), order["status"])
            if new_status != order["status"]:
                upd = {
                    "status": new_status,
                    "api_status": d["status"],
                    "api_charge": d["charge"],
                    "api_start_count": d["start_count"],
                    "api_remains": d["remains"],
                    "currency": d.get("currency", order.get("currency", "RUB")),
                }
                upd = _update_order_finances_from_api(order, upd)
                if new_status == "completed":
                    upd["completed_at"] = datetime.now().isoformat()
                    db.update_daily_stats(
                        completed_orders=1,
                        total_revenue=order["sell_price"],
                        total_cost=upd.get("cost_price", order["cost_price"]),
                        total_profit=upd.get("profit", order["profit"])
                    )
                db.update_order(order["id"], **upd)
                if new_status == "completed":
                    notify_funpay_order_completed(order["id"], completed_at=upd["completed_at"])
                updated += 1

    text = f"✅ Проверено: <b>{len(api_ids)}</b> заказов\n🔄 Обновлено: <b>{updated}</b>"
    _edit(chat_id, msg_id, text, kb.orders_menu())


# ==================== FUNPAY АВТО-ОБРАБОТКА ====================

def _process_funpay_order(chat_id, msg_id, fp_order_id):
    """Обработать заказ FunPay — найти привязку, создать заказ в TwiBoost"""
    if not fp or not api:
        _edit(chat_id, msg_id, "❌ FunPay или TwiBoost API не подключен.", kb.back("fp_sales"))
        return

    r = fp.get_order_details(fp_order_id)
    if not r["success"]:
        _edit(chat_id, msg_id, f"❌ Ошибка: {r['error']}", kb.back("fp_sales"))
        return

    if r["status"] != "paid":
        _edit(chat_id, msg_id, f"⚠️ Заказ #{fp_order_id} не в статусе «оплачен» ({r['status']})", kb.back("fp_sales"))
        return

    description = r.get("description", "")
    buyer = r.get("buyer_username", "")
    price_fp = r.get("price", 0)
    service_name = _resolve_funpay_service_name(r, None, None)

    # Ищем привязку по ID лота FunPay
    matched_lot = _find_lot_by_funpay_lot_id(str(r.get("offer_id", "")))
    if not matched_lot:
        _edit(chat_id, msg_id, "⚠️ Нет привязки для заказа", kb.back("fp_sales"))
        return

    quantity = _extract_requested_quantity(r, None, matched_lot)
    quantity = max(quantity, matched_lot["min_quantity"])
    quantity = min(quantity, matched_lot["max_quantity"])

    # Нужна ссылка — для SMM услуг ссылку берём из описания заказа или buyer
    link = ""
    # Пробуем найти URL в описании
    import re
    urls = re.findall(r'https?://\S+', description)
    if urls:
        link = urls[0]

    if not link:
        _edit(chat_id, msg_id,
              f"⚠️ <b>Не найдена ссылка в заказе</b>\n\n"
              f"📦 #{fp_order_id}\n"
              f"📝 {description[:60]}\n"
              f"🔗 Услуга: {service_name}\n\n"
              f"Обработайте вручную или добавьте ссылку в описание лота FunPay.",
              kb.back("fp_sales"))
        return

    # Создаём заказ в TwiBoost
    svc_id = matched_lot["api_service_id"]
    api_r = _create_api_order_for_lot(matched_lot, link, quantity)

    usd_rub = cfg.get("usd_rub_rate", 92)
    cost = (matched_lot["api_rate"] / 1000) * quantity * usd_rub
    sell = price_fp
    profit = sell - cost

    if api_r["success"]:
        oid = db.add_order(
            api_order_id=str(api_r["order_id"]),
            lot_id=matched_lot["id"], lot_name=matched_lot["name"],
            api_service_id=svc_id, service_name=service_name,
            link=link, quantity=quantity,
            cost_price=cost, sell_price=sell, profit=profit,
            status="processing",
            funpay_order_id=fp_order_id, buyer_username=buyer
        )
        db.update_daily_stats(total_orders=1)
        db.add_log("INFO", "funpay", f"FunPay #{fp_order_id} → API #{api_r['order_id']} ({buyer})")
        _notify_admin_new_order(
            funpay_order_id=fp_order_id,
            api_order_id=api_r["order_id"],
            buyer=buyer,
            service_name=service_name,
            quantity=quantity,
            sell_price=sell,
            cost_price=cost,
            profit=profit,
            link=link,
            state_label="Новый заказ обработан",
        )

        text = (
            f"✅ <b>Заказ обработан!</b>\n\n"
            f"🎮 FunPay: #{fp_order_id}\n"
            f"👤 {buyer}\n"
            f"💰 {sell:.0f}₽\n\n"
            f"🤖 API: #{api_r['order_id']}\n"
            f"📦 {service_name[:60]}\n"
            f"🔗 {link[:50]}\n"
            f"📊 {quantity} шт\n"
            f"📈 Прибыль: <b>{profit:.0f}₽</b>"
        )
        _edit(chat_id, msg_id, text, kb.order_item(oid, allow_ticket=True))

        # Отправляем сообщение покупателю в FunPay
        tpl = cfg.get_message("order_created")
        if tpl and (r.get("chat_id") or r.get("buyer_id")):
            msg_text = tpl.replace("{order_id}", fp_order_id) \
                          .replace("{service_name}", service_name) \
                          .replace("{link}", link) \
                          .replace("{quantity}", str(quantity)) \
                          .replace("{price}", f"{sell:.0f}")
            chat_id_fp = r.get("chat_id") or fp.get_chat_id_by_username(r["buyer_id"])
            fp.send_message(chat_id_fp, msg_text, chat_name=r.get("buyer_username"))
    else:
        db.add_log("ERROR", "funpay", f"FunPay #{fp_order_id}: {api_r.get('error')}")
        text = (
            f"❌ <b>Ошибка обработки</b>\n\n"
            f"🎮 FunPay: #{fp_order_id}\n"
            f"👤 {buyer}\n"
            f"⚠️ {api_r.get('error', 'Неизвестная ошибка')}"
        )
        _edit(chat_id, msg_id, text, kb.back("fp_sales"))


def process_funpay_order_auto(fp_order, admin_chat_id=None, matched_lot=None, offer_id=None):
    """
    Автоматическая обработка нового заказа FunPay (вызывается из фонового потока).
    fp_order — объект OrderShortcut.
    """
    if not fp or not api:
        return False

    description = fp_order.description  # OrderShortcut has 'description' not 'full_description'
    buyer = fp_order.buyer_username
    price_fp = fp_order.price  # OrderShortcut has 'price' not 'sum'
    order_details = fp.get_order_details(fp_order.id) if fp_client_ready() else {"success": False}

    matched_lot = matched_lot or _match_funpay_bound_lot(
        description,
        price_fp,
        offer_id=offer_id,
        amount=order_details.get("amount") if order_details.get("success") else getattr(fp_order, "amount", None),
        short_description=order_details.get("short_description", "") if order_details.get("success") else "",
    )
    service_name = _resolve_funpay_service_name(order_details if order_details.get("success") else {}, fp_order, matched_lot)
    
    if not matched_lot:
        if admin_chat_id:
            bot.send_message(admin_chat_id,
                f"⚠️ <b>Новый заказ FunPay без привязки</b>\n\n"
                f"📦 #{fp_order.id}\n"
                f"📝 {description[:60]}\n"
                f"👤 {buyer}\n"
                f"💰 {price_fp}{_fp_currency_text(fp_order.currency)}",
                parse_mode="HTML", reply_markup=kb.funpay_order(fp_order.id, "paid"))
        return False

    quantity = _extract_requested_quantity(order_details if order_details.get( "success ") else {}, fp_order, matched_lot)
    # 🔥 ПРИМЕНЯЕМ МНОЖИТЕЛЬ ДЛЯ АВТО-ЗАКАЗОВ
    quantity_per_order = int(matched_lot.get("quantity_per_order") or 1)
    quantity = quantity * quantity_per_order
    quantity = max(quantity, matched_lot[ "min_quantity "])
    quantity = min(quantity, matched_lot[ "max_quantity "])

    # Ссылка из описания
    import re
    urls = re.findall(r'https?://\S+', description)
    link = urls[0] if urls else ""

    if not link:
        if admin_chat_id:
            bot.send_message(admin_chat_id,
                f"⚠️ <b>Нет ссылки в заказе FunPay</b>\n\n"
                f"📦 #{fp_order.id}\n📝 {description[:60]}\n👤 {buyer}",
                parse_mode="HTML", reply_markup=kb.funpay_order(fp_order.id, "paid"))
        return False

    # Создаём заказ
    svc_id = matched_lot["api_service_id"]
    api_r = _create_api_order_for_lot(matched_lot, link, quantity)

    usd_rub = cfg.get("usd_rub_rate", 92)
    cost = (matched_lot["api_rate"] / 1000) * quantity * usd_rub
    profit = price_fp - cost

    if api_r["success"]:
        oid = db.add_order(
            api_order_id=str(api_r["order_id"]),
            lot_id=matched_lot["id"], lot_name=matched_lot["name"],
            api_service_id=lot["api_service_id"], service_name=service_name,
            link=link, quantity=quantity,
            cost_price=cost, sell_price=price_fp, profit=profit,
            status="processing",
            funpay_order_id=fp_order.id, buyer_username=buyer
        )
        db.update_daily_stats(total_orders=1)
        db.add_log("INFO", "funpay_auto", f"Авто: FP #{fp_order.id} → API #{api_r['order_id']} | {buyer} | {profit:.0f}₽")

        _notify_admin_new_order(
            funpay_order_id=fp_order.id,
            api_order_id=api_r["order_id"],
            buyer=buyer,
            service_name=service_name,
            quantity=quantity,
            sell_price=price_fp,
            cost_price=cost,
            profit=profit,
            link=link,
            state_label="Авто-заказ создан",
        )

        # Сообщение покупателю в FunPay
        tpl = cfg.get_message("order_created")
        if tpl and buyer:
            msg_text = tpl.replace("{order_id}", fp_order.id) \
                          .replace("{service_name}", service_name) \
                          .replace("{link}", link) \
                          .replace("{quantity}", str(quantity)) \
                          .replace("{price}", f"{price_fp:.0f}")
            chat_id = getattr(fp_order, "chat_id", None)
            if chat_id:
                fp.send_message(chat_id, msg_text, chat_name=buyer)
            else:
                chat = fp.create_chat_with_user(buyer)
                if chat:
                    fp.send_message(chat.id, msg_text, chat_name=buyer)
        return True
    else:
        db.add_log("ERROR", "funpay_auto", f"FP #{fp_order.id}: {api_r.get('error')}")
        if admin_chat_id:
            bot.send_message(admin_chat_id,
                f"❌ <b>Ошибка авто-заказа</b>\n\n"
                f"🎮 FP: #{fp_order.id}\n👤 {buyer}\n⚠️ {api_r.get('error')}",
                parse_mode="HTML")
        return False
