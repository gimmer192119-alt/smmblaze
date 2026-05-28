"""
Конфигурация SMM Auto Bot
"""
import json
import os

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("SMM_CONFIG_PATH", os.path.join(PROJECT_DIR, "config.json"))
INSTANCE_DIR = os.path.dirname(os.path.abspath(CONFIG_PATH))
CONFIG_DIR = INSTANCE_DIR
DB_PATH = os.environ.get("SMM_DB_PATH", os.path.join(INSTANCE_DIR, "data", "smm_bot.db"))
BACKUP_DIR = os.path.join(INSTANCE_DIR, "backups")
EXPORT_DIR = os.path.join(INSTANCE_DIR, "exports")
LOG_PATH = os.environ.get("SMM_LOG_PATH", os.path.join(INSTANCE_DIR, "smm_bot.log"))

# Создаём директории
for d in [os.path.join(INSTANCE_DIR, "data"), BACKUP_DIR, EXPORT_DIR]:
    os.makedirs(d, exist_ok=True)

# Планы лицензий
LICENSE_PLANS = {
    "trial":     {"name": "🆓 Trial",     "duration_days": 7,   "max_lots": 3,      "max_orders_per_day": 50,     "price": 0,    "features": ["lots", "orders", "stats"]},
    "basic":     {"name": "⭐ Basic",      "duration_days": 30,  "max_lots": 10,     "max_orders_per_day": 200,    "price": 990,  "features": ["lots", "orders", "stats", "templates", "promo_codes"]},
    "premium":   {"name": "💎 Premium",    "duration_days": 90,  "max_lots": 50,     "max_orders_per_day": 1000,   "price": 2490, "features": ["lots", "orders", "stats", "templates", "promo_codes", "upsells", "export"]},
    "unlimited": {"name": "👑 Unlimited",  "duration_days": 365, "max_lots": 999999, "max_orders_per_day": 999999, "price": 4990, "features": ["lots", "orders", "stats", "templates", "promo_codes", "upsells", "export", "backup", "multi_api"]},
}
DEFAULT_CONFIG = {
    "app": {
        "role": "owner",
        "title": "SMM Auto Bot",
        "forced_markup_percent": 0,
        "hide_console": False
    },
    "bot_token": "",
    "admin_ids": [],
    "mirrors": {
        "enabled": True,
        "default_share_percent": 5.0,
    },
    "funpay_golden_key": "236dbxu53jbau2h1jtdu2xhq7vkd626c",
    "funpay_auto_process": True,
    "funpay_auto_delivery": True,
    "funpay_auto_raise": True,
    "funpay_raise_interval": 1800,
    "funpay_check_interval": 30,
    "funpay_category_presets": {},
    "funpay_proxy": "",
    "support_center": {
        "enabled": True,
        "php_sessid": "",
        "funpay_login": "",
        "form_id": 1,
        "login_field_id": 1,
        "order_field_id": 2,
        "role_field_id": 3,
        "role_value": 2,
        "subject_field_id": 5,
        "subject_value": 201,
        "timeout": 20,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0"
    },
    "funpay_withdraw": {
        "currency_id": "rub",
        "ext_currency_id": "fps",
        "wallet": "",
        "wallet_extra": "",
        "amount_int": 100,
        "twofactor_code": "",
        "auto_enabled": False,
        "auto_min_balance": 0
    },
    "telegram_proxy": "",
    "twiboost_api_key": "",
    "twiboost_api_url": "https://twiboost.com/api/v2",
    "twiboost_web": {
        "enabled": False,
        "orders_url": "https://twiboost.com/api/orders",
        "site_host": "twiboost.com",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
        "xsrf_token": "",
        "cookies": ""
    },
    "knowledge_base": {
        "enabled": True,
        "greeting_text": "Здравствуйте! Все товары в этом магазине на авто-выдаче. После оплаты бот сам подскажет, что отправить дальше.",
        "entries": [
            {
                "title": "Автовыдача",
                "triggers": ["привет", "здравствуйте", "добрый день", "hello", "hi"],
                "reply": "Здравствуйте! Все товары в этом магазине на авто-выдаче. После оплаты бот сам подскажет, что отправить дальше."
            },
            {
                "title": "База знаний",
                "triggers": ["база знаний", "faq", "помощь", "помоги"],
                "reply": "Популярные ответы:\n• Все товары на авто-выдаче.\n• Если заказ ещё не запущен, можно написать: Отмена.\n• Для статуса заказа напишите: инфо.\n• Для рефилла напишите: рефил.\n• Для скорости услуги напишите: скорость"
            },
            {
                "title": "Скорость",
                "triggers": ["скорость", "speed"],
                "reply": "Напишите команду в формате:\nскорость <ссылка TwiBoost>\nили просто: скорость\nТогда бот попробует показать скорость услуги текущего заказа."
            }
        ]
    },
    "auto_process_orders": True,
    "order_check_interval": 60,
    "balance_check_interval": 300,
    "low_balance_threshold": 5.0,
    "usd_rub_rate": 92,
    "daily_report_time": "09:00",
    "notifications": {
        "startup": False,
        "new_order": True,
        "buyer_message": True,
        "order_completed": True,
        "order_error": True,
        "low_balance": True,
        "daily_report": True,
        "support_ticket": True,
        "review_bonus": True,
    },
    "messages": {
        "order_created": "✅ Заказ #{order_id} создан!\n\n📦 Услуга: {service_name}\n🔗 Ссылка: {link}\n📊 Количество: {quantity}\n💰 Цена: {price}₽\n\n⏳ Выполнение уже началось!",
        "order_completed": "🎉 Заказ #{order_id} выполнен!\n\n📦 {service_name}\n✅ Всё готово!",
        "order_error": "❌ Ошибка заказа #{order_id}\n\n📦 {service_name}\n⚠️ {error}\n\nМы уже разбираемся!",
        "review_bonus": "🎁 Спасибо за отзыв! Держи бонус:\n\n🎫 Промокод: {promo_code}\n💰 {bonus_text}\n📅 Действует до: {expires_at}",
    },
    "license_key": "",
    "license_server_url": "",
}


