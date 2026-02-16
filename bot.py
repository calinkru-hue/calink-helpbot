import logging

from telegram import ReactionTypeEmoji, Update
from telegram.error import TelegramError
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
    save_message_mapping,
    get_client_message_id,
    delete_message_mapping,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ─── Helpers ─────────────────────────────────

def _is_from_bot(message, context) -> bool:
    """Проверить, отправлено ли сообщение самим ботом."""
    return message.from_user and message.from_user.id == context.bot.id


def _get_reply_target(message):
    """Вернуть replied_to сообщение, если это ответ на сообщение бота (не на создание топика)."""
    replied_to = message.reply_to_message
    if not replied_to:
        return None
    if replied_to.forum_topic_created:
        return None
    return replied_to


# ─── /start ──────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие клиента."""
    await update.message.reply_text(WELCOME_MESSAGE)


# ─── Авто-ответ (job callback) ───────────────

async def send_auto_reply(context: ContextTypes.DEFAULT_TYPE):
    """Отправить авто-ответ пользователю через N секунд."""
    user_id = context.job.data
    try:
        await context.bot.send_message(chat_id=user_id, text=AUTO_REPLY_MESSAGE)
        await update_auto_reply_time(user_id)
        logger.info("Авто-ответ отправлен user %d", user_id)
    except TelegramError:
        logger.exception("Ошибка авто-ответа для user %d", user_id)


# ─── Клиент → Группа саппорта ────────────────

async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Клиент пишет боту → бот пересылает в топик группы саппорта."""
    message = update.message
    if not message:
        return

    user = message.from_user
    user_id = user.id
    first_name = user.first_name or ""
    username = user.username or ""

    db_user = await get_user(user_id)

    if db_user is None:
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
            logger.info("Создан топик '%s' (id=%d) для user %d", topic_name, topic_id, user_id)
        except TelegramError:
            logger.exception("Ошибка создания топика для user %d", user_id)
            await message.reply_text("Произошла ошибка. Пожалуйста, попробуйте позже.")
            return
    else:
        topic_id = db_user["topic_id"]

    try:
        sent = await message.copy(
            chat_id=SUPPORT_GROUP_ID,
            message_thread_id=topic_id,
        )
        # Сохраняем маппинг: сообщение клиента в группе ↔ оригинал у клиента
        await save_message_mapping(
            group_message_id=sent.message_id,
            client_message_id=message.message_id,
            user_id=user_id,
            topic_id=topic_id,
        )
    except TelegramError:
        logger.exception("Ошибка пересылки в топик для user %d", user_id)
        await message.reply_text("Не удалось отправить сообщение. Попробуйте позже.")
        return

    # Авто-ответ: через N секунд, не чаще раза в день
    job_name = f"auto_reply_{user_id}"
    if not context.job_queue.get_jobs_by_name(job_name):
        if await should_send_auto_reply(user_id):
            context.job_queue.run_once(
                send_auto_reply,
                when=AUTO_REPLY_DELAY,
                data=user_id,
                name=job_name,
            )


# ─── Саппорт → Клиент (reply на сообщение бота) ─

async def handle_support_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Саппорт отвечает reply на сообщение бота → пересылка клиенту + ✅."""
    message = update.message
    if not message or _is_from_bot(message, context):
        return
    if not message.message_thread_id:
        return

    replied_to = _get_reply_target(message)
    if not replied_to:
        return  # Просто сообщение в топике → внутреннее обсуждение
    if not replied_to.from_user or replied_to.from_user.id != context.bot.id:
        return  # Reply на другого саппорта → не пересылаем

    topic_id = message.message_thread_id
    db_user = await get_user_by_topic(topic_id)
    if db_user is None:
        return

    try:
        sent = await message.copy(chat_id=db_user["user_id"])
        # Маппинг: сообщение саппорта в группе ↔ сообщение у клиента
        await save_message_mapping(
            group_message_id=message.message_id,
            client_message_id=sent.message_id,
            user_id=db_user["user_id"],
            topic_id=topic_id,
        )
        await message.set_reaction(reaction=[ReactionTypeEmoji("👍")])
    except TelegramError:
        logger.exception("Ошибка пересылки клиенту %d", db_user["user_id"])


# ─── Редактирование сообщения саппорта → обновление у клиента ─

