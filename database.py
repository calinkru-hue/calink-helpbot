import aiosqlite
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "support_bot.db")


async def init_db():
    """Создать таблицу users, если не существует."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT,
                username TEXT,
                topic_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
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
