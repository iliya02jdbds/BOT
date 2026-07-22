import os
import re
import math
import time
import asyncio
import sqlite3
import logging
from datetime import datetime

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
    MessageHandler, ConversationHandler, filters, ApplicationHandlerStop,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# 🎨 سازگاری دکمه‌های رنگی: اگه کتابخانه‌ی نصب‌شده پارامتر style رو پشتیبانی کنه (مثل الان)،
# دکمه‌ها همون‌طور شیشه‌ای/رنگی می‌مونن؛ اگه یه روز روی کتابخانه‌ی استاندارد اجرا بشه،
# به‌جای کرش کردن، style بی‌سروصدا نادیده گرفته میشه.
_TgInlineKeyboardButton = InlineKeyboardButton
_BTN_STYLE_SUPPORTED = None


def InlineKeyboardButton(*args, **kwargs):  # noqa: F811 - عمداً جایگزین نسخه‌ی کتابخانه میشه
    global _BTN_STYLE_SUPPORTED
    if "style" in kwargs and _BTN_STYLE_SUPPORTED is not False:
        try:
            btn = _TgInlineKeyboardButton(*args, **kwargs)
            _BTN_STYLE_SUPPORTED = True
            return btn
        except TypeError:
            _BTN_STYLE_SUPPORTED = False
    if _BTN_STYLE_SUPPORTED is False:
        kwargs.pop("style", None)
    return _TgInlineKeyboardButton(*args, **kwargs)

# ==================== تنظیمات ====================
TOKEN = os.environ["BOT_TOKEN"]
# ⚠️ توکن بات رو توی همین فایل به صورت متن‌باز گذاشتی. چون این فایل ممکنه دست کس دی بیفته،
# پیشنهاد می‌کنم از @BotFather دستور /revoke بزنی و یه توکن جدید بگیری.

# 👑 مالکان اصلی ربات (این‌ها همیشه دسترسی کامل دارن و هیچ‌کس نمی‌تونه حذفشون کنه).
# برای اضافه کردن مالک دوم، فقط آیدی عددیش رو داخل همین ست بنویس:
OWNER_IDS = {
    7300334271,
    # 123456789,   # <- آیدی عددی مالک دوم رو اینجا جایگزین کن و کامنتش رو بردار
}

BOT_NAME = "EKSODI VPN💫"

# 🔒 عضویت اجباری در کانال قبل از استفاده از بات
REQUIRED_CHANNEL_USERNAME = "EKSODI_VPN"       # بدون @ و بدون لینک
REQUIRED_CHANNEL_ID = f"@{REQUIRED_CHANNEL_USERNAME}"
REQUIRED_CHANNEL_URL = f"https://t.me/{REQUIRED_CHANNEL_USERNAME}"

# مقادیر پیش‌فرض (این‌ها بعد از اولین اجرا از پنل ادمین قابل تغییرن؛ همین‌جا فقط مقدار اولیه‌ست)
DEFAULT_SUPPORT_USERNAME = "EKSODI8"
DEFAULT_NEW_USER_BONUS = 0
DEFAULT_REFERRAL_BONUS = 0

# مبلغ‌های پیشنهادی برای شارژ کیف پول (تومان)
CHARGE_PRESETS = [50000, 100000, 200000, 500000, 1000000]

MIN_CUSTOM_CHARGE = 25000
MAX_CUSTOM_CHARGE = 1000000

# حداقل و حداکثر حجم قابل خرید (گیگابایت)
MIN_VOLUME_GB = 1
MAX_VOLUME_GB = 1000

# ⚡️ فقط برای اولین اجرا: از روی این دیکشنری پلن‌های اولیه ساخته میشن (کلید=گیگ، مقدار=قیمت تومان).
# بعد از اولین اجرا دیگه به این دیکشنری نیازی نیست — همه‌چیز از «پنل ادمین → 🧩 مدیریت پلن‌ها»
# قابل ساخت/ویرایش/قیمت‌گذاریه (مثل پلن «نامحدود» یا هر پلن جدید دیگه).
LEGACY_AUTO_PACKAGES = {
    5: 30000,
    10: 60000,
    20: 120000,
}

# 🧪 هر کانفیگ تست رایگان حداکثر به همین تعداد نفر متفاوت تحویل داده میشه، بعد خودکار حذف میشه
TEST_CONFIG_MAX_DELIVERIES = 3

# فاصله بین پیام‌های ارسال همگانی برای جلوگیری از محدودیت تلگرام (ثانیه)
BROADCAST_DELAY = 0.05

# 🎀 استیکرهای بات (اختیاری). برای هر رویداد یه file_id بذار تا بات موقع اون اتفاق استیکر بفرسته.
# گرفتن file_id: یه استیکر دلخواه رو برای خودِ بات فوروارد/ارسال کن (فقط مالک/ادمین)،
# بات همون لحظه file_id شو برات تو چت می‌فرسته که کپی کنی و اینجا جایگزین کنی.
STICKERS = {
    "welcome": "",           # موقع اولین /start کاربر جدید
    "purchase_success": "",  # موقع تحویل موفق کانفیگ (خرید اتوماتیک یا دستی)
    "deposit_approved": "",  # موقع تایید شارژ کیف پول
}

# مهلت هر گفتگوی چندمرحله‌ای (ثانیه) - بعد از این مدت بی‌فعالیتی، گفتگو خودکار لغو می‌شود
CONV_TIMEOUT = 600

# ==================== دیتابیس ====================
DB_PATH = os.environ.get("DB_PATH", "vip_bot.db")
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
# 🛡 پایداری و مقاومت در برابر قفل شدن دیتابیس (WAL + مهلت انتظار)
try:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
except Exception:
    pass


def db_run(query: str, params: tuple = ()):
    """اجرای INSERT/UPDATE/DELETE با کرسر مستقل (برای جلوگیری از تداخل)."""
    c = conn.execute(query, params)
    conn.commit()
    return c


def db_one(query: str, params: tuple = ()):
    return conn.execute(query, params).fetchone()


def db_all(query: str, params: tuple = ()):
    return conn.execute(query, params).fetchall()


db_run("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    balance INTEGER DEFAULT 0,
    used_configs INTEGER DEFAULT 0,
    country TEXT,
    join_date TEXT,
    is_banned INTEGER DEFAULT 0,
    total_spent INTEGER DEFAULT 0,
    referal_code TEXT UNIQUE,
    refered_by INTEGER DEFAULT 0
)
""")

db_run("""
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    type TEXT,
    amount INTEGER,
    description TEXT,
    date REAL
)
""")

db_run("""
CREATE TABLE IF NOT EXISTS support_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    message TEXT,
    is_from_admin INTEGER DEFAULT 0,
    date REAL,
    is_read INTEGER DEFAULT 0
)
""")

db_run("""
CREATE TABLE IF NOT EXISTS deposits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    amount INTEGER,
    status TEXT DEFAULT 'pending',
    receipt_type TEXT,
    receipt_note TEXT,
    created_at REAL,
    decided_at REAL
)
""")

# سفارش‌های خرید کانفیگ با حجم دلخواه
db_run("""
CREATE TABLE IF NOT EXISTS config_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    volume_gb REAL,
    price INTEGER,
    status TEXT DEFAULT 'pending',   -- pending / delivered / cancelled
    created_at REAL,
    delivered_at REAL
)
""")

# ⚡️ انبار کانفیگ‌های آماده برای خرید اتوماتیک (هر ردیف = یک کانفیگ که فقط یک‌بار تحویل داده میشه)
db_run("""
CREATE TABLE IF NOT EXISTS auto_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_gb INTEGER,
    source_chat_id INTEGER,
    source_message_id INTEGER,
    status TEXT DEFAULT 'available',   -- available / delivered
    added_by INTEGER,
    added_at REAL,
    delivered_to INTEGER,
    delivered_at REAL
)
""")

# 🧪 انبار کانفیگ‌های تست رایگان: هر ردیف یک کانفیگ که تا TEST_CONFIG_MAX_DELIVERIES نفر
# متفاوت می‌گیرنش (همه یک کانفیگ رو می‌گیرن، نه اینکه هرکس یه کانفیگ جدا بگیره)، بعد حذف میشه
db_run("""
CREATE TABLE IF NOT EXISTS test_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_chat_id INTEGER,
    source_message_id INTEGER,
    delivered_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',   -- active / exhausted
    added_by INTEGER,
    added_at REAL
)
""")

# هر کاربر فقط یک‌بار می‌تونه کانفیگ تست بگیره (UNIQUE روی user_id تضمینش می‌کنه حتی موقع رقابت هم‌زمان)
db_run("""
CREATE TABLE IF NOT EXISTS test_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER UNIQUE,
    test_config_id INTEGER,
    delivered_at REAL
)
""")

db_run("""
CREATE TABLE IF NOT EXISTS bot_settings (
    key TEXT PRIMARY KEY,
    value TEXT
)
""")

# ادمین‌های اضافه‌شده از پنل (علاوه بر OWNER_IDS که داخل کد ثابت هستن)
db_run("""
CREATE TABLE IF NOT EXISTS bot_admins (
    id INTEGER PRIMARY KEY,
    added_by INTEGER,
    added_at REAL
)
""")

# 🧩 پلن‌های فروش (پکیج‌های حجمی، «نامحدود» و هر پلن دلخواه دیگه) — کاملاً از پنل ادمین قابل مدیریت.
# delivery_mode: auto (فقط تحویل آنی از انبار) / manual (فقط سفارش دستی برای ادمین) / hybrid (اول انبار، اگه خالی بود دستی)
# show_in: auto (فقط منوی خرید اتوماتیک) / buy (فقط منوی خرید کانفیگ) / both (هر دو)
db_run("""
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    price INTEGER NOT NULL DEFAULT 0,
    confirm_text TEXT,
    delivery_mode TEXT DEFAULT 'hybrid',
    show_in TEXT DEFAULT 'both',
    is_active INTEGER DEFAULT 1,
    sort_order INTEGER DEFAULT 0,
    legacy_gb INTEGER,
    created_at REAL
)
""")

# 🎟 کدهای تخفیف + سابقه‌ی استفاده (هر کاربر از هر کد فقط یک‌بار — UNIQUE تضمینش می‌کنه)
db_run("""
CREATE TABLE IF NOT EXISTS discount_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    dtype TEXT DEFAULT 'percent',
    value INTEGER NOT NULL,
    max_uses INTEGER DEFAULT 0,
    used_count INTEGER DEFAULT 0,
    expires_at REAL,
    is_active INTEGER DEFAULT 1,
    created_by INTEGER,
    created_at REAL
)
""")

db_run("""
CREATE TABLE IF NOT EXISTS discount_uses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code_id INTEGER,
    user_id INTEGER,
    amount_saved INTEGER,
    used_at REAL,
    UNIQUE(code_id, user_id)
)
""")


# ==================== مهاجرت خودکار دیتابیس قدیمی ====================
def ensure_columns(table: str, columns: dict):
    """اگه دیتابیس از یه نسخه قدیمی‌تر بات مونده باشه و ستونی کم داشته باشه،
    اینجا بدون از دست رفتن داده‌ها اضافه‌ش می‌کنیم."""
    existing = {row["name"] for row in db_all(f"PRAGMA table_info({table})")}
    for col, coldef in columns.items():
        if col not in existing:
            try:
                db_run(f"ALTER TABLE {table} ADD COLUMN {col} {coldef}")
                logger.info("migration: added column %s.%s", table, col)
            except Exception as e:
                logger.warning("migration failed for %s.%s: %s", table, col, e)


ensure_columns("users", {
    "username": "TEXT",
    "first_name": "TEXT",
    "balance": "INTEGER DEFAULT 0",
    "used_configs": "INTEGER DEFAULT 0",
    "country": "TEXT",
    "join_date": "TEXT",
    "is_banned": "INTEGER DEFAULT 0",
    "total_spent": "INTEGER DEFAULT 0",
    "referal_code": "TEXT",
    "refered_by": "INTEGER DEFAULT 0",
})
ensure_columns("deposits", {
    "user_id": "INTEGER",
    "amount": "INTEGER",
    "status": "TEXT DEFAULT 'pending'",
    "receipt_type": "TEXT",
    "receipt_note": "TEXT",
    "created_at": "REAL",
    "decided_at": "REAL",
})
ensure_columns("config_orders", {
    "user_id": "INTEGER",
    "volume_gb": "REAL",
    "price": "INTEGER",
    "status": "TEXT DEFAULT 'pending'",
    "created_at": "REAL",
    "delivered_at": "REAL",
    "plan_id": "INTEGER",
})
ensure_columns("auto_configs", {
    "package_gb": "INTEGER",
    "source_chat_id": "INTEGER",
    "source_message_id": "INTEGER",
    "status": "TEXT DEFAULT 'available'",
    "added_by": "INTEGER",
    "added_at": "REAL",
    "delivered_to": "INTEGER",
    "delivered_at": "REAL",
    "plan_id": "INTEGER",
})
ensure_columns("plans", {
    "name": "TEXT",
    "price": "INTEGER DEFAULT 0",
    "confirm_text": "TEXT",
    "delivery_mode": "TEXT DEFAULT 'hybrid'",
    "show_in": "TEXT DEFAULT 'both'",
    "is_active": "INTEGER DEFAULT 1",
    "sort_order": "INTEGER DEFAULT 0",
    "legacy_gb": "INTEGER",
    "created_at": "REAL",
})
ensure_columns("discount_codes", {
    "code": "TEXT",
    "dtype": "TEXT DEFAULT 'percent'",
    "value": "INTEGER DEFAULT 0",
    "max_uses": "INTEGER DEFAULT 0",
    "used_count": "INTEGER DEFAULT 0",
    "expires_at": "REAL",
    "is_active": "INTEGER DEFAULT 1",
    "created_by": "INTEGER",
    "created_at": "REAL",
})
ensure_columns("discount_uses", {
    "code_id": "INTEGER",
    "user_id": "INTEGER",
    "amount_saved": "INTEGER",
    "used_at": "REAL",
})
ensure_columns("test_configs", {
    "source_chat_id": "INTEGER",
    "source_message_id": "INTEGER",
    "delivered_count": "INTEGER DEFAULT 0",
    "status": "TEXT DEFAULT 'active'",
    "added_by": "INTEGER",
    "added_at": "REAL",
})
ensure_columns("test_deliveries", {
    "user_id": "INTEGER",
    "test_config_id": "INTEGER",
    "delivered_at": "REAL",
})
ensure_columns("support_messages", {
    "user_id": "INTEGER",
    "message": "TEXT",
    "is_from_admin": "INTEGER DEFAULT 0",
    "date": "REAL",
    "is_read": "INTEGER DEFAULT 0",
})
ensure_columns("transactions", {
    "user_id": "INTEGER",
    "type": "TEXT",
    "amount": "INTEGER",
    "description": "TEXT",
    "date": "REAL",
})

# ==================== توابع تنظیمات پایدار ====================
def get_setting(key: str, default: str = "") -> str:
    row = db_one("SELECT value FROM bot_settings WHERE key=?", (key,))
    return row["value"] if row else default


def set_setting(key: str, value: str):
    db_run("INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)", (key, str(value)))


def _init_setting(key: str, default: str):
    if not get_setting(key):
        set_setting(key, default)


_init_setting("maintenance_mode", "0")
_init_setting("welcome_msg", f"🌟 به {BOT_NAME} خوش آمدی!")
_init_setting("purchase_notify", "1")
_init_setting("join_notify", "1")
_init_setting("support_notify", "1")
_init_setting("deposit_notify", "1")
_init_setting("card_number", "0000-0000-0000-0000")
_init_setting("card_holder", "به نام صاحب حساب")
_init_setting("price_per_gb", "10000")
_init_setting("support_username", DEFAULT_SUPPORT_USERNAME)
_init_setting("signup_bonus", str(DEFAULT_NEW_USER_BONUS))
_init_setting("referral_bonus", str(DEFAULT_REFERRAL_BONUS))

# ==================== ساخت پلن‌های اولیه (فقط اولین اجرا) ====================
UNLIMITED_CONFIRM_TEXT = "آیا تایید می‌کنید خرید کانفیگ تک سروره آمریکا نامحدود را؟"


def _seed_plans():
    """اولین اجرا: پکیج‌های قدیمی 5/10/20 گیگ به پلن تبدیل میشن (بدون از دست رفتن انبار)
    و پلن «نامحدود تک سرور آمریکا» ساخته میشه. اجراهای بعدی هیچ کاری نمی‌کنه."""
    if db_one("SELECT id FROM plans LIMIT 1"):
        return
    order = 0
    for gb, price in LEGACY_AUTO_PACKAGES.items():
        order += 1
        c = db_run(
            "INSERT INTO plans (name, price, confirm_text, delivery_mode, show_in, is_active, sort_order, legacy_gb, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (f"⚡️ {gb} گیگ", price, None, "auto", "auto", 1, order, gb, time.time()),
        )
        # انبار قدیمی همین پکیج به پلن جدید وصل میشه که هیچ کانفیگی از دست نره
        db_run("UPDATE auto_configs SET plan_id=? WHERE package_gb=? AND plan_id IS NULL", (c.lastrowid, gb))
    db_run(
        "INSERT INTO plans (name, price, confirm_text, delivery_mode, show_in, is_active, sort_order, legacy_gb, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("♾ نامحدود (تک سرور آمریکا)", 250000, UNLIMITED_CONFIRM_TEXT, "hybrid", "both", 1, order + 1, None, time.time()),
    )
    logger.info("plans seeded (legacy packages migrated + unlimited plan created)")


_seed_plans()

# ==================== States (هر گفتگو state های مستقل خودش رو داره) ====================
(ASK_USER_ID, ASK_AMOUNT, SEND_MSG_UID, SEND_MSG_TEXT, SUPPORT_MSG, ADMIN_REPLY_MSG,
 CHARGE_CUSTOM_AMOUNT, CHARGE_RECEIPT, ASK_VOLUME, ADMIN_SEND_CFG, SET_PRICE_PER_GB,
 SET_CARD_NUMBER, SET_CARD_HOLDER, SET_WELCOME, BC_TEXT, BC_CONFIRM,
 ADD_ADMIN_ID, SET_SUPPORT_USERNAME, SET_SIGNUP_BONUS, SET_REFERRAL_BONUS,
 ADMIN_AUTO_ADD_CFG, ADMIN_TEST_ADD_CFG,
 PLAN_NEW_NAME, PLAN_NEW_PRICE, PLAN_NEW_TEXT,
 PLAN_EDIT_PRICE, PLAN_EDIT_NAME, PLAN_EDIT_TEXT,
 DISC_ENTER_CODE, DISC_NEW_CODE, DISC_NEW_TYPE,
 DISC_NEW_VALUE, DISC_NEW_MAX, DISC_NEW_DAYS) = range(34)

# ==================== توابع کمکی ====================
def md_escape(text) -> str:
    return re.sub(r'([_*`\[])', r'\\\1', str(text))


def fmt_money(n) -> str:
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


def fmt_volume(v) -> str:
    try:
        v = float(v)
        return str(int(v)) if v == int(v) else f"{v:g}"
    except Exception:
        return str(v)


PERSIAN_WEEKDAYS = {0: "دوشنبه", 1: "سه‌شنبه", 2: "چهارشنبه", 3: "پنجشنبه", 4: "جمعه", 5: "شنبه", 6: "یکشنبه"}


def gregorian_to_jalali(gy: int, gm: int, gd: int):
    """تبدیل تاریخ میلادی به شمسی، بدون نیاز به کتابخانه‌ی خارجی."""
    g_days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    j_days_in_month = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]
    gy2 = gy - 1600
    gm2 = gm - 1
    gd2 = gd - 1
    g_day_no = 365 * gy2 + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
    for i in range(gm2):
        g_day_no += g_days_in_month[i]
    if gm2 > 1 and ((gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0)):
        g_day_no += 1
    g_day_no += gd2
    j_day_no = g_day_no - 79
    j_np = j_day_no // 12053
    j_day_no %= 12053
    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no %= 1461
    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365
    jm, jd = 12, j_day_no - 348
    for i in range(11):
        if j_day_no < j_days_in_month[i]:
            jm, jd = i + 1, j_day_no + 1
            break
        j_day_no -= j_days_in_month[i]
    return jy, jm, jd


def get_user(uid: int):
    return db_one("SELECT * FROM users WHERE id=?", (uid,))


def get_deposit(dep_id: int):
    return db_one("SELECT * FROM deposits WHERE id=?", (dep_id,))


def get_order(order_id: int):
    return db_one("SELECT * FROM config_orders WHERE id=?", (order_id,))


def order_desc(order) -> str:
    """توضیح خوانای سفارش: اسم پلن (مثل نامحدود) یا حجم دلخواه."""
    try:
        if order["plan_id"]:
            p = db_one("SELECT name FROM plans WHERE id=?", (order["plan_id"],))
            return p["name"] if p else f"پلن #{order['plan_id']}"
    except Exception:
        pass
    return f"{fmt_volume(order['volume_gb'])} گیگ"


def get_price_per_gb() -> int:
    try:
        return int(get_setting("price_per_gb", "10000"))
    except Exception:
        return 10000


def get_support_username() -> str:
    return get_setting("support_username", DEFAULT_SUPPORT_USERNAME)


def get_signup_bonus() -> int:
    try:
        return int(get_setting("signup_bonus", "0"))
    except Exception:
        return 0


def get_referral_bonus() -> int:
    try:
        return int(get_setting("referral_bonus", "0"))
    except Exception:
        return 0


# ==================== 🎟 کدهای تخفیف (توابع پایه) ====================
def get_discount_by_code(code: str):
    return db_one("SELECT * FROM discount_codes WHERE code=?", (code.strip().upper(),))


def get_discount(did: int):
    return db_one("SELECT * FROM discount_codes WHERE id=?", (did,))


def discount_status(d, uid: int = None) -> str:
    """'ok' یا دلیل نامعتبر بودن کد برای این کاربر."""
    if not d or not d["is_active"]:
        return "notfound"
    if d["expires_at"] and time.time() > d["expires_at"]:
        return "expired"
    # اول چک شخصی (پیام دقیق‌تر به کاربر)، بعد سقف کل
    if uid is not None and db_one(
        "SELECT 1 FROM discount_uses WHERE code_id=? AND user_id=?", (d["id"], uid)
    ):
        return "used"
    if d["max_uses"] and d["used_count"] >= d["max_uses"]:
        return "maxed"
    return "ok"


def apply_discount(price: int, d) -> int:
    """قیمت بعد از تخفیف (هیچ‌وقت زیر صفر نمیره)."""
    if d["dtype"] == "amount":
        return max(int(price) - int(d["value"]), 0)
    return max(int(price) - (int(price) * int(d["value"])) // 100, 0)


def discount_label(d) -> str:
    return f"{d['value']}٪" if d["dtype"] == "percent" else f"{fmt_money(d['value'])} تومان"


def redeem_discount(d, uid: int, amount_saved: int) -> bool:
    """ثبت اتمیک استفاده از کد (سقف کل + یک‌بار برای هر کاربر). True یعنی موفق."""
    try:
        db_run("INSERT INTO discount_uses (code_id, user_id, amount_saved, used_at) VALUES (?,?,?,?)",
               (d["id"], uid, amount_saved, time.time()))
    except sqlite3.IntegrityError:
        return False  # همین کاربر هم‌زمان از یه جای دیگه استفاده کرده
    cur = db_run(
        "UPDATE discount_codes SET used_count=used_count+1 "
        "WHERE id=? AND is_active=1 AND (max_uses=0 OR used_count<max_uses)",
        (d["id"],)
    )
    if cur.rowcount == 0:
        db_run("DELETE FROM discount_uses WHERE code_id=? AND user_id=?", (d["id"], uid))
        return False  # سقف کل همین لحظه پر شد
    return True


def refund_discount(code_id: int, uid: int):
    """برگشت استفاده از کد وقتی خرید ناموفق میشه و پول برمی‌گرده."""
    cur = db_run("DELETE FROM discount_uses WHERE code_id=? AND user_id=?", (code_id, uid))
    if cur.rowcount:
        db_run("UPDATE discount_codes SET used_count=MAX(used_count-1,0) WHERE id=?", (code_id,))


def _pending_discount_for(context, kind: str, pid=None):
    """کد تخفیفی که کاربر برای همین خرید ثبت کرده (اگه هنوز معتبر باشه)."""
    pd = context.user_data.get("pending_discount")
    if not pd or pd.get("kind") != kind:
        return None
    if kind == "plan" and pd.get("pid") != pid:
        return None
    d = get_discount(pd.get("code_id"))
    if discount_status(d, pd.get("uid")) != "ok":
        context.user_data.pop("pending_discount", None)
        return None
    return d


def log_tx(uid: int, ttype: str, amount: int, desc: str):
    db_run(
        "INSERT INTO transactions (user_id, type, amount, description, date) VALUES (?,?,?,?,?)",
        (uid, ttype, amount, desc, time.time()),
    )


async def safe_edit(query, text, reply_markup=None, parse_mode=None):
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            logger.warning("edit failed: %s", e)
            try:
                await query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            except Exception:
                pass


def cancel_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🚫 لغو عملیات", callback_data="cancel_conv", style="danger")]])


def is_maintenance() -> bool:
    return get_setting("maintenance_mode") == "1"


# ---- سطح دسترسی: مالک (owner) / ادمین (owner + ادمین‌های اضافه‌شده) ----
def admin_ids() -> set:
    ids = set(OWNER_IDS)
    try:
        for row in db_all("SELECT id FROM bot_admins"):
            ids.add(row["id"])
    except Exception:
        pass
    return ids


def is_owner(uid: int) -> bool:
    return uid in OWNER_IDS


def is_admin(uid: int) -> bool:
    return uid in admin_ids()


async def guard_admin(update: Update) -> bool:
    uid = update.effective_user.id
    if not is_admin(uid):
        if update.callback_query:
            await update.callback_query.answer("⛔ دسترسی غیرمجاز!", show_alert=True)
        return False
    return True


async def guard_owner(update: Update) -> bool:
    uid = update.effective_user.id
    if not is_owner(uid):
        if update.callback_query:
            await update.callback_query.answer("⛔ این بخش فقط برای مالک ربات مجازه!", show_alert=True)
        return False
    return True


async def notify_admins(context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None,
                         parse_mode=ParseMode.MARKDOWN):
    """ارسال پیام به همه‌ی ادمین‌های فعلی (مالکان + ادمین‌های اضافه‌شده)."""
    for aid in admin_ids():
        try:
            await context.bot.send_message(aid, text, parse_mode=parse_mode, reply_markup=reply_markup)
        except Exception as e:
            logger.warning("notify_admins failed for %s: %s", aid, e)


async def notify_owners(context: ContextTypes.DEFAULT_TYPE, text: str, parse_mode=ParseMode.MARKDOWN):
    for oid in OWNER_IDS:
        try:
            await context.bot.send_message(oid, text, parse_mode=parse_mode)
        except Exception:
            pass


async def send_sticker_safe(context: ContextTypes.DEFAULT_TYPE, chat_id: int, key: str):
    """اگه برای این رویداد استیکر تنظیم شده باشه (تو دیکشنری STICKERS بالای فایل)، می‌فرستدش.
    اگه خالی باشه یا ارسالش خطا بده، بی‌سروصدا رد میشه تا جلوی کارِ اصلی بات رو نگیره."""
    file_id = STICKERS.get(key)
    if not file_id:
        return
    try:
        await context.bot.send_sticker(chat_id, file_id)
    except Exception as e:
        logger.warning("sticker send failed (%s -> %s): %s", key, chat_id, e)


async def sticker_id_grabber(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """فقط برای مالک/ادمین: هر استیکری که برای بات بفرستی، file_id شو برات برمی‌گردونه
    تا تو دیکشنری STICKERS بالای فایل جایگزینش کنی."""
    uid = update.effective_user.id
    if not is_admin(uid):
        return
    sticker = update.message.sticker
    if not sticker:
        return
    await update.message.reply_text(
        f"🆔 file_id این استیکر:\n`{sticker.file_id}`\n\n"
        f"این رو کپی کن و تو دیکشنری STICKERS بالای فایل جایگزین کن.",
        parse_mode=ParseMode.MARKDOWN,
    )


# ==================== عضویت اجباری در کانال ====================
def join_channel_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 عضویت در کانال", url=REQUIRED_CHANNEL_URL, style="primary")],
        [InlineKeyboardButton("✅ عضو شدم", callback_data="check_join", style="success")],
    ])


# کش عضویت: هم جلوی ریت‌لیمیت تلگرام رو می‌گیره، هم اگه یه لحظه شبکه/API قطع شد،
# کاربرای عضو از بات بیرون نمی‌مونن (آخرین وضعیت معتبرشون ملاک میشه).
_member_cache = {}  # user_id -> (is_member, checked_at)
MEMBER_CACHE_TTL = 300  # ثانیه


async def is_member_of_channel(bot, user_id: int) -> bool:
    """چک می‌کنه کاربر عضو کانال اجباری هست یا نه (با کش ۵ دقیقه‌ای برای عضوها)."""
    now = time.time()
    cached = _member_cache.get(user_id)
    if cached and cached[0] and (now - cached[1]) < MEMBER_CACHE_TTL:
        return True
    try:
        member = await bot.get_chat_member(REQUIRED_CHANNEL_ID, user_id)
        ok = member.status in ("member", "administrator", "creator")
        _member_cache[user_id] = (ok, now)
        return ok
    except Exception as e:
        logger.warning("membership check failed for %s: %s", user_id, e)
        # خطای موقتی (شبکه/ریت‌لیمیت): اگه قبلاً وضعیتش رو دیدیم، همون رو ملاک بگیر؛
        # اگه هیچ‌وقت تایید نشده، برای امنیت عضو در نظر نمی‌گیریمش.
        if cached is not None:
            return cached[0]
        return False


async def send_join_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🔒 *دسترسی محدود شده*\n"
        "━━━━━━━━━━━━━━\n"
        "برای استفاده از بات، اول باید عضو کانال ما بشی.\n\n"
        "بعد از عضویت، روی دکمه‌ی «✅ عضو شدم» بزن."
    )
    if update.callback_query:
        try:
            await update.callback_query.answer("⛔ اول باید عضو کانال بشی!", show_alert=True)
        except Exception:
            pass
        try:
            await context.bot.send_message(
                update.effective_chat.id, text, parse_mode=ParseMode.MARKDOWN, reply_markup=join_channel_kb()
            )
        except Exception:
            pass
    elif update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=join_channel_kb())


async def membership_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اجرا میشه قبل از هر هندلر دیگه‌ای (group=-1). اگه کاربر عضو کانال نباشه،
    پیام عضویت اجباری رو نشون میده و جلوی ادامه‌ی پردازش رو می‌گیره."""
    user = update.effective_user
    if not user:
        return
    uid = user.id

    # مالکان و ادمین‌ها همیشه دسترسی دارن
    if is_admin(uid):
        return

    # ⛔ کاربر مسدود به هیچ بخشی از بات دسترسی نداره (قبلاً فقط /start چک می‌شد و
    # کاربر بن‌شده می‌تونست با دکمه‌ها به همه‌چیز از جمله خرید دسترسی داشته باشه)
    banned_row = get_user(uid)
    if banned_row and banned_row["is_banned"]:
        try:
            if update.callback_query:
                await update.callback_query.answer("⛔ شما مسدود هستید.", show_alert=True)
            elif update.message:
                await update.message.reply_text(
                    f"⛔ شما مسدود هستید.\nبرای اعتراض به پشتیبانی پیام بده: @{get_support_username()}"
                )
        except Exception:
            pass
        raise ApplicationHandlerStop

    # خود دکمه‌ی «عضو شدم» رو اینجا بلاک نکن، هندلر مخصوص خودش جواب میده
    if update.callback_query and update.callback_query.data == "check_join":
        return

    joined = await is_member_of_channel(context.bot, uid)
    if joined:
        return  # عضوه، بذار پردازش عادی ادامه پیدا کنه

    await send_join_prompt(update, context)
    raise ApplicationHandlerStop


