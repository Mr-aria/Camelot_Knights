# -*- coding: utf-8 -*-
"""ربات گزارش‌های شهروندان کملوت - با پنل مدیریت، پاسخ و بلاک"""

from __future__ import annotations

import logging
import os
import json
import sqlite3
import threading
import io
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, List, Dict, Any

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    ConversationHandler,
)

# -----------------------------
# Configuration
# -----------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "توکن_ربات_را_اینجا_بگذارید_یا_در_متغیرهای_محیطی")
OWNER_ID = 1275490079
TEHRAN = ZoneInfo("Asia/Tehran")
DB_PATH = "report_bot.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("camelot-report-bot")

# -----------------------------
# Constants
# -----------------------------

BTN_ADMIN = "🛠 پنل مدیریت"

# -----------------------------
# SQLite helpers
# -----------------------------

_db_lock = threading.RLock()
_db = sqlite3.connect(DB_PATH, check_same_thread=False)
_db.row_factory = sqlite3.Row

def db_exec(query: str, params: tuple = ()) -> None:
    with _db_lock:
        cur = _db.execute(query, params)
        _db.commit()
        return cur

def db_one(query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    with _db_lock:
        cur = _db.execute(query, params)
        return cur.fetchone()

def db_all(query: str, params: tuple = ()) -> List[sqlite3.Row]:
    with _db_lock:
        cur = _db.execute(query, params)
        return cur.fetchall()

def init_db() -> None:
    with _db_lock:
        _db.execute("PRAGMA journal_mode=WAL;")
        _db.execute(
            """
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                replied_at TEXT,
                reply_text TEXT
            )
            """
        )
        _db.execute(
            """
            CREATE TABLE IF NOT EXISTS blacklist (
                user_id INTEGER PRIMARY KEY,
                reason TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        _db.execute(
            """
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        _db.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        _db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('bot_status', 'on')")
        _db.commit()

init_db()

# -----------------------------
# Utility helpers
# -----------------------------

def now_tehran() -> str:
    return datetime.now(TEHRAN).strftime("%Y-%m-%d %H:%M:%S")

def bot_is_on() -> bool:
    row = db_one("SELECT value FROM settings WHERE key = 'bot_status'")
    return row["value"] == "on" if row else True

def set_bot_status(status: str) -> None:
    db_exec("INSERT INTO settings(key, value) VALUES('bot_status', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (status,))

def log_action(user_id: Optional[int], action: str, details: str = "") -> None:
    action_map = {
        "report_received": "دریافت گزارش",
        "owner_reply": "پاسخ مالک",
        "user_blocked": "بلاک کاربر",
        "user_unblocked": "آنبلاک کاربر",
        "toggle_bot": "تغییر وضعیت ربات",
        "backup_export": "گرفتن پشتیبان",
        "backup_import": "بازیابی از پشتیبان",
    }
    persian_action = action_map.get(action, action)
    db_exec(
        "INSERT INTO logs(user_id, action, details, created_at) VALUES(?, ?, ?, ?)",
        (user_id, persian_action, details, now_tehran()),
    )

def is_owner(uid: int) -> bool:
    return uid == OWNER_ID

def is_blacklisted(uid: int) -> bool:
    return db_one("SELECT 1 FROM blacklist WHERE user_id = ?", (uid,)) is not None

# ==================== Backup & Restore Functions ====================

def export_full_backup() -> str:
    tables = ['reports', 'blacklist', 'logs', 'settings']
    data = {}
    with _db_lock:
        for table in tables:
            cursor = _db.execute(f"SELECT * FROM {table}")
            rows = cursor.fetchall()
            data[table] = [dict(row) for row in rows]
    return json.dumps(data, indent=2, ensure_ascii=False)

def import_full_backup(json_data: str) -> tuple:
    try:
        data = json.loads(json_data)
    except json.JSONDecodeError as e:
        return False, f"فایل JSON معتبر نیست: {e}"
    expected = {'reports', 'blacklist', 'logs', 'settings'}
    if not expected.issubset(data.keys()):
        return False, "فایل پشتیبان کامل نیست."
    with _db_lock:
        try:
            for table in expected:
                _db.execute(f"DELETE FROM {table}")
            for table, rows in data.items():
                if not rows:
                    continue
                columns = list(rows[0].keys())
                placeholders = ','.join(['?' for _ in columns])
                col_names = ','.join(columns)
                for row in rows:
                    values = [row.get(col) for col in columns]
                    _db.execute(f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})", values)
            _db.commit()
            return True, "بازیابی با موفقیت انجام شد."
        except Exception as e:
            _db.rollback()
            return False, f"خطا: {str(e)}"

# ==================== Access control ====================

async def check_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id if update.effective_user else None
    if uid is None:
        return False
    if is_blacklisted(uid) and not is_owner(uid):
        msg = "⛔ شما توسط مالک ربات بلاک شده‌اید و نمی‌توانید پیام ارسال کنید."
        if update.message:
            await update.message.reply_text(msg)
        elif update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        return False
    if not bot_is_on() and not is_owner(uid):
        msg = "⛔ ربات در حال حاضر خاموش است."
        if update.message:
            await update.message.reply_text(msg)
        elif update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        return False
    return True

# ==================== Keyboards ====================

def main_menu_kb(uid: int) -> ReplyKeyboardMarkup:
    rows = []
    if is_owner(uid):
        rows.append([BTN_ADMIN])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔌 خاموش/روشن", callback_data="admin_toggle_bot")],
        [InlineKeyboardButton("📋 لاگ‌ها", callback_data="admin_logs")],
        [InlineKeyboardButton("💾 پشتیبان‌گیری و بازیابی", callback_data="admin_backup")],
        [InlineKeyboardButton("❌ بستن پنل", callback_data="cancel_action")],
    ])

