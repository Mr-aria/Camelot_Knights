import asyncio
import json
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ContentType
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile
)
from aiogram.fsm.context import FSMContext

from config import BOT_TOKEN, OWNER_ID
from database import (
    init_db, upsert_user, add_log, add_report, is_bot_enabled,
    set_bot_enabled, is_user_blocked, block_user, unblock_user,
    get_recent_logs, export_backup, restore_backup, get_user
)
from states import AdminReplyState, RestoreState

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

def admin_panel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🟢 روشن کردن ربات", callback_data="bot_on"),
            InlineKeyboardButton(text="🔴 خاموش کردن ربات", callback_data="bot_off"),
        ],
        [
            InlineKeyboardButton(text="📜 مشاهده لاگ‌ها", callback_data="view_logs"),
        ],
        [
            InlineKeyboardButton(text="📦 پشتیبان گیری", callback_data="backup_data"),
            InlineKeyboardButton(text="♻️ بازیابی", callback_data="restore_data"),
        ]
    ])

def report_action_keyboard(user_id: int, blocked: bool = False):
    buttons = [
        [InlineKeyboardButton(text="✉️ پاسخ", callback_data=f"reply:{user_id}")]
    ]
    if blocked:
        buttons.append([InlineKeyboardButton(text="✅ آنبلاک کاربر", callback_data=f"unblock:{user_id}")])
    else:
        buttons.append([InlineKeyboardButton(text="⛔ بلاک کاربر", callback_data=f"block:{user_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def format_user_info(message: Message):
    user = message.from_user
    username = f"@{user.username}" if user.username else "ندارد"
    full_name = user.full_name
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"📨 گزارش جدید\n\n"
        f"🆔 آیدی عددی: {user.id}\n"
        f"👤 یوزرنیم: {username}\n"
        f"📛 نام: {full_name}\n"
        f"🕒 تاریخ: {date_str}\n\n"
    )

async def send_to_owner_with_meta(message: Message):
    header = format_user_info(message)
    blocked = is_user_blocked(message.from_user.id)

    if message.content_type == ContentType.TEXT:
        text = header + f"💬 پیام کاربر:\n{message.text}"
        await bot.send_message(
            OWNER_ID,
            text,
            reply_markup=report_action_keyboard(message.from_user.id, blocked)
        )
        add_report(message.from_user.id, "text", content=message.text)

    elif message.content_type == ContentType.PHOTO:
        caption = message.caption or ""
        text = header + f"🖼 عکس کاربر\n\n📝 کپشن:\n{caption if caption else 'ندارد'}"
        await bot.send_photo(
            OWNER_ID,
            photo=message.photo[-1].file_id,
            caption=text,
            reply_markup=report_action_keyboard(message.from_user.id, blocked)
        )
        add_report(message.from_user.id, "photo", content=caption, file_id=message.photo[-1].file_id)

    elif message.content_type == ContentType.VIDEO:
        caption = message.caption or ""
        text = header + f"🎬 ویدیو کاربر\n\n📝 کپشن:\n{caption if caption else 'ندارد'}"
        await bot.send_video(
            OWNER_ID,
            video=message.video.file_id,
            caption=text,
            reply_markup=report_action_keyboard(message.from_user.id, blocked)
        )
        add_report(message.from_user.id, "video", content=caption, file_id=message.video.file_id)

    elif message.content_type == ContentType.VOICE:
        text = header + "🎤 ویس کاربر"
        await bot.send_voice(
            OWNER_ID,
            voice=message.voice.file_id,
            caption=text,
            reply_markup=report_action_keyboard(message.from_user.id, blocked)
        )
        add_report(message.from_user.id, "voice", file_id=message.voice.file_id)

    elif message.content_type == ContentType.DOCUMENT:
        caption = message.caption or ""
        text = header + f"📄 فایل کاربر\n\n📝 کپشن:\n{caption if caption else 'ندارد'}"
        await bot.send_document(
            OWNER_ID,
            document=message.document.file_id,
            caption=text,
            reply_markup=report_action_keyboard(message.from_user.id, blocked)
        )
        add_report(message.from_user.id, "document", content=caption, file_id=message.document.file_id)

    else:
        text = header + f"⚠️ یک پیام از نوع {message.content_type} ارسال شد که پشتیبانی محدود دارد."
        await bot.send_message(
            OWNER_ID,
            text,
            reply_markup=report_action_keyboard(message.from_user.id, blocked)
        )
        add_report(message.from_user.id, message.content_type, content="unsupported preview")

@dp.message(Command("start"))
async def start_handler(message: Message):
    upsert_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name
    )
    add_log("start", message.from_user.id, "User started bot")

    if not is_bot_enabled() and message.from_user.id != OWNER_ID:
        await message.answer("ربات خاموشه.")
        return

    if message.from_user.id == OWNER_ID:
        await message.answer(
            "سلام مالک. پنل مدیریت:",
            reply_markup=admin_panel_keyboard()
        )
    else:
        await message.answer("درود ای شهروند کملوت، چه کمکی از دستم برمی آید؟ آیا گزارشی داری؟")

@dp.message(Command("panel"))
async def panel_handler(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    add_log("open_panel", message.from_user.id, "Owner opened panel")
    await message.answer("پنل مدیریت:", reply_markup=admin_panel_keyboard())

@dp.callback_query(F.data == "bot_on")
async def bot_on_handler(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return
    set_bot_enabled(True)
    add_log("bot_on", callback.from_user.id, "Bot turned on")
    await callback.message.edit_text("✅ ربات روشن شد.", reply_markup=admin_panel_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "bot_off")
async def bot_off_handler(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return
    set_bot_enabled(False)
    add_log("bot_off", callback.from_user.id, "Bot turned off")
    await callback.message.edit_text("🔴 ربات خاموش شد.", reply_markup=admin_panel_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "view_logs")
async def logs_handler(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return
    logs = get_recent_logs(20)
    if not logs:
        text = "هیچ لاگی ثبت نشده."
    else:
        parts = []
        for log in logs:
            parts.append(
                f"#{log['id']} | {log['action']}\n"
                f"کاربر: {log['user_id']}\n"
                f"جزئیات: {log['details']}\n"
                f"تاریخ: {log['created_at']}"
            )
        text = "📜 20 لاگ آخر:\n\n" + "\n\n".join(parts)

    add_log("view_logs", callback.from_user.id, "Viewed recent logs")
    await callback.message.answer(text)
    await callback.answer()

@dp.callback_query(F.data == "backup_data")
async def backup_handler(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return

    data = export_backup()
    json_data = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    file = BufferedInputFile(json_data, filename="backup.json")

    add_log("backup", callback.from_user.id, "Database backup exported")
    await callback.message.answer_document(file, caption="✅ فایل پشتیبان دیتابیس")
    await callback.answer()

@dp.callback_query(F.data == "restore_data")
async def restore_request_handler(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        return
    await state.set_state(RestoreState.waiting_for_backup_file)
    add_log("restore_request", callback.from_user.id, "Owner requested restore")
    await callback.message.answer("فایل JSON بکاپ را ارسال کن.")
    await callback.answer()

@dp.message(RestoreState.waiting_for_backup_file, F.document)
async def restore_handler(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return

    try:
        file = await bot.get_file(message.document.file_id)
        file_data = await bot.download_file(file.file_path)
        content = file_data.read().decode("utf-8")
        data = json.loads(content)

        restore_backup(data)
        add_log("restore", message.from_user.id, "Database restored from backup")
        await message.answer("✅ بازیابی با موفقیت انجام شد.")
    except Exception as e:
        add_log("restore_failed", message.from_user.id, str(e))
        await message.answer(f"❌ خطا در بازیابی:\n{e}")

    await state.clear()

@dp.callback_query(F.data.startswith("reply:"))
async def reply_callback_handler(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        return
    user_id = int(callback.data.split(":")[1])
    await state.set_state(AdminReplyState.waiting_for_reply)
    await state.update_data(target_user_id=user_id)
    add_log("reply_mode", callback.from_user.id, f"Reply mode for user {user_id}")
    await callback.message.answer(f"متن پاسخ به کاربر {user_id} را ارسال کن.")
    await callback.answer()

@dp.message(AdminReplyState.waiting_for_reply)
async def send_admin_reply(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return

    data = await state.get_data()
    user_id = data.get("target_user_id")

    try:
        await bot.send_message(user_id, f"📩 پاسخ پشتیبانی:\n\n{message.text}")
        add_log("admin_reply", message.from_user.id, f"Reply sent to user {user_id}: {message.text}")
        await message.answer("✅ پاسخ ارسال شد.")
    except Exception as e:
        add_log("admin_reply_failed", message.from_user.id, str(e))
        await message.answer(f"❌ ارسال پاسخ ناموفق بود:\n{e}")

    await state.clear()

@dp.callback_query(F.data.startswith("block:"))
async def block_handler(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return
    user_id = int(callback.data.split(":")[1])
    block_user(user_id)
    add_log("block_user", callback.from_user.id, f"Blocked user {user_id}")
    await callback.message.answer(f"⛔ کاربر {user_id} بلاک شد.")
    await callback.answer("کاربر بلاک شد.")

@dp.callback_query(F.data.startswith("unblock:"))
async def unblock_handler(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return
    user_id = int(callback.data.split(":")[1])
    unblock_user(user_id)
    add_log("unblock_user", callback.from_user.id, f"Unblocked user {user_id}")
    await callback.message.answer(f"✅ کاربر {user_id} آنبلاک شد.")
    await callback.answer("کاربر آنبلاک شد.")

@dp.message()
async def all_messages_handler(message: Message):
    upsert_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name
    )

    if not is_bot_enabled() and message.from_user.id != OWNER_ID:
        add_log("blocked_by_bot_off", message.from_user.id, "Attempt while bot is off")
        await message.answer("ربات خاموشه.")
        return

    if message.from_user.id == OWNER_ID:
        return

    if is_user_blocked(message.from_user.id):
        add_log("blocked_user_message", message.from_user.id, "Blocked user tried to send message")
        return

    await send_to_owner_with_meta(message)
    add_log("user_report", message.from_user.id, f"Sent {message.content_type} report to owner")
    await message.answer("با تشکر از شما شهروندگرامی.")

async def main():
    init_db()
    add_log("bot_started", OWNER_ID, "Bot process started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
