import asyncio
import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Sequence
from zoneinfo import ZoneInfo

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatType, ParseMode
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
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "webhook")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
PORT = int(os.getenv("PORT", "8080"))
ADMIN_USER_IDS_RAW = os.getenv("ADMIN_USER_IDS", "")
ADMIN_USER_IDS = {
    int(x.strip())
    for x in ADMIN_USER_IDS_RAW.split(",")
    if x.strip().isdigit()
}

if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN")
if not ADMIN_GROUP_ID:
    raise RuntimeError("Не задан ADMIN_GROUP_ID")
if not CHANNEL_ID:
    raise RuntimeError("Не задан CHANNEL_ID")

TZ = ZoneInfo(TIMEZONE)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("gomarik_bot")

# =========================================================
# Константы UI
# =========================================================
BTN_EVENT = "Событие"
BTN_AD = "Реклама"
BTN_PARTNERS = "Партнёры"
BTN_FULL = "Полная"
BTN_QUICK = "Быстрая"
BTN_BACK = "Назад"
BTN_CANCEL = "Отмена"
BTN_SEND = "Отправить"
BTN_EDIT = "Изменить"

MAIN_MENU = ReplyKeyboardMarkup(
    [[KeyboardButton(BTN_EVENT), KeyboardButton(BTN_AD)], [KeyboardButton(BTN_PARTNERS)]],
    resize_keyboard=True,
)
EVENT_MODE_MENU = ReplyKeyboardMarkup(
    [[KeyboardButton(BTN_FULL), KeyboardButton(BTN_QUICK)], [KeyboardButton(BTN_BACK)]],
    resize_keyboard=True,
)
COST_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("Бесплатно"), KeyboardButton("Платно")],
        [KeyboardButton("Донат"), KeyboardButton("Другое")],
        [KeyboardButton(BTN_CANCEL)],
    ],
    resize_keyboard=True,
)
AGE_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("0+"), KeyboardButton("6+"), KeyboardButton("12+")],
        [KeyboardButton("16+"), KeyboardButton("18+")],
        [KeyboardButton("Другое")],
        [KeyboardButton(BTN_CANCEL)],
    ],
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
AD_FORMAT_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("Пост"), KeyboardButton("Интеграция")],
        [KeyboardButton("Подборка"), KeyboardButton("Закреп")],
        [KeyboardButton("Серия"), KeyboardButton("Другое")],
        [KeyboardButton(BTN_BACK), KeyboardButton(BTN_CANCEL)],
    ],
    resize_keyboard=True,
)

FINAL_STATUSES = {"Опубликована", "Отклонена", "Архив"}

# =========================================================
# Состояния ConversationHandler
# =========================================================
(
    EVENT_MODE,
    EV_TITLE,
    EV_DATETIME,
    EV_PLACE,
    EV_COST,
    EV_COST_OTHER,
    EV_AGE,
    EV_AGE_OTHER,
    EV_DESCRIPTION,
    EV_CATEGORY,
    EV_CATEGORY_OTHER,
    EV_CONTACT,
    EV_PHOTO,
    EV_PREVIEW,
    QUICK_EVENT,
    AD_INFO,
    AD_ITEM,
    AD_DATE,
    AD_CONTACT,
    AD_COMMENT,
    AD_MEDIA,
    PARTNER_NAME,
    PARTNER_OFFER,
    PARTNER_IDEA,
    PARTNER_CONTACT,
    PARTNER_LINK,
    PARTNER_COMMENT,
) = range(27)

# =========================================================
# База данных
# =========================================================

def init_db() -> None:
    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS requests (
                request_id TEXT PRIMARY KEY,
                request_type TEXT NOT NULL,
                submit_format TEXT,
                status TEXT NOT NULL,
                tags TEXT DEFAULT '',
                user_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT,
                title TEXT,
                event_dt TEXT,
                place_text TEXT,
                cost_text TEXT,
                age_text TEXT,
                description_text TEXT,
                category_text TEXT,
                contact_text TEXT,
                photo_file_id TEXT,
                raw_text TEXT,
                ad_item TEXT,
                ad_format TEXT,
                ad_date TEXT,
                ad_contact TEXT,
                ad_comment TEXT,
                ad_media_file_id TEXT,
                partner_name TEXT,
                partner_offer TEXT,
                partner_idea TEXT,
                partner_contact TEXT,
                partner_link TEXT,
                partner_comment TEXT,
                admin_group_chat_id INTEGER,
                admin_group_message_id INTEGER,
                channel_message_id INTEGER,
                scheduled_at TEXT,
                pending_admin_action TEXT,
                dialog_open INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_requests_user_open ON requests(user_id, dialog_open)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_requests_admin_message ON requests(admin_group_message_id)"
        )
        conn.commit()