async def check_join_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    joined = await is_member_of_channel(context.bot, uid)
    if not joined:
        await query.answer("❌ هنوز عضو کانال نشدی! اول عضو شو، بعد دوباره بزن.", show_alert=True)
        return
    await query.answer("✅ عضویت تایید شد!")
    await do_start(update, context)


# ==================== منوها ====================
def main_menu():
    keyboard = [
        [InlineKeyboardButton("💥 خرید کانفیگ", callback_data="buy_config", style="success")],
        [InlineKeyboardButton("⚡️ خرید اتوماتیک", callback_data="auto_buy_menu", style="success")],
        [InlineKeyboardButton("🧪 تست رایگان", callback_data="free_test_entry", style="success")],
        [InlineKeyboardButton("💳 شارژ کیف پول", callback_data="charge_wallet", style="primary"),
         InlineKeyboardButton("💰 اعتبار کیف پول", callback_data="wallet", style="primary")],
        [InlineKeyboardButton("🎉 دعوت دوستان", callback_data="invite", style="primary")],
        [InlineKeyboardButton("🧾 حساب کاربری", callback_data="account_info", style="primary")],
        [InlineKeyboardButton("💬 پشتیبانی", callback_data="support_entry", style="primary")],
        [InlineKeyboardButton("❓ راهنما", callback_data="help", style="danger")],
    ]
    return InlineKeyboardMarkup(keyboard)


def _admin_attention_counts():
    po = db_one("SELECT COUNT(*) c FROM config_orders WHERE status='pending'")["c"]
    pd = db_one("SELECT COUNT(*) c FROM deposits WHERE status='pending'")["c"]
    un = db_one("SELECT COUNT(*) c FROM support_messages WHERE is_from_admin=0 AND is_read=0")["c"]
    return po, pd, un


def _cnt(label: str, n: int) -> str:
    """اگه چیزی منتظر رسیدگی باشه، تعدادش روی خود دکمه نشون داده میشه."""
    return f"{label} ({n})" if n else label


def admin_menu():
    po, pd, un = _admin_attention_counts()
    keyboard = [
        [InlineKeyboardButton("🧩 پلن‌ها و قیمت‌ها", callback_data="admin_plans_menu", style="success"),
         InlineKeyboardButton("🎟 کدهای تخفیف", callback_data="admin_discounts", style="success")],
        [InlineKeyboardButton(_cnt("📥 سفارش‌های در انتظار", po), callback_data="admin_pending_orders", style="primary"),
         InlineKeyboardButton(_cnt("💳 درخواست‌های شارژ", pd), callback_data="admin_deposits", style="primary")],
        [InlineKeyboardButton("⚡️ انبار کانفیگ‌ها", callback_data="admin_auto_menu", style="primary"),
         InlineKeyboardButton("🧪 کانفیگ‌های تست", callback_data="admin_test_menu", style="primary")],
        [InlineKeyboardButton("📦 حجم و قیمت گیگ", callback_data="admin_orders_menu", style="primary"),
         InlineKeyboardButton(_cnt("💬 پشتیبانی", un), callback_data="admin_support_inbox", style="primary")],
        [InlineKeyboardButton("👤 مدیریت کاربران", callback_data="admin_users", style="primary"),
         InlineKeyboardButton("📨 پیام به کاربر", callback_data="admin_send_msg_entry", style="primary")],
        [InlineKeyboardButton("📢 ارسال همگانی", callback_data="admin_broadcast_entry", style="primary"),
         InlineKeyboardButton("📊 آمار کلی", callback_data="admin_stats", style="primary")],
        [InlineKeyboardButton("💾 بکاپ دیتابیس", callback_data="admin_backup", style="primary"),
         InlineKeyboardButton("🛡 مدیریت ادمین‌ها", callback_data="admin_manage_admins", style="primary")],
        [InlineKeyboardButton("⚙️ تنظیمات بات", callback_data="admin_settings", style="primary"),
         InlineKeyboardButton("🗑 پاک‌سازی", callback_data="admin_wipe_menu", style="danger")],
        [InlineKeyboardButton("🔄 بروزرسانی پنل", callback_data="admin_back", style="primary")],
        [InlineKeyboardButton("🔙 بازگشت به منوی اصلی", callback_data="back_main", style="primary")],
    ]
    return InlineKeyboardMarkup(keyboard)


def admin_panel_text() -> str:
    """سرصفحه‌ی پنل با خلاصه‌ی چیزهایی که منتظر رسیدگی‌ان."""
    po, pd, un = _admin_attention_counts()
    items = []
    if po:
        items.append(f"• 📥 {po} سفارش در انتظار ارسال")
    if pd:
        items.append(f"• 💳 {pd} درخواست شارژ در انتظار تایید")
    if un:
        items.append(f"• 💬 {un} پیام پشتیبانی خوانده‌نشده")
    text = "👮 *پنل ادمین*\n━━━━━━━━━━━━━━\n"
    text += ("⚠️ نیاز به رسیدگی:\n" + "\n".join(items)) if items else "✅ همه‌چیز مرتبه؛ چیزی در انتظار رسیدگی نیست."
    return text


def profile_text(user) -> str:
    ban = "⛔ مسدود" if user["is_banned"] else "✅ فعال"
    return (
        f"👤 *پروفایل کاربر*\n"
        f"━━━━━━━━━━━━━━\n"
        f"🆔 آیدی: `{user['id']}`\n"
        f"📛 نام: {md_escape(user['first_name'] or '-')}\n"
        f"🔗 یوزرنیم: @{md_escape(user['username'] or '-')}\n"
        f"💰 موجودی: {fmt_money(user['balance'])} تومان\n"
        f"📦 کانفیگ خریداری‌شده: {user['used_configs']}\n"
        f"💵 مجموع خرید: {fmt_money(user['total_spent'])} تومان\n"
        f"📅 تاریخ عضویت: {user['join_date']}\n"
        f"وضعیت: {ban}"
    )


