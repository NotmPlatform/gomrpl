import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ChatType
from telegram.error import Forbidden
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# =========================================================
# Настройки
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "0"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "gomarik_bot.db")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "webhook")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
PORT = int(os.getenv("PORT", "8080"))

if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN")
if not ADMIN_GROUP_ID:
    raise RuntimeError("Не задан ADMIN_GROUP_ID")
if not CHANNEL_ID:
    raise RuntimeError("Не задан CHANNEL_ID")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("gomarik_bot")

# =========================================================
# Кнопки и клавиатуры
# =========================================================
BTN_EVENT = "Добавить событие"
BTN_BIZ = "Реклама / Партнёрство"
BTN_FULL = "Полная"
BTN_QUICK = "Быстрая"
BTN_BACK = "Назад"
BTN_CANCEL = "Отмена"
BTN_SEND = "Отправить"
BTN_EDIT = "Изменить"

MENU_TEXTS = {
    BTN_EVENT,
    BTN_BIZ,
    BTN_FULL,
    BTN_QUICK,
    BTN_BACK,
    BTN_CANCEL,
    BTN_SEND,
    BTN_EDIT,
}

MAIN_MENU = ReplyKeyboardMarkup(
    [[KeyboardButton(BTN_EVENT), KeyboardButton(BTN_BIZ)]],
    resize_keyboard=True,
)

EVENT_MODE_MENU = ReplyKeyboardMarkup(
    [[KeyboardButton(BTN_FULL), KeyboardButton(BTN_QUICK)], [KeyboardButton(BTN_BACK)]],
    resize_keyboard=True,
)

CANCEL_MENU = ReplyKeyboardMarkup(
    [[KeyboardButton(BTN_CANCEL)]],
    resize_keyboard=True,
)

COST_MENU = ReplyKeyboardMarkup(
    [[KeyboardButton("Бесплатно"), KeyboardButton("Платно")], [KeyboardButton("Донат"), KeyboardButton("Другое")], [KeyboardButton(BTN_CANCEL)]],
    resize_keyboard=True,
)

AGE_MENU = ReplyKeyboardMarkup(
    [[KeyboardButton("0+"), KeyboardButton("6+"), KeyboardButton("12+")], [KeyboardButton("16+"), KeyboardButton("18+")], [KeyboardButton("Другое")], [KeyboardButton(BTN_CANCEL)]],
    resize_keyboard=True,
)

CATEGORY_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("Концерт"), KeyboardButton("Театр")],
        [KeyboardButton("Детям"), KeyboardButton("Спорт")],
        [KeyboardButton("Еда"), KeyboardButton("Выставка")],
        [KeyboardButton("Обучение"), KeyboardButton("Вечеринка")],
        [KeyboardButton("Город"), KeyboardButton("Другое")],
        [KeyboardButton("Мастер-класс")],
        [KeyboardButton(BTN_CANCEL)],
    ],
    resize_keyboard=True,
)

PREVIEW_MENU = ReplyKeyboardMarkup(
    [[KeyboardButton(BTN_SEND), KeyboardButton(BTN_EDIT)], [KeyboardButton(BTN_CANCEL)]],
    resize_keyboard=True,
)

COST_CHOICES = {"Бесплатно", "Платно", "Донат"}
AGE_CHOICES = {"0+", "6+", "12+", "16+", "18+"}
CATEGORY_CHOICES = {
    "Концерт",
    "Театр",
    "Детям",
    "Спорт",
    "Еда",
    "Выставка",
    "Обучение",
    "Вечеринка",
    "Город",
    "Мастер-класс",
}

MAX_EVENT_TITLE = 120
MAX_EVENT_PLACE = 120
MAX_EVENT_DESC = 700
MAX_EVENT_CONTACT = 200
MAX_BIZ_PROJECT = 120
MAX_BIZ_DESC = 700
MAX_BIZ_NAME = 80
MIN_QUICK_TEXT = 8

# =========================================================
# Состояния
# =========================================================
(
    EVENT_MODE,
    EVENT_TITLE,
    EVENT_DATETIME,
    EVENT_PLACE,
    EVENT_COST,
    EVENT_COST_CUSTOM,
    EVENT_AGE,
    EVENT_AGE_CUSTOM,
    EVENT_DESC,
    EVENT_CATEGORY,
    EVENT_CATEGORY_CUSTOM,
    EVENT_CONTACT,
    EVENT_PHOTO,
    EVENT_PREVIEW,
    QUICK_EVENT,
    BIZ_PROJECT,
    BIZ_DESC,
    BIZ_PHONE,
    BIZ_NAME,
    BIZ_PREVIEW,
) = range(20)

