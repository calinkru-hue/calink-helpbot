import aiosqlite
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
        # Миграции для существующих таблиц
        for col in ("last_auto_reply TEXT", "is_calink_user INTEGER DEFAULT 0", "card_message_id INTEGER"):
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