def backup_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 گرفتن پشتیبان", callback_data="admin_backup_export")],
        [InlineKeyboardButton("📤 بازیابی از پشتیبان", callback_data="admin_backup_import")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="admin_back")],
    ])

def confirm_kb(yes_data: str, no_data: str = "cancel_action") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ بله", callback_data=yes_data),
        InlineKeyboardButton("❌ نه", callback_data=no_data),
    ]])

def report_notification_kb(report_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📩 پاسخ به گزارش", callback_data=f"admin_reply_{report_id}")],
        [InlineKeyboardButton("🚫 بلاک کاربر", callback_data=f"admin_block_{report_id}")],
    ])

# ==================== Start & Cancel ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await check_access(update, context):
        return
    uid = update.effective_user.id
    welcome = (
        "🏛 **درود ای شهروند کملوت.**\n\n"
        "چه کمکی از دستم برمی‌آید؟ آیا گزارشی داری؟\n\n"
        "لطفاً پیام خود را برای من ارسال کن تا به اطلاع مالک برسانم."
    )
    await update.message.reply_text(
        welcome,
        reply_markup=main_menu_kb(uid),
        parse_mode='Markdown'
    )

# ==================== Message Handler ====================

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """دریافت پیام کاربر و ارسال به مالک"""
    if not update.message or not update.effective_user:
        return
    if not await check_access(update, context):
        return

    uid = update.effective_user.id
    user = update.effective_user
    message_text = update.message.text or update.message.caption or ""

    # اگر دکمه مدیریت بود، آن را مدیریت کن
    if message_text == BTN_ADMIN and is_owner(uid):
        await update.message.reply_text("🛠 **پنل مدیریت**", reply_markup=admin_kb(), parse_mode='Markdown')
        return

    # ذخیره گزارش در دیتابیس
    username = user.username or "بدون یوزرنیم"
    full_name = user.full_name or "بدون نام"
    created_at = now_tehran()

    cursor = db_exec(
        """
        INSERT INTO reports (user_id, username, full_name, message, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (uid, username, full_name, message_text, created_at)
    )
    report_id = cursor.lastrowid
    log_action(uid, "report_received", f"report_id={report_id}")

    # پاسخ به کاربر
    await update.message.reply_text(
        "✅ حرف ها و پیام هایت شنیده شد. مرسی از همراهیت.",
        reply_markup=main_menu_kb(uid)
    )

    # ارسال به مالک
    await notify_owner(update, context, report_id, uid, username, full_name, message_text, created_at)

async def notify_owner(update: Update, context: ContextTypes.DEFAULT_TYPE, report_id: int, uid: int, username: str, full_name: str, message_text: str, created_at: str):
    """ارسال گزارش به مالک"""
    msg = (
        f"📌 *گزارش جدید*\n\n"
        f"🔖 شماره گزارش: `{report_id}`\n"
        f"🕒 تاریخ: `{created_at}`\n\n"
        f"👤 *اطلاعات فرستنده*\n"
        f"• آیدی عددی: `{uid}`\n"
        f"• یوزرنیم: @{username if username != 'بدون یوزرنیم' else 'ندارد'}\n"
        f"• نام اکانت: {full_name}\n\n"
        f"📝 *متن گزارش:*\n"
        f"{message_text}"
    )

    try:
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=msg,
            parse_mode='Markdown',
            reply_markup=report_notification_kb(report_id)
        )
    except Exception as e:
        logger.error(f"Failed to notify owner: {e}")

# ==================== Admin Reply (پاسخ به گزارش) ====================

async def admin_reply_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """شروع پاسخ به گزارش (از طریق دکمه)"""
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if not is_owner(uid):
        await query.edit_message_text("⛔ دسترسی ندارید.")
        return ConversationHandler.END
    if not await check_access(update, context):
        return ConversationHandler.END

    report_id = int(query.data.split('_')[2])
    context.user_data['reply_report_id'] = report_id
    await query.edit_message_text(
        f"📩 **پاسخ به گزارش #{report_id}**\n\n"
        "لطفاً پاسخ خود را به صورت متن ارسال کنید:\n"
        "(برای لغو /cancel بزنید)",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ لغو", callback_data="cancel_action")]
        ])
    )
    return 10  # S_ADMIN_REPLY_TEXT

async def admin_reply_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """دریافت متن پاسخ و ارسال به کاربر"""
    uid = update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("⛔ دسترسی ندارید.")
        return ConversationHandler.END
    if not await check_access(update, context):
        return ConversationHandler.END

    reply_text = update.message.text
    report_id = context.user_data.get('reply_report_id')
    if not report_id:
        await update.message.reply_text("❌ خطا: شناسه گزارش یافت نشد.")
        return ConversationHandler.END

    # دریافت اطلاعات کاربر
    report = db_one("SELECT user_id FROM reports WHERE id = ?", (report_id,))
    if not report:
        await update.message.reply_text("❌ گزارش یافت نشد.")
        return ConversationHandler.END

    target_user_id = report['user_id']

    # بروزرسانی دیتابیس
    db_exec(
        "UPDATE reports SET reply_text = ?, replied_at = ? WHERE id = ?",
        (reply_text, now_tehran(), report_id)
    )
    log_action(uid, "owner_reply", f"report_id={report_id}, user={target_user_id}")

    # ارسال پاسخ به کاربر
    try:
        await context.bot.send_message(
            chat_id=target_user_id,
            text=f"📩 **پاسخ مالک ربات به گزارش شما**\n\n"
                 f"{reply_text}\n\n"
                 f"🕐 {now_tehran()}",
            parse_mode='Markdown'
        )
        await update.message.reply_text(
            f"✅ پاسخ شما به گزارش #{report_id} با موفقیت ارسال شد.",
            reply_markup=main_menu_kb(uid)
        )
    except Exception as e:
        logger.error(f"Error sending reply to user: {e}")
        await update.message.reply_text(
            f"⚠️ پاسخ در دیتابیس ثبت شد اما ارسال به کاربر با خطا مواجه شد.",
            reply_markup=main_menu_kb(uid)
        )

    context.user_data.pop('reply_report_id', None)
    return ConversationHandler.END

# ==================== Admin Block (بلاک کاربر) ====================

async def admin_block_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """بلاک کردن کاربر از طریق دکمه"""
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if not is_owner(uid):
        await query.edit_message_text("⛔ دسترسی ندارید.")
        return
    if not await check_access(update, context):
        return

    report_id = int(query.data.split('_')[2])
    report = db_one("SELECT user_id, username FROM reports WHERE id = ?", (report_id,))
    if not report:
        await query.edit_message_text("❌ گزارش یافت نشد.")
        return

    target_user_id = report['user_id']
    target_username = report['username'] or str(target_user_id)

    # اضافه به لیست سیاه
    db_exec(
        "INSERT OR REPLACE INTO blacklist (user_id, reason, created_at) VALUES (?, ?, ?)",
        (target_user_id, f"بلاک شده توسط مالک (گزارش #{report_id})", now_tehran())
    )
    log_action(uid, "user_blocked", f"user={target_user_id}, report={report_id}")

    # ارسال پیام به کاربر بلاک شده (اگر خطا داد نادیده بگیریم چون ممکن است ربات نتواند به کاربر بلاک شده پیام دهد)
    try:
        await context.bot.send_message(
            chat_id=target_user_id,
            text="⛔ شما توسط مالک ربات بلاک شده‌اید و دیگر نمی‌توانید پیام ارسال کنید."
        )
    except Exception:
        pass

    await query.edit_message_text(
        f"✅ کاربر {target_username} با موفقیت بلاک شد.",
        reply_markup=main_menu_kb(uid)
    )

# ==================== Owner Direct Reply (ریپلای به پیام) ====================

async def owner_direct_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مالک می‌تواند روی پیام گزارش ریپلای بزند و پاسخ مستقیم به کاربر ارسال شود"""
    if not update.message or not update.message.text:
        return

    uid = update.effective_user.id
    if uid != OWNER_ID:
        return

    replied = update.message.reply_to_message
    if not replied:
        return

    replied_text = replied.text or replied.caption or ""
    if "شماره گزارش:" not in replied_text:
        return

    match = re.search(r"شماره گزارش:\s*`?(\d+)`?", replied_text)
    if not match:
        match = re.search(r"شماره گزارش:\s*(\d+)", replied_text)
    if not match:
        await update.message.reply_text("❌ شماره گزارش در پیام پیدا نشد.")
        return

    report_id = int(match.group(1))
    answer_text = update.message.text.strip()

    report = db_one("SELECT user_id FROM reports WHERE id = ?", (report_id,))
    if not report:
        await update.message.reply_text("❌ گزارش پیدا نشد.")
        return

    target_user_id = report['user_id']

    try:
        await context.bot.send_message(
            chat_id=target_user_id,
            text=f"📩 **پاسخ مالک ربات به گزارش شما**\n\n"
                 f"{answer_text}\n\n"
                 f"🕐 {now_tehran()}",
            parse_mode='Markdown'
        )

        db_exec(
            "UPDATE reports SET reply_text = ?, replied_at = ? WHERE id = ?",
            (answer_text, now_tehran(), report_id)
        )
        log_action(uid, "owner_reply", f"report_id={report_id}, user={target_user_id}")

        await update.message.reply_text(
            f"✅ پاسخ شما برای کاربر (گزارش #{report_id}) ارسال شد.",
            reply_markup=main_menu_kb(uid)
        )
    except Exception as e:
        logger.error(f"Failed to send owner reply: {e}")
        await update.message.reply_text(f"❌ ارسال پاسخ ناموفق بود: {str(e)}")

# ==================== Admin Panel ====================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if not is_owner(uid):
        await query.edit_message_text("⛔ دسترسی ندارید.")
        return
    if not await check_access(update, context):
        return

    await query.edit_message_text("🛠 **پنل مدیریت**", reply_markup=admin_kb(), parse_mode='Markdown')

async def admin_toggle_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if not is_owner(uid):
        await query.edit_message_text("⛔ دسترسی ندارید.")
        return
    if not await check_access(update, context):
        return

    new_status = "off" if bot_is_on() else "on"
    set_bot_status(new_status)
    log_action(uid, "toggle_bot", f"وضعیت: {'خاموش' if new_status == 'off' else 'روشن'}")
    status_text = "🟢 روشن" if new_status == "on" else "🔴 خاموش"
    await query.edit_message_text(
        f"✅ وضعیت ربات: {status_text}",
        reply_markup=admin_kb()
    )

async def admin_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if not is_owner(uid):
        await query.edit_message_text("⛔ دسترسی ندارید.")
        return
    if not await check_access(update, context):
        return

    rows = db_all("SELECT * FROM logs ORDER BY id DESC LIMIT 50")
    if not rows:
        await query.edit_message_text("📭 هیچ لاگی وجود ندارد.", reply_markup=admin_kb())
        return

    text = "📋 **لاگ‌های اخیر**\n━━━━━━━━━━━━━━━━━━━\n"
    for row in rows:
        user_info = f"کاربر: {row['user_id']}" if row['user_id'] else "سیستم"
        text += f"🕐 {row['created_at']}\n"
        text += f"👤 {user_info}\n"
        text += f"📌 {row['action']}\n"
        if row['details']:
            text += f"📝 {row['details']}\n"
        text += "━━━━━━━━━━━━━━━━━━━\n"
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=admin_kb())

