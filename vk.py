# -*- coding: utf-8 -*-
"""
====================================================================
 ربات فروش کانفیگ — نسخه بهینه v3.0
====================================================================
✅ حذف پرداخت کارت به کارت — پرداخت فقط از کیف پول
✅ پاداش رفرال: ۱۰,۰۰۰ تومان به ازای هر دعوت (فقط کاربران جدید)
✅ بهبود رابط کاربری و پیام‌ها
✅ رفع باگ close_ticket و back_to_config
✅ بهبود مدیریت خطاها
✅ پیام همگانی ادمین
====================================================================
"""

import logging
import sqlite3
import os
import re
import random
import string
import json
import time
import uuid
import requests
from datetime import datetime, timedelta
from contextlib import closing
from typing import Optional, Dict, List, Tuple
from functools import wraps

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ====================================================================
#  تنظیمات اصلی
# ====================================================================

BOT_TOKEN = "8841213365:AAH4GGJSm5B43nwSvVTUWYat2KusXkqCWp0"
ADMIN_IDS = [7438138322]
CHANNEL_USERNAME = "@kirrr85"
CURRENCY = "تومان"
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shop_advanced.db")

# پاداش رفرال
REFERRAL_BONUS_AMOUNT  = 10_000   # ۱۰,۰۰۰ تومان به ازای هر دعوت

# تنظیمات پنل
PANEL_URL      = "https://voidlatency-hewb0j.wwzwfd.workers.dev/panel/"
PANEL_USERNAME = "12345m"
PANEL_PASSWORD = "12345m"

# لاگینگ
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ====================================================================
#  دکوراتورهای کمکی
# ====================================================================

def admin_only(func):
    """محدود کردن دسترسی به ادمین"""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            if update.message:
                await update.message.reply_text("⛔ شما دسترسی به این بخش ندارید.")
            elif update.callback_query:
                await update.callback_query.answer("⛔ دسترسی ندارید!", show_alert=True)
            return
        return await func(update, context, *args, **kwargs)
    return wrapper


def log_error(func):
    """ثبت خطاها و اطلاع‌رسانی به ادمین"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"❌ خطا در {func.__name__}: {e}", exc_info=True)
            update = next((a for a in args if isinstance(a, Update)), None)
            if update:
                try:
                    for admin_id in ADMIN_IDS:
                        await args[0].bot.send_message(
                            chat_id=admin_id,
                            text=f"⚠️ خطا در تابع <code>{func.__name__}</code>:\n<code>{str(e)[:300]}</code>",
                            parse_mode=ParseMode.HTML
                        )
                except Exception:
                    pass
                try:
                    msg_text = "❌ خطایی رخ داد. تیم پشتیبانی مطلع شد.\nلطفاً چند دقیقه دیگر دوباره امتحان کنید."
                    if update.message:
                        await update.message.reply_text(msg_text)
                    elif update.callback_query:
                        await update.callback_query.edit_message_text(msg_text)
                except Exception:
                    pass
    return wrapper

# ====================================================================
#  دیتابیس
# ====================================================================

class Database:
    """مدیریت دیتابیس (Singleton)"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_db()
        return cls._instance

    def _init_db(self):
        self.db_path = DB_PATH
        self._init_tables()
        self._init_default_products()

    def get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_tables(self):
        with closing(self.get_conn()) as conn, conn:
            conn.executescript("""
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS categories (
                    id   INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE
                );

                CREATE TABLE IF NOT EXISTS products (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    category_id INTEGER NOT NULL,
                    name        TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    is_active   INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS product_variants (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id    INTEGER NOT NULL,
                    variant_name  TEXT NOT NULL,
                    price         INTEGER NOT NULL,
                    stock         INTEGER NOT NULL DEFAULT 0,
                    photo_file_id TEXT DEFAULT NULL,
                    volume_gb     INTEGER DEFAULT 5,
                    days          INTEGER DEFAULT 30,
                    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS cart_items (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    INTEGER NOT NULL,
                    variant_id INTEGER NOT NULL,
                    quantity   INTEGER NOT NULL DEFAULT 1,
                    FOREIGN KEY (variant_id) REFERENCES product_variants(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS users (
                    id              INTEGER PRIMARY KEY,
                    username        TEXT,
                    first_name      TEXT,
                    last_name       TEXT,
                    referral_code   TEXT UNIQUE,
                    referrer_id     INTEGER,
                    wallet_balance  INTEGER DEFAULT 0,
                    created_at      TEXT,
                    FOREIGN KEY (referrer_id) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id         INTEGER NOT NULL,
                    username        TEXT,
                    full_name       TEXT,
                    total_price     INTEGER NOT NULL,
                    discount_amount INTEGER DEFAULT 0,
                    final_price     INTEGER NOT NULL,
                    status          TEXT NOT NULL DEFAULT 'pending',
                    payment_status  TEXT DEFAULT 'unpaid',
                    created_at      TEXT NOT NULL,
                    coupon_code     TEXT,
                    referrer_id     INTEGER,
                    config_link     TEXT,
                    FOREIGN KEY (referrer_id) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS order_items (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id     INTEGER NOT NULL,
                    variant_name TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    quantity     INTEGER NOT NULL,
                    unit_price   INTEGER NOT NULL,
                    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS wallet_transactions (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     INTEGER NOT NULL,
                    amount      INTEGER NOT NULL,
                    description TEXT,
                    created_at  TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                CREATE TABLE IF NOT EXISTS coupons (
                    code              TEXT PRIMARY KEY,
                    discount_type     TEXT NOT NULL,
                    discount_value    INTEGER NOT NULL,
                    min_order_amount  INTEGER DEFAULT 0,
                    expires_at        TEXT,
                    usage_limit       INTEGER DEFAULT 1,
                    used_count        INTEGER DEFAULT 0,
                    is_active         INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS support_tickets (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id        INTEGER NOT NULL,
                    subject        TEXT,
                    message        TEXT,
                    status         TEXT DEFAULT 'open',
                    created_at     TEXT,
                    updated_at     TEXT,
                    admin_response TEXT,
                    responded_at   TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_orders_user_id  ON orders(user_id);
                CREATE INDEX IF NOT EXISTS idx_orders_status   ON orders(status);
                CREATE INDEX IF NOT EXISTS idx_tickets_user_id ON support_tickets(user_id);
            """)

    def _init_default_products(self):
        with closing(self.get_conn()) as conn, conn:
            cat = conn.execute("SELECT id FROM categories WHERE name='کانفیگ'").fetchone()
            if not cat:
                cur = conn.execute("INSERT INTO categories (name) VALUES (?)", ("کانفیگ",))
                cat_id = cur.lastrowid
            else:
                cat_id = cat["id"]

            prods = conn.execute("SELECT id FROM products WHERE category_id=?", (cat_id,)).fetchall()
            if not prods:
                defaults = [
                    ("۵ گیگابایت",  "کانفیگ ۵ گیگابایت — ۳۰ روز",  "۵ گیگابایت / ۳۰ روز",  10_000, 10, 5,   30),
                    ("۱۰ گیگابایت", "کانفیگ ۱۰ گیگابایت — ۳۰ روز", "۱۰ گیگابایت / ۳۰ روز", 20_000, 10, 10,  30),
                    ("نامحدود",     "کانفیگ نامحدود — ۳۰ روز",      "نامحدود / ۳۰ روز",     40_000, 5,  999, 30),
                ]
                for name, desc, variant_name, price, stock, volume, days in defaults:
                    cur = conn.execute(
                        "INSERT INTO products (category_id, name, description) VALUES (?, ?, ?)",
                        (cat_id, name, desc)
                    )
                    conn.execute(
                        "INSERT INTO product_variants (product_id, variant_name, price, stock, volume_gb, days) VALUES (?,?,?,?,?,?)",
                        (cur.lastrowid, variant_name, price, stock, volume, days)
                    )


db = Database()

# ====================================================================
#  توابع کمکی دیتابیس
# ====================================================================

def get_or_create_user(user_id, username, first_name, last_name="", referrer_code=None):
    """
    دریافت یا ایجاد کاربر.
    ⚠️ مهم: پاداش رفرال فقط برای کاربران جدیدی که قبلاً در دیتابیس نبوده‌اند.
    اگر کاربر از قبل وجود داشته باشد، هیچ پاداشی داده نمی‌شود.
    """
    with closing(db.get_conn()) as conn, conn:
        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        
        # اگر کاربر از قبل وجود داشته باشد، فقط برگردان — هیچ پاداشی تعلق نمی‌گیرد
        if user:
            return user
        
        # کاربر جدید است — ایجاد حساب و بررسی رفرال
        referral_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        referrer_id = None
        
        if referrer_code:
            # حالا referrer_code خودش user_id دعوت‌کننده است
            # مستقیماً چک می‌کنیم که این آیدی عددی وجود داره یا نه
            ref_user = conn.execute("SELECT id, first_name FROM users WHERE id=?", (referrer_code,)).fetchone()
            if ref_user and ref_user["id"] != user_id:
                referrer_id = ref_user["id"]
                # پاداش فوری ۱۰,۰۰۰ تومان به دعوت‌کننده
                conn.execute(
                    "UPDATE users SET wallet_balance = wallet_balance + ? WHERE id=?",
                    (REFERRAL_BONUS_AMOUNT, referrer_id)
                )
                conn.execute(
                    "INSERT INTO wallet_transactions (user_id, amount, description, created_at) VALUES (?,?,?,?)",
                    (referrer_id, REFERRAL_BONUS_AMOUNT,
                     f"🎁 پاداش دعوت کاربر {first_name}", datetime.now().isoformat())
                )
                logger.info(f"🎁 پاداش {REFERRAL_BONUS_AMOUNT} تومان به کاربر {referrer_id} بابت دعوت کاربر جدید {user_id}")
        
        # ثبت کاربر جدید
        conn.execute(
            "INSERT INTO users (id, username, first_name, last_name, referral_code, referrer_id, wallet_balance, created_at) VALUES (?,?,?,?,?,?,0,?)",
            (user_id, username, first_name, last_name, referral_code, referrer_id, datetime.now().isoformat())
        )
        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return user


def get_user_by_id(user_id):
    with closing(db.get_conn()) as conn:
        return conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()


def get_user_by_referral_code(code):
    with closing(db.get_conn()) as conn:
        return conn.execute("SELECT * FROM users WHERE referral_code=?", (code,)).fetchone()


