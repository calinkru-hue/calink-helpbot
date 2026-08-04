import aiosqlite
import json
import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Railway: volume примонтирован в /data/, локально — рядом с ботом
DATA_DIR = "/data" if os.path.isdir("/data") else os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DATA_DIR, "support_bot.db")


async def init_db():
    """Создать таблицы, если не существуют."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT,
                username TEXT,
                topic_id INTEGER NOT NULL,
                is_calink_user INTEGER DEFAULT 0,
                card_message_id INTEGER,
                last_auto_reply TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                group_message_id INTEGER NOT NULL,
                client_message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                topic_id INTEGER NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (group_message_id, topic_id)
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_client
            ON messages (client_message_id, user_id)
        """)
        # Плоский audit-trail всей переписки. Схема намеренно 1-в-1 с
        # zorion-helpbot, чтобы один и тот же аналитический SQL работал на
        # обоих ботах. Колонка email_message_id здесь всегда NULL (в Calink
        # нет email-канала) — оставлена ради совместимости запросов.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS event_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                topic_id        INTEGER,
                channel         TEXT NOT NULL,
                event_type      TEXT NOT NULL,
                direction       TEXT,
                actor_type      TEXT,
                actor_id        TEXT,
                actor_name      TEXT,
                text            TEXT,
                media_type      TEXT,
                media_file_id   TEXT,
                tg_message_id   INTEGER,
                email_message_id TEXT,
                extra_json      TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_event_log_topic
            ON event_log (topic_id, created_at)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_event_log_type
            ON event_log (event_type, created_at)
        """)
        # Заявки на фидбек. Схема — подмножество feedback из zorion-helpbot
        # (без email-полей: у Calink нет email-канала), чтобы запросы по
        # оценкам работали одинаково на обоих ботах.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                channel         TEXT NOT NULL DEFAULT 'telegram',
                telegram_user_id INTEGER,
                topic_id        INTEGER NOT NULL,
                rating          INTEGER,
                sent_at         TEXT DEFAULT (datetime('now')),
                rated_at        TEXT,
                yes_clicked_at  TEXT
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_feedback_user
            ON feedback (telegram_user_id, rated_at)
        """)
        # Миграции для существующих таблиц
        for col in ("last_auto_reply TEXT", "is_calink_user INTEGER DEFAULT 0", "card_message_id INTEGER",
                    "last_support_reply_at TEXT", "last_user_message_at TEXT", "feedback_sent_at TEXT"):
            try:
                await db.execute(f"ALTER TABLE users ADD COLUMN {col}")
            except Exception:
                pass
        await db.commit()
        logger.info("БД инициализирована: %s", DB_PATH)


# ─── Users ───────────────────────────────────

async def get_user(user_id: int) -> dict | None:
    """Получить пользователя по user_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, first_name, username, topic_id, "
            "is_calink_user, card_message_id, last_auto_reply "
            "FROM users WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def create_user(user_id: int, first_name: str, username: str, topic_id: int):
    """Создать нового пользователя с привязкой к топику."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users (user_id, first_name, username, topic_id) "
            "VALUES (?, ?, ?, ?)",
            (user_id, first_name, username, topic_id),
        )
        await db.commit()


async def get_user_by_topic(topic_id: int) -> dict | None:
    """Найти пользователя по ID топика."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, first_name, username, topic_id, "
            "is_calink_user, card_message_id "
            "FROM users WHERE topic_id = ?",
            (topic_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def mark_calink_user(user_id: int, card_message_id: int):
    """Отметить пользователя как найденного в Calink и сохранить ID карточки."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET is_calink_user = 1, card_message_id = ? "
            "WHERE user_id = ?",
            (card_message_id, user_id),
        )
        await db.commit()


