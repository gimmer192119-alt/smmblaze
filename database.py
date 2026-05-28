"""
SQLite база данных SMM Auto Bot
"""
import sqlite3
import os
import shutil
import json
import csv
from datetime import datetime, timedelta
from config import DB_PATH, BACKUP_DIR, EXPORT_DIR


class Database:
    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._create_tables()

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _create_tables(self):
        conn = self._conn()
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            api_service_id INTEGER DEFAULT 0,
            api_service_name TEXT DEFAULT '',
            service_type TEXT DEFAULT '',
            order_mode TEXT DEFAULT '',
            split_enabled INTEGER DEFAULT 0,
            vote_answer_number TEXT DEFAULT '',
            review_bonus_enabled INTEGER DEFAULT 0,
            review_bonus_service_id INTEGER DEFAULT 0,
            review_bonus_service_name TEXT DEFAULT '',
            review_bonus_service_type TEXT DEFAULT '',
            review_bonus_quantity INTEGER DEFAULT 0,
            api_provider TEXT DEFAULT 'twiboost',
            api_rate REAL DEFAULT 0,
            price REAL DEFAULT 0,
            markup REAL DEFAULT 30,
            min_quantity INTEGER DEFAULT 100,
            max_quantity INTEGER DEFAULT 10000,
            category TEXT DEFAULT '',
            platform TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            total_orders INTEGER DEFAULT 0,
            total_revenue REAL DEFAULT 0,
            total_profit REAL DEFAULT 0,
            funpay_lot_id TEXT DEFAULT '',
            funpay_lot_name TEXT DEFAULT '',
            quantity_per_order INTEGER DEFAULT 1,
            price_mode TEXT DEFAULT 'fixed',
            price_input REAL DEFAULT 0,
            price_per_unit REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_order_id TEXT DEFAULT '',
            lot_id INTEGER DEFAULT 0,
            lot_name TEXT DEFAULT '',
            api_provider TEXT DEFAULT 'twiboost',
            api_service_id INTEGER DEFAULT 0,
            service_name TEXT DEFAULT '',
            buyer_username TEXT DEFAULT '',
            link TEXT DEFAULT '',
            quantity INTEGER DEFAULT 0,
            cost_price REAL DEFAULT 0,
            sell_price REAL DEFAULT 0,
            profit REAL DEFAULT 0,
            status TEXT DEFAULT 'pending',
            api_status TEXT DEFAULT '',
            api_charge REAL DEFAULT 0,
            api_start_count INTEGER DEFAULT 0,
            api_remains INTEGER DEFAULT 0,
            currency TEXT DEFAULT 'USD',
            error_message TEXT DEFAULT '',
            refill_count INTEGER DEFAULT 0,
            promo_code TEXT DEFAULT '',
            promo_discount REAL DEFAULT 0,
            split_index INTEGER DEFAULT 0,
            split_total INTEGER DEFAULT 0,
            funpay_order_id TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS funpay_sessions (
            funpay_order_id TEXT PRIMARY KEY,
            chat_id TEXT DEFAULT '',
            buyer_username TEXT DEFAULT '',
            buyer_id INTEGER DEFAULT 0,
            lot_id INTEGER DEFAULT 0,
            lot_name TEXT DEFAULT '',
            price REAL DEFAULT 0,
            currency TEXT DEFAULT 'RUB',
            pending_link TEXT DEFAULT '',
            pending_qty INTEGER DEFAULT 0,
            pending_answer_number TEXT DEFAULT '',
            pending_reaction TEXT DEFAULT '',
            pending_comments TEXT DEFAULT '',
            pending_split_parts INTEGER DEFAULT 0,
            pending_split_json TEXT DEFAULT '',
            review_bonus_state TEXT DEFAULT '',
            review_bonus_link TEXT DEFAULT '',
            review_bonus_order_id INTEGER DEFAULT 0,
            promo_code TEXT DEFAULT '',
            promo_value REAL DEFAULT 0,
            state TEXT DEFAULT 'awaiting_link',
            order_id INTEGER DEFAULT 0,
            buyer_confirmed INTEGER DEFAULT 0,
            support_ticket_due_at TEXT DEFAULT '',
            support_ticket_sent INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS user_states (
            user_id INTEGER PRIMARY KEY,
            state TEXT DEFAULT '',
            data TEXT DEFAULT '{}',
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT DEFAULT 'twiboost',
            service_id INTEGER NOT NULL,
            name TEXT DEFAULT '',
            type TEXT DEFAULT '',
            category TEXT DEFAULT '',
            rate REAL DEFAULT 0,
            min_order INTEGER DEFAULT 0,
            max_order INTEGER DEFAULT 0,
            refill INTEGER DEFAULT 0,
            cancel INTEGER DEFAULT 0,
            platform TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(provider, service_id)
        );

        CREATE TABLE IF NOT EXISTS promo_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            discount_type TEXT DEFAULT 'percent',
            discount_value REAL DEFAULT 0,
            max_uses INTEGER DEFAULT 1,
            used_count INTEGER DEFAULT 0,
            min_order_amount REAL DEFAULT 0,
            max_order_amount REAL DEFAULT 0,
            for_username TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            valid_until TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS upsells (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            lot_id INTEGER DEFAULT 0,
            discount_value REAL DEFAULT 10,
            min_order_amount REAL DEFAULT 0,
            max_order_amount REAL DEFAULT 0,
            promo_apply_min_amount REAL DEFAULT 0,
            promo_apply_max_amount REAL DEFAULT 0,
            promo_max_uses INTEGER DEFAULT 1,
            bonus_text TEXT DEFAULT '',
            promo_prefix TEXT DEFAULT 'BONUS',
            promo_duration_days INTEGER DEFAULT 7,
            is_active INTEGER DEFAULT 1,
            times_shown INTEGER DEFAULT 0,
            times_used INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS message_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            text TEXT DEFAULT '',
            msg_type TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS daily_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            total_orders INTEGER DEFAULT 0,
            completed_orders INTEGER DEFAULT 0,
            failed_orders INTEGER DEFAULT 0,
            cancelled_orders INTEGER DEFAULT 0,
            total_revenue REAL DEFAULT 0,
            total_cost REAL DEFAULT 0,
            total_profit REAL DEFAULT 0,
            promos_used INTEGER DEFAULT 0,
            upsells_shown INTEGER DEFAULT 0,
            upsells_converted INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level TEXT DEFAULT 'INFO',
            module TEXT DEFAULT '',
            message TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS mirror_users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id INTEGER UNIQUE NOT NULL,
            username TEXT DEFAULT '',
            full_name TEXT DEFAULT '',
            mirror_name TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            share_percent REAL DEFAULT 5,
            bot_token TEXT DEFAULT '',
            funpay_golden_key TEXT DEFAULT '',
            twiboost_api_key TEXT DEFAULT '',
            settings_json TEXT DEFAULT '{}',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS mirror_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mirror_user_id INTEGER NOT NULL,
            report_month TEXT NOT NULL,
            revenue REAL DEFAULT 0,
            share_percent REAL DEFAULT 5,
            amount_due REAL DEFAULT 0,
            status TEXT DEFAULT 'pending',
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(mirror_user_id, report_month),
            FOREIGN KEY(mirror_user_id) REFERENCES mirror_users(id) ON DELETE CASCADE
        );
        """)
        conn.commit()
        self._ensure_lot_columns(conn)
        self._ensure_order_columns(conn)
        self._ensure_funpay_session_columns(conn)
        self._ensure_promo_columns(conn)
        self._ensure_upsell_columns(conn)
        self._ensure_mirror_columns(conn)
        conn.commit()
        conn.close()

    def _ensure_lot_columns(self, conn):
        """Добавляет недостающие колонки для лотов (миграция)."""
        cols = {row[1] for row in conn.execute("PRAGMA table_info(lots)").fetchall()}

        def add_column(name, ddl):
            if name not in cols:
                conn.execute(f"ALTER TABLE lots ADD COLUMN {ddl}")
                cols.add(name)

        add_column("funpay_lot_id", "funpay_lot_id TEXT DEFAULT ''")
        add_column("funpay_lot_name", "funpay_lot_name TEXT DEFAULT ''")
        add_column("funpay_category_id", "funpay_category_id INTEGER DEFAULT 0")
        add_column("quantity_per_order", "quantity_per_order INTEGER DEFAULT 1")
        add_column("price_mode", "price_mode TEXT DEFAULT 'fixed'")
        add_column("price_input", "price_input REAL DEFAULT 0")
        add_column("price_per_unit", "price_per_unit REAL DEFAULT 0")
        add_column("funpay_category_name", "funpay_category_name TEXT DEFAULT ''")
        add_column("service_type", "service_type TEXT DEFAULT ''")
        add_column("order_mode", "order_mode TEXT DEFAULT ''")
        add_column("split_enabled", "split_enabled INTEGER DEFAULT 0")
        add_column("vote_answer_number", "vote_answer_number TEXT DEFAULT ''")
        add_column("review_bonus_enabled", "review_bonus_enabled INTEGER DEFAULT 0")
        add_column("review_bonus_service_id", "review_bonus_service_id INTEGER DEFAULT 0")
        add_column("review_bonus_service_name", "review_bonus_service_name TEXT DEFAULT ''")
        add_column("review_bonus_service_type", "review_bonus_service_type TEXT DEFAULT ''")
        add_column("review_bonus_quantity", "review_bonus_quantity INTEGER DEFAULT 0")
        conn.execute(
            "UPDATE lots SET order_mode = CASE "
            "WHEN TRIM(COALESCE(order_mode, '')) != '' THEN order_mode "
            "WHEN LOWER(COALESCE(service_type, '')) = 'vote' THEN 'vote' "
            "ELSE 'normal' END"
        )

        # Переносим существующие значения цены в новую колонку при необходимости
        conn.execute(
            "UPDATE lots SET price_per_unit = CASE WHEN price_per_unit = 0 THEN price / 1000.0 ELSE price_per_unit END"
        )

    def _ensure_order_columns(self, conn):
        cols = {row[1] for row in conn.execute("PRAGMA table_info(orders)").fetchall()}

        def add_column(name, ddl):
            if name not in cols:
                conn.execute(f"ALTER TABLE orders ADD COLUMN {ddl}")
                cols.add(name)

        add_column("buyer_username", "buyer_username TEXT DEFAULT ''")
        add_column("funpay_order_id", "funpay_order_id TEXT DEFAULT ''")
        add_column("review_bonus_sent", "review_bonus_sent INTEGER DEFAULT 0")
        add_column("last_review_stars", "last_review_stars INTEGER DEFAULT 0")
        add_column("split_index", "split_index INTEGER DEFAULT 0")
        add_column("split_total", "split_total INTEGER DEFAULT 0")

    def _ensure_funpay_session_columns(self, conn):
        cols = {row[1] for row in conn.execute("PRAGMA table_info(funpay_sessions)").fetchall()}

        def add_column(name, ddl):
            if name not in cols:
                conn.execute(f"ALTER TABLE funpay_sessions ADD COLUMN {ddl}")
                cols.add(name)

        add_column("promo_code", "promo_code TEXT DEFAULT ''")
        add_column("promo_value", "promo_value REAL DEFAULT 0")
        add_column("pending_answer_number", "pending_answer_number TEXT DEFAULT ''")
        add_column("pending_reaction", "pending_reaction TEXT DEFAULT ''")
        add_column("pending_comments", "pending_comments TEXT DEFAULT ''")
        add_column("pending_split_parts", "pending_split_parts INTEGER DEFAULT 0")
        add_column("pending_split_json", "pending_split_json TEXT DEFAULT ''")
        add_column("review_bonus_state", "review_bonus_state TEXT DEFAULT ''")
        add_column("review_bonus_link", "review_bonus_link TEXT DEFAULT ''")
        add_column("review_bonus_order_id", "review_bonus_order_id INTEGER DEFAULT 0")
        add_column("buyer_confirmed", "buyer_confirmed INTEGER DEFAULT 0")
        conn.execute(
            "UPDATE funpay_sessions "
            "SET buyer_confirmed = CASE "
            "WHEN buyer_confirmed = 0 AND support_ticket_due_at = '' AND support_ticket_sent = 1 THEN 1 "
            "ELSE buyer_confirmed END"
        )

    def _ensure_upsell_columns(self, conn):
        cols = {row[1] for row in conn.execute("PRAGMA table_info(upsells)").fetchall()}

        def add_column(name, ddl):
            if name not in cols:
                conn.execute(f"ALTER TABLE upsells ADD COLUMN {ddl}")
                cols.add(name)

        add_column("min_order_amount", "min_order_amount REAL DEFAULT 0")
        add_column("max_order_amount", "max_order_amount REAL DEFAULT 0")
        add_column("promo_apply_min_amount", "promo_apply_min_amount REAL DEFAULT 0")
        add_column("promo_apply_max_amount", "promo_apply_max_amount REAL DEFAULT 0")
        add_column("promo_max_uses", "promo_max_uses INTEGER DEFAULT 1")

    def _ensure_promo_columns(self, conn):
        cols = {row[1] for row in conn.execute("PRAGMA table_info(promo_codes)").fetchall()}

        def add_column(name, ddl):
            if name not in cols:
                conn.execute(f"ALTER TABLE promo_codes ADD COLUMN {ddl}")
                cols.add(name)

        add_column("min_order_amount", "min_order_amount REAL DEFAULT 0")
        add_column("max_order_amount", "max_order_amount REAL DEFAULT 0")

    def _ensure_mirror_columns(self, conn):
        mirror_cols = {row[1] for row in conn.execute("PRAGMA table_info(mirror_users)").fetchall()}
        report_cols = {row[1] for row in conn.execute("PRAGMA table_info(mirror_reports)").fetchall()}

        def add_mirror_column(name, ddl):
            if name not in mirror_cols:
                conn.execute(f"ALTER TABLE mirror_users ADD COLUMN {ddl}")
                mirror_cols.add(name)

        def add_report_column(name, ddl):
            if name not in report_cols:
                conn.execute(f"ALTER TABLE mirror_reports ADD COLUMN {ddl}")
                report_cols.add(name)

        add_mirror_column("mirror_name", "mirror_name TEXT DEFAULT ''")
        add_mirror_column("status", "status TEXT DEFAULT 'pending'")
        add_mirror_column("share_percent", "share_percent REAL DEFAULT 5")
        add_mirror_column("bot_token", "bot_token TEXT DEFAULT ''")
        add_mirror_column("funpay_golden_key", "funpay_golden_key TEXT DEFAULT ''")
        add_mirror_column("twiboost_api_key", "twiboost_api_key TEXT DEFAULT ''")
        add_mirror_column("settings_json", "settings_json TEXT DEFAULT '{}'")
        add_mirror_column("notes", "notes TEXT DEFAULT ''")

        add_report_column("share_percent", "share_percent REAL DEFAULT 5")
        add_report_column("amount_due", "amount_due REAL DEFAULT 0")
        add_report_column("status", "status TEXT DEFAULT 'pending'")
        add_report_column("note", "note TEXT DEFAULT ''")

    # ==================== ЛОТЫ ====================

    def add_lot(self, **kwargs):
        conn = self._conn()
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join(["?"] * len(kwargs))
        conn.execute(f"INSERT INTO lots ({cols}) VALUES ({placeholders})", list(kwargs.values()))
        lid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()
        return lid

    def get_lot(self, lot_id):
        conn = self._conn()
        row = conn.execute("SELECT * FROM lots WHERE id = ?", (lot_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_lots(self, active_only=False):
        conn = self._conn()
        q = "SELECT * FROM lots"
        if active_only:
            q += " WHERE is_active = 1"
        q += " ORDER BY id DESC"
        rows = conn.execute(q).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_all_lots(self):
        """Alias for backwards compatibility (includes inactive lots)."""
        return self.get_lots(active_only=False)

    def update_lot(self, lot_id, **kwargs):
        kwargs["updated_at"] = datetime.now().isoformat()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        conn = self._conn()
        conn.execute(f"UPDATE lots SET {sets} WHERE id = ?", list(kwargs.values()) + [lot_id])
        conn.commit()
        conn.close()

    def delete_lot(self, lot_id):
        conn = self._conn()
        conn.execute("DELETE FROM lots WHERE id = ?", (lot_id,))
        conn.commit()
        conn.close()

    def get_lots_count(self):
        conn = self._conn()
        c = conn.execute("SELECT COUNT(*) FROM lots WHERE is_active = 1").fetchone()[0]
        conn.close()
        return c

    # ==================== MIRRORS ====================

    def upsert_mirror_user(self, telegram_user_id, **kwargs):
        kwargs["updated_at"] = datetime.now().isoformat()
        columns = ["telegram_user_id"] + list(kwargs.keys())
        values = [telegram_user_id] + list(kwargs.values())
        updates = ", ".join(f"{col}=excluded.{col}" for col in kwargs.keys())
        conn = self._conn()
        conn.execute(
            f"INSERT INTO mirror_users ({', '.join(columns)}) VALUES ({', '.join(['?'] * len(columns))}) "
            f"ON CONFLICT(telegram_user_id) DO UPDATE SET {updates}",
            values
        )
        conn.commit()
        conn.close()

    def get_mirror_user(self, telegram_user_id):
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM mirror_users WHERE telegram_user_id = ?",
            (telegram_user_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_mirror_user_by_id(self, mirror_id):
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM mirror_users WHERE id = ?",
            (mirror_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_mirror_users(self, status=None):
        conn = self._conn()
        q = "SELECT * FROM mirror_users"
        params = []
        if status:
            q += " WHERE status = ?"
            params.append(status)
        q += " ORDER BY id DESC"
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def update_mirror_user(self, mirror_id, **kwargs):
        if not kwargs:
            return
        kwargs["updated_at"] = datetime.now().isoformat()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        conn = self._conn()
        conn.execute(f"UPDATE mirror_users SET {sets} WHERE id = ?", list(kwargs.values()) + [mirror_id])
        conn.commit()
        conn.close()

    def delete_mirror_user(self, mirror_id):
        conn = self._conn()
        conn.execute("DELETE FROM mirror_users WHERE id = ?", (mirror_id,))
        conn.commit()
        conn.close()

    def upsert_mirror_report(self, mirror_user_id, report_month, revenue, share_percent, amount_due, status="pending", note=""):
        now_iso = datetime.now().isoformat()
        conn = self._conn()
        conn.execute(
            "INSERT INTO mirror_reports (mirror_user_id, report_month, revenue, share_percent, amount_due, status, note, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(mirror_user_id, report_month) DO UPDATE SET "
            "revenue = excluded.revenue, "
            "share_percent = excluded.share_percent, "
            "amount_due = excluded.amount_due, "
            "status = excluded.status, "
            "note = excluded.note, "
            "updated_at = excluded.updated_at",
            (mirror_user_id, report_month, revenue, share_percent, amount_due, status, note, now_iso)
        )
        conn.commit()
        conn.close()

    def get_mirror_report(self, mirror_user_id, report_month):
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM mirror_reports WHERE mirror_user_id = ? AND report_month = ?",
            (mirror_user_id, report_month)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_latest_mirror_report(self, mirror_user_id):
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM mirror_reports WHERE mirror_user_id = ? ORDER BY report_month DESC, id DESC LIMIT 1",
            (mirror_user_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_mirror_reports(self, mirror_user_id=None, limit=24):
        conn = self._conn()
        q = (
            "SELECT mr.*, mu.username, mu.full_name, mu.mirror_name, mu.telegram_user_id "
            "FROM mirror_reports mr "
            "JOIN mirror_users mu ON mu.id = mr.mirror_user_id"
        )
        params = []
        if mirror_user_id:
            q += " WHERE mr.mirror_user_id = ?"
            params.append(mirror_user_id)
        q += " ORDER BY mr.report_month DESC, mr.id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ==================== ЗАКАЗЫ ====================

    def add_order(self, **kwargs):
        conn = self._conn()
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join(["?"] * len(kwargs))
        conn.execute(f"INSERT INTO orders ({cols}) VALUES ({placeholders})", list(kwargs.values()))
        oid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()
        return oid

    def get_order(self, order_id):
        conn = self._conn()
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_order_by_funpay_order_id(self, funpay_order_id):
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM orders WHERE funpay_order_id = ? ORDER BY id DESC LIMIT 1",
            (funpay_order_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_orders_by_funpay_order_id(self, funpay_order_id):
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM orders WHERE funpay_order_id = ? ORDER BY split_index ASC, id ASC",
            (funpay_order_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_orders(self, status=None, limit=50, offset=0):
        conn = self._conn()
        q = "SELECT * FROM orders"
        params = []
        if status:
            q += " WHERE status = ?"
            params.append(status)
        q += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_active_orders(self):
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM orders WHERE status IN ('pending', 'processing', 'in_progress', 'partial') ORDER BY id"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_lot_stats(self, lot_id):
        conn = self._conn()
        row = conn.execute(
            "SELECT "
            "COUNT(*) as total_orders, "
            "COALESCE(SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END), 0) as completed_orders, "
            "COALESCE(SUM(sell_price), 0) as total_revenue, "
            "COALESCE(SUM(cost_price), 0) as total_cost, "
            "COALESCE(SUM(profit), 0) as total_profit "
            "FROM orders WHERE lot_id = ?",
            (lot_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else {
            "total_orders": 0,
            "completed_orders": 0,
            "total_revenue": 0,
            "total_cost": 0,
            "total_profit": 0,
        }

    def get_most_popular_lot(self, days=None):
        conn = self._conn()
        q = (
            "SELECT lot_id, lot_name, COUNT(*) as total_orders, "
            "COALESCE(SUM(sell_price), 0) as total_revenue, "
            "COALESCE(SUM(profit), 0) as total_profit "
            "FROM orders WHERE lot_id > 0"
        )
        params = []
        if days:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            q += " AND date(created_at) >= ?"
            params.append(cutoff)
        q += " GROUP BY lot_id, lot_name ORDER BY total_orders DESC, total_revenue DESC LIMIT 1"
        row = conn.execute(q, params).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_orders_for_review_bonus(self, limit=50):
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM orders "
            "WHERE funpay_order_id != '' AND review_bonus_sent = 0 "
            "ORDER BY updated_at DESC, id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_orders_for_review_sync(self, limit=100):
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM orders "
            "WHERE funpay_order_id != '' "
            "ORDER BY updated_at DESC, id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def update_order(self, order_id, **kwargs):
        kwargs["updated_at"] = datetime.now().isoformat()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        conn = self._conn()
        conn.execute(f"UPDATE orders SET {sets} WHERE id = ?", list(kwargs.values()) + [order_id])
        conn.commit()
        conn.close()

    def get_orders_count_today(self):
        conn = self._conn()
        today = datetime.now().strftime("%Y-%m-%d")
        c = conn.execute("SELECT COUNT(*) FROM orders WHERE date(created_at) = ?", (today,)).fetchone()[0]
        conn.close()
        return c

    def get_orders_count(self):
        conn = self._conn()
        c = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        conn.close()
        return c

    # ==================== СЕРВИСЫ ====================

    def get_orders_financial_summary(self, month_key=None, exclude_statuses=None):
        conn = self._conn()
        q = (
            "SELECT "
            "COUNT(*) as total_orders, "
            "COALESCE(SUM(sell_price), 0) as total_revenue, "
            "COALESCE(SUM(cost_price), 0) as total_cost, "
            "COALESCE(SUM(profit), 0) as total_profit "
            "FROM orders WHERE 1=1"
        )
        params = []
        if month_key:
            q += " AND substr(COALESCE(created_at, ''), 1, 7) = ?"
            params.append(str(month_key))
        if exclude_statuses:
            placeholders = ", ".join(["?"] * len(exclude_statuses))
            q += f" AND COALESCE(status, '') NOT IN ({placeholders})"
            params.extend([str(x) for x in exclude_statuses])
        row = conn.execute(q, params).fetchone()
        conn.close()
        return dict(row) if row else {
            "total_orders": 0,
            "total_revenue": 0,
            "total_cost": 0,
            "total_profit": 0,
        }

    def upsert_service(self, provider, service_id, **kwargs):
        conn = self._conn()
        existing = conn.execute(
            "SELECT id FROM services WHERE provider = ? AND service_id = ?",
            (provider, service_id)
        ).fetchone()
        if existing:
            sets = ", ".join(f"{k} = ?" for k in kwargs)
            conn.execute(
                f"UPDATE services SET {sets}, updated_at = datetime('now') WHERE provider = ? AND service_id = ?",
                list(kwargs.values()) + [provider, service_id]
            )
        else:
            kwargs["provider"] = provider
            kwargs["service_id"] = service_id
            cols = ", ".join(kwargs.keys())
            placeholders = ", ".join(["?"] * len(kwargs))
            conn.execute(f"INSERT INTO services ({cols}) VALUES ({placeholders})", list(kwargs.values()))
        conn.commit()
        conn.close()

    def get_services(self, provider=None, category=None, limit=50, offset=0):
        conn = self._conn()
        q = "SELECT * FROM services WHERE 1=1"
        params = []
        if provider:
            q += " AND provider = ?"
            params.append(provider)
        if category:
            q += " AND category = ?"
            params.append(category)
        q += " ORDER BY service_id LIMIT ? OFFSET ?"
        params += [limit, offset]
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_service(self, provider, service_id):
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM services WHERE provider = ? AND service_id = ?",
            (provider, service_id)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_services_count(self, provider=None):
        conn = self._conn()
        if provider:
            c = conn.execute("SELECT COUNT(*) FROM services WHERE provider = ?", (provider,)).fetchone()[0]
        else:
            c = conn.execute("SELECT COUNT(*) FROM services").fetchone()[0]
        conn.close()
        return c

    def get_service_categories(self, provider="twiboost"):
        conn = self._conn()
        rows = conn.execute(
            "SELECT DISTINCT category FROM services WHERE provider = ? ORDER BY category",
            (provider,)
        ).fetchall()
        conn.close()
        return [r["category"] for r in rows]

    # ==================== ПРОМОКОДЫ ====================

    def add_promo(self, **kwargs):
        conn = self._conn()
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join(["?"] * len(kwargs))
        conn.execute(f"INSERT INTO promo_codes ({cols}) VALUES ({placeholders})", list(kwargs.values()))
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()
        return pid

    def get_promo(self, code):
        conn = self._conn()
        row = conn.execute("SELECT * FROM promo_codes WHERE code = ?", (code.upper(),)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_promos(self, active_only=False, limit=50):
        conn = self._conn()
        q = "SELECT * FROM promo_codes"
        if active_only:
            q += " WHERE is_active = 1"
        q += f" ORDER BY id DESC LIMIT {limit}"
        rows = conn.execute(q).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def update_promo(self, code, **kwargs):
        if not kwargs:
            return
        conn = self._conn()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        conn.execute(f"UPDATE promo_codes SET {sets} WHERE code = ?", list(kwargs.values()) + [code.upper()])
        conn.commit()
        conn.close()

    def use_promo(self, code):
        conn = self._conn()
        cur = conn.execute(
            "UPDATE promo_codes "
            "SET used_count = used_count + 1 "
            "WHERE code = ? "
            "AND is_active = 1 "
            "AND (max_uses <= 0 OR used_count < max_uses) "
            "AND (valid_until = '' OR valid_until >= ?)",
            (code.upper(), datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
        return cur.rowcount > 0

    def rollback_promo_use(self, code):
        conn = self._conn()
        conn.execute(
            "UPDATE promo_codes SET used_count = CASE WHEN used_count > 0 THEN used_count - 1 ELSE 0 END WHERE code = ?",
            (code.upper(),)
        )
        conn.commit()
        conn.close()

    def delete_promo(self, promo_id):
        conn = self._conn()
        conn.execute("DELETE FROM promo_codes WHERE id = ?", (promo_id,))
        conn.commit()
        conn.close()

    def deactivate_expired_promos(self):
        conn = self._conn()
        now = datetime.now().isoformat()
        conn.execute(
            "UPDATE promo_codes SET is_active = 0 WHERE valid_until != '' AND valid_until < ? AND is_active = 1",
            (now,)
        )
        conn.commit()
        conn.close()

    # ==================== ДОПЫ (UPSELLS) ====================

    def add_upsell(self, **kwargs):
        conn = self._conn()
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join(["?"] * len(kwargs))
        conn.execute(f"INSERT INTO upsells ({cols}) VALUES ({placeholders})", list(kwargs.values()))
        uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()
        return uid

    def get_upsells(self, active_only=False, lot_id=None):
        conn = self._conn()
        q = "SELECT * FROM upsells WHERE 1=1"
        params = []
        if active_only:
            q += " AND is_active = 1"
        if lot_id:
            q += " AND (lot_id = ? OR lot_id = 0)"
            params.append(lot_id)
        q += " ORDER BY id DESC"
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_upsell(self, upsell_id):
        conn = self._conn()
        row = conn.execute("SELECT * FROM upsells WHERE id = ?", (upsell_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def update_upsell(self, upsell_id, **kwargs):
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        conn = self._conn()
        conn.execute(f"UPDATE upsells SET {sets} WHERE id = ?", list(kwargs.values()) + [upsell_id])
        conn.commit()
        conn.close()

    def delete_upsell(self, upsell_id):
        conn = self._conn()
        conn.execute("DELETE FROM upsells WHERE id = ?", (upsell_id,))
        conn.commit()
        conn.close()

    def increment_upsell(self, upsell_id, field="times_shown"):
        conn = self._conn()
        conn.execute(f"UPDATE upsells SET {field} = {field} + 1 WHERE id = ?", (upsell_id,))
        conn.commit()
        conn.close()

    # ==================== ШАБЛОНЫ СООБЩЕНИЙ ====================

    def upsert_template(self, name, text, msg_type="", is_active=1):
        conn = self._conn()
        existing = conn.execute("SELECT id FROM message_templates WHERE name = ?", (name,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE message_templates SET text = ?, msg_type = ?, is_active = ?, updated_at = datetime('now') WHERE name = ?",
                (text, msg_type, is_active, name)
            )
        else:
            conn.execute(
                "INSERT INTO message_templates (name, text, msg_type, is_active) VALUES (?, ?, ?, ?)",
                (name, text, msg_type, is_active)
            )
        conn.commit()
        conn.close()

    def get_template(self, name):
        conn = self._conn()
        row = conn.execute("SELECT * FROM message_templates WHERE name = ?", (name,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_templates(self):
        conn = self._conn()
        rows = conn.execute("SELECT * FROM message_templates ORDER BY name").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ==================== СТАТИСТИКА ====================

    def update_daily_stats(self, **kwargs):
        today = datetime.now().strftime("%Y-%m-%d")
        conn = self._conn()
        existing = conn.execute("SELECT id FROM daily_stats WHERE date = ?", (today,)).fetchone()
        if not existing:
            conn.execute("INSERT INTO daily_stats (date) VALUES (?)", (today,))
        for k, v in kwargs.items():
            conn.execute(f"UPDATE daily_stats SET {k} = {k} + ? WHERE date = ?", (v, today))
        conn.commit()
        conn.close()

    def get_stats(self, days=None):
        conn = self._conn()
        if days:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            rows = conn.execute("SELECT * FROM daily_stats WHERE date >= ? ORDER BY date DESC", (cutoff,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM daily_stats ORDER BY date DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_stats_summary(self, days=None):
        conn = self._conn()
        q = "SELECT COALESCE(SUM(total_orders),0) as total_orders, COALESCE(SUM(completed_orders),0) as completed_orders, COALESCE(SUM(failed_orders),0) as failed_orders, COALESCE(SUM(cancelled_orders),0) as cancelled_orders, COALESCE(SUM(total_revenue),0) as total_revenue, COALESCE(SUM(total_cost),0) as total_cost, COALESCE(SUM(total_profit),0) as total_profit, COALESCE(SUM(promos_used),0) as promos_used, COALESCE(SUM(upsells_shown),0) as upsells_shown FROM daily_stats"
        params = []
        if days:
            cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            q += " WHERE date >= ?"
            params.append(cutoff)
        row = conn.execute(q, params).fetchone()
        conn.close()
        return dict(row) if row else {}

    # ==================== ЛОГИ ====================

    def add_log(self, level, module, message):
        conn = self._conn()
        conn.execute("INSERT INTO logs (level, module, message) VALUES (?, ?, ?)", (level, module, message))
        conn.commit()
        conn.close()

    # ==================== FUNPAY WORKFLOW ====================

    def upsert_funpay_session(self, funpay_order_id, **kwargs):
        kwargs["updated_at"] = datetime.now().isoformat()
        columns = ["funpay_order_id"] + list(kwargs.keys())
        values = [funpay_order_id] + list(kwargs.values())
        updates = ", ".join(f"{col}=excluded.{col}" for col in kwargs.keys())
        conn = self._conn()
        conn.execute(
            f"INSERT INTO funpay_sessions ({', '.join(columns)}) VALUES ({', '.join(['?'] * len(columns))}) "
            f"ON CONFLICT(funpay_order_id) DO UPDATE SET {updates}",
            values
        )
        conn.commit()
        conn.close()

    def get_funpay_session(self, funpay_order_id):
        conn = self._conn()
        row = conn.execute("SELECT * FROM funpay_sessions WHERE funpay_order_id = ?", (funpay_order_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_funpay_session_by_chat(self, chat_id):
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM funpay_sessions WHERE chat_id = ? AND state != 'closed' ORDER BY created_at DESC LIMIT 1",
            (str(chat_id),)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_funpay_session_by_buyer(self, buyer_id):
        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM funpay_sessions WHERE buyer_id = ? AND state != 'closed' ORDER BY updated_at DESC, created_at DESC LIMIT 1",
            (buyer_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_funpay_session_by_order(self, order_id):
        conn = self._conn()
        row = conn.execute("SELECT * FROM funpay_sessions WHERE order_id = ?", (order_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def update_funpay_session(self, funpay_order_id, **kwargs):
        if not kwargs:
            return
        kwargs["updated_at"] = datetime.now().isoformat()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        conn = self._conn()
        conn.execute(
            f"UPDATE funpay_sessions SET {sets} WHERE funpay_order_id = ?",
            list(kwargs.values()) + [funpay_order_id]
        )
        conn.commit()
        conn.close()

    def delete_funpay_session(self, funpay_order_id):
        conn = self._conn()
        conn.execute("DELETE FROM funpay_sessions WHERE funpay_order_id = ?", (funpay_order_id,))
        conn.commit()
        conn.close()

    def get_due_funpay_sessions(self, now_iso):
        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM funpay_sessions WHERE state = 'completed' "
            "AND support_ticket_due_at != '' AND support_ticket_sent = 0 "
            "AND support_ticket_due_at <= ?",
            (now_iso,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def save_user_state(self, user_id, state, data=None):
        payload = json.dumps(data or {}, ensure_ascii=False, default=str)
        conn = self._conn()
        conn.execute(
            "INSERT INTO user_states (user_id, state, data, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET state = excluded.state, data = excluded.data, updated_at = excluded.updated_at",
            (int(user_id), state, payload, datetime.now().isoformat())
        )
        conn.commit()
        conn.close()

    def get_user_state(self, user_id):
        conn = self._conn()
        row = conn.execute("SELECT state, data FROM user_states WHERE user_id = ?", (int(user_id),)).fetchone()
        conn.close()
        if not row:
            return {}
        try:
            data = json.loads(row["data"] or "{}")
        except json.JSONDecodeError:
            data = {}
        return {"state": row["state"], "data": data}

    def delete_user_state(self, user_id):
        conn = self._conn()
        conn.execute("DELETE FROM user_states WHERE user_id = ?", (int(user_id),))
        conn.commit()
        conn.close()

    def get_logs(self, level=None, limit=50):
        conn = self._conn()
        q = "SELECT * FROM logs"
        params = []
        if level:
            q += " WHERE level = ?"
            params.append(level)
        q += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def cleanup_old_logs(self, days=30):
        conn = self._conn()
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        conn.execute("DELETE FROM logs WHERE created_at < ?", (cutoff,))
        conn.commit()
        conn.close()

    # ==================== ЭКСПОРТ / БЭКАП ====================

    def export_orders_csv(self):
        path = os.path.join(EXPORT_DIR, f"orders_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        conn = self._conn()
        rows = conn.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
        conn.close()
        if not rows:
            return None
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=dict(rows[0]).keys())
            writer.writeheader()
            for r in rows:
                writer.writerow(dict(r))
        return path

    def export_stats_json(self):
        path = os.path.join(EXPORT_DIR, f"stats_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        data = {
            "summary": self.get_stats_summary(),
            "daily": self.get_stats(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path

    def backup(self):
        path = os.path.join(BACKUP_DIR, f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
        shutil.copy2(self.db_path, path)
        return path
