"""
🤖 SMM Auto Bot — Standalone Telegram Bot
Автоматизация SMM бизнеса через TwiBoost API

Запуск: python main.py
"""
import logging
import time
import sys
import os
import atexit
import html
import json
import re
import ssl
import urllib3
import requests
from requests.adapters import HTTPAdapter
from threading import Thread, Event
from datetime import datetime
from types import MethodType, SimpleNamespace
from urllib3.util.retry import Retry

import telebot
from telebot import apihelper

from config import Config, CONFIG_DIR, LOG_PATH
from database import Database
from twiboost import TwiBoostAPI
from funpay import FunPayClient
from support_center import FunPaySupportClient
import handlers
import handler_text_overrides

handler_text_overrides.apply(handlers)

# Отключаем проверку SSL для избежания блокировок
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ssl._create_default_https_context = ssl._create_unverified_context

# Добавляем альтернативные DNS для обхода блокировок
import socket

# Настройка DNS серверов
original_getaddrinfo = socket.getaddrinfo

def custom_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    # Альтернативные DNS для Telegram
    if host == 'api.telegram.org':
        # Используем IP адреса напрямую
        alternative_ips = ['149.154.167.220', '149.154.167.221', '149.154.167.222']
        for ip in alternative_ips:
            try:
                return original_getaddrinfo(ip, port, family, type, proto, flags)
            except:
                continue
    # Альтернативные DNS для FunPay
    elif host == 'funpay.com':
        alternative_ips = ['104.21.49.234', '172.67.214.224']
        for ip in alternative_ips:
            try:
                return original_getaddrinfo(ip, port, family, type, proto, flags)
            except:
                continue
    return original_getaddrinfo(host, port, family, type, proto, flags)

socket.getaddrinfo = custom_getaddrinfo


def patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if host == "api.telegram.org":
        try:
            return original_getaddrinfo(host, port, family, type, proto, flags)
        except socket.gaierror:
            for ip in ["149.154.167.220", "149.154.167.221", "149.154.167.222"]:
                try:
                    return original_getaddrinfo(ip, port, family, type, proto, flags)
                except OSError:
                    continue
    if host == "funpay.com":
        try:
            return original_getaddrinfo(host, port, family, type, proto, flags)
        except socket.gaierror:
            for ip in ["104.21.49.234", "172.67.214.224"]:
                try:
                    return original_getaddrinfo(ip, port, family, type, proto, flags)
                except OSError:
                    continue
    return original_getaddrinfo(host, port, family, type, proto, flags)


socket.getaddrinfo = patched_getaddrinfo

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ]
)
logger = logging.getLogger("SMM")

stop_event = Event()
_lock_handle = None
_TELEGRAM_PROXY_CONFIG = None


def _parse_telegram_proxy(proxy_value):
    if not proxy_value:
        return None
    if isinstance(proxy_value, dict):
        prepared = {k: v for k, v in proxy_value.items() if k in ("http", "https") and v}
        return prepared or None
    raw = str(proxy_value).strip()
    if not raw:
        return None
    if raw.startswith("{"):
        try:
            return _parse_telegram_proxy(json.loads(raw))
        except Exception:
            return None
    if "://" not in raw:
        raw = f"http://{raw}"
    return {"http": raw, "https": raw}


