import os
import re
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

# ==================== تنظیمات ====================
TOKEN = os.environ["BOT_TOKEN"]
# ⚠️ توکن بات رو توی همین فایل به صورت متن‌باز گذاشتی. چون این فایل ممکنه دست کس دی بیفته،
# پیشنهاد می‌کنم از @BotFather دستور /revoke بزنی و یه توکن جدید بگیری.

# 👑 مالکان اصلی ربات (این‌ها همیشه دسترسی کامل دارن و هیچ‌کس نمی‌تونه حذفشون کنه).
# برای اضافه کردن مالک دوم، فقط آیدی عددیش رو داخل همین ست بنویس:
OWNER_IDS = {
    7438138322,
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

# حداقل و حداکثر حجم قابل خرید (گیگابایت)
MIN_VOLUME_GB = 1
MAX_VOLUME_GB = 1000

# فاصله بین پیام‌های ارسال همگانی برای جلوگیری از محدودیت تلگرام (ثانیه)
BROADCAST_DELAY = 0.05

# مهلت هر گفتگوی چندمرحله‌ای (ثانیه) - بعد از این مدت بی‌فعالیتی، گفتگو خودکار لغو می‌شود
CONV_TIMEOUT = 600

# ==================== دیتابیس ====================
DB_PATH = os.environ.get("DB_PATH", "vip_bot.db")
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row


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

# ==================== States (هر گفتگو state های مستقل خودش رو داره) ====================
(ASK_USER_ID, ASK_AMOUNT, SEND_MSG_UID, SEND_MSG_TEXT, SUPPORT_MSG, ADMIN_REPLY_MSG,
 CHARGE_CUSTOM_AMOUNT, CHARGE_RECEIPT, ASK_VOLUME, ADMIN_SEND_CFG, SET_PRICE_PER_GB,
 SET_CARD_NUMBER, SET_CARD_HOLDER, SET_WELCOME, BC_TEXT, BC_CONFIRM,
 ADD_ADMIN_ID, SET_SUPPORT_USERNAME, SET_SIGNUP_BONUS, SET_REFERRAL_BONUS) = range(20)

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


def get_user(uid: int):
    return db_one("SELECT * FROM users WHERE id=?", (uid,))


def get_deposit(dep_id: int):
    return db_one("SELECT * FROM deposits WHERE id=?", (dep_id,))


def get_order(order_id: int):
    return db_one("SELECT * FROM config_orders WHERE id=?", (order_id,))


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
    return InlineKeyboardMarkup([[InlineKeyboardButton("🚫 لغو عملیات", callback_data="cancel_conv")]])


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


# ==================== عضویت اجباری در کانال ====================
def join_channel_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 عضویت در کانال", url=REQUIRED_CHANNEL_URL)],
        [InlineKeyboardButton("✅ عضو شدم", callback_data="check_join", style="success")],
    ])


async def is_member_of_channel(bot, user_id: int) -> bool:
    """چک می‌کنه کاربر عضو کانال اجباری هست یا نه."""
    try:
        member = await bot.get_chat_member(REQUIRED_CHANNEL_ID, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning("membership check failed for %s: %s", user_id, e)
        # اگه بات ادمین کانال نباشه یا خطای دیگه‌ای بخوره، برای امنیت عضو در نظر نمی‌گیریمش
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
        [InlineKeyboardButton("💳 شارژ کیف پول", callback_data="charge_wallet", style="primary"),
         InlineKeyboardButton("💰 اعتبار کیف پول", callback_data="wallet", style="primary")],
        [InlineKeyboardButton("🎉 دعوت دوستان", callback_data="invite", style="primary")],
        [InlineKeyboardButton("💬 پشتیبانی", callback_data="support_entry", style="primary")],
        [InlineKeyboardButton("❓ راهنما", callback_data="help", style="danger")],
    ]
    return InlineKeyboardMarkup(keyboard)