def get_all_user_ids():
    """دریافت لیست تمام آیدی‌های کاربران برای ارسال پیام همگانی"""
    with closing(db.get_conn()) as conn:
        users = conn.execute("SELECT id FROM users").fetchall()
        return [user["id"] for user in users]


def add_wallet_transaction(user_id, amount, description):
    with closing(db.get_conn()) as conn, conn:
        conn.execute(
            "INSERT INTO wallet_transactions (user_id, amount, description, created_at) VALUES (?,?,?,?)",
            (user_id, amount, description, datetime.now().isoformat())
        )
        conn.execute("UPDATE users SET wallet_balance = wallet_balance + ? WHERE id=?", (amount, user_id))


def deduct_wallet(user_id, amount):
    """کسر مبلغ از کیف پول — برگرداندن False اگر موجودی کافی نباشد"""
    with closing(db.get_conn()) as conn, conn:
        user = conn.execute("SELECT wallet_balance FROM users WHERE id=?", (user_id,)).fetchone()
        if not user or user["wallet_balance"] < amount:
            return False
        conn.execute("UPDATE users SET wallet_balance = wallet_balance - ? WHERE id=?", (amount, user_id))
        conn.execute(
            "INSERT INTO wallet_transactions (user_id, amount, description, created_at) VALUES (?,?,?,?)",
            (user_id, -amount, "💳 پرداخت سفارش", datetime.now().isoformat())
        )
        return True


# ---- دسته‌بندی و محصول ----

def db_add_category(name):
    with closing(db.get_conn()) as conn, conn:
        cur = conn.execute("INSERT INTO categories (name) VALUES (?)", (name,))
        return cur.lastrowid


def db_get_categories():
    with closing(db.get_conn()) as conn:
        return conn.execute("SELECT * FROM categories ORDER BY name").fetchall()


def db_add_product(category_id, name, description=""):
    with closing(db.get_conn()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO products (category_id, name, description) VALUES (?,?,?)",
            (category_id, name, description)
        )
        return cur.lastrowid


def db_add_variant(product_id, variant_name, price, stock, volume_gb, days, photo_file_id=None):
    with closing(db.get_conn()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO product_variants (product_id, variant_name, price, stock, volume_gb, days, photo_file_id) VALUES (?,?,?,?,?,?,?)",
            (product_id, variant_name, price, stock, volume_gb, days, photo_file_id)
        )
        return cur.lastrowid


def db_get_variants_by_product(product_id):
    with closing(db.get_conn()) as conn:
        return conn.execute(
            "SELECT * FROM product_variants WHERE product_id=? ORDER BY variant_name",
            (product_id,)
        ).fetchall()


def db_get_variant(variant_id):
    with closing(db.get_conn()) as conn:
        return conn.execute("SELECT * FROM product_variants WHERE id=?", (variant_id,)).fetchone()


def db_get_variant_by_name(variant_name):
    with closing(db.get_conn()) as conn:
        return conn.execute(
            "SELECT * FROM product_variants WHERE variant_name=? LIMIT 1",
            (variant_name,)
        ).fetchone()


def db_update_variant_stock(variant_id, new_stock):
    with closing(db.get_conn()) as conn, conn:
        conn.execute("UPDATE product_variants SET stock=? WHERE id=?", (new_stock, variant_id))


def db_deactivate_product(product_id):
    with closing(db.get_conn()) as conn, conn:
        conn.execute("UPDATE products SET is_active=0 WHERE id=?", (product_id,))


def db_get_all_variants():
    with closing(db.get_conn()) as conn:
        return conn.execute("""
            SELECT v.*, p.name AS product_name
            FROM product_variants v
            JOIN products p ON v.product_id = p.id
            WHERE p.is_active = 1
            ORDER BY p.name, v.variant_name
        """).fetchall()


# ---- سبد خرید ----

def db_add_to_cart(user_id, variant_id, quantity=1):
    with closing(db.get_conn()) as conn, conn:
        existing = conn.execute(
            "SELECT * FROM cart_items WHERE user_id=? AND variant_id=?",
            (user_id, variant_id)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE cart_items SET quantity = quantity + ? WHERE id=?",
                (quantity, existing["id"])
            )
        else:
            conn.execute(
                "INSERT INTO cart_items (user_id, variant_id, quantity) VALUES (?,?,?)",
                (user_id, variant_id, quantity)
            )


def db_get_cart(user_id):
    with closing(db.get_conn()) as conn:
        return conn.execute("""
            SELECT ci.id AS cart_id, ci.quantity,
                   v.id AS variant_id, v.variant_name, v.price, v.stock, v.volume_gb, v.days,
                   p.id AS product_id, p.name AS product_name
            FROM cart_items ci
            JOIN product_variants v ON ci.variant_id = v.id
            JOIN products p ON v.product_id = p.id
            WHERE ci.user_id = ?
        """, (user_id,)).fetchall()


def db_clear_cart(user_id):
    with closing(db.get_conn()) as conn, conn:
        conn.execute("DELETE FROM cart_items WHERE user_id=?", (user_id,))


def db_remove_cart_item(cart_id):
    with closing(db.get_conn()) as conn, conn:
        conn.execute("DELETE FROM cart_items WHERE id=?", (cart_id,))


# ---- سفارش ----

def db_create_order(user_id, username, full_name="کاربر", cart_items=None, coupon_code=None, referrer_id=None):
    total = sum(item["price"] * item["quantity"] for item in cart_items)
    discount = 0
    if coupon_code:
        coupon = db_get_coupon(coupon_code)
        if coupon and coupon["is_active"] and coupon["used_count"] < coupon["usage_limit"]:
            exp = coupon["expires_at"]
            if exp is None or datetime.now() < datetime.fromisoformat(exp):
                if total >= coupon["min_order_amount"]:
                    if coupon["discount_type"] == "percent":
                        discount = int(total * coupon["discount_value"] / 100)
                    else:
                        discount = min(coupon["discount_value"], total)
                    with closing(db.get_conn()) as conn, conn:
                        conn.execute("UPDATE coupons SET used_count = used_count + 1 WHERE code=?", (coupon_code,))

    final_price = total - discount

    with closing(db.get_conn()) as conn, conn:
        cur = conn.execute(
            """INSERT INTO orders
               (user_id, username, full_name, total_price, discount_amount, final_price,
                status, payment_status, created_at, coupon_code, referrer_id)
               VALUES (?,?,?,?,?,?,'pending','unpaid',?,?,?)""",
            (user_id, username, full_name, total, discount, final_price,
             datetime.now().isoformat(), coupon_code, referrer_id)
        )
        order_id = cur.lastrowid
        for item in cart_items:
            conn.execute(
                "INSERT INTO order_items (order_id, variant_name, product_name, quantity, unit_price) VALUES (?,?,?,?,?)",
                (order_id, item["variant_name"], item["product_name"], item["quantity"], item["price"])
            )
            new_stock = max(0, item["stock"] - item["quantity"])
            conn.execute("UPDATE product_variants SET stock=? WHERE id=?", (new_stock, item["variant_id"]))
        conn.execute("DELETE FROM cart_items WHERE user_id=?", (user_id,))

    return order_id, final_price


def db_get_order(order_id):
    with closing(db.get_conn()) as conn:
        return conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()


def db_get_order_items(order_id):
    with closing(db.get_conn()) as conn:
        return conn.execute("SELECT * FROM order_items WHERE order_id=?", (order_id,)).fetchall()


def db_update_order_status(order_id, status):
    with closing(db.get_conn()) as conn, conn:
        conn.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))


def db_update_order_payment_status(order_id, payment_status):
    with closing(db.get_conn()) as conn, conn:
        conn.execute("UPDATE orders SET payment_status=? WHERE id=?", (payment_status, order_id))


def db_update_order_config(order_id, config_link):
    with closing(db.get_conn()) as conn, conn:
        conn.execute("UPDATE orders SET config_link=? WHERE id=?", (config_link, order_id))


