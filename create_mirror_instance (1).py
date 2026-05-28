import json
import os
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BASE_CONFIG_PATH = ROOT / "config.json"
MIRRORS_DIR = ROOT / "mirror_instances"


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9а-яА-Я_-]+", "-", value.strip())
    value = value.strip("-_")
    return value or "mirror"


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or default


def load_base_config():
    if BASE_CONFIG_PATH.exists():
        with BASE_CONFIG_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def build_mirror_config(base_config: dict, mirror_name: str, admin_id: int, bot_token: str, forced_markup_percent: float):
    config = json.loads(json.dumps(base_config))
    config.setdefault("app", {})
    config["app"]["role"] = "mirror"
    config["app"]["title"] = f"SMM Mirror - {mirror_name}"
    config["app"]["forced_markup_percent"] = forced_markup_percent
    config["mirrors"] = {"enabled": False, "default_share_percent": 0}
    config["bot_token"] = bot_token
    config["admin_ids"] = [admin_id] if admin_id else []
    config["funpay_golden_key"] = ""
    config["twiboost_api_key"] = ""
    config["support_center"] = config.get("support_center", {})
    config["support_center"]["enabled"] = False
    config.setdefault("notifications", {})
    config["notifications"]["startup"] = False
    return config


def write_launchers(instance_dir: Path):
    python_exe = sys.executable
    main_path = ROOT / "main.py"
    config_path = instance_dir / "config.json"
    db_path = instance_dir / "data" / "smm_bot.db"
    log_path = instance_dir / "smm_bot.log"

    bat = f'''@echo off
set "SMM_CONFIG_PATH={config_path}"
set "SMM_DB_PATH={db_path}"
set "SMM_LOG_PATH={log_path}"
"{python_exe}" "{main_path}"
'''
    (instance_dir / "start_mirror.bat").write_text(bat, encoding="utf-8")

    vbs = f'''Set WshShell = CreateObject("WScript.Shell")
WshShell.Run chr(34) & "{instance_dir / 'start_mirror.bat'}" & chr(34), 0
Set WshShell = Nothing
'''
    (instance_dir / "start_hidden.vbs").write_text(vbs, encoding="utf-8")


def write_readme(instance_dir: Path, mirror_name: str, forced_markup_percent: float):
    text = f'''{mirror_name}

Это отдельный инстанс зеркала.

Как запускать:
1. Откройте start_hidden.vbs - бот запустится без консоли.
2. Если нужен обычный запуск, используйте start_mirror.bat.

Что настроить после первого запуска:
1. В Telegram зайдите в бота.
2. Откройте Настройки.
3. Укажите Golden Key FunPay.
4. Укажите API ключ TwiBoost.

Наценка владельца зафиксирована: {forced_markup_percent:.2f}%.
'''
    (instance_dir / "README.txt").write_text(text, encoding="utf-8")


def main():
    base = load_base_config()
    mirror_name = ask("Название зеркала", "mirror")
    admin_id_raw = ask("Telegram ID друга")
    bot_token = ask("Токен Telegram-бота зеркала")
    forced_markup_raw = ask("Фиксированная наценка %", "0")

    try:
        admin_id = int(admin_id_raw)
    except ValueError:
        print("Telegram ID должен быть числом.")
        return

    try:
        forced_markup_percent = float(forced_markup_raw.replace(",", "."))
    except ValueError:
        print("Наценка должна быть числом.")
        return

    slug = slugify(mirror_name)
    instance_dir = MIRRORS_DIR / slug
    (instance_dir / "data").mkdir(parents=True, exist_ok=True)

    config = build_mirror_config(base, mirror_name, admin_id, bot_token, forced_markup_percent)
    (instance_dir / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    write_launchers(instance_dir)
    write_readme(instance_dir, mirror_name, forced_markup_percent)

    print(f"Готово: {instance_dir}")
    print("Запуск без консоли: start_hidden.vbs")


if __name__ == "__main__":
    main()