def profile_kb(user):
    uid = user["id"]
    ban_btn = (
        InlineKeyboardButton("✅ رفع مسدودیت", callback_data=f"act_unban_{uid}", style="success")
        if user["is_banned"]
        else InlineKeyboardButton("⛔ مسدود کردن", callback_data=f"act_ban_{uid}", style="danger")
    )
    keyboard = [
        [InlineKeyboardButton("➕ افزایش موجودی", callback_data=f"act_addcoin_{uid}", style="success"),
         InlineKeyboardButton("➖ کاهش موجودی", callback_data=f"act_subcoin_{uid}", style="danger")],
        [InlineKeyboardButton("📨 ارسال پیام", callback_data=f"admin_send_to_{uid}", style="primary")],
        [ban_btn],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_users", style="primary")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ==================== شروع ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اجرا میشه وقتی کاربر دستور /start رو بزنه. membership_gate (group=-1) قبل از این
    اجرا شده و مطمئن شده کاربر عضو کانال هست، پس اینجا فقط منطق اصلی start رو صدا می‌زنیم."""
    await do_start(update, context)


async def do_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """منطق اصلی start. هم از دستور /start (update.message) و هم از دکمه‌ی
    «✅ عضو شدم» (update.callback_query) قابل فراخوانیه."""
    user = update.effective_user
    uid = user.id
    chat_id = update.effective_chat.id

    async def send(text, **kwargs):
        if update.message:
            return await update.message.reply_text(text, **kwargs)
        return await context.bot.send_message(chat_id, text, **kwargs)

    async def replace_old_menu(new_msg):
        """🧹 فقط یه منوی زنده بمونه: اگه کاربر دوباره /start بزنه، منوی قبلی حذف میشه."""
        old_id = context.user_data.get("last_menu_msg_id")
        if old_id and new_msg and old_id != new_msg.message_id:
            try:
                await context.bot.delete_message(chat_id, old_id)
            except Exception:
                pass  # پیام قدیمی‌تر از ۴۸ ساعت یا قبلاً حذف‌شده؛ مهم نیست
        if new_msg:
            context.user_data["last_menu_msg_id"] = new_msg.message_id

    if is_maintenance() and not is_admin(uid):
        await send("🔧 بات در حال تعمیر و نگهداری است.\nلطفاً بعداً مراجعه کنید.")
        return

    existing = get_user(uid)

    if not existing:
        country = "Unknown"
        try:
            res = await asyncio.to_thread(lambda: requests.get("https://ipapi.co/json/", timeout=3).json())
            country = res.get("country_name", "Unknown")
        except Exception:
            pass

        ref_code = f"VIP{uid % 1000000:06d}"
        join_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        referrer_id = 0
        if context.args:
            arg = context.args[0]
            row = db_one("SELECT id FROM users WHERE referal_code=?", (arg,))
            if row and row["id"] != uid:
                referrer_id = row["id"]

        signup_bonus = get_signup_bonus()
        referral_bonus = get_referral_bonus()

        db_run(
            """INSERT INTO users (id, username, first_name, balance, country, join_date, referal_code, refered_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (uid, user.username or "no_username", user.first_name, signup_bonus,
             country, join_date, ref_code, referrer_id),
        )
        if signup_bonus:
            log_tx(uid, "signup_bonus", signup_bonus, "هدیه عضویت")

        if referrer_id and referral_bonus:
            db_run("UPDATE users SET balance=balance+? WHERE id=?", (referral_bonus, referrer_id))
            log_tx(referrer_id, "referral_bonus", referral_bonus, f"معرفی کاربر {uid}")
            try:
                await context.bot.send_message(
                    referrer_id, f"🎉 یک نفر با لینک دعوت تو عضو شد! +{fmt_money(referral_bonus)} تومان گرفتی."
                )
            except Exception:
                pass

        if get_setting("join_notify", "1") == "1":
            await notify_admins(
                context,
                f"🆕 کاربر جدید:\n👤 {md_escape(user.first_name)}\n🆔 `{uid}`\n🔗 @{md_escape(user.username or '-')}\n🌍 {country}",
            )

        welcome = get_setting("welcome_msg", f"🌟 به {BOT_NAME} خوش آمدی!")
        welcome_text = (
            f"✨ {welcome}\n"
            f"━━━━━━━━━━━━━━\n"
            f"🎁 هدیه‌ی عضویتت فعال شد!\n"
            f"👇 از دکمه‌های زیر شروع کن:"
        )
        await send_sticker_safe(context, chat_id, "welcome")
        m = await send(welcome_text, reply_markup=main_menu())
        await replace_old_menu(m)
    else:
        if existing["is_banned"]:
            await send("⛔ شما مسدود هستید.\nبرای اعتراض از بخش پشتیبانی استفاده کنید.")
            return
        db_run("UPDATE users SET first_name=?, username=? WHERE id=?",
               (user.first_name, user.username, uid))
        m = await send(
            f"🔄 خوش برگشتی، {md_escape(user.first_name)} 👋\n"
            f"━━━━━━━━━━━━━━\n"
            f"یکی از گزینه‌های زیر رو انتخاب کن:",
            reply_markup=main_menu(),
        )
        await replace_old_menu(m)


async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit(
        query,
        f"🏠 *{BOT_NAME}*\n━━━━━━━━━━━━━━\n✨ یکی از گزینه‌ها رو انتخاب کن:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu(),
    )


async def help_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "❓ *راهنمای استفاده*\n"
        "━━━━━━━━━━━━━━\n"
        "💥 از «خرید کانفیگ» حجم دلخواهت رو انتخاب و پرداخت کن\n\n"
        "⚡️ از «خرید اتوماتیک» یکی از پکیج‌های آماده رو بگیر و آنی تحویل بگیر\n\n"
        "🧪 از «تست رایگان» یه کانفیگ تست، فقط یک‌بار و رایگان بگیر\n\n"
        "💳 از «شارژ کیف پول» حساب خودت رو شارژ کن\n\n"
        "💰 موجودی و تاریخچه در «اعتبار کیف پول»\n\n"
        "🧾 اطلاعات کامل حسابت در «حساب کاربری»\n\n"
        "🎉 با «دعوت دوستان» به ازای هر معرفی جایزه بگیر\n"
        "━━━━━━━━━━━━━━\n"
        f"💬 سوال داشتی به پشتیبانی پیام بده: @{get_support_username()}"
    )
    kb = [[InlineKeyboardButton("🔙 بازگشت", callback_data="back_main", style="primary")]]
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def invite_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    user = get_user(uid)

    # ساخت لینک اختصاصی با یوزرنیم واقعی بات + کد رفرال کاربر
    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={user['referal_code']}"

    # شمارش تعداد کسانی که با لینک این کاربر عضو شدن
    referred = db_one("SELECT COUNT(*) c FROM users WHERE refered_by=?", (uid,))["c"]
    referral_bonus = get_referral_bonus()

    bonus_line = (
        f"به ازای هر دوست که با لینک تو عضو بشه، {fmt_money(referral_bonus)} تومان می‌گیری!\n\n"
        if referral_bonus else ""
    )

    text = (
        f"🎉 *دعوت دوستان*\n━━━━━━━━━━━━━━\n"
        f"{bonus_line}"
        f"🔗 لینک اختصاصی تو:\n`{link}`\n\n"
        f"👥 تعداد دعوت‌شده‌ها: *{referred}*"
    )

    kb = [
        [InlineKeyboardButton("📤 اشتراک‌گذاری لینک", switch_inline_query="بیا با لینک من عضو شو!", style="success")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main", style="primary")]
    ]
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def account_info_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """کارت اطلاعات حساب کاربری، به سبک پنل SONIC."""
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    user = get_user(uid)

    referred = db_one("SELECT COUNT(*) c FROM users WHERE refered_by=?", (uid,))["c"]

    now = datetime.now()
    jy, jm, jd = gregorian_to_jalali(now.year, now.month, now.day)
    jalali_today = f"{jy}/{jm:02d}/{jd:02d}"
    weekday_fa = PERSIAN_WEEKDAYS[now.weekday()]
    time_str = now.strftime("%H:%M")

    try:
        gy, gm, gd = (int(x) for x in user["join_date"][:10].split("-"))
        jjy, jjm, jjd = gregorian_to_jalali(gy, gm, gd)
        join_jalali = f"{jjy}/{jjm:02d}/{jjd:02d}"
    except Exception:
        join_jalali = user["join_date"] or "-"

    text = (
        f"🧾 *حساب کاربری*\n━━━━━━━━━━━━━━\n"
        f"🆔 آیدی عددیت : `{uid}`\n"
        f"👤 اسمت : {md_escape(user['first_name'])}\n\n"
        f"💰 موجودی حسابت : *{fmt_money(user['balance'])}* تومان\n\n"
        f"🌱 تعداد زیرمجموعه هات : *{referred}*"
    )

    kb = [
        [InlineKeyboardButton(str(uid), callback_data="noop", style="primary"),
         InlineKeyboardButton("شناسه کاربری 🆔", callback_data="noop", style="primary")],
        [InlineKeyboardButton(join_jalali, callback_data="noop", style="primary"),
         InlineKeyboardButton("تاریخ عضویت ⏱", callback_data="noop", style="primary")],
        [InlineKeyboardButton(fmt_money(user['balance']), callback_data="noop", style="primary"),
         InlineKeyboardButton("موجودی (تومان) 💳", callback_data="noop", style="primary")],
        [InlineKeyboardButton(str(referred), callback_data="noop", style="primary"),
         InlineKeyboardButton("تعداد زیرمجموعه 🌱", callback_data="noop", style="primary")],
        [InlineKeyboardButton(f"⏱ {jalali_today}", callback_data="noop", style="primary"),
         InlineKeyboardButton(f"📅 {weekday_fa}", callback_data="noop", style="primary"),
         InlineKeyboardButton(f"🕒 {time_str}", callback_data="noop", style="primary")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main", style="primary")],
    ]
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def noop_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دکمه‌های صرفاً نمایشی (بدون عملکرد) تو کارت حساب کاربری."""
    await update.callback_query.answer()


# ==================== کیف پول ====================
async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    user = get_user(uid)
    text = (
        f"💰 *اعتبار کیف پول*\n━━━━━━━━━━━━━━\n"
        f"💳 موجودی فعلی: *{fmt_money(user['balance'])}* تومان\n"
        f"📦 کانفیگ‌های خریداری‌شده: *{user['used_configs']}*\n"
        f"🧮 مجموع خرید: *{fmt_money(user['total_spent'])}* تومان"
    )
    kb = [
        [InlineKeyboardButton("💳شارژ کیف پول", callback_data="charge_wallet", style="primary")],
        [InlineKeyboardButton("📜 تاریخچه تراکنش‌ها", callback_data="tx_history", style="primary")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main", style="primary")],
    ]
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def tx_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    rows = db_all("SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 10", (uid,))
    if not rows:
        text = "📜 هنوز تراکنشی ثبت نشده."
    else:
        lines = ["📜 *۱۰ تراکنش اخیر*", "━━━━━━━━━━━━━━"]
        for r in rows:
            sign = "+" if r["amount"] >= 0 else ""
            try:
                date = datetime.fromtimestamp(r["date"]).strftime("%m-%d %H:%M")
            except Exception:
                date = "—"  # ردیف‌های خیلی قدیمی که تاریخ ندارن، کل تاریخچه رو نمی‌شکنن
            lines.append(f"{date} | {sign}{fmt_money(r['amount'])} | {md_escape(r['description'])}")
        text = "\n".join(lines)
    kb = [[InlineKeyboardButton("🔙 بازگشت", callback_data="wallet", style="primary")]]
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


# ==================== شارژ کیف پول ====================
def charge_amount_kb():
    keyboard = []
    row = []
    for amt in CHARGE_PRESETS:
        row.append(InlineKeyboardButton(f"{fmt_money(amt)} تومان", callback_data=f"charge_amt_{amt}", style="primary"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("✏️ مبلغ دلخواه", callback_data="charge_custom", style="primary")])
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="wallet", style="primary")])
    return InlineKeyboardMarkup(keyboard)


async def charge_wallet_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "💳 *شارژ کیف پول*\n"
        "━━━━━━━━━━━━━━\n"
        "مبلغ مورد نظرت رو انتخاب کن:"
    )
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=charge_amount_kb())


async def show_charge_payment(chat_send, amount: int, context: ContextTypes.DEFAULT_TYPE, edit_query=None):
    card = get_setting("card_number")
    holder = get_setting("card_holder")
    text = (
        "💳 *پرداخت شارژ کیف پول*\n"
        "━━━━━━━━━━━━━━\n"
        f"مبلغ انتخابی: *{fmt_money(amount)} تومان*\n\n"
        f"💳 شماره کارت: `{card}`\n"
        f"👤 به نام: {md_escape(holder)}\n\n"
        "⚠️ لطفاً دقیقاً همین مبلغ رو واریز کن.\n"
        "بعد از واریز، روی «ارسال فیش» بزن و عکس، گیف یا متن فیش واریزی رو بفرست."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 ارسال فیش", callback_data="charge_send_receipt", style="primary")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="charge_wallet", style="primary")],
    ])
    if edit_query is not None:
        await safe_edit(edit_query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    else:
        await chat_send(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def charge_amount_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    amount = int(context.match.group(1))
    # 🛡 فقط مبلغ‌های پیش‌فرض واقعی قبول میشه (جلوی callback جعلی با مبلغ دلخواه رو می‌گیره)
    if amount not in CHARGE_PRESETS:
        await query.answer("❌ مبلغ نامعتبره.", show_alert=True)
        return
    await query.answer()
    context.user_data["charge_amount"] = amount
    await show_charge_payment(None, amount, context, edit_query=query)


async def charge_custom_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["conv_return"] = "charge_wallet"
    await safe_edit(
        query,
        f"✏️ مبلغ دلخواه رو به تومان و فقط بصورت عدد بفرست:\n"
        f"(بین {fmt_money(MIN_CUSTOM_CHARGE)} تا {fmt_money(MAX_CUSTOM_CHARGE)} تومان)",
        reply_markup=cancel_kb()
    )
    return CHARGE_CUSTOM_AMOUNT


async def receive_charge_custom_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(",", "")
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ فقط عدد مثبت بفرست یا لغو کن.", reply_markup=cancel_kb())
        return CHARGE_CUSTOM_AMOUNT
    amount = int(text)
    # 🛡 حداقل/حداکثر شارژ (قبلاً تعریف شده بود ولی هیچ‌جا اعمال نمی‌شد)
    if amount < MIN_CUSTOM_CHARGE or amount > MAX_CUSTOM_CHARGE:
        await update.message.reply_text(
            f"❌ مبلغ باید بین {fmt_money(MIN_CUSTOM_CHARGE)} تا {fmt_money(MAX_CUSTOM_CHARGE)} تومان باشه. دوباره بفرست:",
            reply_markup=cancel_kb()
        )
        return CHARGE_CUSTOM_AMOUNT
    context.user_data["charge_amount"] = amount
    await show_charge_payment(update.message.reply_text, amount, context)
    return ConversationHandler.END


async def charge_send_receipt_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not context.user_data.get("charge_amount"):
        await safe_edit(query, "❌ اول یه مبلغ انتخاب کن.", reply_markup=charge_amount_kb())
        return ConversationHandler.END
    await safe_edit(
        query,
        "📤 حالا عکس، گیف یا متن فیش واریزی رو همینجا بفرست:",
        reply_markup=cancel_kb()
    )
    return CHARGE_RECEIPT


async def receive_charge_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = get_user(uid)
    amount = context.user_data.get("charge_amount")
    if not amount:
        await update.message.reply_text("❌ مشکلی پیش اومد، دوباره از «شارژ کیف پول» شروع کن.", reply_markup=main_menu())
        return ConversationHandler.END

    if update.message.photo:
        receipt_type = "عکس"
    elif update.message.animation:
        receipt_type = "گیف"
    elif update.message.text:
        receipt_type = "متن"
    else:
        await update.message.reply_text("❌ فقط عکس، گیف یا متن قابل قبوله. دوباره بفرست:", reply_markup=cancel_kb())
        return CHARGE_RECEIPT

    # 🛡 کپشن عکس/گیف هم به عنوان توضیح فیش ذخیره میشه (قبلاً گم می‌شد)
    note = update.message.text or update.message.caption or ""
    dep_id = db_run(
        "INSERT INTO deposits (user_id, amount, status, receipt_type, receipt_note, created_at) VALUES (?,?,?,?,?,?)",
        (uid, amount, "pending", receipt_type, note, time.time())
    ).lastrowid

    admin_notified = False
    if get_setting("deposit_notify", "1") == "1":
        try:
            info = (
                f"💳 *درخواست شارژ کیف پول #{dep_id}*\n"
                f"━━━━━━━━━━━━━━\n"
                f"👤 {md_escape(user['first_name'] or 'ناشناس')} (`{uid}`)\n"
                f"🔗 @{md_escape(user['username'] or '-')}\n"
                f"💰 مبلغ: {fmt_money(amount)} تومان\n"
                f"📎 نوع فیش: {receipt_type}"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ تایید", callback_data=f"dep_approve_{dep_id}", style="success"),
                 InlineKeyboardButton("❌ رد", callback_data=f"dep_reject_{dep_id}", style="danger")],
            ])
            await notify_admins(context, info, reply_markup=kb)
            admin_notified = True
            for aid in admin_ids():
                try:
                    await context.bot.copy_message(
                        chat_id=aid,
                        from_chat_id=update.effective_chat.id,
                        message_id=update.message.message_id,
                    )
                except Exception as e:
                    logger.warning("could not forward receipt to admin %s: %s", aid, e)
        except Exception as e:
            logger.warning("could not send deposit info to admins: %s", e)
    else:
        admin_notified = True  # ثبت شد؛ ادمین باید دستی از «درخواست‌های شارژ» چک کنه

    if admin_notified:
        await update.message.reply_text(
            "✅ فیش شما ثبت شد.\nبعد از تایید ادمین، کیف پولت شارژ میشه.",
            reply_markup=main_menu()
        )
    else:
        await update.message.reply_text(
            "⚠️ فیش شما ثبت شد ولی در ارسال پیام به ادمین مشکلی پیش اومد. با پشتیبانی تماس بگیر.",
            reply_markup=main_menu()
        )
    context.user_data.pop("charge_amount", None)
    return ConversationHandler.END


# ---- تایید/رد شارژ توسط ادمین ----
async def dep_approve_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    dep_id = int(context.match.group(1))
    dep = get_deposit(dep_id)
    if not dep:
        await query.answer("❌ این درخواست پیدا نشد.", show_alert=True)
        return
    if dep["status"] != "pending":
        await query.answer("❌ این درخواست قبلاً بررسی شده.", show_alert=True)
        return

    # 🛡 اگه کاربر از دیتابیس حذف شده باشه، به‌جای تایید بی‌اثر، به ادمین هشدار بده
    if not get_user(dep["user_id"]):
        await query.answer("❌ این کاربر دیگه تو دیتابیس نیست؛ درخواست دست‌نخورده موند.", show_alert=True)
        return

    # 🛡 اتمیک: اگه دو ادمین هم‌زمان بزنن، فقط یکی اعمال میشه (جلوی شارژ دوبار رو می‌گیره)
    cur = db_run("UPDATE deposits SET status='approved', decided_at=? WHERE id=? AND status='pending'",
                 (time.time(), dep_id))
    if cur.rowcount == 0:
        await query.answer("❌ این درخواست قبلاً بررسی شده.", show_alert=True)
        return
    db_run("UPDATE users SET balance=balance+? WHERE id=?", (dep["amount"], dep["user_id"]))
    log_tx(dep["user_id"], "charge_approved", dep["amount"], f"شارژ کیف پول (تایید #{dep_id})")

    await query.answer("✅ تایید شد")
    try:
        await send_sticker_safe(context, dep["user_id"], "deposit_approved")
        await context.bot.send_message(
            dep["user_id"],
            f"✅ شارژ کیف پول شما تایید شد!\n💰 مبلغ {fmt_money(dep['amount'])} تومان به کیف پولت اضافه شد.",
        )
    except Exception:
        pass

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    try:
        await query.message.reply_text(
            f"✅ درخواست #{dep_id} تایید شد و {fmt_money(dep['amount'])} تومان به کاربر `{dep['user_id']}` اضافه شد.",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        pass


async def dep_reject_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    dep_id = int(context.match.group(1))
    dep = get_deposit(dep_id)
    if not dep:
        await query.answer("❌ این درخواست پیدا نشد.", show_alert=True)
        return
    if dep["status"] != "pending":
        await query.answer("❌ این درخواست قبلاً بررسی شده.", show_alert=True)
        return

    cur = db_run("UPDATE deposits SET status='rejected', decided_at=? WHERE id=? AND status='pending'",
                 (time.time(), dep_id))
    if cur.rowcount == 0:
        await query.answer("❌ این درخواست قبلاً بررسی شده.", show_alert=True)
        return

    await query.answer("❌ رد شد")
    try:
        await context.bot.send_message(
            dep["user_id"],
            f"❌ متاسفانه فیش شارژ کیف پول (#{dep_id}) تایید نشد.\n"
            f"در صورت اعتراض به پشتیبانی @{get_support_username()} پیام بده."
        )
    except Exception:
        pass
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    try:
        await query.message.reply_text(f"❌ درخواست #{dep_id} رد شد.")
    except Exception:
        pass


async def admin_deposits_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    await query.answer()
    rows = db_all("SELECT * FROM deposits WHERE status='pending' ORDER BY id DESC LIMIT 15")
    if not rows:
        await safe_edit(query, "📭 درخواست شارژ در انتظار وجود نداره.", reply_markup=admin_menu())
        return
    text = "💳 *درخواست‌های شارژ در انتظار*\n━━━━━━━━━━━━━━\n"
    kb = []
    for r in rows:
        u = get_user(r["user_id"])
        name = md_escape(u["first_name"] or "ناشناس") if u else "حذف‌شده"
        text += f"#{r['id']} | {name} | {fmt_money(r['amount'])} تومان\n"
        kb.append([
            InlineKeyboardButton(f"✅ تایید #{r['id']}", callback_data=f"dep_approve_{r['id']}", style="success"),
            InlineKeyboardButton(f"❌ رد #{r['id']}", callback_data=f"dep_reject_{r['id']}", style="danger"),
        ])
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back", style="primary")])
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


# ==================== خرید کانفیگ (پلن‌ها + حجم دلخواه) ====================
async def buy_config_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """منوی خرید کانفیگ: پلن‌های آماده (مثل نامحدود) + گزینه‌ی حجم دلخواه."""
    query = update.callback_query
    await query.answer()
    price_gb = get_price_per_gb()
    text = (
        "🛒 *خرید کانفیگ*\n"
        "━━━━━━━━━━━━━━\n"
        "یکی از پلن‌های آماده رو انتخاب کن، یا حجم دلخواه خودت رو بخر:\n\n"
        f"💎 قیمت هر گیگ برای حجم دلخواه: {fmt_money(price_gb)} تومان"
    )
    kb = plan_buttons("buy")
    kb.append([InlineKeyboardButton("✏️ حجم دلخواه", callback_data="buy_custom_volume", style="primary")])
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back_main", style="primary")])
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def buy_config_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["conv_return"] = "buy_config"
    price_gb = get_price_per_gb()
    text = (
        "✏️ *خرید با حجم دلخواه*\n"
        "━━━━━━━━━━━━━━\n"
        f"💎 قیمت هر گیگابایت: {fmt_money(price_gb)} تومان\n"
        f"📏 حجم مجاز: بین {MIN_VOLUME_GB} تا {MAX_VOLUME_GB} گیگابایت\n\n"
        "حجم دلخواهت رو به گیگابایت (فقط عدد) بفرست:"
    )
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=cancel_kb())
    return ASK_VOLUME


async def receive_volume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip().replace(",", ".")
    try:
        volume = float(raw)
    except ValueError:
        await update.message.reply_text("❌ فقط عدد بفرست (مثلاً 20 یا 15.5) یا لغو کن.", reply_markup=cancel_kb())
        return ASK_VOLUME

    # 🛡 جلوی ورودی‌هایی مثل nan و inf که از فیلتر بالا رد میشن و بعداً کرش می‌دن
    if not math.isfinite(volume):
        await update.message.reply_text("❌ عدد نامعتبره. یه عدد عادی بفرست:", reply_markup=cancel_kb())
        return ASK_VOLUME

    if volume < MIN_VOLUME_GB or volume > MAX_VOLUME_GB:
        await update.message.reply_text(
            f"❌ حجم باید بین {MIN_VOLUME_GB} تا {MAX_VOLUME_GB} گیگابایت باشه. دوباره بفرست:",
            reply_markup=cancel_kb()
        )
        return ASK_VOLUME

    price_gb = get_price_per_gb()
    price = round(volume * price_gb)
    context.user_data["pending_volume"] = volume
    context.user_data["pending_price"] = price

    text, kb = _volume_confirm_text_kb(context)
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    return ConversationHandler.END


def _volume_confirm_text_kb(context):
    """صفحه‌ی تایید خرید حجم دلخواه (با پشتیبانی کد تخفیف)."""
    volume = context.user_data.get("pending_volume")
    price = context.user_data.get("pending_price")
    disc = _pending_discount_for(context, "volume")
    final_price = apply_discount(price, disc) if disc else price
    if disc:
        price_lines = (f"💰 قیمت کل: {fmt_money(price)} تومان\n"
                       f"🎟 با کد تخفیف ({discount_label(disc)}): *{fmt_money(final_price)} تومان*")
    else:
        price_lines = f"💰 قیمت کل: {fmt_money(price)} تومان"
    text = (
        "🧾 *تایید خرید*\n━━━━━━━━━━━━━━\n"
        f"📦 حجم: {fmt_volume(volume)} گیگابایت\n"
        f"{price_lines}\n\n"
        "آیا خرید رو تایید می‌کنی؟"
    )
    rows = [[InlineKeyboardButton("✅ تایید خرید", callback_data="cfg_confirm", style="success")]]
    if disc:
        rows.append([InlineKeyboardButton("🗑 حذف کد تخفیف", callback_data="disc_clear", style="danger")])
    else:
        rows.append([InlineKeyboardButton("🎟 کد تخفیف دارم", callback_data="disc_volume", style="success")])
    rows.append([InlineKeyboardButton("❌ انصراف", callback_data="cfg_cancel", style="danger")])
    return text, InlineKeyboardMarkup(rows)


async def cfg_cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("لغو شد")
    context.user_data.pop("pending_volume", None)
    context.user_data.pop("pending_price", None)
    context.user_data.pop("pending_discount", None)
    await safe_edit(query, "🚫 خرید لغو شد.", reply_markup=main_menu())


async def cfg_confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    volume = context.user_data.get("pending_volume")
    price = context.user_data.get("pending_price")

    if volume is None or price is None:
        await query.answer("❌ درخواست منقضی شده، دوباره تلاش کن.", show_alert=True)
        await safe_edit(query, "❌ درخواست منقضی شده. دوباره از «خرید کانفیگ» شروع کن.", reply_markup=main_menu())
        return

    user = get_user(uid)
    # 🎟 اعمال کد تخفیف (اگه برای همین خرید ثبت شده باشه)
    disc = _pending_discount_for(context, "volume")
    base_price = price
    price = apply_discount(base_price, disc) if disc else base_price
    saved = base_price - price

    # 💰 کسر اتمیک: فقط وقتی کم میشه که موجودی واقعاً کافی باشه
    # (ضد دوبار-کلیک، دو دستگاه هم‌زمان و منفی شدن موجودی)
    cur = db_run(
        "UPDATE users SET balance=balance-?, total_spent=total_spent+? "
        "WHERE id=? AND is_banned=0 AND balance>=?",
        (price, price, uid, price)
    )
    if cur.rowcount == 0:
        await query.answer("❌ موجودی کافی نیست!", show_alert=True)
        await safe_edit(
            query,
            f"❌ موجودی کافی نداری!\nلازم: {fmt_money(price)} تومان\nموجودی تو: {fmt_money(user['balance'])} تومان",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 شارژ کیف پول", callback_data="charge_wallet", style="primary")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main", style="primary")],
            ])
        )
        return
    # 🎟 مصرف اتمیک کد تخفیف؛ اگه همین لحظه نامعتبر شده باشه، پول کامل برمی‌گرده
    disc_note = ""
    if disc:
        if redeem_discount(disc, uid, saved):
            disc_note = f" | 🎟 {fmt_money(saved)} تومان تخفیف" if saved else ""
            context.user_data.pop("pending_discount", None)
        else:
            db_run("UPDATE users SET balance=balance+?, total_spent=total_spent-? WHERE id=?", (price, price, uid))
            context.user_data.pop("pending_discount", None)
            await query.answer("❌ این کد تخفیف دیگه معتبر نیست؛ بدون کد دوباره تایید کن.", show_alert=True)
            t, kb = _volume_confirm_text_kb(context)
            await safe_edit(query, t, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
            return

    order_id = db_run(
        "INSERT INTO config_orders (user_id, volume_gb, price, status, created_at) VALUES (?,?,?,?,?)",
        (uid, volume, price, "pending", time.time())
    ).lastrowid
    log_tx(uid, "purchase", -price, f"خرید کانفیگ {fmt_volume(volume)} گیگ (سفارش #{order_id}){disc_note}")

    context.user_data.pop("pending_volume", None)
    context.user_data.pop("pending_price", None)

    await query.answer("✅ ثبت شد!")
    await safe_edit(
        query,
        "✅ *خرید با موفقیت ثبت شد!*\n\n"
        f"📦 سفارش #{order_id} — {fmt_volume(volume)} گیگابایت\n"
        "کانفیگ به‌زودی توسط پشتیبانی برات ارسال میشه.\n"
        "💡 اگر مشکلی بود از بخش «پشتیبانی» پیام بده.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu()
    )

    if get_setting("purchase_notify") == "1":
        try:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("📤 ارسال کانفیگ", callback_data=f"sendcfg_order_{order_id}", style="primary")
            ]])
            await notify_admins(
                context,
                f"🛍 *خرید جدید*\n━━━━━━━━━━━━━━\n"
                f"👤 {md_escape(user['first_name'] or 'ناشناس')} (`{uid}`)\n"
                f"📦 حجم: {fmt_volume(volume)} گیگابایت\n"
                f"💰 {fmt_money(price)} تومان\n"
                f"🆔 سفارش #{order_id}",
                reply_markup=kb
            )
        except Exception:
            pass


# ---- ارسال کانفیگ به خریدار توسط ادمین ----
async def admin_sendcfg_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    order_id = int(context.match.group(1))
    order = get_order(order_id)
    if not order:
        await query.message.reply_text("❌ این سفارش پیدا نشد.", reply_markup=admin_menu())
        return ConversationHandler.END
    if order["status"] == "delivered":
        await query.message.reply_text("ℹ️ کانفیگ این سفارش قبلاً ارسال شده.", reply_markup=admin_menu())
        return ConversationHandler.END

    # اگه ادمین وسط یه ارسال کانفیگ دیگه بود، اینجا هدف رو عوض می‌کنیم (رفع باگ بی‌پاسخ ماندن دکمه)
    context.user_data["order_target_id"] = order_id
    context.user_data["order_target_uid"] = order["user_id"]
    await query.message.reply_text(
        f"📤 کانفیگ (متن، عکس یا فایل) رو بفرست تا برای خریدار سفارش #{order_id} (`{order['user_id']}`) ارسال بشه:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=cancel_kb()
    )
    return ADMIN_SEND_CFG


async def receive_admin_send_cfg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    target_uid = context.user_data.get("order_target_uid")
    order_id = context.user_data.get("order_target_id")
    if not target_uid or not order_id:
        await update.message.reply_text("❌ سفارش مشخص نیست.", reply_markup=admin_menu())
        return ConversationHandler.END

    try:
        await context.bot.copy_message(
            chat_id=target_uid,
            from_chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
        )
        db_run("UPDATE config_orders SET status='delivered', delivered_at=? WHERE id=?", (time.time(), order_id))
        db_run("UPDATE users SET used_configs=used_configs+1 WHERE id=?", (target_uid,))
        await send_sticker_safe(context, target_uid, "purchase_success")
        # 🔙 برگشت به لیست سفارش‌های در انتظار (اگه سفارش دیگه‌ای مونده باشه، همون‌جا آماده‌ست)
        t, kb = _pending_orders_text_kb()
        await update.message.reply_text(
            f"✅ کانفیگ برای خریدار `{target_uid}` (سفارش #{order_id}) ارسال شد.\n\n{t}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb
        )
    except Exception as e:
        await update.message.reply_text(f"❌ ارسال ناموفق بود: {e}", reply_markup=admin_menu())

    context.user_data.pop("order_target_uid", None)
    context.user_data.pop("order_target_id", None)
    return ConversationHandler.END


# ---- پنل مدیریت حجم و کانفیگ (ادمین) ----
def _orders_menu_text_kb():
    price_gb = get_price_per_gb()
    pending_count = db_one("SELECT COUNT(*) c FROM config_orders WHERE status='pending'")["c"]
    text = (
        "📦 *مدیریت حجم و کانفیگ‌ها*\n"
        "━━━━━━━━━━━━━━\n"
        f"💎 قیمت فعلی هر گیگ: {fmt_money(price_gb)} تومان\n"
        f"📥 سفارش‌های در انتظار ارسال: {pending_count}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 تغییر قیمت هر گیگ", callback_data="admin_set_price", style="primary")],
        [InlineKeyboardButton("📥 سفارش‌های در انتظار", callback_data="admin_pending_orders", style="primary")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back", style="primary")],
    ])
    return text, kb


async def admin_orders_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    await query.answer()
    text, kb = _orders_menu_text_kb()
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


def _pending_orders_text_kb():
    rows = db_all("SELECT * FROM config_orders WHERE status='pending' ORDER BY id DESC LIMIT 15")
    if not rows:
        return "📭 سفارش در انتظاری وجود نداره.", InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back", style="primary")]])
    text = "📥 *سفارش‌های در انتظار ارسال*\n━━━━━━━━━━━━━━\n"
    kb = []
    for r in rows:
        u = get_user(r["user_id"])
        name = md_escape(u["first_name"] or "ناشناس") if u else "حذف‌شده"
        text += f"#{r['id']} | {name} | {md_escape(order_desc(r))} | {fmt_money(r['price'])} تومان\n"
        kb.append([InlineKeyboardButton(f"📤 ارسال کانفیگ #{r['id']}", callback_data=f"sendcfg_order_{r['id']}", style="primary")])
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back", style="primary")])
    return text, InlineKeyboardMarkup(kb)


async def admin_pending_orders_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    await query.answer()
    text, kb = _pending_orders_text_kb()
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def admin_set_price_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    context.user_data["conv_return"] = "admin_orders_menu"
    await safe_edit(
        query,
        f"💎 قیمت فعلی: {fmt_money(get_price_per_gb())} تومان به ازای هر گیگ\n\nقیمت جدید هر گیگ رو به تومان بفرست:",
        reply_markup=cancel_kb()
    )
    return SET_PRICE_PER_GB


async def receive_price_per_gb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(",", "")
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ فقط عدد مثبت بفرست یا لغو کن.", reply_markup=cancel_kb())
        return SET_PRICE_PER_GB
    set_setting("price_per_gb", text)
    t, kb = _orders_menu_text_kb()
    await update.message.reply_text(
        f"✅ قیمت هر گیگ روی {fmt_money(int(text))} تومان تنظیم شد.\n\n{t}",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb
    )
    return ConversationHandler.END


# ==================== پلن‌ها (خرید اتوماتیک + پلن‌های بخش خرید کانفیگ) ====================
def get_plan(pid: int):
    return db_one("SELECT * FROM plans WHERE id=?", (pid,))


def all_plans():
    return db_all("SELECT * FROM plans ORDER BY sort_order, id")


def plans_for(place: str):
    """پلن‌های فعالی که باید تو این بخش نمایش داده بشن (place: 'auto' یا 'buy')."""
    return db_all(
        "SELECT * FROM plans WHERE is_active=1 AND (show_in=? OR show_in='both') ORDER BY sort_order, id",
        (place,),
    )


def plan_stock(pid: int) -> int:
    """تعداد کانفیگ‌های آماده‌ی موجود در انبار این پلن."""
    return db_one(
        "SELECT COUNT(*) c FROM auto_configs WHERE plan_id=? AND status='available'", (pid,)
    )["c"]


def plan_confirm_text(plan) -> str:
    """متن تاییدیه‌ی خرید پلن (اگه ادمین متن اختصاصی گذاشته باشه همون میاد،
    مثل: «آیا تایید می‌کنید خرید کانفیگ تک سروره آمریکا نامحدود را؟»)."""
    if plan["confirm_text"]:
        return plan["confirm_text"]
    return f"آیا تایید می‌کنید خرید «{plan['name']}» را؟"


def plan_buttons(place: str):
    """دکمه‌های شیشه‌ای رنگی پلن‌ها برای منوهای خرید (هم‌استایل بقیه‌ی بات)."""
    keyboard = []
    for p in plans_for(place):
        label = f"{p['name']} | {fmt_money(p['price'])} تومان"
        if p["delivery_mode"] == "auto" and plan_stock(p["id"]) == 0:
            label += " (ناموجود)"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"plan_sel_{p['id']}", style="success")])
    return keyboard


def auto_buy_menu_kb():
    keyboard = plan_buttons("auto")
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="back_main", style="primary")])
    return InlineKeyboardMarkup(keyboard)


async def auto_buy_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "⚡️ *خرید اتوماتیک*\n"
        "━━━━━━━━━━━━━━\n"
        "پلن مورد نظرت رو انتخاب کن؛ اگه کانفیگ آماده تو انبار باشه، همون لحظه تحویل می‌گیری:"
    )
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=auto_buy_menu_kb())


async def _show_plan_confirm(query, context, pid: int):
    """صفحه‌ی تایید خرید پلن (با پشتیبانی کد تخفیف)."""
    plan = get_plan(pid)
    if not plan or not plan["is_active"]:
        try:
            await query.answer("❌ این پلن دیگه فعال نیست.", show_alert=True)
        except Exception:
            pass
        return

    uid = query.from_user.id
    user = get_user(uid)
    if not user:
        try:
            await query.answer("❌ اول /start رو بزن.", show_alert=True)
        except Exception:
            pass
        return
    try:
        await query.answer()
    except Exception:
        pass

    price = int(plan["price"])
    disc = _pending_discount_for(context, "plan", pid)
    final_price = apply_discount(price, disc) if disc else price

    if user["balance"] < final_price:
        kb_rows = [[InlineKeyboardButton("💳 شارژ کیف پول", callback_data="charge_wallet", style="primary")]]
        if not disc:
            kb_rows.append([InlineKeyboardButton("🎟 کد تخفیف دارم", callback_data=f"disc_plan_{pid}", style="success")])
        kb_rows.append([InlineKeyboardButton("🔙 بازگشت", callback_data="auto_buy_menu", style="primary")])
        await safe_edit(
            query,
            f"❌ موجودی کافی نداری!\n💎 قیمت پلن: {fmt_money(final_price)} تومان\n💰 موجودی تو: {fmt_money(user['balance'])} تومان",
            reply_markup=InlineKeyboardMarkup(kb_rows)
        )
        return

    stock = plan_stock(pid)
    if plan["delivery_mode"] == "auto" and stock == 0:
        await safe_edit(
            query,
            "❌ فعلاً کانفیگی برای این پلن موجود نیست. بعداً دوباره سر بزن.",
            reply_markup=auto_buy_menu_kb()
        )
        return

    if stock > 0:
        delivery_line = "بعد از تایید، کانفیگ *بلافاصله* برات ارسال میشه."
    else:
        delivery_line = "بعد از تایید، سفارشت ثبت میشه و کانفیگ *توسط پشتیبانی* برات ارسال میشه."

    if disc:
        price_lines = (f"💰 قیمت: {fmt_money(price)} تومان\n"
                       f"🎟 با کد تخفیف ({discount_label(disc)}): *{fmt_money(final_price)} تومان*")
    else:
        price_lines = f"💰 قیمت: {fmt_money(price)} تومان"

    text = (
        "🧾 *تایید خرید*\n━━━━━━━━━━━━━━\n"
        f"❓ {md_escape(plan_confirm_text(plan))}\n\n"
        f"📦 پلن: {md_escape(plan['name'])}\n"
        f"{price_lines}\n\n"
        f"{delivery_line}"
    )
    kb_rows = [[InlineKeyboardButton("✅ بله، تایید می‌کنم", callback_data=f"plan_ok_{pid}", style="success")]]
    if disc:
        kb_rows.append([InlineKeyboardButton("🗑 حذف کد تخفیف", callback_data="disc_clear", style="danger")])
    else:
        kb_rows.append([InlineKeyboardButton("🎟 کد تخفیف دارم", callback_data=f"disc_plan_{pid}", style="success")])
    kb_rows.append([InlineKeyboardButton("❌ انصراف", callback_data="auto_buy_menu", style="danger")])
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb_rows))


async def plan_select_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """کاربر روی یه پلن (مثل «نامحدود») کلیک کرده → نمایش متن تاییدیه‌ی همون پلن."""
    await _show_plan_confirm(update.callback_query, context, int(context.match.group(1)))


async def plan_confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تایید نهایی خرید پلن. اول پول به صورت اتمیک کم میشه، بعد:
    - اگه انبار کانفیگ آماده داشت → تحویل آنی
    - اگه نداشت و پلن دستی/ترکیبی بود → ثبت سفارش برای ارسال توسط ادمین
    - اگه نداشت و پلن فقط-آنی بود → برگشت کامل وجه"""
    query = update.callback_query
    pid = int(context.match.group(1))
    plan = get_plan(pid)
    if not plan or not plan["is_active"]:
        await query.answer("❌ این پلن دیگه فعال نیست.", show_alert=True)
        return

    uid = query.from_user.id
    user = get_user(uid)
    if not user:
        await query.answer("❌ اول /start رو بزن.", show_alert=True)
        return

    base_price = int(plan["price"])
    pname = plan["name"]
    disc = _pending_discount_for(context, "plan", pid)
    price = apply_discount(base_price, disc) if disc else base_price
    saved = base_price - price

    # 💰 کسر اتمیک: ضد دوبار-کلیک، دو دستگاه هم‌زمان و منفی شدن موجودی
    cur = db_run(
        "UPDATE users SET balance=balance-?, total_spent=total_spent+? "
        "WHERE id=? AND is_banned=0 AND balance>=?",
        (price, price, uid, price)
    )
    if cur.rowcount == 0:
        await query.answer("❌ موجودی کافی نیست!", show_alert=True)
        return

    # 🎟 مصرف اتمیک کد تخفیف؛ اگه همین لحظه نامعتبر شده باشه، پول کامل برمی‌گرده
    disc_id = None
    if disc:
        if redeem_discount(disc, uid, saved):
            disc_id = disc["id"]
            context.user_data.pop("pending_discount", None)
        else:
            db_run("UPDATE users SET balance=balance+?, total_spent=total_spent-? WHERE id=?", (price, price, uid))
            context.user_data.pop("pending_discount", None)
            try:
                await query.answer("❌ این کد تخفیف دیگه معتبر نیست؛ بدون کد دوباره امتحان کن.", show_alert=True)
            except Exception:
                pass
            await _show_plan_confirm(query, context, pid)
            return

    # ⚡️ تلاش برای تحویل آنی از انبار (پلن‌های auto و hybrid)
    delivered = False
    cfg_id = None
    if plan["delivery_mode"] in ("auto", "hybrid"):
        for _ in range(3):
            row = db_one(
                "SELECT id, source_chat_id, source_message_id FROM auto_configs "
                "WHERE plan_id=? AND status='available' ORDER BY id LIMIT 1",
                (pid,)
            )
            if not row:
                break
            # رزرو اتمیک: اگه یه ریکوئست دیگه زودتر برده باشتش، میریم سراغ ردیف بعدی
            claim = db_run(
                "UPDATE auto_configs SET status='delivered', delivered_to=?, delivered_at=? "
                "WHERE id=? AND status='available'",
                (uid, time.time(), row["id"])
            )
            if claim.rowcount == 0:
                continue
            cfg_id = row["id"]
            try:
                await context.bot.copy_message(
                    chat_id=uid,
                    from_chat_id=row["source_chat_id"],
                    message_id=row["source_message_id"],
                )
                delivered = True
            except Exception as e:
                logger.error("plan delivery failed for %s: %s", uid, e)
                # کانفیگ هدر نره؛ برگرده به انبار (اگه پلن hybrid باشه سفارش دستی ثبت میشه)
                db_run("UPDATE auto_configs SET status='available', delivered_to=NULL, delivered_at=NULL WHERE id=?",
                       (cfg_id,))
                cfg_id = None
            break

    disc_note = f" | 🎟 {fmt_money(saved)} تومان تخفیف" if disc_id and saved else ""

    if delivered:
        db_run("UPDATE users SET used_configs=used_configs+1 WHERE id=?", (uid,))
        log_tx(uid, "plan_purchase", -price, f"خرید پلن «{pname}» (کانفیگ #{cfg_id}){disc_note}")
        await query.answer("✅ ارسال شد!")
        await send_sticker_safe(context, uid, "purchase_success")
        disc_line = f"\n🎟 {fmt_money(saved)} تومان تخفیف گرفتی!" if disc_id and saved else ""
        await safe_edit(
            query,
            f"✅ *خرید موفق بود!*\n\n📦 پلن «{md_escape(pname)}» با موفقیت ارسال شد.{disc_line}\nاز خریدت ممنونیم 🌟",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu()
        )
        if get_setting("purchase_notify") == "1":
            try:
                await notify_admins(
                    context,
                    f"⚡️ *خرید پلن (تحویل آنی)*\n━━━━━━━━━━━━━━\n"
                    f"👤 {md_escape(user['first_name'] or 'ناشناس')} (`{uid}`)\n"
                    f"📦 پلن: {md_escape(pname)}\n"
                    f"💰 {fmt_money(price)} تومان\n"
                    f"🆔 کانفیگ #{cfg_id} تحویل داده شد."
                )
            except Exception:
                pass
        return

    if plan["delivery_mode"] == "auto":
        # انبار خالی/ارسال ناموفق و این پلن فقط تحویل آنی داره → برگشت کامل وجه (+ برگشت کد تخفیف)
        db_run("UPDATE users SET balance=balance+?, total_spent=total_spent-? WHERE id=?", (price, price, uid))
        if disc_id:
            refund_discount(disc_id, uid)
        log_tx(uid, "plan_refund", price, f"برگشت وجه پلن «{pname}» (انبار خالی)")
        await query.answer("❌ همین الان تموم شد!", show_alert=True)
        await safe_edit(
            query,
            "❌ موجودی انبار این پلن همین الان تموم شد و هیچ مبلغی از حسابت کم نشد.\nبعداً دوباره امتحان کن.",
            reply_markup=auto_buy_menu_kb()
        )
        return

    # 👤 سفارش دستی (پلن manual، یا hybrid با انبار خالی): ادمین کانفیگ رو می‌فرسته
    order_id = db_run(
        "INSERT INTO config_orders (user_id, volume_gb, price, status, created_at, plan_id) VALUES (?,?,?,?,?,?)",
        (uid, None, price, "pending", time.time(), pid)
    ).lastrowid
    log_tx(uid, "plan_purchase", -price, f"خرید پلن «{pname}» (سفارش #{order_id}){disc_note}")
    await query.answer("✅ ثبت شد!")
    await safe_edit(
        query,
        "✅ *خرید با موفقیت ثبت شد!*\n\n"
        f"📦 سفارش #{order_id} — {md_escape(pname)}\n"
        "کانفیگ به‌زودی توسط پشتیبانی برات ارسال میشه.\n"
        "💡 اگر مشکلی بود از بخش «پشتیبانی» پیام بده.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu()
    )
    if get_setting("purchase_notify") == "1":
        try:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("📤 ارسال کانفیگ", callback_data=f"sendcfg_order_{order_id}", style="primary")
            ]])
            await notify_admins(
                context,
                f"🛍 *خرید پلن (در انتظار ارسال دستی)*\n━━━━━━━━━━━━━━\n"
                f"👤 {md_escape(user['first_name'] or 'ناشناس')} (`{uid}`)\n"
                f"📦 پلن: {md_escape(pname)}\n"
                f"💰 {fmt_money(price)} تومان\n"
                f"🆔 سفارش #{order_id}",
                reply_markup=kb
            )
        except Exception:
            pass