def admin_menu():
    keyboard = [
        [InlineKeyboardButton("📦 مدیریت حجم و کانفیگ‌ها", callback_data="admin_orders_menu")],
        [InlineKeyboardButton("👤 مدیریت کاربران", callback_data="admin_users")],
        [InlineKeyboardButton("💳 درخواست‌های شارژ", callback_data="admin_deposits")],
        [InlineKeyboardButton("💬 صندوق پشتیبانی", callback_data="admin_support_inbox")],
        [InlineKeyboardButton("📨 ارسال پیام به کاربر", callback_data="admin_send_msg_entry")],
        [InlineKeyboardButton("📢 ارسال همگانی", callback_data="admin_broadcast_entry")],
        [InlineKeyboardButton("📊 آمار کلی", callback_data="admin_stats")],
        [InlineKeyboardButton("💾 بکاپ دیتابیس", callback_data="admin_backup")],
        [InlineKeyboardButton("🛡 مدیریت ادمین‌ها", callback_data="admin_manage_admins")],
        [InlineKeyboardButton("🗑 پاک‌سازی داده‌ها", callback_data="admin_wipe_menu")],
        [InlineKeyboardButton("⚙️ تنظیمات بات", callback_data="admin_settings")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(keyboard)


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
        InlineKeyboardButton("✅ رفع مسدودیت", callback_data=f"act_unban_{uid}")
        if user["is_banned"]
        else InlineKeyboardButton("⛔ مسدود کردن", callback_data=f"act_ban_{uid}")
    )
    keyboard = [
        [InlineKeyboardButton("➕ افزایش موجودی", callback_data=f"act_addcoin_{uid}"),
         InlineKeyboardButton("➖ کاهش موجودی", callback_data=f"act_subcoin_{uid}")],
        [InlineKeyboardButton("📨 ارسال پیام", callback_data=f"admin_send_to_{uid}")],
        [ban_btn],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_users")],
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
            await update.message.reply_text(text, **kwargs)
        else:
            await context.bot.send_message(chat_id, text, **kwargs)

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
        await send(f"{welcome}", reply_markup=main_menu())
    else:
        if existing["is_banned"]:
            await send("⛔ شما مسدود هستید.\nبرای اعتراض از بخش پشتیبانی استفاده کنید.")
            return
        db_run("UPDATE users SET first_name=?, username=? WHERE id=?",
               (user.first_name, user.username, uid))
        await send(f"🔄 خوش برگشتی، {user.first_name}!", reply_markup=main_menu())


async def back_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit(query, f"🏠 {BOT_NAME}\n\nیکی از گزینه‌ها رو انتخاب کن:", reply_markup=main_menu())


async def help_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "❓ *راهنما*\n"
        "━━━━━━━━━━━━━━\n"
        " از «خرید کانفیگ» حجم دلخواهت رو انتخاب و پرداخت کن\n"
        "💳 از «شارژ کیف پول» حساب خودت رو شارژ کن\n"
        "💰موجودی و تاریخچه در «اعتبار کیف پول»\n"
        "🎉 با «دعوت دوستان» به ازای هر معرفی جایزه بگیر\n"
        f"💬 سوال داشتی به پشتیبانی پیام بده: @{get_support_username()}"
    )
    kb = [[InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")]]
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
        f"🔗 لینک اختصاصی تو:\n{link}\n\n"
        f"👥 تعداد دعوت‌شده‌ها: {referred}"
    )

    kb = [
        [InlineKeyboardButton("📤 اشتراک‌گذاری لینک", switch_inline_query="بیا با لینک من عضو شو!")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")]
    ]
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))
    

# ==================== کیف پول ====================
async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    user = get_user(uid)
    text = (
        f"💰 *اعتبار کیف پول*\n━━━━━━━━━━━━━━\n"
        f"موجودی فعلی: {fmt_money(user['balance'])} تومان\n"
        f"کانفیگ‌های خریداری‌شده: {user['used_configs']}\n"
        f"مجموع خرید: {fmt_money(user['total_spent'])} تومان"
    )
    kb = [
        [InlineKeyboardButton("💳شارژ کیف پول", callback_data="charge_wallet")],
        [InlineKeyboardButton("📜 تاریخچه تراکنش‌ها", callback_data="tx_history")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")],
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
            date = datetime.fromtimestamp(r["date"]).strftime("%m-%d %H:%M")
            lines.append(f"{date} | {sign}{fmt_money(r['amount'])} | {md_escape(r['description'])}")
        text = "\n".join(lines)
    kb = [[InlineKeyboardButton("🔙 بازگشت", callback_data="wallet")]]
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


# ==================== شارژ کیف پول ====================
def charge_amount_kb():
    keyboard = []
    row = []
    for amt in CHARGE_PRESETS:
        row.append(InlineKeyboardButton(f"{fmt_money(amt)} تومان", callback_data=f"charge_amt_{amt}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("✏️ مبلغ دلخواه", callback_data="charge_custom")])
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="wallet")])
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
        [InlineKeyboardButton("📤 ارسال فیش", callback_data="charge_send_receipt")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="charge_wallet")],
    ])
    if edit_query is not None:
        await safe_edit(edit_query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    else:
        await chat_send(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def charge_amount_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    amount = int(context.match.group(1))
    context.user_data["charge_amount"] = amount
    await show_charge_payment(None, amount, context, edit_query=query)


async def charge_custom_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit(query, "✏️ مبلغ دلخواه رو به تومان و فقط بصورت عدد بفرست:", reply_markup=cancel_kb())
    return CHARGE_CUSTOM_AMOUNT


async def receive_charge_custom_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(",", "")
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ فقط عدد مثبت بفرست یا لغو کن.", reply_markup=cancel_kb())
        return CHARGE_CUSTOM_AMOUNT
    amount = int(text)
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

    note = update.message.text if update.message.text else ""
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
                [InlineKeyboardButton("✅ تایید", callback_data=f"dep_approve_{dep_id}"),
                 InlineKeyboardButton("❌ رد", callback_data=f"dep_reject_{dep_id}")],
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

    db_run("UPDATE deposits SET status='approved', decided_at=? WHERE id=?", (time.time(), dep_id))
    db_run("UPDATE users SET balance=balance+? WHERE id=?", (dep["amount"], dep["user_id"]))
    log_tx(dep["user_id"], "charge_approved", dep["amount"], f"شارژ کیف پول (تایید #{dep_id})")

    await query.answer("✅ تایید شد")
    try:
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

    db_run("UPDATE deposits SET status='rejected', decided_at=? WHERE id=?", (time.time(), dep_id))

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
            InlineKeyboardButton(f"✅ تایید #{r['id']}", callback_data=f"dep_approve_{r['id']}"),
            InlineKeyboardButton(f"❌ رد #{r['id']}", callback_data=f"dep_reject_{r['id']}"),
        ])
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")])
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


