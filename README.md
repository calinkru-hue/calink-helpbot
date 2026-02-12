# Telegram Support Bot

Бот-мост между клиентами и командой поддержки. Клиент пишет боту → бот создаёт топик в группе → саппорт отвечает в топике → бот пересылает ответ клиенту.

## Быстрый старт

### 1. Создать бота
- Открыть [@BotFather](https://t.me/BotFather) в Telegram
- `/newbot` → получить токен

### 2. Настроить группу саппорта
- Создать группу (supergroup)
- Включить Topics (Настройки → Topics)
- Добавить бота в группу **как администратора** с правами:
  - ✅ Manage Topics
  - ✅ Send Messages
- Узнать ID группы: добавить [@RawDataBot](https://t.me/RawDataBot), скопировать `chat.id`

### 3. Настроить .env
```bash
cp .env.example .env
```
Заполнить `BOT_TOKEN` и `SUPPORT_GROUP_ID` (с минусом: `-100...`).

### 4. Установить и запустить
```bash
pip install -r requirements.txt
python bot.py
```

## Деплой на Railway
1. Push в GitHub
2. Подключить репо в Railway
3. Добавить переменные `BOT_TOKEN` и `SUPPORT_GROUP_ID` в Settings → Variables
4. Railway задеплоит автоматически