async def admin_backup_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if not is_owner(uid):
        await query.edit_message_text("⛔ دسترسی ندارید.")
        return
    if not await check_access(update, context):
        return

    await query.edit_message_text(
        "💾 **پشتیبان‌گیری و بازیابی**",
        reply_markup=backup_kb(),
        parse_mode='Markdown'
    )

async def admin_backup_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if not is_owner(uid):
        await query.edit_message_text("⛔ دسترسی ندارید.")
        return
    if not await check_access(update, context):
        return

    await query.edit_message_text("📥 در حال تهیه پشتیبان... لطفاً صبر کنید.", parse_mode='Markdown')
    try:
        json_data = export_full_backup()
        file_obj = io.BytesIO(json_data.encode('utf-8'))
        file_obj.name = f"report_bot_backup_{datetime.now(TEHRAN).strftime('%Y%m%d_%H%M%S')}.json"
        await context.bot.send_document(
            chat_id=uid,
            document=file_obj,
            caption="💾 **پشتیبان ربات گزارش‌ها**\n\n"
                    f"🕐 تاریخ: {now_tehran()}\n"
                    "برای بازیابی، از بخش «بازیابی از پشتیبان» استفاده کنید.",
            parse_mode='Markdown'
        )
        log_action(uid, "backup_export", f"تعداد رکوردها: {len(json.loads(json_data).get('reports', []))}")
        await query.edit_message_text(
            "✅ پشتیبان با موفقیت تهیه و ارسال شد.",
            reply_markup=backup_kb()
        )
    except Exception as e:
        logger.error(f"Export backup error: {e}")
        await query.edit_message_text(f"❌ خطا: {str(e)}", reply_markup=backup_kb())

