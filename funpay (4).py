import logging
import html
import json
import re
import socket
import ssl
import threading
import time
import urllib3
import sys
from pathlib import Path
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Optional

from bs4 import BeautifulSoup

_LOCAL_FUNPAYAPI_ROOT = Path(__file__).resolve().parent / "funpaycardinal" / "FunPayCardinal-main"
if _LOCAL_FUNPAYAPI_ROOT.exists():
    local_root = str(_LOCAL_FUNPAYAPI_ROOT)
    if local_root not in sys.path:
        sys.path.append(local_root)

from FunPayAPI.account import Account
from FunPayAPI.common import exceptions as fp_exceptions
from FunPayAPI.common.enums import Currency, OrderStatuses, SubCategoryTypes
from FunPayAPI import types as fp_types
from FunPayAPI.updater import events as fp_events
from FunPayAPI.updater.runner import Runner

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
ssl._create_default_https_context = ssl._create_unverified_context

logger = logging.getLogger("SMM.funpay")

FUNPAY_REQUEST_TIMEOUT = 20


class FunPayEventType(Enum):
    NEW_MESSAGE = "new_message"
    NEW_ORDER = "new_order"
    ORDER_STATUS_CHANGED = "order_status_changed"


@dataclass
class CompatSale:
    order_id: str
    id: str
    status: str
    price: float
    currency: str
    buyer_username: str
    buyer_id: int | None
    chat_id: int | str | None
    description: str
    amount: int | None
    subcategory_name: str | None
    raw: object