# =========================================================
# База данных
# =========================================================
@contextmanager
def db_conn():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_id TEXT UNIQUE,
                req_type TEXT NOT NULL,
                form_type TEXT,
                status TEXT NOT NULL DEFAULT 'Новая',
                user_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT,
                admin_group_message_id INTEGER,
                channel_message_id INTEGER,
                dialog_open INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,

                event_title TEXT,
                event_datetime TEXT,
                event_place TEXT,
                event_cost TEXT,
                event_age TEXT,
                event_desc TEXT,
                event_category TEXT,
                event_contact TEXT,
                photo_file_id TEXT,
                quick_text TEXT,

                biz_project TEXT,
                biz_desc TEXT,
                biz_phone TEXT,
                biz_name TEXT
            );

            CREATE TABLE IF NOT EXISTS user_dialogs (
                user_id INTEGER PRIMARY KEY,
                request_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )


def set_public_id(conn: sqlite3.Connection, req_id: int) -> str:
    public_id = f"GM_{req_id:05d}"
    conn.execute("UPDATE requests SET public_id = ? WHERE id = ?", (public_id, req_id))
    return public_id


def create_request(req_type: str, form_type: str, user, data: Dict[str, Any]) -> int:
    username = f"@{user.username}" if user.username else ""
    full_name = " ".join(x for x in [user.first_name or "", user.last_name or ""] if x).strip()
    created_at = datetime.now().isoformat(timespec="seconds")

    with db_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO requests (
                req_type, form_type, status, user_id, username, full_name, created_at,
                event_title, event_datetime, event_place, event_cost, event_age, event_desc,
                event_category, event_contact, photo_file_id, quick_text,
                biz_project, biz_desc, biz_phone, biz_name
            ) VALUES (?, ?, 'Новая', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                req_type,
                form_type,
                user.id,
                username,
                full_name,
                created_at,
                data.get("event_title"),
                data.get("event_datetime"),
                data.get("event_place"),
                data.get("event_cost"),
                data.get("event_age"),
                data.get("event_desc"),
                data.get("event_category"),
                data.get("event_contact"),
                data.get("photo_file_id"),
                data.get("quick_text"),
                data.get("biz_project"),
                data.get("biz_desc"),
                data.get("biz_phone"),
                data.get("biz_name"),
            ),
        )
        req_id = cur.lastrowid
        set_public_id(conn, req_id)
        return req_id


def get_request_by_id(req_id: int) -> Optional[sqlite3.Row]:
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM requests WHERE id = ?", (req_id,)).fetchone()
    return row


def get_request_by_admin_message(admin_message_id: int) -> Optional[sqlite3.Row]:
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM requests WHERE admin_group_message_id = ?", (admin_message_id,)
        ).fetchone()
    return row


def update_request(req_id: int, **fields: Any) -> None:
    if not fields:
        return
    keys = list(fields.keys())
    values = [fields[k] for k in keys]
    values.append(req_id)
    sql = f"UPDATE requests SET {', '.join(f'{k} = ?' for k in keys)} WHERE id = ?"
    with db_conn() as conn:
        conn.execute(sql, values)


