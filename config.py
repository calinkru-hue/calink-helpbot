import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPPORT_GROUP_ID = int(os.getenv("SUPPORT_GROUP_ID", "0"))

WELCOME_MESSAGE_CALINK = (
    "👋 Здравствуйте! Это поддержка Calink.\n"
    "У вас вопрос по вашей странице calink.ru/{grub}?"
)

WELCOME_MESSAGE_DEFAULT = (
    "👋 Здравствуйте! Это поддержка Calink.\n"
    "Какой у вас вопрос?"
)

AUTO_REPLY_MESSAGE = "ok, скоро ответим!"

# Задержка перед авто-ответом (секунды)
AUTO_REPLY_DELAY = 5

# ─── Фидбек: «вопрос решён?» → оценка ────────

FEEDBACK_ENABLED = (os.getenv("FEEDBACK_ENABLED", "true").lower() != "false")

# Через сколько часов после последнего ответа саппорта спрашивать оценку.
# На dev ставь 0, чтобы не ждать.
FEEDBACK_DELAY_HOURS = float(os.getenv("FEEDBACK_DELAY_HOURS") or "2")

# Как часто тикает фоновый sweeper (минуты).
FEEDBACK_SWEEP_INTERVAL_MIN = int(os.getenv("FEEDBACK_SWEEP_INTERVAL_MIN") or "5")

# Тексты клиенту (по-русски — аудитория Calink русскоязычная)
RESOLUTION_PROMPT = (
    "Ваш вопрос решён?"
)

RATING_PROMPT = "Спасибо! Как оцените нашу помощь?"

FEEDBACK_THANK_YOU = (
    "Спасибо за оценку! Будут вопросы — просто напишите."
)

FEEDBACK_NOT_RESOLVED_ACK = (
    "Извините. Напишите здесь, что осталось нерешённым — разберёмся."
)

# Уведомления в топик саппорта
TOPIC_NOTICE_RESOLVED = "✅ Клиент подтвердил, что вопрос решён"
TOPIC_NOTICE_NOT_RESOLVED = "⚠️ Клиент говорит, что вопрос НЕ решён — нужен follow-up"

# Calink API
CALINK_API_URL = "https://calink.ru/api/hooks/support/user/info"
CALINK_API_SECRET = os.getenv(
    "CALINK_API_SECRET", "HE110_k3y_f0r_SUPp0rt_h00k"
)

# ─── Hard-reset юзера (dev/QA инструмент) ────

# Кодовое слово, отправленное в топик, полностью стирает юзера из БД и
# закрывает топик. Через `or`, а НЕ через getenv(name, default): если в
# Railway переменная задана пустой строкой, getenv вернёт "" вместо дефолта —
# на этом в zorion-helpbot уже спотыкались (пустой keyword матчит всё подряд).
RESET_KEYWORD = os.getenv("RESET_KEYWORD") or "!user_del!"

# ─── Admin HTTP server (снапшот БД) ──────────

# Railway подставляет PORT сам; локально — 8080.
ADMIN_PORT = int(os.getenv("PORT") or os.getenv("ADMIN_PORT") or "8080")

# Bearer-токены для GET /admin/db через запятую, один токен на человека.
# Пусто → эндпоинт отключён (отдаёт 503). Отзыв = удалить токен из env.
ADMIN_DB_TOKENS = frozenset(
    t.strip() for t in os.getenv("ADMIN_DB_TOKENS", "").split(",") if t.strip()
)