# Admin backup import conversation
async def admin_backup_import_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if not is_owner(uid):
        await query.edit_message_text("⛔ دسترسی ندارید.")
        return ConversationHandler.END
    if not await check_access(update, context):
        return ConversationHandler.END

    await query.edit_message_text(
        "📤 **بازیابی از پشتیبان**\n\n"
        "⚠️ این عملیات تمام اطلاعات فعلی را بازنویسی می‌کند.\n"
        "لطفاً فایل JSON پشتیبان را ارسال کنید.\n"
        "(برای لغو /cancel بزنید)",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ لغو", callback_data="cancel_action")]
        ])
    )
    return 20  # S_ADMIN_BACKUP_IMPORT_FILE

async def admin_backup_import_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    if not is_owner(uid):
        await update.message.reply_text("⛔ دسترسی ندارید.")
        return ConversationHandler.END
    if not await check_access(update, context):
        return ConversationHandler.END

    document = update.message.document
    if not document or not document.file_name.endswith('.json'):
        await update.message.reply_text("❌ لطفاً یک فایل JSON معتبر ارسال کنید.", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ لغو", callback_data="cancel_action")]
        ]))
        return 20

    try:
        file = await context.bot.get_file(document.file_id)
        content = await file.download_as_bytearray()
        json_data = content.decode('utf-8')
        context.user_data['backup_json_data'] = json_data
        await update.message.reply_text(
            "⚠️ تأیید نهایی: آیا مطمئن هستید؟",
            reply_markup=confirm_kb("admin_backup_import_confirm", "cancel_action")
        )
        return 21  # S_ADMIN_BACKUP_CONFIRM
    except Exception as e:
        await update.message.reply_text(f"❌ خطا: {str(e)}", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ لغو", callback_data="cancel_action")]
        ]))
        return ConversationHandler.END

