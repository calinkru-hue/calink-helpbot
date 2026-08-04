"""Маленький aiohttp-сервер рядом с polling-ботом.

Эндпоинты:
  GET /admin/db  — live-снапшот SQLite (Bearer-auth через ADMIN_DB_TOKENS)
  GET /health    — liveness для Railway

Живёт в том же event-loop, что и бот: поднимается из post_init,
останавливается из post_shutdown.
"""
import logging
import os
import sqlite3
import tempfile

from aiohttp import web

from config import ADMIN_DB_TOKENS, ADMIN_PORT
from database import DB_PATH

logger = logging.getLogger(__name__)


async def _admin_db_endpoint(request: web.Request) -> web.Response:
    """Отдать live-снапшот БД авторизованному вызывающему. У каждого коллеги
    свой токен в ADMIN_DB_TOKENS; отзыв = удалить токен из env."""
    if not ADMIN_DB_TOKENS:
        return web.Response(status=503, text="admin db access not configured")

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return web.Response(status=401, text="missing bearer token")
    token = auth[7:].strip()
    if token not in ADMIN_DB_TOKENS:
        logger.warning("Отклонён запрос /admin/db с неверным токеном")
        return web.Response(status=403, text="invalid token")

    # sqlite3 backup API копирует живую БД безопасно, пока бот пишет.
    # БД маленькая, поэтому читаем в память и удаляем temp-файл до ответа —
    # проще, чем городить on-eof cleanup (в aiohttp его и нет как API).
    fd, snapshot_path = tempfile.mkstemp(suffix=".db", prefix="calink_snap_")
    os.close(fd)
    try:
        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(snapshot_path)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
        with open(snapshot_path, "rb") as f:
            data = f.read()
    except Exception:
        logger.exception("Не удалось снять снапшот БД для /admin/db")
        return web.Response(status=500, text="snapshot failed")
    finally:
        try:
            os.unlink(snapshot_path)
        except OSError:
            pass

    logger.info(
        "📦 /admin/db снапшот отдан (префикс токена=%s, байт=%d)",
        token[:6], len(data),
    )

    return web.Response(
        body=data,
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Disposition": 'attachment; filename="support_bot.db"',
        },
    )


async def _health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def start_admin_server() -> web.AppRunner:
    """Поднять сервер на 0.0.0.0:ADMIN_PORT. Возвращает runner, чтобы
    вызывающий мог остановить его при shutdown."""
    app = web.Application()
    app.router.add_get("/admin/db", _admin_db_endpoint)
    app.router.add_get("/health", _health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", ADMIN_PORT)
    await site.start()
    logger.info("🌐 Admin-сервер слушает 0.0.0.0:%d", ADMIN_PORT)
    return runner