@contextmanager
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def generate_request_id(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT request_id FROM requests ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    last_num = 0
    if row and row["request_id"]:
        m = re.search(r"(\d+)$", row["request_id"])
        if m:
            last_num = int(m.group(1))
    return f"GM-{last_num + 1:05d}"


def create_request(payload: Dict[str, Any]) -> str:
    with db() as conn:
        request_id = generate_request_id(conn)
        created_at = now_iso()
        payload = {**payload}
        payload.setdefault("submit_format", "")
        payload.setdefault("tags", "")
        payload.setdefault("username", "")
        payload.setdefault("full_name", "")
        payload.setdefault("status", "Новая")
        payload.setdefault("created_at", created_at)
        payload.setdefault("updated_at", created_at)
        payload.setdefault("dialog_open", 0)
        columns = ["request_id"] + list(payload.keys())
        values = [request_id] + [payload[k] for k in payload.keys()]
        placeholders = ", ".join(["?"] * len(values))
        conn.execute(
            f"INSERT INTO requests ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )
        return request_id


def get_request(request_id: str) -> Optional[sqlite3.Row]:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        return row


def get_request_by_admin_message(message_id: int) -> Optional[sqlite3.Row]:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM requests WHERE admin_group_message_id = ?",
            (message_id,),
        ).fetchone()
        return row


def get_open_request_for_user(user_id: int) -> Optional[sqlite3.Row]:
    with db() as conn:
        row = conn.execute(
            """
            SELECT * FROM requests
            WHERE user_id = ? AND dialog_open = 1
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        return row


def update_request(request_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now_iso()
    assignments = ", ".join([f"{k} = ?" for k in fields.keys()])
    values = list(fields.values()) + [request_id]
    with db() as conn:
        conn.execute(
            f"UPDATE requests SET {assignments} WHERE request_id = ?",
            values,
        )


def add_tag(request_id: str, tag: str) -> None:
    row = get_request(request_id)
    if not row:
        return
    tags = {t for t in (row["tags"] or "").split(",") if t}
    tags.add(tag)
    update_request(request_id, tags=",".join(sorted(tags)))


def build_duplicate_tag(title: str, event_dt: str, place_text: str) -> Optional[str]:
    with db() as conn:
        row = conn.execute(
            """
            SELECT request_id FROM requests
            WHERE request_type = 'event'
              AND title = ?
              AND event_dt = ?
              AND place_text = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (title, event_dt, place_text),
        ).fetchone()
        if row:
            return row["request_id"]
    return None


def list_scheduled_requests() -> Sequence[sqlite3.Row]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM requests WHERE scheduled_at IS NOT NULL AND status = 'В план'"
        ).fetchall()
        return rows


def autoarchive_expired_requests() -> int:
    now = datetime.now(TZ)
    changed = 0
    with db() as conn:
        rows = conn.execute(
            "SELECT request_id, event_dt, status FROM requests WHERE request_type = 'event' AND event_dt IS NOT NULL"
        ).fetchall()
        for row in rows:
            try:
                event_dt = datetime.fromisoformat(row["event_dt"])
            except Exception:
                continue
            if event_dt.tzinfo is None:
                event_dt = event_dt.replace(tzinfo=TZ)
            if event_dt < now and row["status"] not in FINAL_STATUSES:
                conn.execute(
                    "UPDATE requests SET status = ?, dialog_open = 0, updated_at = ? WHERE request_id = ?",
                    ("Архив", now_iso(), row["request_id"]),
                )
                changed += 1
    return changed


# =========================================================
# Вспомогательные функции
# =========================================================

def sanitize(text: Optional[str]) -> str:
    return (text or "").strip()


def is_private(update: Update) -> bool:
    return update.effective_chat is not None and update.effective_chat.type == ChatType.PRIVATE


def is_admin_group(update: Update) -> bool:
    return update.effective_chat is not None and update.effective_chat.id == ADMIN_GROUP_ID


def is_authorized_admin(update: Update) -> bool:
    if not is_admin_group(update):
        return False
    if not ADMIN_USER_IDS:
        return True
    user = update.effective_user
    return bool(user and user.id in ADMIN_USER_IDS)


def user_full_name(update: Update) -> str:
    user = update.effective_user
    if not user:
        return ""
    return " ".join(part for part in [user.first_name, user.last_name] if part).strip()


def event_preview_text(data: Dict[str, Any]) -> str:
    lines = [
        "Проверьте заявку:",
        "",
        f"Название: {data.get('title', '')}",
        f"Дата и время: {data.get('event_dt_text', '')}",
        f"Место: {data.get('place_text', '')}",
        f"Стоимость: {data.get('cost_text', '')}",
        f"Возраст: {data.get('age_text', '')}",
        f"Описание: {data.get('description_text', '')}",
        f"Категория: {data.get('category_text', '')}",
        f"Контакт / ссылка: {data.get('contact_text', '') or 'нет'}",
        f"Афиша: {'есть' if data.get('photo_file_id') else 'нет'}",
    ]
    return "\n".join(lines)


def parse_event_datetime(value: str) -> tuple[datetime, str]:
    value = sanitize(value)
    patterns = ["%d.%m.%Y %H:%M", "%d.%m %H:%M"]
    last_error: Optional[Exception] = None
    for pattern in patterns:
        try:
            dt = datetime.strptime(value, pattern)
            if pattern == "%d.%m %H:%M":
                now = datetime.now(TZ)
                dt = dt.replace(year=now.year)
                if dt.replace(tzinfo=TZ) < now - timedelta(hours=1):
                    dt = dt.replace(year=now.year + 1)
            dt = dt.replace(tzinfo=TZ)
            pretty = dt.strftime("%d.%m.%Y %H:%M")
            return dt, pretty
        except Exception as exc:
            last_error = exc
    raise ValueError("Используйте формат ДД.ММ.ГГГГ ЧЧ:ММ или ДД.ММ ЧЧ:ММ") from last_error


def parse_schedule_datetime(value: str) -> datetime:
    dt, _ = parse_event_datetime(value)
    return dt


def mention_user(row: sqlite3.Row) -> str:
    username = sanitize(row["username"])
    full_name = sanitize(row["full_name"])
    if username:
        return f"@{username}"
    return full_name or str(row["user_id"])