async def auto_cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("لغو شد")
    await safe_edit(query, "🚫 خرید لغو شد.", reply_markup=main_menu())


# ---- مدیریت انبار کانفیگ‌های آماده‌ی پلن‌ها (ادمین) ----
def admin_auto_menu_kb():
    keyboard = []
    for p in all_plans():
        if p["delivery_mode"] not in ("auto", "hybrid"):
            continue
        left = plan_stock(p["id"])
        keyboard.append([InlineKeyboardButton(
            f"➕ {p['name']} (موجود: {left})", callback_data=f"auto_add_pkg_{p['id']}", style="success")])
    keyboard.append([InlineKeyboardButton("🧩 مدیریت پلن‌ها", callback_data="admin_plans_menu", style="primary")])
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back", style="primary")])
    return InlineKeyboardMarkup(keyboard)


def _auto_menu_text_kb():
    lines = ["⚡️ *انبار کانفیگ‌های آماده*", "━━━━━━━━━━━━━━"]
    has_any = False
    for p in all_plans():
        if p["delivery_mode"] in ("auto", "hybrid"):
            has_any = True
            lines.append(f"📦 {md_escape(p['name'])} | {fmt_money(p['price'])} تومان | موجودی انبار: {plan_stock(p['id'])}")
    if not has_any:
        lines.append("هیچ پلنی با تحویل آنی تعریف نشده. از «🧩 مدیریت پلن‌ها» نحوه تحویل رو تغییر بده.")
    lines.append("\nهر کانفیگ فقط یک‌بار برای یک مشتری ارسال میشه و بعدش خودکار از انبار کم میشه.")
    return "\n".join(lines), admin_auto_menu_kb()


async def admin_auto_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    await query.answer()
    text, kb = _auto_menu_text_kb()
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def admin_auto_add_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    pid = int(context.match.group(1))
    plan = get_plan(pid)
    if not plan:
        await query.message.reply_text("❌ این پلن پیدا نشد.", reply_markup=admin_menu())
        return ConversationHandler.END
    context.user_data["auto_add_plan"] = pid
    left = plan_stock(pid)
    await safe_edit(
        query,
        f"📦 افزودن کانفیگ به انبار پلن *{md_escape(plan['name'])}* (فعلاً {left} تا موجوده)\n\n"
        "کانفیگ رو بفرست (متن، عکس یا فایل). می‌تونی پشت‌سرهم چندتا بفرستی، هرکدوم فقط برای یک نفر ارسال میشه.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=cancel_kb()
    )
    return ADMIN_AUTO_ADD_CFG


async def receive_auto_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    pid = context.user_data.get("auto_add_plan")
    plan = get_plan(pid) if pid else None
    if not plan:
        await update.message.reply_text("❌ پلن مشخص نیست، دوباره از منو شروع کن.", reply_markup=admin_menu())
        return ConversationHandler.END

    db_run(
        "INSERT INTO auto_configs (plan_id, package_gb, source_chat_id, source_message_id, status, added_by, added_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (pid, plan["legacy_gb"], update.effective_chat.id, update.message.message_id, "available",
         update.effective_user.id, time.time())
    )
    left = plan_stock(pid)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ افزودن کانفیگ بعدی", callback_data="auto_add_more", style="success")],
        [InlineKeyboardButton("✅ پایان", callback_data="auto_add_finish", style="success")],
    ])
    await update.message.reply_text(
        f"✅ کانفیگ اضافه شد. موجودی فعلی انبار «{plan['name']}»: {left} تا.\n\nمی‌خوای یکی دیگه اضافه کنی؟",
        reply_markup=kb
    )
    return ADMIN_AUTO_ADD_CFG


async def auto_add_more_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pid = context.user_data.get("auto_add_plan")
    plan = get_plan(pid) if pid else None
    if not plan:
        await query.message.reply_text("❌ پلن مشخص نیست، دوباره از منو شروع کن.", reply_markup=admin_menu())
        return ConversationHandler.END
    await query.message.reply_text(
        f"📦 کانفیگ بعدی برای انبار «{plan['name']}» رو بفرست:",
        reply_markup=cancel_kb()
    )
    return ADMIN_AUTO_ADD_CFG