# ==================== خرید کانفیگ (حجم دلخواه) ====================
async def buy_config_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    price_gb = get_price_per_gb()
    text = (
        "🛒 *خرید کانفیگ*\n"
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

    text = (
        "🧾 *تایید خرید*\n━━━━━━━━━━━━━━\n"
        f"📦 حجم: {fmt_volume(volume)} گیگابایت\n"
        f"💰 قیمت کل: {fmt_money(price)} تومان\n\n"
        "آیا خرید رو تایید می‌کنی؟"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ تایید خرید", callback_data="cfg_confirm")],
        [InlineKeyboardButton("❌ انصراف", callback_data="cfg_cancel")],
    ])
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    return ConversationHandler.END


async def cfg_cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("لغو شد")
    context.user_data.pop("pending_volume", None)
    context.user_data.pop("pending_price", None)
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
    if user["balance"] < price:
        await query.answer("❌ موجودی کافی نیست!", show_alert=True)
        await safe_edit(
            query,
            f"❌ موجودی کافی نداری!\nلازم: {fmt_money(price)} تومان\nموجودی تو: {fmt_money(user['balance'])} تومان",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 شارژ کیف پول", callback_data="charge_wallet")],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")],
            ])
        )
        return

    db_run("UPDATE users SET balance=balance-?, total_spent=total_spent+? WHERE id=?",
           (price, price, uid))
    order_id = db_run(
        "INSERT INTO config_orders (user_id, volume_gb, price, status, created_at) VALUES (?,?,?,?,?)",
        (uid, volume, price, "pending", time.time())
    ).lastrowid
    log_tx(uid, "purchase", -price, f"خرید کانفیگ {fmt_volume(volume)} گیگ (سفارش #{order_id})")

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
                InlineKeyboardButton("📤 ارسال کانفیگ", callback_data=f"sendcfg_order_{order_id}")
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
        await update.message.reply_text(
            f"✅ کانفیگ برای خریدار `{target_uid}` (سفارش #{order_id}) ارسال شد.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_menu()
        )
    except Exception as e:
        await update.message.reply_text(f"❌ ارسال ناموفق بود: {e}", reply_markup=admin_menu())

    context.user_data.pop("order_target_uid", None)
    context.user_data.pop("order_target_id", None)
    return ConversationHandler.END


