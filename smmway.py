"""Smmway API client.
Primary path:
GET https://smmway.ru/api/v2
"""
from __future__ import annotations
import json
import logging
import time
import re
from http.cookies import SimpleCookie
from typing import List, Optional, Dict, Any, Union
import requests

logger = logging.getLogger("SMM.smmway")

_CYR_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}

class SmmwayAPI:
    SERVICE_TYPES = [
        "like", "subscribe", "comment", "like_to_comment",
        "dislike", "dislike_to_comment", "repost", "friend",
        "vote", "retweet", "follow", "favorite",
    ]
    ORDER_STATUSES = [
        "In progress", "Completed", "Awaiting",
        "Canceled", "Fail", "Partial",
    ]

    def __init__(self, api_key: str, api_url: str = "https://smmway.ru/api/v2", web_config: Optional[dict] = None):
        self.api_key = api_key
        self.api_url = api_url.rstrip("/")
        self.web_config = dict(web_config or {})
        self._last_request = 0
        self._min_interval = 1  # Задержка между запросами (сек)

    def _request(self, params: dict) -> dict:
        """Базовый метод для HTTP-запросов с rate limiting и логированием."""
        if self._web_enabled() and not self.web_config.get("xsrf_token"):
            self.refresh_xsrf_token()
        
        # Rate limiting
        elapsed = time.time() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

        params["key"] = self.api_key
        
        # 🔥 Логирование запроса для отладки
        logger.debug(f"Smmway API request: {params}")
        
        try:
            resp = requests.get(self.api_url, params=params, timeout=30)
            self._last_request = time.time()
            raw_text = resp.text
            data = resp.json()

            if isinstance(data, dict) and "error" in data:
                logger.error("Smmway API error: %s", data["error"])
                return {"success": False, "error": data["error"], "raw": data}

            return {"success": True, "data": data, "raw_text": raw_text}
        except requests.exceptions.Timeout:
            return {"success": False, "error": "Таймаут запроса"}
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "Ошибка подключения"}
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response: {e}, raw: {raw_text[:200]}")
            return {"success": False, "error": "Невалидный ответ API"}
        except Exception as e:
            logger.error(f"Unexpected error in _request: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
        
    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        """Безопасное преобразование значения в int."""
        if value is None:
            return default
        try:
            return int(float(value))  # float() обработает строки типа "123.0"
        except (TypeError, ValueError):
            return default
    
    @staticmethod
    def _extract_order_id(payload: Any) -> Optional[int]:
        """Гибкое извлечение order_id из ответа API (поддержка разных форматов)."""
        if payload is None:
            return None
        if isinstance(payload, (int, float)) and int(payload) > 0:
            return int(payload)
        if isinstance(payload, str):
            cleaned = payload.strip()
            if cleaned.isdigit():
                return int(cleaned)
            return None
        if isinstance(payload, list):
            for item in payload:
                found = SmmwayAPI._extract_order_id(item)
                if found:
                    return found
            return None
        if not isinstance(payload, dict):
            return None

        # 🔥 Сначала проверяем обёртку с ключом "data"
        if "data" in payload and isinstance(payload["data"], dict):
            found = SmmwayAPI._extract_order_id(payload["data"])
            if found:
                return found

        # Прямые кандидаты
        direct_candidates = [
            payload.get("order"),
            payload.get("order_id"),
            payload.get("id"),
            payload.get("result"),
        ]
        for value in direct_candidates:
            found = SmmwayAPI._extract_order_id(value)
            if found:
                return found

        # Поиск по ключам, содержащим "order" или заканчивающимся на "id"
        for key, value in payload.items():
            if "order" in str(key).lower() or str(key).lower().endswith("id"):
                found = SmmwayAPI._extract_order_id(value)
                if found:
                    return found
        return None

    def _web_enabled(self) -> bool:
        """Проверка, включён ли web fallback."""
        return bool(
            self.web_config.get("enabled")
            and self.web_config.get("xsrf_token")
            and self.web_config.get("cookies")
        )

    @staticmethod
    def _slugify_service_name(value: str) -> str:
        """Преобразование названия сервиса в URL-friendly slug."""
        text = re.sub(r"\[[^\]]*\]", "", str(value or "").lower())
        text = text.replace("ё", "е")
        converted = []
        for ch in text:
            if ch in _CYR_MAP:
                converted.append(_CYR_MAP[ch])
            else:
                converted.append(ch)
        text = "".join(converted)
        text = re.sub(r"[^a-z0-9]+", "-", text)
        text = re.sub(r"-{2,}", "-", text).strip("-")
        parts = [part for part in text.split("-") if part][:4]
        return "-".join(parts)

    def _web_headers(self, referer: str = "https://smmway.ru/") -> dict:
        """Заголовки для web fallback запросов."""
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Connection": "keep-alive",
            "Referer": referer,
            "User-Agent": str(
                self.web_config.get("user_agent")
                or "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:149.0) Gecko/20100101 Firefox/149.0"
            ),
        }

    def _web_cookies(self) -> dict:
        """Парсинг cookies из строки в dict."""
        return self._cookie_dict(self.web_config.get("cookies"))

    @staticmethod
    def _cookie_dict(cookie_value: Union[str, dict, None]) -> dict:
        """Конвертация cookies в dict."""
        if isinstance(cookie_value, dict):
            return {str(k): str(v) for k, v in cookie_value.items() if v}
        raw = str(cookie_value or "").strip()
        if not raw:
            return {}
        parsed = {}
        jar = SimpleCookie()
        jar.load(raw)
        for key, morsel in jar.items():
            parsed[key] = morsel.value
        return parsed

    def _create_order_via_web(self, service_id: int, link: str, quantity: int, extra_params: Optional[dict] = None) -> dict:
        """Fallback-метод создания заказа через web-интерфейс (POST)."""
        if not self._web_enabled():
            return {"success": False, "error": "web_fallback_not_configured"}

        session = requests.Session()
        session.headers.update({
            "User-Agent": str(self.web_config.get("user_agent") or "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"),
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://smmway.ru",
            "Referer": "https://smmway.ru/",
            "Connection": "keep-alive",
        })

        # 1. Загружаем куки из конфига
        cookies_raw = str(self.web_config.get("cookies") or "")
        if cookies_raw:
            for part in cookies_raw.split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    session.cookies.set(k, v.strip())

        # 2. Автоматически вытягиваем свежий XSRF-TOKEN
        try:
            login_page = session.get("https://smmway.ru/login", timeout=10)
            xsrf = session.cookies.get("XSRF-TOKEN")
            if not xsrf:
                match = re.search(r'<meta name="csrf-token" content="([^"]+)"', login_page.text)
                if match:
                    xsrf = match.group(1)
            if xsrf:
                session.headers["X-XSRF-TOKEN"] = xsrf
                session.cookies.set("XSRF-TOKEN", xsrf)
        except Exception as e:
            logger.warning(f"Не удалось получить свежий XSRF: {e}")

        # 3. Формируем payload
        payload = {
            "service": int(service_id),
            "link": str(link),
            "quantity": int(quantity),
        }
        if extra_params:
            payload.update({k: v for k, v in extra_params.items() if v is not None})

        # 4. Отправляем запрос
        url = str(self.web_config.get("orders_url") or "https://smmway.ru/api/orders").strip()
        try:
            resp = session.post(url, json=payload, timeout=30)
        except requests.exceptions.Timeout:
            return {"success": False, "error": "Таймаут web fallback Smmway"}
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "Ошибка подключения к web fallback Smmway"}

        # 5. Обрабатываем ответ
        if resp.status_code >= 400:
            error_msg = "Неизвестная ошибка"
            try:
                data = resp.json()
                error_msg = data.get("message") or data.get("error") or f"HTTP {resp.status_code}"
            except Exception:
                error_msg = f"HTTP {resp.status_code} | {resp.text[:200]}"
            return {"success": False, "error": error_msg}

        try:
            data = resp.json()
        except json.JSONDecodeError:
            data = {"raw": resp.text[:500]}

        order_id = self._extract_order_id(data)
        if not order_id:
            logger.warning("Web fallback не вернул ID заказа. Ответ: %s", data)
            return {"success": False, "error": "Web fallback не вернул ID заказа", "raw": data}
            
        return {"success": True, "order_id": int(order_id), "source": "web", "currency": "USD", "charge": 0, "start_count": 0, "remains": quantity}

    def get_balance(self) -> dict:
        """Получение баланса аккаунта."""
        r = self._request({"action": "balance"})
        if r["success"]:
            try:
                return {
                    "success": True,
                    "balance": float(r["data"].get("balance", 0)),
                    "currency": r["data"].get("currency", "USD"),
                }
            except (ValueError, TypeError, AttributeError):
                return {"success": True, "balance": 0, "currency": "USD"}
        return r

    def create_custom_order(self, service_id: int, link: str, quantity: int, **extra_params) -> dict:
        """Создание заказа с произвольными доп. параметрами."""
        return self.create_order(service_id, link, quantity, extra_params=extra_params)

    def create_vote_order(self, service_id: int, link: str, quantity: int, option_field: str, option_value: Any, **extra_params) -> dict:
        """Создание заказа на голоса с поддержкой web fallback."""
        field = str(option_field or "").strip()
        if not field:
            return {"success": False, "error": "Не указано имя параметра варианта голоса"}
        payload = {field: option_value}
        payload.update(extra_params or {})

        result = self.create_order(service_id, link, quantity, extra_params=payload)
        if result.get("success"):
            return result

        # 🔥 Fallback на web, если не получен ID заказа
        if self._web_enabled() and (
            "Не получен ID заказа" in str(result.get("error", ""))
            or "order id" in str(result.get("error", "")).lower()
        ):
            web_result = self._create_order_via_web(service_id, link, quantity, extra_params=payload)
            if web_result.get("success"):
                return web_result
            if web_result.get("error") == "web_fallback_not_configured":
                return {
                    "success": False,
                    "error": "Не получен ID заказа. Для этой услуги настройте web fallback",
                }
            return {
                "success": False,
                "error": f"{result.get('error')} | web fallback: {web_result.get('error')}",
            }
        return result

    def get_services(self) -> dict:
        """Получение списка услуг."""
        r = self._request({"action": "services"})
        if r["success"] and isinstance(r["data"], list):
            parsed = []
            for s in r["data"]:
                parsed.append({
                    "service_id": int(s.get("service", 0)),
                    "name": s.get("name", ""),
                    "type": s.get("type", ""),
                    "category": s.get("category", ""),
                    "rate": float(s.get("rate", 0)),
                    "min": int(s.get("min", 0)),
                    "max": int(s.get("max", 0)),
                    "refill": bool(s.get("refill", False)),
                    "cancel": bool(s.get("cancel", False)),
                    "platform": self.detect_platform(s.get("category", "")),
                })
            return {"success": True, "services": parsed, "count": len(parsed)}
        if r["success"]:
            return {"success": True, "services": [], "count": 0}
        return r

    def create_order(self, service_id: int, link: str, quantity: int, extra_params: Optional[dict] = None) -> dict:
        """Создание заказа с улучшенной обработкой ошибок и web fallback."""
        # 🔥 Гарантируем типы
        try:
            service_id = int(service_id)
            quantity = int(quantity)
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid params: service_id={service_id}, quantity={quantity}, error={e}")
            return {"success": False, "error": f"Неверный формат параметров: {e}"}

        payload = {
            "action": "add",
            "service": service_id,
            "link": link,
            "quantity": quantity,
        }
        if extra_params:
            payload.update({k: v for k, v in extra_params.items() if v is not None and v != ""})

        r = self._request(payload)

        # 🔥 ИЗВЛЕКАЕМ ORDER_ID ИЗ ОТВЕТА
        if r.get("success") and r.get("data"):
            order_id = self._extract_order_id(r["data"])
            if order_id:
                return {
                    "success": True,
                    "order_id": int(order_id),
                    "charge": float(r["data"].get("charge", 0)),
                    "start_count": self._safe_int(r["data"].get("start_count")),
                    "remains": self._safe_int(r["data"].get("remains")),
                    "currency": r["data"].get("currency", "USD"),
                }
            else:
                logger.warning("Smmway API returned success but no order_id in response: %s", r["data"])
                return {
                    "success": False,
                    "error": "Не получен ID заказа",
                    "raw": r["data"]
                }

        # 🔥 Если ошибка и есть web fallback — пробуем через POST
        if not r.get("success") and self._web_enabled():
            logger.info(f"API v2 failed, trying web fallback for service {service_id}")
            web_result = self._create_order_via_web(service_id, link, quantity, extra_params)
            if web_result.get("success"):
                return web_result

        return r
    
    def check_order_status(self, order_id: Union[int, str]) -> dict:
        """Проверка статуса одного заказа."""
        r = self._request({"action": "status", "order": order_id})
        if r["success"]:
            d = r["data"]
            return {
                "success": True,
                "order_id": str(order_id),
                "status": d.get("status", "Unknown"),
                "charge": float(d.get("charge", 0)),
                "start_count": self._safe_int(d.get("start_count")),
                "remains": self._safe_int(d.get("remains")),
                "currency": d.get("currency", "USD"),
            }
        return r

    def check_orders_status(self, order_ids: List[Union[int, str]]) -> dict:
        """Пакетная проверка статусов заказов."""
        ids_str = ",".join(str(i) for i in order_ids)
        r = self._request({"action": "status", "orders": ids_str})
        if r["success"] and isinstance(r["data"], dict):
            parsed = {}
            for oid, d in r["data"].items():
                if isinstance(d, str):
                    # Ошибка для конкретного заказа
                    parsed[oid] = {
                        "status": "Error",
                        "error": d,
                        "charge": 0,
                        "start_count": 0,
                        "remains": 0,
                        "currency": "USD",
                    }
                elif isinstance(d, dict):
                    parsed[oid] = {
                        "status": d.get("status", "Unknown"),
                        "charge": float(d.get("charge", 0)),
                        "start_count": self._safe_int(d.get("start_count")),
                        "remains": self._safe_int(d.get("remains")),
                        "currency": d.get("currency", "USD"),
                    }
            return {"success": True, "orders": parsed}
        return r

    def cancel_order(self, order_id: Union[int, str]) -> dict:
        """Отмена заказа."""
        r = self._request({"action": "cancel", "order": order_id})
        if r["success"]:
            ok = str(r["data"].get("ok", "")).lower() == "true"
            return {"success": ok, "order_id": str(order_id)}
        return r

    def refill_order(self, order_id: Union[int, str]) -> dict:
        """Запрос рефилла заказа."""
        r = self._request({"action": "refill", "order": order_id})
        if r["success"]:
            return {"success": True, "order_id": str(order_id), "refill_id": r["data"].get("refill", 0)}
        return r

    def test_connection(self) -> dict:
        """Тест подключения к API."""
        return self.get_balance()

    def get_service_by_id(self, service_id: int) -> Optional[dict]:
        """Получение информации об услуге по ID."""
        r = self.get_services()
        if r["success"]:
            for s in r["services"]:
                if s["service_id"] == service_id:
                    return s
        return None

    def refresh_xsrf_token(self) -> bool:
        """Автоматическое обновление XSRF-TOKEN для web fallback."""
        if not self._web_enabled():
            return False
    
        try:
            resp = requests.get(
                "https://smmway.ru/login",
                headers=self._web_headers(),
                cookies=self._cookie_dict(self.web_config.get("cookies")),
                timeout=15,
                allow_redirects=True
            )
        
            # 1. Пробуем взять из куки
            xsrf = resp.cookies.get("XSRF-TOKEN")
        
            # 2. Если нет — ищем в HTML
            if not xsrf:
                match = re.search(r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', resp.text)
                if match:
                    xsrf = match.group(1)
        
            # 3. Сохраняем в web_config
            if xsrf:
                self.web_config["xsrf_token"] = xsrf
                self.web_config["enabled"] = True
                # Опционально: сохраняем в config.json через cfg
                if 'cfg' in globals() and cfg:
                    cfg.set("smmway_web.xsrf_token", xsrf)
                logger.info("✅ XSRF-TOKEN обновлён: %s...", xsrf[:10])
                return True
            else:
                logger.warning("⚠️ Не удалось извлечь XSRF-TOKEN")
                return False
            
        except Exception as e:
            logger.error(f"❌ Ошибка обновления XSRF: {e}")
            return False

    def detect_platform(self, category: str) -> str:
        """Определение платформы по названию категории (с поддержкой кириллицы)."""
        cl = category.lower()
        for platform, kws in {
            "instagram": ["instagram", "инстаграм"],
            "telegram": ["telegram", "телеграм"],
            "youtube": ["youtube", "ютуб"],
            "tiktok": ["tiktok", "тикток"],
            "twitter": ["twitter", "твиттер", "x.com"],
            "vk": ["vk", "вконтакте"],
            "facebook": ["facebook", "фейсбук"],
            "discord": ["discord"],
            "spotify": ["spotify"],
            "twitch": ["twitch"],
        }.items():
            for kw in kws:
                if kw in cl:
                    return platform
        return "other"
