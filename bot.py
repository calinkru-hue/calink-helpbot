import asyncio
import logging
import re
import traceback

from telegram import ReactionTypeEmoji, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from config import (
    BOT_TOKEN,
    SUPPORT_GROUP_ID,
    WELCOME_MESSAGE_CALINK,
    WELCOME_MESSAGE_DEFAULT,
    AUTO_REPLY_MESSAGE,
    AUTO_REPLY_DELAY,
    RESET_KEYWORD,
    FEEDBACK_ENABLED,
    RATING_PROMPT,
    FEEDBACK_THANK_YOU,
    FEEDBACK_NOT_RESOLVED_ACK,
    TOPIC_NOTICE_RESOLVED,
    TOPIC_NOTICE_NOT_RESOLVED,
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
    mark_calink_user,
    save_card_message_id,
    log_event,
    delete_user_by_topic,
    mark_support_reply,
    mark_user_message,
    get_unanswered_feedback,
    mark_feedback_yes_clicked,
    mark_feedback_no_resolved,
    update_feedback_rating,
)
from calink_api import lookup_calink_user, format_user_card
from admin_server import start_admin_server
from feedback import (
    feedback_sweeper,
    build_rating_keyboard,
    emoji_for,
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


# ─── Хелперы event_log ───────────────────────

def _describe_media(message) -> tuple[str | None, str | None, str]:
    """Вернуть (media_type, file_id, placeholder) для сообщения TG.
    Placeholder пустой, если медиа нет. Сами файлы не скачиваем — по file_id
    оригинал всегда можно достать через Bot API getFile."""
    if message.photo:
        return ("photo", message.photo[-1].file_id, "[photo]")
    if message.video:
        dur = getattr(message.video, "duration", None)
        return ("video", message.video.file_id, f"[video {dur}s]" if dur else "[video]")
    if message.voice:
        dur = getattr(message.voice, "duration", None)
        return ("voice", message.voice.file_id, f"[voice {dur}s]" if dur else "[voice]")
    if message.audio:
        return ("audio", message.audio.file_id, "[audio]")
    if message.document:
        size = (message.document.file_size or 0) / 1024 / 1024
        name = message.document.file_name or "file"
        return ("document", message.document.file_id, f"[document {name} {size:.1f}MB]")
    if message.sticker:
        em = message.sticker.emoji or ""
        return ("sticker", message.sticker.file_id, f"[sticker {em}]".strip())
    if message.animation:
        return ("animation", message.animation.file_id, "[animation]")
    if message.video_note:
        return ("video_note", message.video_note.file_id, "[video_note]")
    return (None, None, "")


def _message_to_log_payload(message) -> tuple[str, str | None, str | None]:
    """Превратить сообщение TG в (text, media_type, file_id) для event_log.
    Если есть и медиа, и caption — склеивает placeholder с подписью."""
    media_type, file_id, placeholder = _describe_media(message)
    if message.text:
        return (message.text, media_type, file_id)
    if message.caption:
        if placeholder:
            return (f"{placeholder}: {message.caption}", media_type, file_id)
        return (message.caption, None, None)
    return (placeholder, media_type, file_id)


def _actor_name(first_name: str | None, username: str | None) -> str:
    name = (first_name or "").strip()
    if username:
        name = f"{name} @{username}".strip() if name else f"@{username}"
    return name


async def _send_and_pin_card(context, topic_id: int, card_text: str) -> int | None:
    """Отправить карточку в топик и запинить. Вернуть message_id."""
    try:
        card_msg = await context.bot.send_message(
            chat_id=SUPPORT_GROUP_ID,
            message_thread_id=topic_id,
            text=card_text,
            disable_web_page_preview=True,
        )
        await context.bot.pin_chat_message(
            chat_id=SUPPORT_GROUP_ID,
            message_id=card_msg.message_id,
        )
        return card_msg.message_id
    except TelegramError:
        logger.exception("Ошибка отправки/пина карточки в топик %d", topic_id)
        return None


# ─── /start ──────────────────────────────────

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие клиента — персонализированное для пользователей Calink."""
    user_id = update.message.from_user.id
    calink_user = await lookup_calink_user(user_id)

    if calink_user and calink_user.get("grub"):
        text = WELCOME_MESSAGE_CALINK.format(grub=calink_user["grub"])
    else:
        text = WELCOME_MESSAGE_DEFAULT

    await update.message.reply_text(text, disable_web_page_preview=True)

    # Топика у нового юзера ещё может не быть — тогда topic_id останется NULL.
    db_user = await get_user(user_id)
    await log_event(
        event_type="bot_message",
        topic_id=db_user["topic_id"] if db_user else None,
        direction="out",
        actor_type="bot",
        actor_id="bot",
        text=text,
        extra={"trigger": "/start", "to_user_id": user_id},
    )


# ─── Авто-ответ (job callback) ───────────────

async def send_auto_reply(context: ContextTypes.DEFAULT_TYPE):
    """Отправить авто-ответ пользователю через N секунд."""
    user_id = context.job.data
    try:
        await context.bot.send_message(chat_id=user_id, text=AUTO_REPLY_MESSAGE, disable_web_page_preview=True)
        await update_auto_reply_time(user_id)
        logger.info("Авто-ответ отправлен user %d", user_id)
        db_user = await get_user(user_id)
        await log_event(
            event_type="bot_message",
            topic_id=db_user["topic_id"] if db_user else None,
            direction="out",
            actor_type="bot",
            actor_id="bot",
            text=AUTO_REPLY_MESSAGE,
            extra={"trigger": "auto_reply", "to_user_id": user_id},
        )
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
        # ── Новый пользователь: создаём топик + карточку ──
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
            await log_event(
                event_type="topic_created",
                topic_id=topic_id,
                direction="system",
                actor_type="system",
                actor_id=user_id,
                actor_name=_actor_name(first_name, username),
                text=topic_name,
            )
        except TelegramError:
            logger.exception("Ошибка создания топика для user %d", user_id)
            await message.reply_text("Произошла ошибка. Пожалуйста, попробуйте позже.")
            return

        # Карточка пользователя
        calink_user = await lookup_calink_user(user_id)
        card_text = format_user_card(calink_user, username)
        card_id = await _send_and_pin_card(context, topic_id, card_text)

        if card_id:
            if calink_user:
                await mark_calink_user(user_id, card_id)
            else:
                await save_card_message_id(user_id, card_id)
    else:
        topic_id = db_user["topic_id"]

        # ── Существующий не-Calink пользователь: перепроверяем ──
        if not db_user.get("is_calink_user"):
            calink_user = await lookup_calink_user(user_id)
            if calink_user:
                # Удаляем старую карточку
                old_card_id = db_user.get("card_message_id")
                if old_card_id:
                    try:
                        await context.bot.delete_message(
                            chat_id=SUPPORT_GROUP_ID,
                            message_id=old_card_id,
                        )
                    except TelegramError:
                        logger.warning("Не удалось удалить старую карточку %s", old_card_id)

                # Новая карточка с данными Calink
                card_text = format_user_card(calink_user, username)
                card_id = await _send_and_pin_card(context, topic_id, card_text)
                if card_id:
                    await mark_calink_user(user_id, card_id)
                logger.info("User %d найден в Calink, карточка обновлена", user_id)

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
        text_for_log, media_type, file_id = _message_to_log_payload(message)
        await log_event(
            event_type="client_message",
            topic_id=topic_id,
            direction="in",
            actor_type="client",
            actor_id=user_id,
            actor_name=_actor_name(first_name, username),
            text=text_for_log,
            media_type=media_type,
            media_file_id=file_id,
            tg_message_id=message.message_id,
        )
        # Последнее слово за клиентом → sweeper не будет просить оценку,
        # пока саппорт не ответит.
        await mark_user_message(user_id)
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
        text_for_log, media_type, file_id = _message_to_log_payload(message)
        sender = message.from_user
        await log_event(
            event_type="support_reply",
            topic_id=topic_id,
            direction="out",
            actor_type="support",
            actor_id=sender.id if sender else None,
            actor_name=_actor_name(
                sender.first_name if sender else "",
                sender.username if sender else "",
            ),
            text=text_for_log,
            media_type=media_type,
            media_file_id=file_id,
            tg_message_id=message.message_id,
            extra={"to_user_id": db_user["user_id"]},
        )
        # Перезапускаем таймер фидбека: sweeper спросит оценку через
        # FEEDBACK_DELAY_HOURS после этого момента.
        await mark_support_reply(db_user["user_id"])
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


# ─── Фидбек: обработка кнопок ────────────────

async def _log_bot_dm(user_id: int, topic_id: int | None, text: str) -> None:
    """Записать исходящее сообщение бота клиенту как bot_message."""
    await log_event(
        event_type="bot_message",
        topic_id=topic_id,
        direction="out",
        actor_type="bot",
        actor_id="bot",
        text=text,
        extra={"to_user_id": user_id},
    )


async def handle_resolve_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 1: клиент нажал Да или Нет на «Ваш вопрос решён?»."""
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("resolve:"):
        return
    await query.answer()

    answer = query.data.split(":", 1)[1]
    if answer not in ("yes", "no"):
        return

    user_id = query.from_user.id
    fb = await get_unanswered_feedback(user_id)

    if answer == "yes":
        if fb is not None:
            posted = await mark_feedback_yes_clicked(fb["id"])
            if posted:
                await log_event(
                    event_type="yes_clicked",
                    topic_id=fb["topic_id"],
                    direction="in",
                    actor_type="client",
                    actor_id=user_id,
                    actor_name=_actor_name(
                        query.from_user.first_name, query.from_user.username,
                    ),
                    extra={"feedback_id": fb["id"]},
                )
                try:
                    await context.bot.send_message(
                        chat_id=SUPPORT_GROUP_ID,
                        message_thread_id=fb["topic_id"],
                        text=TOPIC_NOTICE_RESOLVED,
                    )
                except TelegramError:
                    logger.exception(
                        "Не удалось написать в топик %d о решении", fb["topic_id"]
                    )
        # Показываем шаг 2 даже если строки нет (например, после reset) —
        # для клиента флоу остаётся связным.
        try:
            await query.edit_message_text(
                RATING_PROMPT,
                reply_markup=build_rating_keyboard(),
            )
            await _log_bot_dm(user_id, fb["topic_id"] if fb else None, RATING_PROMPT)
        except TelegramError:
            logger.exception("Не удалось показать клавиатуру оценок")
        return

    # answer == "no"
    if fb is None:
        try:
            await query.edit_message_text(FEEDBACK_NOT_RESOLVED_ACK)
        except TelegramError:
            pass
        return

    if not await mark_feedback_no_resolved(fb["id"]):
        # Идемпотентность: клиент уже отвечал.
        try:
            await query.edit_message_text(FEEDBACK_NOT_RESOLVED_ACK)
        except TelegramError:
            pass
        return

    await log_event(
        event_type="no_clicked",
        topic_id=fb["topic_id"],
        direction="in",
        actor_type="client",
        actor_id=user_id,
        actor_name=_actor_name(
            query.from_user.first_name, query.from_user.username,
        ),
        extra={"feedback_id": fb["id"]},
    )

    try:
        await context.bot.send_message(
            chat_id=SUPPORT_GROUP_ID,
            message_thread_id=fb["topic_id"],
            text=TOPIC_NOTICE_NOT_RESOLVED,
        )
    except TelegramError:
        logger.exception("Не удалось написать в топик %d о нерешённом", fb["topic_id"])

    try:
        await query.edit_message_text(FEEDBACK_NOT_RESOLVED_ACK)
        await _log_bot_dm(user_id, fb["topic_id"], FEEDBACK_NOT_RESOLVED_ACK)
    except TelegramError:
        pass


async def handle_feedback_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Шаг 2: клиент нажал одну из 5 эмодзи-оценок."""
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("rate:"):
        return
    await query.answer()

    try:
        rating = int(query.data.split(":", 1)[1])
    except ValueError:
        return
    if rating < 1 or rating > 5:
        return

    user_id = query.from_user.id
    fb = await get_unanswered_feedback(user_id)

    # Клавиатуру гасим и благодарим всегда — даже если строку не нашли.
    if fb is None or not await update_feedback_rating(fb["id"], rating):
        try:
            await query.edit_message_text(FEEDBACK_THANK_YOU)
        except TelegramError:
            pass
        return

    await log_event(
        event_type="rated",
        topic_id=fb["topic_id"],
        direction="in",
        actor_type="client",
        actor_id=user_id,
        actor_name=_actor_name(
            query.from_user.first_name, query.from_user.username,
        ),
        extra={"feedback_id": fb["id"], "rating": rating},
    )

    try:
        await context.bot.send_message(
            chat_id=SUPPORT_GROUP_ID,
            message_thread_id=fb["topic_id"],
            text=f"⭐️ Оценка {emoji_for(rating)} ({rating}/5)",
        )
    except TelegramError:
        logger.exception("Не удалось написать оценку в топик %d", fb["topic_id"])

    try:
        await query.edit_message_text(FEEDBACK_THANK_YOU)
        await _log_bot_dm(user_id, fb["topic_id"], FEEDBACK_THANK_YOU)
    except TelegramError:
        pass


# ─── Hard-reset юзера по кодовому слову ──────

async def handle_reset_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кодовое слово в топике (по умолчанию !user_del!) → полностью стереть
    юзера из БД и закрыть топик. Инструмент для QA: позволяет пройти онбординг
    «с чистого листа» — при следующем сообщении бот увидит юзера впервые.

    Что делает:
      1. удаляет само сообщение-триггер (чтобы не мозолило глаза в топике)
      2. каскадом стирает users / messages / event_log по topic_id
      3. снимает pending-джобу авто-ответа (иначе она выстрелит уже после
         удаления юзера и запишет осиротевший bot_message)
      4. закрывает форум-топик — он уезжает в архив

    В event_log ничего не пишет: reset не должен попадать в аналитику.
    """
    message = update.message
    if not message or not message.message_thread_id:
        return

    topic_id = message.message_thread_id

    # Сначала убираем триггер — даже если дальше что-то упадёт.
    try:
        await message.delete()
    except TelegramError:
        logger.warning("Не удалось удалить сообщение-триггер reset в топике %d", topic_id)

    user_id = await delete_user_by_topic(topic_id)

    if user_id is None:
        logger.info("Reset в топике %d: юзер не найден в БД, чистить нечего", topic_id)
    else:
        # Снимаем запланированный авто-ответ для удалённого юзера.
        for job in context.job_queue.get_jobs_by_name(f"auto_reply_{user_id}"):
            job.schedule_removal()

    try:
        await context.bot.close_forum_topic(
            chat_id=SUPPORT_GROUP_ID,
            message_thread_id=topic_id,
        )
    except TelegramError:
        logger.warning("Не удалось закрыть топик %d", topic_id)

    logger.info("✅ Reset выполнен: топик=%d, user_id=%s", topic_id, user_id)


# ─── Внутренняя переписка саппортов (только лог) ─

async def handle_support_internal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log-only хендлер: любое сообщение в топике, которое НЕ является reply на
    сообщение бота (те обрабатывает handle_support_message и логирует как
    support_reply). Никуда не пересылает — только пишет в event_log.

    Живёт в handler-группе 1, поэтому срабатывает ПОСЛЕ группы 0 для того же
    апдейта и не мешает основному роутингу."""
    message = update.message
    if not message or _is_from_bot(message, context):
        return
    if not message.message_thread_id:
        return

    # Пропускаем кодовое слово reset: оно живёт в группе 0 (handle_reset_keyword),
    # которая каскадом чистит event_log по topic_id. Логирование здесь, в группе 1,
    # выполнилось бы ПОСЛЕ каскада и оставило осиротевшую строку на уже стёртый
    # топик. Точно этот баг ловили в zorion-helpbot.
    if message.text and message.text == RESET_KEYWORD:
        return

    # Reply на сообщение бота → это ответ клиенту, уже залогирован в группе 0.
    replied_to = _get_reply_target(message)
    if replied_to and replied_to.from_user and replied_to.from_user.id == context.bot.id:
        return

    text_for_log, media_type, file_id = _message_to_log_payload(message)
    sender = message.from_user
    await log_event(
        event_type="support_internal",
        topic_id=message.message_thread_id,
        direction="internal",
        actor_type="support",
        actor_id=sender.id if sender else None,
        actor_name=_actor_name(
            sender.first_name if sender else "",
            sender.username if sender else "",
        ),
        text=text_for_log,
        media_type=media_type,
        media_file_id=file_id,
        tg_message_id=message.message_id,
    )


# ─── Глобальный обработчик ошибок ────────────

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Ловит любое упавшее исключение в хендлерах и пишет его в event_log."""
    logger.exception("Необработанная ошибка в хендлере", exc_info=context.error)

    topic_id = None
    if isinstance(update, Update) and update.effective_message:
        topic_id = update.effective_message.message_thread_id

    await log_event(
        event_type="error",
        topic_id=topic_id,
        direction="system",
        actor_type="system",
        text=str(context.error),
        extra={
            "where": "global_error_handler",
            "traceback": "".join(
                traceback.format_exception(
                    type(context.error), context.error, context.error.__traceback__
                )
            )[-4000:] if context.error else None,
        },
    )


# ─── Запуск ──────────────────────────────────

_admin_runner = None
_feedback_task = None


async def post_init(application: Application):
    """Инициализация БД + подъём admin-сервера и feedback-sweeper'а."""
    global _admin_runner, _feedback_task
    await init_db()
    try:
        _admin_runner = await start_admin_server()
    except Exception:
        logger.exception("Не удалось поднять admin-сервер — бот продолжает работу")

    if FEEDBACK_ENABLED:
        # Ссылку на таск держим в модульной переменной: иначе сборщик мусора
        # может прибить его на полуслове.
        _feedback_task = asyncio.create_task(feedback_sweeper(application))
    else:
        logger.info("Feedback sweeper выключен (FEEDBACK_ENABLED=false)")


async def post_shutdown(application: Application):
    """Корректно остановить sweeper и admin-сервер."""
    global _admin_runner, _feedback_task
    if _feedback_task and not _feedback_task.done():
        _feedback_task.cancel()
        try:
            await _feedback_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Ошибка остановки feedback sweeper")

    if _admin_runner is not None:
        try:
            await _admin_runner.cleanup()
        except Exception:
            logger.exception("Ошибка остановки admin-сервера")


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN не задан! Проверьте файл .env")
    if not SUPPORT_GROUP_ID:
        raise ValueError("SUPPORT_GROUP_ID не задан! Проверьте файл .env")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

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

    # Группа: кодовое слово hard-reset. Должен быть ДО handle_support_message —
    # внутри группы 0 обрабатывает первый совпавший хендлер.
    app.add_handler(
        MessageHandler(
            filters.Chat(SUPPORT_GROUP_ID)
            & filters.IS_TOPIC_MESSAGE
            & filters.UpdateType.MESSAGE
            & filters.Regex(rf"^{re.escape(RESET_KEYWORD)}$"),
            handle_reset_keyword,
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

    # Группа 1: log-only логгер внутренней переписки саппортов.
    # Отдельная handler-группа, чтобы срабатывать после основного роутинга
    # (группа 0) для того же апдейта, а не вместо него.
    app.add_handler(
        MessageHandler(
            filters.Chat(SUPPORT_GROUP_ID) & ~filters.COMMAND & filters.IS_TOPIC_MESSAGE & filters.UpdateType.MESSAGE,
            handle_support_internal,
        ),
        group=1,
    )

    # Личка: кнопки фидбека
    app.add_handler(
        CallbackQueryHandler(handle_resolve_callback, pattern=r"^resolve:(yes|no)$")
    )
    app.add_handler(
        CallbackQueryHandler(handle_feedback_rating, pattern=r"^rate:[1-5]$")
    )

    app.add_error_handler(global_error_handler)

    logger.info("Бот запущен")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
