"""Фоновый sweeper фидбека.

Через FEEDBACK_DELAY_HOURS часов после последнего ответа саппорта спрашиваем
клиента «Ваш вопрос решён?». Да → 5 эмодзи-оценок, Нет → извинение + алёрт в
топик. Только Telegram: email-канала у Calink нет.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from config import (
    FEEDBACK_DELAY_HOURS,
    FEEDBACK_SWEEP_INTERVAL_MIN,
    RESOLUTION_PROMPT,
)
from database import (
    users_due_for_feedback,
    mark_feedback_sent,
    save_feedback,
    log_event,
)

logger = logging.getLogger(__name__)

# От худшей к лучшей
EMOJIS = ["😡", "😕", "😐", "🙂", "😍"]


def build_yes_no_keyboard() -> InlineKeyboardMarkup:
    """Шаг 1: «Ваш вопрос решён?» [Да] [Нет]."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Да", callback_data="resolve:yes"),
        InlineKeyboardButton("❌ Нет", callback_data="resolve:no"),
    ]])


def build_rating_keyboard() -> InlineKeyboardMarkup:
    """Шаг 2: 5 эмодзи, callback_data='rate:1' … 'rate:5'."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(emoji, callback_data=f"rate:{i + 1}")
        for i, emoji in enumerate(EMOJIS)
    ]])


def emoji_for(rating: int) -> str:
    if 1 <= rating <= 5:
        return EMOJIS[rating - 1]
    return "❓"


async def feedback_sweeper(app):
    """Фоновая задача: раз в N минут искать юзеров, которым пора задать вопрос."""
    bot = app.bot
    interval_sec = max(60, FEEDBACK_SWEEP_INTERVAL_MIN * 60)
    logger.info(
        "⭐️ Feedback sweeper запущен (задержка=%sч, интервал=%dмин)",
        FEEDBACK_DELAY_HOURS, FEEDBACK_SWEEP_INTERVAL_MIN,
    )
    try:
        while True:
            try:
                await _sweep(bot)
            except Exception:
                logger.exception("Ошибка в feedback sweep")
            await asyncio.sleep(interval_sec)
    except asyncio.CancelledError:
        logger.info("⭐️ Feedback sweeper остановлен")
        raise


async def _sweep(bot):
    threshold = (
        datetime.now(timezone.utc) - timedelta(hours=FEEDBACK_DELAY_HOURS)
    ).isoformat()
    for row in await users_due_for_feedback(threshold):
        await _send_prompt(bot, row["user_id"], row["topic_id"])


async def _send_prompt(bot, user_id: int, topic_id: int):
    try:
        await bot.send_message(
            chat_id=user_id,
            text=RESOLUTION_PROMPT,
            reply_markup=build_yes_no_keyboard(),
            disable_web_page_preview=True,
        )
    except TelegramError as e:
        # Юзер заблокировал бота / удалил аккаунт — помечаем отправленным,
        # чтобы не долбиться вечно.
        logger.warning("Не удалось отправить вопрос об оценке user %d: %s", user_id, e)
        await mark_feedback_sent(user_id)
        await log_event(
            event_type="error",
            topic_id=topic_id,
            actor_type="system",
            text=str(e),
            extra={"where": "_send_prompt", "user_id": user_id},
        )
        return

    fb_id = await save_feedback(topic_id=topic_id, telegram_user_id=user_id)
    await mark_feedback_sent(user_id)
    await log_event(
        event_type="feedback_sent",
        topic_id=topic_id,
        direction="out",
        actor_type="bot",
        actor_id="bot",
        text=RESOLUTION_PROMPT,
        extra={"feedback_id": fb_id, "to_user_id": user_id},
    )
    logger.info("⭐️ Вопрос об оценке отправлен user %d", user_id)