# ---- پنل مدیریت حجم و کانفیگ (ادمین) ----
async def admin_orders_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    await query.answer()
    price_gb = get_price_per_gb()
    pending_count = db_one("SELECT COUNT(*) c FROM config_orders WHERE status='pending'")["c"]
    text = (
        "📦 *مدیریت حجم و کانفیگ‌ها*\n"
        "━━━━━━━━━━━━━━\n"
        f"💎 قیمت فعلی هر گیگ: {fmt_money(price_gb)} تومان\n"
        f"📥 سفارش‌های در انتظار ارسال: {pending_count}"
    )
    kb = [
        [InlineKeyboardButton("💎 تغییر قیمت هر گیگ", callback_data="admin_set_price")],
        [InlineKeyboardButton("📥 سفارش‌های در انتظار", callback_data="admin_pending_orders")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")],
    ]
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def admin_pending_orders_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    await query.answer()
    rows = db_all("SELECT * FROM config_orders WHERE status='pending' ORDER BY id DESC LIMIT 15")
    if not rows:
        await safe_edit(query, "📭 سفارش در انتظاری وجود نداره.", reply_markup=admin_menu())
        return
    text = "📥 *سفارش‌های در انتظار ارسال*\n━━━━━━━━━━━━━━\n"
    kb = []
    for r in rows:
        u = get_user(r["user_id"])
        name = md_escape(u["first_name"] or "ناشناس") if u else "حذف‌شده"
        text += f"#{r['id']} | {name} | {fmt_volume(r['volume_gb'])} گیگ | {fmt_money(r['price'])} تومان\n"
        kb.append([InlineKeyboardButton(f"📤 ارسال کانفیگ #{r['id']}", callback_data=f"sendcfg_order_{r['id']}")])
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin_orders_menu")])
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def admin_set_price_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
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
    await update.message.reply_text(f"✅ قیمت هر گیگ روی {fmt_money(int(text))} تومان تنظیم شد.", reply_markup=admin_menu())
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
        [InlineKeyboardButton("✍️ ارسال پیام", callback_data="support_start")],
        [InlineKeyboardButton(f"💬 پیام به @{support_username}", url=f"https://t.me/{support_username}")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="back_main")]
    ]
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def support_start_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
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
                [InlineKeyboardButton("💬 پاسخ دادن", callback_data=f"admin_reply_sel_{uid}_{msg_id}")],
                [InlineKeyboardButton("👤 پروفایل", callback_data=f"act_manage_{uid}")]
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
)


def _clear_conv_keys(user_data):
    for k in CONV_KEYS:
        user_data.pop(k, None)


