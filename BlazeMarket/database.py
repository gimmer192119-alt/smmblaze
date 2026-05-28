"""
BlazeMarket Database - SQLite database management
"""
import sqlite3
import os
import json
from datetime import datetime
from config import DB_PATH


class Database:
    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._create_tables()
    
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn
    
    def _create_tables(self):
        conn = self._conn()
        conn.executescript("""
        -- Users table
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE,
            username TEXT,
            full_name TEXT,
            role TEXT DEFAULT 'buyer',
            mirror_code TEXT UNIQUE,
            created_at TEXT DEFAULT (datetime('now'))
        );
        
        -- Order codes for comment orders
        CREATE TABLE IF NOT EXISTS order_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            chat_id INTEGER,
            message_id INTEGER,
            service_type TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now')),
            expires_at TEXT
        );
        
        -- Orders table
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_code TEXT,
            user_id INTEGER,
            service_id INTEGER,
            service_name TEXT,
            provider TEXT DEFAULT 'twiboost',
            link TEXT,
            quantity INTEGER,
            cost_price REAL,
            sell_price REAL,
            markup_percent REAL,
            profit REAL,
            status TEXT DEFAULT 'pending',
            comments TEXT,
            comments_file TEXT,
            api_order_id TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT
        );
        
        -- Services from API providers
        CREATE TABLE IF NOT EXISTS services (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT DEFAULT 'twiboost',
            service_id INTEGER NOT NULL,
            name TEXT,
            type TEXT,
            category TEXT,
            platform TEXT,
            rate REAL,
            min_order INTEGER,
            max_order INTEGER,
            is_active INTEGER DEFAULT 1,
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(provider, service_id)
        );
        
        -- Mirror shops (resellers)
        CREATE TABLE IF NOT EXISTS mirrors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER,
            name TEXT,
            code TEXT UNIQUE,
            share_percent REAL DEFAULT 20.0,
            markup_percent REAL DEFAULT 0,
            bot_token TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );
        
        -- Transactions
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            type TEXT,
            payment_id TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now'))
        );
        
        -- Settings
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)
        conn.commit()
        conn.close()
    
    # User operations
    def add_user(self, telegram_id, username=None, full_name=None, role='buyer'):
        conn = self._conn()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO users (telegram_id, username, full_name, role) VALUES (?, ?, ?, ?)",
                (telegram_id, username, full_name, role)
            )
            conn.commit()
            return conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        finally:
            conn.close()
    
    def get_user(self, telegram_id):
        conn = self._conn()
        try:
            return conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        finally:
            conn.close()
    
    def update_user_role(self, telegram_id, role):
        conn = self._conn()
        try:
            conn.execute("UPDATE users SET role = ? WHERE telegram_id = ?", (role, telegram_id))
            conn.commit()
        finally:
            conn.close()
    
    # Order code operations
    def create_order_code(self, code, chat_id, message_id, service_type, expires_in_hours=24):
        from datetime import timedelta
        expires_at = datetime.now() + timedelta(hours=expires_in_hours)
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO order_codes (code, chat_id, message_id, service_type, expires_at) VALUES (?, ?, ?, ?, ?)",
                (code, chat_id, message_id, service_type, expires_at.isoformat())
            )
            conn.commit()
            return code
        finally:
            conn.close()
    
    def get_order_code(self, code):
        conn = self._conn()
        try:
            return conn.execute("SELECT * FROM order_codes WHERE code = ?", (code,)).fetchone()
        finally:
            conn.close()
    
    def update_order_code_status(self, code, status):
        conn = self._conn()
        try:
            conn.execute("UPDATE order_codes SET status = ? WHERE code = ?", (status, code))
            conn.commit()
        finally:
            conn.close()
    
    # Order operations
    def create_order(self, order_code, user_id, service_id, service_name, link, quantity, 
                     cost_price, sell_price, markup_percent, provider='twiboost'):
        profit = sell_price - cost_price
        conn = self._conn()
        try:
            cursor = conn.execute(
                """INSERT INTO orders 
                   (order_code, user_id, service_id, service_name, link, quantity, 
                    cost_price, sell_price, markup_percent, profit, provider)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (order_code, user_id, service_id, service_name, link, quantity,
                 cost_price, sell_price, markup_percent, profit, provider)
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()
    
    def update_order_comments(self, order_id, comments=None, comments_file=None):
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE orders SET comments = ?, comments_file = ? WHERE id = ?",
                (comments, comments_file, order_id)
            )
            conn.commit()
        finally:
            conn.close()
    
    def get_order(self, order_id):
        conn = self._conn()
        try:
            return conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        finally:
            conn.close()
    
    # Service operations
    def add_service(self, provider, service_id, name, service_type, category, platform, 
                    rate, min_order, max_order):
        conn = self._conn()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO services 
                   (provider, service_id, name, type, category, platform, rate, min_order, max_order, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (provider, service_id, name, service_type, category, platform, rate, min_order, max_order)
            )
            conn.commit()
        finally:
            conn.close()
    
    def get_services_by_category(self, category, provider='twiboost'):
        conn = self._conn()
        try:
            return conn.execute(
                "SELECT * FROM services WHERE category = ? AND provider = ? AND is_active = 1",
                (category, provider)
            ).fetchall()
        finally:
            conn.close()
    
    def get_all_services(self, provider='twiboost'):
        conn = self._conn()
        try:
            return conn.execute(
                "SELECT * FROM services WHERE provider = ? AND is_active = 1 ORDER BY category, name",
                (provider,)
            ).fetchall()
        finally:
            conn.close()
    
    # Mirror operations
    def create_mirror(self, owner_id, name, code, share_percent=20.0, markup_percent=0):
        conn = self._conn()
        try:
            cursor = conn.execute(
                "INSERT INTO mirrors (owner_id, name, code, share_percent, markup_percent) VALUES (?, ?, ?, ?, ?)",
                (owner_id, name, code, share_percent, markup_percent)
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()
    
    def get_mirror_by_code(self, code):
        conn = self._conn()
        try:
            return conn.execute("SELECT * FROM mirrors WHERE code = ?", (code,)).fetchone()
        finally:
            conn.close()
    
    # Transaction operations
    def add_transaction(self, user_id, amount, trans_type, payment_id=None, status='pending'):
        conn = self._conn()
        try:
            cursor = conn.execute(
                "INSERT INTO transactions (user_id, amount, type, payment_id, status) VALUES (?, ?, ?, ?, ?)",
                (user_id, amount, trans_type, payment_id, status)
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()
    
    # Settings operations
    def get_setting(self, key, default=None):
        conn = self._conn()
        try:
            result = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return result['value'] if result else default
        finally:
            conn.close()
    
    def set_setting(self, key, value):
        conn = self._conn()
        try:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
            conn.commit()
        finally:
            conn.close()


db = Database()