async def save_card_message_id(user_id: int, card_message_id: int):
    """Сохранить ID сообщения-карточки (для не-Calink пользователей)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET card_message_id = ? WHERE user_id = ?",
            (card_message_id, user_id),
        )
        await db.commit()


async def should_send_auto_reply(user_id: int) -> bool:
    """Проверить, нужно ли отправить авто-ответ (не чаще раза в день)."""
    user = await get_user(user_id)
    if not user or not user.get("last_auto_reply"):
        return True
    last = datetime.fromisoformat(user["last_auto_reply"])
    now = datetime.now(timezone.utc)
    return (now - last).total_seconds() > 86400


async def update_auto_reply_time(user_id: int):
    """Обновить время последнего авто-ответа."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET last_auto_reply = ? WHERE user_id = ?",
            (now, user_id),
        )
        await db.commit()


# ─── Messages (маппинг group ↔ client) ──────

async def save_message_mapping(
    group_message_id: int,
    client_message_id: int,
    user_id: int,
    topic_id: int,
):
    """Сохранить связь group_message_id ↔ client_message_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO messages "
            "(group_message_id, client_message_id, user_id, topic_id) "
            "VALUES (?, ?, ?, ?)",
            (group_message_id, client_message_id, user_id, topic_id),
        )
        await db.commit()


async def get_client_message_id(group_message_id: int, topic_id: int) -> int | None:
    """Найти client_message_id по group_message_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT client_message_id FROM messages "
            "WHERE group_message_id = ? AND topic_id = ?",
            (group_message_id, topic_id),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def delete_message_mapping(group_message_id: int, topic_id: int):
    """Удалить запись маппинга."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM messages WHERE group_message_id = ? AND topic_id = ?",
            (group_message_id, topic_id),
        )
        await db.commit()


# ─── Фидбек ──────────────────────────────────

# ВАЖНО про таймстемпы: все сравнения дат обёрнуты в SQLite datetime().
# Python пишет '2026-08-04T12:00:00+00:00' (через T), а DEFAULT (datetime('now'))
# пишет '2026-08-04 12:00:00' (через пробел). Пробел (0x20) сортируется РАНЬШЕ
# 'T' (0x54), поэтому строковое сравнение колонок разного происхождения врёт.
# На этом уже спотыкались в zorion-helpbot (reminder уходил через минуту вместо
# пяти). datetime() приводит оба формата к одному виду.


async def mark_support_reply(user_id: int):
    """Обновить время последнего ответа саппорта — от него считается задержка
    перед вопросом об оценке."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET last_support_reply_at = ? WHERE user_id = ?",
            (now, user_id),
        )
        await db.commit()


async def mark_user_message(user_id: int):
    """Обновить время последнего сообщения клиента. Нужно, чтобы не просить
    оценку, когда последнее слово за клиентом (мы ещё должны ответить)."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET last_user_message_at = ? WHERE user_id = ?",
            (now, user_id),
        )
        await db.commit()


async def mark_feedback_sent(user_id: int):
    """Отметить, что вопрос об оценке отправлен (чтобы не спамить)."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET feedback_sent_at = ? WHERE user_id = ?",
            (now, user_id),
        )
        await db.commit()


async def users_due_for_feedback(threshold_iso: str) -> list[dict]:
    """Юзеры, которым пора задать вопрос об оценке.

    Все условия должны выполняться:
      - саппорт хотя бы раз отвечал, и этот ответ старше порога
      - вопрос либо не задавался, либо задавался ДО последнего ответа саппорта
        (т.е. на новое обращение спросим снова)
      - последнее слово в переписке за саппортом, а не за клиентом — иначе мы
        сами ещё должны ответить, и спрашивать оценку рано
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, topic_id FROM users "
            "WHERE last_support_reply_at IS NOT NULL "
            "  AND datetime(last_support_reply_at) < datetime(?) "
            "  AND (feedback_sent_at IS NULL "
            "       OR datetime(feedback_sent_at) < datetime(last_support_reply_at)) "
            "  AND (last_user_message_at IS NULL "
            "       OR datetime(last_user_message_at) <= datetime(last_support_reply_at))",
            (threshold_iso,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def save_feedback(topic_id: int, telegram_user_id: int) -> int:
    """Создать строку заявки на фидбек. Возвращает id."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO feedback (channel, telegram_user_id, topic_id) "
            "VALUES ('telegram', ?, ?)",
            (telegram_user_id, topic_id),
        )
        await db.commit()
        return cursor.lastrowid