async def admin_backup_import_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if not is_owner(uid):
        await query.edit_message_text("⛔ دسترسی ندارید.")
        return
    if not await check_access(update, context):
        return

    json_data = context.user_data.get('backup_json_data')
    if not json_data:
        await query.edit_message_text("❌ داده‌های پشتیبان یافت نشد.")
        return

    await query.edit_message_text("🔄 در حال بازیابی... لطفاً صبر کنید.", parse_mode='Markdown')
    success, msg = import_full_backup(json_data)
    if success:
        log_action(uid, "backup_import", "بازیابی موفقیت‌آمیز")
        await query.edit_message_text(
            "✅ بازیابی با موفقیت انجام شد.\nلطفاً ربات را ری‌استارت کنید.",
            reply_markup=admin_kb()
        )
    else:
        await query.edit_message_text(f"❌ خطا: {msg}", reply_markup=admin_kb())
    context.user_data.pop('backup_json_data', None)

async def admin_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    if not is_owner(uid):
        await query.edit_message_text("⛔ دسترسی ندارید.")
        return
    if not await check_access(update, context):
        return

    await query.edit_message_text("🛠 پنل مدیریت", reply_markup=admin_kb(), parse_mode='Markdown')

# ==================== Callback Handler ====================

async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """مدیریت کالبک‌های مدیریتی (با پیشوند admin_)"""
    query = update.callback_query
    data = query.data

    if not await check_access(update, context):
        return

    if data == "admin_panel":
        await admin_panel(update, context)
    elif data == "admin_toggle_bot":
        await admin_toggle_bot(update, context)
    elif data == "admin_logs":
        await admin_logs(update, context)
    elif data == "admin_backup":
        await admin_backup_menu(update, context)
    elif data == "admin_backup_export":
        await admin_backup_export(update, context)
    elif data == "admin_back":
        await admin_back(update, context)
    else:
        await query.answer("دکمه نامعتبر", show_alert=True)