def build_event_post_text(row: sqlite3.Row) -> str:
    title = sanitize(row["title"])
    category = sanitize(row["category_text"])
    dt_text = ""
    if row["event_dt"]:
        try:
            dt = datetime.fromisoformat(row["event_dt"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=TZ)
            dt_text = dt.astimezone(TZ).strftime("%d.%m.%Y")
            time_text = dt.astimezone(TZ).strftime("%H:%M")
        except Exception:
            dt_text = row["event_dt"]
            time_text = ""
    else:
        time_text = ""
    lines = [f"{category} • {title}" if category else title]
    if dt_text:
        lines.append(f"📅 Дата: {dt_text}")
    if time_text:
        lines.append(f"🕒 Время: {time_text}")
    if sanitize(row["place_text"]):
        lines.append(f"📍 Место: {row['place_text']}")
    if sanitize(row["cost_text"]):
        lines.append(f"💳 Стоимость: {row['cost_text']}")
    if sanitize(row["age_text"]):
        lines.append(f"🔞 Возраст: {row['age_text']}")
    if sanitize(row["description_text"]):
        lines.append(f"✍️ Описание: {row['description_text']}")
    if sanitize(row["contact_text"]):
        lines.append(f"📲 Контакт: {row['contact_text']}")
    return "\n".join(lines)


def build_admin_caption(row: sqlite3.Row) -> str:
    req_id = row["request_id"]
    req_type = row["request_type"]
    status = row["status"]
    submit_format = sanitize(row["submit_format"]) or "—"
    duplicate_note = ""
    tags = {t for t in sanitize(row["tags"]).split(",") if t}
    for tag in tags:
        if tag.startswith("Дубль:"):
            duplicate_note = f"\n⚠️ Возможный дубль: {tag.split(':', 1)[1]}"
            break

    if req_type == "event":
        lines = [
            f"Заявка #{req_id}",
            f"Тип: Событие",
            f"Формат: {submit_format}",
            f"Статус: {status}{duplicate_note}",
            "",
        ]
        if sanitize(row["raw_text"]):
            lines.extend(
                [
                    f"Текст: {row['raw_text']}",
                    f"Автор: {mention_user(row)}",
                    "",
                    "Чтобы написать заявителю, ответьте reply на это сообщение.",
                ]
            )
            return "\n".join(lines)
        lines.extend(
            [
                f"Название: {sanitize(row['title'])}",
                f"Дата и время: {format_event_dt_for_admin(row['event_dt'])}",
                f"Место: {sanitize(row['place_text'])}",
                f"Стоимость: {sanitize(row['cost_text'])}",
                f"Возраст: {sanitize(row['age_text'])}",
                f"Категория: {sanitize(row['category_text'])}",
                f"Описание: {sanitize(row['description_text'])}",
                f"Контакт / ссылка: {sanitize(row['contact_text']) or 'нет'}",
                f"Автор: {mention_user(row)}",
                "",
                "Чтобы написать заявителю, ответьте reply на это сообщение.",
            ]
        )
        return "\n".join(lines)

    if req_type == "ad":
        return "\n".join(
            [
                f"Заявка #{req_id}",
                "Тип: Реклама",
                f"Статус: {status}",
                "",
                f"Что рекламируют: {sanitize(row['ad_item'])}",
                f"Формат: {sanitize(row['ad_format'])}",
                f"Дата: {sanitize(row['ad_date'])}",
                f"Контакт: {sanitize(row['ad_contact'])}",
                f"Комментарий: {sanitize(row['ad_comment']) or '—'}",
                f"Автор: {mention_user(row)}",
                "",
                "Чтобы написать заявителю, ответьте reply на это сообщение.",
            ]
        )

    return "\n".join(
        [
            f"Заявка #{req_id}",
            "Тип: Партнёры",
            f"Статус: {status}",
            "",
            f"Компания: {sanitize(row['partner_name'])}",
            f"Предложение: {sanitize(row['partner_offer'])}",
            f"Идея: {sanitize(row['partner_idea'])}",
            f"Контакт: {sanitize(row['partner_contact'])}",
            f"Ссылка: {sanitize(row['partner_link']) or '—'}",
            f"Комментарий: {sanitize(row['partner_comment']) or '—'}",
            f"Автор: {mention_user(row)}",
            "",
            "Чтобы написать заявителю, ответьте reply на это сообщение.",
        ]
    )


def format_event_dt_for_admin(value: Optional[str]) -> str:
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return value


def event_moderation_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("В канал", callback_data=f"ev|publish|{request_id}"),
                InlineKeyboardButton("В план", callback_data=f"ev|plan|{request_id}"),
            ],
            [
                InlineKeyboardButton("Подборка", callback_data=f"ev|digest|{request_id}"),
                InlineKeyboardButton("На правки", callback_data=f"ev|revise|{request_id}"),
            ],
            [
                InlineKeyboardButton("Коммерция", callback_data=f"ev|commercial|{request_id}"),
                InlineKeyboardButton("Отклонить", callback_data=f"ev|reject|{request_id}"),
            ],
            [InlineKeyboardButton("Архив", callback_data=f"ev|archive|{request_id}")],
        ]
    )


def simple_moderation_keyboard(prefix: str, request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Принять", callback_data=f"{prefix}|accept|{request_id}"),
                InlineKeyboardButton("Уточнить", callback_data=f"{prefix}|clarify|{request_id}"),
            ],
            [
                InlineKeyboardButton("Отклонить", callback_data=f"{prefix}|reject|{request_id}"),
                InlineKeyboardButton("Архив", callback_data=f"{prefix}|archive|{request_id}"),
            ],
        ]
    )


async def send_request_to_admin_group(context: ContextTypes.DEFAULT_TYPE, request_id: str) -> None:
    row = get_request(request_id)
    if not row:
        return
    caption = build_admin_caption(row)
    keyboard: InlineKeyboardMarkup
    if row["request_type"] == "event":
        keyboard = event_moderation_keyboard(request_id)
    elif row["request_type"] == "ad":
        keyboard = simple_moderation_keyboard("ad", request_id)
    else:
        keyboard = simple_moderation_keyboard("pr", request_id)

    sent_message: Message
    photo_id = sanitize(row["photo_file_id"] or row["ad_media_file_id"])
    if photo_id:
        sent_message = await context.bot.send_photo(
            chat_id=ADMIN_GROUP_ID,
            photo=photo_id,
            caption=caption,
            reply_markup=keyboard,
        )
    else:
        sent_message = await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=caption,
            reply_markup=keyboard,
        )
    update_request(
        request_id,
        admin_group_chat_id=sent_message.chat_id,
        admin_group_message_id=sent_message.message_id,
    )


