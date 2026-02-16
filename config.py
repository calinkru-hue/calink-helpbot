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

# Calink API
CALINK_API_URL = "https://calink.ru/api/hooks/support/user/info"
CALINK_API_SECRET = os.getenv(
    "CALINK_API_SECRET", "HE110_k3y_f0r_SUPp0rt_h00k"
)