async def auto_add_finish_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("تمام شد ✅")
    context.user_data.pop("auto_add_plan", None)
    # 🔙 برگشت به همون منوی انبار (نه پرت شدن به پنل اصلی)
    t, kb = _auto_menu_text_kb()
    await query.message.reply_text(f"✅ افزودن کانفیگ‌ها تموم شد.\n\n{t}",
                                   parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    return ConversationHandler.END


# ==================== 🧩 مدیریت پلن‌ها (ادمین) ====================
PLAN_MODE_ORDER = ["hybrid", "auto", "manual"]
DELIVERY_LABELS = {
    "auto": "⚡️ فقط آنی از انبار",
    "manual": "👤 فقط دستی توسط ادمین",
    "hybrid": "⚡️+👤 آنی؛ اگه انبار خالی بود، دستی",
}
PLAN_SHOW_ORDER = ["both", "auto", "buy"]
SHOW_LABELS = {
    "auto": "فقط «خرید اتوماتیک»",
    "buy": "فقط «خرید کانفیگ»",
    "both": "هر دو بخش",
}


async def admin_plans_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    await query.answer()
    text = (
        "🧩 *مدیریت پلن‌ها*\n━━━━━━━━━━━━━━\n"
        "از اینجا می‌تونی پلن جدید بسازی (مثل «نامحدود»)، قیمت‌ها رو عوض کنی،\n"
        "متن تاییدیه‌ی خرید هر پلن رو تنظیم کنی و نحوه تحویل رو مشخص کنی.\n\n"
        "روی هر پلن بزن تا مدیریتش کنی:"
    )
    kb = []
    for p in all_plans():
        status = "🟢" if p["is_active"] else "🔴"
        kb.append([InlineKeyboardButton(
            f"{status} {p['name']} | {fmt_money(p['price'])} تومان",
            callback_data=f"padm_{p['id']}", style="primary")])
    kb.append([InlineKeyboardButton("➕ ساخت پلن جدید", callback_data="padm_new", style="success")])
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back", style="primary")])
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


def _plan_admin_text_kb(plan):
    pid = plan["id"]
    stock = plan_stock(pid)
    active = "🟢 فعال" if plan["is_active"] else "🔴 غیرفعال"
    text = (
        "🧩 *جزئیات پلن*\n━━━━━━━━━━━━━━\n"
        f"📛 نام: {md_escape(plan['name'])}\n"
        f"💰 قیمت: {fmt_money(plan['price'])} تومان\n"
        f"🚚 نحوه تحویل: {DELIVERY_LABELS.get(plan['delivery_mode'], plan['delivery_mode'])}\n"
        f"👁 محل نمایش: {SHOW_LABELS.get(plan['show_in'], plan['show_in'])}\n"
        f"📦 موجودی انبار: {stock}\n"
        f"وضعیت: {active}\n\n"
        f"📝 متن تاییدیه‌ی خرید:\n«{md_escape(plan_confirm_text(plan))}»"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 تغییر قیمت", callback_data=f"padm_price_{pid}", style="success"),
         InlineKeyboardButton("✏️ تغییر نام", callback_data=f"padm_name_{pid}", style="primary")],
        [InlineKeyboardButton("📝 تغییر متن تاییدیه", callback_data=f"padm_text_{pid}", style="primary")],
        [InlineKeyboardButton("🚚 تغییر نحوه تحویل", callback_data=f"padm_mode_{pid}", style="primary"),
         InlineKeyboardButton("👁 تغییر محل نمایش", callback_data=f"padm_show_{pid}", style="primary")],
        [InlineKeyboardButton("🔴 غیرفعال کردن" if plan["is_active"] else "🟢 فعال کردن",
                              callback_data=f"padm_toggle_{pid}",
                              style="danger" if plan["is_active"] else "success")],
        [InlineKeyboardButton(f"➕ افزودن کانفیگ به انبار (موجود: {stock})",
                              callback_data=f"auto_add_pkg_{pid}", style="success")],
        [InlineKeyboardButton("🗑 حذف پلن", callback_data=f"padm_del_{pid}", style="danger")],
        [InlineKeyboardButton("🔙 لیست پلن‌ها", callback_data="admin_plans_menu", style="primary")],
    ])
    return text, kb


async def _render_plan_admin(query, pid: int):
    plan = get_plan(pid)
    if not plan:
        await safe_edit(query, "❌ این پلن پیدا نشد.", reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 لیست پلن‌ها", callback_data="admin_plans_menu", style="primary")]]))
        return
    text, kb = _plan_admin_text_kb(plan)
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def plan_admin_view_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    await query.answer()
    await _render_plan_admin(query, int(context.match.group(1)))


async def padm_mode_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    pid = int(context.match.group(1))
    plan = get_plan(pid)
    if not plan:
        await query.answer("❌ پیدا نشد.", show_alert=True)
        return
    cur_mode = plan["delivery_mode"] if plan["delivery_mode"] in PLAN_MODE_ORDER else "hybrid"
    new_mode = PLAN_MODE_ORDER[(PLAN_MODE_ORDER.index(cur_mode) + 1) % len(PLAN_MODE_ORDER)]
    db_run("UPDATE plans SET delivery_mode=? WHERE id=?", (new_mode, pid))
    await query.answer(DELIVERY_LABELS[new_mode])
    await _render_plan_admin(query, pid)


async def padm_show_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    pid = int(context.match.group(1))
    plan = get_plan(pid)
    if not plan:
        await query.answer("❌ پیدا نشد.", show_alert=True)
        return
    cur_show = plan["show_in"] if plan["show_in"] in PLAN_SHOW_ORDER else "both"
    new_show = PLAN_SHOW_ORDER[(PLAN_SHOW_ORDER.index(cur_show) + 1) % len(PLAN_SHOW_ORDER)]
    db_run("UPDATE plans SET show_in=? WHERE id=?", (new_show, pid))
    await query.answer(SHOW_LABELS[new_show])
    await _render_plan_admin(query, pid)


async def padm_toggle_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    pid = int(context.match.group(1))
    plan = get_plan(pid)
    if not plan:
        await query.answer("❌ پیدا نشد.", show_alert=True)
        return
    new_val = 0 if plan["is_active"] else 1
    db_run("UPDATE plans SET is_active=? WHERE id=?", (new_val, pid))
    await query.answer("🟢 فعال شد" if new_val else "🔴 غیرفعال شد")
    await _render_plan_admin(query, pid)


async def padm_del_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    await query.answer()
    pid = int(context.match.group(1))
    plan = get_plan(pid)
    if not plan:
        await _render_plan_admin(query, pid)
        return
    stock = plan_stock(pid)
    text = (
        f"⚠️ *تایید حذف پلن*\n\nمطمئنی می‌خوای پلن «{md_escape(plan['name'])}» رو برای همیشه حذف کنی؟\n"
        f"📦 {stock} کانفیگ استفاده‌نشده تو انبارش هم حذف میشه.\nاین کار قابل بازگشت نیست!"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ بله، حذف کن", callback_data=f"padm_delok_{pid}", style="danger"),
         InlineKeyboardButton("🚫 نه، لغو", callback_data=f"padm_{pid}", style="primary")],
    ])
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def padm_delok_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    pid = int(context.match.group(1))
    db_run("DELETE FROM auto_configs WHERE plan_id=? AND status='available'", (pid,))
    db_run("DELETE FROM plans WHERE id=?", (pid,))
    await query.answer("🗑 حذف شد")
    await admin_plans_menu_cb(update, context)


def _back_to_plan_kb(pid):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت به پلن", callback_data=f"padm_{pid}", style="primary")]])


# ---- ویرایش قیمت / نام / متن تاییدیه ----
async def padm_price_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    pid = int(context.match.group(1))
    plan = get_plan(pid)
    if not plan:
        await query.message.reply_text("❌ پلن پیدا نشد.", reply_markup=admin_menu())
        return ConversationHandler.END
    context.user_data["plan_edit_id"] = pid
    await safe_edit(
        query,
        f"💰 قیمت فعلی «{md_escape(plan['name'])}»: {fmt_money(plan['price'])} تومان\n\n"
        "قیمت جدید رو به تومان (فقط عدد) بفرست:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=cancel_kb()
    )
    return PLAN_EDIT_PRICE