async def publish_event_request(
    context: ContextTypes.DEFAULT_TYPE,
    request_id: str,
    notify_user: bool = True,
) -> Optional[int]:
    row = get_request(request_id)
    if not row or row["request_type"] != "event":
        return None
    text = build_event_post_text(row)
    sent_message: Message
    if sanitize(row["photo_file_id"]):
        sent_message = await context.bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=row["photo_file_id"],
            caption=text,
        )
    else:
        sent_message = await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=text,
        )
    update_request(
        request_id,
        status="Опубликована",
        channel_message_id=sent_message.message_id,
        dialog_open=0,
        scheduled_at=None,
        pending_admin_action=None,
    )
    if notify_user:
        try:
            await context.bot.send_message(
                chat_id=row["user_id"],
                text=f"Ваша заявка #{request_id} опубликована. Спасибо!",
            )
        except Forbidden:
            logger.warning("Не удалось уведомить пользователя %s", row["user_id"])
    return sent_message.message_id


async def schedule_publish_job(
    app: Application,
    request_id: str,
    run_at: datetime,
) -> bool:
    if app.job_queue is None:
        logger.warning("JobQueue не настроен. Планирование для заявки %s недоступно.", request_id)
        return False
    existing = app.job_queue.get_jobs_by_name(f"publish:{request_id}")
    for job in existing:
        job.schedule_removal()
    app.job_queue.run_once(
        publish_scheduled_request_job,
        when=run_at,
        data={"request_id": request_id},
        name=f"publish:{request_id}",
    )
    return True


async def publish_scheduled_request_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    request_id = context.job.data["request_id"]
    try:
        await publish_event_request(context, request_id, notify_user=True)
        row = get_request(request_id)
        if row and row["admin_group_chat_id"] and row["admin_group_message_id"]:
            await context.bot.send_message(
                chat_id=row["admin_group_chat_id"],
                text=f"Заявка #{request_id} опубликована по плану.",
                reply_to_message_id=row["admin_group_message_id"],
            )
    except Exception as exc:
        logger.exception("Ошибка публикации по плану %s: %s", request_id, exc)


async def restore_scheduled_jobs(app: Application) -> None:
    if app.job_queue is None:
        logger.warning("JobQueue не настроен. Восстановление отложенных публикаций пропущено.")
        return
    for row in list_scheduled_requests():
        scheduled_at = sanitize(row["scheduled_at"])
        if not scheduled_at:
            continue
        try:
            run_at = datetime.fromisoformat(scheduled_at)
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=TZ)
        except Exception:
            continue
        if run_at <= datetime.now(TZ):
            continue
        await schedule_publish_job(app, row["request_id"], run_at)


async def daily_autoarchive_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    changed = autoarchive_expired_requests()
    if changed:
        logger.info("Автоархивировано заявок: %s", changed)


async def safe_copy_message(
    bot,
    to_chat_id: int,
    from_chat_id: int,
    message_id: int,
    reply_to_message_id: Optional[int] = None,
) -> None:
    await bot.copy_message(
        chat_id=to_chat_id,
        from_chat_id=from_chat_id,
        message_id=message_id,
        reply_to_message_id=reply_to_message_id,
    )


def set_active_flow(context: ContextTypes.DEFAULT_TYPE, value: str) -> None:
    context.user_data["active_flow"] = value


def clear_active_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("active_flow", None)


def clear_event_draft(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("event_form", None)


def clear_all_forms(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("event_form", None)
    context.user_data.pop("ad_form", None)
    context.user_data.pop("partner_form", None)
    clear_active_flow(context)


def suppress_next_private_fallback(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["suppress_next_fallback"] = True


# =========================================================
# Пользовательские сценарии
# =========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not is_private(update):
        return ConversationHandler.END
    context.user_data.pop("event_form", None)
    await update.message.reply_text(
        "Привет! Это бот GoМарик.\n"
        "Через него можно отправить событие для публикации, оставить рекламную заявку или предложить партнёрство.\n\n"
        "Выберите нужный раздел ниже.",
        reply_markup=MAIN_MENU,
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    clear_all_forms(context)
    suppress_next_private_fallback(context)
    await update.message.reply_text("Действие отменено.", reply_markup=MAIN_MENU)
    return ConversationHandler.END


async def open_event_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    clear_all_forms(context)
    set_active_flow(context, "event")
    await update.message.reply_text(
        "Как удобнее отправить событие?",
        reply_markup=EVENT_MODE_MENU,
    )
    return EVENT_MODE


async def event_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = sanitize(update.message.text)
    if text == BTN_FULL:
        set_active_flow(context, "event")
        context.user_data["event_form"] = {}
        await update.message.reply_text("Введите название события.", reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BTN_CANCEL)]], resize_keyboard=True))
        return EV_TITLE
    if text == BTN_QUICK:
        set_active_flow(context, "event")
        await update.message.reply_text(
            "Отправьте одним сообщением всё, что есть о событии: название, дату, место, стоимость, описание, контакт и фото. "
            "Если понадобится, мы уточним детали позже.",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BTN_CANCEL)]], resize_keyboard=True),
        )
        return QUICK_EVENT
    clear_all_forms(context)
    suppress_next_private_fallback(context)
    await update.message.reply_text("Возвращаю в главное меню.", reply_markup=MAIN_MENU)
    return ConversationHandler.END


async def ev_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.setdefault("event_form", {})["title"] = sanitize(update.message.text)
    await update.message.reply_text("Укажите дату и время в формате ДД.ММ.ГГГГ ЧЧ:ММ или ДД.ММ ЧЧ:ММ.")
    return EV_DATETIME


async def ev_datetime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = sanitize(update.message.text)
    try:
        dt, pretty = parse_event_datetime(value)
    except ValueError:
        await update.message.reply_text("Неверный формат. Пример: 25.03.2026 19:00")
        return EV_DATETIME
    form = context.user_data.setdefault("event_form", {})
    form["event_dt"] = dt.isoformat()
    form["event_dt_text"] = pretty
    await update.message.reply_text("Укажите место проведения.")
    return EV_PLACE


