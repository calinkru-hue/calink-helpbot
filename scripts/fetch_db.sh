#!/usr/bin/env bash
# Скачать свежий снапшот SQLite-базы Calink Support Bot.
#
# Использование:
#   CALINK_BOT_DB_TOKEN=<твой-токен> bash scripts/fetch_db.sh [output_path]
#
# Опциональные переопределения:
#   CALINK_BOT_DB_URL   — полный URL /admin/db (по умолчанию — прод)
#
# Снапшот снимается на сервере через sqlite3 backup API, поэтому качать
# безопасно даже пока бот пишет в базу.
set -euo pipefail

: "${CALINK_BOT_DB_URL:=https://web-production-2302c.up.railway.app/admin/db}"
: "${CALINK_BOT_DB_TOKEN:?Set CALINK_BOT_DB_TOKEN env var (ask Anton for your token)}"

OUT="${1:-support_bot.db}"

echo "→ Скачиваю снапшот с ${CALINK_BOT_DB_URL}"
# -f: HTTP 4xx/5xx → non-zero exit. -L: следовать редиректам. -S: показывать
# ошибки даже когда -s глушит прогресс. (--fail-with-body появился в curl 7.76
# и отсутствует в системном curl macOS — поэтому портируемый -f.)
curl -fsSL \
    -H "Authorization: Bearer ${CALINK_BOT_DB_TOKEN}" \
    "${CALINK_BOT_DB_URL}" -o "${OUT}"

bytes=$(wc -c < "${OUT}" | tr -d ' ')
echo "✓ Сохранено ${OUT} (${bytes} байт)"

if command -v sqlite3 >/dev/null 2>&1; then
    echo ""
    echo "Быстрая сводка:"
    sqlite3 "${OUT}" <<'SQL'
.headers on
.mode column
SELECT
    (SELECT COUNT(*) FROM users)     AS tg_users,
    (SELECT COUNT(*) FROM messages)  AS msg_mappings,
    (SELECT COUNT(*) FROM event_log) AS event_log_rows;
SELECT event_type, COUNT(*) AS n
FROM event_log
GROUP BY event_type
ORDER BY n DESC;
SQL
else
    echo "(поставь sqlite3 для сводки)"
fi