def db_get_user_orders(user_id, limit=20):
    with closing(db.get_conn()) as conn:
        return conn.execute(
            "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()


def db_get_all_orders(status=None, limit=20):
    with closing(db.get_conn()) as conn:
        if status:
            return conn.execute(
                "SELECT * FROM orders WHERE status=? ORDER BY id DESC LIMIT ?",
                (status, limit)
            ).fetchall()
        return conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def db_get_stats():
    with closing(db.get_conn()) as conn:
        return {
            "total_orders":   conn.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"],
            "total_revenue":  conn.execute("SELECT COALESCE(SUM(final_price),0) s FROM orders WHERE status!='cancelled'").fetchone()["s"],
            "pending_orders": conn.execute("SELECT COUNT(*) c FROM orders WHERE status='pending'").fetchone()["c"],
            "paid_orders":    conn.execute("SELECT COUNT(*) c FROM orders WHERE payment_status='paid'").fetchone()["c"],
            "products_count": conn.execute("SELECT COUNT(*) c FROM products WHERE is_active=1").fetchone()["c"],
            "users_count":    conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"],
        }


# ---- کوپن ----

def db_add_coupon(code, discount_type, discount_value, min_order_amount=0, expires_at=None, usage_limit=1):
    with closing(db.get_conn()) as conn, conn:
        conn.execute(
            "INSERT INTO coupons (code, discount_type, discount_value, min_order_amount, expires_at, usage_limit) VALUES (?,?,?,?,?,?)",
            (code, discount_type, discount_value, min_order_amount, expires_at, usage_limit)
        )


def db_get_coupon(code):
    with closing(db.get_conn()) as conn:
        return conn.execute("SELECT * FROM coupons WHERE code=?", (code,)).fetchone()


def db_get_all_coupons():
    with closing(db.get_conn()) as conn:
        return conn.execute("SELECT * FROM coupons ORDER BY code").fetchall()


# ---- تیکت ----

def db_create_ticket(user_id, subject, message):
    with closing(db.get_conn()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO support_tickets (user_id, subject, message, status, created_at, updated_at) VALUES (?,?,?,'open',?,?)",
            (user_id, subject, message, datetime.now().isoformat(), datetime.now().isoformat())
        )
        return cur.lastrowid


def db_get_tickets(status=None):
    with closing(db.get_conn()) as conn:
        if status:
            return conn.execute("SELECT * FROM support_tickets WHERE status=? ORDER BY id DESC", (status,)).fetchall()
        return conn.execute("SELECT * FROM support_tickets ORDER BY id DESC").fetchall()


def db_get_ticket(ticket_id):
    with closing(db.get_conn()) as conn:
        return conn.execute("SELECT * FROM support_tickets WHERE id=?", (ticket_id,)).fetchone()


def db_update_ticket_response(ticket_id, admin_response):
    with closing(db.get_conn()) as conn, conn:
        conn.execute(
            "UPDATE support_tickets SET admin_response=?, status='in_progress', responded_at=?, updated_at=? WHERE id=?",
            (admin_response, datetime.now().isoformat(), datetime.now().isoformat(), ticket_id)
        )


def db_close_ticket(ticket_id):
    with closing(db.get_conn()) as conn, conn:
        conn.execute(
            "UPDATE support_tickets SET status='closed', updated_at=? WHERE id=?",
            (datetime.now().isoformat(), ticket_id)
        )

# ====================================================================
#  توابع عمومی
# ====================================================================

STATUS_LABELS = {
    "pending":   "⏳ در انتظار بررسی",
    "confirmed": "✅ تأیید شده",
    "shipped":   "📦 ارسال شده",
    "cancelled": "❌ لغو شده",
}

PAYMENT_STATUS_LABELS = {
    "unpaid": "💳 پرداخت نشده",
    "paid":   "✅ پرداخت شده",
}


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def format_price(amount: int) -> str:
    return f"{amount:,} {CURRENCY}"


async def is_user_member(bot, user_id):
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_USERNAME, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logger.error(f"خطا در بررسی عضویت: {e}")
        return False

# ====================================================================
#  اتصال به پنل
# ====================================================================

def panel_login() -> Optional[requests.Session]:
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    try:
        resp = session.post(f"{PANEL_URL}login",
                            data={"username": PANEL_USERNAME, "password": PANEL_PASSWORD},
                            headers=headers, timeout=15)
        if resp.status_code == 200:
            logger.info("✅ لاگین به پنل موفق")
            return session
        logger.error(f"❌ خطا در لاگین پنل: {resp.status_code}")
        return None
    except Exception as e:
        logger.error(f"❌ خطا در لاگین پنل: {e}")
        return None


def create_config_on_panel(remark: str, volume_gb: int, days: int = 30) -> Optional[str]:
    session = panel_login()
    if not session:
        return None
    try:
        ports_resp = session.get(f"{PANEL_URL}xui/API/inbound/nextPort", timeout=10)
        port = 443
        if ports_resp.status_code == 200:
            try:
                data = ports_resp.json()
                if data.get("success"):
                    port = data.get("obj", 443)
            except Exception:
                pass

        expiry = int(time.time() + days * 24 * 3600)
        client_id = str(uuid.uuid4())
        inbound_data = {
            "up": 0, "down": 0,
            "total": volume_gb * 1024 * 1024 * 1024,
            "remark": remark,
            "enable": True,
            "expiryTime": expiry,
            "listen": "",
            "port": port,
            "protocol": "vless",
            "settings": json.dumps({
                "clients": [{
                    "id": client_id, "flow": "",
                    "email": f"{remark}@example.com",
                    "limitIp": 0, "totalGB": volume_gb,
                    "expiryTime": expiry, "enable": True,
                    "tgId": "", "subId": ""
                }]
            }),
            "streamSettings": json.dumps({
                "network": "ws", "security": "none",
                "wsSettings": {"path": "/", "headers": {}}
            }),
            "sniffing": json.dumps({"enabled": True, "destOverride": ["http", "tls"]})
        }
        resp = session.post(f"{PANEL_URL}xui/API/inbound/add", json=inbound_data, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                inbound_id = data.get("obj")
                return _get_config_link(session, inbound_id, port, remark, client_id)
        logger.error(f"خطا در ساخت اینباند: {resp.status_code} — {resp.text[:200]}")
        return None
    except Exception as e:
        logger.error(f"خطا در ایجاد کانفیگ: {e}", exc_info=True)
        return None


def _get_config_link(session: requests.Session, inbound_id: int, port: int,
                     remark: str, client_id: str) -> Optional[str]:
    try:
        resp = session.get(f"{PANEL_URL}xui/API/inbound/get/{inbound_id}", timeout=10)
        if resp.status_code == 200 and resp.json().get("success"):
            domain = PANEL_URL.replace("https://", "").replace("http://", "").replace("/panel/", "")
            return (f"vless://{client_id}@{domain}:{port}"
                    f"?encryption=none&security=none&type=ws&path=/&host={domain}#{remark}")
        return None
    except Exception as e:
        logger.error(f"خطا در دریافت لینک: {e}")
        return None

# ====================================================================
#  کیبوردها
# ====================================================================

def main_menu_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    rows = [
        ["🛍 خرید کانفیگ", "🛒 سبد خرید"],
        ["📦 سفارشات من", "🎫 پشتیبانی"],
        ["👥 دعوت از دوستان", "💰 کیف پول"],
    ]
    if is_admin(user_id):
        rows.append(["⚙️ پنل مدیریت"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def admin_menu_keyboard() -> ReplyKeyboardMarkup:
    rows = [
        ["➕ افزودن دسته", "➕ افزودن محصول", "➕ افزودن واریانت"],
        ["📋 لیست محصولات", "🧾 مدیریت سفارشات"],
        ["🎫 مدیریت کوپن‌ها", "📩 تیکت‌های پشتیبانی"],
        ["📊 آمار فروش", "👥 کاربران", "📦 مدیریت موجودی"],
        ["📢 پیام همگانی", "🔙 بازگشت به منوی اصلی"],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

# ====================================================================
#  حالت‌های مکالمه
# ====================================================================

(
    ADD_PRODUCT_CATEGORY,
    ADD_PRODUCT_NAME,
    ADD_PRODUCT_DESCRIPTION,
    ADD_VARIANT_PRODUCT,
    ADD_VARIANT_NAME,
    ADD_VARIANT_PRICE,
    ADD_VARIANT_STOCK,
    ADD_VARIANT_PHOTO,
    ADD_VARIANT_VOLUME,
    ADD_VARIANT_DAYS,
    CHECKOUT_COUPON,
    TICKET_SUBJECT,
    TICKET_MESSAGE,
    TICKET_RESPONSE,
    MANUAL_CONFIG,
    COUPON_CODE,
    COUPON_TYPE,
    COUPON_VALUE,
    COUPON_MIN_ORDER,
    COUPON_EXPIRY,
    COUPON_LIMIT,
    STOCK_MANAGE_SELECT,
    STOCK_MANAGE_NEW,
    BROADCAST_MESSAGE,
) = range(24)

# ====================================================================
#  هندلرهای اصلی
# ====================================================================

@log_error
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    bot  = context.bot

    if not await is_user_member(bot, user.id):
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 عضویت در کانال", url=f"https://t.me/{CHANNEL_USERNAME[1:]}")],
            [InlineKeyboardButton("✅ بررسی عضویت", callback_data="check_sub")]
        ])
        await update.message.reply_text(
            f"🔒 <b>دسترسی محدود</b>\n\n"
            f"برای استفاده از ربات، ابتدا در کانال ما عضو شوید:\n{CHANNEL_USERNAME}",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
        return

    args = context.args
    # حالا args[0] مستقیماً user_id دعوت‌کننده است (عدد)
    referrer_code = args[0] if args else None

    user_data = get_or_create_user(user.id, user.username, user.first_name, user.last_name or "", referrer_code)

    welcome = f"👋 سلام <b>{user.first_name}</b>!\n\nبه ربات فروش کانفیگ خوش آمدید 🚀"
    if referrer_code and user_data["referrer_id"]:
        ref_user = get_user_by_id(user_data["referrer_id"])
        if ref_user:
            ref_name = ref_user["first_name"] or ref_user["username"] or "دوست"
            welcome += f"\n\n🎁 شما توسط <b>{ref_name}</b> دعوت شدید!"
    welcome += "\n\nاز منوی پایین گزینه مورد نظر را انتخاب کنید 👇"

    await update.message.reply_text(welcome, parse_mode=ParseMode.HTML,
                                    reply_markup=main_menu_keyboard(user.id))


@log_error
async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user  = query.from_user

    if await is_user_member(context.bot, user.id):
        await query.answer("✅ عضویت تأیید شد!")
        await query.edit_message_text("✅ عضویت شما تأیید شد.")
        get_or_create_user(user.id, user.username, user.first_name, user.last_name or "")
        await context.bot.send_message(
            chat_id=user.id,
            text=f"👋 سلام <b>{user.first_name}</b>!\nاز منوی پایین گزینه مورد نظر را انتخاب کنید 👇",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard(user.id)
        )
    else:
        await query.answer("❌ هنوز عضو نشده‌اید!", show_alert=True)


@log_error
async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "↩️ عملیات لغو شد.",
        reply_markup=main_menu_keyboard(update.effective_user.id)
    )
    return ConversationHandler.END

# ====================================================================
#  خرید کانفیگ
# ====================================================================

@log_error
async def show_config_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with closing(db.get_conn()) as conn:
        variants = conn.execute("""
            SELECT v.*, p.name AS product_name
            FROM product_variants v
            JOIN products p ON v.product_id = p.id
            WHERE p.is_active = 1
              AND p.category_id = (SELECT id FROM categories WHERE name = 'کانفیگ')
            ORDER BY v.price
        """).fetchall()

    if not variants:
        text = "⚠️ در حال حاضر محصولی برای فروش موجود نیست.\nلطفاً بعداً مراجعه کنید."
        if update.message:
            await update.message.reply_text(text)
        else:
            await update.callback_query.edit_message_text(text)
        return

    keyboard = []
    for v in variants:
        if v["stock"] > 0:
            label = f"✅ {v['product_name']} — {format_price(v['price'])}"
        else:
            label = f"❌ {v['product_name']} — ناموجود"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"buy_config:{v['id']}")])
    keyboard.append([InlineKeyboardButton("🔙 منوی اصلی", callback_data="back_to_menu")])

    header = (
        "🛍 <b>خرید کانفیگ</b>\n\n"
        "📌 پرداخت از کیف پول انجام می‌شود.\n"
        "👥 با دعوت هر نفر <b>10,000 تومان</b> به کیف پول شما افزوده می‌شود!\n\n"
        "یکی از پلان‌های زیر را انتخاب کنید:"
    )

    if update.message:
        await update.message.reply_text(header, parse_mode=ParseMode.HTML,
                                        reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.callback_query.edit_message_text(header, parse_mode=ParseMode.HTML,
                                                      reply_markup=InlineKeyboardMarkup(keyboard))


@log_error
async def buy_config_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        variant_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.edit_message_text("❌ خطا در انتخاب محصول.")
        return

    variant = db_get_variant(variant_id)
    if not variant:
        await query.edit_message_text("❌ محصول یافت نشد.")
        return

    if variant["stock"] <= 0:
        await query.edit_message_text(
            "⚠️ متأسفانه این پلان موجود نیست.\nلطفاً پلان دیگری را انتخاب کنید.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="back_to_config")]
            ])
        )
        return

    db_add_to_cart(query.from_user.id, variant_id, 1)

    await query.edit_message_text(
        f"✅ <b>{variant['variant_name']}</b> به سبد خرید اضافه شد.\n\n"
        f"💰 قیمت: <b>{format_price(variant['price'])}</b>\n"
        f"📊 حجم: {variant['volume_gb']} گیگابایت\n"
        f"📅 مدت: {variant['days']} روز",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 مشاهده سبد خرید", callback_data="go_to_cart")],
            [InlineKeyboardButton("🔙 ادامه خرید", callback_data="back_to_config")]
        ])
    )


