# Guarant Mini‑App (Railway MVP)

Это **универсальный гарант** для **легальных цифровых товаров/услуг** (не для продажи/передачи чужих аккаунтов/доступов).
Хостинг: Railway (API + Telegram bot в одном сервисе).

## 1) Railway переменные
- BOT_TOKEN = токен бота
- ADMIN_ID = твой telegram id (пример: 7489815425)
- DATABASE_URL = строка Postgres из Railway (Plugin → Postgres → Variables)
- APP_BASE_URL = публичный URL сервиса Railway (пример: https://your-app.up.railway.app)
- COMMISSION_PCT = комиссия (по умолчанию 5)

## 2) Запуск на Railway
Railway сам поставит зависимости из requirements.txt.
Start command:
`uvicorn app.main:app --host 0.0.0.0 --port $PORT`

## 3) Telegram Mini App
Открой бота → /start → кнопка "Открыть мини‑апп".
Авторизация идёт через Telegram WebApp initData (проверяется на сервере).

## 4) Админ‑команды (в боте)
- /topups  — список заявок
- /approve <id> — подтвердить заявку и начислить баланс
- /reject <id>  — отклонить
- /bal <user_id> — баланс
- /grant <user_id> <amount> — начислить вручную
- /take <user_id> <amount> — списать вручную

## 5) Как работает сделка
1) Продавец создаёт сделку (сумма, описание).
2) Покупатель открывает ссылку сделки и вносит сумму с баланса → "в резерв".
3) Продавец отмечает "Выполнено".
4) Покупатель подтверждает → деньги на баланс продавца (комиссия админу).
5) Спор → админ решает (release/refund) в админ‑панели бота.