async def get_unanswered_feedback(user_id: int) -> dict | None:
    """Последняя заявка, на которую юзер ещё не ответил.

    `rated_at IS NULL` — канонический признак «ответа не было»: клик «Нет»
    ставит rated_at, не заполняя rating, поэтому проверять только rating нельзя.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, topic_id FROM feedback "
            "WHERE telegram_user_id = ? AND rated_at IS NULL "
            "ORDER BY id DESC LIMIT 1",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def mark_feedback_yes_clicked(feedback_id: int) -> bool:
    """Зафиксировать клик «Да, решён». Идемпотентно: срабатывает только когда
    и yes_clicked_at, и rated_at пусты — повторные клики не плодят сообщения
    в топике и не дают переобуться после выбора «Да»."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE feedback SET yes_clicked_at = ? "
            "WHERE id = ? AND yes_clicked_at IS NULL AND rated_at IS NULL",
            (now, feedback_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def mark_feedback_no_resolved(feedback_id: int) -> bool:
    """Зафиксировать ответ «Нет, не решён»: ставит rated_at, rating остаётся
    NULL. Идемпотентно и блокирует путь «Нет» после выбранного «Да»."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE feedback SET rated_at = ? "
            "WHERE id = ? AND rated_at IS NULL AND yes_clicked_at IS NULL",
            (now, feedback_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def update_feedback_rating(feedback_id: int, rating: int) -> bool:
    """Поставить оценку. Идемпотентно: только если rated_at ещё пуст."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE feedback SET rating = ?, rated_at = ? "
            "WHERE id = ? AND rated_at IS NULL",
            (rating, now, feedback_id),
        )
        await db.commit()
        return cursor.rowcount > 0


# ─── Hard-reset юзера (dev/QA) ───────────────

async def delete_user_by_topic(topic_id: int) -> int | None:
    """Полностью стереть юзера, привязанного к топику, из всех 4 таблиц одной
    транзакцией. Возвращает user_id (для отмены pending-джобов и логов) либо
    None, если топик не найден.

    Намеренно НЕ пишет ничего в event_log: reset — тестовый инструмент, его
    служебная активность не должна попадать в аналитику. Следы остаются только
    в логах Railway.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id FROM users WHERE topic_id = ?", (topic_id,)
        ) as cursor:
            row = await cursor.fetchone()
            user_id = row[0] if row else None

        if user_id is not None:
            await db.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
            await db.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
            await db.execute("DELETE FROM feedback WHERE telegram_user_id = ?", (user_id,))
        await db.execute("DELETE FROM messages WHERE topic_id = ?", (topic_id,))
        await db.execute("DELETE FROM feedback WHERE topic_id = ?", (topic_id,))
        await db.execute("DELETE FROM event_log WHERE topic_id = ?", (topic_id,))
        await db.commit()

    return user_id


# ─── Event log (полная история переписки) ────

async def log_event(
    *,
    event_type: str,
    channel: str = "telegram",
    topic_id: int | None = None,
    direction: str | None = None,
    actor_type: str | None = None,
    actor_id: str | int | None = None,
    actor_name: str | None = None,
    text: str | None = None,
    media_type: str | None = None,
    media_file_id: str | None = None,
    tg_message_id: int | None = None,
    extra: dict | None = None,
) -> None:
    """Добавить одну запись в event_log. Устойчива к ошибкам — никогда не
    поднимает исключение: падение аудит-лога не должно ломать основной флоу."""
    try:
        actor_id_str = str(actor_id) if actor_id is not None else None
        extra_json = json.dumps(extra, ensure_ascii=False) if extra else None
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO event_log (topic_id, channel, event_type, direction, "
                "actor_type, actor_id, actor_name, text, media_type, media_file_id, "
                "tg_message_id, extra_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (topic_id, channel, event_type, direction, actor_type, actor_id_str,
                 actor_name, text, media_type, media_file_id, tg_message_id,
                 extra_json),
            )
            await db.commit()
    except Exception:
        logger.exception("log_event failed (type=%s topic=%s)", event_type, topic_id)
