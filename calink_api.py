"""Клиент для Calink Support API."""
import logging

import httpx

from config import CALINK_API_URL, CALINK_API_SECRET

logger = logging.getLogger(__name__)

_HEADERS = {
    "Content-Type": "application/json",
    "X-Support-Secret": CALINK_API_SECRET,
}

# Таймаут на запрос — 5 секунд
_TIMEOUT = httpx.Timeout(5.0)


async def lookup_calink_user(telegram_id: int) -> dict | None:
    """
    Запросить информацию о пользователе Calink по Telegram ID.

    Возвращает dict с полями uid, name, grub, tariff или None если не найден.
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                CALINK_API_URL,
                headers=_HEADERS,
                json={"telegram": telegram_id},
            )

        if resp.status_code == 200:
            data = resp.json()
            logger.info("Calink user found: uid=%s, grub=%s", data.get("uid"), data.get("grub"))
            return data

        if resp.status_code == 404:
            logger.info("Calink user not found for telegram_id=%d", telegram_id)
            return None

        logger.warning(
            "Calink API вернул %d: %s", resp.status_code, resp.text
        )
        return None

    except httpx.HTTPError:
        logger.exception("Ошибка запроса к Calink API для telegram_id=%d", telegram_id)
        return None


def format_user_card(calink_user: dict | None, username: str = "") -> str:
    """
    Сформировать текст информационной карточки пользователя.

    Если calink_user is None — пользователь не из Calink.
    """
    if calink_user is None:
        parts = ["⚠️ ПОЛЬЗОВАТЕЛЬ НЕ ИЗ CALINK"]
        if username:
            parts.append(f"@{username}")
        return "\n".join(parts)

    uid = calink_user.get("uid", "—")
    name = calink_user.get("name", "—")
    grub = calink_user.get("grub", "")
    tariff = calink_user.get("tariff", "—")

    lines = [
        f"UID: {uid}",
        name,
    ]
    if username:
        lines.append(f"@{username}")
    if grub:
        lines.append(f"calink.ru/{grub}")
        lines.append(f"https://calink.ru/app?as_user={uid}")
    lines.append(f"Тариф: {tariff}")

    return "\n".join(lines)