def _configure_telegram_network(proxy_config=None):
    apihelper.proxy = proxy_config or None
    apihelper.CONNECT_TIMEOUT = 15
    apihelper.READ_TIMEOUT = 45
    apihelper.RETRY_ON_ERROR = False
    apihelper.RETRY_ENGINE = 1
    apihelper.MAX_RETRIES = 1
    apihelper.RETRY_TIMEOUT = 1
    apihelper.SESSION_TIME_TO_LIVE = 300
    session = requests.Session()
    session.trust_env = proxy_config is None
    if proxy_config:
        session.proxies = proxy_config
    retry = Retry(
        total=0,
        connect=0,
        read=0,
        redirect=0,
        status=0,
        backoff_factor=0,
        allowed_methods=False,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    apihelper.session = session


def _compact_telegram_text(text: str, limit: int = 700) -> str:
    raw = re.sub(r"<[^>]+>", "", str(text or ""))
    raw = re.sub(r"\s+", " ", raw).strip()
    if len(raw) <= limit:
        return raw
    return raw[: limit - 1].rstrip() + "…"


def _patch_telegram_bot(bot: telebot.TeleBot):
    original_send_message = bot.send_message
    original_reply_to = bot.reply_to
    original_get_me = bot.get_me
    original_answer_callback_query = bot.answer_callback_query
    fallback_user = SimpleNamespace(
        id=0,
        is_bot=True,
        first_name="SMM Auto Bot",
        username="smm_auto_bot",
    )

    def safe_send_message(self, *args, **kwargs):
        try:
            return original_send_message(*args, **kwargs)
        except Exception as e:
            logger.warning(f"Telegram send_message failed: {e}")
            error_text = str(e).lower()
            if any(token in error_text for token in ("timeout", "connection", "ssl", "proxy", "reset")):
                try:
                    _configure_telegram_network(_TELEGRAM_PROXY_CONFIG)
                    return original_send_message(*args, **kwargs)
                except Exception as retry_e:
                    logger.warning(f"Telegram send_message retry failed: {retry_e}")
                    try:
                        compact_kwargs = dict(kwargs)
                        compact_kwargs.pop("parse_mode", None)
                        compact_args = list(args)
                        if len(compact_args) >= 2:
                            compact_args[1] = _compact_telegram_text(compact_args[1])
                        elif "text" in compact_kwargs:
                            compact_kwargs["text"] = _compact_telegram_text(compact_kwargs["text"])
                        else:
                            return None
                        return original_send_message(*compact_args, **compact_kwargs)
                    except Exception as fallback_e:
                        logger.warning(f"Telegram send_message fallback failed: {fallback_e}")
            return None

    def safe_reply_to(self, *args, **kwargs):
        try:
            return original_reply_to(*args, **kwargs)
        except Exception as e:
            logger.warning(f"Telegram reply_to failed: {e}")
            return None

    def safe_get_me(self, *args, **kwargs):
        try:
            me = original_get_me(*args, **kwargs)
            if me is not None:
                self._user = me
                return me
        except Exception as e:
            logger.warning(f"Telegram get_me failed: {e}")
        self._user = getattr(self, "_user", None) or fallback_user
        return self._user

    def safe_answer_callback_query(self, *args, **kwargs):
        try:
            return original_answer_callback_query(*args, **kwargs)
        except Exception as e:
            error_text = str(e).lower()
            if "query is too old" in error_text or "query id is invalid" in error_text:
                logger.warning(f"Telegram answer_callback_query skipped: {e}")
                return None
            logger.warning(f"Telegram answer_callback_query failed: {e}")
            return None

    bot._user = fallback_user
    bot.send_message = MethodType(safe_send_message, bot)
    bot.reply_to = MethodType(safe_reply_to, bot)
    bot.get_me = MethodType(safe_get_me, bot)
    bot.answer_callback_query = MethodType(safe_answer_callback_query, bot)
    return bot


def acquire_single_instance_lock():
    """Гарантирует, что бот запущен в единственном экземпляре."""
    global _lock_handle
    lock_path = os.path.join(CONFIG_DIR, "bot.lock")
    os.makedirs(CONFIG_DIR, exist_ok=True)
    _lock_handle = open(lock_path, "w")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(_lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(_lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_handle.write(str(os.getpid()))
        _lock_handle.flush()
        return True
    except OSError:
        _lock_handle.close()
        _lock_handle = None
        return False


def release_single_instance_lock():
    global _lock_handle
    if not _lock_handle:
        return
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(_lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(_lock_handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        _lock_handle.close()
        _lock_handle = None


atexit.register(release_single_instance_lock)


def _format_ru_datetime(iso_value: str) -> str:
    if not iso_value:
        return "не указано"
    try:
        return datetime.fromisoformat(str(iso_value)).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return str(iso_value)


def _build_support_reminder_message(session: dict, order: dict | None) -> str:
    return handlers.build_support_ticket_message(session, order)


def order_checker_loop(db: Database, api: TwiBoostAPI, cfg: Config, bot: telebot.TeleBot):
    """Фоновая проверка статусов активных заказов каждые 30 секунд"""
    interval = 30  # Fixed 30 seconds interval
    while not stop_event.is_set():
        logger.info(f"Checking orders... Active: {len(db.get_active_orders())}")
        stop_event.wait(interval)
        if stop_event.is_set():
            break
        try:
            active = db.get_active_orders()
            if not active:
                logger.debug("No active orders to check")
                continue
            logger.info(f"Found {len(active)} active orders to check")
            api_ids = [o["api_order_id"] for o in active if o["api_order_id"]]
            if not api_ids:
                logger.debug("No API IDs in active orders")
                continue

            r = api.check_orders_status(api_ids)
            if not r["success"]:
                logger.error(f"Failed to check orders: {r.get('error')}")
                continue

            status_map = {
                "awaiting": "pending", "in progress": "in_progress",
                "completed": "completed", "partial": "partial",
                "canceled": "cancelled", "fail": "failed",
            }

            updated = 0
            for order in active:
                aid = order["api_order_id"]
                if aid not in r["orders"]:
                    continue
                d = r["orders"][aid]
                if d.get("status") == "Error":
                    continue
                new_status = status_map.get(d["status"].lower(), order["status"])
                if new_status != order["status"]:
                    from datetime import datetime
                    upd = {
                        "status": new_status,
                        "api_status": d["status"],
                        "api_charge": d["charge"],
                        "api_start_count": d["start_count"],
                        "api_remains": d["remains"],
                        "currency": d.get("currency", order.get("currency", "RUB")),
                    }
                    upd = handlers._update_order_finances_from_api(order, upd)
                    if new_status == "completed":
                        upd["completed_at"] = datetime.now().isoformat()
                        db.update_daily_stats(
                            completed_orders=1,
                            total_revenue=order["sell_price"],
                            total_cost=upd.get("cost_price", order["cost_price"]),
                            total_profit=upd.get("profit", order["profit"])
                        )

                    elif new_status == "failed":
                        db.update_daily_stats(failed_orders=1)
                        if cfg.get("notifications.order_error", True):
                            text = f"❌ <b>Заказ #{order['id']} провалился</b>\n\n📦 {order['service_name'][:40]}"
                            for uid in cfg.admin_ids:
                                try:
                                    bot.send_message(uid, text, parse_mode="HTML")
                                except:
                                    pass

                    db.update_order(order["id"], **upd)
                    if new_status == "completed":
                        handlers.notify_funpay_order_completed(order["id"], completed_at=upd["completed_at"])
                    db.add_log("INFO", "checker", f"#{order['id']}: {order['status']} → {new_status}")
                    updated += 1

            if updated > 0:
                logger.info(f"Updated {updated} orders")

        except Exception as e:
            logger.error(f"Order checker error: {e}")
            db.add_log("ERROR", "checker", str(e))


def balance_checker_loop(db: Database, api: TwiBoostAPI, cfg: Config, bot: telebot.TeleBot):
    """Фоновая проверка баланса API"""
    interval = cfg.get("balance_check_interval", 300)
    while not stop_event.is_set():
        stop_event.wait(interval)
        if stop_event.is_set():
            break
        try:
            r = api.get_balance()
            if r["success"]:
                threshold = cfg.get("low_balance_threshold", 5)
                balance = r["balance"]
                currency = str(r.get("currency", "USD")).upper()
                usd_rub = cfg.get("usd_rub_rate", 92)
                symbol_map = {"USD": "$", "EUR": "€", "RUB": "₽", "RUR": "₽"}
                symbol = symbol_map.get(currency)
                if symbol == "$" or symbol == "€":
                    balance_str = f"{symbol}{balance:.2f}"
                    threshold_str = f"{symbol}{threshold:.2f}"
                    rub_hint = f"\n💴 ≈ {balance * usd_rub:.0f}₽ (курс {usd_rub})" if currency == "USD" else ""
                elif symbol == "₽":
                    balance_str = f"{balance:.2f}{symbol}"
                    threshold_str = f"{threshold:.2f}{symbol}"
                    rub_hint = ""
                else:
                    balance_str = f"{balance:.2f} {currency}"
                    threshold_str = f"{threshold:.2f} {currency}"
                    rub_hint = ""

                if balance < threshold and cfg.get("notifications.low_balance", True):
                    text = (
                        f"⚠️ <b>Низкий баланс!</b>\n\n"
                        f"💰 {balance_str}\n📉 Порог: {threshold_str}{rub_hint}"
                    )
                    for uid in cfg.admin_ids:
                        try:
                            bot.send_message(uid, text, parse_mode="HTML")
                        except:
                            pass
        except Exception as e:
            logger.error(f"Balance checker error: {e}")


def funpay_support_loop(
    fp_client: FunPayClient,
    db: Database,
    cfg: Config,
    bot: telebot.TeleBot,
    support_client: FunPaySupportClient | None = None,
):
    """Фоновая проверка просроченных сессий FunPay (тикеты поддержки)."""
    check_interval = max(30, cfg.get("funpay_check_interval", 60))
    auto_raise_enabled = bool(cfg.get("funpay_auto_raise", True))
    raise_interval = max(300, int(cfg.get("funpay_raise_interval", 1800) or 1800))
    next_raise_at = time.time() + min(15, raise_interval)
    next_auto_withdraw_at = time.time() + 15
    next_funpay_status_check = time.time() + 300  # 🔥 Добавь эту строку
    while not stop_event.is_set():
        stop_event.wait(check_interval)
        if stop_event.is_set():
            break
        try:
            if auto_raise_enabled and time.time() >= next_raise_at:
                raise_result = fp_client.raise_profile_lots()
                if raise_result.get("success"):
                    logger.info(
                        "FunPay auto-raise: categories=%s, lots=%s",
                        raise_result.get("categories", 0),
                        raise_result.get("lots", 0),
                    )
                    next_raise_at = time.time() + raise_interval
                else:
                    wait_time = int(raise_result.get("wait_time") or raise_interval)
                    logger.warning("FunPay auto-raise skipped: %s", raise_result.get("error", "unknown error"))
                    next_raise_at = time.time() + max(300, wait_time)
        except Exception as e:
            logger.error(f"FunPay auto-raise loop error: {e}")
        try:
            withdraw_cfg = cfg.get("funpay_withdraw", {})
            auto_enabled = bool(withdraw_cfg.get("auto_enabled", False))
            auto_min_balance = int(withdraw_cfg.get("auto_min_balance") or 0)
            amount_int = int(withdraw_cfg.get("amount_int") or 0)
            wallet = str(withdraw_cfg.get("wallet") or "").strip()
            wallet_extra = str(withdraw_cfg.get("wallet_extra") or "").strip()
            if auto_enabled and auto_min_balance > 0 and amount_int > 0 and wallet and wallet_extra and time.time() >= next_auto_withdraw_at:
                fp_status = fp_client.test_connection()
                if fp_status.get("success"):
                    balance = float(fp_status.get("balance") or 0)
                    if balance >= auto_min_balance:
                        result = fp_client.create_withdraw(
                            currency_id=str(withdraw_cfg.get("currency_id") or "rub"),
                            ext_currency_id=str(withdraw_cfg.get("ext_currency_id") or "fps"),
                            wallet=wallet,
                            wallet_extra=wallet_extra,
                            amount_int=amount_int,
                            twofactor_code=str(withdraw_cfg.get("twofactor_code") or "").strip(),
                            preview=False,
                        )
                        payload = result.get("data") or {}
                        if result.get("success") and isinstance(payload, dict) and payload.get("error") is False:
                            bank_name = str(payload.get("fps_bank_name") or "").strip()
                            amount_ext = str(payload.get("amount_ext") or "").strip()
                            text_lines = [
                                "✅ <b>Авто-вывод успешно отправлен</b>",
                                " ",
                                f"💰 Баланс FunPay: <b>{balance:.2f}₽</b>",
                                f"💸 Списано: <b>{amount_int}₽</b>",
                                " ",
                                f"📊 <b>Статистика за {stats['month']}</b>",
                                f"📦 Заказов: {stats['order_count']}",
                                f"💰 Общая выручка: {stats['total_revenue']}₽",
                                f"💵 Затраты TwiBoost: {stats['total_cost']}₽",
                                f"🔗 Привязанные лоты: {stats['bound_revenue']}₽",
                                f"🌐 Прочие/Без привязки: {stats['unbound_revenue']}₽",
                                f"📈 Чистая прибыль: {stats['net_profit']}₽",
                                f"💸 Доля владельца ({stats['share_percent']}%): <b>{stats['owner_share']}₽</b>"
                            ]
                            if amount_ext:
                                text_lines.append(f"📥 К получению: <b>{html.escape(amount_ext)}</b> ₽")
                            if bank_name:
                                text_lines.append(f"🏦 Банк: <b>{html.escape(bank_name)}</b>")
                            for admin_id in cfg.admin_ids:
                                try:
                                    bot.send_message(admin_id, "\n".join(text_lines), parse_mode="HTML")
                                except Exception:
                                    pass
                            logger.info("FunPay auto withdraw sent: amount=%s balance=%s", amount_int, balance)
                            next_auto_withdraw_at = time.time() + 1800
                        else:
                            error_text = str(payload.get("msg") or result.get("error") or "unknown error")
                            logger.warning("FunPay auto withdraw failed: %s", error_text)
                            for admin_id in cfg.admin_ids:
                                try:
                                    bot.send_message(
                                        admin_id,
                                        "⚠️ <b>Автовывод не отправлен</b>\n\n" + html.escape(error_text),
                                        parse_mode="HTML",
                                    )
                                except Exception:
                                    pass
                            next_auto_withdraw_at = time.time() + 1800
                    else:
                        next_auto_withdraw_at = time.time() + 300
                else:
                    next_auto_withdraw_at = time.time() + 300
        except Exception as e:
            logger.error(f"FunPay auto withdraw loop error: {e}")
                # 🔥 Проверка статуса заказов в FunPay каждые 5 минут
        try:
            if time.time() >= next_funpay_status_check:
                active_orders = db.get_active_orders(limit=20)
                for order in active_orders:
                    funpay_id = order.get("funpay_order_id")
                    if not funpay_id or order.get("status") == "completed":
                        continue
                    try:
                        details = fp.get_order_details(funpay_id)
                        if details.get("success"):
                            fp_status = details.get("status")
                            if fp_status in ("closed", "completed") and order.get("status") != "completed":
                                db.update_order(order["id"], status="completed", completed_at=datetime.now().isoformat())
                                notify_funpay_order_completed(order["id"])
                                logger.info(f"✅ Order #{order['id']} marked completed via FunPay (FP: {funpay_id})")
                    except Exception as e:
                        logger.warning(f"Failed to check FunPay status for order #{order.get('id')} (FP: {funpay_id}): {e}")
                next_funpay_status_check = time.time() + 300
        except Exception as e:
            logger.error(f"FunPay status check loop error: {e}")
        try:
            due_sessions = db.get_due_funpay_sessions(datetime.now().isoformat())
            if due_sessions:
                logger.info("FunPay support reminders due: %s", len(due_sessions))
                if len(due_sessions) > 1:
                    items = []
                    for session in due_sessions:
                        order = db.get_order(session["order_id"]) if session.get("order_id") else None
                        items.append((session, order))

                    ticket_result = None
                    if support_client and support_client.is_enabled():
                        try:
                            ticket_result = support_client.create_unconfirmed_confirmation_ticket_batch(items)
                        except Exception as e:
                            ticket_result = {"success": False, "error": str(e)}

                    reminder_lines = [
                        f"⏰ <b>Просроченные подтверждения FunPay: {len(due_sessions)}</b>",
                        "",
                    ]
                    for idx, (session, order) in enumerate(items, start=1):
                        service_name = session.get("lot_name") or "Услуга"
                        completed_at = session.get("updated_at")
                        local_order_id = session.get("order_id") or "—"
                        if order:
                            service_name = order.get("service_name") or order.get("lot_name") or service_name
                            completed_at = order.get("completed_at") or completed_at
                            local_order_id = order.get("id") or local_order_id
                        service_name = str(service_name or "Услуга").strip()
                        if len(service_name) > 60:
                            service_name = service_name[:59].rstrip() + "…"
                        reminder_lines.append(
                            f"{idx}. 🎮 #{html.escape(session.get('funpay_order_id') or '—')} | "
                            f"🧾 #{html.escape(str(local_order_id))} | "
                            f"👤 {html.escape(session.get('buyer_username') or 'неизвестно')}"
                        )
                        reminder_lines.append(
                            f"   📦 {html.escape(service_name)} | 🕒 {html.escape(_format_ru_datetime(completed_at))}"
                        )

                    reminder_message = "\n".join(reminder_lines)
                    if ticket_result:
                        if ticket_result.get("success"):
                            ticket_suffix = f"#{ticket_result.get('ticket_id')}" if ticket_result.get("ticket_id") else "создан"
                            reminder_message += (
                                "\n\n✅ <b>Общий автотикет создан</b>\n"
                                f"🎫 Номер: <b>{html.escape(ticket_suffix)}</b>"
                            )
                            if ticket_result.get("ticket_url"):
                                reminder_message += f"\n🔗 {html.escape(ticket_result['ticket_url'])}"
                        else:
                            reminder_message += (
                                "\n\n⚠️ <b>Общий автотикет не отправлен</b>\n"
                                f"Причина: {html.escape(str(ticket_result.get('error') or 'неизвестная ошибка'))}"
                            )

                    if cfg.get("notifications.support_ticket", True):
                        for admin_id in cfg.admin_ids:
                            try:
                                bot.send_message(admin_id, reminder_message, parse_mode="HTML")
                            except Exception:
                                pass

                    if ticket_result and ticket_result.get("success"):
                        logger.info(
                            "FunPay support batch ticket created for %s orders (%s)",
                            len(due_sessions),
                            ticket_result.get("ticket_id") or "no-id",
                        )
                        for session in due_sessions:
                            db.update_funpay_session(
                                session["funpay_order_id"],
                                support_ticket_sent=1,
                                support_ticket_due_at="",
                            )
                    elif support_client and support_client.is_enabled():
                        error_text = str((ticket_result or {}).get("error", "") or "")
                        retry_delay = 3600
                        if "1 СЂР°Р· РІ СЃСѓС‚РєРё" in error_text or "1 раз в сутки" in error_text:
                            retry_delay = 24 * 3600
                        retry_at = datetime.now().timestamp() + retry_delay
                        for session in due_sessions:
                            db.update_funpay_session(
                                session["funpay_order_id"],
                                support_ticket_due_at=datetime.fromtimestamp(retry_at).isoformat(),
                            )
                        logger.warning(
                            "FunPay support batch ticket failed for %s orders: %s",
                            len(due_sessions),
                            (ticket_result or {}).get("error", "unknown error"),
                        )
                    else:
                        logger.info("FunPay support reminder processed for %s orders", len(due_sessions))
                        for session in due_sessions:
                            db.update_funpay_session(
                                session["funpay_order_id"],
                                support_ticket_sent=1,
                                support_ticket_due_at="",
                            )
                    due_sessions = []
            for session in due_sessions:
                order = db.get_order(session["order_id"]) if session.get("order_id") else None
                ticket_result = None
                if support_client and support_client.is_enabled():
                    try:
                        ticket_result = support_client.create_unconfirmed_confirmation_ticket(session, order)
                    except Exception as e:
                        ticket_result = {"success": False, "error": str(e)}

                reminder_message = _build_support_reminder_message(session, order)
                if ticket_result:
                    if ticket_result.get("success"):
                        ticket_suffix = f"#{ticket_result.get('ticket_id')}" if ticket_result.get("ticket_id") else "создан"
                        reminder_message += (
                            "\n\n✅ <b>Автотикет создан</b>\n"
                            f"🎫 Номер: <b>{html.escape(ticket_suffix)}</b>"
                        )
                        if ticket_result.get("ticket_url"):
                            reminder_message += f"\n🔗 {html.escape(ticket_result['ticket_url'])}"
                    else:
                        reminder_message += (
                            "\n\n⚠️ <b>Автотикет не отправлен</b>\n"
                            f"Причина: {html.escape(str(ticket_result.get('error') or 'неизвестная ошибка'))}"
                        )

                if cfg.get("notifications.support_ticket", True):
                    for admin_id in cfg.admin_ids:
                        try:
                            bot.send_message(admin_id, reminder_message, parse_mode="HTML")
                        except Exception:
                            pass
                if ticket_result and ticket_result.get("success"):
                    logger.info(
                        "FunPay support ticket created for order #%s (%s)",
                        session["funpay_order_id"],
                        ticket_result.get("ticket_id") or "no-id",
                    )
                    db.update_funpay_session(
                        session["funpay_order_id"],
                        support_ticket_sent=1,
                        support_ticket_due_at="",
                    )
                elif support_client and support_client.is_enabled():
                    error_text = str((ticket_result or {}).get("error", "") or "")
                    retry_delay = 3600
                    if "1 раз в сутки" in error_text:
                        retry_delay = 24 * 3600
                    retry_at = datetime.now().timestamp() + retry_delay
                    db.update_funpay_session(
                        session["funpay_order_id"],
                        support_ticket_due_at=datetime.fromtimestamp(retry_at).isoformat(),
                    )
                    logger.warning(
                        "FunPay support ticket failed for order #%s: %s",
                        session["funpay_order_id"],
                        (ticket_result or {}).get("error", "unknown error"),
                    )
                else:
                    logger.info("FunPay support reminder processed for order #%s", session["funpay_order_id"])
                    db.update_funpay_session(
                        session["funpay_order_id"],
                        support_ticket_sent=1,
                        support_ticket_due_at="",
                    )
            handlers.process_review_bonuses()
        except Exception as e:
            logger.error(f"FunPay support loop error: {e}")
            db.add_log("ERROR", "funpay_support", str(e))
# ... (конец функции funpay_support_loop) ...

def xsrf_refresh_loop(api_obj, cfg_obj, interval=3600):
    """Фоновое обновление куки и XSRF-TOKEN для TwiBoost web-fallback"""
    while not stop_event.is_set():
        stop_event.wait(interval)
        if stop_event.is_set():
            break
        try:
            if api_obj and api_obj._web_enabled():
                # Логика обновления берётся из вашего _create_order_via_web
                # Просто делаем лёгкий GET к логину, чтобы сервер обновил сессию
                import requests
                headers = {
                    "User-Agent": str(api_obj.web_config.get("user_agent") or "Mozilla/5.0"),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                }
                cookies = {}
                for part in str(api_obj.web_config.get("cookies", "")).split(";"):
                    if "=" in part:
                        k, v = part.strip().split("=", 1)
                        cookies[k] = v.strip()
                
                resp = requests.get("https://twiboost.com/login", headers=headers, cookies=cookies, timeout=10)
                new_xsrf = resp.cookies.get("XSRF-TOKEN")
                if new_xsrf:
                    api_obj.web_config["xsrf_token"] = new_xsrf
                    api_obj.web_config["enabled"] = True
                    logger.debug("✅ XSRF-TOKEN автоматически обновлён фоновой задачей")
        except Exception as e:
            logger.warning(f"⚠️ Фоновое обновление XSRF не удалось: {e}")
            return
def main():
    global _TELEGRAM_PROXY_CONFIG
    logger.info("=" * 50)
    logger.info("🤖 SMM Auto Bot — Запуск")
    logger.info("=" * 50)

    if not acquire_single_instance_lock():
        logger.error("⚠️ Найден другой запущенный экземпляр бота. Остановите его перед запуском.")
        sys.exit(1)

    # 1. Конфигурация
    cfg = Config()
    support_client = FunPaySupportClient(cfg)
    logger.info("✅ Конфигурация загружена")

    # Проверка токена
    if not cfg.bot_token:
        print("\n" + "=" * 50)
        print("⚠️  Токен бота не настроен!")
        print("=" * 50)
        token = input("Введите токен Telegram бота: ").strip()
        cfg.set("bot_token", token)
        print()

    if not cfg.admin_ids:
        print("⚠️  Не указаны admin ID!")
        admin_input = input("Введите ваш Telegram ID: ").strip()
        try:
            cfg.set("admin_ids", [int(admin_input)])
        except ValueError:
            print("❌ Неверный ID. Укажите вручную в config.json")
            sys.exit(1)

    # 2. База данных
    db = Database()
    logger.info("✅ База данных готова")
    
    # 🔥 === ВСТАВЛЯТЬ ЗДЕСЬ: Загрузка настроек зеркала из БД ===
    if db and cfg.get("app.role") == "mirror":
        mirror_user_id = cfg.admin_ids[0] if cfg.admin_ids else None
        if mirror_user_id:
            mirror = db.get_mirror_user(mirror_user_id)
            if mirror:
                try:
                    settings = json.loads(mirror.get("settings_json", "{}") or "{}")
                    if settings:
                        if "twiboost_cookies" in settings:
                            cfg.set("twiboost_web.cookies", settings["twiboost_cookies"])
                        if "funpay_golden_key" in settings and settings["funpay_golden_key"]:
                            cfg.set("funpay_golden_key", settings["funpay_golden_key"])
                        if "twiboost_api_key" in settings and settings["twiboost_api_key"]:
                            cfg.set("twiboost_api_key", settings["twiboost_api_key"])
                        logger.info("✅ Настройки зеркала успешно подгружены из базы")
                except Exception as e:
                    logger.error(f"Ошибка загрузки настроек зеркала: {e}")

    # 3. API клиент TwiBoost
    api_client = None
    if cfg.twiboost_api_key:
        api_client = TwiBoostAPI(cfg.twiboost_api_key, cfg.twiboost_api_url, cfg.get("twiboost_web", {}))
        r = api_client.test_connection()
        if r["success"]:
            currency = str(r.get("currency", "USD")).upper()
            usd_rub = cfg.get("usd_rub_rate", 92)
            balance = r["balance"]
            symbol_map = {"USD": "$", "EUR": "€", "RUB": "₽", "RUR": "₽"}
            symbol = symbol_map.get(currency)
            if symbol == "$" or symbol == "€":
                balance_text = f"{symbol}{balance:.2f}"
                rub_hint = f" (≈ {balance * usd_rub:.0f}₽)" if currency == "USD" else ""
            elif symbol == "₽":
                balance_text = f"{balance:.2f}{symbol}"
                rub_hint = ""
            else:
                balance_text = f"{balance:.2f} {currency}"
                rub_hint = ""
            logger.info(f"✅ TwiBoost: {balance_text}{rub_hint}")
        else:
            logger.warning(f"⚠️ TwiBoost: {r['error']}")
    else:
        logger.info("ℹ️ API ключ не настроен — настройте через бота")
        api_client = TwiBoostAPI("", cfg.twiboost_api_url, cfg.get("twiboost_web", {}))

    # 4. FunPay клиент
    fp_client = None
    fp_profile_lots_count = 0
    if cfg.funpay_golden_key:
        # Check DNS resolution first
        try:
            import socket
            socket.gethostbyname('funpay.com')
            logger.info("✅ DNS FunPay доступен")
            try:
                # Parse proxy if configured
                proxy = None
                if cfg.funpay_proxy:
                    try:
                        import json
                        proxy = json.loads(cfg.funpay_proxy)
                        logger.info(f"✅ Используется прокси: {proxy.get('http', 'N/A')}")
                    except:
                        logger.warning("⚠️ Неверный формат прокси в конфиге")
                
                fp_client = FunPayClient(cfg.funpay_golden_key, proxy=proxy)
                if fp_client.start():
                    logger.info(f"✅ FunPay: {fp_client.username} (ID: {fp_client.user_id})")
                    lots_result = fp_client.get_profile_lots()
                    if lots_result.get("success"):
                        fp_profile_lots_count = lots_result.get("count", len(lots_result.get("lots", [])))
                        logger.info(f"✅ FunPay lots detected: {fp_profile_lots_count}")
                else:
                    logger.warning("⚠️ FunPay: не удалось запустить runner")
                    fp_client = None
            except Exception as e:
                logger.error(f"⚠️ FunPay: ошибка подключения — {e}")
                logger.info("ℹ️ Бот будет работать без FunPay до восстановления доступа")
                fp_client = None
        except socket.gaierror:
            logger.warning("⚠️ DNS: funpay.com недоступен - проверьте подключение к интернету")
            logger.info("ℹ️ Бот будет работать без FunPay до восстановления доступа")
    else:
        logger.info("ℹ️ FunPay golden_key не настроен — настройте через бота")

    # 5. Telegram бот
    # Parse proxy for Telegram if configured
    telegram_proxy = None
    if cfg.funpay_proxy:
        try:
            import json
            proxy_data = json.loads(cfg.funpay_proxy)
            telegram_proxy = proxy_data.get('http') or proxy_data.get('https')
            if telegram_proxy:
                logger.info(f"✅ Telegram использует прокси: {telegram_proxy}")
        except:
            logger.warning("⚠️ Неверный формат прокси для Telegram")
    
    telegram_proxy = _parse_telegram_proxy(cfg.telegram_proxy)
    _TELEGRAM_PROXY_CONFIG = telegram_proxy
    if cfg.telegram_proxy and not telegram_proxy:
        logger.warning("⚠️ Неверный формат telegram_proxy. Используйте host:port или http://host:port")
    elif telegram_proxy:
        logger.info(f"✅ Telegram использует прокси: {telegram_proxy.get('http') or telegram_proxy.get('https')}")
    _configure_telegram_network(telegram_proxy)
    bot = telebot.TeleBot(cfg.bot_token, parse_mode="HTML")
    bot = _patch_telegram_bot(bot)
    logger.info("✅ Telegram бот создан")

    # 6. Регистрация хэндлеров
    handlers.setup(bot, cfg, db, api_client, fp_client, support_client)
    logger.info("✅ Хэндлеры зарегистрированы")
        # 6.1 Восстановление зеркал
    handlers.restore_mirror_instances()

    # 7. Фоновые задачи
    if cfg.twiboost_api_key:
        Thread(target=handlers.order_checker_loop_v2, args=(db, api_client, cfg, bot, stop_event, logger), daemon=True, name="OrderChecker").start()
        Thread(target=balance_checker_loop, args=(db, api_client, cfg, bot), daemon=True, name="BalanceChecker").start()
        logger.info("✅ Фоновые задачи TwiBoost запущены")

    if fp_client and fp_client._initiated:
        Thread(
            target=funpay_support_loop,
            args=(fp_client, db, cfg, bot, support_client),
            daemon=True,
            name="FunPaySupport",
        ).start()
        logger.info("✅ Поддержка FunPay запущена")
    # 🔥 Авто-обновление XSRF-TOKEN (раз в час)
    if api_client and cfg.get("twiboost_web.enabled"):
        Thread(
            target=xsrf_refresh_loop,
            args=(api_client, cfg, 3600),  # 3600 секунд = 1 час
            daemon=True,
            name="XSRFRefresher",
        ).start()
        logger.info("✅ Запущено фоновое обновление XSRF-TOKEN")

    # 8. Уведомление о запуске
    fp_status = f"🎮 FunPay: ✅ {fp_client.username}" if fp_client and fp_client._initiated else "🎮 FunPay: ❌ не подключен"
    startup_text = (
        "✅ <b>Бот запущен</b>\n\n"
        f"🌐 API: {'✅' if cfg.twiboost_api_key else '❌ не настроен'}\n"
        f"🎮 FunPay: {'✅ ' + fp_client.username if fp_client and fp_client._initiated else '❌ не подключен'}\n"
        f"🛒 Лотов: <b>{db.get_lots_count()}</b>\n"
        f"🎮 Лотов на FunPay: <b>{fp_profile_lots_count}</b>\n"
        f"🔗 С FunPay: <b>{len([l for l in db.get_lots() if l.get('funpay_lot_id')])}</b>\n"
        f"📦 Активных заказов: <b>{len(db.get_active_orders())}</b>\n\n"
        "📱 Отправьте /smm для управления"
    )
    for uid in (cfg.admin_ids if cfg.get("notifications.startup", False) else []):
        try:
            text = (
                "╔══════════════════════════╗\n"
                "║  🤖 <b>SMM Auto Bot</b>  ║\n"
                "╚══════════════════════════╝\n\n"
                "✅ Бот запущен!\n\n"
                f"🌐 API: {'✅' if cfg.twiboost_api_key else '❌ не настроен'}\n"
                f"{fp_status}\n"
                f"🛒 Лотов: <b>{db.get_lots_count()}</b>\n"
                f"🎮 Лотов на FunPay: <b>{fp_profile_lots_count}</b>\n"
                f"🔗 С FunPay: <b>{len([l for l in db.get_lots() if l.get('funpay_lot_id')])}</b>\n"
                f"📦 Активных заказов: <b>{len(db.get_active_orders())}</b>\n\n"
                "📱 Отправьте /smm для управления"
            )
            bot.send_message(uid, text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Не удалось отправить уведомление {uid}: {e}")

    logger.info("=" * 50)
    logger.info("🚀 Бот запущен! Ctrl+C для остановки")
    logger.info("=" * 50)

    # 8. Polling with improved error handling
    while not stop_event.is_set():
        try:
            _configure_telegram_network(telegram_proxy)
            logger.info("Starting Telegram polling...")
            bot.polling(
                non_stop=False,
                interval=0,
                timeout=20,
                long_polling_timeout=10,
                skip_pending=False,
                allowed_updates=["message", "callback_query"],
            )
        except KeyboardInterrupt:
            logger.info("⏹ Остановка...")
            stop_event.set()
            break
        except Exception as e:
            logger.error(f"Polling error: {e}")
            error_text = str(e).lower()
            if (
                "timeout" in error_text
                or "connection" in error_text
                or "ssl" in error_text
                or "proxy" in error_text
                or "reset" in error_text
            ):
                logger.info("Telegram connection issue, resetting session and retrying in 5 seconds...")
                _configure_telegram_network(telegram_proxy)
                stop_event.wait(5)
            else:
                logger.error("Fatal polling error, stopping")
                break


if __name__ == "__main__":
    main()
