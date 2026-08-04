# PROJECT BIBLE — Calink Support Bot

## Что это?

Telegram-бот поддержки для сервиса [Calink](https://calink.ru). Работает как мост между клиентами и командой саппорта.

**Клиент** пишет боту в личку → **бот** создаёт топик в группе саппорта и пересылает туда → **саппорт** отвечает reply на сообщение бота → **бот** отправляет ответ клиенту.

---

## Стек

| Компонент | Технология |
|-----------|------------|
| Язык | Python 3.11+ |
| Telegram API | `python-telegram-bot[all]` v21+ |
| База данных | SQLite (`aiosqlite`) |
| HTTP-клиент | `httpx` (async, для Calink API) |
| HTTP-сервер | `aiohttp` (`/admin/db`, `/health`) |
| Конфигурация | `python-dotenv` (.env файл) |
| Хостинг | [Railway.app](https://railway.app) |
| Git | GitHub → `calinkru-hue/calink-helpbot` |

---

## Структура файлов

```
├── bot.py             # Главный файл: хендлеры, запуск бота
├── database.py        # SQLite: таблицы users, messages, event_log + log_event()
├── admin_server.py    # aiohttp-сервер: GET /admin/db (снапшот БД), GET /health
├── calink_api.py      # Клиент для Calink API (поиск юзера по telegram_id)
├── config.py          # Конфигурация из .env + текстовые константы
├── scripts/
│   └── fetch_db.sh    # Скачать снапшот БД по Bearer-токену
├── requirements.txt   # Зависимости
├── Procfile           # Команда запуска для Railway
├── .env               # Секреты (НЕ в git!)
└── .env.example       # Шаблон .env
```

---

## Переменные окружения (.env)

| Переменная | Описание | Пример |
|------------|----------|--------|
| `BOT_TOKEN` | Токен от @BotFather | `123456:ABC-DEF...` |
| `SUPPORT_GROUP_ID` | ID группы саппорта (с `-100`) | `-1001234567890` |
| `CALINK_API_SECRET` | Секрет для Calink API (опционально) | `HE110_k3y_f0r_SUPp0rt_h00k` |
| `ADMIN_DB_TOKENS` | Bearer-токены для `GET /admin/db` через запятую (пусто = эндпоинт выключен, 503) | `tok_alice,tok_bob` |
| `ADMIN_PORT` | Порт admin-сервера. На Railway подставляется через `PORT` | `8080` |

---

## Как работает бот

### Новый клиент пишет боту

1. `/start` → бот шлёт приветствие (персонализированное если юзер есть в Calink)
2. Первое сообщение → бот создаёт **топик** в группе саппорта
3. Бот запрашивает **Calink API** по telegram_id юзера
4. В топике появляется **карточка** (UID, имя, ссылка, тариф) и **пинится**
5. Сообщение клиента пересылается в топик
6. Через **5 секунд** бот шлёт авто-ответ: «ok, скоро ответим!» (max 1 раз в день)

### Саппорт отвечает клиенту

- **Reply на сообщение бота** в топике → пересылается клиенту + 👍 реакция
- **Просто сообщение** в топике → внутреннее обсуждение, клиент НЕ видит

### Редактирование и удаление

- **Редактирование**: саппорт редактирует своё сообщение → обновляется у клиента + ✍ реакция
- **Удаление**: reply на своё сообщение + `/del` → удалится у клиента + в топике (оба сообщения + команда)

### Карточка пользователя

Если юзер **есть в Calink**:
```
UID: 123
Иван Иванов
@username
calink.ru/ivan
https://calink.ru/app?as_user=123
Тариф: pro
```

Если **нет в Calink**:
```
⚠️ ПОЛЬЗОВАТЕЛЬ НЕ ИЗ CALINK
@username
```
→ При каждом новом сообщении бот перепроверяет API и обновляет карточку если юзер появился.

---

## База данных (SQLite)

### Таблица `users`
| Поле | Тип | Описание |
|------|-----|----------|
| `user_id` | INTEGER PK | Telegram user ID |
| `first_name` | TEXT | Имя из Telegram |
| `username` | TEXT | @username |
| `topic_id` | INTEGER | ID топика в группе саппорта |
| `is_calink_user` | INTEGER | 1 = найден в Calink |
| `card_message_id` | INTEGER | ID запиненной карточки |
| `last_auto_reply` | TEXT | ISO timestamp последнего авто-ответа |

### Таблица `messages`
| Поле | Тип | Описание |
|------|-----|----------|
| `group_message_id` | INTEGER | ID сообщения в группе |
| `client_message_id` | INTEGER | ID сообщения у клиента |
| `user_id` | INTEGER | Telegram user ID |
| `topic_id` | INTEGER | ID топика |

### Таблица `event_log` (v1.6.0)

Плоский audit-trail всей переписки. Цель — дать возможность реконструировать любой диалог и считать метрики, не листая топики глазами. Схема **намеренно 1-в-1 с `zorion-helpbot`**, чтобы один и тот же аналитический SQL работал на обоих ботах.

| Поле | Описание |
|------|----------|
| `topic_id` | Привязка к форум-топику. NULL для событий вне топика (например `/start` до создания топика) |
| `channel` | Всегда `telegram` (в Calink нет других каналов) |
| `event_type` | См. словарь ниже |
| `direction` | `in` (от клиента) / `out` (саппорт→клиенту, бот→клиенту) / `internal` (саппорт↔саппорт) / `system` |
| `actor_type` | `client` / `support` / `bot` / `system` |
| `actor_id`, `actor_name` | TG user_id и имя вида «First @username» |
| `text` | Тело сообщения. Для медиа — placeholder: `[photo]`, `[voice 23s]`, `[document file.pdf 1.2MB]` |
| `media_type`, `media_file_id` | Тип медиа + `file_id`, по которому оригинал качается через Bot API `getFile` |
| `tg_message_id` | Привязка к Telegram message_id |
| `email_message_id` | Всегда NULL — оставлено для совместимости схемы с zorion-helpbot |
| `extra_json` | Доп.данные: `{"to_user_id": 42}`, `{"trigger": "auto_reply"}`, `{"traceback": "..."}` |
| `created_at` | Время вставки (UTC) |

Словарь `event_type`:
- **Сообщения:** `client_message`, `support_reply` (reply саппорта на сообщение бота → ушло клиенту), `support_internal` (саппорт↔саппорт в топике, только лог), `bot_message` (автоответы в DM: `/start`-приветствие и «ok, скоро ответим!»)
- **Lifecycle:** `topic_created`
- **Ошибки:** `error` (`extra.where`, `extra.traceback`)

`log_event(...)` в `database.py` — центральная утилита, оборачивает INSERT в try/except: **никогда не поднимает исключение**, падение лога не ломает основной флоу. Хукнута из всех хендлеров `bot.py`.

`support_internal` — отдельный log-only хендлер в **handler-группе 1** PTB: срабатывает после основного роутинга (группа 0) для того же апдейта, поэтому логирует внутреннюю переписку не мешая пересылке.

**Не логируются:** правки сообщений и `/del` — паритет с zorion-helpbot.

БД хранится на **Railway Volume** (`/data/support_bot.db`), локально — рядом с ботом.

⚠️ `DATA_DIR` в `database.py` берёт `/data` **только если каталог существует**, иначе кладёт БД рядом с ботом — то есть в эфемерную ФС контейнера, стирающуюся при каждом редеплое. Для маппинга ID это было терпимо, для архива переписок — нет. Volume обязателен.

---

## Удалённый доступ к БД (`admin_server.py`, v1.6.0)

Рядом с polling-ботом в том же asyncio event-loop крутится aiohttp-сервер на `0.0.0.0:$PORT`. Поднимается из `post_init`, останавливается из `post_shutdown`. Если сервер не поднялся — бот **продолжает работать** (ошибка только в лог).

- `GET /admin/db` — live-снапшот SQLite через `sqlite3.backup()` (безопасно при работающем боте, без блокировок). Авторизация — Bearer против `ADMIN_DB_TOKENS`. Коды: `503` токены не настроены, `401` нет заголовка, `403` неверный токен, `200` + файл.
- `GET /health` — liveness.

Использование (коллега):

```bash
export CALINK_BOT_DB_TOKEN=<твой_токен>
bash scripts/fetch_db.sh          # → support_bot.db в текущей папке
sqlite3 support_bot.db            # → пиши SQL по event_log
```

Скрипт печатает быструю сводку: число юзеров, маппингов и распределение `event_type`.

Генерация токена: `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Один токен на человека, отзыв = удалить из env.

**Важно про PII:** в `event_log` лежат полные тексты сообщений клиентов и их имена. Токены раздавать только через secure-канал (1Password / Signal), снапшоты не коммитить и не пересылать в чаты.

---

## Деплой

### Окружения

| Окружение | Ветка Git | Описание |
|-----------|----------|----------|
| **prod** | `main` | Боевой бот, реальные пользователи |
| **dev** | `dev` | Тестовый бот, отдельная группа |

У каждого окружения свой `BOT_TOKEN` и `SUPPORT_GROUP_ID` в Railway Variables.

### Флоу деплоя

```
1. Код → git push origin dev     → Railway auto-deploy → dev-бот
2. Тестируем на dev
3. git checkout main && git merge dev && git push origin main → prod
```

**Никогда не пушим напрямую в `main`** — только через merge из `dev`.

### Railway

- Каждый environment подключён к GitHub и деплоится автоматически
- Volume `/data/` хранит SQLite базу (переживает редеплои)
- `Procfile`: `web: python3 bot.py`

### Требования к группе саппорта

1. Должна быть **supergroup** (не просто группа)
2. **Topics** включены (Settings → Topics)
3. Бот добавлен как **админ** с правами: Manage Topics, Send Messages, Delete Messages, Pin Messages

---

## Calink API

```
POST https://calink.ru/api/hooks/support/user/info
Header: X-Support-Secret: <секрет>
Body: {"telegram": 123456789}
```

Ответ 200: `{"uid": 123, "name": "...", "grub": "ivan", "tariff": "pro"}`
Ответ 404: пользователь не найден

---

## Локальная разработка

```bash
# 1. Клонировать
git clone https://github.com/calinkru-hue/calink-helpbot.git
cd calink-helpbot

# 2. Создать .env (по шаблону .env.example)
cp .env.example .env
# Заполнить BOT_TOKEN и SUPPORT_GROUP_ID

# 3. Установить зависимости
pip install -r requirements.txt

# 4. Запустить
python3 bot.py
```
