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
| `RESET_KEYWORD` | Кодовое слово hard-reset юзера (пусто = дефолт `!user_del!`) | `!user_del!` |
| `FEEDBACK_ENABLED` | Включить sweeper оценок (`false` = выключить) | `true` |
| `FEEDBACK_DELAY_HOURS` | Через сколько часов после ответа саппорта спрашивать оценку. На dev — `0` | `2` |
| `FEEDBACK_SWEEP_INTERVAL_MIN` | Как часто тикает sweeper (минуты) | `5` |

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

## Фидбек: «вопрос решён?» → оценка (v1.8.0)

Через `FEEDBACK_DELAY_HOURS` часов после **последнего ответа саппорта** бот пишет клиенту в личку «Ваш вопрос решён?» с кнопками Да / Нет.

- **Да** → сообщение редактируется в шаг 2: 5 эмодзи `😡 😕 😐 🙂 😍`. В топик летит `✅ Клиент подтвердил, что вопрос решён`, на оценку — отдельным сообщением `⭐️ Оценка 😍 (5/5)`.
- **Нет** → «Извините. Напишите здесь, что осталось нерешённым». В топик: `⚠️ Клиент говорит, что вопрос НЕ решён — нужен follow-up`.

Фоновый `feedback_sweeper` (в `feedback.py`) тикает раз в `FEEDBACK_SWEEP_INTERVAL_MIN` минут. Запускается из `post_init`, отменяется в `post_shutdown`; ссылка на таск держится в модульной переменной, иначе сборщик мусора может прибить его на полуслове.

**Кого выбирает `users_due_for_feedback`** — все условия обязательны:
- саппорт хотя бы раз отвечал, и его ответ старше порога
- вопрос либо не задавался, либо задавался ДО последнего ответа саппорта — поэтому на **повторное обращение спросим снова**
- **последнее слово за саппортом, а не за клиентом**: если клиент написал после нашего ответа, мы сами ещё должны ответить, и просить оценку рано (`last_user_message_at <= last_support_reply_at`)

Для этого в `users` появились колонки `last_support_reply_at`, `last_user_message_at`, `feedback_sent_at` (миграции через `ALTER TABLE` в `init_db`). Заполняются хуками в `handle_user_message` (`mark_user_message`) и `handle_support_message` (`mark_support_reply`).

**Идемпотентность** через таймстемпы строки `feedback`:
- `rated_at IS NULL AND yes_clicked_at IS NULL` → ответа не было
- `yes_clicked_at IS NOT NULL` → подтвердил решение (оценки может ещё не быть)
- `rated_at IS NOT NULL AND rating IS NULL` → ответил «Нет»
- `rated_at IS NOT NULL AND rating IS NOT NULL` → оценил

Повторные клики попадают в `UPDATE ... WHERE ... IS NULL` и ничего не делают — дублей в топике нет. Клик «Нет» после «Да» тоже заблокирован.

**Таблица `feedback`** — подмножество одноимённой таблицы из `zorion-helpbot` без email-полей (`email_user_id`, `token`), чтобы запросы по оценкам работали одинаково на обоих ботах. Колонка `channel` всегда `'telegram'`.

⚠️ **Все сравнения дат обёрнуты в SQLite `datetime()`.** Python пишет `2026-08-04T12:00:00+00:00` (через `T`), а `DEFAULT (datetime('now'))` — `2026-08-04 12:00:00` (через пробел). Пробел сортируется раньше `T`, поэтому строковое сравнение колонок разного происхождения врёт. В `zorion-helpbot` на этом уже горели (reminder уходил через минуту вместо пяти). Добавляешь новый запрос по времени — оборачивай оба операнда.

События в `event_log`: `feedback_sent`, `yes_clicked`, `no_clicked`, `rated` (с `extra.rating`).

---

## Hard-reset юзера — `!user_del!` (v1.7.0)

QA-инструмент: отправить в топик кодовое слово **`!user_del!`** — юзер полностью исчезает из памяти бота, топик закрывается. При следующем его сообщении бот начинает онбординг так, будто видит впервые (создаст новый топик и карточку). Меняется через env `RESET_KEYWORD`.

Что делает `handle_reset_keyword`:
1. удаляет само сообщение-триггер из топика
2. каскадом стирает `users` / `messages` / `event_log` по `topic_id` — одной транзакцией (`database.delete_user_by_topic`)
3. снимает pending-джобу авто-ответа (иначе она выстрелит после удаления юзера и запишет осиротевший `bot_message`)
4. закрывает форум-топик — он уезжает в архив

**В `event_log` ничего не пишет** — reset это тестовый инструмент, его служебная активность не должна попадать в аналитику. Следы остаются только в логах Railway (`✅ Reset выполнен: топик=…`).

Две грабли, на которых уже спотыкались в `zorion-helpbot` и которые тут закрыты сразу:

- **Пустая env-переменная.** `RESET_KEYWORD` читается как `os.getenv("RESET_KEYWORD") or "!user_del!"`, а НЕ через `getenv(name, default)`. Если в Railway переменная задана пустой строкой, второй вариант вернёт `""` — и regex-фильтр начнёт матчить вообще всё.
- **Гонка handler-групп.** `handle_support_internal` (группа 1) делает early-return на кодовом слове. Иначе он сработал бы уже ПОСЛЕ каскада из группы 0 и вставил бы строку в `event_log` на только что стёртый топик — orphan-запись.

Фильтр — строгий `^!user_del!$`: подстроки, пробелы по краям и другой регистр не срабатывают. Хендлер зарегистрирован **до** `handle_support_message`, т.к. внутри группы 0 отрабатывает первый совпавший.

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