async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _clear_conv_keys(context.user_data)
    query = update.callback_query
    menu = admin_menu() if is_admin(update.effective_user.id) else main_menu()
    if query:
        await query.answer()
        await safe_edit(query, "🚫 عملیات لغو شد.", reply_markup=menu)
    else:
        await update.message.reply_text("🚫 عملیات لغو شد.", reply_markup=menu)
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
    await update.message.reply_text("👮 پنل ادمین", reply_markup=admin_menu())


async def admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit(query, "👮 پنل ادمین", reply_markup=admin_menu())


# ---- مدیریت کاربران ----
async def admin_users_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    await query.answer()
    kb = [
        [InlineKeyboardButton("🔍 جستجوی کاربر با آیدی", callback_data="admin_search_entry")],
        [InlineKeyboardButton("🕒 کاربران اخیر", callback_data="admin_recent_users")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")],
    ]
    await safe_edit(query, "👤 مدیریت کاربران", reply_markup=InlineKeyboardMarkup(kb))


async def search_user_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
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
        kb.append([InlineKeyboardButton(f"مدیریت {r['id']}", callback_data=f"act_manage_{r['id']}")])
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin_users")])
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
        db_run("UPDATE users SET balance=balance-? WHERE id=?", (amount, uid))
        log_tx(uid, "admin_sub", -amount, "کاهش دستی توسط ادمین")
        msg = f"✅ {fmt_money(amount)} تومان از کاربر {uid} کم شد."
    await update.message.reply_text(msg, reply_markup=admin_menu())
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
        f"👤 کاربر: {user['first_name'] or '-'} (`{uid}`)\n\n📨 پیامت رو بفرست:",
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
        await update.message.reply_text(f"✅ پیام به کاربر {uid} ارسال شد.", reply_markup=admin_menu())
    except Exception as e:
        await update.message.reply_text(f"❌ ارسال ناموفق! (ممکنه کاربر بات رو بلاک کرده باشه)\nخطا: {e}", reply_markup=admin_menu())

    _clear_conv_keys(context.user_data)
    return ConversationHandler.END


# ---- صندوق پشتیبانی (ادمین) ----
async def admin_support_inbox(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    await query.answer()

    unread = db_one("SELECT COUNT(*) c FROM support_messages WHERE is_from_admin=0 AND is_read=0")["c"]
    rows = db_all("SELECT * FROM support_messages WHERE is_from_admin=0 ORDER BY id DESC LIMIT 15")

    if not rows:
        text = "📭 صندوق پشتیبانی خالیه!"
        kb = [[InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")]]
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup(kb))
        return

    text = f"💬 *صندوق پشتیبانی* ({unread} خوانده‌نشده)\n━━━━━━━━━━━━━━\n"
    kb = []
    for r in rows:
        user = get_user(r["user_id"])
        name = md_escape(user["first_name"] or "ناشناس") if user else "حذف‌شده"
        read_flag = "📋" if r["is_read"] else "🔵"
        short_msg = md_escape(r["message"][:30]) + ("..." if len(r["message"]) > 30 else "")
        text += f"{read_flag} #{r['id']} | {name} | {short_msg}\n"
        kb.append([InlineKeyboardButton(
            f"💬 پاسخ #{r['id']}", callback_data=f"admin_reply_sel_{r['user_id']}_{r['id']}"
        )])
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")])
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


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
        await update.message.reply_text("✅ پاسخ ارسال شد.", reply_markup=admin_menu())
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
        [InlineKeyboardButton("✅ ارسال به همه", callback_data="bc_yes")],
        [InlineKeyboardButton("🚫 لغو", callback_data="cancel_conv")],
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
    admins_count = len(admin_ids())
    text = (
        f"📊 *آمار کلی*\n━━━━━━━━━━━━━━\n"
        f"👥 کل کاربران: {total_users}\n"
        f"⛔ مسدود شده: {banned}\n"
        f"💵 مجموع خرید کاربران: {fmt_money(total_spent)} تومان\n"
        f"📦 کانفیگ‌های ارسال‌شده: {delivered_orders}\n"
        f"📥 سفارش در انتظار ارسال: {pending_orders}\n"
        f"💳 درخواست شارژ در انتظار: {pending_dep}\n"
        f"🛡 تعداد ادمین‌ها: {admins_count}"
    )
    kb = [[InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")]]
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
async def admin_manage_admins_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_owner(update):
        return
    query = update.callback_query
    await query.answer()
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
        kb.append([InlineKeyboardButton(f"➖ حذف {a['id']}", callback_data=f"admin_rm_{a['id']}")])
    kb.append([InlineKeyboardButton("➕ افزودن ادمین جدید", callback_data="admin_add_entry")])
    kb.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")])
    await safe_edit(query, "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def admin_add_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_owner(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
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
    await update.message.reply_text(f"✅ کاربر `{new_id}` به عنوان ادمین اضافه شد.",
                                     parse_mode=ParseMode.MARKDOWN, reply_markup=admin_menu())
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
    "users": "همه کاربران، کیف پول‌ها و کل تاریخچه‌شون",
    "full": "کل دیتابیس (کاربران، تراکنش‌ها، سفارش‌ها، پیام‌ها)",
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
        [InlineKeyboardButton("🧾 پاک کردن تاریخچه تراکنش‌ها", callback_data="wipe_ask_tx")],
        [InlineKeyboardButton("📦 پاک کردن سفارش‌ها و شارژها", callback_data="wipe_ask_orders")],
        [InlineKeyboardButton("💬 پاک کردن پیام‌های پشتیبانی", callback_data="wipe_ask_support")],
        [InlineKeyboardButton("👥 پاک کردن همه کاربران", callback_data="wipe_ask_users")],
        [InlineKeyboardButton("💣 ریست کامل دیتابیس", callback_data="wipe_ask_full")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")],
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
        [InlineKeyboardButton("✅ بله، پاک کن", callback_data=f"wipe_do_{key}"),
         InlineKeyboardButton("🚫 نه، لغو", callback_data="admin_wipe_menu")],
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
        elif key == "users":
            for t in ("users", "transactions", "deposits", "config_orders", "support_messages"):
                db_run(f"DELETE FROM {t}")
        elif key == "full":
            for t in ("users", "transactions", "deposits", "config_orders", "support_messages"):
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
async def admin_settings_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard_admin(update):
        return
    query = update.callback_query
    await query.answer()

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
                               callback_data="toggle_maintenance")],
        [InlineKeyboardButton("🔔 تنظیمات اطلاع‌رسانی", callback_data="admin_notify_settings")],
        [InlineKeyboardButton("💳 تغییر شماره کارت", callback_data="set_card_number_entry")],
        [InlineKeyboardButton("👤 تغییر نام صاحب کارت", callback_data="set_card_holder_entry")],
        [InlineKeyboardButton("☎️ تغییر آیدی پشتیبانی", callback_data="set_support_username_entry")],
        [InlineKeyboardButton("📝 تغییر پیام خوش‌آمدگویی", callback_data="set_welcome_entry")],
        [InlineKeyboardButton("🎁 تغییر هدیه عضویت", callback_data="set_signup_bonus_entry")],
        [InlineKeyboardButton("🤝 تغییر هدیه دعوت", callback_data="set_referral_bonus_entry")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")],
    ]
    await safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


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
        [InlineKeyboardButton("🆕 تغییر وضعیت اطلاع کاربر جدید", callback_data="toggle_join_notify")],
        [InlineKeyboardButton("🛒 تغییر وضعیت اطلاع خرید", callback_data="toggle_purchase_notify")],
        [InlineKeyboardButton("💳 تغییر وضعیت اطلاع شارژ", callback_data="toggle_deposit_notify")],
        [InlineKeyboardButton("💬 تغییر وضعیت اطلاع پشتیبانی", callback_data="toggle_support_notify")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_settings")],
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
    await update.message.reply_text("✅ پیام خوش‌آمدگویی تغییر کرد!", reply_markup=admin_menu())
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
    await update.message.reply_text("✅ شماره کارت بروزرسانی شد.", reply_markup=admin_menu())
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
    await update.message.reply_text("✅ نام صاحب کارت بروزرسانی شد.", reply_markup=admin_menu())
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
    await update.message.reply_text("✅ آیدی پشتیبانی بروزرسانی شد.", reply_markup=admin_menu())
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
    await update.message.reply_text(f"✅ هدیه عضویت روی {fmt_money(int(text))} تومان تنظیم شد.", reply_markup=admin_menu())
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
    await update.message.reply_text(f"✅ هدیه دعوت روی {fmt_money(int(text))} تومان تنظیم شد.", reply_markup=admin_menu())
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
        entry_points=[CallbackQueryHandler(buy_config_entry, pattern=r"^buy_config$")],
        states={ASK_VOLUME: [
            CallbackQueryHandler(buy_config_entry, pattern=r"^buy_config$"),
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

    # 🔒 عضویت اجباری در کانال: این باید قبل از هر هندلر دیگه‌ای اجرا بشه (group=-1)
    # تا هیچ بخشی از بات بدون عضویت در دسترس نباشه.
    app.add_handler(MessageHandler(filters.ALL, membership_gate), group=-1)
    app.add_handler(CallbackQueryHandler(membership_gate), group=-1)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CallbackQueryHandler(check_join_cb, pattern=r"^check_join$"))

    # گفتگوهای چندمرحله‌ای (هر کدوم مستقل، برای جلوگیری از قفل شدن بقیه دکمه‌ها)
    for conv in (
        coin_conv, search_conv, send_msg_conv, support_conv, admin_reply_conv,
        broadcast_conv, charge_custom_conv, charge_receipt_conv, buy_config_conv,
        admin_sendcfg_conv, set_price_conv, set_welcome_conv,
        set_card_number_conv, set_card_holder_conv, set_support_username_conv,
        set_signup_bonus_conv, set_referral_bonus_conv, admin_add_conv,
    ):
        app.add_handler(conv)

    # کاربر عادی
    app.add_handler(CallbackQueryHandler(back_main, pattern=r"^back_main$"))
    app.add_handler(CallbackQueryHandler(help_cb, pattern=r"^help$"))
    app.add_handler(CallbackQueryHandler(invite_cb, pattern=r"^invite$"))
    app.add_handler(CallbackQueryHandler(wallet, pattern=r"^wallet$"))
    app.add_handler(CallbackQueryHandler(tx_history, pattern=r"^tx_history$"))
    app.add_handler(CallbackQueryHandler(support_entry_cb, pattern=r"^support_entry$"))

    # خرید کانفیگ
    app.add_handler(CallbackQueryHandler(cfg_confirm_cb, pattern=r"^cfg_confirm$"))
    app.add_handler(CallbackQueryHandler(cfg_cancel_cb, pattern=r"^cfg_cancel$"))

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
    app.add_handler(CallbackQueryHandler(wipe_ask_cb, pattern=r"^wipe_ask_(tx|orders|support|users|full)$"))
    app.add_handler(CallbackQueryHandler(wipe_do_cb, pattern=r"^wipe_do_(tx|orders|support|users|full)$"))

    # ⛑ شبکه‌ی ایمنی: اگه هیچ‌کدوم از بالا یه callback query رو مدیریت نکردن (مثلاً چون یه
    # گفتگوی نیمه‌تموم دیگه باز مونده)، حداقل یه پاسخ به کاربر/ادمین بدیم، نه سکوت مطلق.
    # این باید همیشه *آخرین* هندلر ثبت‌شده باشه.
    app.add_handler(CallbackQueryHandler(fallback_callback))

    app.add_error_handler(error_handler)

    print("🚀 بات اجرا شد! TEST123")
    app.run_polling()


if __name__ == "__main__":
    main()