@log_error
async def back_to_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_config_products(update, context)


@log_error
async def go_to_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await show_cart(update, context)

# ====================================================================
#  سبد خرید
# ====================================================================

@log_error
async def show_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id     = update.effective_user.id
    items       = db_get_cart(user_id)
    is_callback = update.callback_query is not None

    if not items:
        text = "🛒 سبد خرید شما خالی است."
        if is_callback:
            await update.callback_query.edit_message_text(text)
        else:
            await update.message.reply_text(text)
        return

    total = 0
    lines = ["🛒 <b>سبد خرید شما:</b>\n"]
    kbd   = []

    for item in items:
        subtotal = item["price"] * item["quantity"]
        total   += subtotal
        lines.append(
            f"• {item['product_name']} — {item['variant_name']}\n"
            f"  {item['quantity']} × {format_price(item['price'])} = <b>{format_price(subtotal)}</b>"
        )
        kbd.append([InlineKeyboardButton(f"🗑 حذف: {item['variant_name']}", callback_data=f"rmcart:{item['cart_id']}")])

    lines.append(f"\n💰 <b>جمع کل: {format_price(total)}</b>")

    coupon_code = context.user_data.get("coupon_code")
    if coupon_code:
        coupon = db_get_coupon(coupon_code)
        if coupon:
            discount = int(total * coupon["discount_value"] / 100) if coupon["discount_type"] == "percent" else min(coupon["discount_value"], total)
            lines.append(f"🎫 تخفیف ({coupon_code}): <b>−{format_price(discount)}</b>")
            lines.append(f"💳 قابل پرداخت: <b>{format_price(total - discount)}</b>")

    user_row = get_user_by_id(user_id)
    if user_row:
        lines.append(f"\n👛 موجودی کیف پول: <b>{format_price(user_row['wallet_balance'])}</b>")

    kbd.append([InlineKeyboardButton("🎫 اعمال کد تخفیف", callback_data="apply_coupon")])
    kbd.append([InlineKeyboardButton("✅ پرداخت از کیف پول", callback_data="checkout")])
    kbd.append([InlineKeyboardButton("🗑 خالی کردن سبد", callback_data="clearcart")])

    text = "\n".join(lines)
    if is_callback:
        await update.callback_query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                                      reply_markup=InlineKeyboardMarkup(kbd))
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML,
                                        reply_markup=InlineKeyboardMarkup(kbd))


@log_error
async def remove_cart_item_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        cart_id = int(query.data.split(":")[1])
        db_remove_cart_item(cart_id)
        await query.answer("🗑 حذف شد")
        await show_cart(update, context)
    except Exception as e:
        logger.error(f"خطا در حذف آیتم: {e}")
        await query.answer("❌ خطا در حذف", show_alert=True)


@log_error
async def clear_cart_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    db_clear_cart(query.from_user.id)
    await query.answer("سبد خرید خالی شد")
    await query.edit_message_text("🗑 سبد خرید شما خالی شد.")

# ====================================================================
#  کوپن
# ====================================================================

@log_error
async def apply_coupon_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🎫 کد تخفیف خود را وارد کنید:")
    return CHECKOUT_COUPON


@log_error
async def apply_coupon_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code   = update.message.text.strip().upper()
    coupon = db_get_coupon(code)

    if not coupon or not coupon["is_active"] or coupon["used_count"] >= coupon["usage_limit"]:
        await update.message.reply_text("❌ کد تخفیف نامعتبر یا منقضی شده است.")
        return ConversationHandler.END

    if coupon["expires_at"] and datetime.now() > datetime.fromisoformat(coupon["expires_at"]):
        await update.message.reply_text("❌ کد تخفیف منقضی شده است.")
        return ConversationHandler.END

    context.user_data["coupon_code"] = code
    await update.message.reply_text(
        "✅ کد تخفیف اعمال شد!\nبرای مشاهده سبد خرید، دکمه 🛒 سبد خرید را بزنید.",
        reply_markup=main_menu_keyboard(update.effective_user.id)
    )
    return ConversationHandler.END

# ====================================================================
#  ثبت سفارش — پرداخت از کیف پول
# ====================================================================

@log_error
async def checkout_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    items   = db_get_cart(user_id)

    if not items:
        await query.edit_message_text("🛒 سبد خرید خالی است.")
        return

    for item in items:
        if item["quantity"] > item["stock"]:
            await query.edit_message_text(
                f"⚠️ موجودی <b>{item['product_name']} — {item['variant_name']}</b> کافی نیست.\n"
                f"موجودی انبار: {item['stock']}",
                parse_mode=ParseMode.HTML
            )
            return

    user    = query.from_user
    user_row = get_user_by_id(user_id)
    referrer_id = user_row["referrer_id"] if user_row else None

    total    = sum(i["price"] * i["quantity"] for i in items)
    coupon_code = context.user_data.get("coupon_code")
    discount = 0
    if coupon_code:
        coupon = db_get_coupon(coupon_code)
        if coupon and coupon["is_active"]:
            if coupon["discount_type"] == "percent":
                discount = int(total * coupon["discount_value"] / 100)
            else:
                discount = min(coupon["discount_value"], total)
    final_price = total - discount

    wallet = user_row["wallet_balance"] if user_row else 0
    if wallet < final_price:
        shortage = final_price - wallet
        await query.edit_message_text(
            f"⚠️ <b>موجودی کیف پول کافی نیست</b>\n\n"
            f"💰 مبلغ سفارش: <b>{format_price(final_price)}</b>\n"
            f"👛 موجودی شما: <b>{format_price(wallet)}</b>\n"
            f"📉 کمبود: <b>{format_price(shortage)}</b>\n\n"
            f"👥 با دعوت دوستان کیف پول خود را شارژ کنید!\n"
            f"هر دعوت = <b>10,000 تومان</b> 🎁",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👥 لینک دعوت من", callback_data="show_referral_link")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="go_to_cart")]
            ])
        )
        return

    order_id, final_price = db_create_order(
        user_id=user_id,
        username=user.username or "",
        full_name=user.first_name,
        cart_items=items,
        coupon_code=coupon_code,
        referrer_id=referrer_id
    )
    context.user_data.pop("coupon_code", None)

    success = deduct_wallet(user_id, final_price)
    if not success:
        await query.edit_message_text("❌ خطا در کسر از کیف پول. لطفاً دوباره امتحان کنید.")
        return

    db_update_order_payment_status(order_id, "paid")
    db_update_order_status(order_id, "confirmed")

    context.user_data["order_id"] = order_id

    await query.edit_message_text(
        f"✅ <b>سفارش #{order_id} با موفقیت ثبت شد!</b>\n\n"
        f"💰 مبلغ پرداخت‌شده: <b>{format_price(final_price)}</b>\n"
        f"📦 در حال آماده‌سازی کانفیگ...\n\n"
        f"⏳ لطفاً چند لحظه صبر کنید.",
        parse_mode=ParseMode.HTML
    )

    order_items = db_get_order_items(order_id)
    if order_items:
        first_item = order_items[0]
        variant    = db_get_variant_by_name(first_item["variant_name"])
        if variant:
            remark      = f"user{user_id}_{order_id}"
            config_link = None
            try:
                config_link = create_config_on_panel(remark, variant["volume_gb"], variant["days"])
            except Exception as e:
                logger.error(f"خطا در ساخت کانفیگ: {e}")

            if config_link:
                db_update_order_config(order_id, config_link)
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🎉 <b>کانفیگ سفارش #{order_id} آماده است!</b>\n\n"
                         f"🔗 لینک کانفیگ:\n<code>{config_link}</code>\n\n"
                         f"📱 این لینک را در اپ کلاینت خود وارد کنید.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=main_menu_keyboard(user_id)
                )
            else:
                admin_msg = (
                    f"⚠️ <b>کانفیگ خودکار ناموفق</b>\n\n"
                    f"🧾 سفارش: #{order_id}\n"
                    f"👤 کاربر: {user.first_name} (ID: {user_id})\n"
                    f"📦 محصول: {first_item['product_name']}\n"
                    f"📊 حجم: {variant['volume_gb']}GB / {variant['days']} روز"
                )
                kbd = InlineKeyboardMarkup([[
                    InlineKeyboardButton("📤 ارسال دستی کانفیگ",
                                         callback_data=f"manual_send_config:{user_id}:{order_id}")
                ]])
                for admin_id in ADMIN_IDS:
                    try:
                        await context.bot.send_message(chat_id=admin_id, text=admin_msg,
                                                       parse_mode=ParseMode.HTML, reply_markup=kbd)
                    except Exception:
                        pass

                await context.bot.send_message(
                    chat_id=user_id,
                    text="⚙️ کانفیگ شما در حال آماده‌سازی است.\n"
                         "📩 ظرف چند دقیقه برای شما ارسال خواهد شد.",
                    reply_markup=main_menu_keyboard(user_id)
                )