class Config:
    """Управление конфигурацией"""

    def __init__(self, path=None):
    # 🔥 1. Сначала задаём путь к файлу
        self._config_path = path or os.path.join(CONFIG_DIR, "config.json")
    
    # 🔥 2. Инициализируем хранилище
        self._data = {}
    
    # 🔥 3. Только теперь загружаем/сохраняем конфиг
        self.load()

    def load(self):
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        self._merge_defaults(self._data, DEFAULT_CONFIG)
        self.save()

    def _merge_defaults(self, target, defaults):
        for key, value in defaults.items():
            if key not in target:
                target[key] = value
            elif isinstance(target.get(key), dict) and isinstance(value, dict):
                self._merge_defaults(target[key], value)

    def save(self, path=None):
        path = path or self._config_path
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def get(self, key, default=None):
        keys = key.split(".")
        val = self._data
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k)
            else:
                return default
            if val is None:
                return default
        return val

    def set(self, key, value):
        keys = key.split(".")
        d = self._data
        for k in keys[:-1]:
            if k not in d or not isinstance(d[k], dict):
                d[k] = {}
            d = d[k]
        d[keys[-1]] = value
        self.save()

    @property
    def bot_token(self):
        return self._data.get("bot_token", "")

    @property
    def admin_ids(self):
        return self._data.get("admin_ids", [])

    @property
    def funpay_golden_key(self):
        return self._data.get("funpay_golden_key", "")

    @property
    def funpay_auto_process(self):
        return self._data.get("funpay_auto_process", True)

    @property
    def funpay_category_presets(self):
        return self._data.get("funpay_category_presets", {})

    def add_funpay_category_preset(self, service_id, preset):
        presets = self._data.setdefault("funpay_category_presets", {})
        service_key = str(service_id)
        service_list = presets.setdefault(service_key, [])
        if not any(
            p.get("category_name") == preset.get("category_name") and
            p.get("subcategory_name") == preset.get("subcategory_name") and
            p.get("subcategory_id", 0) == preset.get("subcategory_id", 0)
            for p in service_list
        ):
            service_list.append({
                "category_name": preset.get("category_name", ""),
                "subcategory_name": preset.get("subcategory_name", ""),
                "subcategory_id": preset.get("subcategory_id", 0)
            })
            self.save()

    @property
    def funpay_proxy(self):
        return self._data.get("funpay_proxy", "")

    @property
    def telegram_proxy(self):
        return self._data.get("telegram_proxy", "")

    @property
    def twiboost_api_key(self):
        return self._data.get("twiboost_api_key", "")

    @property
    def twiboost_api_url(self):
        return self._data.get("twiboost_api_url", "https://twiboost.com/api/v2")

    def get_message(self, name):
        return self._data.get("messages", {}).get(name, "")