async def handle_global_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """لغو عملیات از هر جایی"""
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id
    await query.edit_message_text(
        "❌ عملیات لغو شد.",
        reply_markup=main_menu_kb(uid)
    )

# ==================== Conversation Handlers ====================

admin_reply_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(admin_reply_start, pattern="^admin_reply_")],
    states={
        10: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_reply_text_handler)],
    },
    fallbacks=[
        CommandHandler("cancel", lambda u,c: ConversationHandler.END),
        CallbackQueryHandler(handle_global_cancel, pattern="^cancel_action$"),
    ],
)

admin_backup_import_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(admin_backup_import_start, pattern="^admin_backup_import$")],
    states={
        20: [MessageHandler(filters.Document.ALL, admin_backup_import_file)],
        21: [CallbackQueryHandler(admin_backup_import_confirm, pattern="^admin_backup_import_confirm$")],
    },
    fallbacks=[
        CommandHandler("cancel", lambda u,c: ConversationHandler.END),
        CallbackQueryHandler(handle_global_cancel, pattern="^cancel_action$"),
    ],
)

# ===================== Main =====================

def main() -> None:
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", lambda u,c: ConversationHandler.END))

    # Conversation handlers
    app.add_handler(admin_reply_conv)
    app.add_handler(admin_backup_import_conv)

    # Handler برای بلاک کردن کاربر
    app.add_handler(CallbackQueryHandler(admin_block_user, pattern="^admin_block_"))

    # Handler سراسری برای لغو
    app.add_handler(CallbackQueryHandler(handle_global_cancel, pattern="^cancel_action$"))

    # Handler برای کالبک‌های مدیریتی
    app.add_handler(CallbackQueryHandler(handle_admin_callback, pattern="^admin_"))

    # Handler برای ریپلای مستقیم مالک (قبل از handler عمومی)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, owner_direct_reply_handler))

    # Handler برای پیام‌های کاربران (بعد از همه)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_message))

    logger.info("ربات گزارش‌های شهروندان کملوت با موفقیت راه‌اندازی شد.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