# ====================================================================
#  ارسال دستی کانفیگ توسط ادمین
# ====================================================================

@log_error
async def manual_send_config_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    try:
        _, user_id, order_id = query.data.split(":")
        user_id, order_id = int(user_id), int(order_id)
    except (ValueError, IndexError):
        await query.answer("❌ خطا در داده‌ها", show_alert=True)
        return

    await query.answer()
    await query.edit_message_text(
        f"✍️ لطفاً لینک کانفیگ را برای کاربر {user_id} (سفارش #{order_id}) ارسال کنید:\n\n"
        f"⚠️ لینک باید با <code>vless://</code> یا <code>vmess://</code> شروع شود."
        , parse_mode=ParseMode.HTML
    )
    context.user_data["manual_config_target"] = {"user_id": user_id, "order_id": order_id}
    return MANUAL_CONFIG


@log_error
async def manual_config_send_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = context.user_data.get("manual_config_target")
    if not target:
        await update.message.reply_text("❌ خطا: مقصدی یافت نشد.")
        return ConversationHandler.END

    user_id, order_id = target["user_id"], target["order_id"]
    config_link = update.message.text.strip()

    if not config_link.startswith(("vless://", "vmess://", "trojan://")):
        await update.message.reply_text("⚠️ لینک معتبر نیست. دوباره ارسال کنید.")
        return MANUAL_CONFIG

    db_update_order_config(order_id, config_link)

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=f"🎉 <b>کانفیگ سفارش #{order_id} آماده است!</b>\n\n"
                 f"🔗 لینک کانفیگ:\n<code>{config_link}</code>\n\n"
                 f"📱 این لینک را در اپ کلاینت خود وارد کنید.",
            parse_mode=ParseMode.HTML
        )
        await update.message.reply_text(f"✅ کانفیگ با موفقیت به کاربر {user_id} ارسال شد.",
                                        reply_markup=admin_menu_keyboard())
    except Exception as e:
        logger.error(f"خطا در ارسال به کاربر: {e}")
        await update.message.reply_text(f"❌ خطا در ارسال به کاربر:\n{e}")

    context.user_data.pop("manual_config_target", None)
    return ConversationHandler.END

# ====================================================================
#  سفارشات من
# ====================================================================

@log_error
async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    orders  = db_get_user_orders(user_id)

    if not orders:
        await update.message.reply_text(
            "📦 شما هنوز هیچ سفارشی ثبت نکرده‌اید.\n\n"
            "برای خرید کانفیگ، روی 🛍 خرید کانفیگ بزنید."
        )
        return

    for order in orders:
        items = db_get_order_items(order["id"])
        items_text = "\n".join([
            f"  • {it['product_name']} — {it['variant_name']} × {it['quantity']}"
            for it in items
        ])
        text = (
            f"🧾 <b>سفارش #{order['id']}</b>\n"
            f"📅 تاریخ: {order['created_at'][:10]}\n"
            f"💰 مبلغ: <b>{format_price(order['final_price'])}</b>\n"
            f"📌 وضعیت: {STATUS_LABELS.get(order['status'], order['status'])}\n"
            f"💳 پرداخت: {PAYMENT_STATUS_LABELS.get(order['payment_status'], order['payment_status'])}\n\n"
            f"🛍 اقلام:\n{items_text}"
        )
        kbd = None
        if order["config_link"]:
            text += f"\n\n🔗 <b>کانفیگ شما:</b>\n<code>{order['config_link']}</code>"
            kbd = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 ارسال مجدد کانفیگ", callback_data=f"resend_config:{order['id']}")
            ]])
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kbd)


@log_error
async def resend_config_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        order_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ خطا در شناسایی سفارش.")
        return

    order = db_get_order(order_id)
    if not order:
        await query.edit_message_text("❌ سفارش یافت نشد.")
        return
    if order["user_id"] != query.from_user.id:
        await query.answer("⛔ این سفارش متعلق به شما نیست.", show_alert=True)
        return
    if not order["config_link"]:
        await query.edit_message_text(
            "⚠️ کانفیگ هنوز برای این سفارش آماده نشده.\n"
            "لطفاً صبر کنید یا با پشتیبانی تماس بگیرید."
        )
        return

    await context.bot.send_message(
        chat_id=query.from_user.id,
        text=f"🔄 <b>ارسال مجدد کانفیگ #{order_id}:</b>\n\n"
             f"<code>{order['config_link']}</code>\n\n"
             f"📱 این لینک را در اپ کلاینت خود وارد کنید.",
        parse_mode=ParseMode.HTML
    )
    await query.edit_message_text("✅ کانفیگ مجدداً برای شما ارسال شد.")

# ====================================================================
#  کیف پول و رفرال
# ====================================================================

@log_error
async def show_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = update.effective_user.id
    user_row = get_user_by_id(user_id)
    if not user_row:
        await update.message.reply_text("❌ کاربر یافت نشد.")
        return

    bot_username = context.bot.username
    # لینک دعوت با user_id خود کاربر
    ref_link = f"https://t.me/{bot_username}?start={user_id}"

    with closing(db.get_conn()) as conn:
        ref_count = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE referrer_id=?", (user_id,)
        ).fetchone()["c"]

    text = (
        f"💰 <b>کیف پول شما</b>\n\n"
        f"👛 موجودی: <b>{format_price(user_row['wallet_balance'])}</b>\n\n"
        f"──────────────────\n"
        f"👥 <b>سیستم دعوت</b>\n"
        f"🔗 لینک اختصاصی:\n<code>{ref_link}</code>\n\n"
        f"✅ دعوت‌های موفق: <b>{ref_count}</b>\n"
        f"🎁 پاداش هر دعوت: <b>{format_price(REFERRAL_BONUS_AMOUNT)}</b>\n\n"
        f"📌 با هر دعوت موفق، بلافاصله {format_price(REFERRAL_BONUS_AMOUNT)} به کیف پول شما اضافه می‌شود!"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


@log_error
async def show_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = update.effective_user.id
    user_row = get_user_by_id(user_id)
    if not user_row:
        await update.message.reply_text("❌ کاربر یافت نشد.")
        return

    with closing(db.get_conn()) as conn:
        ref_count = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE referrer_id=?", (user_id,)
        ).fetchone()["c"]

    bot_username = context.bot.username
    # لینک دعوت با user_id خود کاربر
    ref_link = f"https://t.me/{bot_username}?start={user_id}"
    earned   = ref_count * REFERRAL_BONUS_AMOUNT

    text = (
        f"👥 <b>دعوت از دوستان</b>\n\n"
        f"🔗 لینک اختصاصی شما:\n<code>{ref_link}</code>\n\n"
        f"📊 آمار شما:\n"
        f"  • دعوت‌های موفق: <b>{ref_count} نفر</b>\n"
        f"  • کل پاداش دریافتی: <b>{format_price(earned)}</b>\n\n"
        f"🎁 <b>به ازای هر دعوت:</b>\n"
        f"  ✅ شما {format_price(REFERRAL_BONUS_AMOUNT)} دریافت می‌کنید\n\n"
        f"💡 لینک را برای دوستانتان ارسال کنید و با هر دعوت کانفیگ رایگان بگیرید!"
    )

    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 اشتراک‌گذاری لینک",
                              switch_inline_query=f"🚀 با این لینک ثبت‌نام کن و کانفیگ رایگان بگیر!\n{ref_link}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_to_menu")]
    ])
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kbd)


@log_error
async def show_referral_link_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    user_id  = query.from_user.id
    user_row = get_user_by_id(user_id)
    if not user_row:
        await query.answer("❌ خطا", show_alert=True)
        return

    await query.answer()
    # لینک دعوت با user_id خود کاربر
    ref_link = f"https://t.me/{context.bot.username}?start={user_id}"
    await query.edit_message_text(
        f"🔗 <b>لینک دعوت اختصاصی شما:</b>\n\n<code>{ref_link}</code>\n\n"
        f"🎁 هر دعوت = <b>{format_price(REFERRAL_BONUS_AMOUNT)}</b> به کیف پول شما!",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 بازگشت", callback_data="go_to_cart")]
        ])
    )

# ====================================================================
#  پشتیبانی
# ====================================================================

@log_error
async def support_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 <b>ارسال تیکت پشتیبانی</b>\n\n"
        "لطفاً موضوع مشکل خود را وارد کنید:",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove()
    )
    return TICKET_SUBJECT


@log_error
async def support_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subject = update.message.text.strip()
    if len(subject) < 3:
        await update.message.reply_text("❌ موضوع باید حداقل ۳ کاراکتر باشد. دوباره وارد کنید:")
        return TICKET_SUBJECT
    context.user_data["ticket_subject"] = subject
    await update.message.reply_text("✍️ حالا توضیحات کامل مشکل خود را بنویسید:")
    return TICKET_MESSAGE


@log_error
async def support_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subject = context.user_data.get("ticket_subject", "بدون موضوع")
    message = update.message.text.strip()
    if len(message) < 10:
        await update.message.reply_text("❌ پیام باید حداقل ۱۰ کاراکتر باشد.")
        return TICKET_MESSAGE

    user      = update.effective_user
    ticket_id = db_create_ticket(user.id, subject, message)

    await update.message.reply_text(
        f"✅ <b>تیکت #{ticket_id} ثبت شد.</b>\n\n"
        f"📌 موضوع: {subject}\n\n"
        f"⏳ تیم پشتیبانی به‌زودی پاسخ خواهد داد.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(user.id)
    )

    admin_text = (
        f"📩 <b>تیکت جدید #{ticket_id}</b>\n\n"
        f"👤 کاربر: {user.first_name} (@{user.username or '—'})\n"
        f"🆔 آیدی: <code>{user.id}</code>\n"
        f"📌 موضوع: {subject}\n\n"
        f"📝 پیام:\n{message}"
    )
    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ پاسخ به تیکت", callback_data=f"reply_ticket:{ticket_id}")]
    ])
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=admin_text,
                                           parse_mode=ParseMode.HTML, reply_markup=kbd)
        except Exception as e:
            logger.error(f"خطا در ارسال تیکت به ادمین: {e}")

    return ConversationHandler.END

# ====================================================================
#  پاسخ ادمین به تیکت
# ====================================================================