async def ev_place(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.setdefault("event_form", {})["place_text"] = sanitize(update.message.text)
    await update.message.reply_text("Укажите стоимость.", reply_markup=COST_MENU)
    return EV_COST


async def ev_cost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = sanitize(update.message.text)
    if text == "Другое":
        await update.message.reply_text("Введите стоимость вручную.")
        return EV_COST_OTHER
    context.user_data.setdefault("event_form", {})["cost_text"] = text
    await update.message.reply_text("Укажите возрастное ограничение.", reply_markup=AGE_MENU)
    return EV_AGE


async def ev_cost_other(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.setdefault("event_form", {})["cost_text"] = sanitize(update.message.text)
    await update.message.reply_text("Укажите возрастное ограничение.", reply_markup=AGE_MENU)
    return EV_AGE


async def ev_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = sanitize(update.message.text)
    if text == "Другое":
        await update.message.reply_text("Введите возрастное ограничение вручную.")
        return EV_AGE_OTHER
    context.user_data.setdefault("event_form", {})["age_text"] = text
    await update.message.reply_text("Коротко опишите событие.", reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BTN_CANCEL)]], resize_keyboard=True))
    return EV_DESCRIPTION


async def ev_age_other(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.setdefault("event_form", {})["age_text"] = sanitize(update.message.text)
    await update.message.reply_text("Коротко опишите событие.", reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BTN_CANCEL)]], resize_keyboard=True))
    return EV_DESCRIPTION


async def ev_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.setdefault("event_form", {})["description_text"] = sanitize(update.message.text)
    await update.message.reply_text("Выберите категорию.", reply_markup=CATEGORY_MENU)
    return EV_CATEGORY


async def ev_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = sanitize(update.message.text)
    if text == "Другое":
        await update.message.reply_text("Введите категорию вручную.")
        return EV_CATEGORY_OTHER
    context.user_data.setdefault("event_form", {})["category_text"] = text
    await update.message.reply_text(
        "Укажите контакт или ссылку для связи. Можно указать что-то одно. Если ничего нет — напишите “нет”.",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BTN_CANCEL)]], resize_keyboard=True),
    )
    return EV_CONTACT


async def ev_category_other(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.setdefault("event_form", {})["category_text"] = sanitize(update.message.text)
    await update.message.reply_text(
        "Укажите контакт или ссылку для связи. Можно указать что-то одно. Если ничего нет — напишите “нет”.",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BTN_CANCEL)]], resize_keyboard=True),
    )
    return EV_CONTACT


async def ev_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = sanitize(update.message.text)
    context.user_data.setdefault("event_form", {})["contact_text"] = "" if text.lower() == "нет" else text
    await update.message.reply_text("Отправьте фото или афишу одним сообщением.")
    return EV_PHOTO


async def ev_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        await update.message.reply_text("Нужно отправить именно фото или афишу изображением.")
        return EV_PHOTO
    form = context.user_data.setdefault("event_form", {})
    form["photo_file_id"] = update.message.photo[-1].file_id
    await update.message.reply_text(event_preview_text(form), reply_markup=PREVIEW_MENU)
    return EV_PREVIEW


async def ev_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = sanitize(update.message.text)
    if text == BTN_SEND:
        form = context.user_data.get("event_form", {})
        user = update.effective_user
        request_id = create_request(
            {
                "request_type": "event",
                "submit_format": "Полная",
                "status": "Новая",
                "user_id": user.id,
                "username": user.username or "",
                "full_name": user_full_name(update),
                "title": form.get("title", ""),
                "event_dt": form.get("event_dt", ""),
                "place_text": form.get("place_text", ""),
                "cost_text": form.get("cost_text", ""),
                "age_text": form.get("age_text", ""),
                "description_text": form.get("description_text", ""),
                "category_text": form.get("category_text", ""),
                "contact_text": form.get("contact_text", ""),
                "photo_file_id": form.get("photo_file_id", ""),
            }
        )
        duplicate_id = build_duplicate_tag(form.get("title", ""), form.get("event_dt", ""), form.get("place_text", ""))
        if duplicate_id and duplicate_id != request_id:
            add_tag(request_id, f"Дубль:{duplicate_id}")
        await send_request_to_admin_group(context, request_id)
        clear_all_forms(context)
        suppress_next_private_fallback(context)
        await update.message.reply_text(
            "Спасибо! Ваша заявка принята и отправлена на модерацию. Если потребуется, мы свяжемся с вами для уточнения деталей.",
            reply_markup=MAIN_MENU,
        )
        return ConversationHandler.END
    if text == BTN_EDIT:
        context.user_data["event_form"] = {}
        await update.message.reply_text("Давайте заполним заново. Введите название события.", reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BTN_CANCEL)]], resize_keyboard=True))
        return EV_TITLE
    suppress_next_private_fallback(context)
    await update.message.reply_text("Заявка отменена.", reply_markup=MAIN_MENU)
    clear_all_forms(context)
    return ConversationHandler.END


async def quick_event(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    raw_text = sanitize(message.caption or message.text)
    photo_file_id = message.photo[-1].file_id if message.photo else ""
    if not raw_text and not photo_file_id:
        await message.reply_text("Отправьте хотя бы текст или фото.")
        return QUICK_EVENT
    user = update.effective_user
    request_id = create_request(
        {
            "request_type": "event",
            "submit_format": "Быстрая",
            "status": "Новая",
            "user_id": user.id,
            "username": user.username or "",
            "full_name": user_full_name(update),
            "raw_text": raw_text,
            "photo_file_id": photo_file_id,
            "dialog_open": 0,
            "tags": "Быстрая",
        }
    )
    await send_request_to_admin_group(context, request_id)
    clear_all_forms(context)
    suppress_next_private_fallback(context)
    await message.reply_text(
        "Спасибо! Заявка принята. Если потребуется, мы уточним детали.",
        reply_markup=MAIN_MENU,
    )
    return ConversationHandler.END


async def open_ad(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    clear_all_forms(context)
    set_active_flow(context, "ad")
    await update.message.reply_text(
        "Реклама в GoМарик\n\n"
        "Форматы:\n"
        "— пост\n"
        "— интеграция\n"
        "— подборка\n"
        "— закреп\n"
        "— серия\n"
        "— другое по согласованию\n\n"
        "Важно:\n"
        "— коммерческие размещения обсуждаются отдельно\n"
        "— стоимость зависит от формата и даты\n"
        "— чем точнее заявка, тем быстрее ответ\n\n"
        "Выберите формат ниже.",
        reply_markup=AD_FORMAT_MENU,
    )
    return AD_INFO


async def ad_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = sanitize(update.message.text)
    if text == BTN_BACK:
        clear_all_forms(context)
        suppress_next_private_fallback(context)
        await update.message.reply_text("Возвращаю в главное меню.", reply_markup=MAIN_MENU)
        return ConversationHandler.END
    context.user_data["ad_form"] = {"ad_format": text}
    await update.message.reply_text("Коротко напишите, что вы рекламируете.", reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BTN_CANCEL)]], resize_keyboard=True))
    return AD_ITEM


