import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from config import (
    BOT_TOKEN,
    SUPPORT_GROUP_ID,
    WELCOME_MESSAGE,
    AUTO_REPLY_MESSAGE,
    AUTO_REPLY_DELAY,
)
from database import (
    init_db,
    get_user,
    create_user,
    get_user_by_topic,
    should_send_auto_reply,
    update_auto_reply_time,
)

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# /start — приветствие клиента
# ─────────────────────────────────────────────
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /start от клиента."""
    await update.message.reply_text(WELCOME_MESSAGE)


# ─────────────────────────────────────────────
# Авто-ответ через 5 секунд (job callback)
# ─────────────────────────────────────────────
async def send_auto_reply(context: ContextTypes.DEFAULT_TYPE):
    """Отправить авто-ответ пользователю (вызывается по таймеру)."""
    user_id = context.job.data
    try:
        await context.bot.send_message(chat_id=user_id, text=AUTO_REPLY_MESSAGE)
        await update_auto_reply_time(user_id)
        logger.info(f"Авто-ответ отправлен user {user_id}")
    except Exception as e:
        logger.error(f"Ошибка авто-ответа для user {user_id}: {e}")


# ─────────────────────────────────────────────
# Сообщение от клиента → группа саппорта
# ─────────────────────────────────────────────
async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Клиент пишет боту → бот пересылает в топик группы саппорта."""
    message = update.message
    if not message:
        return

    user = message.from_user
    user_id = user.id
    first_name = user.first_name or ""
    username = user.username or ""

    # Найти или создать топик для этого пользователя
    db_user = await get_user(user_id)
    is_new_user = db_user is None

    if is_new_user:
        # Создаём новый топик в группе саппорта
        topic_name = first_name
        if username:
            topic_name += f" @{username}"

        try:
            forum_topic = await context.bot.create_forum_topic(
                chat_id=SUPPORT_GROUP_ID,
                name=topic_name,
            )
            topic_id = forum_topic.message_thread_id

            await create_user(user_id, first_name, username, topic_id)
            logger.info(
                f"Создан топик '{topic_name}' (id={topic_id}) для user {user_id}"
            )
        except Exception as e:
            logger.error(f"Ошибка создания топика: {e}")
            await message.reply_text(
                "Произошла ошибка. Пожалуйста, попробуйте позже."
            )
            return
    else:
        topic_id = db_user["topic_id"]

    # Пересылаем сообщение в топик (copy_message сохраняет тип контента)
    try:
        await message.copy(
            chat_id=SUPPORT_GROUP_ID,
            message_thread_id=topic_id,
        )
    except Exception as e:
        logger.error(f"Ошибка пересылки в топик: {e}")
        await message.reply_text(
            "Не удалось отправить сообщение. Попробуйте позже."
        )
        return

    # Авто-ответ: через 5 секунд, не чаще раза в день
    # Проверяем, нет ли уже запланированного авто-ответа для этого пользователя
    job_name = f"auto_reply_{user_id}"
    existing_jobs = context.job_queue.get_jobs_by_name(job_name)
    if not existing_jobs:
        # Проверяем, нужно ли отправлять (не чаще раза в день)
        if await should_send_auto_reply(user_id):
            context.job_queue.run_once(
                send_auto_reply,
                when=AUTO_REPLY_DELAY,
                data=user_id,
                name=job_name,
            )


# ─────────────────────────────────────────────
# Сообщение от саппорта → клиенту
# (только если это Reply на сообщение бота)
# ─────────────────────────────────────────────
async def handle_support_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Саппорт отвечает на сообщение бота в топике → бот пересылает клиенту."""
    message = update.message
    if not message:
        return

    # Игнорируем сообщения от самого бота
    if message.from_user and message.from_user.id == context.bot.id:
        return

    # Игнорируем сообщения не в топиках (General thread)
    if not message.message_thread_id:
        return

    # ✅ Только если это Reply на сообщение бота
    if not message.reply_to_message:
        return  # Обычное сообщение в топике — внутреннее обсуждение, не пересылаем

    replied_to = message.reply_to_message

    # Игнорируем reply на служебное сообщение о создании топика
    # (в forum topics ВСЕ сообщения автоматически reply на это сообщение)
    if replied_to.forum_topic_created:
        return

    # Проверяем, что отвечают на сообщение бота (пересланное от клиента)
    if not replied_to.from_user or replied_to.from_user.id != context.bot.id:
        return  # Reply на сообщение другого саппорта — не пересылаем

    topic_id = message.message_thread_id

    # Найти клиента по topic_id
    db_user = await get_user_by_topic(topic_id)
    if db_user is None:
        return

    # Пересылаем ответ клиенту
    try:
        await message.copy(chat_id=db_user["user_id"])
    except Exception as e:
        logger.error(f"Ошибка пересылки клиенту {db_user['user_id']}: {e}")


# ─────────────────────────────────────────────
# Запуск бота
# ─────────────────────────────────────────────
async def post_init(application: Application):
    """Инициализация БД при старте бота."""
    await init_db()
    logger.info("База данных инициализирована")


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не задан! Проверьте файл .env")
    if not SUPPORT_GROUP_ID:
        raise ValueError("SUPPORT_GROUP_ID не задан! Проверьте файл .env")

    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Команда /start — только в личке
    application.add_handler(
        CommandHandler("start", start_command, filters=filters.ChatType.PRIVATE)
    )

    # Сообщения от клиентов — любые сообщения в личке бота
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & ~filters.COMMAND,
            handle_user_message,
        )
    )

    # Сообщения от саппорта — сообщения в группе саппорта
    application.add_handler(
        MessageHandler(
            filters.Chat(SUPPORT_GROUP_ID) & ~filters.COMMAND & filters.IS_TOPIC_MESSAGE,
            handle_support_message,
        )
    )

    logger.info("Бот запущен")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