@log_error
async def admin_reply_ticket_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    try:
        ticket_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.answer("❌ خطا در داده‌ها", show_alert=True)
        return

    context.user_data["reply_ticket_id"] = ticket_id
    await query.answer()
    await query.edit_message_text(f"✍️ پاسخ خود را برای تیکت #{ticket_id} بنویسید:")
    return TICKET_RESPONSE


@log_error
async def admin_reply_ticket_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ticket_id = context.user_data.get("reply_ticket_id")
    if not ticket_id:
        await update.message.reply_text("❌ خطا.")
        return ConversationHandler.END

    response = update.message.text.strip()
    if not response:
        await update.message.reply_text("❌ پاسخ نمی‌تواند خالی باشد.")
        return TICKET_RESPONSE

    db_update_ticket_response(ticket_id, response)
    ticket = db_get_ticket(ticket_id)

    if ticket:
        try:
            await context.bot.send_message(
                chat_id=ticket["user_id"],
                text=f"📩 <b>پاسخ تیکت #{ticket_id}</b>\n\n{response}\n\n"
                     f"برای بستن تیکت: <code>/close_ticket {ticket_id}</code>",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"خطا در ارسال پاسخ به کاربر: {e}")

    await update.message.reply_text("✅ پاسخ ارسال شد.", reply_markup=admin_menu_keyboard())
    return ConversationHandler.END