async def ad_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.setdefault("ad_form", {})["ad_item"] = sanitize(update.message.text)
    await update.message.reply_text("Укажите желаемую дату размещения.")
    return AD_DATE


async def ad_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.setdefault("ad_form", {})["ad_date"] = sanitize(update.message.text)
    await update.message.reply_text("Укажите контакт для связи.")
    return AD_CONTACT


async def ad_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.setdefault("ad_form", {})["ad_contact"] = sanitize(update.message.text)
    await update.message.reply_text("Добавьте комментарий. Если нечего добавить — напишите “нет”.")
    return AD_COMMENT


async def ad_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = sanitize(update.message.text)
    context.user_data.setdefault("ad_form", {})["ad_comment"] = "" if text.lower() == "нет" else text
    await update.message.reply_text("Отправьте фото / макет / ссылку. Если ничего нет — напишите “нет”.")
    return AD_MEDIA


async def ad_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    form = context.user_data.setdefault("ad_form", {})
    media_file_id = ""
    if update.message.photo:
        media_file_id = update.message.photo[-1].file_id
    elif update.message.text:
        txt = sanitize(update.message.text)
        if txt.lower() != "нет":
            form["ad_comment"] = f"{form.get('ad_comment', '')}\nМатериал: {txt}".strip()
    user = update.effective_user
    request_id = create_request(
        {
            "request_type": "ad",
            "submit_format": "Реклама",
            "status": "Новая",
            "user_id": user.id,
            "username": user.username or "",
            "full_name": user_full_name(update),
            "ad_item": form.get("ad_item", ""),
            "ad_format": form.get("ad_format", ""),
            "ad_date": form.get("ad_date", ""),
            "ad_contact": form.get("ad_contact", ""),
            "ad_comment": form.get("ad_comment", ""),
            "ad_media_file_id": media_file_id,
        }
    )
    await send_request_to_admin_group(context, request_id)
    clear_all_forms(context)
    suppress_next_private_fallback(context)
    await update.message.reply_text(
        "Спасибо! Рекламная заявка принята. Мы рассмотрим её и свяжемся с вами.",
        reply_markup=MAIN_MENU,
    )
    return ConversationHandler.END


async def open_partners(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    clear_all_forms(context)
    set_active_flow(context, "partner")
    await update.message.reply_text(
        "Отправьте заявку на партнёрство или спецпроект.",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton(BTN_CANCEL)]], resize_keyboard=True),
    )
    return PARTNER_NAME


async def partner_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["partner_form"] = {"partner_name": sanitize(update.message.text)}
    await update.message.reply_text("Коротко напишите, что вы предлагаете.")
    return PARTNER_OFFER


async def partner_offer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.setdefault("partner_form", {})["partner_offer"] = sanitize(update.message.text)
    await update.message.reply_text("Опишите идею сотрудничества.")
    return PARTNER_IDEA