async def handle_edited_support_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Саппорт редактирует сообщение → бот редактирует у клиента + ✏️."""
    message = update.edited_message
    if not message or _is_from_bot(message, context):
        return
    if not message.message_thread_id:
        return

    topic_id = message.message_thread_id
    db_user = await get_user_by_topic(topic_id)
    if db_user is None:
        return

    client_msg_id = await get_client_message_id(message.message_id, topic_id)
    if client_msg_id is None:
        return  # Это сообщение не пересылалось клиенту

    try:
        # Редактируем текстовое сообщение у клиента
        if message.text:
            await context.bot.edit_message_text(
                chat_id=db_user["user_id"],
                message_id=client_msg_id,
                text=message.text,
                entities=message.entities,
            )
        elif message.caption is not None:
            await context.bot.edit_message_caption(
                chat_id=db_user["user_id"],
                message_id=client_msg_id,
                caption=message.caption,
                caption_entities=message.caption_entities,
            )
        await message.set_reaction(reaction=[ReactionTypeEmoji("✍")])
        logger.info("Сообщение %d отредактировано у клиента %d", message.message_id, db_user["user_id"])
    except TelegramError:
        logger.exception("Ошибка редактирования у клиента %d", db_user["user_id"])


# ─── /del — удаление сообщения у клиента ─────

async def handle_del_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Reply на своё сообщение + /del →
    1. Удалить сообщение у клиента
    2. Удалить оригинальное сообщение саппорта в топике
    3. Удалить саму команду /del в топике
    """
    message = update.message
    if not message or not message.message_thread_id:
        return

    replied_to = _get_reply_target(message)
    if not replied_to:
        return

    # /del можно делать только на свои сообщения или на сообщения бота
    topic_id = message.message_thread_id
    db_user = await get_user_by_topic(topic_id)
    if db_user is None:
        return

    target_msg_id = replied_to.message_id
    client_msg_id = await get_client_message_id(target_msg_id, topic_id)

    if client_msg_id is None:
        # Сообщение не было переслано клиенту — просто удаляем /del
        try:
            await message.delete()
        except TelegramError:
            pass
        return

    errors = []

    # 1. Удаляем у клиента
    try:
        await context.bot.delete_message(
            chat_id=db_user["user_id"],
            message_id=client_msg_id,
        )
    except TelegramError as e:
        errors.append(f"клиент: {e}")
        logger.warning("Не удалось удалить сообщение у клиента %d: %s", db_user["user_id"], e)

    # 2. Удаляем оригинальное сообщение в топике
    try:
        await context.bot.delete_message(
            chat_id=SUPPORT_GROUP_ID,
            message_id=target_msg_id,
        )
    except TelegramError as e:
        errors.append(f"топик: {e}")
        logger.warning("Не удалось удалить сообщение %d в топике: %s", target_msg_id, e)

    # 3. Удаляем команду /del
    try:
        await message.delete()
    except TelegramError as e:
        errors.append(f"/del: {e}")

    # Чистим маппинг
    await delete_message_mapping(target_msg_id, topic_id)

    if not errors:
        logger.info("Удалено сообщение %d у клиента %d", target_msg_id, db_user["user_id"])
    else:
        logger.warning("Частичное удаление msg %d: %s", target_msg_id, "; ".join(errors))


# ─── Запуск ──────────────────────────────────

async def post_init(application: Application):
    """Инициализация БД при старте."""
    await init_db()


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не задан! Проверьте файл .env")
    if not SUPPORT_GROUP_ID:
        raise ValueError("SUPPORT_GROUP_ID не задан! Проверьте файл .env")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # Личка: /start
    app.add_handler(
        CommandHandler("start", start_command, filters=filters.ChatType.PRIVATE)
    )

    # Личка: любое сообщение клиента
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & ~filters.COMMAND,
            handle_user_message,
        )
    )

    # Группа: /del (должен быть ДО общего обработчика)
    app.add_handler(
        CommandHandler(
            "del",
            handle_del_command,
            filters=filters.Chat(SUPPORT_GROUP_ID) & filters.IS_TOPIC_MESSAGE,
        )
    )

    # Группа: reply-ответ саппорта → клиенту (только новые, не edited)
    app.add_handler(
        MessageHandler(
            filters.Chat(SUPPORT_GROUP_ID) & ~filters.COMMAND & filters.IS_TOPIC_MESSAGE & filters.UpdateType.MESSAGE,
            handle_support_message,
        )
    )

    # Группа: редактирование сообщения саппорта
    app.add_handler(
        MessageHandler(
            filters.Chat(SUPPORT_GROUP_ID) & filters.IS_TOPIC_MESSAGE & filters.UpdateType.EDITED_MESSAGE,
            handle_edited_support_message,
        )
    )

    logger.info("Бот запущен")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