@log_error
async def close_ticket_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بستن تیکت توسط کاربر یا ادمین"""
    if not context.args:
        await update.message.reply_text(
            "📌 استفاده: /close_ticket <شماره تیکت>\nمثال: /close_ticket 5"
        )
        return

    try:
        ticket_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ شماره تیکت باید عدد باشد.")
        return

    ticket = db_get_ticket(ticket_id)
    if not ticket:
        await update.message.reply_text("❌ تیکت یافت نشد.")
        return

    if ticket["user_id"] != update.effective_user.id and not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ شما مجاز به بستن این تیکت نیستید.")
        return

    if ticket["status"] == "closed":
        await update.message.reply_text(f"⚠️ تیکت #{ticket_id} قبلاً بسته شده است.")
        return

    db_close_ticket(ticket_id)
    await update.message.reply_text(f"✅ تیکت #{ticket_id} بسته شد.")

# ====================================================================
#  پنل مدیریت
# ====================================================================

@admin_only
@log_error
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚙️ <b>پنل مدیریت</b>", parse_mode=ParseMode.HTML,
                                    reply_markup=admin_menu_keyboard())


@admin_only
@log_error
async def admin_back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔙 بازگشت به منوی اصلی.",
                                    reply_markup=main_menu_keyboard(update.effective_user.id))

# ---- مدیریت موجودی ----

@admin_only
@log_error
async def stock_manage_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    variants = db_get_all_variants()
    if not variants:
        await update.message.reply_text("📦 هیچ محصولی موجود نیست.")
        return ConversationHandler.END

    kbd = [
        [InlineKeyboardButton(
            f"{v['product_name']} — {v['variant_name']} (موجودی: {v['stock']})",
            callback_data=f"stock_sel:{v['id']}"
        )]
        for v in variants
    ]
    kbd.append([InlineKeyboardButton("❌ انصراف", callback_data="cancel_stock")])

    await update.message.reply_text(
        "📦 <b>مدیریت موجودی</b>\n\nمحصول مورد نظر را انتخاب کنید:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(kbd)
    )
    return STOCK_MANAGE_SELECT


@admin_only
@log_error
async def stock_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        variant_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ خطا.")
        return ConversationHandler.END

    context.user_data["stock_variant_id"] = variant_id
    variant = db_get_variant(variant_id)
    if not variant:
        await query.edit_message_text("❌ محصول یافت نشد.")
        return ConversationHandler.END

    await query.edit_message_text(
        f"📦 <b>{variant['variant_name']}</b>\n"
        f"موجودی فعلی: {variant['stock']}\n\n"
        f"موجودی جدید را وارد کنید:",
        parse_mode=ParseMode.HTML
    )
    return STOCK_MANAGE_NEW


@admin_only
@log_error
async def stock_set_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        new_stock = int(update.message.text.strip())
        if new_stock < 0:
            await update.message.reply_text("❌ موجودی نمی‌تواند منفی باشد.")
            return STOCK_MANAGE_NEW
    except ValueError:
        await update.message.reply_text("❌ لطفاً یک عدد وارد کنید.")
        return STOCK_MANAGE_NEW

    variant_id = context.user_data.get("stock_variant_id")
    if not variant_id:
        await update.message.reply_text("❌ خطا.")
        return ConversationHandler.END

    db_update_variant_stock(variant_id, new_stock)
    variant = db_get_variant(variant_id)
    await update.message.reply_text(
        f"✅ موجودی <b>{variant['variant_name']}</b> به {new_stock} به‌روز شد.",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_menu_keyboard()
    )
    context.user_data.pop("stock_variant_id", None)
    return ConversationHandler.END


@log_error
async def cancel_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("↩️ عملیات لغو شد.")
    return ConversationHandler.END

# ---- افزودن دسته ----

@admin_only
@log_error
async def add_category_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📂 نام دسته‌بندی جدید را وارد کنید:",
                                    reply_markup=ReplyKeyboardRemove())
    return ADD_PRODUCT_CATEGORY


@admin_only
@log_error
async def add_category_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("❌ نام نمی‌تواند خالی باشد.")
        return ADD_PRODUCT_CATEGORY
    try:
        db_add_category(name)
        await update.message.reply_text(f"✅ دسته «{name}» اضافه شد.",
                                        reply_markup=admin_menu_keyboard())
    except sqlite3.IntegrityError:
        await update.message.reply_text("⚠️ این دسته قبلاً وجود دارد.",
                                        reply_markup=admin_menu_keyboard())
    return ConversationHandler.END

# ---- افزودن محصول ----

@admin_only
@log_error
async def add_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    categories = db_get_categories()
    if not categories:
        await update.message.reply_text("⚠️ ابتدا دسته‌بندی بسازید.")
        return ConversationHandler.END

    kbd = [[InlineKeyboardButton(cat["name"], callback_data=f"addprod_cat:{cat['id']}")] for cat in categories]
    await update.message.reply_text("📦 دسته‌بندی محصول را انتخاب کنید:",
                                    reply_markup=InlineKeyboardMarkup(kbd))
    return ADD_PRODUCT_CATEGORY


@admin_only
@log_error
async def add_product_category_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        cat_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ خطا.")
        return ConversationHandler.END
    context.user_data["new_product_cat"] = cat_id
    await query.edit_message_text("📝 نام محصول را وارد کنید:")
    return ADD_PRODUCT_NAME


@admin_only
@log_error
async def add_product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("❌ نام نمی‌تواند خالی باشد.")
        return ADD_PRODUCT_NAME
    context.user_data["new_product_name"] = name
    await update.message.reply_text("📝 توضیحات محصول (یا '-' برای رد):")
    return ADD_PRODUCT_DESCRIPTION


@admin_only
@log_error
async def add_product_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    desc = update.message.text.strip()
    if desc == "-":
        desc = ""
    db_add_product(context.user_data["new_product_cat"], context.user_data["new_product_name"], desc)
    await update.message.reply_text(
        f"✅ محصول «{context.user_data['new_product_name']}» اضافه شد.",
        reply_markup=admin_menu_keyboard()
    )
    return ConversationHandler.END

# ---- افزودن واریانت ----

@admin_only
@log_error
async def add_variant_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with closing(db.get_conn()) as conn:
        products = conn.execute("SELECT id, name FROM products WHERE is_active=1").fetchall()
    if not products:
        await update.message.reply_text("⚠️ هیچ محصولی موجود نیست.")
        return ConversationHandler.END
    kbd = [[InlineKeyboardButton(p["name"], callback_data=f"variant_prod:{p['id']}")] for p in products]
    await update.message.reply_text("📦 محصول را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(kbd))
    return ADD_VARIANT_PRODUCT


@admin_only
@log_error
async def add_variant_product_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        product_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ خطا.")
        return ConversationHandler.END
    context.user_data["variant_product_id"] = product_id
    await query.edit_message_text("📝 نام واریانت را وارد کنید (مثال: ۱۰ گیگابایت / ۳۰ روز):")
    return ADD_VARIANT_NAME


@admin_only
@log_error
async def add_variant_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("❌ نام نمی‌تواند خالی باشد.")
        return ADD_VARIANT_NAME
    context.user_data["variant_name"] = name
    await update.message.reply_text("💰 قیمت (تومان — فقط عدد):")
    return ADD_VARIANT_PRICE


@admin_only
@log_error
async def add_variant_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = int(update.message.text.strip().replace(",", ""))
        if price < 0:
            raise ValueError
        context.user_data["variant_price"] = price
        await update.message.reply_text("📊 حجم (گیگابایت):")
        return ADD_VARIANT_VOLUME
    except ValueError:
        await update.message.reply_text("❌ عدد معتبر وارد کنید.")
        return ADD_VARIANT_PRICE


@admin_only
@log_error
async def add_variant_volume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        volume = int(update.message.text.strip())
        if volume < 1:
            raise ValueError
        context.user_data["variant_volume"] = volume
        await update.message.reply_text("📅 مدت اعتبار (روز):")
        return ADD_VARIANT_DAYS
    except ValueError:
        await update.message.reply_text("❌ عدد معتبر (حداقل ۱) وارد کنید.")
        return ADD_VARIANT_VOLUME


@admin_only
@log_error
async def add_variant_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        days = int(update.message.text.strip())
        if days < 1:
            raise ValueError
        context.user_data["variant_days"] = days
        await update.message.reply_text("📦 موجودی انبار:")
        return ADD_VARIANT_STOCK
    except ValueError:
        await update.message.reply_text("❌ عدد معتبر (حداقل ۱) وارد کنید.")
        return ADD_VARIANT_DAYS


@admin_only
@log_error
async def add_variant_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        stock = int(update.message.text.strip())
        if stock < 0:
            raise ValueError
        context.user_data["variant_stock"] = stock
        await update.message.reply_text("🖼 عکس محصول ارسال کنید یا '-' برای رد:")
        return ADD_VARIANT_PHOTO
    except ValueError:
        await update.message.reply_text("❌ عدد معتبر وارد کنید.")
        return ADD_VARIANT_STOCK


@admin_only
@log_error
async def add_variant_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_id = None
    if update.message.photo:
        photo_id = update.message.photo[-1].file_id
    elif update.message.text and update.message.text.strip() == "-":
        pass
    else:
        await update.message.reply_text("❌ عکس ارسال کنید یا '-' بنویسید.")
        return ADD_VARIANT_PHOTO

    db_add_variant(
        context.user_data["variant_product_id"],
        context.user_data["variant_name"],
        context.user_data["variant_price"],
        context.user_data["variant_stock"],
        context.user_data["variant_volume"],
        context.user_data["variant_days"],
        photo_id
    )
    await update.message.reply_text(
        f"✅ واریانت «{context.user_data['variant_name']}» اضافه شد.",
        reply_markup=admin_menu_keyboard()
    )
    return ConversationHandler.END

# ---- مدیریت کوپن ----

@admin_only
@log_error
async def admin_coupon_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    coupons = db_get_all_coupons()
    if coupons:
        lines = ["🎫 <b>کوپن‌های فعال:</b>\n"]
        for c in coupons:
            lines.append(
                f"• <code>{c['code']}</code> — {c['discount_type']} {c['discount_value']} "
                f"(استفاده: {c['used_count']}/{c['usage_limit']})"
            )
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)

    await update.message.reply_text("برای افزودن کوپن جدید، کد آن را وارد کنید (یا /cancel):")
    return COUPON_CODE


@admin_only
@log_error
async def coupon_get_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip().upper()
    if not code:
        await update.message.reply_text("❌ کد نمی‌تواند خالی باشد.")
        return COUPON_CODE
    if db_get_coupon(code):
        await update.message.reply_text("⚠️ این کد قبلاً وجود دارد.")
        return COUPON_CODE
    context.user_data["coupon_code"] = code
    await update.message.reply_text("نوع تخفیف: <b>percent</b> یا <b>fixed</b>", parse_mode=ParseMode.HTML)
    return COUPON_TYPE


@admin_only
@log_error
async def coupon_get_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dtype = update.message.text.strip().lower()
    if dtype not in ["percent", "fixed"]:
        await update.message.reply_text("❌ لطفاً percent یا fixed وارد کنید.")
        return COUPON_TYPE
    context.user_data["coupon_type"] = dtype
    hint = "بین ۱ تا ۱۰۰" if dtype == "percent" else "مبلغ به تومان"
    await update.message.reply_text(f"مقدار تخفیف ({hint}):")
    return COUPON_VALUE


@admin_only
@log_error
async def coupon_get_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        value = int(update.message.text.strip())
        if context.user_data["coupon_type"] == "percent" and not (1 <= value <= 100):
            await update.message.reply_text("❌ درصد باید بین ۱ تا ۱۰۰ باشد.")
            return COUPON_VALUE
        if value < 0:
            raise ValueError
        context.user_data["coupon_value"] = value
        await update.message.reply_text("حداقل مبلغ سفارش (یا ۰ برای بدون محدودیت):")
        return COUPON_MIN_ORDER
    except ValueError:
        await update.message.reply_text("❌ عدد معتبر وارد کنید.")
        return COUPON_VALUE


@admin_only
@log_error
async def coupon_get_min_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        min_order = int(update.message.text.strip())
        if min_order < 0:
            raise ValueError
        context.user_data["coupon_min_order"] = min_order
        await update.message.reply_text("تاریخ انقضا (YYYY-MM-DD) یا '-' برای بدون انقضا:")
        return COUPON_EXPIRY
    except ValueError:
        await update.message.reply_text("❌ عدد معتبر وارد کنید.")
        return COUPON_MIN_ORDER


@admin_only
@log_error
async def coupon_get_expiry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    expiry = None
    txt    = update.message.text.strip()
    if txt != "-":
        try:
            expiry = datetime.strptime(txt, "%Y-%m-%d").isoformat()
        except ValueError:
            await update.message.reply_text("❌ فرمت اشتباه. مثال: 2025-12-31 یا '-'")
            return COUPON_EXPIRY
    context.user_data["coupon_expiry"] = expiry
    await update.message.reply_text("تعداد دفعات استفاده (پیش‌فرض ۱):")
    return COUPON_LIMIT


@admin_only
@log_error
async def coupon_get_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        limit = max(1, int(update.message.text.strip()))
    except ValueError:
        limit = 1

    db_add_coupon(
        context.user_data["coupon_code"],
        context.user_data["coupon_type"],
        context.user_data["coupon_value"],
        context.user_data["coupon_min_order"],
        context.user_data["coupon_expiry"],
        limit
    )
    await update.message.reply_text(
        f"✅ کوپن <code>{context.user_data['coupon_code']}</code> اضافه شد.",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_menu_keyboard()
    )
    context.user_data.clear()
    return ConversationHandler.END

# ---- لیست محصولات ----

@admin_only
@log_error
async def admin_list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with closing(db.get_conn()) as conn:
        rows = conn.execute("""
            SELECT p.id AS product_id, p.name AS product_name,
                   c.name AS category_name,
                   v.id AS variant_id, v.variant_name, v.price, v.stock
            FROM products p
            JOIN categories c ON p.category_id = c.id
            LEFT JOIN product_variants v ON p.id = v.product_id
            WHERE p.is_active = 1
            ORDER BY c.name, p.name, v.variant_name
        """).fetchall()

    if not rows:
        await update.message.reply_text("📦 محصولی ثبت نشده.")
        return

    grouped = {}
    for row in rows:
        pid = row["product_id"]
        if pid not in grouped:
            grouped[pid] = {"name": row["product_name"], "category": row["category_name"], "variants": []}
        if row["variant_id"]:
            grouped[pid]["variants"].append(row)

    for pid, data in grouped.items():
        text = f"🛍 <b>{data['name']}</b> (دسته: {data['category']})\n"
        if data["variants"]:
            for v in data["variants"]:
                text += f"  • {v['variant_name']} — {format_price(v['price'])} | موجودی: {v['stock']}\n"
        else:
            text += "  ⚠️ بدون واریانت\n"
        kbd = InlineKeyboardMarkup([[InlineKeyboardButton("🗑 غیرفعال کردن", callback_data=f"delprod:{pid}")]])
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kbd)


@admin_only
@log_error
async def admin_delete_product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        product_id = int(query.data.split(":")[1])
        db_deactivate_product(product_id)
        await query.answer("✅ محصول غیرفعال شد")
        await query.edit_message_text("✅ محصول غیرفعال شد.")
    except Exception as e:
        logger.error(f"خطا در غیرفعال کردن محصول: {e}")
        await query.answer("❌ خطا", show_alert=True)

# ---- مدیریت سفارشات ----

@admin_only
@log_error
async def admin_show_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    orders = db_get_all_orders(limit=20)
    if not orders:
        await update.message.reply_text("🧾 سفارشی وجود ندارد.")
        return

    for order in orders:
        text = (
            f"🧾 <b>سفارش #{order['id']}</b>\n"
            f"👤 {order['full_name']} (@{order['username'] or '—'})\n"
            f"💰 {format_price(order['final_price'])} | تخفیف: {format_price(order['discount_amount'])}\n"
            f"📌 وضعیت: {STATUS_LABELS.get(order['status'], order['status'])}\n"
            f"💳 پرداخت: {PAYMENT_STATUS_LABELS.get(order['payment_status'], order['payment_status'])}\n"
            f"🔗 کانفیگ: {'✅ ارسال شده' if order['config_link'] else '❌ ارسال نشده'}"
        )
        kbd = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ تأیید", callback_data=f"ordstatus:{order['id']}:confirmed"),
            InlineKeyboardButton("📦 ارسال شد", callback_data=f"ordstatus:{order['id']}:shipped"),
            InlineKeyboardButton("❌ لغو", callback_data=f"ordstatus:{order['id']}:cancelled"),
        ]])
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kbd)


@admin_only
@log_error
async def order_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        _, order_id_str, new_status = query.data.split(":")
        order_id = int(order_id_str)
    except (ValueError, IndexError):
        await query.answer("❌ خطا", show_alert=True)
        return

    db_update_order_status(order_id, new_status)
    await query.answer(f"وضعیت به {STATUS_LABELS.get(new_status, new_status)} تغییر کرد.")

    order = db_get_order(order_id)
    if order:
        try:
            await context.bot.send_message(
                chat_id=order["user_id"],
                text=f"📌 وضعیت سفارش <b>#{order_id}</b>: {STATUS_LABELS.get(new_status, new_status)}",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"خطا در اطلاع‌رسانی کاربر: {e}")

    await query.edit_message_text(f"✅ وضعیت سفارش #{order_id} به‌روز شد.")


@admin_only
@log_error
async def admin_show_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tickets = db_get_tickets()
    if not tickets:
        await update.message.reply_text("📩 هیچ تیکتی وجود ندارد.")
        return

    for t in tickets:
        emoji = {"open": "🔴", "in_progress": "🟡", "closed": "🟢"}.get(t["status"], "⚪")
        status_text = {"open": "باز", "in_progress": "در حال بررسی", "closed": "بسته"}.get(t["status"], t["status"])
        text = (
            f"{emoji} <b>تیکت #{t['id']}</b> — {status_text}\n"
            f"👤 کاربر: <code>{t['user_id']}</code>\n"
            f"📌 موضوع: {t['subject']}\n"
            f"📝 {t['message'][:150]}{'...' if len(t['message']) > 150 else ''}"
        )
        kbd = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ پاسخ", callback_data=f"reply_ticket:{t['id']}")],
            [InlineKeyboardButton("✅ بستن تیکت", callback_data=f"close_ticket_admin:{t['id']}")]
        ])
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kbd)


@log_error
async def admin_close_ticket_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    try:
        ticket_id = int(query.data.split(":")[1])
    except (ValueError, IndexError):
        await query.answer("❌ خطا", show_alert=True)
        return

    db_close_ticket(ticket_id)
    await query.answer("✅ تیکت بسته شد.")
    await query.edit_message_text(f"✅ تیکت #{ticket_id} بسته شد.")


@admin_only
@log_error
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    s = db_get_stats()
    text = (
        f"📊 <b>آمار فروشگاه</b>\n\n"
        f"🧾 کل سفارشات: <b>{s['total_orders']}</b>\n"
        f"⏳ در انتظار بررسی: <b>{s['pending_orders']}</b>\n"
        f"💳 پرداخت شده: <b>{s['paid_orders']}</b>\n"
        f"💰 کل فروش: <b>{format_price(s['total_revenue'])}</b>\n\n"
        f"🛍 محصولات فعال: <b>{s['products_count']}</b>\n"
        f"👥 کاربران ثبت‌شده: <b>{s['users_count']}</b>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


@admin_only
@log_error
async def admin_users_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with closing(db.get_conn()) as conn:
        users = conn.execute("SELECT * FROM users ORDER BY id DESC LIMIT 50").fetchall()

    if not users:
        await update.message.reply_text("👥 هیچ کاربری ثبت‌نام نکرده.")
        return

    lines = ["👥 <b>آخرین ۵۰ کاربر:</b>\n"]
    for u in users:
        name = u["first_name"] or "—"
        uname = f"@{u['username']}" if u["username"] else "—"
        lines.append(
            f"• {name} ({uname}) | "
            f"کیف پول: {format_price(u['wallet_balance'])} | "
            f"کد: <code>{u['referral_code']}</code>"
        )

    full_text = "\n".join(lines)
    if len(full_text) > 4000:
        chunks = []
        chunk  = lines[0]
        for line in lines[1:]:
            if len(chunk) + len(line) + 1 > 4000:
                chunks.append(chunk)
                chunk = line
            else:
                chunk += "\n" + line
        chunks.append(chunk)
        for chunk in chunks:
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(full_text, parse_mode=ParseMode.HTML)

# ====================================================================
#  پیام همگانی ادمین
# ====================================================================

@admin_only
@log_error
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """شروع فرآیند ارسال پیام همگانی"""
    await update.message.reply_text(
        "📢 <b>ارسال پیام همگانی</b>\n\n"
        "لطفاً پیام متنی خود را برای ارسال به <b>همه کاربران</b> وارد کنید.\n"
        "برای لغو، از /cancel استفاده کنید.",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove()
    )
    return BROADCAST_MESSAGE


@admin_only
@log_error
async def broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ارسال پیام به همه کاربران و بازگشت به منوی ادمین"""
    message_text = update.message.text.strip()
    if not message_text:
        await update.message.reply_text("❌ پیام نمی‌تواند خالی باشد. لطفاً دوباره وارد کنید:")
        return BROADCAST_MESSAGE

    user_ids = get_all_user_ids()
    success_count = 0
    fail_count = 0

    await update.message.reply_text(f"⏳ در حال ارسال پیام به {len(user_ids)} کاربر...")

    for user_id in user_ids:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"📢 <b>پیام مدیریت:</b>\n\n{message_text}",
                parse_mode=ParseMode.HTML
            )
            success_count += 1
        except Exception as e:
            logger.warning(f"ارسال پیام همگانی به کاربر {user_id} ناموفق: {e}")
            fail_count += 1

    await update.message.reply_text(
        f"✅ ارسال پیام همگانی به پایان رسید.\n\n"
        f"📊 <b>گزارش:</b>\n"
        f"• موفق: {success_count}\n"
        f"• ناموفق: {fail_count}",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_menu_keyboard()
    )
    return ConversationHandler.END