async def partner_idea(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.setdefault("partner_form", {})["partner_idea"] = sanitize(update.message.text)
    await update.message.reply_text("Укажите контакт для связи.")
    return PARTNER_CONTACT


async def partner_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.setdefault("partner_form", {})["partner_contact"] = sanitize(update.message.text)
    await update.message.reply_text("Укажите ссылку. Если ссылки нет — напишите “нет”.")
    return PARTNER_LINK


async def partner_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = sanitize(update.message.text)
    context.user_data.setdefault("partner_form", {})["partner_link"] = "" if text.lower() == "нет" else text
    await update.message.reply_text("Добавьте комментарий. Если нечего добавить — напишите “нет”.")
    return PARTNER_COMMENT


async def partner_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = sanitize(update.message.text)
    form = context.user_data.setdefault("partner_form", {})
    form["partner_comment"] = "" if text.lower() == "нет" else text
    user = update.effective_user
    request_id = create_request(
        {
            "request_type": "partner",
            "submit_format": "Партнёры",
            "status": "Новая",
            "user_id": user.id,
            "username": user.username or "",
            "full_name": user_full_name(update),
            "partner_name": form.get("partner_name", ""),
            "partner_offer": form.get("partner_offer", ""),
            "partner_idea": form.get("partner_idea", ""),
            "partner_contact": form.get("partner_contact", ""),
            "partner_link": form.get("partner_link", ""),
            "partner_comment": form.get("partner_comment", ""),
        }
    )
    await send_request_to_admin_group(context, request_id)
    clear_all_forms(context)
    suppress_next_private_fallback(context)
    await update.message.reply_text(
        "Спасибо! Ваше предложение отправлено на рассмотрение.",
        reply_markup=MAIN_MENU,
    )
    return ConversationHandler.END


# =========================================================
# Reply-мост и приватные ответы пользователя
# =========================================================
async def forward_user_reply_if_needed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private(update):
        return
    if not update.message or update.message.text and update.message.text.startswith("/"):
        return

    open_request = get_open_request_for_user(update.effective_user.id)
    if not open_request:
        return

    # Если диалог закрыт статусом, показываем меню и не пересылаем.
    if open_request["status"] in FINAL_STATUSES:
        update_request(open_request["request_id"], dialog_open=0)
        await update.message.reply_text(
            "Эта заявка уже закрыта. Если хотите отправить новую, выберите раздел ниже.",
            reply_markup=MAIN_MENU,
        )
        return

    admin_group_message_id = open_request["admin_group_message_id"]
    if not admin_group_message_id:
        return

    await safe_copy_message(
        bot=context.bot,
        to_chat_id=ADMIN_GROUP_ID,
        from_chat_id=update.effective_chat.id,
        message_id=update.message.message_id,
        reply_to_message_id=admin_group_message_id,
    )
    update_request(open_request["request_id"], updated_at=now_iso())


async def admin_group_reply_bridge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized_admin(update):
        return
    if not update.message or not update.message.reply_to_message:
        return
    if update.message.text and update.message.text.startswith("/"):
        return

    source_request = get_request_by_admin_message(update.message.reply_to_message.message_id)
    if not source_request:
        return

    # Если ожидали дату планирования, используем reply как дату публикации.
    if source_request["pending_admin_action"] == "schedule":
        if not update.message.text:
            await update.message.reply_text(
                "Для планирования отправьте дату reply-сообщением в формате ДД.ММ.ГГГГ ЧЧ:ММ.",
                reply_to_message_id=source_request["admin_group_message_id"],
            )
            return
        try:
            run_at = parse_schedule_datetime(update.message.text)
        except ValueError:
            await update.message.reply_text(
                "Неверный формат даты. Пример: 25.03.2026 18:30",
                reply_to_message_id=source_request["admin_group_message_id"],
            )
            return
        update_request(
            source_request["request_id"],
            status="В план",
            scheduled_at=run_at.isoformat(),
            pending_admin_action=None,
            dialog_open=0,
        )
        scheduled = await schedule_publish_job(context.application, source_request["request_id"], run_at)
        if scheduled:
            text = f"Заявка #{source_request['request_id']} поставлена в план на {run_at.astimezone(TZ).strftime('%d.%m.%Y %H:%M')}"
        else:
            text = "Планирование недоступно: JobQueue не настроен. Установите зависимость python-telegram-bot[job-queue]."
        await update.message.reply_text(
            text,
            reply_to_message_id=source_request["admin_group_message_id"],
        )
        return

    # Обычный reply пользователю
    try:
        await safe_copy_message(
            bot=context.bot,
            to_chat_id=source_request["user_id"],
            from_chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
        )
        update_request(source_request["request_id"], dialog_open=1)
        await update.message.reply_text(
            f"Сообщение отправлено заявителю по заявке #{source_request['request_id']}",
            reply_to_message_id=source_request["admin_group_message_id"],
        )
    except Forbidden:
        await update.message.reply_text(
            "Пользователь больше не может получать сообщения от бота.",
            reply_to_message_id=source_request["admin_group_message_id"],
        )


# =========================================================
# Модерация кнопками
# =========================================================
async def moderation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    if not is_authorized_admin(update):
        await query.answer("Нет доступа", show_alert=True)
        return

    try:
        prefix, action, request_id = query.data.split("|", 2)
    except ValueError:
        return

    row = get_request(request_id)
    if not row:
        await query.answer("Заявка не найдена", show_alert=True)
        return

    if prefix == "ev":
        await handle_event_moderation(query, context, row, action)
    elif prefix == "ad":
        await handle_simple_moderation(query, context, row, action, "Рекламная заявка")
    elif prefix == "pr":
        await handle_simple_moderation(query, context, row, action, "Партнёрская заявка")


async def handle_event_moderation(query, context, row: sqlite3.Row, action: str) -> None:
    request_id = row["request_id"]
    reply_to = row["admin_group_message_id"]

    if action == "publish":
        await publish_event_request(context, request_id, notify_user=True)
        await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=f"Заявка #{request_id} опубликована.",
            reply_to_message_id=reply_to,
        )
        return

    if action == "plan":
        update_request(request_id, status="В план", pending_admin_action="schedule", dialog_open=0)
        await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=(
                f"Заявка #{request_id} переведена в статус “В план”.\n"
                "Ответьте reply на карточку датой публикации в формате ДД.ММ.ГГГГ ЧЧ:ММ."
            ),
            reply_to_message_id=reply_to,
        )
        return

    if action == "digest":
        add_tag(request_id, "Подборка")
        await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=f"Заявка #{request_id} добавлена в подборку.",
            reply_to_message_id=reply_to,
        )
        return

    if action == "revise":
        update_request(request_id, status="На правки", dialog_open=1)
        await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=(
                "Статус изменён на “На правки”. "
                "Ответьте reply на карточку, чтобы отправить сообщение заявителю."
            ),
            reply_to_message_id=reply_to,
        )
        return

    if action == "commercial":
        add_tag(request_id, "Коммерция")
        update_request(request_id, dialog_open=1)
        try:
            await context.bot.send_message(
                chat_id=row["user_id"],
                text=(
                    f"Спасибо за заявку #{request_id}. Этот формат относится к коммерческому размещению. "
                    "Для обсуждения условий воспользуйтесь разделом “Реклама” или дождитесь ответа администратора."
                ),
            )
        except Forbidden:
            logger.warning("Не удалось уведомить пользователя %s", row["user_id"])
        await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=f"Заявка #{request_id} помечена как коммерческая.",
            reply_to_message_id=reply_to,
        )
        return

    if action == "reject":
        update_request(request_id, status="Отклонена", dialog_open=0, pending_admin_action=None)
        try:
            await context.bot.send_message(
                chat_id=row["user_id"],
                text=f"Ваша заявка #{request_id} отклонена.",
            )
        except Forbidden:
            logger.warning("Не удалось уведомить пользователя %s", row["user_id"])
        await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=f"Заявка #{request_id} отклонена.",
            reply_to_message_id=reply_to,
        )
        return

    if action == "archive":
        update_request(request_id, status="Архив", dialog_open=0, pending_admin_action=None)
        await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=f"Заявка #{request_id} отправлена в архив.",
            reply_to_message_id=reply_to,
        )
        return