def set_active_dialog(user_id: int, req_id: int) -> None:
    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO user_dialogs (user_id, request_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET request_id = excluded.request_id, updated_at = excluded.updated_at
            """,
            (user_id, req_id, datetime.now().isoformat(timespec="seconds")),
        )


def clear_active_dialog(user_id: int) -> None:
    with db_conn() as conn:
        conn.execute("DELETE FROM user_dialogs WHERE user_id = ?", (user_id,))


def get_active_dialog(user_id: int) -> Optional[sqlite3.Row]:
    with db_conn() as conn:
        row = conn.execute(
            """
            SELECT r.*
            FROM user_dialogs ud
            JOIN requests r ON r.id = ud.request_id
            WHERE ud.user_id = ? AND r.dialog_open = 1
            """,
            (user_id,),
        ).fetchone()
    return row


def find_duplicate_event(title: str, when_text: str, place: str) -> Optional[str]:
    with db_conn() as conn:
        row = conn.execute(
            """
            SELECT public_id FROM requests
            WHERE req_type = 'event'
              AND event_title = ?
              AND event_datetime = ?
              AND event_place = ?
            ORDER BY id DESC LIMIT 1
            """,
            (title.strip(), when_text.strip(), place.strip()),
        ).fetchone()
    return row["public_id"] if row else None


# =========================================================
# Вспомогательные функции
# =========================================================
def normalize_ru_phone(value: str) -> Optional[str]:
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if len(digits) == 11 and digits.startswith("7"):
        return "+" + digits
    return None


def request_hashtag(public_id: Optional[str]) -> str:
    return f"#{public_id}" if public_id else ""


def user_label(row_or_user) -> str:
    if hasattr(row_or_user, "username"):
        username = f"@{row_or_user.username}" if row_or_user.username else ""
        full_name = " ".join(x for x in [row_or_user.first_name or "", row_or_user.last_name or ""] if x).strip()
    else:
        username = row_or_user["username"] or ""
        full_name = row_or_user["full_name"] or ""
    return username or full_name or "Без имени"


def limit_text(value: str, max_len: int) -> str:
    return (value or "").strip()[:max_len].strip()


def event_preview_text(form: Dict[str, Any]) -> str:
    return (
        "Проверьте заявку:\n\n"
        f"Название: {form.get('title', '')}\n"
        f"Дата и время: {form.get('datetime', '')}\n"
        f"Место: {form.get('place', '')}\n"
        f"Стоимость: {form.get('cost', '')}\n"
        f"Возраст: {form.get('age', '')}\n"
        f"Описание: {form.get('desc', '')}\n"
        f"Категория: {form.get('category', '')}\n"
        f"Контакт / ссылка: {form.get('contact', '')}\n"
        "Афиша: есть"
    )


def biz_preview_text(form: Dict[str, Any]) -> str:
    return (
        "Проверьте заявку:\n\n"
        f"Название проекта: {form.get('project', '')}\n"
        f"Описание: {form.get('desc', '')}\n"
        f"Телефон: {form.get('phone', '')}\n"
        f"Имя: {form.get('name', '')}"
    )


def channel_post_text(row: sqlite3.Row) -> str:
    title = row["event_title"] or "Событие"
    category = row["event_category"] or "Событие"
    lines = [
        f"{category} • {title}",
        f"📅 Дата: {row['event_datetime'] or '—'}",
        f"📍 Место: {row['event_place'] or '—'}",
        f"💳 Стоимость: {row['event_cost'] or '—'}",
        f"🔞 Возраст: {row['event_age'] or '—'}",
        f"✍️ Описание: {row['event_desc'] or '—'}",
        f"📲 Контакт: {row['event_contact'] or '—'}",
    ]
    return "\n".join(lines)


def event_admin_text(row: sqlite3.Row) -> str:
    duplicate = find_duplicate_event(
        row["event_title"] or "",
        row["event_datetime"] or "",
        row["event_place"] or "",
    )
    tag = request_hashtag(row["public_id"])
    duplicate_line = f"\nМетка: Дубль ({duplicate})" if duplicate and duplicate != row["public_id"] else ""
    if row["form_type"] == "quick":
        return (
            f"Заявка {tag}\n"
            f"Тип: Событие\n"
            f"Формат: Быстрая\n"
            f"Статус: {row['status']}{duplicate_line}\n\n"
            f"Текст: {row['quick_text'] or '—'}\n"
            f"Автор: {user_label(row)}\n\n"
            "Чтобы написать заявителю, ответьте reply на это сообщение."
        )
    return (
        f"Заявка {tag}\n"
        f"Тип: Событие\n"
        f"Формат: Полная\n"
        f"Статус: {row['status']}{duplicate_line}\n\n"
        f"Название: {row['event_title'] or '—'}\n"
        f"Дата и время: {row['event_datetime'] or '—'}\n"
        f"Место: {row['event_place'] or '—'}\n"
        f"Стоимость: {row['event_cost'] or '—'}\n"
        f"Возраст: {row['event_age'] or '—'}\n"
        f"Категория: {row['event_category'] or '—'}\n"
        f"Описание: {row['event_desc'] or '—'}\n"
        f"Контакт / ссылка: {row['event_contact'] or '—'}\n"
        f"Автор: {user_label(row)}\n\n"
        "Чтобы написать заявителю, ответьте reply на это сообщение."
    )


def biz_admin_text(row: sqlite3.Row) -> str:
    tag = request_hashtag(row["public_id"])
    return (
        f"Заявка {tag}\n"
        f"Тип: Реклама / Партнёрство\n"
        f"Статус: {row['status']}\n\n"
        f"Название проекта: {row['biz_project'] or '—'}\n"
        f"Описание: {row['biz_desc'] or '—'}\n"
        f"Телефон: {row['biz_phone'] or '—'}\n"
        f"Имя: {row['biz_name'] or '—'}\n"
        f"Автор: {user_label(row)}\n\n"
        "Чтобы написать заявителю, ответьте reply на это сообщение."
    )


async def refresh_admin_card(context: ContextTypes.DEFAULT_TYPE, req_id: int) -> None:
    row = get_request_by_id(req_id)
    if not row or not row["admin_group_message_id"]:
        return

    try:
        if row["req_type"] == "event":
            text = event_admin_text(row)
            keyboard = event_admin_keyboard(req_id)
            if row["photo_file_id"]:
                await context.bot.edit_message_caption(
                    chat_id=ADMIN_GROUP_ID,
                    message_id=row["admin_group_message_id"],
                    caption=text,
                    reply_markup=keyboard,
                )
            else:
                await context.bot.edit_message_text(
                    chat_id=ADMIN_GROUP_ID,
                    message_id=row["admin_group_message_id"],
                    text=text,
                    reply_markup=keyboard,
                )
        else:
            await context.bot.edit_message_text(
                chat_id=ADMIN_GROUP_ID,
                message_id=row["admin_group_message_id"],
                text=biz_admin_text(row),
                reply_markup=biz_admin_keyboard(req_id),
            )
    except Exception as exc:
        logger.warning("Не удалось обновить карточку %s: %s", req_id, exc)


def biz_admin_text(row: sqlite3.Row) -> str:
    return (
        f"Заявка #{row['public_id']}\n"
        f"Тип: Реклама / Партнёрство\n"
        f"Статус: {row['status']}\n\n"
        f"Название проекта: {row['biz_project'] or '—'}\n"
        f"Описание: {row['biz_desc'] or '—'}\n"
        f"Телефон: {row['biz_phone'] or '—'}\n"
        f"Имя: {row['biz_name'] or '—'}\n"
        f"Автор: {user_label(row)}\n\n"
        "Чтобы написать заявителю, ответьте reply на это сообщение."
    )


def event_admin_keyboard(req_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("В канал", callback_data=f"event_publish:{req_id}"), InlineKeyboardButton("На правки", callback_data=f"event_fix:{req_id}")],
            [InlineKeyboardButton("Отклонить", callback_data=f"event_reject:{req_id}"), InlineKeyboardButton("Архив", callback_data=f"event_archive:{req_id}")],
        ]
    )


def biz_admin_keyboard(req_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Принять", callback_data=f"biz_accept:{req_id}"), InlineKeyboardButton("На правки", callback_data=f"biz_fix:{req_id}")],
            [InlineKeyboardButton("Отклонить", callback_data=f"biz_reject:{req_id}"), InlineKeyboardButton("Архив", callback_data=f"biz_archive:{req_id}")],
        ]
    )


async def send_event_to_admin(context: ContextTypes.DEFAULT_TYPE, req_id: int) -> None:
    row = get_request_by_id(req_id)
    if not row:
        return
    text = event_admin_text(row)
    keyboard = event_admin_keyboard(req_id)
    if row["photo_file_id"]:
        sent = await context.bot.send_photo(
            chat_id=ADMIN_GROUP_ID,
            photo=row["photo_file_id"],
            caption=text,
            reply_markup=keyboard,
        )
    else:
        sent = await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=text,
            reply_markup=keyboard,
        )
    update_request(req_id, admin_group_message_id=sent.message_id)


async def send_biz_to_admin(context: ContextTypes.DEFAULT_TYPE, req_id: int) -> None:
    row = get_request_by_id(req_id)
    if not row:
        return
    sent = await context.bot.send_message(
        chat_id=ADMIN_GROUP_ID,
        text=biz_admin_text(row),
        reply_markup=biz_admin_keyboard(req_id),
    )
    update_request(req_id, admin_group_message_id=sent.message_id)


# =========================================================
# Команды и меню
# =========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.effective_message.reply_text(
        "Привет! Это бот GoМарик.\n"
        "Через него можно отправить событие для публикации или оставить заявку на рекламу / партнёрство.\n\n"
        "Выберите нужный раздел ниже.",
        reply_markup=MAIN_MENU,
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.effective_message.reply_text("Действие отменено.", reply_markup=MAIN_MENU)
    return ConversationHandler.END


# =========================================================
# Событие
# =========================================================
async def event_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["event_form"] = {}
    await update.effective_message.reply_text(
        "Как удобнее отправить событие?",
        reply_markup=EVENT_MODE_MENU,
    )
    return EVENT_MODE


async def event_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text
    if text == BTN_FULL:
        context.user_data["event_form"] = {}
        await update.effective_message.reply_text("Введите название события.", reply_markup=CANCEL_MENU)
        return EVENT_TITLE
    if text == BTN_QUICK:
        await update.effective_message.reply_text(
            "Отправьте одним сообщением всё, что есть о событии: название, дату, место, стоимость, описание, контакт и фото. Если понадобится, мы уточним детали позже.",
            reply_markup=CANCEL_MENU,
        )
        return QUICK_EVENT
    await update.effective_message.reply_text("Возвращаю в главное меню.", reply_markup=MAIN_MENU)
    return ConversationHandler.END


async def event_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = limit_text(update.effective_message.text, MAX_EVENT_TITLE)
    if len(value) < 2:
        await update.effective_message.reply_text("Название слишком короткое. Введите название события.")
        return EVENT_TITLE
    context.user_data["event_form"]["title"] = value
    await update.effective_message.reply_text("Укажите дату и время.", reply_markup=CANCEL_MENU)
    return EVENT_DATETIME


async def event_datetime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["event_form"]["datetime"] = update.effective_message.text.strip()
    await update.effective_message.reply_text("Укажите место.", reply_markup=CANCEL_MENU)
    return EVENT_PLACE


async def event_place(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = limit_text(update.effective_message.text, MAX_EVENT_PLACE)
    if len(value) < 2:
        await update.effective_message.reply_text("Укажите место понятнее.")
        return EVENT_PLACE
    context.user_data["event_form"]["place"] = value
    await update.effective_message.reply_text("Укажите стоимость.", reply_markup=COST_MENU)
    return EVENT_COST


async def event_cost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip()
    if text == "Другое":
        await update.effective_message.reply_text("Введите стоимость вручную.", reply_markup=CANCEL_MENU)
        return EVENT_COST_CUSTOM
    if text not in COST_CHOICES:
        await update.effective_message.reply_text("Выберите кнопку или введите через 'Другое'.", reply_markup=COST_MENU)
        return EVENT_COST
    context.user_data["event_form"]["cost"] = text
    await update.effective_message.reply_text("Укажите возраст.", reply_markup=AGE_MENU)
    return EVENT_AGE


async def event_cost_custom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["event_form"]["cost"] = update.effective_message.text.strip()
    await update.effective_message.reply_text("Укажите возраст.", reply_markup=AGE_MENU)
    return EVENT_AGE


async def event_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip()
    if text == "Другое":
        await update.effective_message.reply_text("Введите возраст вручную.", reply_markup=CANCEL_MENU)
        return EVENT_AGE_CUSTOM
    if text not in AGE_CHOICES:
        await update.effective_message.reply_text("Выберите кнопку или введите через 'Другое'.", reply_markup=AGE_MENU)
        return EVENT_AGE
    context.user_data["event_form"]["age"] = text
    await update.effective_message.reply_text("Коротко опишите событие.", reply_markup=CANCEL_MENU)
    return EVENT_DESC


async def event_age_custom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["event_form"]["age"] = update.effective_message.text.strip()
    await update.effective_message.reply_text("Коротко опишите событие.", reply_markup=CANCEL_MENU)
    return EVENT_DESC


async def event_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = limit_text(update.effective_message.text, MAX_EVENT_DESC)
    if len(value) < 10:
        await update.effective_message.reply_text("Описание слишком короткое. Добавьте чуть больше деталей.")
        return EVENT_DESC
    context.user_data["event_form"]["desc"] = value
    await update.effective_message.reply_text("Выберите категорию.", reply_markup=CATEGORY_MENU)
    return EVENT_CATEGORY


async def event_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip()
    if text == "Другое":
        await update.effective_message.reply_text("Введите категорию вручную.", reply_markup=CANCEL_MENU)
        return EVENT_CATEGORY_CUSTOM
    if text not in CATEGORY_CHOICES:
        await update.effective_message.reply_text("Выберите категорию кнопкой или через 'Другое'.", reply_markup=CATEGORY_MENU)
        return EVENT_CATEGORY
    context.user_data["event_form"]["category"] = text
    await update.effective_message.reply_text(
        "Укажите ссылку на событие или контактный номер организатора, чтобы люди могли связаться с вами для уточнения информации. Если ничего нет — напишите 'нет'.",
        reply_markup=CANCEL_MENU,
    )
    return EVENT_CONTACT


async def event_category_custom(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["event_form"]["category"] = limit_text(update.effective_message.text, 60)
    await update.effective_message.reply_text(
        "Укажите ссылку на событие или контактный номер организатора, чтобы люди могли связаться с вами для уточнения информации. Если ничего нет — напишите 'нет'.",
        reply_markup=CANCEL_MENU,
    )
    return EVENT_CONTACT


async def event_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["event_form"]["contact"] = limit_text(update.effective_message.text, MAX_EVENT_CONTACT)
    await update.effective_message.reply_text("Отправьте фото или афишу одним сообщением.", reply_markup=CANCEL_MENU)
    return EVENT_PHOTO


async def event_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    if not message.photo:
        await message.reply_text("Нужно отправить именно фото или афишу изображением.")
        return EVENT_PHOTO
    context.user_data["event_form"]["photo_file_id"] = message.photo[-1].file_id
    await message.reply_text(event_preview_text(context.user_data["event_form"]), reply_markup=PREVIEW_MENU)
    return EVENT_PREVIEW


async def event_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip()
    form = context.user_data.get("event_form", {})
    if text == BTN_SEND:
        req_id = create_request(
            req_type="event",
            form_type="full",
            user=update.effective_user,
            data={
                "event_title": form.get("title"),
                "event_datetime": form.get("datetime"),
                "event_place": form.get("place"),
                "event_cost": form.get("cost"),
                "event_age": form.get("age"),
                "event_desc": form.get("desc"),
                "event_category": form.get("category"),
                "event_contact": form.get("contact"),
                "photo_file_id": form.get("photo_file_id"),
            },
        )
        await send_event_to_admin(context, req_id)
        context.user_data.clear()
        await update.effective_message.reply_text(
            "Спасибо! Ваша заявка принята и отправлена на модерацию.",
            reply_markup=MAIN_MENU,
        )
        return ConversationHandler.END
    if text == BTN_EDIT:
        context.user_data["event_form"] = {}
        await update.effective_message.reply_text("Давайте заполним заново. Введите название события.")
        return EVENT_TITLE
    await update.effective_message.reply_text("Заявка отменена.", reply_markup=MAIN_MENU)
    context.user_data.clear()
    return ConversationHandler.END


async def quick_event(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.effective_message
    quick_text = limit_text((message.caption or message.text or ""), MAX_EVENT_DESC)
    if not quick_text and not message.photo:
        await message.reply_text("Отправьте хотя бы текст или фото.")
        return QUICK_EVENT
    if len(quick_text) < MIN_QUICK_TEXT and not message.photo:
        await message.reply_text("Добавьте чуть больше информации о событии.")
        return QUICK_EVENT
    req_id = create_request(
        req_type="event",
        form_type="quick",
        user=update.effective_user,
        data={
            "quick_text": quick_text,
            "photo_file_id": message.photo[-1].file_id if message.photo else None,
        },
    )
    await send_event_to_admin(context, req_id)
    context.user_data.clear()
    await message.reply_text(
        "Спасибо! Заявка принята. Если потребуется, мы уточним детали.",
        reply_markup=MAIN_MENU,
    )
    return ConversationHandler.END


# =========================================================
# Реклама / партнёрство
# =========================================================
async def biz_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["biz_form"] = {}
    await update.effective_message.reply_text(
        "Оставьте заявку на рекламу или партнёрство. Я задам 4 коротких вопроса.\n\n"
        "Первый вопрос: как называется ваш проект?",
        reply_markup=CANCEL_MENU,
    )
    return BIZ_PROJECT


async def biz_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = limit_text(update.effective_message.text, MAX_BIZ_PROJECT)
    if len(value) < 2:
        await update.effective_message.reply_text("Название проекта слишком короткое. Укажите его ещё раз.")
        return BIZ_PROJECT
    context.user_data["biz_form"]["project"] = value
    await update.effective_message.reply_text("Кратко опишите, что вы хотите разместить или предложить.", reply_markup=CANCEL_MENU)
    return BIZ_DESC


async def biz_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = limit_text(update.effective_message.text, MAX_BIZ_DESC)
    if len(value) < 10:
        await update.effective_message.reply_text("Опишите заявку чуть подробнее.")
        return BIZ_DESC
    context.user_data["biz_form"]["desc"] = value
    await update.effective_message.reply_text("Укажите контактный номер телефона в формате +7XXXXXXXXXX.", reply_markup=CANCEL_MENU)
    return BIZ_PHONE


async def biz_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw_phone = update.effective_message.text.strip()
    normalized = normalize_ru_phone(raw_phone)
    if not normalized:
        await update.effective_message.reply_text(
            "Номер указан неверно. Введите телефон в формате +7XXXXXXXXXX.",
            reply_markup=CANCEL_MENU,
        )
        return BIZ_PHONE
    context.user_data["biz_form"]["phone"] = normalized
    await update.effective_message.reply_text("Как вас зовут?", reply_markup=CANCEL_MENU)
    return BIZ_NAME


async def biz_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = limit_text(update.effective_message.text, MAX_BIZ_NAME)
    if len(value) < 2:
        await update.effective_message.reply_text("Укажите имя корректно.")
        return BIZ_NAME
    context.user_data["biz_form"]["name"] = value
    await update.effective_message.reply_text(biz_preview_text(context.user_data["biz_form"]), reply_markup=PREVIEW_MENU)
    return BIZ_PREVIEW


async def biz_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip()
    form = context.user_data.get("biz_form", {})
    if text == BTN_SEND:
        req_id = create_request(
            req_type="biz",
            form_type="simple",
            user=update.effective_user,
            data={
                "biz_project": form.get("project"),
                "biz_desc": form.get("desc"),
                "biz_phone": form.get("phone"),
                "biz_name": form.get("name"),
            },
        )
        await send_biz_to_admin(context, req_id)
        context.user_data.clear()
        await update.effective_message.reply_text(
            "Спасибо! Ваша заявка принята и отправлена менеджеру.",
            reply_markup=MAIN_MENU,
        )
        return ConversationHandler.END
    if text == BTN_EDIT:
        context.user_data["biz_form"] = {}
        await update.effective_message.reply_text("Давайте заполним заново. Как называется ваш проект?", reply_markup=CANCEL_MENU)
        return BIZ_PROJECT
    context.user_data.clear()
    await update.effective_message.reply_text("Заявка отменена.", reply_markup=MAIN_MENU)
    return ConversationHandler.END


# =========================================================
# Reply-мост
# =========================================================
async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or message.chat_id != ADMIN_GROUP_ID or message.from_user.is_bot:
        return
    if not message.reply_to_message:
        return

    row = get_request_by_admin_message(message.reply_to_message.message_id)
    if not row or not row["dialog_open"]:
        return

    try:
        await context.bot.copy_message(
            chat_id=row["user_id"],
            from_chat_id=message.chat_id,
            message_id=message.message_id,
        )
        set_active_dialog(row["user_id"], row["id"])

        summary = (message.text or message.caption or "").strip()
        note = "Менеджер → заявителю"
        if summary:
            note += f"\n{summary[:800]}"
        note += f"\n{request_hashtag(row['public_id'])}"
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            note,
            reply_to_message_id=row["admin_group_message_id"],
        )
    except Forbidden:
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Не удалось доставить сообщение по заявке {request_hashtag(row['public_id'])}: пользователь заблокировал бота.",
            reply_to_message_id=row["admin_group_message_id"],
        )


async def handle_user_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or update.effective_chat.type != ChatType.PRIVATE:
        return

    text = (message.text or message.caption or "").strip()
    if text in MENU_TEXTS:
        return

    row = get_active_dialog(update.effective_user.id)
    if not row:
        await message.reply_text("Выберите нужный раздел ниже.", reply_markup=MAIN_MENU)
        return

    try:
        await context.bot.copy_message(
            chat_id=ADMIN_GROUP_ID,
            from_chat_id=message.chat_id,
            message_id=message.message_id,
            reply_to_message_id=row["admin_group_message_id"],
        )
        await context.bot.send_message(
            ADMIN_GROUP_ID,
            f"Заявитель → менеджеру\n{request_hashtag(row['public_id'])}",
            reply_to_message_id=row["admin_group_message_id"],
        )
    except Exception as exc:
        logger.exception("Не удалось переслать ответ пользователя: %s", exc)
        await message.reply_text("Не удалось отправить сообщение менеджеру. Попробуйте позже.")


# =========================================================
# Модерация
# =========================================================
async def moderate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    try:
        action, req_id_raw = data.split(":", 1)
        req_id = int(req_id_raw)
    except ValueError:
        return

    row = get_request_by_id(req_id)
    if not row:
        await query.message.reply_text("Заявка не найдена.")
        return

    tag = request_hashtag(row["public_id"])

    if action == "event_publish":
        if row["req_type"] != "event":
            return
        text = channel_post_text(row)
        if row["photo_file_id"]:
            sent = await context.bot.send_photo(CHANNEL_ID, row["photo_file_id"], caption=text)
        else:
            sent = await context.bot.send_message(CHANNEL_ID, text)
        update_request(req_id, status="Опубликована", dialog_open=1, channel_message_id=sent.message_id)
        set_active_dialog(row["user_id"], req_id)
        await refresh_admin_card(context, req_id)
        await query.message.reply_text(
            f"Опубликовано. {tag}",
            reply_to_message_id=row["admin_group_message_id"],
        )
        try:
            await context.bot.send_message(
                row["user_id"],
                "Ваша заявка опубликована в канале. Спасибо!",
            )
        except Forbidden:
            pass
        return

    if action == "event_fix":
        update_request(req_id, status="На правки", dialog_open=1)
        set_active_dialog(row["user_id"], req_id)
        await refresh_admin_card(context, req_id)
        await query.message.reply_text(
            f"Статус изменён на «На правки». Ответьте reply на карточку, чтобы написать заявителю.\n{tag}",
            reply_to_message_id=row["admin_group_message_id"],
        )
        return

    if action == "biz_fix":
        update_request(req_id, status="На правки", dialog_open=1)
        set_active_dialog(row["user_id"], req_id)
        await refresh_admin_card(context, req_id)
        await query.message.reply_text(
            f"Статус изменён на «На правки». Ответьте reply на карточку, чтобы написать заявителю.\n{tag}",
            reply_to_message_id=row["admin_group_message_id"],
        )
        return

    if action == "biz_accept":
        update_request(req_id, status="В работе", dialog_open=1)
        set_active_dialog(row["user_id"], req_id)
        await refresh_admin_card(context, req_id)
        await query.message.reply_text(
            f"Заявка принята в работу. {tag}",
            reply_to_message_id=row["admin_group_message_id"],
        )
        try:
            await context.bot.send_message(
                row["user_id"],
                "Ваша заявка принята в работу. Если потребуется, менеджер свяжется с вами здесь.",
            )
        except Forbidden:
            pass
        return

    if action in {"event_reject", "biz_reject"}:
        update_request(req_id, status="Отклонена", dialog_open=0)
        clear_active_dialog(row["user_id"])
        await refresh_admin_card(context, req_id)
        try:
            await context.bot.send_message(row["user_id"], "Ваша заявка закрыта менеджером.")
        except Forbidden:
            pass
        await query.message.reply_text(
            f"Заявка отклонена. {tag}",
            reply_to_message_id=row["admin_group_message_id"],
        )
        return

    if action in {"event_archive", "biz_archive"}:
        update_request(req_id, status="Архив", dialog_open=0)
        clear_active_dialog(row["user_id"])
        await refresh_admin_card(context, req_id)
        await query.message.reply_text(
            f"Заявка перенесена в архив. {tag}",
            reply_to_message_id=row["admin_group_message_id"],
        )
        return


# =========================================================
# Прочее
# =========================================================
async def private_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    await update.effective_message.reply_text("Выберите нужный раздел ниже.", reply_markup=MAIN_MENU)


# =========================================================
# main
# =========================================================
def build_application() -> Application:
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.TEXT & filters.Regex(f"^{BTN_EVENT}$"), event_entry),
            MessageHandler(filters.TEXT & filters.Regex(f"^{BTN_BIZ}$"), biz_entry),
        ],
        states={
            EVENT_MODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_mode)],
            EVENT_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_title)],
            EVENT_DATETIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_datetime)],
            EVENT_PLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_place)],
            EVENT_COST: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_cost)],
            EVENT_COST_CUSTOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_cost_custom)],
            EVENT_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_age)],
            EVENT_AGE_CUSTOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_age_custom)],
            EVENT_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_desc)],
            EVENT_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_category)],
            EVENT_CATEGORY_CUSTOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_category_custom)],
            EVENT_CONTACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_contact)],
            EVENT_PHOTO: [MessageHandler((filters.PHOTO | filters.TEXT) & ~filters.COMMAND, event_photo)],
            EVENT_PREVIEW: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_preview)],
            QUICK_EVENT: [MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, quick_event)],
            BIZ_PROJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, biz_project)],
            BIZ_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, biz_desc)],
            BIZ_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, biz_phone)],
            BIZ_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, biz_name)],
            BIZ_PREVIEW: [MessageHandler(filters.TEXT & ~filters.COMMAND, biz_preview)],
        },
        fallbacks=[
            CommandHandler("start", start),
            MessageHandler(filters.TEXT & filters.Regex(f"^{BTN_CANCEL}$"), cancel),
            MessageHandler(filters.TEXT & filters.Regex(f"^{BTN_BACK}$"), start),
            MessageHandler(filters.TEXT & filters.Regex(f"^{BTN_EVENT}$"), event_entry),
            MessageHandler(filters.TEXT & filters.Regex(f"^{BTN_BIZ}$"), biz_entry),
        ],
        per_chat=True,
        per_user=True,
        per_message=False,
        allow_reentry=True,
    )

    application.add_handler(conv)
    application.add_handler(CallbackQueryHandler(moderate_callback))
    application.add_handler(
        MessageHandler(
            filters.Chat(chat_id=ADMIN_GROUP_ID)
            & (filters.TEXT | filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.AUDIO)
            & ~filters.COMMAND,
            handle_admin_reply,
        )
    )
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE
            & (filters.TEXT | filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.AUDIO)
            & ~filters.COMMAND,
            handle_user_reply,
        )
    )
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.ALL, private_unknown))
    return application


def main() -> None:
    init_db()
    application = build_application()
    if WEBHOOK_URL:
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=f"{WEBHOOK_URL.rstrip('/')}/{WEBHOOK_PATH}",
            url_path=WEBHOOK_PATH,
            secret_token=WEBHOOK_SECRET or None,
            drop_pending_updates=True,
        )
    else:
        application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()