# ====================================================================
#  منوی اصلی — بازگشت
# ====================================================================

@log_error
async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user  = query.from_user
    await context.bot.send_message(
        chat_id=user.id,
        text=f"🏠 منوی اصلی",
        reply_markup=main_menu_keyboard(user.id)
    )
    await query.edit_message_text("🏠 به منوی اصلی بازگشتید.")

# ====================================================================
#  main
# ====================================================================

def main():
    db._init_tables()
    db._init_default_products()

    app = Application.builder().token(BOT_TOKEN).build()

    # ── دستورات ──────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel_conversation))
    app.add_handler(CommandHandler("close_ticket", close_ticket_command))
    app.add_handler(CallbackQueryHandler(check_subscription, pattern="^check_sub$"))

    # ── مکالمات ──────────────────────────────────────────────────────

    # پیام همگانی
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📢 پیام همگانی$"), broadcast_start)],
        states={
            BROADCAST_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_send)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        per_message=False,
    ))

    # کوپن
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(apply_coupon_callback, pattern="^apply_coupon$")],
        states={CHECKOUT_COUPON: [MessageHandler(filters.TEXT & ~filters.COMMAND, apply_coupon_text)]},
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        per_message=False,
    ))

    # پشتیبانی
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🎫 پشتیبانی$"), support_start)],
        states={
            TICKET_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, support_subject)],
            TICKET_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, support_message)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        per_message=False,
    ))

    # پاسخ ادمین به تیکت
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_reply_ticket_callback, pattern=r"^reply_ticket:")],
        states={TICKET_RESPONSE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_reply_ticket_text)]},
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        per_message=False,
    ))

    # ارسال دستی کانفیگ
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(manual_send_config_callback, pattern=r"^manual_send_config:")],
        states={MANUAL_CONFIG: [MessageHandler(filters.TEXT & ~filters.COMMAND, manual_config_send_text)]},
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        per_message=False,
    ))

    # مدیریت موجودی
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📦 مدیریت موجودی$"), stock_manage_start)],
        states={
            STOCK_MANAGE_SELECT: [CallbackQueryHandler(stock_select_callback, pattern=r"^stock_sel:")],
            STOCK_MANAGE_NEW:    [MessageHandler(filters.TEXT & ~filters.COMMAND, stock_set_new)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conversation),
            CallbackQueryHandler(cancel_stock, pattern="^cancel_stock$")
        ],
        per_message=False,
    ))

    # افزودن دسته
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ افزودن دسته$"), add_category_start)],
        states={ADD_PRODUCT_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_category_save)]},
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        per_message=False,
    ))

    # افزودن محصول
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ افزودن محصول$"), add_product_start)],
        states={
            ADD_PRODUCT_CATEGORY:    [CallbackQueryHandler(add_product_category_chosen, pattern=r"^addprod_cat:")],
            ADD_PRODUCT_NAME:        [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_name)],
            ADD_PRODUCT_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_product_description)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        per_message=False,
    ))

    # افزودن واریانت
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ افزودن واریانت$"), add_variant_start)],
        states={
            ADD_VARIANT_PRODUCT: [CallbackQueryHandler(add_variant_product_chosen, pattern=r"^variant_prod:")],
            ADD_VARIANT_NAME:    [MessageHandler(filters.TEXT & ~filters.COMMAND, add_variant_name)],
            ADD_VARIANT_PRICE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, add_variant_price)],
            ADD_VARIANT_VOLUME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, add_variant_volume)],
            ADD_VARIANT_DAYS:    [MessageHandler(filters.TEXT & ~filters.COMMAND, add_variant_days)],
            ADD_VARIANT_STOCK:   [MessageHandler(filters.TEXT & ~filters.COMMAND, add_variant_stock)],
            ADD_VARIANT_PHOTO:   [MessageHandler((filters.PHOTO | filters.TEXT) & ~filters.COMMAND, add_variant_photo)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        per_message=False,
    ))

    # مدیریت کوپن
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🎫 مدیریت کوپن‌ها$"), admin_coupon_menu)],
        states={
            COUPON_CODE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, coupon_get_code)],
            COUPON_TYPE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, coupon_get_type)],
            COUPON_VALUE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, coupon_get_value)],
            COUPON_MIN_ORDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, coupon_get_min_order)],
            COUPON_EXPIRY:    [MessageHandler(filters.TEXT & ~filters.COMMAND, coupon_get_expiry)],
            COUPON_LIMIT:     [MessageHandler(filters.TEXT & ~filters.COMMAND, coupon_get_limit)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        per_message=False,
    ))

    # ── دکمه‌های منوی اصلی ──────────────────────────────────────────
    app.add_handler(MessageHandler(filters.Regex("^🛍 خرید کانفیگ$"),       show_config_products))
    app.add_handler(MessageHandler(filters.Regex("^🛒 سبد خرید$"),           show_cart))
    app.add_handler(MessageHandler(filters.Regex("^📦 سفارشات من$"),          my_orders))
    app.add_handler(MessageHandler(filters.Regex("^👥 دعوت از دوستان$"),      show_referral))
    app.add_handler(MessageHandler(filters.Regex("^💰 کیف پول$"),             show_wallet))

    # ── دکمه‌های ادمین ──────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.Regex("^⚙️ پنل مدیریت$"),          admin_panel))
    app.add_handler(MessageHandler(filters.Regex("^🔙 بازگشت به منوی اصلی$"), admin_back_to_main))
    app.add_handler(MessageHandler(filters.Regex("^📋 لیست محصولات$"),         admin_list_products))
    app.add_handler(MessageHandler(filters.Regex("^🧾 مدیریت سفارشات$"),       admin_show_orders))
    app.add_handler(MessageHandler(filters.Regex("^📩 تیکت‌های پشتیبانی$"),    admin_show_tickets))
    app.add_handler(MessageHandler(filters.Regex("^📊 آمار فروش$"),             admin_stats))
    app.add_handler(MessageHandler(filters.Regex("^👥 کاربران$"),               admin_users_list))

    # ── کالبک‌ها ────────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(buy_config_callback,          pattern=r"^buy_config:"))
    app.add_handler(CallbackQueryHandler(back_to_config,               pattern=r"^back_to_config$"))
    app.add_handler(CallbackQueryHandler(go_to_cart,                   pattern=r"^go_to_cart$"))
    app.add_handler(CallbackQueryHandler(back_to_main_menu,            pattern=r"^back_to_menu$"))
    app.add_handler(CallbackQueryHandler(remove_cart_item_callback,    pattern=r"^rmcart:"))
    app.add_handler(CallbackQueryHandler(clear_cart_callback,          pattern=r"^clearcart$"))
    app.add_handler(CallbackQueryHandler(checkout_now,                 pattern=r"^checkout$"))
    app.add_handler(CallbackQueryHandler(order_status_callback,        pattern=r"^ordstatus:"))
    app.add_handler(CallbackQueryHandler(admin_delete_product_callback,pattern=r"^delprod:"))
    app.add_handler(CallbackQueryHandler(admin_close_ticket_callback,  pattern=r"^close_ticket_admin:"))
    app.add_handler(CallbackQueryHandler(resend_config_callback,       pattern=r"^resend_config:"))
    app.add_handler(CallbackQueryHandler(show_referral_link_callback,  pattern=r"^show_referral_link$"))

    logger.info("✅ ربات با موفقیت راه‌اندازی شد.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