async def handle_simple_moderation(query, context, row: sqlite3.Row, action: str, label: str) -> None:
    request_id = row["request_id"]
    reply_to = row["admin_group_message_id"]

    if action == "accept":
        update_request(request_id, status="В план", dialog_open=1)
        try:
            await context.bot.send_message(
                chat_id=row["user_id"],
                text=f"Ваша заявка #{request_id} принята. Мы свяжемся с вами по деталям.",
            )
        except Forbidden:
            logger.warning("Не удалось уведомить пользователя %s", row["user_id"])
        await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=f"{label} #{request_id} принята.",
            reply_to_message_id=reply_to,
        )
        return

    if action == "clarify":
        update_request(request_id, status="На правки", dialog_open=1)
        await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=(
                "Статус изменён на “На правки”. "
                "Ответьте reply на карточку, чтобы отправить сообщение заявителю."
            ),
            reply_to_message_id=reply_to,
        )
        return

    if action == "reject":
        update_request(request_id, status="Отклонена", dialog_open=0)
        try:
            await context.bot.send_message(
                chat_id=row["user_id"],
                text=f"Ваша заявка #{request_id} отклонена.",
            )
        except Forbidden:
            logger.warning("Не удалось уведомить пользователя %s", row["user_id"])
        await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=f"{label} #{request_id} отклонена.",
            reply_to_message_id=reply_to,
        )
        return

    if action == "archive":
        update_request(request_id, status="Архив", dialog_open=0)
        await context.bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=f"{label} #{request_id} отправлена в архив.",
            reply_to_message_id=reply_to,
        )
        return


# =========================================================
# Общие хендлеры
# =========================================================
async def private_fallback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_private(update):
        return
    if context.user_data.pop("suppress_next_fallback", False):
        return
    if context.user_data.get("active_flow"):
        return
    await update.message.reply_text("Выберите раздел ниже.", reply_markup=MAIN_MENU)


async def on_startup(app: Application) -> None:
    init_db()
    await restore_scheduled_jobs(app)
    if app.job_queue is not None:
        app.job_queue.run_daily(daily_autoarchive_job, time=datetime.now(TZ).time(), name="daily_autoarchive")
    else:
        logger.warning(
            "JobQueue не настроен. Бот запущен без автоархива и отложенных публикаций. "
            'Установите зависимость: pip install "python-telegram-bot[job-queue]"'
        )
    logger.info("Бот запущен")


# =========================================================
# Сборка приложения
# =========================================================

def build_application() -> Application:
    application = ApplicationBuilder().token(BOT_TOKEN).post_init(on_startup).build()

    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.ChatType.PRIVATE & filters.Regex(f"^{BTN_EVENT}$"), open_event_menu),
            MessageHandler(filters.ChatType.PRIVATE & filters.Regex(f"^{BTN_AD}$"), open_ad),
            MessageHandler(filters.ChatType.PRIVATE & filters.Regex(f"^{BTN_PARTNERS}$"), open_partners),
        ],
        states={
            EVENT_MODE: [MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, event_mode)],
            EV_TITLE: [MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, ev_title)],
            EV_DATETIME: [MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, ev_datetime)],
            EV_PLACE: [MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, ev_place)],
            EV_COST: [MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, ev_cost)],
            EV_COST_OTHER: [MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, ev_cost_other)],
            EV_AGE: [MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, ev_age)],
            EV_AGE_OTHER: [MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, ev_age_other)],
            EV_DESCRIPTION: [MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, ev_description)],
            EV_CATEGORY: [MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, ev_category)],
            EV_CATEGORY_OTHER: [MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, ev_category_other)],
            EV_CONTACT: [MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, ev_contact)],
            EV_PHOTO: [MessageHandler(filters.ChatType.PRIVATE & filters.PHOTO, ev_photo)],
            EV_PREVIEW: [MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, ev_preview)],
            QUICK_EVENT: [MessageHandler(filters.ChatType.PRIVATE & (filters.TEXT | filters.PHOTO) & ~filters.COMMAND, quick_event)],
            AD_INFO: [MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, ad_info)],
            AD_ITEM: [MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, ad_item)],
            AD_DATE: [MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, ad_date)],
            AD_CONTACT: [MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, ad_contact)],
            AD_COMMENT: [MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, ad_comment)],
            AD_MEDIA: [MessageHandler(filters.ChatType.PRIVATE & (filters.TEXT | filters.PHOTO) & ~filters.COMMAND, ad_media)],
            PARTNER_NAME: [MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, partner_name)],
            PARTNER_OFFER: [MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, partner_offer)],
            PARTNER_IDEA: [MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, partner_idea)],
            PARTNER_CONTACT: [MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, partner_contact)],
            PARTNER_LINK: [MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, partner_link)],
            PARTNER_COMMENT: [MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, partner_comment)],
        },
        fallbacks=[
            CommandHandler("start", start),
            MessageHandler(filters.ChatType.PRIVATE & filters.Regex(f"^{BTN_CANCEL}$"), cancel),
        ],
        allow_reentry=True,
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(moderation_callback))
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, forward_user_reply_if_needed), group=0)
    application.add_handler(MessageHandler(filters.Chat(ADMIN_GROUP_ID) & filters.REPLY & ~filters.COMMAND, admin_group_reply_bridge), group=0)
    application.add_handler(conv_handler, group=1)
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, private_fallback), group=2)

    return application


# =========================================================
# Точка входа
# =========================================================

def main() -> None:
    application = build_application()
    if WEBHOOK_URL:
        webhook_url = f"{WEBHOOK_URL.rstrip('/')}/{WEBHOOK_PATH}"
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=WEBHOOK_PATH,
            webhook_url=webhook_url,
            secret_token=WEBHOOK_SECRET or None,
            drop_pending_updates=True,
        )
    else:
        application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
