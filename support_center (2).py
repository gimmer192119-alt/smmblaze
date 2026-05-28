import html
import json
import logging
import re
from datetime import datetime
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


logger = logging.getLogger("SMM.support")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) "
    "Gecko/20100101 Firefox/148.0"
)


class FunPaySupportClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.base_url = "https://support.funpay.com"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.cfg.get("support_center.user_agent", DEFAULT_USER_AGENT),
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            }
        )
        self.session.verify = False

        proxy = self.cfg.get("funpay_proxy", "")
        if isinstance(proxy, dict) and proxy:
            self.session.proxies.update(proxy)
            self.session.trust_env = False
        elif isinstance(proxy, str) and proxy.strip():
            proxy_url = proxy.strip()
            if "://" not in proxy_url:
                proxy_url = f"http://{proxy_url}"
            self.session.proxies.update({"http": proxy_url, "https": proxy_url})
            self.session.trust_env = False
        else:
            self.session.trust_env = True

        self.timeout = max(10, int(self.cfg.get("support_center.timeout", 20) or 20))
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            redirect=0,
            status=2,
            backoff_factor=1,
            allowed_methods=frozenset({"GET", "POST"}),
            status_forcelist=[429, 500, 502, 503, 504],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def is_enabled(self) -> bool:
        return bool(
            self.cfg.get("support_center.enabled", False)
            and self.cfg.get("funpay_golden_key", "").strip()
        )

    def _apply_cookies(self):
        self.session.cookies.clear()
        golden_key = str(self.cfg.get("funpay_golden_key", "") or "").strip()
        php_sessid = str(self.cfg.get("support_center.php_sessid", "") or "").strip()
        if golden_key:
            self.session.cookies.set("golden_key", golden_key, domain=".funpay.com", path="/")
        if php_sessid:
            self.session.cookies.set("PHPSESSID", php_sessid, domain="support.funpay.com", path="/")

    @staticmethod
    def _is_redirect(response) -> bool:
        return response is not None and response.status_code in (301, 302, 303, 307, 308)

    def _follow_redirects(self, url: str, headers=None, max_hops: int = 10):
        current_url = url
        visited = {}
        response = None

        for _ in range(max_hops):
            visited[current_url] = visited.get(current_url, 0) + 1
            if visited[current_url] > 3:
                raise requests.TooManyRedirects(f"Redirect loop detected at {current_url}")
            response = self.session.get(
                current_url,
                timeout=self.timeout,
                allow_redirects=False,
                headers=headers,
            )
            if not self._is_redirect(response):
                return response
            location = response.headers.get("Location") or response.headers.get("location") or ""
            if not location:
                return response
            current_url = urljoin(current_url, location)

        raise requests.TooManyRedirects("Exceeded manual redirect limit")

    def _refresh_php_sessid(self, form_id: int) -> dict:
        golden_key = str(self.cfg.get("funpay_golden_key", "") or "").strip()
        if not golden_key:
            return {"success": False, "error": "golden_key not configured"}

        return_to = quote(f"/tickets/new/{int(form_id)}", safe="")
        sso_url = f"https://funpay.com/support/sso?return_to={return_to}"
        last_error = ""

        for _ in range(3):
            self.session.cookies.clear()
            self.session.cookies.set("golden_key", golden_key, domain=".funpay.com", path="/")
            try:
                response = self._follow_redirects(
                    sso_url,
                    headers={"Referer": "https://funpay.com/"},
                    max_hops=8,
                )
                final_url = response.url or ""
                if response.status_code != 200:
                    last_error = f"Support SSO failed: HTTP {response.status_code}"
                    continue
                if "account/login" in final_url:
                    last_error = "Support SSO redirected to FunPay login"
                    continue
                php_sessid = requests.utils.dict_from_cookiejar(self.session.cookies).get("PHPSESSID", "")
                if not php_sessid:
                    last_error = "Support PHPSESSID was not issued by SSO"
                    continue
                self.cfg.set("support_center.php_sessid", php_sessid)
                return {"success": True, "php_sessid": php_sessid}
            except requests.RequestException as exc:
                last_error = str(exc)
                logger.warning("Support SSO refresh failed: %s", exc)

        return {"success": False, "error": last_error or "Support SSO refresh failed"}

    @staticmethod
    def _parse_body_config(soup: BeautifulSoup) -> dict:
        body = soup.find("body")
        raw = body.get("data-app-config") if body else ""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def _get_form(self, form_id: int) -> dict:
        url = f"{self.base_url}/tickets/new/{int(form_id)}"
        response = None
        last_error = ""

        self._apply_cookies()
        try:
            response = self._follow_redirects(url, headers={"Referer": self.base_url + "/"}, max_hops=8)
        except requests.RequestException as exc:
            last_error = str(exc)
            logger.warning("Support form request with cached session failed: %s", exc)
            response = None

        if response is None or response.status_code != 200 or "account/login" in (response.url or ""):
            for _ in range(2):
                refreshed = self._refresh_php_sessid(form_id)
                if not refreshed.get("success"):
                    last_error = refreshed.get("error") or "Support session refresh failed"
                    continue
                self._apply_cookies()
                try:
                    response = self._follow_redirects(url, headers={"Referer": self.base_url + "/"}, max_hops=8)
                except requests.RequestException as exc:
                    last_error = str(exc)
                    logger.warning("Support form request after refresh failed: %s", exc)
                    response = None
                    continue
                if response is not None and response.status_code == 200 and "account/login" not in (response.url or ""):
                    break

        if response is None:
            return {"success": False, "error": last_error or "Support form request failed"}
        if response.status_code != 200:
            return {"success": False, "error": f"Support form request failed: HTTP {response.status_code}"}
        if "account/login" in (response.url or "") or "support/sso" in (response.url or ""):
            return {"success": False, "error": "Support session expired and automatic refresh failed"}

        soup = BeautifulSoup(response.text, "lxml")
        form = soup.find("form")
        body_config = self._parse_body_config(soup)

        token_input = soup.find("input", {"name": "ticket[_token]"})
        token = token_input.get("value", "").strip() if token_input else ""
        if not token:
            token = str(body_config.get("csrfToken") or "").strip()
        if not token:
            return {"success": False, "error": "Support token not found"}

        action = form.get("action") if form else f"/tickets/create/{int(form_id)}"
        create_url = urljoin(self.base_url, action)

        login_field_id = int(self.cfg.get("support_center.login_field_id", 1) or 1)
        login_name = f"ticket[fields][{login_field_id}]"
        login_input = soup.find(attrs={"name": login_name})
        login_value = (login_input.get("value") or "").strip() if login_input else ""

        return {
            "success": True,
            "token": token,
            "create_url": create_url,
            "referer": url,
            "login": login_value,
        }

    @staticmethod
    def _paragraphs_html(lines: list[str]) -> str:
        chunks = []
        for line in lines:
            line = str(line or "").strip()
            if not line:
                continue
            chunks.append(f"<p>{html.escape(line)}</p>")
        return "".join(chunks)

    @staticmethod
    def _format_completed_text(value) -> str:
        try:
            return datetime.fromisoformat(str(value)).strftime("%d.%m.%Y %H:%M")
        except Exception:
            return str(value or "не указано")

    def build_unconfirmed_batch_comment_html(self, items: list[tuple[dict, dict | None]]) -> str:
        login_value = str(self.cfg.get("support_center.funpay_login", "") or "").strip()
        lines = [
            "Здравствуйте.",
            "Прошу проверить несколько заказов: покупатели не подтвердили их более 24 часов после выполнения.",
        ]
        if login_value:
            lines.append(f"Ник на FunPay: {login_value}")
        lines.append("")
        lines.append(f"Количество заказов: {len(items)}")
        lines.append("")
        for index, (session, order) in enumerate(items, start=1):
            service_name = session.get("lot_name") or "Услуга"
            completed_at = session.get("updated_at")
            local_order_id = session.get("order_id") or "—"
            if order:
                service_name = order.get("service_name") or order.get("lot_name") or service_name
                completed_at = order.get("completed_at") or completed_at
                local_order_id = order.get("id") or local_order_id
            lines.append(
                f"{index}. FunPay #{session.get('funpay_order_id') or '—'} | "
                f"внутренний #{local_order_id} | "
                f"{session.get('buyer_username') or 'неизвестно'} | "
                f"{service_name} | "
                f"{self._format_completed_text(completed_at)}"
            )
        return self._paragraphs_html(lines)

    def build_unconfirmed_comment_html(self, session: dict, order: dict | None = None) -> str:
        service_name = session.get("lot_name") or "Услуга"
        completed_at = session.get("updated_at")
        local_order_id = session.get("order_id") or "—"
        if order:
            service_name = order.get("service_name") or order.get("lot_name") or service_name
            completed_at = order.get("completed_at") or completed_at
            local_order_id = order.get("id") or local_order_id

        try:
            completed_text = datetime.fromisoformat(str(completed_at)).strftime("%d.%m.%Y %H:%M")
        except Exception:
            completed_text = str(completed_at or "не указано")

        lines = [
            "Здравствуйте.",
            "Прошу проверить заказ: покупатель не подтвердил его более 24 часов после выполнения.",
            f"Ник на FunPay: {self.cfg.get('support_center.funpay_login', '') or ''}".strip(),
            f"Номер заказа FunPay: {session.get('funpay_order_id') or '—'}",
            f"Внутренний заказ: {local_order_id}",
            f"Услуга: {service_name}",
            f"Покупатель: {session.get('buyer_username') or 'неизвестно'}",
            f"Выполнен: {completed_text}",
        ]
        return self._paragraphs_html(lines)

    @staticmethod
    def _extract_ticket_info(data) -> tuple[str, str]:
        text = json.dumps(data, ensure_ascii=False) if isinstance(data, (dict, list)) else str(data or "")
        url_match = re.search(r"https://support\.funpay\.com/tickets/\d+", text)
        id_match = re.search(r"/tickets/(\d+)", text)
        return (
            url_match.group(0) if url_match else "",
            id_match.group(1) if id_match else "",
        )

    def create_unconfirmed_confirmation_ticket(self, session: dict, order: dict | None = None) -> dict:
        if not self.is_enabled():
            return {"success": False, "error": "Support center integration is disabled"}

        form_id = int(self.cfg.get("support_center.form_id", 1) or 1)
        form = self._get_form(form_id)
        if not form.get("success"):
            return form

        login_field_id = int(self.cfg.get("support_center.login_field_id", 1) or 1)
        order_field_id = int(self.cfg.get("support_center.order_field_id", 2) or 2)
        role_field_id = int(self.cfg.get("support_center.role_field_id", 3) or 3)
        subject_field_id = int(self.cfg.get("support_center.subject_field_id", 5) or 5)

        login_value = (
            str(self.cfg.get("support_center.funpay_login", "") or "").strip()
            or form.get("login", "").strip()
        )
        if not login_value:
            return {"success": False, "error": "FunPay login for support form is empty"}

        payload = {
            f"ticket[fields][{login_field_id}]": login_value,
            f"ticket[fields][{order_field_id}]": str(session.get("funpay_order_id") or "").strip(),
            f"ticket[fields][{role_field_id}]": str(self.cfg.get("support_center.role_value", 2) or 2),
            f"ticket[fields][{subject_field_id}]": str(self.cfg.get("support_center.subject_value", 201) or 201),
            "ticket[comment][body_html]": self.build_unconfirmed_comment_html(session, order),
            "ticket[comment][attachments]": "",
            "ticket[_token]": form["token"],
        }

        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": self.base_url,
            "Referer": form["referer"],
        }

        response = None
        redirect_to = ""
        for attempt in range(2):
            try:
                response = self.session.post(
                    form["create_url"],
                    data=payload,
                    headers=headers,
                    timeout=self.timeout,
                    allow_redirects=False,
                )
            except requests.RequestException as exc:
                logger.warning("Support ticket POST failed: %s", exc)
                response = None

            redirect_to = ""
            if self._is_redirect(response):
                redirect_to = response.headers.get("Location") or response.headers.get("location") or ""

            if (
                response is not None
                and response.status_code == 200
                and "account/login" not in (response.text or "")
                and "support/sso" not in (response.text or "")
            ):
                break

            if attempt == 1:
                break

            form = self._get_form(form_id)
            if not form.get("success"):
                return form
            payload["ticket[_token]"] = form["token"]
            headers["Referer"] = form["referer"]

        if response is None:
            return {"success": False, "error": "Support ticket request failed"}
        if self._is_redirect(response):
            return {
                "success": False,
                "error": f"Support ticket request redirected to {redirect_to or 'unknown location'}",
            }

        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text}

        text = json.dumps(data, ensure_ascii=False) if isinstance(data, (dict, list)) else str(data)
        if response.status_code != 200:
            if isinstance(data, dict):
                for error_key in ("error", "message", "errors"):
                    if data.get(error_key):
                        return {"success": False, "error": str(data.get(error_key))}
            preview = (response.text or "").strip()
            if preview:
                return {"success": False, "error": preview[:500]}
            return {"success": False, "error": f"Support ticket request failed: HTTP {response.status_code}"}
        if isinstance(data, dict):
            for error_key in ("error", "message", "errors"):
                if data.get(error_key):
                    return {"success": False, "error": str(data.get(error_key))}

        ticket_url, ticket_id = self._extract_ticket_info(data)
        return {
            "success": True,
            "ticket_id": ticket_id,
            "ticket_url": ticket_url,
            "response": data,
            "raw_text": text,
        }

    def create_unconfirmed_confirmation_ticket_batch(self, items: list[tuple[dict, dict | None]]) -> dict:
        if not items:
            return {"success": False, "error": "No sessions provided"}
        if len(items) == 1:
            session, order = items[0]
            return self.create_unconfirmed_confirmation_ticket(session, order)
        if not self.is_enabled():
            return {"success": False, "error": "Support center integration is disabled"}

        form_id = int(self.cfg.get("support_center.form_id", 1) or 1)
        form = self._get_form(form_id)
        if not form.get("success"):
            return form

        login_field_id = int(self.cfg.get("support_center.login_field_id", 1) or 1)
        order_field_id = int(self.cfg.get("support_center.order_field_id", 2) or 2)
        role_field_id = int(self.cfg.get("support_center.role_field_id", 3) or 3)
        subject_field_id = int(self.cfg.get("support_center.subject_field_id", 5) or 5)

        first_session, _ = items[0]
        login_value = (
            str(self.cfg.get("support_center.funpay_login", "") or "").strip()
            or form.get("login", "").strip()
        )
        if not login_value:
            return {"success": False, "error": "FunPay login for support form is empty"}

        payload = {
            f"ticket[fields][{login_field_id}]": login_value,
            f"ticket[fields][{order_field_id}]": str(first_session.get("funpay_order_id") or "").strip(),
            f"ticket[fields][{role_field_id}]": str(self.cfg.get("support_center.role_value", 2) or 2),
            f"ticket[fields][{subject_field_id}]": str(self.cfg.get("support_center.subject_value", 201) or 201),
            "ticket[comment][body_html]": self.build_unconfirmed_batch_comment_html(items),
            "ticket[comment][attachments]": "",
            "ticket[_token]": form["token"],
        }

        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": self.base_url,
            "Referer": form["referer"],
        }

        response = None
        redirect_to = ""
        for attempt in range(2):
            try:
                response = self.session.post(
                    form["create_url"],
                    data=payload,
                    headers=headers,
                    timeout=self.timeout,
                    allow_redirects=False,
                )
            except requests.RequestException as exc:
                logger.warning("Support batch ticket POST failed: %s", exc)
                response = None

            redirect_to = ""
            if self._is_redirect(response):
                redirect_to = response.headers.get("Location") or response.headers.get("location") or ""

            if (
                response is not None
                and response.status_code == 200
                and "account/login" not in (response.text or "")
                and "support/sso" not in (response.text or "")
            ):
                break

            if attempt == 1:
                break

            form = self._get_form(form_id)
            if not form.get("success"):
                return form
            payload["ticket[_token]"] = form["token"]
            headers["Referer"] = form["referer"]

        if response is None:
            return {"success": False, "error": "Support batch ticket request failed"}
        if self._is_redirect(response):
            return {
                "success": False,
                "error": f"Support batch ticket request redirected to {redirect_to or 'unknown location'}",
            }

        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text}

        text = json.dumps(data, ensure_ascii=False) if isinstance(data, (dict, list)) else str(data)
        if response.status_code != 200:
            if isinstance(data, dict):
                for error_key in ("error", "message", "errors"):
                    if data.get(error_key):
                        return {"success": False, "error": str(data.get(error_key))}
            preview = (response.text or "").strip()
            if preview:
                return {"success": False, "error": preview[:500]}
            return {"success": False, "error": f"Support batch ticket request failed: HTTP {response.status_code}"}
        if isinstance(data, dict):
            for error_key in ("error", "message", "errors"):
                if data.get(error_key):
                    return {"success": False, "error": str(data.get(error_key))}

        ticket_url, ticket_id = self._extract_ticket_info(data)
        return {
            "success": True,
            "ticket_id": ticket_id,
            "ticket_url": ticket_url,
            "response": data,
            "raw_text": text,
        }
