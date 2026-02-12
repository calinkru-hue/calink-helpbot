import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPPORT_GROUP_ID = int(os.getenv("SUPPORT_GROUP_ID", "0"))

WELCOME_MESSAGE = (
    "👋 Здравствуйте! Это бот поддержки.\n\n"
    "Напишите ваш вопрос, и мы ответим как можно скорее."
)
