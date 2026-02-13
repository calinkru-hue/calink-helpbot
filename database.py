import aiosqlite
import os
from datetime import datetime, timezone

# Railway: volume примонтирован в /data/, локально — рядом с ботом
DATA_DIR = "/data" if os.path.isdir("/data") else os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DATA_DIR, "support_bot.db")


async def init_db():
    """Создать таблицу users, если не существует."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT,
                username TEXT,
                topic_id INTEGER,
                last_auto_reply TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Миграция: добавить last_auto_reply если таблица уже существует
        try:
            await db.execute("ALTER TABLE users ADD COLUMN last_auto_reply TEXT")
        except Exception:
            pass  # Колонка уже существует
        await db.commit()


async def get_user(user_id: int) -> dict | None:
    """Получить пользователя по user_id."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
    return None


async def create_user(user_id: int, first_name: str, username: str, topic_id: int):
    """Создать нового пользователя с привязкой к топику."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO users (user_id, first_name, username, topic_id)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, first_name, username, topic_id),
        )
        await db.commit()


async def get_user_by_topic(topic_id: int) -> dict | None:
    """Найти пользователя по ID топика."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE topic_id = ?", (topic_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
    return None


async def should_send_auto_reply(user_id: int) -> bool:
    """Проверить, нужно ли отправить авто-ответ (не чаще раза в день)."""
    user = await get_user(user_id)
    if not user or not user.get("last_auto_reply"):
        return True

    last = datetime.fromisoformat(user["last_auto_reply"])
    now = datetime.now(timezone.utc)
    return (now - last).total_seconds() > 86400  # 24 часа


async def update_auto_reply_time(user_id: int):
    """Обновить время последнего авто-ответа."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET last_auto_reply = ? WHERE user_id = ?",
            (now, user_id),
        )
        await db.commit()