class FunPayClient:
    def __init__(self, golden_key: str, user_agent: str = None, proxy: dict = None):
        self.golden_key = golden_key
        self.proxy = proxy
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        self._account: Optional[Account] = None
        self._runner: Optional[Runner] = None
        self._runner_loop_thread: Optional[threading.Thread] = None
        self._runner_thread: Optional[threading.Thread] = None
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._initiated = False
        self._seen_order_states: dict[str, str] = {}
        self._seen_chat_messages: dict[int | str, int] = {}
        self._poll_bootstrapped = False
        self._staff_chat_cache_id: Optional[int] = None
        self._staff_chat_cache_name: Optional[str] = None
        self._staff_chat_cache_at = 0.0

        self._listeners: Dict[FunPayEventType, list] = {
            FunPayEventType.NEW_MESSAGE: [],
            FunPayEventType.NEW_ORDER: [],
            FunPayEventType.ORDER_STATUS_CHANGED: [],
        }

        self.user_id = None
        self.username = None
        self.balance = 0.0

    def start(self) -> bool:
        if self._runner_thread and self._runner_thread.is_alive():
            return True

        try:
            logger.info("FunPay: start init")
            logger.info("FunPay: creating Account")
            self._account = Account(
                self.golden_key,
                user_agent=self.user_agent,
                proxy=self.proxy,
                requests_timeout=FUNPAY_REQUEST_TIMEOUT,
            )
            logger.info("FunPay: requesting account page")
            self._account.get()
            self.user_id = self._account.id
            self.username = self._account.username
            self.balance = float(getattr(self._account, "total_balance", 0) or 0)
            logger.info("FunPay: account loaded (%s, %s)", self.username, self.user_id)
            logger.info("FunPay: creating Runner")
            self._runner = Runner(self._account, disable_message_requests=False, disabled_order_requests=False)
            self._initiated = True
        except fp_exceptions.UnauthorizedError as e:
            logger.error("FunPay: invalid golden_key (%s)", e)
            return False
        except Exception as e:
            logger.error("FunPay: init error - %s", e, exc_info=True)
            return False

        self._stop_event.clear()
        self._runner_loop_thread = threading.Thread(target=self._runner_worker_loop, name="FunPayRunnerLoop", daemon=True)
        self._runner_thread = threading.Thread(target=self._runner_loop, name="FunPayRunner", daemon=True)
        self._poll_thread = threading.Thread(target=self._poll_loop, name="FunPayPoller", daemon=True)
        self._runner_loop_thread.start()
        time.sleep(0.2)
        self._runner_thread.start()
        self._poll_thread.start()
        logger.info("FunPay: runner started for %s (%s)", self.username, self.user_id)
        return True

    def stop(self):
        self._stop_event.set()
        if self._runner_thread and self._runner_thread.is_alive():
            self._runner_thread.join(timeout=5)
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=5)
        self._runner_loop_thread = None
        self._runner_thread = None
        self._poll_thread = None

    def add_listener(self, event_type: FunPayEventType, callback: Callable):
        self._listeners[event_type].append(callback)

    def on(self, event_type: FunPayEventType, callback: Callable):
        self.add_listener(event_type, callback)

    def get_order(self, order_id: str):
        if not self._account:
            raise RuntimeError("FunPay account not initialised")
        return self._account.get_order(str(order_id))

    def get_profile_lots(self) -> dict:
        if not self._account:
            return {"success": False, "error": "Account not initialised"}
        try:
            profile = self._account.get_user(self._account.id)
            lots = profile.get_lots()
            result = []
            for lot in lots:
                subcategory = getattr(lot, "subcategory", None)
                result.append({
                    "id": lot.id,
                    "offer_id": lot.id,
                    "title": lot.title or lot.description or f"Lot #{lot.id}",
                    "description": lot.description or "",
                    "price": lot.price,
                    "currency": self._normalize_currency(lot.currency),
                    "category": subcategory.ui_name if subcategory else "",
                    "subcategory_id": subcategory.id if subcategory else None,
                    "category_id": subcategory.category.id if subcategory and getattr(subcategory, "category", None) else None,
                    "active": getattr(lot, "active", True),
                    "public_link": getattr(lot, "public_link", ""),
                })
            return {"success": True, "count": len(result), "lots": result}
        except Exception as e:
            logger.error("FunPay: failed to load lots - %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    def get_offer_edit_form(self, node_id: int, offer_id: int | None = None) -> dict:
        if not self._account:
            return {"success": False, "error": "Account not initialised"}
        try:
            api_method = f"lots/offerEdit?offer={int(offer_id)}" if offer_id else f"lots/offerEdit?node={int(node_id)}"
            response = self._account.method("get", api_method, {}, {}, raise_not_200=True)
            html_response = response.content.decode()
            bs = BeautifulSoup(html_response, "lxml")
            form = bs.find("form", class_="form-offer-editor")
            if not form:
                return {"success": False, "error": "Форма FunPay не найдена"}

            payload = {}
            for field in form.find_all("input"):
                name = field.get("name")
                if not name or name == "query":
                    continue
                field_type = str(field.get("type") or "").lower()
                if field_type == "checkbox":
                    if field.has_attr("checked"):
                        payload[name] = field.get("value") or "on"
                    continue
                payload[name] = field.get("value") or ""

            for field in form.find_all("textarea"):
                name = field.get("name")
                if not name:
                    continue
                payload[name] = field.text or ""

            field_schema_map = {}
            lot_fields = form.find("div", class_="lot-fields")
            if lot_fields and lot_fields.get("data-fields"):
                try:
                    parsed = json.loads(html.unescape(lot_fields.get("data-fields")))
                    if isinstance(parsed, list):
                        field_schema_map = {str(item.get("id")): item for item in parsed if isinstance(item, dict) and item.get("id")}
                except Exception:
                    field_schema_map = {}

            field_schema = []
            for wrapper in form.find_all("div", class_="lot-field"):
                field_id = str(wrapper.get("data-id") or "").strip()
                if not field_id:
                    continue
                label_tag = wrapper.find("label", class_="control-label")
                label = label_tag.get_text(" ", strip=True) if label_tag else field_id
                classes = wrapper.get("class", []) or []
                is_hidden = "hidden" in classes
                input_tag = wrapper.find("select") or wrapper.find("textarea") or wrapper.find("input", class_="lot-field-input")
                field_name = input_tag.get("name") if input_tag else ""
                field_kind = "text"
                options = []
                current_value = payload.get(field_name, "")
                if input_tag:
                    if input_tag.name == "select":
                        field_kind = "select"
                        for option in input_tag.find_all("option"):
                            value = option.get("value", "")
                            if value == "":
                                continue
                            options.append({
                                "value": value,
                                "label": option.get_text(" ", strip=True) or value,
                            })
                        selected = input_tag.find("option", selected=True)
                        if selected is not None:
                            current_value = selected.get("value", "")
                    elif input_tag.name == "textarea":
                        field_kind = "textarea"
                    else:
                        field_kind = str(input_tag.get("type") or "text").lower()

                schema_item = field_schema_map.get(field_id, {})
                field_schema.append({
                    "id": field_id,
                    "name": field_name,
                    "label": label,
                    "kind": field_kind,
                    "hidden": is_hidden,
                    "options": options,
                    "value": current_value,
                    "conditions": schema_item.get("conditions", []) if isinstance(schema_item, dict) else [],
                    "schema_type": schema_item.get("type") if isinstance(schema_item, dict) else None,
                })

            return {
                "success": True,
                "node_id": int(node_id),
                "offer_id": int(offer_id or 0),
                "action": form.get("action") or "https://funpay.com/lots/offerSave",
                "fields": payload,
                "field_schema": field_schema,
            }
        except Exception as e:
            logger.error("FunPay: failed to parse offer edit form for node=%s offer=%s - %s", node_id, offer_id, e, exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    def _extract_offer_id_from_payload(payload: Any) -> int | None:
        if payload is None:
            return None
        if isinstance(payload, (int, float)) and int(payload) > 0:
            return int(payload)
        if isinstance(payload, str):
            match = re.search(r"(?:offer(?:_id)?|id)[=/:\"]+(\d+)", payload, flags=re.I)
            if match:
                return int(match.group(1))
            if payload.isdigit():
                return int(payload)
            return None
        if isinstance(payload, list):
            for item in payload:
                found = FunPayClient._extract_offer_id_from_payload(item)
                if found:
                    return found
            return None
        if isinstance(payload, dict):
            for key in ("offer_id", "offerId", "id", "offer"):
                found = FunPayClient._extract_offer_id_from_payload(payload.get(key))
                if found:
                    return found
            for value in payload.values():
                found = FunPayClient._extract_offer_id_from_payload(value)
                if found:
                    return found
        return None

    def create_offer(self, node_id: int, *, field_values: dict[str, str], price: float | int | str,
                     amount: int | str, summary_ru: str, summary_en: str = "", desc_ru: str = "",
                     desc_en: str = "", payment_msg_ru: str = "", payment_msg_en: str = "",
                     active: bool = True) -> dict:
        if not self._account:
            return {"success": False, "error": "Account not initialised"}
        form = self.get_offer_edit_form(node_id)
        if not form.get("success"):
            return form
        try:
            before = self.get_profile_lots()
            before_ids = {str(lot.get("offer_id")) for lot in (before.get("lots") or [])} if before.get("success") else set()

            payload = dict(form["fields"])
            payload["csrf_token"] = payload.get("csrf_token") or getattr(self._account, "csrf_token", "")
            payload["offer_id"] = "0"
            payload["node_id"] = str(int(node_id))
            payload["fields[summary][ru]"] = str(summary_ru or "")
            payload["fields[summary][en]"] = str(summary_en or summary_ru or "")
            payload["fields[desc][ru]"] = str(desc_ru or "")
            payload["fields[desc][en]"] = str(desc_en or desc_ru or "")
            payload["fields[payment_msg][ru]"] = str(payment_msg_ru or "")
            payload["fields[payment_msg][en]"] = str(payment_msg_en or "")
            payload["fields[images]"] = payload.get("fields[images]", "")
            payload["price"] = str(price)
            payload["amount"] = str(amount)
            if active:
                payload["active"] = "on"
            else:
                payload.pop("active", None)

            for field_id, value in (field_values or {}).items():
                payload[f"fields[{field_id}]"] = str(value)

            headers = {
                "accept": "*/*",
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "x-requested-with": "XMLHttpRequest",
            }
            response = self._account.method("post", "lots/offerSave", headers, payload, raise_not_200=True)
            try:
                data = response.json()
            except Exception:
                data = {}

            errors = {}
            if isinstance(data, dict):
                if data.get("errors"):
                    try:
                        for key, value in data.get("errors"):
                            errors[str(key)] = str(value)
                    except Exception:
                        pass
                if data.get("error") or errors:
                    err = data.get("error") or "; ".join(f"{k}: {v}" for k, v in errors.items())
                    return {"success": False, "error": err or "FunPay вернул ошибку при сохранении лота", "raw": data}

            offer_id = self._extract_offer_id_from_payload(data)
            title_match = str(summary_ru or summary_en or "").strip()

            after = self.get_profile_lots()
            if after.get("success"):
                new_lots = after.get("lots") or []
                if not offer_id:
                    for lot in new_lots:
                        lot_offer_id = str(lot.get("offer_id") or "")
                        if lot_offer_id and lot_offer_id not in before_ids:
                            offer_id = int(lot_offer_id)
                            break
                if offer_id:
                    for lot in new_lots:
                        if str(lot.get("offer_id")) == str(offer_id):
                            return {
                                "success": True,
                                "offer_id": str(offer_id),
                                "title": lot.get("title") or title_match,
                                "price": lot.get("price"),
                                "raw": data,
                            }
                if title_match:
                    for lot in new_lots:
                        if str(lot.get("title") or "").strip() == title_match:
                            return {
                                "success": True,
                                "offer_id": str(lot.get("offer_id") or ""),
                                "title": lot.get("title") or title_match,
                                "price": lot.get("price"),
                                "raw": data,
                            }

            return {
                "success": True,
                "offer_id": str(offer_id or ""),
                "title": title_match,
                "raw": data,
            }
        except Exception as e:
            logger.error("FunPay: create_offer failed for node=%s - %s", node_id, e, exc_info=True)
            return {"success": False, "error": str(e)}

    def get_sales(self, **kwargs) -> dict:
        if not self._account:
            return {"success": False, "error": "Account not initialised"}
        try:
            next_id, orders, *_ = self._account.get_sales(**kwargs)
            return {
                "success": True,
                "next": next_id,
                "orders": [self._adapt_sale(order) for order in orders],
            }
        except Exception as e:
            logger.error("FunPay: failed to load sales - %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    def get_order_details(self, order_id: str) -> dict:
        if not self._account:
            return {"success": False, "error": "Account not initialised"}
        try:
            order = self._account.get_order(str(order_id))
            offer_id = self._extract_offer_id(order)
            review = getattr(order, "review", None)
            return {
                "success": True,
                "order_id": order.id,
                "id": order.id,
                "status": self._normalize_status(order.status),
                "price": order.sum,
                "currency": self._normalize_currency(order.currency),
                "description": order.full_description or order.short_description or "",
                "short_description": order.short_description or "",
                "buyer_username": order.buyer_username,
                "buyer_id": order.buyer_id,
                "chat_id": order.chat_id,
                "offer_id": offer_id,
                "amount": getattr(order, "amount", None),
                "review_stars": review.stars if review else 0,
            }
        except Exception as e:
            logger.error("FunPay: failed to load order %s - %s", order_id, e, exc_info=True)
            return {"success": False, "error": str(e)}

    def send_message(self, chat_id: int | str, text: str, **kwargs) -> dict:
        if not self._account:
            return {"success": False, "error": "Account not initialised"}
        try:
            message = self._account.send_message(chat_id, text, **kwargs)
            return {"success": True, "message_id": message.id}
        except Exception as e:
            logger.error("FunPay: send_message failed for %s - %s", chat_id, e)
            return {"success": False, "error": str(e)}

    def refund_order(self, order_id: str) -> dict:
        if not self._account:
            return {"success": False, "error": "Account not initialised"}
        try:
            self._account.refund(str(order_id))
            return {"success": True}
        except Exception as e:
            logger.error("FunPay: refund_order failed for %s - %s", order_id, e, exc_info=True)
            return {"success": False, "error": str(e)}

    def preview_withdraw(self, currency_id: str, ext_currency_id: str, wallet: str, wallet_extra: str,
                         amount_int: int, twofactor_code: str = "") -> dict:
        return self._withdraw_request(
            currency_id=currency_id,
            ext_currency_id=ext_currency_id,
            wallet=wallet,
            wallet_extra=wallet_extra,
            amount_int=amount_int,
            twofactor_code=twofactor_code,
            preview=True,
        )

    def create_withdraw(self, currency_id: str, ext_currency_id: str, wallet: str, wallet_extra: str,
                        amount_int: int, twofactor_code: str = "", preview: bool = False, **extra_fields) -> dict:
        return self._withdraw_request(
            currency_id=currency_id,
            ext_currency_id=ext_currency_id,
            wallet=wallet,
            wallet_extra=wallet_extra,
            amount_int=amount_int,
            twofactor_code=twofactor_code,
            preview=preview,
            extra_fields=extra_fields,
        )

    def _withdraw_request(self, currency_id: str, ext_currency_id: str, wallet: str, wallet_extra: str,
                          amount_int: int, twofactor_code: str = "", preview: bool = True,
                          extra_fields: Optional[dict] = None) -> dict:
        if not self._account:
            return {"success": False, "error": "Account not initialised"}
        try:
            payload = {
                "csrf_token": getattr(self._account, "csrf_token", "") or "",
                "preview": "1" if preview else "",
                "currency_id": str(currency_id or "rub").strip(),
                "ext_currency_id": str(ext_currency_id or "fps").strip(),
                "wallet_extra": str(wallet_extra or "").strip(),
                "wallet": str(wallet or "").strip(),
                "amount_int": str(int(amount_int or 0)),
                "twofactor_code": str(twofactor_code or "").strip(),
            }
            if extra_fields:
                payload.update({k: "" if v is None else str(v) for k, v in extra_fields.items()})
            headers = {
                "accept": "application/json, text/javascript, */*; q=0.01",
                "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                "x-requested-with": "XMLHttpRequest",
                "origin": "https://funpay.com",
                "referer": "https://funpay.com/account/balance",
            }
            response = self._account.method("post", "withdraw/withdraw", headers, payload, raise_not_200=True)
            try:
                data = response.json()
            except Exception:
                data = {"raw_text": response.text}
            return {
                "success": True,
                "preview": preview,
                "status_code": response.status_code,
                "data": data,
                "raw_text": response.text,
            }
        except Exception as e:
            logger.error("FunPay: withdraw request failed - %s", e, exc_info=True)
            return {"success": False, "error": str(e), "preview": preview}

    def get_withdraw_options(self) -> dict:
        if not self._account:
            return {"success": False, "error": "Account not initialised"}
        try:
            response = self._account.method("get", "account/balance", {}, {}, raise_not_200=True)
            html_response = response.content.decode(errors="ignore")
            bs = BeautifulSoup(html_response, "lxml")

            bank_options = []
            bank_select = bs.find("select", attrs={"name": "wallet_extra"})
            if bank_select:
                for option in bank_select.find_all("option"):
                    value = str(option.get("value") or "").strip()
                    if not value:
                        continue
                    label = option.get_text(" ", strip=True)
                    if not label:
                        content = html.unescape(str(option.get("data-content") or ""))
                        if content:
                            label = BeautifulSoup(content, "lxml").get_text(" ", strip=True)
                    bank_options.append({"value": value, "label": label or value})

            ext_options = []
            ext_select = bs.find("select", attrs={"name": "ext_currency_id"})
            if ext_select:
                for option in ext_select.find_all("option"):
                    value = str(option.get("value") or "").strip()
                    if not value:
                        continue
                    ext_options.append({"value": value, "label": option.get_text(" ", strip=True) or value})

            return {
                "success": True,
                "banks": bank_options,
                "ext_currency_options": ext_options,
            }
        except Exception as e:
            logger.error("FunPay: failed to load withdraw options - %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    @staticmethod
    def _is_staff_chat_message(message) -> bool:
        return bool(
            getattr(message, "is_support", False)
            or getattr(message, "is_moderation", False)
            or getattr(message, "is_arbitration", False)
        )

    @staticmethod
    def _staff_chat_name_match(name: str | None) -> bool:
        text = str(name or "").strip().lower()
        return any(token in text for token in ("поддерж", "support", "модерац", "moderation", "арбитраж", "arbitration"))

    def find_staff_chat(self, force: bool = False) -> dict:
        if not self._account:
            return {"success": False, "error": "Account not initialised"}

        now = time.time()
        if (
            not force
            and self._staff_chat_cache_id
            and now - self._staff_chat_cache_at < 1800
        ):
            return {
                "success": True,
                "chat_id": self._staff_chat_cache_id,
                "chat_name": self._staff_chat_cache_name or "",
            }

        try:
            chats = self._account.get_chats(update=True)
            chat_items = list(chats.values()) if isinstance(chats, dict) else list(chats or [])
            for chat in chat_items:
                if self._staff_chat_name_match(getattr(chat, "name", None)):
                    self._staff_chat_cache_id = int(chat.id)
                    self._staff_chat_cache_name = getattr(chat, "name", "") or ""
                    self._staff_chat_cache_at = now
                    return {
                        "success": True,
                        "chat_id": self._staff_chat_cache_id,
                        "chat_name": self._staff_chat_cache_name,
                    }
                try:
                    history = self._account.get_chat_history(chat.id, interlocutor_username=getattr(chat, "name", None))
                except Exception as history_error:
                    logger.debug("FunPay: failed to inspect chat %s for staff badge - %s", getattr(chat, "id", "?"), history_error)
                    continue

                staff_messages = [message for message in history if self._is_staff_chat_message(message)]
                foreign_user_messages = [
                    message for message in history
                    if not self._is_staff_chat_message(message)
                    and not getattr(message, "by_bot", False)
                    and getattr(message, "author_id", None) not in (self.user_id, 0, None)
                ]
                if staff_messages and not foreign_user_messages:
                    self._staff_chat_cache_id = int(chat.id)
                    self._staff_chat_cache_name = getattr(chat, "name", "") or ""
                    self._staff_chat_cache_at = now
                    return {
                        "success": True,
                        "chat_id": self._staff_chat_cache_id,
                        "chat_name": self._staff_chat_cache_name,
                    }

            return {"success": False, "error": "Support chat not found"}
        except Exception as e:
            logger.error("FunPay: failed to resolve support chat - %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    def send_support_message(self, text: str, force_scan: bool = False) -> dict:
        if not text:
            return {"success": False, "error": "Empty support message"}

        chat_info = self.find_staff_chat(force=force_scan)
        if not chat_info.get("success"):
            return chat_info

        result = self.send_message(chat_info["chat_id"], text, chat_name=chat_info.get("chat_name"))
        if result.get("success"):
            result["chat_id"] = chat_info["chat_id"]
            result["chat_name"] = chat_info.get("chat_name", "")
        return result

    def get_chat_id_by_username(self, username_or_id) -> int:
        if not self._account:
            raise RuntimeError("FunPay account not initialised")
        try:
            if isinstance(username_or_id, int) or (isinstance(username_or_id, str) and username_or_id.isdigit()):
                buyer_id = int(username_or_id)
                runner = self._runner or getattr(self._account, "runner", None)
                if runner:
                    for chat_id, interlocutor_id in getattr(runner, "users_ids", {}).items():
                        if interlocutor_id == buyer_id:
                            return int(chat_id)

            chats = self._account.get_chats(update=True)
            if isinstance(chats, dict):
                for chat in chats.values():
                    if chat.name == username_or_id:
                        return chat.id
            else:
                for chat in chats:
                    if chat.name == username_or_id:
                        return chat.id
            return 0
        except Exception as e:
            logger.error("FunPay: failed to resolve chat %s - %s", username_or_id, e)
            return 0

    def get_chat_by_name(self, name: str, make_request: bool = False, **kwargs):
        if not self._account:
            raise RuntimeError("FunPay account not initialised")
        try:
            if "create_if_not_exists" in kwargs and kwargs["create_if_not_exists"]:
                make_request = True
            return self._account.get_chat_by_name(name, make_request=make_request)
        except Exception as e:
            logger.error("FunPay: failed to get chat by name %s - %s", name, e)
            return None

    def create_chat_with_user(self, username: str):
        return self.get_chat_by_name(username, make_request=True)

    def get_categories(self):
        if not self._account:
            return []
        try:
            categories = []
            for category in self._account.categories:
                subcategories = []
                for subcategory in category.get_subcategories():
                    subcategories.append({
                        "id": subcategory.id,
                        "name": subcategory.name,
                        "type": subcategory.type.name.lower(),
                        "category_name": category.name,
                    })
                categories.append({"id": category.id, "name": category.name, "subcategories": subcategories})
            return categories
        except Exception as e:
            logger.error("FunPay: failed to load categories - %s", e, exc_info=True)
            return []

    def raise_profile_lots(self) -> dict:
        if not self._account:
            return {"success": False, "error": "Account not initialised"}
        try:
            profile = self._account.get_user(self._account.id)
            lots = profile.get_lots()
            grouped: dict[int, set[int]] = {}
            total_lots = 0
            for lot in lots:
                subcategory = getattr(lot, "subcategory", None)
                category = getattr(subcategory, "category", None)
                if not subcategory or not category:
                    continue
                if subcategory.type is not SubCategoryTypes.COMMON:
                    continue
                grouped.setdefault(category.id, set()).add(subcategory.id)
                total_lots += 1

            if not grouped:
                return {"success": True, "categories": 0, "lots": 0}

            for category_id, subcategories in grouped.items():
                self._account.raise_lots(category_id, subcategories=sorted(subcategories))
                time.sleep(1)

            return {"success": True, "categories": len(grouped), "lots": total_lots}
        except fp_exceptions.RaiseError as e:
            wait_time = getattr(e, "wait_time", None)
            message = getattr(e, "error_message", None) or str(e)
            return {"success": False, "error": message, "wait_time": wait_time}
        except Exception as e:
            logger.error("FunPay: failed to raise lots - %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    def test_connection(self) -> dict:
        try:
            account = Account(
                self.golden_key,
                user_agent=self.user_agent,
                proxy=self.proxy,
                requests_timeout=FUNPAY_REQUEST_TIMEOUT,
            )
            account.get()
            user = account.get_user(account.id)
            profile = account.get_user(account.id)
            lots = profile.get_lots()
            balance = self._resolve_balance_for_account(account, lots)
            return {
                "success": True,
                "username": user.username,
                "user_id": user.id,
                "balance": balance,
                "lots_count": len(lots),
            }
        except fp_exceptions.UnauthorizedError as e:
            return {"success": False, "error": f"Invalid golden_key: {e}"}
        except Exception as e:
            logger.error("FunPay: test_connection failed - %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    def _emit(self, event_type: FunPayEventType, payload: object):
        if event_type is FunPayEventType.NEW_ORDER:
            order = getattr(payload, "order", None)
            if order is not None:
                self._seen_order_states[getattr(order, "id")] = self._normalize_status(getattr(order, "status", ""))
        elif event_type is FunPayEventType.ORDER_STATUS_CHANGED:
            order = getattr(payload, "order", None)
            if order is not None:
                self._seen_order_states[getattr(order, "id")] = self._normalize_status(getattr(order, "status", ""))
        elif event_type is FunPayEventType.NEW_MESSAGE:
            message = getattr(payload, "message", None)
            if message is not None:
                self._seen_chat_messages[getattr(message, "chat_id")] = getattr(message, "id", 0)

        for cb in self._listeners.get(event_type, []):
            try:
                cb(payload)
            except Exception as e:
                logger.error("FunPay listener error (%s): %s", event_type, e, exc_info=True)

    def _runner_loop(self):
        if not self._runner:
            return

        while not self._stop_event.is_set():
            try:
                for event in self._runner.listen(requests_delay=5.0, ignore_exceptions=True):
                    if self._stop_event.is_set():
                        break

                    if isinstance(event, fp_events.NewOrderEvent):
                        self._emit(FunPayEventType.NEW_ORDER, event)
                        logger.info("FunPay: new order #%s", event.order.id)
                    elif isinstance(event, fp_events.InitialOrderEvent):
                        self._seen_order_states[getattr(event.order, "id")] = self._normalize_status(
                            getattr(event.order, "status", "")
                        )
                        logger.info("FunPay: initial order #%s", event.order.id)
                    elif isinstance(event, fp_events.NewMessageEvent):
                        self._emit(FunPayEventType.NEW_MESSAGE, event)
                        logger.info("FunPay: new message chat=%s msg=%s", event.message.chat_id, event.message.id)
                    elif isinstance(event, fp_events.InitialChatEvent):
                        self._seen_chat_messages[getattr(event.chat, "id")] = getattr(event.chat, "node_msg_id", 0)
                        logger.info("FunPay: initial chat chat=%s", event.chat.id)
                    elif isinstance(event, fp_events.LastChatMessageChangedEvent):
                        self._seen_chat_messages[getattr(event.chat, "id")] = getattr(event.chat, "node_msg_id", 0)
                        logger.info(
                            "FunPay: last chat message changed chat=%s node_msg=%s",
                            event.chat.id,
                            getattr(event.chat, "node_msg_id", 0),
                        )
                    elif isinstance(event, fp_events.OrderStatusChangedEvent):
                        self._emit(FunPayEventType.ORDER_STATUS_CHANGED, event)
                        logger.info("FunPay: order status changed #%s", event.order.id)
            except Exception as e:
                logger.error("FunPay: runner loop error - %s", e, exc_info=True)
                if self._stop_event.wait(10):
                    break

    def _runner_worker_loop(self):
        if not self._runner:
            return
        try:
            logger.info("FunPay: runner loop thread started")
            self._runner.loop()
        except Exception as e:
            logger.error("FunPay: runner worker error - %s", e, exc_info=True)

    def _poll_loop(self):
        while not self._stop_event.wait(8):
            if not self._account:
                continue
            try:
                if not self._poll_bootstrapped:
                    self._bootstrap_poll_state()
                    self._poll_bootstrapped = True
                    logger.info("FunPay: fallback poll bootstrap completed")
                    continue
                self._poll_orders()
            except Exception as e:
                logger.error("FunPay: order poll error - %s", e, exc_info=True)
            try:
                self._poll_messages()
            except Exception as e:
                logger.error("FunPay: message poll error - %s", e, exc_info=True)

    def _bootstrap_poll_state(self):
        if not self._account:
            return
        try:
            _, orders, *_ = self._account.get_sales()
            self._seen_order_states = {
                order.id: self._normalize_status(order.status)
                for order in orders
            }
        except Exception as e:
            logger.warning("FunPay: failed to bootstrap orders state - %s", e)

        try:
            chats_iterable = self._account.request_chats()
            self._account.add_chats(chats_iterable)
            self._seen_chat_messages = {
                chat.id: getattr(chat, "node_msg_id", 0)
                for chat in chats_iterable
            }
        except Exception as e:
            logger.warning("FunPay: failed to bootstrap chats state - %s", e)

    def _poll_orders(self):
        if not self._account:
            return
        _, orders, *_ = self._account.get_sales()
        current_states = {}
        for order in orders:
            current_status = self._normalize_status(order.status)
            current_states[order.id] = current_status
            previous_status = self._seen_order_states.get(order.id)

            if previous_status is None:
                logger.info("FunPay fallback: new order #%s", order.id)
                self._emit(FunPayEventType.NEW_ORDER, fp_events.NewOrderEvent("fallback_poll", order))
            elif previous_status != current_status:
                logger.info("FunPay fallback: order status changed #%s -> %s", order.id, current_status)
                self._emit(FunPayEventType.ORDER_STATUS_CHANGED, fp_events.OrderStatusChangedEvent("fallback_poll", order))

        self._seen_order_states.update(current_states)

    def _poll_messages(self):
        if not self._account:
            return
        chats_iterable = self._account.request_chats()
        self._account.add_chats(chats_iterable)
        for chat in chats_iterable:
            chat_id = getattr(chat, "id", None)
            node_msg_id = getattr(chat, "node_msg_id", None)
            if chat_id is None or node_msg_id is None:
                continue

            previous_message_id = self._seen_chat_messages.get(chat_id)
            if previous_message_id is None:
                self._seen_chat_messages[chat_id] = node_msg_id
                continue
            if node_msg_id <= previous_message_id:
                continue

            messages = self._account.get_chat_history(
                chat_id,
                last_message_id=previous_message_id,
                interlocutor_username=getattr(chat, "name", None),
                from_id=previous_message_id + 1,
            )
            if not messages:
                self._seen_chat_messages[chat_id] = node_msg_id
                continue

            for message in messages:
                logger.info("FunPay fallback: new message chat=%s msg=%s", message.chat_id, message.id)
                self._emit(FunPayEventType.NEW_MESSAGE, fp_events.NewMessageEvent("fallback_poll", message))

            self._seen_chat_messages[chat_id] = max(
                node_msg_id,
                max(getattr(message, "id", 0) for message in messages),
            )

    def _adapt_sale(self, order) -> CompatSale:
        return CompatSale(
            order_id=order.id,
            id=order.id,
            status=self._normalize_status(order.status),
            price=order.price,
            currency=self._normalize_currency(order.currency),
            buyer_username=getattr(order, "buyer_username", ""),
            buyer_id=getattr(order, "buyer_id", None),
            chat_id=getattr(order, "chat_id", None),
            description=getattr(order, "description", "") or "",
            amount=getattr(order, "amount", None),
            subcategory_name=getattr(order, "subcategory_name", None),
            raw=order,
        )

    def _resolve_balance(self) -> float:
        if not self._account:
            return 0.0
        try:
            profile = self._account.get_user(self._account.id)
            lots = profile.get_lots()
            return self._resolve_balance_for_account(self._account, lots)
        except Exception:
            return 0.0

    @staticmethod
    def _resolve_balance_for_account(account: Account, lots: list) -> float:
        if not lots:
            return 0.0
        lot_id = None
        for lot in lots:
            current_id = getattr(lot, "id", None)
            if isinstance(current_id, int):
                lot_id = current_id
                break
            if isinstance(current_id, str) and current_id.isdigit():
                lot_id = int(current_id)
                break
        if lot_id is None:
            return 0.0
        try:
            balance = account.get_balance(lot_id)
        except Exception:
            return 0.0
        if getattr(balance, "total_rub", 0):
            return float(balance.total_rub)
        if getattr(balance, "total_usd", 0):
            return float(balance.total_usd)
        if getattr(balance, "total_eur", 0):
            return float(balance.total_eur)
        return 0.0

    @staticmethod
    def _normalize_status(status) -> str:
        if isinstance(status, OrderStatuses):
            mapping = {
                OrderStatuses.PAID: "paid",
                OrderStatuses.CLOSED: "closed",
                OrderStatuses.REFUNDED: "refunded",
            }
            return mapping.get(status, status.name.lower())

        name = getattr(status, "name", None)
        if isinstance(name, str):
            return name.lower()

        text = str(status).strip().lower()
        return {
            "0": "paid",
            "1": "closed",
            "2": "refunded",
        }.get(text, text)

    @staticmethod
    def _normalize_currency(currency) -> str:
        if isinstance(currency, Currency):
            mapping = {
                Currency.USD: "$",
                Currency.RUB: "₽",
                Currency.EUR: "€",
            }
            return mapping.get(currency, str(currency))

        name = getattr(currency, "name", None)
        if name == "USD":
            return "$"
        if name == "RUB":
            return "₽"
        if name == "EUR":
            return "€"
        return str(currency)

    def _extract_offer_id(self, order) -> str | int | None:
        for attr_name in ("offer_id", "lot_id"):
            value = getattr(order, attr_name, None)
            if value:
                return value

        html = getattr(order, "html", "") or ""
        if html:
            match = re.search(r"(?:offer\\?id=|offer=)(\\d+)", html)
            if match:
                return match.group(1)

        description = (getattr(order, "full_description", "") or "") + " " + (getattr(order, "short_description", "") or "")
        match = re.search(r"#(\\d{4,})", description)
        if match:
            return match.group(1)

        return None

    @property
    def is_initiated(self) -> bool:
        return self._initiated

    @property
    def is_running(self) -> bool:
        return bool(self._runner_thread and self._runner_thread.is_alive())
