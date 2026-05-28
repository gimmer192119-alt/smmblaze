"""
BlazeMarket - SMM Services Bot & Web App
Main configuration file
"""
import json
import os

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(PROJECT_DIR, "config.json")
DB_PATH = os.path.join(PROJECT_DIR, "data", "blazemarket.db")
LOG_PATH = os.path.join(PROJECT_DIR, "logs", "blazemarket.log")

# Создаём директории
for d in [os.path.join(PROJECT_DIR, "data"), os.path.join(PROJECT_DIR, "logs")]:
    os.makedirs(d, exist_ok=True)

DEFAULT_CONFIG = {
    "bot": {
        "token": "",
        "admin_ids": [],
        "webhook_url": ""
    },
    "payment": {
        "pally_merchant_id": "",
        "pally_secret_key": "",
        "currency": "RUB"
    },
    "api": {
        "twiboost_api_key": "",
        "twiboost_api_url": "https://twiboost.com/api/v2",
        "smmway_api_key": "",
        "smmway_api_url": "https://smmway.ru/api/v2"
    },
    "mirrors": {
        "enabled": True,
        "default_share_percent": 20.0,
        "base_markup_percent": 0
    },
    "design": {
        "theme": "lime_graphite",
        "primary_color": "#32CD32",
        "secondary_color": "#2F4F4F",
        "accent_color": "#7FFF00"
    },
    "services": {
        "categories": {
            "telegram": "Telegram",
            "instagram": "Instagram", 
            "tiktok": "TikTok",
            "youtube": "YouTube",
            "twitter": "Twitter/X",
            "facebook": "Facebook",
            "vk": "VKontakte",
            "other": "Other"
        }
    },
    "messages": {
        "welcome_buyer": "👋 Добро пожаловать в BlazeMarket!\n\nВы выбрали режим покупателя. Введите код заказа или выберите услугу.",
        "welcome_seller": "💼 Добро пожаловать в BlazeMarket!\n\nВы выбрали режим продавца. Хотите купить бота за 1000₽ или создать бесплатный магазин?",
        "enter_code": "🔑 Введите код заказа:",
        "enter_comments": "📝 Отправьте комментарии (текстом или файлом). Без символа #",
        "order_created": "✅ Заказ создан! Ожидайте выполнения."
    }
}


class Config:
    """Управление конфигурацией"""
    
    def __init__(self, path=None):
        self._config_path = path or CONFIG_PATH
        self._data = {}
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
        return self._data.get("bot", {}).get("token", "")
    
    @property
    def admin_ids(self):
        return self._data.get("bot", {}).get("admin_ids", [])
    
    @property
    def pally_merchant_id(self):
        return self._data.get("payment", {}).get("pally_merchant_id", "")
    
    @property
    def pally_secret_key(self):
        return self._data.get("payment", {}).get("pally_secret_key", "")


cfg = Config()