async def receive_plan_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    pid = context.user_data.get("plan_edit_id")
    text = update.message.text.strip().replace(",", "")
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ فقط عدد مثبت بفرست یا لغو کن.", reply_markup=cancel_kb())
        return PLAN_EDIT_PRICE
    if not pid or not get_plan(pid):
        await update.message.reply_text("❌ پلن مشخص نیست.", reply_markup=admin_menu())
        return ConversationHandler.END
    db_run("UPDATE plans SET price=? WHERE id=?", (int(text), pid))
    t, kb = _plan_admin_text_kb(get_plan(pid))
    await update.message.reply_text(f"✅ قیمت پلن روی {fmt_money(int(text))} تومان تنظیم شد.\n\n{t}",
                                     parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    context.user_data.pop("plan_edit_id", None)
    return ConversationHandler.END


async def padm_name_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    pid = int(context.match.group(1))
    plan = get_plan(pid)
    if not plan:
        await query.message.reply_text("❌ پلن پیدا نشد.", reply_markup=admin_menu())
        return ConversationHandler.END
    context.user_data["plan_edit_id"] = pid
    await safe_edit(
        query,
        f"✏️ نام فعلی: {md_escape(plan['name'])}\n\nنام جدید پلن رو بفرست:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=cancel_kb()
    )
    return PLAN_EDIT_NAME


async def receive_plan_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    pid = context.user_data.get("plan_edit_id")
    name = update.message.text.strip()
    if not name or len(name) > 60:
        await update.message.reply_text("❌ یه نام معتبر (حداکثر ۶۰ حرف) بفرست یا لغو کن.", reply_markup=cancel_kb())
        return PLAN_EDIT_NAME
    if not pid or not get_plan(pid):
        await update.message.reply_text("❌ پلن مشخص نیست.", reply_markup=admin_menu())
        return ConversationHandler.END
    db_run("UPDATE plans SET name=? WHERE id=?", (name, pid))
    t, kb = _plan_admin_text_kb(get_plan(pid))
    await update.message.reply_text(f"✅ نام پلن بروزرسانی شد.\n\n{t}",
                                    parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    context.user_data.pop("plan_edit_id", None)
    return ConversationHandler.END


async def padm_text_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    pid = int(context.match.group(1))
    plan = get_plan(pid)
    if not plan:
        await query.message.reply_text("❌ پلن پیدا نشد.", reply_markup=admin_menu())
        return ConversationHandler.END
    context.user_data["plan_edit_id"] = pid
    await safe_edit(
        query,
        "📝 متن فعلی تاییدیه‌ی خرید:\n"
        f"«{md_escape(plan_confirm_text(plan))}»\n\n"
        "متن جدید رو بفرست (مثلاً: آیا تایید می‌کنید خرید کانفیگ تک سروره آمریکا نامحدود را؟)\n"
        "یا فقط «-» بفرست تا متن پیش‌فرض ساخته بشه:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=cancel_kb()
    )
    return PLAN_EDIT_TEXT


async def receive_plan_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    pid = context.user_data.get("plan_edit_id")
    raw = update.message.text.strip()
    if not pid or not get_plan(pid):
        await update.message.reply_text("❌ پلن مشخص نیست.", reply_markup=admin_menu())
        return ConversationHandler.END
    ctext = None if raw == "-" else raw[:500]
    db_run("UPDATE plans SET confirm_text=? WHERE id=?", (ctext, pid))
    t, kb = _plan_admin_text_kb(get_plan(pid))
    await update.message.reply_text(f"✅ متن تاییدیه‌ی خرید بروزرسانی شد.\n\n{t}",
                                    parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    context.user_data.pop("plan_edit_id", None)
    return ConversationHandler.END


# ---- ساخت پلن جدید (ویزارد سه‌مرحله‌ای) ----
async def padm_new_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    context.user_data["conv_return"] = "admin_plans_menu"
    await safe_edit(
        query,
        "➕ *ساخت پلن جدید*\n━━━━━━━━━━━━━━\n"
        "1️⃣ اول اسم پلن رو بفرست (مثلاً: ♾ نامحدود دو کاربره آلمان):",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=cancel_kb()
    )
    return PLAN_NEW_NAME


async def receive_plan_new_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    name = update.message.text.strip()
    if not name or len(name) > 60:
        await update.message.reply_text("❌ یه نام معتبر (حداکثر ۶۰ حرف) بفرست یا لغو کن.", reply_markup=cancel_kb())
        return PLAN_NEW_NAME
    context.user_data["plan_new_name"] = name
    await update.message.reply_text("2️⃣ حالا قیمت پلن رو به تومان (فقط عدد) بفرست:", reply_markup=cancel_kb())
    return PLAN_NEW_PRICE


async def receive_plan_new_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    text = update.message.text.strip().replace(",", "")
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ فقط عدد مثبت بفرست یا لغو کن.", reply_markup=cancel_kb())
        return PLAN_NEW_PRICE
    context.user_data["plan_new_price"] = int(text)
    await update.message.reply_text(
        "3️⃣ متن تاییدیه‌ی خرید رو بفرست (سوالی که موقع خرید از کاربر پرسیده میشه)\n"
        "یا فقط «-» بفرست تا متن پیش‌فرض ساخته بشه:",
        reply_markup=cancel_kb()
    )
    return PLAN_NEW_TEXT


async def receive_plan_new_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    name = context.user_data.get("plan_new_name")
    price = context.user_data.get("plan_new_price")
    if not name or not price:
        await update.message.reply_text("❌ اطلاعات ناقصه، دوباره از «ساخت پلن جدید» شروع کن.", reply_markup=admin_menu())
        return ConversationHandler.END
    raw = update.message.text.strip()
    ctext = None if raw == "-" else raw[:500]
    max_order = db_one("SELECT COALESCE(MAX(sort_order),0) m FROM plans")["m"] or 0
    pid = db_run(
        "INSERT INTO plans (name, price, confirm_text, delivery_mode, show_in, is_active, sort_order, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (name, price, ctext, "hybrid", "both", 1, max_order + 1, time.time())
    ).lastrowid
    context.user_data.pop("plan_new_name", None)
    context.user_data.pop("plan_new_price", None)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ افزودن کانفیگ به انبارش", callback_data=f"auto_add_pkg_{pid}", style="success")],
        [InlineKeyboardButton("🧩 مدیریت همین پلن", callback_data=f"padm_{pid}", style="primary")],
        [InlineKeyboardButton("🔙 پنل ادمین", callback_data="admin_back", style="primary")],
    ])
    await update.message.reply_text(
        f"✅ پلن «{name}» ساخته شد!\n"
        f"💰 قیمت: {fmt_money(price)} تومان\n"
        f"🚚 تحویل: آنی + دستی (پیش‌فرض)\n"
        f"👁 نمایش: هر دو بخش خرید\n\n"
        "می‌تونی همین الان کانفیگ آماده به انبارش اضافه کنی تا تحویل آنی بشه؛ "
        "اگه اضافه نکنی، سفارش‌ها به صورت دستی برای ارسال میان پیش ادمین.",
        reply_markup=kb
    )
    return ConversationHandler.END


# ==================== 🎟 کد تخفیف — سمت کاربر (وارد کردن کد موقع خرید) ====================
async def disc_plan_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    pid = int(context.match.group(1))
    plan = get_plan(pid)
    if not plan or not plan["is_active"]:
        await query.answer("❌ این پلن فعال نیست.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    context.user_data["disc_ask"] = {"kind": "plan", "pid": pid}
    await query.message.reply_text("🎟 کد تخفیفت رو بفرست:", reply_markup=cancel_kb())
    return DISC_ENTER_CODE


async def disc_volume_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not context.user_data.get("pending_volume"):
        await query.answer("❌ درخواست منقضی شده؛ دوباره از «خرید کانفیگ» شروع کن.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    context.user_data["disc_ask"] = {"kind": "volume"}
    await query.message.reply_text("🎟 کد تخفیفت رو بفرست:", reply_markup=cancel_kb())
    return DISC_ENTER_CODE


async def receive_discount_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ask = context.user_data.get("disc_ask")
    if not ask:
        await update.message.reply_text("❌ درخواست منقضی شده.", reply_markup=main_menu())
        return ConversationHandler.END

    code = update.message.text.strip().upper()
    d = get_discount_by_code(code)
    st = discount_status(d, uid)
    if st != "ok":
        reasons = {
            "notfound": "❌ این کد وجود نداره یا غیرفعاله.",
            "expired": "❌ این کد منقضی شده.",
            "maxed": "❌ ظرفیت استفاده از این کد پر شده.",
            "used": "❌ تو قبلاً از این کد استفاده کردی.",
        }
        await update.message.reply_text(
            reasons.get(st, "❌ کد نامعتبره.") + "\nیه کد دیگه بفرست یا لغو کن:",
            reply_markup=cancel_kb()
        )
        return DISC_ENTER_CODE

    context.user_data["pending_discount"] = {"kind": ask["kind"], "pid": ask.get("pid"), "code_id": d["id"], "uid": uid}
    context.user_data.pop("disc_ask", None)

    if ask["kind"] == "plan":
        plan = get_plan(ask["pid"])
        if not plan or not plan["is_active"]:
            await update.message.reply_text("❌ این پلن دیگه فعال نیست.", reply_markup=main_menu())
            return ConversationHandler.END
        base = int(plan["price"])
        final = apply_discount(base, d)
        text = (
            f"✅ کد تخفیف اعمال شد! ({discount_label(d)})\n\n"
            "🧾 *تایید خرید*\n━━━━━━━━━━━━━━\n"
            f"❓ {md_escape(plan_confirm_text(plan))}\n\n"
            f"📦 پلن: {md_escape(plan['name'])}\n"
            f"💰 قیمت: {fmt_money(base)} تومان\n"
            f"🎟 با تخفیف: *{fmt_money(final)} تومان*"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ بله، تایید می‌کنم", callback_data=f"plan_ok_{plan['id']}", style="success")],
            [InlineKeyboardButton("🗑 حذف کد تخفیف", callback_data="disc_clear", style="danger")],
            [InlineKeyboardButton("❌ انصراف", callback_data="auto_buy_menu", style="danger")],
        ])
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    else:
        if not context.user_data.get("pending_volume"):
            await update.message.reply_text("❌ درخواست خرید منقضی شده.", reply_markup=main_menu())
            return ConversationHandler.END
        t, kb = _volume_confirm_text_kb(context)
        await update.message.reply_text(
            f"✅ کد تخفیف اعمال شد! ({discount_label(d)})\n\n{t}",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb
        )
    return ConversationHandler.END


async def disc_clear_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف کد تخفیف از خرید جاری و نمایش دوباره‌ی صفحه‌ی تایید."""
    query = update.callback_query
    pd = context.user_data.pop("pending_discount", None)
    try:
        await query.answer("🗑 کد تخفیف حذف شد")
    except Exception:
        pass
    if pd and pd.get("kind") == "plan" and pd.get("pid"):
        await _show_plan_confirm(query, context, pd["pid"])
    elif pd and pd.get("kind") == "volume" and context.user_data.get("pending_volume"):
        t, kb = _volume_confirm_text_kb(context)
        await safe_edit(query, t, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    else:
        await safe_edit(query, "🚫 درخواست منقضی شده.", reply_markup=main_menu())


# ==================== 🎟 مدیریت کدهای تخفیف (ادمین) ====================
def _discounts_text_kb():
    rows = db_all("SELECT * FROM discount_codes ORDER BY id DESC LIMIT 25")
    lines = ["🎟 *کدهای تخفیف*", "━━━━━━━━━━━━━━"]
    kb = []
    if rows:
        for d in rows:
            status = "🟢" if d["is_active"] else "🔴"
            cap = f"{d['used_count']}/{d['max_uses'] if d['max_uses'] else '∞'}"
            if d["expires_at"]:
                exp = "منقضی‌شده ⏰" if time.time() > d["expires_at"] else datetime.fromtimestamp(d["expires_at"]).strftime("تا %m-%d")
            else:
                exp = "بدون انقضا"
            lines.append(f"{status} {md_escape(d['code'])} | {discount_label(d)} | استفاده: {cap} | {exp}")
            kb.append([InlineKeyboardButton(f"⚙️ {d['code']}", callback_data=f"dadm_{d['id']}", style="primary")])
    else:
        lines.append("هنوز هیچ کدی نساختی.")
    lines.append("\nℹ️ کاربر موقع خرید (هم پلن‌ها هم حجم دلخواه) دکمه‌ی «🎟 کد تخفیف دارم» رو می‌بینه.\n"
                 "هر کاربر از هر کد فقط *یک‌بار* می‌تونه استفاده کنه.")
    kb.append([InlineKeyboardButton("➕ ساخت کد جدید", callback_data="dadm_new", style="success")])
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back", style="primary")])
    return "\n".join(lines), InlineKeyboardMarkup(kb)


async def admin_discounts_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    await query.answer()
    t, kb = _discounts_text_kb()
    await safe_edit(query, t, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def dadm_view_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    await query.answer()
    did = int(context.match.group(1))
    d = get_discount(did)
    if not d:
        t, kb = _discounts_text_kb()
        await safe_edit(query, t, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        return
    total_saved = db_one("SELECT COALESCE(SUM(amount_saved),0) s FROM discount_uses WHERE code_id=?", (did,))["s"]
    exp = "بدون انقضا"
    if d["expires_at"]:
        exp = ("منقضی‌شده ⏰ " if time.time() > d["expires_at"] else "") + datetime.fromtimestamp(d["expires_at"]).strftime("%Y-%m-%d %H:%M")
    text = (
        "🎟 *جزئیات کد تخفیف*\n━━━━━━━━━━━━━━\n"
        f"🔤 کد: `{d['code']}`\n"
        f"💸 تخفیف: {discount_label(d)}\n"
        f"🔢 استفاده‌شده: {d['used_count']}{' از ' + str(d['max_uses']) if d['max_uses'] else ' (بدون سقف)'}\n"
        f"⏳ انقضا: {exp}\n"
        f"💰 مجموع تخفیف داده‌شده: {fmt_money(total_saved)} تومان\n"
        f"وضعیت: {'🟢 فعال' if d['is_active'] else '🔴 غیرفعال'}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔴 غیرفعال کردن" if d["is_active"] else "🟢 فعال کردن",
                              callback_data=f"dadm_toggle_{did}",
                              style="danger" if d["is_active"] else "success")],
        [InlineKeyboardButton("🗑 حذف کد", callback_data=f"dadm_del_{did}", style="danger")],
        [InlineKeyboardButton("🔙 لیست کدها", callback_data="admin_discounts", style="primary")],
    ])
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def dadm_toggle_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    did = int(context.match.group(1))
    d = get_discount(did)
    if not d:
        await query.answer("❌ پیدا نشد.", show_alert=True)
        return
    new_val = 0 if d["is_active"] else 1
    db_run("UPDATE discount_codes SET is_active=? WHERE id=?", (new_val, did))
    await query.answer("🟢 فعال شد" if new_val else "🔴 غیرفعال شد")
    await dadm_view_cb(update, context)


async def dadm_del_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    await query.answer()
    did = int(context.match.group(1))
    d = get_discount(did)
    if not d:
        t, kb = _discounts_text_kb()
        await safe_edit(query, t, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        return
    text = f"⚠️ *تایید حذف*\n\nمطمئنی می‌خوای کد «{md_escape(d['code'])}» رو برای همیشه حذف کنی؟"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ بله، حذف کن", callback_data=f"dadm_delok_{did}", style="danger"),
         InlineKeyboardButton("🚫 نه، لغو", callback_data=f"dadm_{did}", style="primary")],
    ])
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def dadm_delok_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    did = int(context.match.group(1))
    db_run("DELETE FROM discount_codes WHERE id=?", (did,))
    await query.answer("🗑 حذف شد")
    t, kb = _discounts_text_kb()
    await safe_edit(query, t, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


# ---- ویزارد ساخت کد تخفیف جدید ----
async def dadm_new_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    context.user_data["dnew"] = {}
    await safe_edit(
        query,
        "➕ *ساخت کد تخفیف جدید*\n━━━━━━━━━━━━━━\n"
        "1️⃣ خودِ کد رو بفرست (حروف انگلیسی/عدد، مثل: OFF20 یا EYD1404):",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=cancel_kb()
    )
    return DISC_NEW_CODE


async def receive_dnew_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    code = update.message.text.strip().upper()
    if not re.fullmatch(r"[A-Z0-9_-]{3,32}", code):
        await update.message.reply_text(
            "❌ کد باید ۳ تا ۳۲ کاراکتر و فقط حروف انگلیسی/عدد/خط تیره باشه. دوباره بفرست:",
            reply_markup=cancel_kb()
        )
        return DISC_NEW_CODE
    if get_discount_by_code(code):
        await update.message.reply_text("❌ این کد از قبل وجود داره. یه کد دیگه بفرست:", reply_markup=cancel_kb())
        return DISC_NEW_CODE
    context.user_data.setdefault("dnew", {})["code"] = code
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("٪ درصدی (مثلاً ۲۰٪)", callback_data="dnew_type_percent", style="success")],
        [InlineKeyboardButton("💵 مبلغ ثابت (تومان)", callback_data="dnew_type_amount", style="success")],
        [InlineKeyboardButton("🚫 لغو عملیات", callback_data="cancel_conv", style="danger")],
    ])
    await update.message.reply_text("2️⃣ نوع تخفیف رو انتخاب کن:", reply_markup=kb)
    return DISC_NEW_TYPE


async def dnew_type_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    dtype = context.match.group(1)
    context.user_data.setdefault("dnew", {})["dtype"] = dtype
    hint = "3️⃣ درصد تخفیف رو بفرست (عدد بین 1 تا 100):" if dtype == "percent" else "3️⃣ مبلغ تخفیف رو به تومان بفرست:"
    await query.message.reply_text(hint, reply_markup=cancel_kb())
    return DISC_NEW_VALUE


async def receive_dnew_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    text = update.message.text.strip().replace(",", "")
    dnew = context.user_data.get("dnew") or {}
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ فقط عدد مثبت بفرست یا لغو کن.", reply_markup=cancel_kb())
        return DISC_NEW_VALUE
    val = int(text)
    if dnew.get("dtype") == "percent" and val > 100:
        await update.message.reply_text("❌ درصد نمی‌تونه بیشتر از 100 باشه. دوباره بفرست:", reply_markup=cancel_kb())
        return DISC_NEW_VALUE
    dnew["value"] = val
    context.user_data["dnew"] = dnew
    await update.message.reply_text("4️⃣ سقف کل تعداد استفاده رو بفرست (0 یعنی بدون سقف):", reply_markup=cancel_kb())
    return DISC_NEW_MAX


async def receive_dnew_max(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ فقط عدد بفرست (0 یعنی بدون سقف) یا لغو کن.", reply_markup=cancel_kb())
        return DISC_NEW_MAX
    context.user_data.setdefault("dnew", {})["max_uses"] = int(text)
    await update.message.reply_text("5️⃣ چند روز اعتبار داشته باشه؟ (0 یعنی بدون انقضا):", reply_markup=cancel_kb())
    return DISC_NEW_DAYS


async def receive_dnew_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ فقط عدد بفرست (0 یعنی بدون انقضا) یا لغو کن.", reply_markup=cancel_kb())
        return DISC_NEW_DAYS
    days = int(text)
    dnew = context.user_data.pop("dnew", None) or {}
    if not dnew.get("code") or not dnew.get("value"):
        await update.message.reply_text("❌ اطلاعات ناقصه؛ دوباره از «ساخت کد جدید» شروع کن.", reply_markup=admin_menu())
        return ConversationHandler.END
    expires_at = (time.time() + days * 86400) if days else None
    try:
        db_run(
            "INSERT INTO discount_codes (code, dtype, value, max_uses, used_count, expires_at, is_active, created_by, created_at) "
            "VALUES (?,?,?,?,0,?,1,?,?)",
            (dnew["code"], dnew.get("dtype", "percent"), dnew["value"], dnew.get("max_uses", 0),
             expires_at, update.effective_user.id, time.time())
        )
    except sqlite3.IntegrityError:
        await update.message.reply_text("❌ این کد همین الان توسط ادمین دیگه‌ای ساخته شد!", reply_markup=admin_menu())
        return ConversationHandler.END
    # 🔙 برگشت مستقیم به لیست کدها (نه پرت شدن به پنل اصلی)
    t, kb = _discounts_text_kb()
    await update.message.reply_text(f"✅ کد «{dnew['code']}» ساخته شد!\n\n{t}",
                                    parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    return ConversationHandler.END


# ==================== تست رایگان (هر کاربر فقط یک‌بار؛ هر کانفیگ تا ۳ نفر) ====================
def test_available_count() -> int:
    """تعداد کانفیگ‌های تست فعالی که هنوز ظرفیت تحویل دارن (برای نمایش به ادمین)."""
    return db_one(
        "SELECT COUNT(*) c FROM test_configs WHERE status='active' AND delivered_count<?",
        (TEST_CONFIG_MAX_DELIVERIES,)
    )["c"]


def has_used_test(uid: int) -> bool:
    return db_one("SELECT 1 FROM test_deliveries WHERE user_id=?", (uid,)) is not None


async def free_test_entry_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if has_used_test(uid):
        await safe_edit(
            query,
            "🧪 *تست رایگان*\n━━━━━━━━━━━━━━\n"
            "❌ تو قبلاً یک‌بار از تست رایگان استفاده کردی.\n"
            "هر کاربر فقط یک‌بار می‌تونه کانفیگ تست بگیره.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💥 خرید کانفیگ", callback_data="buy_config", style="success")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main", style="primary")],
            ])
        )
        return

    left = test_available_count()
    text = (
        "🧪 *تست رایگان*\n━━━━━━━━━━━━━━\n"
        "یه کانفیگ تست، کاملاً رایگان و فقط یک‌بار بگیر و کیفیت سرویس رو امتحان کن!\n\n"
        f"📦 وضعیت موجودی: {'✅ موجود' if left > 0 else '❌ فعلاً ناموجود'}"
    )
    kb = [
        [InlineKeyboardButton("🎁 دریافت کانفیگ تست", callback_data="free_test_claim", style="success")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main", style="primary")],
    ]
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def free_test_claim_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id

    if has_used_test(uid):
        await query.answer("❌ قبلاً از تست رایگان استفاده کردی!", show_alert=True)
        return

    await query.answer("⏳ در حال بررسی...")

    # ممکنه چند کانفیگ تست هم‌زمان فعال باشن؛ همیشه از قدیمی‌ترینِ ناتموم استفاده می‌کنیم
    # تا نفر دوم و سوم هم دقیقاً همون کانفیگِ نفر اول رو بگیرن، نه یه کانفیگ جدا.
    claimed_row = None
    for _ in range(5):
        row = db_one(
            "SELECT id, source_chat_id, source_message_id FROM test_configs "
            "WHERE status='active' AND delivered_count<? ORDER BY id LIMIT 1",
            (TEST_CONFIG_MAX_DELIVERIES,)
        )
        if not row:
            break
        cfg_id = row["id"]
        # رزرو اتمیک یک "جایگاه" از همین کانفیگ (فقط اگه هنوز زیر سقف ۳ نفر بود)
        cur = db_run(
            "UPDATE test_configs SET delivered_count=delivered_count+1 "
            "WHERE id=? AND status='active' AND delivered_count<?",
            (cfg_id, TEST_CONFIG_MAX_DELIVERIES)
        )
        if cur.rowcount == 0:
            continue  # یکی دیگه هم‌زمان همین آخرین جا رو برد؛ برو سراغ کانفیگ بعدی
        claimed_row = row
        break

    if not claimed_row:
        await safe_edit(
            query,
            "😔 فعلاً کانفیگ تستی موجود نیست.\nبعداً دوباره امتحان کن یا از «خرید کانفیگ» استفاده کن.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💥 خرید کانفیگ", callback_data="buy_config", style="success")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main", style="primary")],
            ])
        )
        return

    cfg_id = claimed_row["id"]

    # ثبت تحویل برای این کاربر؛ UNIQUE(user_id) جلوی گرفتن تست دوم رو حتی موقع رقابت هم‌زمان می‌گیره
    try:
        db_run(
            "INSERT INTO test_deliveries (user_id, test_config_id, delivered_at) VALUES (?,?,?)",
            (uid, cfg_id, time.time())
        )
    except sqlite3.IntegrityError:
        # کاربر هم‌زمان از یه جای دیگه تست گرفته؛ جایگاهی که رزرو کردیم رو برگردون
        db_run("UPDATE test_configs SET delivered_count=delivered_count-1 WHERE id=?", (cfg_id,))
        await query.answer("❌ قبلاً از تست رایگان استفاده کردی!", show_alert=True)
        return

    try:
        await context.bot.copy_message(
            chat_id=uid,
            from_chat_id=claimed_row["source_chat_id"],
            message_id=claimed_row["source_message_id"],
        )
    except Exception as e:
        logger.error("test config delivery failed for %s: %s", uid, e)
        # 🛡 شانس یک‌باره‌ی کاربر نسوزه: رزرو و ثبتِ تحویل کامل برمی‌گرده تا بعداً دوباره امتحان کنه
        db_run("DELETE FROM test_deliveries WHERE user_id=?", (uid,))
        db_run("UPDATE test_configs SET delivered_count=MAX(delivered_count-1,0) WHERE id=?", (cfg_id,))
        await safe_edit(
            query,
            "❌ در ارسال کانفیگ تست مشکلی پیش اومد؛ نگران نباش، شانس تستت محفوظ موند.\n"
            "چند لحظه بعد دوباره امتحان کن.",
            reply_markup=main_menu()
        )
        return

    # فقط بعد از ارسالِ موفق: اگه به سقف نفرات رسید، کانفیگ کامل حذف میشه
    updated = db_one("SELECT delivered_count FROM test_configs WHERE id=?", (cfg_id,))
    if updated and updated["delivered_count"] >= TEST_CONFIG_MAX_DELIVERIES:
        db_run("DELETE FROM test_configs WHERE id=?", (cfg_id,))

    await safe_edit(
        query,
        "✅ *کانفیگ تست ارسال شد!*\n\nامیدواریم از کیفیت سرویس راضی باشی 🌟\n"
        "برای استفاده کامل می‌تونی از «خرید کانفیگ» یا «خرید اتوماتیک» استفاده کنی.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu()
    )

    if get_setting("purchase_notify") == "1":
        try:
            user = get_user(uid)
            await notify_admins(
                context,
                f"🧪 *تست رایگان تحویل داده شد*\n━━━━━━━━━━━━━━\n"
                f"👤 {md_escape(user['first_name'] or 'ناشناس') if user else uid} (`{uid}`)\n"
                f"🆔 کانفیگ تست #{cfg_id}"
            )
        except Exception:
            pass


# ---- مدیریت کانفیگ‌های تست رایگان (ادمین) ----
def _test_menu_text_kb():
    rows = db_all(
        "SELECT id, delivered_count FROM test_configs WHERE status='active' ORDER BY id"
    )
    total_used = db_one("SELECT COUNT(*) c FROM test_deliveries")["c"]
    lines = ["🧪 *مدیریت کانفیگ‌های تست رایگان*", "━━━━━━━━━━━━━━"]
    if rows:
        for r in rows:
            lines.append(f"📦 کانفیگ #{r['id']} | {r['delivered_count']}/{TEST_CONFIG_MAX_DELIVERIES} نفر گرفتن")
    else:
        lines.append("فعلاً هیچ کانفیگ تستی در صف نیست.")
    lines.append(f"\n👥 مجموع کاربرانی که تا الان تست گرفتن: {total_used}")
    lines.append(f"\nℹ️ هر کانفیگ تست بین اولین {TEST_CONFIG_MAX_DELIVERIES} نفر درخواست‌کننده مشترکه، "
                 f"بعد از رسیدن به {TEST_CONFIG_MAX_DELIVERIES} نفر خودکار حذف میشه و نوبت کانفیگ بعدی میشه.")
    kb = [
        [InlineKeyboardButton("➕ افزودن کانفیگ تست جدید", callback_data="admin_test_add_entry", style="success")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back", style="primary")],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(kb)


async def admin_test_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    await query.answer()
    text, kb = _test_menu_text_kb()
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def admin_test_add_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    context.user_data["conv_return"] = "admin_test_menu"
    await safe_edit(
        query,
        "🧪 کانفیگ تست رو بفرست (متن، عکس یا فایل).\n"
        f"این کانفیگ بین اولین {TEST_CONFIG_MAX_DELIVERIES} نفری که «تست رایگان» بزنن مشترک میشه.\n"
        "می‌تونی پشت‌سرهم چندتا کانفیگ تست جدا اضافه کنی:",
        reply_markup=cancel_kb()
    )
    return ADMIN_TEST_ADD_CFG


async def receive_test_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    db_run(
        "INSERT INTO test_configs (source_chat_id, source_message_id, delivered_count, status, added_by, added_at) "
        "VALUES (?,?,0,'active',?,?)",
        (update.effective_chat.id, update.message.message_id, update.effective_user.id, time.time())
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ افزودن کانفیگ تست بعدی", callback_data="test_add_more", style="success")],
        [InlineKeyboardButton("✅ پایان", callback_data="test_add_finish", style="success")],
    ])
    await update.message.reply_text("✅ کانفیگ تست اضافه شد.\n\nمی‌خوای یکی دیگه هم اضافه کنی؟", reply_markup=kb)
    return ADMIN_TEST_ADD_CFG


async def test_add_more_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("🧪 کانفیگ تست بعدی رو بفرست:", reply_markup=cancel_kb())
    return ADMIN_TEST_ADD_CFG


async def test_add_finish_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("تمام شد ✅")
    # 🔙 برگشت به همون منوی تست (نه پرت شدن به پنل اصلی)
    t, kb = _test_menu_text_kb()
    await query.message.reply_text(f"✅ افزودن کانفیگ‌های تست تموم شد.\n\n{t}",
                                   parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    return ConversationHandler.END


# ==================== پشتیبانی (کاربر به ادمین) ====================
async def support_entry_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    support_username = get_support_username()
    text = (
        "💬 *پشتیبانی*\n"
        "━━━━━━━━━━━━━━\n"
        f"می‌تونی مستقیم پیامت رو اینجا بفرستی تا به ادمین برسه،\n"
        f"یا مستقیم به آیدی پشتیبانی پیام بدی: @{support_username}\n\n"
        "برای ارسال از داخل بات، روی دکمه زیر بزن:"
    )
    kb = [
        [InlineKeyboardButton("✍️ ارسال پیام", callback_data="support_start", style="primary")],
        [InlineKeyboardButton(f"💬 پیام به @{support_username}", url=f"https://t.me/{support_username}", style="primary")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main", style="primary")]
    ]
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def support_start_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["conv_return"] = "support_entry"
    await safe_edit(query, "✍️ پیامت رو الان بفرست:", reply_markup=cancel_kb())
    return SUPPORT_MSG


async def receive_support_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = get_user(uid)
    msg_text = update.message.text

    msg_id = db_run(
        "INSERT INTO support_messages (user_id, message, is_from_admin, date, is_read) VALUES (?,?,0,?,0)",
        (uid, msg_text, time.time())
    ).lastrowid

    if get_setting("support_notify", "1") == "1":
        try:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 پاسخ دادن", callback_data=f"admin_reply_sel_{uid}_{msg_id}", style="primary")],
                [InlineKeyboardButton("👤 پروفایل", callback_data=f"act_manage_{uid}", style="primary")]
            ])
            await notify_admins(
                context,
                f"📩 *پیام جدید از پشتیبانی*\n"
                f"━━━━━━━━━━━━━━\n"
                f"👤 {md_escape(user['first_name'] or 'ناشناس')} (`{uid}`)\n"
                f"🔗 @{md_escape(user['username'] or '-')}\n\n"
                f"💬 {md_escape(msg_text)}",
                reply_markup=kb
            )
        except Exception:
            pass

    await update.message.reply_text(
        "✅ پیامت ارسال شد!\nبه محض اینکه ادمین جوابت رو بده، بهت اطلاع می‌دیم.",
        reply_markup=main_menu()
    )
    return ConversationHandler.END


# ==================== لغو گفتگو ====================
CONV_KEYS = (
    "charge_amount", "pending_volume", "pending_price", "target_uid", "coin_action",
    "send_msg_target", "reply_target_uid", "order_target_id", "order_target_uid",
    "auto_add_plan", "plan_edit_id", "plan_new_name", "plan_new_price",
    "disc_ask", "dnew", "conv_return",
)


def _clear_conv_keys(user_data):
    for k in CONV_KEYS:
        user_data.pop(k, None)


def _infer_cancel_return(ud) -> str:
    """🔙 لغو هوشمند: از روی وضعیت گفتگو حدس می‌زنیم کاربر/ادمین از کدوم صفحه اومده."""
    if ud.get("conv_return"):
        return ud["conv_return"]
    if ud.get("plan_edit_id"):
        return f"padm_{ud['plan_edit_id']}"
    if ud.get("auto_add_plan"):
        return "admin_auto_menu"
    if ud.get("order_target_id"):
        return "admin_pending_orders"
    if ud.get("target_uid"):
        return f"act_manage_{ud['target_uid']}"
    if ud.get("send_msg_target"):
        return f"act_manage_{ud['send_msg_target']}"
    if ud.get("reply_target_uid"):
        return "admin_support_inbox"
    if ud.get("dnew") is not None:
        return "admin_discounts"
    ask = ud.get("disc_ask")
    if ask:
        if ask.get("kind") == "plan" and ask.get("pid"):
            return f"plan_sel_{ask['pid']}"
        return "buy_config"
    if ud.get("charge_amount"):
        return "charge_wallet"
    return ""


async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ret = _infer_cancel_return(context.user_data)
    _clear_conv_keys(context.user_data)
    rows = []
    if ret:
        rows.append([InlineKeyboardButton("🔙 برگشت به بخش قبلی", callback_data=ret, style="success")])
    if is_admin(update.effective_user.id):
        rows.append([InlineKeyboardButton("🏠 پنل ادمین", callback_data="admin_back", style="primary")])
    else:
        rows.append([InlineKeyboardButton("🏠 منوی اصلی", callback_data="back_main", style="primary")])
    kb = InlineKeyboardMarkup(rows)
    query = update.callback_query
    if query:
        await query.answer()
        await safe_edit(query, "🚫 عملیات لغو شد.", reply_markup=kb)
    else:
        await update.message.reply_text("🚫 عملیات لغو شد.", reply_markup=kb)
    return ConversationHandler.END


async def conv_timeout(update, context: ContextTypes.DEFAULT_TYPE):
    """وقتی گفتگو به‌خاطر بی‌فعالیتی منقضی میشه (تا برای همیشه کاربر/ادمین گیر نکنه)."""
    try:
        if context.user_data is not None:
            _clear_conv_keys(context.user_data)
    except Exception:
        pass


# ==================== دکمه‌ی پشتیبان: هیچ دکمه‌ای بی‌پاسخ نمونه ====================
async def fallback_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اگه هیچ‌کدوم از هندلرهای بالا این کلیک رو مدیریت نکردن (مثلاً چون یه گفتگوی
    نیمه‌تموم دیگه باز مونده)، حداقل یه پاسخ روشن به کاربر/ادمین بدیم به‌جای سکوت کامل."""
    query = update.callback_query
    try:
        await query.answer(
            "⏳ یه عملیات نیمه‌تمام از قبل باز مونده. با /cancel لغوش کن یا تمومش کن، بعد دوباره امتحان کن.",
            show_alert=True,
        )
    except Exception:
        pass


# ==================== پنل ادمین ====================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ دسترسی غیرمجاز!")
        return
    await update.message.reply_text(admin_panel_text(), parse_mode=ParseMode.MARKDOWN, reply_markup=admin_menu())


async def admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit(query, admin_panel_text(), parse_mode=ParseMode.MARKDOWN, reply_markup=admin_menu())


# ---- مدیریت کاربران ----
async def admin_users_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    await query.answer()
    kb = [
        [InlineKeyboardButton("🔍 جستجوی کاربر با آیدی", callback_data="admin_search_entry", style="primary")],
        [InlineKeyboardButton("🕒 کاربران اخیر", callback_data="admin_recent_users", style="primary")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back", style="primary")],
    ]
    await safe_edit(query, "👤 مدیریت کاربران", reply_markup=InlineKeyboardMarkup(kb))


async def search_user_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    context.user_data["conv_return"] = "admin_users"
    await safe_edit(query, "🔎 آیدی عددی کاربر رو ارسال کن:", reply_markup=cancel_kb())
    return ASK_USER_ID


async def receive_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ فقط آیدی عددی بفرست یا لغو کن.", reply_markup=cancel_kb())
        return ASK_USER_ID
    uid = int(text)
    user = get_user(uid)
    if not user:
        await update.message.reply_text("❌ کاربری با این آیدی پیدا نشد.", reply_markup=admin_menu())
        return ConversationHandler.END
    await update.message.reply_text(profile_text(user), parse_mode=ParseMode.MARKDOWN, reply_markup=profile_kb(user))
    return ConversationHandler.END


async def recent_users_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    await query.answer()
    rows = db_all("SELECT * FROM users ORDER BY id DESC LIMIT 10")
    if not rows:
        await safe_edit(query, "کاربری ثبت نشده.", reply_markup=admin_menu())
        return
    text = "🕒 *۱۰ کاربر اخیر*\n━━━━━━━━━━━━━━\n"
    kb = []
    for r in rows:
        flag = "⛔" if r["is_banned"] else "✅"
        text += f"{flag} `{r['id']}` — {md_escape(r['first_name'] or '-')} — 💰{fmt_money(r['balance'])}\n"
        kb.append([InlineKeyboardButton(f"مدیریت {r['id']}", callback_data=f"act_manage_{r['id']}", style="primary")])
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin_users", style="primary")])
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def manage_user_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    await query.answer()
    uid = int(context.match.group(1))
    user = get_user(uid)
    if not user:
        await safe_edit(query, "❌ کاربر پیدا نشد.", reply_markup=admin_menu())
        return
    await safe_edit(query, profile_text(user), parse_mode=ParseMode.MARKDOWN, reply_markup=profile_kb(user))


async def ban_user_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    uid = int(context.match.group(1))
    db_run("UPDATE users SET is_banned=1 WHERE id=?", (uid,))
    await query.answer("کاربر مسدود شد ⛔")
    user = get_user(uid)
    await safe_edit(query, profile_text(user), parse_mode=ParseMode.MARKDOWN, reply_markup=profile_kb(user))


async def unban_user_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    uid = int(context.match.group(1))
    db_run("UPDATE users SET is_banned=0 WHERE id=?", (uid,))
    await query.answer("رفع مسدودیت شد ✅")
    user = get_user(uid)
    await safe_edit(query, profile_text(user), parse_mode=ParseMode.MARKDOWN, reply_markup=profile_kb(user))


async def coin_action_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    uid = int(context.match.group(1))
    action = "add" if query.data.startswith("act_addcoin_") else "sub"
    context.user_data["target_uid"] = uid
    context.user_data["coin_action"] = action
    verb = "افزایش" if action == "add" else "کاهش"
    await safe_edit(query, f"چند تومان {verb} پیدا کنه کاربر `{uid}`؟ (فقط عدد بفرست)",
                     parse_mode=ParseMode.MARKDOWN, reply_markup=cancel_kb())
    return ASK_AMOUNT


async def receive_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ فقط عدد مثبت بفرست یا لغو کن.", reply_markup=cancel_kb())
        return ASK_AMOUNT
    amount = int(text)
    uid = context.user_data.get("target_uid")
    action = context.user_data.get("coin_action")
    user = get_user(uid)
    if not user:
        await update.message.reply_text("❌ این کاربر دیگر پیدا نشد.", reply_markup=admin_menu())
        _clear_conv_keys(context.user_data)
        return ConversationHandler.END

    if action == "add":
        db_run("UPDATE users SET balance=balance+? WHERE id=?", (amount, uid))
        log_tx(uid, "admin_add", amount, "افزایش دستی توسط ادمین")
        msg = f"✅ {fmt_money(amount)} تومان به کاربر {uid} اضافه شد."
    else:
        # 🛡 موجودی هیچ‌وقت منفی نمیشه (حداکثر تا صفر کم میشه)
        db_run("UPDATE users SET balance=MAX(balance-?, 0) WHERE id=?", (amount, uid))
        log_tx(uid, "admin_sub", -amount, "کاهش دستی توسط ادمین")
        msg = f"✅ {fmt_money(amount)} تومان از کاربر {uid} کم شد (تا حداقل صفر)."
    # 🔙 برگشت به پروفایل همون کاربر (نه پرت شدن به پنل اصلی)
    user = get_user(uid)
    await update.message.reply_text(f"{msg}\n\n{profile_text(user)}",
                                    parse_mode=ParseMode.MARKDOWN, reply_markup=profile_kb(user))
    _clear_conv_keys(context.user_data)
    return ConversationHandler.END


# ---- ارسال پیام به کاربر (ادمین) ----
async def admin_send_msg_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    await safe_edit(query, "📨 آیدی عددی کاربر مورد نظر رو بفرست:", reply_markup=cancel_kb())
    return SEND_MSG_UID


async def admin_send_to_user_direct(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    uid = int(context.match.group(1))
    context.user_data["send_msg_target"] = uid
    await safe_edit(query, f"📨 پیامت رو برای کاربر `{uid}` بفرست:", parse_mode=ParseMode.MARKDOWN, reply_markup=cancel_kb())
    return SEND_MSG_TEXT


async def receive_send_msg_uid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ فقط آیدی عددی بفرست یا لغو کن.", reply_markup=cancel_kb())
        return SEND_MSG_UID
    uid = int(text)
    user = get_user(uid)
    if not user:
        await update.message.reply_text("❌ کاربری با این آیدی پیدا نشد.", reply_markup=admin_menu())
        _clear_conv_keys(context.user_data)
        return ConversationHandler.END
    context.user_data["send_msg_target"] = uid
    await update.message.reply_text(
        f"👤 کاربر: {md_escape(user['first_name'] or '-')} (`{uid}`)\n\n📨 پیامت رو بفرست:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=cancel_kb()
    )
    return SEND_MSG_TEXT


async def receive_send_msg_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data.get("send_msg_target")
    msg_text = update.message.text
    user = get_user(uid)
    if not user:
        await update.message.reply_text("❌ کاربر پیدا نشد.", reply_markup=admin_menu())
        _clear_conv_keys(context.user_data)
        return ConversationHandler.END

    try:
        await context.bot.send_message(
            uid,
            f"📨 *پیام از ادمین:*\n━━━━━━━━━━━━━━\n{md_escape(msg_text)}",
            parse_mode=ParseMode.MARKDOWN
        )
        # 🔙 برگشت به پروفایل همون کاربر (نه پرت شدن به پنل اصلی)
        await update.message.reply_text(f"✅ پیام به کاربر {uid} ارسال شد.\n\n{profile_text(user)}",
                                        parse_mode=ParseMode.MARKDOWN, reply_markup=profile_kb(user))
    except Exception as e:
        await update.message.reply_text(f"❌ ارسال ناموفق! (ممکنه کاربر بات رو بلاک کرده باشه)\nخطا: {e}", reply_markup=admin_menu())

    _clear_conv_keys(context.user_data)
    return ConversationHandler.END


# ---- صندوق پشتیبانی (ادمین) ----
def _support_inbox_text_kb():
    unread = db_one("SELECT COUNT(*) c FROM support_messages WHERE is_from_admin=0 AND is_read=0")["c"]
    rows = db_all("SELECT * FROM support_messages WHERE is_from_admin=0 ORDER BY id DESC LIMIT 15")

    if not rows:
        return "📭 صندوق پشتیبانی خالیه!", InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back", style="primary")]])

    text = f"💬 *صندوق پشتیبانی* ({unread} خوانده‌نشده)\n━━━━━━━━━━━━━━\n"
    kb = []
    for r in rows:
        user = get_user(r["user_id"])
        name = md_escape(user["first_name"] or "ناشناس") if user else "حذف‌شده"
        read_flag = "📋" if r["is_read"] else "🔵"
        short_msg = md_escape(r["message"][:30]) + ("..." if len(r["message"]) > 30 else "")
        text += f"{read_flag} #{r['id']} | {name} | {short_msg}\n"
        kb.append([InlineKeyboardButton(
            f"💬 پاسخ #{r['id']}", callback_data=f"admin_reply_sel_{r['user_id']}_{r['id']}", style="primary")])
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back", style="primary")])
    return text, InlineKeyboardMarkup(kb)


async def admin_support_inbox(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    await query.answer()
    text, kb = _support_inbox_text_kb()
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def admin_reply_sel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()  # قبلاً این خط جا افتاده بود؛ باعث می‌شد دکمه بدون پاسخ بمونه
    target_uid = int(context.match.group(1))
    msg_id = int(context.match.group(2))
    db_run("UPDATE support_messages SET is_read=1 WHERE id=?", (msg_id,))
    context.user_data["reply_target_uid"] = target_uid
    await query.message.reply_text(
        f"💬 جوابت رو برای کاربر `{target_uid}` بفرست:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=cancel_kb()
    )
    return ADMIN_REPLY_MSG


async def receive_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    uid = context.user_data.get("reply_target_uid")
    msg_text = update.message.text
    if not uid:
        await update.message.reply_text("❌ کاربر مقصد پیدا نشد.", reply_markup=admin_menu())
        return ConversationHandler.END

    db_run(
        "INSERT INTO support_messages (user_id, message, is_from_admin, date, is_read) VALUES (?,?,1,?,1)",
        (uid, msg_text, time.time())
    )

    try:
        await context.bot.send_message(
            uid,
            f"💬 *پاسخ پشتیبانی:*\n━━━━━━━━━━━━━━\n{md_escape(msg_text)}",
            parse_mode=ParseMode.MARKDOWN
        )
        # 🔙 برگشت به صندوق پشتیبانی (نه پرت شدن به پنل اصلی)
        t, kb = _support_inbox_text_kb()
        await update.message.reply_text(f"✅ پاسخ ارسال شد.\n\n{t}",
                                        parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    except Exception as e:
        await update.message.reply_text(f"❌ ارسال ناموفق: {e}", reply_markup=admin_menu())

    _clear_conv_keys(context.user_data)
    return ConversationHandler.END


# ---- ارسال همگانی ----
async def broadcast_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    await safe_edit(query, "📢 متن پیام همگانی رو بفرست:", reply_markup=cancel_kb())
    return BC_TEXT


async def receive_bc_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["bc_text"] = update.message.text
    kb = [
        [InlineKeyboardButton("✅ ارسال به همه", callback_data="bc_yes", style="success")],
        [InlineKeyboardButton("🚫 لغو", callback_data="cancel_conv", style="danger")],
    ]
    await update.message.reply_text(
        f"پیش‌نمایش پیام:\n\n{update.message.text}\n\nارسال بشه؟",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return BC_CONFIRM


async def send_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    text = context.user_data.get("bc_text", "")
    rows = db_all("SELECT id FROM users WHERE is_banned=0")
    sent, failed = 0, 0
    for r in rows:
        try:
            await context.bot.send_message(r["id"], f"📢 {text}")
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(BROADCAST_DELAY)
    await query.message.reply_text(f"✅ ارسال شد به {sent} کاربر. (ناموفق: {failed})", reply_markup=admin_menu())
    context.user_data.pop("bc_text", None)
    return ConversationHandler.END


# ---- آمار ----
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    await query.answer()
    total_users = db_one("SELECT COUNT(*) c FROM users")["c"]
    banned = db_one("SELECT COUNT(*) c FROM users WHERE is_banned=1")["c"]
    total_spent = db_one("SELECT COALESCE(SUM(total_spent),0) s FROM users")["s"]
    pending_dep = db_one("SELECT COUNT(*) c FROM deposits WHERE status='pending'")["c"]
    pending_orders = db_one("SELECT COUNT(*) c FROM config_orders WHERE status='pending'")["c"]
    delivered_orders = db_one("SELECT COUNT(*) c FROM config_orders WHERE status='delivered'")["c"]
    auto_available = db_one("SELECT COUNT(*) c FROM auto_configs WHERE status='available'")["c"]
    auto_delivered = db_one("SELECT COUNT(*) c FROM auto_configs WHERE status='delivered'")["c"]
    test_active = db_one("SELECT COUNT(*) c FROM test_configs WHERE status='active'")["c"]
    test_delivered = db_one("SELECT COUNT(*) c FROM test_deliveries")["c"]
    admins_count = len(admin_ids())
    text = (
        f"📊 *آمار کلی*\n━━━━━━━━━━━━━━\n"
        f"👥 کل کاربران: {total_users}\n"
        f"⛔ مسدود شده: {banned}\n"
        f"💵 مجموع خرید کاربران: {fmt_money(total_spent)} تومان\n"
        f"📦 کانفیگ‌های ارسال‌شده: {delivered_orders}\n"
        f"📥 سفارش در انتظار ارسال: {pending_orders}\n"
        f"💳 درخواست شارژ در انتظار: {pending_dep}\n"
        f"⚡️ کانفیگ اتوماتیک موجود: {auto_available} | تحویل‌شده: {auto_delivered}\n"
        f"🧪 کانفیگ تست فعال: {test_active} | تحویل‌شده به کاربران: {test_delivered}\n"
        f"🛡 تعداد ادمین‌ها: {admins_count}"
    )
    kb = [[InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back", style="primary")]]
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


# ---- بکاپ ----
async def admin_backup_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    await query.answer()
    try:
        import tempfile, shutil
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name
        shutil.copy2(DB_PATH, tmp_path)
        with open(tmp_path, "rb") as f:
            await context.bot.send_document(
                update.effective_user.id, document=f,
                caption=f"💾 بکاپ دیتابیس — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                reply_markup=admin_menu()
            )
        os.remove(tmp_path)
    except Exception as e:
        await safe_edit(query, f"❌ خطا در بکاپ: {e}", reply_markup=admin_menu())


# ==================== مدیریت ادمین‌های ربات (فقط مالک) ====================
def _manage_admins_text_kb():
    added = db_all("SELECT * FROM bot_admins ORDER BY added_at DESC")
    lines = ["🛡 *مدیریت ادمین‌های ربات*", "━━━━━━━━━━━━━━", "👑 *مالکان اصلی (ثابت در کد):*"]
    for oid in OWNER_IDS:
        lines.append(f"  • `{oid}`")
    lines.append("")
    lines.append(f"🛡 *ادمین‌های اضافه‌شده:* ({len(added)})")
    if not added:
        lines.append("  فعلاً کسی اضافه نشده.")
    kb = []
    for a in added:
        lines.append(f"  • `{a['id']}`")
        kb.append([InlineKeyboardButton(f"➖ حذف {a['id']}", callback_data=f"admin_rm_{a['id']}", style="danger")])
    kb.append([InlineKeyboardButton("➕ افزودن ادمین جدید", callback_data="admin_add_entry", style="success")])
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back", style="primary")])
    return "\n".join(lines), InlineKeyboardMarkup(kb)


async def admin_manage_admins_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_owner(update):
        return
    query = update.callback_query
    await query.answer()
    text, kb = _manage_admins_text_kb()
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def admin_add_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_owner(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    context.user_data["conv_return"] = "admin_manage_admins"
    await safe_edit(query, "🛡 آیدی عددی کاربری که می‌خوای ادمین بشه رو بفرست:\n(کاربر باید قبلاً /start رو زده باشه)",
                     reply_markup=cancel_kb())
    return ADD_ADMIN_ID


async def receive_add_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        return ConversationHandler.END
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ فقط آیدی عددی بفرست یا لغو کن.", reply_markup=cancel_kb())
        return ADD_ADMIN_ID
    new_id = int(text)
    if new_id in OWNER_IDS:
        await update.message.reply_text("ℹ️ این کاربر از قبل مالک رباته.", reply_markup=admin_menu())
        return ConversationHandler.END
    if db_one("SELECT id FROM bot_admins WHERE id=?", (new_id,)):
        await update.message.reply_text("ℹ️ این کاربر از قبل ادمینه.", reply_markup=admin_menu())
        return ConversationHandler.END

    db_run("INSERT INTO bot_admins (id, added_by, added_at) VALUES (?,?,?)",
           (new_id, update.effective_user.id, time.time()))
    # 🔙 برگشت به صفحه‌ی مدیریت ادمین‌ها (نه پرت شدن به پنل اصلی)
    t, kb = _manage_admins_text_kb()
    await update.message.reply_text(f"✅ کاربر `{new_id}` به عنوان ادمین اضافه شد.\n\n{t}",
                                     parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    try:
        await context.bot.send_message(
            new_id,
            "🎉 تبریک! شما به عنوان ادمین این ربات اضافه شدید.\nبرای ورود به پنل مدیریت، دستور /admin رو بزن."
        )
    except Exception:
        pass
    return ConversationHandler.END


async def admin_rm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_owner(update):
        return
    query = update.callback_query
    rm_id = int(context.match.group(1))
    db_run("DELETE FROM bot_admins WHERE id=?", (rm_id,))
    await query.answer("✅ ادمین حذف شد")
    try:
        await context.bot.send_message(rm_id, "ℹ️ دسترسی ادمین شما به این ربات لغو شد.")
    except Exception:
        pass
    await admin_manage_admins_cb(update, context)


# ==================== پاک‌سازی داده‌ها (فقط مالک) ====================
WIPE_LABELS = {
    "tx": "تاریخچه تراکنش‌ها",
    "orders": "سفارش‌ها و درخواست‌های شارژ",
    "support": "پیام‌های پشتیبانی",
    "auto": "کانفیگ‌های اتوماتیک باقی‌مانده",
    "test": "کانفیگ‌های تست رایگان باقی‌مانده و تاریخچه‌ی تحویل‌ها",
    "users": "همه کاربران، کیف پول‌ها و کل تاریخچه‌شون",
    "full": "کل دیتابیس (کاربران، تراکنش‌ها، سفارش‌ها، پیام‌ها، کانفیگ‌های اتوماتیک و تست)",
}


async def admin_wipe_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_owner(update):
        return
    query = update.callback_query
    await query.answer()
    text = (
        "🗑 *پاک‌سازی داده‌ها*\n━━━━━━━━━━━━━━\n"
        "⚠️ همه‌ی این عملیات‌ها *غیرقابل بازگشت* هستن. با دقت انتخاب کن."
    )
    kb = [
        [InlineKeyboardButton("🧾 پاک کردن تاریخچه تراکنش‌ها", callback_data="wipe_ask_tx", style="danger")],
        [InlineKeyboardButton("📦 پاک کردن سفارش‌ها و شارژها", callback_data="wipe_ask_orders", style="danger")],
        [InlineKeyboardButton("💬 پاک کردن پیام‌های پشتیبانی", callback_data="wipe_ask_support", style="danger")],
        [InlineKeyboardButton("⚡️ پاک کردن کانفیگ‌های اتوماتیک", callback_data="wipe_ask_auto", style="danger")],
        [InlineKeyboardButton("🧪 پاک کردن کانفیگ‌های تست رایگان", callback_data="wipe_ask_test", style="danger")],
        [InlineKeyboardButton("👥 پاک کردن همه کاربران", callback_data="wipe_ask_users", style="danger")],
        [InlineKeyboardButton("💣 ریست کامل دیتابیس", callback_data="wipe_ask_full", style="danger")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back", style="primary")],
    ]
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def wipe_ask_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_owner(update):
        return
    query = update.callback_query
    await query.answer()
    key = query.data.split("_", 2)[2]
    label = WIPE_LABELS.get(key, key)
    text = f"⚠️ *تایید نهایی*\n\nمطمئنی می‌خوای «{label}» رو کامل و برای همیشه پاک کنی؟\nاین کار قابل بازگشت نیست!"
    kb = [
        [InlineKeyboardButton("✅ بله، پاک کن", callback_data=f"wipe_do_{key}", style="success"),
         InlineKeyboardButton("🚫 نه، لغو", callback_data="admin_wipe_menu", style="danger")],
    ]
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def wipe_do_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_owner(update):
        return
    query = update.callback_query
    key = query.data.split("_", 2)[2]
    try:
        if key == "tx":
            db_run("DELETE FROM transactions")
        elif key == "orders":
            db_run("DELETE FROM config_orders")
            db_run("DELETE FROM deposits")
        elif key == "support":
            db_run("DELETE FROM support_messages")
        elif key == "auto":
            db_run("DELETE FROM auto_configs")
        elif key == "test":
            db_run("DELETE FROM test_configs")
            db_run("DELETE FROM test_deliveries")
        elif key == "users":
            for t in ("users", "transactions", "deposits", "config_orders", "support_messages"):
                db_run(f"DELETE FROM {t}")
        elif key == "full":
            for t in ("users", "transactions", "deposits", "config_orders", "support_messages",
                      "auto_configs", "test_configs", "test_deliveries"):
                db_run(f"DELETE FROM {t}")
        else:
            await query.answer("❌ نامعتبر", show_alert=True)
            return
        try:
            db_run("VACUUM")
        except Exception:
            pass
        await query.answer("✅ انجام شد")
        await safe_edit(query, f"✅ «{WIPE_LABELS.get(key, key)}» با موفقیت پاک شد.", reply_markup=admin_menu())
    except Exception as e:
        logger.error("wipe error: %s", e)
        await query.answer("❌ خطا در پاک‌سازی", show_alert=True)


# ---- تنظیمات بات ----
def _settings_text_kb():
    maintenance = get_setting("maintenance_mode", "0")
    welcome_msg = get_setting("welcome_msg", "")
    card = get_setting("card_number")
    holder = get_setting("card_holder")
    support_username = get_support_username()
    signup_bonus = get_signup_bonus()
    referral_bonus = get_referral_bonus()

    m_text = "🔴 فعال" if maintenance == "1" else "🟢 غیرفعال"

    text = (
        f"⚙️ *تنظیمات بات*\n"
        f"━━━━━━━━━━━━━━\n"
        f"🔧 حالت تعمیر: {m_text}\n"
        f"💳 شماره کارت: `{card}`\n"
        f"👤 به نام: {md_escape(holder)}\n"
        f"☎️ آیدی پشتیبانی: @{support_username}\n"
        f"🎁 هدیه عضویت: {fmt_money(signup_bonus)} تومان\n"
        f"🤝 هدیه دعوت دوست: {fmt_money(referral_bonus)} تومان\n"
        f"📝 پیام خوش‌آمدگویی: {md_escape(welcome_msg[:50])}{'...' if len(welcome_msg) > 50 else ''}"
    )
    kb = [
        [InlineKeyboardButton(f"🔧 تعمیر: {'خاموش کردن' if maintenance == '1' else 'روشن کردن'}",
                               callback_data="toggle_maintenance", style="danger")],
        [InlineKeyboardButton("🔔 تنظیمات اطلاع‌رسانی", callback_data="admin_notify_settings", style="primary")],
        [InlineKeyboardButton("💳 تغییر شماره کارت", callback_data="set_card_number_entry", style="primary")],
        [InlineKeyboardButton("👤 تغییر نام صاحب کارت", callback_data="set_card_holder_entry", style="primary")],
        [InlineKeyboardButton("☎️ تغییر آیدی پشتیبانی", callback_data="set_support_username_entry", style="primary")],
        [InlineKeyboardButton("📝 تغییر پیام خوش‌آمدگویی", callback_data="set_welcome_entry", style="primary")],
        [InlineKeyboardButton("🎁 تغییر هدیه عضویت", callback_data="set_signup_bonus_entry", style="primary")],
        [InlineKeyboardButton("🤝 تغییر هدیه دعوت", callback_data="set_referral_bonus_entry", style="primary")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back", style="primary")],
    ]
    return text, InlineKeyboardMarkup(kb)


async def admin_settings_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    await query.answer()
    text, kb = _settings_text_kb()
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def _settings_reply(update, done_msg: str):
    """پیام موفقیت + برگشت مستقیم به صفحه‌ی تنظیمات (نه پرت شدن به پنل اصلی)."""
    t, kb = _settings_text_kb()
    await update.message.reply_text(f"{done_msg}\n\n{t}", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def admin_notify_settings_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    await query.answer()

    def flag(key):
        return "✅ فعال" if get_setting(key, "1") == "1" else "❌ غیرفعال"

    text = (
        "🔔 *تنظیمات اطلاع‌رسانی*\n━━━━━━━━━━━━━━\n"
        f"🆕 اطلاع کاربر جدید: {flag('join_notify')}\n"
        f"🛒 اطلاع خرید کانفیگ: {flag('purchase_notify')}\n"
        f"💳 اطلاع درخواست شارژ: {flag('deposit_notify')}\n"
        f"💬 اطلاع پیام پشتیبانی: {flag('support_notify')}"
    )
    kb = [
        [InlineKeyboardButton("🆕 تغییر وضعیت اطلاع کاربر جدید", callback_data="toggle_join_notify", style="primary")],
        [InlineKeyboardButton("🛒 تغییر وضعیت اطلاع خرید", callback_data="toggle_purchase_notify", style="success")],
        [InlineKeyboardButton("💳 تغییر وضعیت اطلاع شارژ", callback_data="toggle_deposit_notify", style="primary")],
        [InlineKeyboardButton("💬 تغییر وضعیت اطلاع پشتیبانی", callback_data="toggle_support_notify", style="primary")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_settings", style="primary")],
    ]
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


def _make_toggle_handler(setting_key: str, label: str):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guard_admin(update):
            return
        current = get_setting(setting_key, "1")
        new_val = "0" if current == "1" else "1"
        set_setting(setting_key, new_val)
        status = "فعال ✅" if new_val == "1" else "غیرفعال ❌"
        await update.callback_query.answer(f"{label}: {status}")
        await admin_notify_settings_cb(update, context)
    return handler


toggle_join_notify_cb = _make_toggle_handler("join_notify", "اطلاع کاربر جدید")
toggle_purchase_notify_cb = _make_toggle_handler("purchase_notify", "اطلاع خرید")
toggle_deposit_notify_cb = _make_toggle_handler("deposit_notify", "اطلاع شارژ")
toggle_support_notify_cb = _make_toggle_handler("support_notify", "اطلاع پشتیبانی")


async def toggle_maintenance_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    current = get_setting("maintenance_mode", "0")
    new_val = "0" if current == "1" else "1"
    set_setting("maintenance_mode", new_val)
    status = "غیرفعال 🟢" if new_val == "0" else "فعال 🔴"
    await update.callback_query.answer(f"حالت تعمیر: {status}")
    await admin_settings_cb(update, context)


async def set_welcome_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    await safe_edit(query, "📝 پیام خوش‌آمدگویی جدید رو بفرست:", reply_markup=cancel_kb())
    return SET_WELCOME


async def receive_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    set_setting("welcome_msg", update.message.text)
    await _settings_reply(update, "✅ پیام خوش‌آمدگویی تغییر کرد!")
    return ConversationHandler.END


async def set_card_number_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    await safe_edit(query, "💳 شماره کارت جدید رو بفرست:", reply_markup=cancel_kb())
    return SET_CARD_NUMBER


async def receive_card_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    set_setting("card_number", update.message.text.strip())
    await _settings_reply(update, "✅ شماره کارت بروزرسانی شد.")
    return ConversationHandler.END


async def set_card_holder_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    await safe_edit(query, "👤 نام صاحب کارت جدید رو بفرست:", reply_markup=cancel_kb())
    return SET_CARD_HOLDER


async def receive_card_holder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    set_setting("card_holder", update.message.text.strip())
    await _settings_reply(update, "✅ نام صاحب کارت بروزرسانی شد.")
    return ConversationHandler.END


async def set_support_username_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    await safe_edit(query, "☎️ آیدی پشتیبانی جدید رو بفرست (بدون @):", reply_markup=cancel_kb())
    return SET_SUPPORT_USERNAME


async def receive_support_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    val = update.message.text.strip().lstrip("@")
    if not val:
        await update.message.reply_text("❌ مقدار نامعتبره، دوباره بفرست یا لغو کن.", reply_markup=cancel_kb())
        return SET_SUPPORT_USERNAME
    set_setting("support_username", val)
    await _settings_reply(update, "✅ آیدی پشتیبانی بروزرسانی شد.")
    return ConversationHandler.END


async def set_signup_bonus_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    await safe_edit(query, f"🎁 هدیه فعلی عضویت: {fmt_money(get_signup_bonus())} تومان\n\nمقدار جدید رو به تومان بفرست (۰ یعنی غیرفعال):",
                     reply_markup=cancel_kb())
    return SET_SIGNUP_BONUS


async def receive_signup_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    text = update.message.text.strip().replace(",", "")
    if not text.isdigit():
        await update.message.reply_text("❌ فقط عدد بفرست یا لغو کن.", reply_markup=cancel_kb())
        return SET_SIGNUP_BONUS
    set_setting("signup_bonus", text)
    await _settings_reply(update, f"✅ هدیه عضویت روی {fmt_money(int(text))} تومان تنظیم شد.")
    return ConversationHandler.END


async def set_referral_bonus_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    await safe_edit(query, f"🤝 هدیه فعلی دعوت: {fmt_money(get_referral_bonus())} تومان\n\nمقدار جدید رو به تومان بفرست (۰ یعنی غیرفعال):",
                     reply_markup=cancel_kb())
    return SET_REFERRAL_BONUS


async def receive_referral_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    text = update.message.text.strip().replace(",", "")
    if not text.isdigit():
        await update.message.reply_text("❌ فقط عدد بفرست یا لغو کن.", reply_markup=cancel_kb())
        return SET_REFERRAL_BONUS
    set_setting("referral_bonus", text)
    await _settings_reply(update, f"✅ هدیه دعوت روی {fmt_money(int(text))} تومان تنظیم شد.")
    return ConversationHandler.END


# ==================== خطاها ====================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling update: %s", context.error, exc_info=context.error)
    try:
        await notify_owners(context, f"⚠️ خطای بات:\n`{str(context.error)[:500]}`")
    except Exception:
        pass


# ==================== اجرا ====================
def main():
    app = Application.builder().token(TOKEN).build()

    common_fallbacks = [
        CommandHandler("cancel", cancel_conv),
        CallbackQueryHandler(cancel_conv, pattern=r"^cancel_conv$"),
    ]

    # نکته‌ی مهم برای رفع باگ «دکمه بی‌پاسخ»: هرجا ممکنه ادمین وسط یه گفتگو باشه و
    # روی یه دکمه‌ی مشابه (برای یه هدف دیگه، مثلاً سفارش دیگه) بزنه، خود entry handler
    # رو هم داخل state لیست می‌کنیم تا re-entry جواب بده، نه اینکه بی‌صدا نادیده گرفته بشه.

    coin_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(coin_action_entry, pattern=r"^act_addcoin_(\d+)$"),
            CallbackQueryHandler(coin_action_entry, pattern=r"^act_subcoin_(\d+)$"),
        ],
        states={ASK_AMOUNT: [
            CallbackQueryHandler(coin_action_entry, pattern=r"^act_addcoin_(\d+)$"),
            CallbackQueryHandler(coin_action_entry, pattern=r"^act_subcoin_(\d+)$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_amount),
        ]},
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    search_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(search_user_entry, pattern=r"^admin_search_entry$")],
        states={ASK_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_user_id)]},
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    send_msg_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_send_msg_entry, pattern=r"^admin_send_msg_entry$"),
            CallbackQueryHandler(admin_send_to_user_direct, pattern=r"^admin_send_to_(\d+)$"),
        ],
        states={
            SEND_MSG_UID: [
                CallbackQueryHandler(admin_send_msg_entry, pattern=r"^admin_send_msg_entry$"),
                CallbackQueryHandler(admin_send_to_user_direct, pattern=r"^admin_send_to_(\d+)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_send_msg_uid),
            ],
            SEND_MSG_TEXT: [
                CallbackQueryHandler(admin_send_msg_entry, pattern=r"^admin_send_msg_entry$"),
                CallbackQueryHandler(admin_send_to_user_direct, pattern=r"^admin_send_to_(\d+)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_send_msg_text),
            ],
        },
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    support_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(support_start_cb, pattern=r"^support_start$")],
        states={SUPPORT_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_support_msg)]},
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    admin_reply_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_reply_sel_cb, pattern=r"^admin_reply_sel_(\d+)_(\d+)$")],
        states={ADMIN_REPLY_MSG: [
            CallbackQueryHandler(admin_reply_sel_cb, pattern=r"^admin_reply_sel_(\d+)_(\d+)$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_admin_reply),
        ]},
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(broadcast_entry, pattern=r"^admin_broadcast_entry$")],
        states={
            BC_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_bc_text)],
            BC_CONFIRM: [CallbackQueryHandler(send_broadcast, pattern=r"^bc_yes$")],
        },
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    charge_custom_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(charge_custom_entry, pattern=r"^charge_custom$")],
        states={CHARGE_CUSTOM_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_charge_custom_amount)]},
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    charge_receipt_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(charge_send_receipt_entry, pattern=r"^charge_send_receipt$")],
        states={
            CHARGE_RECEIPT: [MessageHandler(
                (filters.PHOTO | filters.ANIMATION | filters.TEXT) & ~filters.COMMAND,
                receive_charge_receipt
            )],
        },
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    buy_config_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(buy_config_entry, pattern=r"^buy_custom_volume$")],
        states={ASK_VOLUME: [
            CallbackQueryHandler(buy_config_entry, pattern=r"^buy_custom_volume$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_volume),
        ]},
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    admin_sendcfg_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_sendcfg_entry, pattern=r"^sendcfg_order_(\d+)$")],
        states={
            ADMIN_SEND_CFG: [
                # کلیک روی «ارسال کانفیگ» برای یه سفارش دیگه، وسط یه ارسال ناتموم:
                # به‌جای بی‌پاسخ موندن، هدف رو عوض می‌کنه (رفع اصلی باگ گزارش‌شده)
                CallbackQueryHandler(admin_sendcfg_entry, pattern=r"^sendcfg_order_(\d+)$"),
                MessageHandler(
                    (filters.TEXT | filters.PHOTO | filters.ANIMATION | filters.Document.ALL) & ~filters.COMMAND,
                    receive_admin_send_cfg
                ),
            ],
        },
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    set_price_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_set_price_entry, pattern=r"^admin_set_price$")],
        states={SET_PRICE_PER_GB: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_price_per_gb)]},
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    admin_auto_add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_auto_add_entry, pattern=r"^auto_add_pkg_(\d+)$")],
        states={
            ADMIN_AUTO_ADD_CFG: [
                # اگه ادمین وسط افزودن کانفیگ برای یه پلن دیگه بزنه، هدف عوض میشه نه اینکه بی‌پاسخ بمونه
                CallbackQueryHandler(admin_auto_add_entry, pattern=r"^auto_add_pkg_(\d+)$"),
                CallbackQueryHandler(auto_add_more_cb, pattern=r"^auto_add_more$"),
                CallbackQueryHandler(auto_add_finish_cb, pattern=r"^auto_add_finish$"),
                MessageHandler(
                    (filters.TEXT | filters.PHOTO | filters.ANIMATION | filters.Document.ALL) & ~filters.COMMAND,
                    receive_auto_config
                ),
            ],
        },
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    admin_test_add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_test_add_entry, pattern=r"^admin_test_add_entry$")],
        states={
            ADMIN_TEST_ADD_CFG: [
                CallbackQueryHandler(admin_test_add_entry, pattern=r"^admin_test_add_entry$"),
                CallbackQueryHandler(test_add_more_cb, pattern=r"^test_add_more$"),
                CallbackQueryHandler(test_add_finish_cb, pattern=r"^test_add_finish$"),
                MessageHandler(
                    (filters.TEXT | filters.PHOTO | filters.ANIMATION | filters.Document.ALL) & ~filters.COMMAND,
                    receive_test_config
                ),
            ],
        },
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    set_welcome_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(set_welcome_entry, pattern=r"^set_welcome_entry$")],
        states={SET_WELCOME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_welcome)]},
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    set_card_number_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(set_card_number_entry, pattern=r"^set_card_number_entry$")],
        states={SET_CARD_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_card_number)]},
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    set_card_holder_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(set_card_holder_entry, pattern=r"^set_card_holder_entry$")],
        states={SET_CARD_HOLDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_card_holder)]},
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    set_support_username_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(set_support_username_entry, pattern=r"^set_support_username_entry$")],
        states={SET_SUPPORT_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_support_username)]},
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    set_signup_bonus_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(set_signup_bonus_entry, pattern=r"^set_signup_bonus_entry$")],
        states={SET_SIGNUP_BONUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_signup_bonus)]},
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    set_referral_bonus_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(set_referral_bonus_entry, pattern=r"^set_referral_bonus_entry$")],
        states={SET_REFERRAL_BONUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_referral_bonus)]},
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    admin_add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_entry, pattern=r"^admin_add_entry$")],
        states={ADD_ADMIN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_add_admin_id)]},
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    # 🧩 گفتگوهای مدیریت پلن‌ها
    plan_edit_price_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(padm_price_entry, pattern=r"^padm_price_(\d+)$")],
        states={PLAN_EDIT_PRICE: [
            CallbackQueryHandler(padm_price_entry, pattern=r"^padm_price_(\d+)$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_plan_price),
        ]},
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    plan_edit_name_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(padm_name_entry, pattern=r"^padm_name_(\d+)$")],
        states={PLAN_EDIT_NAME: [
            CallbackQueryHandler(padm_name_entry, pattern=r"^padm_name_(\d+)$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_plan_name),
        ]},
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    plan_edit_text_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(padm_text_entry, pattern=r"^padm_text_(\d+)$")],
        states={PLAN_EDIT_TEXT: [
            CallbackQueryHandler(padm_text_entry, pattern=r"^padm_text_(\d+)$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_plan_text),
        ]},
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    plan_new_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(padm_new_entry, pattern=r"^padm_new$")],
        states={
            PLAN_NEW_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_plan_new_name)],
            PLAN_NEW_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_plan_new_price)],
            PLAN_NEW_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_plan_new_text)],
        },
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    # 🎟 وارد کردن کد تخفیف موقع خرید (کاربر)
    disc_apply_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(disc_plan_entry, pattern=r"^disc_plan_(\d+)$"),
            CallbackQueryHandler(disc_volume_entry, pattern=r"^disc_volume$"),
        ],
        states={DISC_ENTER_CODE: [
            CallbackQueryHandler(disc_plan_entry, pattern=r"^disc_plan_(\d+)$"),
            CallbackQueryHandler(disc_volume_entry, pattern=r"^disc_volume$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_discount_code),
        ]},
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    # 🎟 ویزارد ساخت کد تخفیف (ادمین)
    disc_new_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(dadm_new_entry, pattern=r"^dadm_new$")],
        states={
            DISC_NEW_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_dnew_code)],
            DISC_NEW_TYPE: [CallbackQueryHandler(dnew_type_cb, pattern=r"^dnew_type_(percent|amount)$")],
            DISC_NEW_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_dnew_value)],
            DISC_NEW_MAX: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_dnew_max)],
            DISC_NEW_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_dnew_days)],
        },
        fallbacks=common_fallbacks,
        conversation_timeout=CONV_TIMEOUT,
        per_user=True,
    )

    # 🔒 عضویت اجباری در کانال: این باید قبل از هر هندلر دیگه‌ای اجرا بشه (group=-1)
    # تا هیچ بخشی از بات بدون عضویت در دسترس نباشه.
    app.add_handler(MessageHandler(filters.ALL, membership_gate), group=-1)
    app.add_handler(CallbackQueryHandler(membership_gate), group=-1)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(MessageHandler(filters.Sticker.ALL, sticker_id_grabber))
    app.add_handler(CallbackQueryHandler(check_join_cb, pattern=r"^check_join$"))

    # گفتگوهای چندمرحله‌ای (هر کدوم مستقل، برای جلوگیری از قفل شدن بقیه دکمه‌ها)
    for conv in (
        coin_conv, search_conv, send_msg_conv, support_conv, admin_reply_conv,
        broadcast_conv, charge_custom_conv, charge_receipt_conv, buy_config_conv,
        admin_sendcfg_conv, set_price_conv, admin_auto_add_conv, admin_test_add_conv,
        set_welcome_conv, set_card_number_conv, set_card_holder_conv, set_support_username_conv,
        set_signup_bonus_conv, set_referral_bonus_conv, admin_add_conv,
        plan_edit_price_conv, plan_edit_name_conv, plan_edit_text_conv, plan_new_conv,
        disc_apply_conv, disc_new_conv,
    ):
        app.add_handler(conv)

    # کاربر عادی
    app.add_handler(CallbackQueryHandler(back_main, pattern=r"^back_main$"))
    app.add_handler(CallbackQueryHandler(help_cb, pattern=r"^help$"))
    app.add_handler(CallbackQueryHandler(invite_cb, pattern=r"^invite$"))
    app.add_handler(CallbackQueryHandler(wallet, pattern=r"^wallet$"))
    app.add_handler(CallbackQueryHandler(account_info_cb, pattern=r"^account_info$"))
    app.add_handler(CallbackQueryHandler(noop_cb, pattern=r"^noop$"))
    app.add_handler(CallbackQueryHandler(tx_history, pattern=r"^tx_history$"))
    app.add_handler(CallbackQueryHandler(support_entry_cb, pattern=r"^support_entry$"))

    # خرید کانفیگ (منوی پلن‌ها + حجم دلخواه)
    app.add_handler(CallbackQueryHandler(buy_config_menu_cb, pattern=r"^buy_config$"))
    app.add_handler(CallbackQueryHandler(cfg_confirm_cb, pattern=r"^cfg_confirm$"))
    app.add_handler(CallbackQueryHandler(cfg_cancel_cb, pattern=r"^cfg_cancel$"))

    # خرید اتوماتیک / پلن‌ها
    app.add_handler(CallbackQueryHandler(auto_buy_menu_cb, pattern=r"^auto_buy_menu$"))
    app.add_handler(CallbackQueryHandler(plan_select_cb, pattern=r"^plan_sel_(\d+)$"))
    app.add_handler(CallbackQueryHandler(plan_confirm_cb, pattern=r"^plan_ok_(\d+)$"))
    # دکمه‌های قدیمی (پیام‌های قبل از این آپدیت) → هدایت به منوی جدید
    app.add_handler(CallbackQueryHandler(auto_buy_menu_cb, pattern=r"^auto_(pkg|confirm)_\d+$"))
    app.add_handler(CallbackQueryHandler(auto_cancel_cb, pattern=r"^auto_cancel$"))
    app.add_handler(CallbackQueryHandler(admin_auto_menu_cb, pattern=r"^admin_auto_menu$"))

    # 🎟 کدهای تخفیف
    app.add_handler(CallbackQueryHandler(disc_clear_cb, pattern=r"^disc_clear$"))
    app.add_handler(CallbackQueryHandler(admin_discounts_cb, pattern=r"^admin_discounts$"))
    app.add_handler(CallbackQueryHandler(dadm_view_cb, pattern=r"^dadm_(\d+)$"))
    app.add_handler(CallbackQueryHandler(dadm_toggle_cb, pattern=r"^dadm_toggle_(\d+)$"))
    app.add_handler(CallbackQueryHandler(dadm_del_cb, pattern=r"^dadm_del_(\d+)$"))
    app.add_handler(CallbackQueryHandler(dadm_delok_cb, pattern=r"^dadm_delok_(\d+)$"))

    # 🧩 مدیریت پلن‌ها (ادمین)
    app.add_handler(CallbackQueryHandler(admin_plans_menu_cb, pattern=r"^admin_plans_menu$"))
    app.add_handler(CallbackQueryHandler(plan_admin_view_cb, pattern=r"^padm_(\d+)$"))
    app.add_handler(CallbackQueryHandler(padm_mode_cb, pattern=r"^padm_mode_(\d+)$"))
    app.add_handler(CallbackQueryHandler(padm_show_cb, pattern=r"^padm_show_(\d+)$"))
    app.add_handler(CallbackQueryHandler(padm_toggle_cb, pattern=r"^padm_toggle_(\d+)$"))
    app.add_handler(CallbackQueryHandler(padm_del_cb, pattern=r"^padm_del_(\d+)$"))
    app.add_handler(CallbackQueryHandler(padm_delok_cb, pattern=r"^padm_delok_(\d+)$"))

    # تست رایگان
    app.add_handler(CallbackQueryHandler(free_test_entry_cb, pattern=r"^free_test_entry$"))
    app.add_handler(CallbackQueryHandler(free_test_claim_cb, pattern=r"^free_test_claim$"))
    app.add_handler(CallbackQueryHandler(admin_test_menu_cb, pattern=r"^admin_test_menu$"))

    # شارژ کیف پول
    app.add_handler(CallbackQueryHandler(charge_wallet_entry, pattern=r"^charge_wallet$"))
    app.add_handler(CallbackQueryHandler(charge_amount_cb, pattern=r"^charge_amt_(\d+)$"))
    app.add_handler(CallbackQueryHandler(dep_approve_cb, pattern=r"^dep_approve_(\d+)$"))
    app.add_handler(CallbackQueryHandler(dep_reject_cb, pattern=r"^dep_reject_(\d+)$"))

    # پنل ادمین
    app.add_handler(CallbackQueryHandler(admin_back, pattern=r"^admin_back$"))
    app.add_handler(CallbackQueryHandler(admin_users_menu, pattern=r"^admin_users$"))
    app.add_handler(CallbackQueryHandler(admin_orders_menu, pattern=r"^admin_orders_menu$"))
    app.add_handler(CallbackQueryHandler(admin_pending_orders_cb, pattern=r"^admin_pending_orders$"))
    app.add_handler(CallbackQueryHandler(admin_deposits_cb, pattern=r"^admin_deposits$"))
    app.add_handler(CallbackQueryHandler(admin_stats, pattern=r"^admin_stats$"))
    app.add_handler(CallbackQueryHandler(admin_backup_cb, pattern=r"^admin_backup$"))
    app.add_handler(CallbackQueryHandler(admin_settings_cb, pattern=r"^admin_settings$"))
    app.add_handler(CallbackQueryHandler(admin_notify_settings_cb, pattern=r"^admin_notify_settings$"))
    app.add_handler(CallbackQueryHandler(admin_support_inbox, pattern=r"^admin_support_inbox$"))
    app.add_handler(CallbackQueryHandler(recent_users_cb, pattern=r"^admin_recent_users$"))
    app.add_handler(CallbackQueryHandler(manage_user_cb, pattern=r"^act_manage_(\d+)$"))
    app.add_handler(CallbackQueryHandler(ban_user_cb, pattern=r"^act_ban_(\d+)$"))
    app.add_handler(CallbackQueryHandler(unban_user_cb, pattern=r"^act_unban_(\d+)$"))
    app.add_handler(CallbackQueryHandler(toggle_maintenance_cb, pattern=r"^toggle_maintenance$"))
    app.add_handler(CallbackQueryHandler(toggle_join_notify_cb, pattern=r"^toggle_join_notify$"))
    app.add_handler(CallbackQueryHandler(toggle_purchase_notify_cb, pattern=r"^toggle_purchase_notify$"))
    app.add_handler(CallbackQueryHandler(toggle_deposit_notify_cb, pattern=r"^toggle_deposit_notify$"))
    app.add_handler(CallbackQueryHandler(toggle_support_notify_cb, pattern=r"^toggle_support_notify$"))

    # مدیریت ادمین‌ها و پاک‌سازی داده‌ها (فقط مالک)
    app.add_handler(CallbackQueryHandler(admin_manage_admins_cb, pattern=r"^admin_manage_admins$"))
    app.add_handler(CallbackQueryHandler(admin_rm_cb, pattern=r"^admin_rm_(\d+)$"))
    app.add_handler(CallbackQueryHandler(admin_wipe_menu_cb, pattern=r"^admin_wipe_menu$"))
    app.add_handler(CallbackQueryHandler(wipe_ask_cb, pattern=r"^wipe_ask_(tx|orders|support|auto|test|users|full)$"))
    app.add_handler(CallbackQueryHandler(wipe_do_cb, pattern=r"^wipe_do_(tx|orders|support|auto|test|users|full)$"))

    # ⛑ شبکه‌ی ایمنی: اگه هیچ‌کدوم از بالا یه callback query رو مدیریت نکردن (مثلاً چون یه
    # گفتگوی نیمه‌تموم دیگه باز مونده)، حداقل یه پاسخ به کاربر/ادمین بدیم، نه سکوت مطلق.
    # این باید همیشه *آخرین* هندلر ثبت‌شده باشه.
    app.add_handler(CallbackQueryHandler(fallback_callback))

    app.add_error_handler(error_handler)

    print("🚀 بات اجرا شد! TEST123")
    app.run_polling()


if __name__ == "__main__":
    main()
